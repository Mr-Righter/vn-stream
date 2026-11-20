"""Describe the configured transport without exposing credentials in URLs."""

from urllib.parse import unquote, urlsplit

from vn_stream.i18n import text


def describe_stream(path: str | None) -> dict:
    if path is not None:
        decoded = unquote(path)
        url = urlsplit(decoded)
        if (
            not decoded.startswith("/")
            or decoded.startswith("//")
            or "\\" in decoded
            or url.scheme
            or url.netloc
            or url.query
            or url.fragment
            or any(part in (".", "..") for part in decoded.split("/"))
        ):
            raise ValueError(text("errors.theStreamUrlMustBeASameOriginAbsolute"))
    return {"available": path is not None, "url": path, "backend": "selkies"}
