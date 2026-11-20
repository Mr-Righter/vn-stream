"""Request context for response IDs and localized messages."""

from contextvars import ContextVar
from uuid import uuid4

from vn_stream.i18n import language

request_locale: ContextVar[str] = ContextVar("request_locale", default="zh-CN")


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope["request_id"] = uuid4().hex
        locale = language(
            dict(scope.get("headers", []))
            .get(b"accept-language", b"")
            .decode("ascii", errors="ignore")
        )
        token = request_locale.set(locale)

        async def with_locale(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                by_name = dict(headers)
                if b"application/json" in by_name.get(b"content-type", b""):
                    previous = by_name.get(b"vary", b"")
                    headers = [(key, value) for key, value in headers if key != b"vary"]
                    headers.append(
                        (b"vary", b"Accept-Language" + (b", " + previous if previous else b""))
                    )
                    message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, with_locale)
        finally:
            request_locale.reset(token)
