"""Public installation metadata; no game, credential or session information."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from vn_stream.ui import THEME
from vn_stream.ui.installation import ICON_PATHS


def web_app_routes(prefix: str) -> APIRouter:
    router = APIRouter()

    @router.api_route("/manifest.webmanifest", methods=["GET", "HEAD"], include_in_schema=False)
    def manifest():
        return JSONResponse(
            {
                "id": "/",
                "name": "VN Stream",
                "short_name": "VN Stream",
                "scope": "/",
                "start_url": prefix.rstrip("/") + "/",
                "display": "standalone",
                "background_color": THEME["modes"]["dark"]["colorBgLayout"],
                "theme_color": THEME["modes"]["dark"]["colorBgLayout"],
                "icons": [
                    {"src": path, "sizes": f"{size}x{size}", "type": "image/png"}
                    for path, size in zip(ICON_PATHS, (192, 512), strict=True)
                ],
            },
            media_type="application/manifest+json",
            headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
        )

    return router
