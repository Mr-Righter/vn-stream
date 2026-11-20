"""Apply narrowly checked hooks to the digest-pinned Selkies image, failing on drift."""

import hashlib
import json
import sys
from pathlib import Path
from shutil import copyfile


def replace_once(value, old, new):
    if value.count(old) != 1:
        raise SystemExit("Pinned Selkies lifecycle hook does not match; refusing partial patch")
    return value.replace(old, new, 1)


def patch(root):
    from importlib.resources import files

    theme_file = files("vn_stream.ui") / "theme.json"
    background = json.loads(theme_file.read_text())["modes"]["dark"]["colorBgLayout"]
    server = root / "lsiopy/lib/python3.13/site-packages/selkies/websockets_mode.py"
    value = server.read_text()
    value = replace_once(
        value,
        "        try:\n            await self.ws_handler(ws, remote_address, token, query_role=query_role, query_slot=query_slot)",
        "        from vn_stream.streaming.media_presence import watch as vn_stream_watch\n"
        "        vn_stream_task = asyncio.create_task(vn_stream_watch(self, ws, request, client_permissions))\n"
        "        try:\n            await self.ws_handler(ws, remote_address, token, query_role=query_role, query_slot=query_slot)",
    )
    value = replace_once(
        value,
        "        finally:\n            self._report_client_presence()\n        return ws",
        "        finally:\n            vn_stream_task.cancel()\n"
        "            try:\n                await vn_stream_task\n"
        "            except asyncio.CancelledError:\n                pass\n"
        "            self._report_client_presence()\n        return ws",
    )
    value = replace_once(
        value,
        "if display_id == 'primary' and self._joins_beside_owner(websocket, tab_id):",
        "if False:  # VN Stream has exactly one controlling media connection.",
    )
    compile(value, str(server), "exec")
    server.write_text(value)
    core = root / "usr/share/selkies/selkies-dashboard/selkies-core.js"
    if not core.is_file():
        core = root / "usr/share/selkies/web/selkies-core.js"
    value = core.read_text()
    value = replace_once(
        value,
        "window.selkiesTransport=u,Ce()",
        "window.selkiesTransport=u,window.dispatchEvent(new CustomEvent(`vn-stream:transport`)),Ce()",
    )
    # This URL is shared by the worker and main-thread WebSocket transports.
    value = replace_once(
        value,
        "je.pathname+=`api/websockets`;",
        "je.pathname+=`api/websockets`;"
        "je.searchParams.set(`vn_connection`,window.__vnConnection||(window.__vnConnection=crypto.randomUUID()));"
        "je.searchParams.set(`vn_stream_session`,new URL(location.href).searchParams.get(`session`)||``);",
    )
    core.write_text(value)
    # The media binding is a protocol change. A stable module URL can keep the
    # pre-upgrade client in a browser's HTTP cache, including mobile WebKit.
    versioned = "selkies-core-" + hashlib.sha256(value.encode()).hexdigest()[:16] + ".js"
    index = core.parent / "index.html"
    html = replace_once(index.read_text(), 'src="selkies-core.js"', 'src="' + versioned + '"')
    html = replace_once(html, "<title>Selkies</title>", "<title>VN Stream</title>")
    html = replace_once(
        html,
        '<link rel="manifest" href="manifest.json" crossorigin="use-credentials">',
        '<link rel="manifest" href="/manifest.webmanifest">'
        '<meta name="apple-mobile-web-app-title" content="VN Stream">'
        f'<meta name="theme-color" content="{background}">',
    )
    html = replace_once(
        html,
        '<link rel="icon" type="image/png" href="icon.png">',
        '<link rel="icon" type="image/x-icon" sizes="32x32" href="/favicon.ico">',
    )
    html = replace_once(
        html,
        '<link rel="apple-touch-icon" href="icon-512.png">',
        '<link rel="apple-touch-icon" sizes="192x192" href="/app-icons/icon-192.png">',
    )
    html = replace_once(
        html,
        'content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"',
        'content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover"',
    )
    (core.parent / versioned).write_text(value)
    index.write_text(html)
    icons = root / "opt/vn-stream/web/app-icons"
    # The base image recreates its web directory from these defaults at startup.
    copyfile(icons / "icon-192.png", root / "usr/share/selkies/www/icon.png")
    copyfile(icons / "icon-512.png", core.parent / "icon-512.png")


def version_player_assets(root):
    # Include every bundled dependency so shared chunk and icon changes also
    # invalidate the stable player entry URL. Paths participate in the digest.
    assets = root / "opt/vn-stream/web"
    digest = hashlib.sha256()
    for asset in sorted(assets.rglob("*")):
        if asset.is_file() and asset.suffix in {".js", ".css", ".svg", ".woff2"}:
            digest.update(asset.relative_to(assets).as_posix().encode())
            digest.update(b"\0")
            digest.update(asset.read_bytes())
            digest.update(b"\0")
    version = digest.hexdigest()[:16]
    path = root / "etc/nginx/vn-stream-locations.conf"
    value = path.read_text()
    marker = 'src="/library/game-controls.js"'
    if value.count(marker) != 2:
        raise SystemExit("Player entry hook does not match; refusing stale UI assets")
    path.write_text(value.replace(marker, 'src="/library/game-controls.js?v=' + version + '"'))


def patch_nginx_init(root):
    path = root / "etc/s6-overlay/s6-rc.d/init-nginx/run"
    value = path.read_text()
    start = "# create self signed cert\n"
    end = "# modify nginx config\n"
    before, marker, tail = value.partition(start)
    generation, boundary, after = tail.partition(end)
    if not marker or not boundary or "openssl req -new -x509" not in generation:
        raise SystemExit("Pinned Selkies certificate hook does not match")
    value = before + end + after
    copy = "cp /defaults/default.conf ${NGINX_CONFIG}\n"
    path.write_text(
        replace_once(
            value,
            copy,
            copy.rstrip("\n") + " || exit 1\n. /defaults/vn-stream-https-init || exit 1\n",
        )
    )


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/")
    patch_nginx_init(root)
    patch(root)
    version_player_assets(root)
