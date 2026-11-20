"""Stable old client addresses must not survive a media protocol upgrade."""

import importlib.util
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "patch_selkies", Path(__file__).resolve().parents[2] / "scripts/patch-selkies.py"
)
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


def pinned_layout(root, suffix=""):
    server = root / "lsiopy/lib/python3.13/site-packages/selkies/websockets_mode.py"
    server.parent.mkdir(parents=True)
    server.write_text(
        "class Server:\n"
        "    async def handler(self):\n"
        "        try:\n"
        "            await self.ws_handler(ws, remote_address, token, query_role=query_role, query_slot=query_slot)\n"
        "        finally:\n"
        "            self._report_client_presence()\n"
        "        return ws\n"
        "    def ownership(self):\n"
        "        if display_id == 'primary' and self._joins_beside_owner(websocket, tab_id):\n"
        "            pass\n"
    )
    dashboard = root / "usr/share/selkies/selkies-dashboard"
    dashboard.mkdir(parents=True)
    (dashboard / "selkies-core.js").write_text(
        "je.pathname+=`api/websockets`;window.selkiesTransport=u,Ce()" + suffix
    )
    (dashboard / "index.html").write_text(
        "<html><head><title>Selkies</title>"
        '<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<link rel="apple-touch-icon" href="icon-512.png">'
        '<link rel="manifest" href="manifest.json" crossorigin="use-credentials">'
        '<link rel="icon" type="image/png" href="icon.png">'
        '</head><body><script type="module" src="selkies-core.js"></script></body></html>'
    )
    www = root / "usr/share/selkies/www"
    www.mkdir()
    (www / "icon.png").write_bytes(b"Base image icon")
    (dashboard / "icon-512.png").write_bytes(b"Base dashboard icon")
    icons = root / "opt/vn-stream/web/app-icons"
    icons.mkdir(parents=True)
    (icons / "icon-192.png").write_bytes(b"VN Stream icon")
    (icons / "icon-512.png").write_bytes(b"VN Stream dashboard icon")
    return dashboard


def test_media_entrypoint_uses_fingerprinted_script_instead_of_cached_alias(tmp_path):
    dashboard = pinned_layout(tmp_path)
    patcher.patch(tmp_path)
    html = (dashboard / "index.html").read_text()
    entry = re.search(r'src="(selkies-core-[0-9a-f]+\.js)"', html)
    assert entry, "HTML still requests the old cached media client address"
    assert 'src="selkies-core.js"' not in html
    assert "vn_connection" in (dashboard / entry[1]).read_text()


def test_media_entrypoint_changes_when_client_protocol_changes(tmp_path):
    first = pinned_layout(tmp_path / "first")
    second = pinned_layout(tmp_path / "second", ";/* next protocol */")
    patcher.patch(tmp_path / "first")
    patcher.patch(tmp_path / "second")
    assert (first / "index.html").read_text() != (second / "index.html").read_text()


def test_nginx_hook_removes_certificate_generation_before_loading_optional_https(tmp_path):
    init = tmp_path / "etc/s6-overlay/s6-rc.d/init-nginx/run"
    init.parent.mkdir(parents=True)
    init.write_text(
        "#!/usr/bin/with-contenv bash\n\n"
        "# create self signed cert\nopenssl req -new -x509\n\n"
        "# modify nginx config\ncp /defaults/default.conf ${NGINX_CONFIG}\n"
        "cp -a /usr/share/selkies/$DASHBOARD /usr/share/selkies/web\n"
    )
    patcher.patch_nginx_init(tmp_path)
    result = init.read_text()
    assert "openssl req" not in result
    assert result.index("cp /defaults/default.conf") < result.index(
        ". /defaults/vn-stream-https-init"
    )
    assert "cp -a /usr/share/selkies/$DASHBOARD /usr/share/selkies/web" in result


def test_toolbar_revision_changes_for_styles_only_and_covers_both_entry_routes(tmp_path):
    assets = tmp_path / "opt/vn-stream/web"
    assets.mkdir(parents=True)
    (assets / "game-controls.js").write_text("unchanged script")
    css = assets / "game-controls.css"
    css.write_text("old toolbar")
    nginx = tmp_path / "etc/nginx/vn-stream-locations.conf"
    nginx.parent.mkdir(parents=True)
    original = '<script src="/library/game-controls.js"></script>\n' * 2
    nginx.write_text(original)
    patcher.version_player_assets(tmp_path)
    first = nginx.read_text()
    assert len(re.findall(r"game-controls.js\?v=[0-9a-f]+", first)) == 2
    nginx.write_text(original)
    css.write_text("compact toolbar")
    patcher.version_player_assets(tmp_path)
    assert nginx.read_text() != first


def test_player_uses_the_same_installation_manifest_and_safe_viewport(tmp_path):
    dashboard = pinned_layout(tmp_path)
    patcher.patch(tmp_path)
    html = (dashboard / "index.html").read_text()
    assert 'href="/manifest.webmanifest"' in html
    assert 'href="manifest.json"' not in html
    assert 'href="/app-icons/icon-192.png"' in html
    assert 'rel="icon" type="image/x-icon" sizes="32x32" href="/favicon.ico"' in html
    assert 'href="icon.png"' not in html
    assert "<title>VN Stream</title>" in html
    assert 'name="apple-mobile-web-app-title" content="VN Stream"' in html
    assert "viewport-fit=cover" in html
    assert (tmp_path / "usr/share/selkies/www/icon.png").read_bytes() == b"VN Stream icon"
    assert (dashboard / "icon-512.png").read_bytes() == b"VN Stream dashboard icon"


def test_toolbar_revision_includes_shared_chunks_and_icons(tmp_path):
    assets = tmp_path / "opt/vn-stream/web"
    (assets / "chunks").mkdir(parents=True)
    (assets / "icons").mkdir()
    (assets / "game-controls.js").write_text("import './chunks/shared.js'")
    (assets / "game-controls.css").write_text("toolbar")
    chunk = assets / "chunks/shared.js"
    chunk.write_text("first shared UI")
    icon = assets / "icons/moon.svg"
    icon.write_text("first icon")
    nginx = tmp_path / "etc/nginx/vn-stream-locations.conf"
    nginx.parent.mkdir(parents=True)
    original = '<script src="/library/game-controls.js"></script>\n' * 2

    def revision():
        nginx.write_text(original)
        patcher.version_player_assets(tmp_path)
        return nginx.read_text()

    first = revision()
    chunk.write_text("new shared UI")
    second = revision()
    assert second != first
    icon.write_text("new icon")
    assert revision() != second


def test_player_revision_includes_local_font_bytes(tmp_path):
    assets = tmp_path / "opt/vn-stream/web/fonts"
    assets.mkdir(parents=True)
    font = assets / "subset.woff2"
    font.write_bytes(b"first font")
    nginx = tmp_path / "etc/nginx/vn-stream-locations.conf"
    nginx.parent.mkdir(parents=True)
    original = '<script src="/library/game-controls.js"></script>' * 2
    nginx.write_text(original)
    patcher.version_player_assets(tmp_path)
    first = nginx.read_text()
    nginx.write_text(original)
    font.write_bytes(b"revised font")
    patcher.version_player_assets(tmp_path)
    assert nginx.read_text() != first
