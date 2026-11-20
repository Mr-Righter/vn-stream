"""Export the checked API contract without opening the production data or a port."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from vn_stream.api.app import create_app


def main() -> None:
    with TemporaryDirectory(prefix="vn-openapi-") as workspace:
        root = Path(workspace)
        app = create_app(root / "data", [root / "games"], "openapi-export-only")
        try:
            schema = app.openapi()
            schema["paths"] = {
                path: item for path, item in schema["paths"].items() if path.startswith("/api/v2/")
            }
            print(json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2))
        finally:
            app.state.services.close()


if __name__ == "__main__":
    main()
