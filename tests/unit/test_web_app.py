"""One installation identity includes the library, login and root player."""

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from tests.auth_helpers import TestClient, api_data, sign_in
from vn_stream.api.app import create_app

PUBLIC = Path(__file__).resolve().parents[2] / "web/public"


@pytest.mark.parametrize("prefix,start", [("", "/"), ("/library", "/library/")])
def test_public_install_metadata_has_one_origin_wide_identity(tmp_path, prefix, start):
    with TestClient(
        create_app(tmp_path / "data", [], "secret", public_prefix=prefix, web=PUBLIC)
    ) as c:
        canonical = c.get("/manifest.webmanifest", follow_redirects=False)
        assert canonical.status_code == 200
        assert canonical.headers["content-type"].startswith("application/manifest+json")
        manifest = api_data(canonical)
        assert manifest["name"] == "VN Stream"
        assert manifest["short_name"] == "VN Stream"
        assert manifest["id"] == "/"
        assert manifest["scope"] == "/"
        assert manifest["start_url"] == start
        assert manifest["display"] == "standalone"
        assert "?" not in manifest["start_url"]
        assert c.head("/manifest.webmanifest").status_code == 200
        assert [icon["sizes"] for icon in manifest["icons"]] == ["192x192", "512x512"]
        for path, size in [
            ("/app-icons/icon-32.png", 32),
            (manifest["icons"][0]["src"], 192),
            (manifest["icons"][1]["src"], 512),
        ]:
            response = c.get(path, follow_redirects=False)
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"
            with Image.open(BytesIO(response.content)) as image:
                assert image.format == "PNG"
                assert image.size == (size, size)
            assert c.head(path).status_code == 200
        assert c.post("/app-icons/icon-32.png", follow_redirects=False).status_code == 302
        icon = c.get("/favicon.ico", follow_redirects=False)
        assert icon.status_code == 200
        assert icon.headers["content-type"] in {"image/x-icon", "image/vnd.microsoft.icon"}
        with Image.open(BytesIO(icon.content)) as favicon_image:
            assert favicon_image.format == "ICO"
            assert favicon_image.size == (32, 32)
        assert c.head("/favicon.ico").status_code == 200
        assert c.post("/favicon.ico", follow_redirects=False).status_code == 302
        assert c.get("/api/v2/projects").status_code == 401
        assert c.get("/api/v2/session").status_code == 401
        assert c.get("/api/v2/stream").status_code == 401
        assert c.get("/index.html", follow_redirects=False).status_code == 302
        # Only the fixed install resources are anonymous; no broad icon-folder exemption.
        assert c.get("/app-icons/private.txt", follow_redirects=False).status_code == 302
        assert c.get("/branding/banner.png", follow_redirects=False).status_code == 302
        favicon = '<link rel="icon" type="image/x-icon" sizes="32x32" href="/favicon.ico"'
        assert favicon in c.get("/login").text
        sign_in(c, "secret")
        assert favicon in c.get("/").text
        assert c.get("/manifest.json").status_code == 404
        assert c.get("/project.json").status_code == 404
        assert c.post("/manifest.webmanifest", json={}).status_code == 405
