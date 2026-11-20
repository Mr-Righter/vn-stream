"""Export a reviewed commit without internal documents or local workspace state."""

import argparse
import shutil
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_MARKDOWN = {"README.md", "README_ZH.md", "AGENTS.md"}
PUBLIC_DOC_FILES = {
    "docs/backend-architecture.md",
    "docs/engine-integration.md",
    "docs/frontend-and-api.md",
    "docs/development-and-release.md",
    "docs/images/game-details.png",
    "docs/images/browser-stream.png",
}
NOTICE_PATHS = {"tests/unit/archive_fixtures/README.md": "tests/unit/archive_fixtures/NOTICE.txt"}
SOURCE_DIRS = {".github", "config", "containers", "scripts", "src", "tests", "tools", "web"}
PUBLIC_ROOT = PUBLIC_MARKDOWN | {
    ".dockerignore",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "LICENSE",
    "compose.yaml",
    "pyproject.toml",
    "requirements.lock",
}


def public_path(name: str) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Invalid tracked path: {name}")
    if name in NOTICE_PATHS:
        return NOTICE_PATHS[name]
    if name in PUBLIC_DOC_FILES:
        return name
    if len(path.parts) == 1:
        return name if name in PUBLIC_ROOT else None
    if path.parts[:2] in {("config", "local"), ("config", "secrets")} or any(
        part in {".venv", "node_modules", "dist", "__pycache__"} for part in path.parts
    ):
        return None
    if path.suffix.lower() in {".md", ".key", ".sqlite", ".sqlite3"} or (
        path.name == ".env" or path.name.startswith(".env.")
    ):
        return None
    return name if path.parts[0] in SOURCE_DIRS else None


def export(ref: str, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"],
        cwd=ROOT,
        text=True,
    ).strip()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent, prefix=".vn-public-tree-") as directory:
        tree = Path(directory) / "checkout"
        tree.mkdir()
        with subprocess.Popen(
            ["git", "archive", "--format=tar", commit],
            cwd=ROOT,
            stdout=subprocess.PIPE,
        ) as process:
            assert process.stdout is not None
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for entry in archive:
                    name = public_path(entry.name)
                    if name is None or entry.isdir():
                        continue
                    if not entry.isfile():
                        raise ValueError(f"Public source must be a regular file: {entry.name}")
                    target = tree / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(entry)
                    assert source is not None
                    with source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o755 if entry.mode & 0o111 else 0o644)
            if process.wait():
                raise ValueError("Unable to read the public export commit")
        required = PUBLIC_MARKDOWN | PUBLIC_DOC_FILES | {
            "LICENSE",
            "compose.yaml",
            "web/api/openapi-v2.json",
        }
        missing = sorted(name for name in required if not (tree / name).is_file())
        if missing:
            raise ValueError(f"Commit lacks required public files: {', '.join(missing)}")
        tree.rename(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="Reviewed commit or tag")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    export(args.ref, args.output.absolute())


if __name__ == "__main__":
    main()
