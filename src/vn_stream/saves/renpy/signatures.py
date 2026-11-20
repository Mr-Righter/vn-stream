"""Game-owned Ren'Py tokens and inert re-signing of standard save containers."""

import base64
import io
import os
import tempfile
import zipfile

import ecdsa

from vn_stream.i18n import text
from vn_stream.projects.locations import project
from vn_stream.saves.renpy import pickle_codec as save_codec
from vn_stream.storage.files import safe, sync_folder


class Signatures:
    def __init__(self, library, project_id):
        self.library, self.project_id = library, project_id

    def keys(self, create=False, *, readonly=False):
        root = project(self.library, self.project_id)
        path = safe(root, ".vn-stream/home/.renpy/tokens/security_keys.txt")
        if not path.exists():
            if not create:
                return [], set()
            path.parent.mkdir(parents=True, exist_ok=True)
            key = ecdsa.SigningKey.generate(curve=ecdsa.NIST256p)
            line = b"signing-key " + base64.b64encode(key.to_der()) + b" "
            line += base64.b64encode(key.verifying_key.to_der()) + b"\n"
            descriptor, temporary = tempfile.mkstemp(prefix=".token-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(line)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, path)
                    sync_folder(path.parent)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        try:
            if not path.is_file() or path.stat().st_size > 64 * 1024:
                raise ValueError
            keys, trusted = [], set()
            for line in path.read_text(encoding="ascii").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                parts = line.split()
                if parts[0] == "signing-key" and len(parts) == 3:
                    key = ecdsa.SigningKey.from_der(base64.b64decode(parts[1], validate=True))
                    public = base64.b64decode(parts[2], validate=True)
                    if key.verifying_key.to_der() != public:
                        raise ValueError
                    keys.append(key)
                    trusted.add(public)
                elif parts[0] == "verifying-key" and len(parts) == 2:
                    public = base64.b64decode(parts[1], validate=True)
                    ecdsa.VerifyingKey.from_der(public)
                    trusted.add(public)
                else:
                    raise ValueError
            if not keys:
                raise ValueError
            if not readonly:
                path.chmod(0o600)
            return keys, trusted
        except (
            ValueError,
            UnicodeError,
            ecdsa.der.UnexpectedDER,
            ecdsa.MalformedPointError,
        ) as error:
            raise ValueError(text("saves.keyDamaged")) from error

    def verify(self, data, signatures, *, readonly=False):
        _, trusted = self.keys(readonly=readonly)
        for line in signatures.splitlines():
            try:
                kind, encoded, signature = line.split()
                public = base64.b64decode(encoded, validate=True)
                if kind == b"signature" and public in trusted:
                    if ecdsa.VerifyingKey.from_der(public).verify(
                        base64.b64decode(signature, validate=True), data
                    ):
                        return True
            except (
                ValueError,
                ecdsa.BadSignatureError,
                ecdsa.der.UnexpectedDER,
                ecdsa.MalformedPointError,
            ):
                continue
        return False

    def sign(self, original, signatures, modified):
        keys, _ = self.keys(create=True)
        return b"".join(
            b"signature "
            + base64.b64encode(key.verifying_key.to_der())
            + b" "
            + base64.b64encode(key.sign(modified))
            + b"\n"
            for key in keys
        )

    def resign(self, data, persistent=False, *, trusted_source=False, force_local=False):
        log, signatures, _ = save_codec.unpack(data, persistent)
        self.keys(create=True)
        if not force_local and self.verify(log, signatures):
            return data
        if not trusted_source:
            save_codec.parse(log)
        signature = self.sign(log, signatures, log)
        if persistent:
            result = (
                data[: len(data) - len(signatures)] + signature if signatures else data + signature
            )
            if len(result) > save_codec.MAX_FILE:
                raise ValueError(text("saves.tooLarge"))
            return result
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w") as target:
            target.comment = source.comment
            for entry in source.infolist():
                target.writestr(
                    entry, signature if entry.filename == "signatures" else source.read(entry)
                )
            if "signatures" not in source.namelist():
                target.writestr("signatures", signature)
        result = output.getvalue()
        if len(result) > save_codec.MAX_FILE:
            raise ValueError(text("saves.tooLarge"))
        return result
