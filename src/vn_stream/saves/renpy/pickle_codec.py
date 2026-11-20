"""Bounded, inert pickle inspection and dictionary patches; never unpickle game state."""

import hashlib
import io
import json
import math
import pickle
import pickletools
import struct
import time
import zipfile
import zlib
from dataclasses import dataclass, field

MAX_FILE = 64 * 1024**2
MAX_EXPANDED = 256 * 1024**2


@dataclass(eq=False)
class Node:
    kind: str
    items: list = field(default_factory=list)
    label: str = ""
    memo: int | None = None
    state: object = None
    build: int | None = None


class Invalid(ValueError):
    pass


def fail():
    raise Invalid("Unsupported or invalid save structure")


def parse(data):
    if len(data) > MAX_EXPANDED:
        fail()
    stack, memo, instructions = [], {}, []
    mark = object()
    deadline = time.monotonic() + 15

    def popmark():
        try:
            i = len(stack) - 1 - stack[::-1].index(mark)
        except ValueError:
            fail()
        values = stack[i + 1 :]
        del stack[i:]
        return values

    def pairs(node, values):
        if not isinstance(node, Node) or len(values) % 2:
            fail()
        node.items.extend(zip(values[::2], values[1::2], strict=True))

    try:
        for count, (op, arg, pos) in enumerate(pickletools.genops(data)):
            if count >= 2_000_000 or len(memo) > 500_000 or len(stack) > 100_000:
                fail()
            if count % 4096 == 0 and time.monotonic() > deadline:
                fail()
            name = op.name
            instructions.append((name, pos))
            if name in ("PROTO", "FRAME"):
                if name == "PROTO" and arg not in (2, 3, 4, 5):
                    fail()
            elif name == "MARK":
                stack.append(mark)
            elif name == "STOP":
                if len(stack) != 1 or pos != len(data) - 1:
                    fail()
                return stack[0], memo, instructions
            elif name in ("NONE", "NEWTRUE", "NEWFALSE"):
                stack.append({"NONE": None, "NEWTRUE": True, "NEWFALSE": False}[name])
            elif name in (
                "BININT",
                "BININT1",
                "BININT2",
                "INT",
                "LONG",
                "LONG1",
                "LONG4",
                "BINFLOAT",
                "FLOAT",
                "BINUNICODE",
                "SHORT_BINUNICODE",
                "BINUNICODE8",
                "UNICODE",
                "BINSTRING",
                "SHORT_BINSTRING",
                "STRING",
                "BINBYTES",
                "SHORT_BINBYTES",
                "BINBYTES8",
                "BYTEARRAY8",
            ):
                if isinstance(arg, (str, bytes, bytearray)) and len(arg) > 16 * 1024**2:
                    fail()
                stack.append(arg)
            elif name in ("EMPTY_DICT", "EMPTY_LIST", "EMPTY_TUPLE", "EMPTY_SET"):
                stack.append(Node(name[6:].lower()))
            elif name in ("TUPLE", "LIST", "DICT", "FROZENSET"):
                values = popmark()
                node = Node(name.lower())
                if name == "DICT":
                    pairs(node, values)
                else:
                    node.items.extend(values)
                stack.append(node)
            elif name in ("TUPLE1", "TUPLE2", "TUPLE3"):
                n = int(name[-1])
                values = stack[-n:]
                del stack[-n:]
                stack.append(Node("tuple", values))
            elif name in ("BINPUT", "LONG_BINPUT", "PUT", "MEMOIZE"):
                key = len(memo) if name == "MEMOIZE" else int(arg)
                if not 0 <= key <= 500_000 or key in memo:
                    fail()
                memo[key] = stack[-1]
                if isinstance(stack[-1], Node):
                    stack[-1].memo = key
            elif name in ("BINGET", "LONG_BINGET", "GET"):
                stack.append(memo[int(arg)])
            elif name == "GLOBAL":
                stack.append(Node("global", label=arg))
            elif name == "STACK_GLOBAL":
                symbol, module = stack.pop(), stack.pop()
                if not isinstance(module, str) or not isinstance(symbol, str):
                    fail()
                stack.append(Node("global", label=module + " " + symbol))
            elif name in ("NEWOBJ", "REDUCE", "NEWOBJ_EX"):
                if name == "NEWOBJ_EX":
                    stack.pop()
                args, constructor = stack.pop(), stack.pop()
                stack.append(
                    Node(
                        "object",
                        [constructor, args],
                        constructor.label if isinstance(constructor, Node) else "",
                    )
                )
            elif name == "BUILD":
                state = stack.pop()
                obj = stack[-1]
                if not isinstance(obj, Node):
                    fail()
                obj.state, obj.build = state, pos
            elif name in ("SETITEMS", "APPENDS", "ADDITEMS"):
                values = popmark()
                node = stack[-1]
                if name == "SETITEMS":
                    pairs(node, values)
                elif isinstance(node, Node):
                    node.items.extend(values)
                else:
                    fail()
            elif name == "SETITEM":
                value, key = stack.pop(), stack.pop()
                pairs(stack[-1], [key, value])
            elif name == "APPEND":
                value = stack.pop()
                if not isinstance(stack[-1], Node):
                    fail()
                stack[-1].items.append(value)
            elif name == "POP":
                stack.pop()
            elif name == "POP_MARK":
                popmark()
            elif name == "DUP":
                stack.append(stack[-1])
            else:
                # Persistent IDs, extension registries and external buffers are unsupported.
                fail()
    except (ValueError, IndexError, KeyError, TypeError, OverflowError, struct.error) as exc:
        raise Invalid("Unsupported or invalid save structure") from exc
    fail()


def mapping(node):
    if not isinstance(node, Node) or node.kind != "dict":
        fail()
    if any(type(k) is not str for k, _ in node.items):
        fail()
    return dict(node.items)


def unpack(data, persistent=False):
    if not 0 < len(data) <= MAX_FILE:
        fail()
    if persistent:
        stream = zlib.decompressobj()
        try:
            log = stream.decompress(data, MAX_EXPANDED + 1)
        except zlib.error as exc:
            raise Invalid("Invalid persistent file") from exc
        if not stream.eof or stream.unconsumed_tail or len(log) > MAX_EXPANDED:
            fail()
        return log, stream.unused_data, None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > 64 or len({i.filename for i in infos}) != len(infos):
                fail()
            if sum(i.file_size for i in infos) > MAX_EXPANDED or any(
                i.flag_bits & 1 for i in infos
            ):
                fail()
            log = z.read("log")
            signatures = z.read("signatures") if "signatures" in z.namelist() else b""
            return log, signatures, z.read("json") if "json" in z.namelist() else None
    except (zipfile.BadZipFile, KeyError, RuntimeError) as exc:
        raise Invalid("Invalid save archive") from exc


def structure(data, persistent=False):
    log, signatures, metadata = unpack(data, persistent)
    root, memo, instructions = parse(log)
    if persistent:
        if (
            not isinstance(root, Node)
            or root.label not in ("renpy.persistent Persistent", "renpy.game Persistent")
            or root.build is None
        ):
            fail()
        target, insertion = root.state, root.build
    else:
        if not isinstance(root, Node) or root.kind != "tuple" or len(root.items) != 2:
            fail()
        target, insertion = root.items[0], len(log) - 1
        rollback = root.items[1]
        if isinstance(rollback, Node) and rollback.kind == "object":
            if rollback.label != "renpy.rollback RollbackLog" or rollback.build is None:
                fail()
            mapping(rollback.state)
            if rollback.state.memo is None:
                fail()
    values = mapping(target)
    if target.memo is None:
        fail()
    return log, signatures, metadata, target, values, insertion, instructions


def fields(values, persistent):
    result = []
    for name, value in values.items():
        leaf = name.rsplit(".", 1)[-1]
        if leaf.startswith("_") or (not persistent and not name.startswith("store.")):
            continue
        kind = {bool: "boolean", str: "text", int: "number", float: "number"}.get(type(value))
        if not kind or (type(value) is float and not math.isfinite(value)):
            continue
        result.append(
            dict(
                id=hashlib.sha256(name.encode()).hexdigest()[:32],
                name=("persistent." + name) if persistent else name,
                type=kind,
                value=str(value) if kind == "number" else value,
                numeric_type="integer"
                if type(value) is int
                else "decimal"
                if type(value) is float
                else None,
            )
        )
    return sorted(result, key=lambda f: f["name"].casefold())


def inspect(data, persistent=False):
    _, sig, metadata, _, values, _, _ = structure(data, persistent)
    return dict(fields=fields(values, persistent), signed=bool(sig), metadata=metadata)


def scalar(value):
    # Only exact primitive types reach this trusted encoder.
    if type(value) not in (bool, str, int, float):
        fail()
    encoded = pickle.dumps(value, protocol=2)
    ops = list(pickletools.genops(encoded))
    return b"".join(
        encoded[pos : ops[i + 1][2]]
        for i, (op, _, pos) in enumerate(ops[:-1])
        if op.name not in ("PROTO", "BINPUT", "LONG_BINPUT", "PUT", "MEMOIZE")
    )


def patch_dictionary(memo, updates):
    get = b"h" + bytes([memo]) if memo < 256 else b"j" + struct.pack("<I", memo)
    return get + b"".join(scalar(k) + scalar(v) + b"s" for k, v in updates.items()) + b"0"


def available_names(values):
    return {f["name"] for f in fields(values, False)}


def edit(data, changes, persistent=False, signer=None):
    log, signatures, _, target, values, insertion, instructions = structure(data, persistent)
    available = {f["id"]: f for f in fields(values, persistent)}
    updates = {}
    if not changes or len(changes) > 10_000:
        fail()
    for change in changes:
        item = available.get(change.get("id"))
        if not item or item["type"] != change.get("type"):
            fail()
        name = item["name"][11:] if persistent else item["name"]
        value = change.get("value")
        original = values[name]
        if item["type"] == "boolean":
            if type(value) is not bool:
                fail()
        elif item["type"] == "text":
            if type(value) is not str or len(value.encode("utf-8")) > 1024**2:
                fail()
        else:
            if type(value) is not str or len(value) > 4096:
                fail()
            try:
                value = int(value, 10) if type(original) is int else float(value)
            except ValueError:
                fail()
            if type(value) is float and not math.isfinite(value):
                fail()
        updates[name] = value
    injections = {insertion: patch_dictionary(target.memo, updates)}
    if not persistent:
        root, _, _ = parse(log)
        rollback = root.items[1]
        if isinstance(rollback, Node) and rollback.label == "renpy.rollback RollbackLog":
            state = mapping(rollback.state)
            if rollback.state.memo is None or rollback.build is None:
                fail()
            previous = state.get("_vn_stream_edits", "{}")
            if type(previous) is not str or len(previous) > 16 * 1024**2:
                fail()
            try:
                pending = json.loads(previous)
            except ValueError:
                fail()
            names = available_names(values)
            if (
                type(pending) is not dict
                or len(pending) > 10000
                or any(k not in names for k in pending)
            ):
                fail()
            pending.update(updates)
            if len(pending) > 10000:
                fail()
            payload = json.dumps(pending, ensure_ascii=True, allow_nan=False)
            # Keep rollback records untouched; the normal-load adapter applies these after rollback.
            injections[rollback.build] = patch_dictionary(
                rollback.state.memo, {"_vn_stream_edits": payload}
            )
    if persistent and "_changed" in values:
        changed = values["_changed"]
        mapping(changed)
        if changed.memo is None:
            fail()
        injections[insertion] += patch_dictionary(changed.memo, {k: time.time() for k in updates})
    pieces = []
    for i, (name, pos) in enumerate(instructions):
        if pos in injections:
            pieces.append(injections[pos])
        end = instructions[i + 1][1] if i + 1 < len(instructions) else len(log)
        if name != "FRAME":
            pieces.append(log[pos:end])
    modified = b"".join(pieces)
    parse(modified)
    if signatures and signer is None:
        raise Invalid("A trusted signing key is required")
    signature = signer(log, signatures, modified) if signer else b""
    if persistent:
        return zlib.compress(modified, 3) + signature
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            content = (
                modified
                if info.filename == "log"
                else signature
                if info.filename == "signatures"
                else src.read(info)
            )
            dst.writestr(info, content)
        if signature and "signatures" not in src.namelist():
            dst.writestr("signatures", signature)
    return out.getvalue()
