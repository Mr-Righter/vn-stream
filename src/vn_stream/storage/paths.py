"""Service-owned data locations, separate from original game content."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataPaths:
    root: Path

    def initialize(self) -> None:
        for name in (
            "database",
            "runtimes",
            "uploads",
            "tasks",
            "save-backups",
            "cache",
            "tmp",
            "logs",
        ):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @property
    def database(self) -> Path:
        return self.root / "database/vn-stream.sqlite3"

    @property
    def session(self) -> Path:
        return self.root / "tasks/session"

    @property
    def project_cache(self) -> Path:
        return self.root / "cache/projects"
