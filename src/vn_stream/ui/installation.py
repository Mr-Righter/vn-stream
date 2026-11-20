"""Public installation resource allowlist shared by authentication and routes."""

ICON_PATHS = ("/app-icons/icon-192.png", "/app-icons/icon-512.png")
INSTALL_PATHS = frozenset(("/manifest.webmanifest", "/favicon.ico", "/app-icons/icon-32.png", *ICON_PATHS))
