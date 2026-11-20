"""Development entry point; one worker owns the single game session."""

import argparse
import os
from pathlib import Path

import uvicorn

from vn_stream.api.app import create_app
from vn_stream.i18n import text


def main():
    parser = argparse.ArgumentParser(description="VN Stream")
    parser.add_argument("--data", type=Path, default=Path("var"))
    parser.add_argument("--library-root", type=Path, default=None)
    parser.add_argument("--discovery-root", type=Path, action="append", default=[])
    parser.add_argument("--root", type=Path, action="append", default=[])
    parser.add_argument("--web", type=Path, default=Path("web/dist"))
    parser.add_argument("--stream-path", default=None)
    parser.add_argument("--public-prefix", default="")
    parser.add_argument("--allow-local-http", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    password = os.environ.get("VN_STREAM_PASSWORD", "")
    if not password:
        parser.error(text("errors.setTheLoginPasswordWithVnStreamPassword"))
    app = create_app(
        args.data.resolve(),
        args.root or [args.data.resolve().parent / "projects"],
        password,
        username=os.environ.get("VN_STREAM_USERNAME", "player"),
        web=args.web.resolve(),
        stream_path=args.stream_path,
        library_root=args.library_root,
        discovery_roots=args.discovery_root or None,
        public_prefix=args.public_prefix,
        allow_insecure_local=(args.allow_local_http or not args.public_prefix)
        and args.host in {"127.0.0.1", "localhost", "::1"},
    )
    uvicorn.run(app, host=args.host, port=args.port, workers=1)


if __name__ == "__main__":
    main()
