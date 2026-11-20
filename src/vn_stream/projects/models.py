"""Project identity, independent of catalog persistence."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    path: str
    engine_version: str | None
    engine_id: str = "renpy"
