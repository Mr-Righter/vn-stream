"""Restrict login redirects to local application paths."""

from urllib.parse import quote, unquote


def login_url(prefix: str, target: str) -> str:
    # Interpret percent escapes too: encoded slashes/backslashes must not form an
    # external redirect when another layer decodes them.
    decoded = target
    for _ in range(3):
        decoded = unquote(decoded)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(c) < 32 for c in decoded)
    ):
        target = prefix + "/"
    return prefix + "/login?next=" + quote(target, safe="")
