"""Bounded reads of VN Stream project identity records."""

import json
from pathlib import Path

from vn_stream.i18n import text


def _read_json(path: Path) -> dict:
    if path.is_symlink():
        raise ValueError(text("errors.backupMetadataContainsALink"))
    with path.open(encoding="utf-8") as file:
        content = file.read(64 * 1024 + 1)
    if len(content) > 64 * 1024:
        raise ValueError(text("errors.backupMetadataExceedsTheSizeLimit"))
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError(text("errors.invalidBackupMetadataFormat"))
    return value


def read_manifest(project: Path, project_id: str | None = None) -> dict:
    if (project / ".vn-stream").is_symlink():
        raise ValueError(text("errors.theBackupMetadataFolderContainsALink"))
    manifest = _read_json(project / ".vn-stream/project.json")
    if manifest.get("format") != "vn-stream/1" or (
        project_id is not None and manifest.get("project_id") != project_id
    ):
        raise ValueError(text("errors.theBackupIdentityDoesNotMatchItsFolder"))
    engine_id = manifest.get("engine_id", "renpy")
    if (
        not isinstance(manifest.get("name"), str)
        or not project.is_dir()
        or (engine_id == "renpy" and not (project / "game").is_dir())
    ):
        raise ValueError(text("errors.theBackedUpGameStructureIsIncomplete"))
    return manifest
