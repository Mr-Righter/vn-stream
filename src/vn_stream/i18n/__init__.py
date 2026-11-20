"""Shared message identities and safe presentation at HTTP boundaries."""

import json
import re
from importlib.resources import files

ROOT = files(__package__) / "locales"
CATALOGS = {name: json.loads((ROOT / (name + ".json")).read_text()) for name in ("zh-CN", "en")}
LEGACY = json.loads((ROOT / "legacy.json").read_text())
FIELDS = {"detail", "error", "warning", "notice"}
FALLBACK = "common.requestFailed"


def language(header):
    choices = []
    for index, item in enumerate((header or "").split(",")):
        parts = item.strip().split(";")
        name = parts[0].lower()
        base = name.split("-", 1)[0]
        name = "en" if base == "en" else "zh-CN" if base == "zh" else None
        quality = 1.0
        try:
            for part in parts[1:]:
                if part.strip().startswith("q="):
                    quality = float(part.strip()[2:])
        except ValueError:
            continue
        if name and 0 < quality <= 1:
            choices.append((quality, -index, name))
    return max(choices)[2] if choices else "zh-CN"


def descriptor(value):
    if isinstance(value, MessageText):
        return {"code": value.code, "params": value.params.copy()}
    if isinstance(value, BaseException) and value.args:
        return descriptor(value.args[0])
    if isinstance(value, dict) and isinstance(value.get("code"), str):
        params = value.get("params", {})
        if (
            isinstance(params, dict)
            and len(params) <= 16
            and all(isinstance(k, str) and type(v) in (str, int, float) for k, v in params.items())
        ):
            return {"code": value["code"], "params": params.copy()}
    if isinstance(value, str) and value in LEGACY:
        return {"code": LEGACY[value], "params": {}}
    return {"code": FALLBACK, "params": {}}


def render(message, locale="zh-CN"):
    value = descriptor(message)
    template = CATALOGS[language(locale)].get(value["code"])
    if template is None:
        return CATALOGS[language(locale)][FALLBACK]
    required = set(re.findall(r"\{([A-Za-z0-9_]+)\}", template))
    if not required.issubset(value["params"]):
        return CATALOGS[language(locale)][FALLBACK]
    return re.sub(r"\{([A-Za-z0-9_]+)\}", lambda m: str(value["params"][m[1]]), template)


class MessageText(str):
    def __new__(cls, code, params):
        obj = super().__new__(cls, render({"code": code, "params": params}))
        obj.code, obj.params = code, params
        return obj


def text(code, **params):
    return MessageText(code, params)


def localize_payload(value, locale="zh-CN"):
    if isinstance(value, list):
        return [localize_payload(item, locale) for item in value]
    if not isinstance(value, dict):
        return value
    # Game metadata, filenames, paths and payload data are never translated.
    result = {}
    messages = dict(value.get("messages") or {})
    for key, item in value.items():
        if key == "messages":
            continue
        if key in FIELDS and isinstance(item, str) and item:
            message = descriptor(messages.get(key, item))
            messages[key] = message
            result[key] = render(message, locale)
        else:
            result[key] = localize_payload(item, locale) if isinstance(item, (dict, list)) else item
    if messages:
        result["messages"] = messages
    return result


def durable_messages(record):
    """Store identities alongside legacy text; render only when serving a reader."""
    result = dict(record)
    messages = dict(result.get("messages") or {})
    for field in FIELDS:
        value = result.get(field)
        if isinstance(value, str) and value:
            previous = messages.get(field)
            messages[field] = (
                descriptor(value)
                if isinstance(value, MessageText) or not previous or render(previous) != value
                else previous
            )
        elif field in result:
            messages.pop(field, None)
    if messages:
        result["messages"] = messages
    else:
        result.pop("messages", None)
    return result
