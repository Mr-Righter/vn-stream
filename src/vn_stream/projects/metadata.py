"""Bounded static display metadata; directory sizes stay cached across parser upgrades."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from vn_stream.assets.archive_index import Resources, image_type, safe_path
from vn_stream.engines.renpy.static_config import configuration
from vn_stream.projects.windows_icon import launcher_icon

_fallback_cache: dict[Path, dict] = {}
SCHEMA = 3


def safe_asset(project: Path, relative: str) -> Path | None:
    return safe_path(project, relative)


def _replace(cache: Path, content: bytes):
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_name(cache.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)


def _json(data):
    if not data:
        return {}
    try:
        content = json.loads(data)
        return content if isinstance(content, dict) else {}
    except (ValueError, UnicodeError, RecursionError):
        return {}


def game_info(project: Path, cache: Path, *, refresh: bool = False, engine_id="renpy") -> dict:
    cached = _fallback_cache.get(cache, {})
    if not cached:
        try:
            content = json.loads(cache.read_text())
            cached = content if isinstance(content, dict) else {}
        except (OSError, ValueError, RecursionError):
            pass
    if not refresh and cached.get("schema") == SCHEMA:
        return cached.copy()
    size = cached.get("size_bytes")
    result = {
        "schema": SCHEMA,
        "display_name": None,
        "release_version": None,
        "icon": None,
        "size_bytes": size if type(size) is int and size >= 0 else None,
        "icon_mime": None,
        "icon_revision": None,
        "metadata_sources": {},
    }
    resources = Resources(project) if engine_id == "renpy" else None
    # Android's package version may simply be 1.0: game build/config data outrank it.
    # Different files at the same tier must agree; file order is not authority.
    platform = []
    for relative in ("assets/x-android.json", "android.json"):
        path = safe_asset(project, relative)
        try:
            content = _json(path.read_bytes()) if path else {}
        except OSError:
            content = {}
        platform.append((content, relative))
    builds = []
    for relative in ("build_info.json", "cache/build_info.json"):
        found = resources.read(relative) if resources else None
        if found:
            builds.append((_json(found[0]), found[1]))
    for tier in (platform, builds):
        for field, key in (("name", "display_name"), ("version", "release_version")):
            candidates = [
                (content[field], source)
                for content, source in tier
                if isinstance(content.get(field), str) and 0 < len(content[field]) <= 512
            ]
            if candidates:
                values = {value for value, _ in candidates}
                result[key] = next(iter(values)) if len(values) == 1 else None
                result["metadata_sources"][key] = sorted({source for _, source in candidates})
    values, sources = configuration(resources) if resources else ({}, {})
    for key in ("display_name", "release_version"):
        if key in sources:
            result[key] = values.get(key)
            result["metadata_sources"][key] = sorted(set(sources[key]))

    image = None
    image_source = None
    candidates = [values.get("window_icon")]
    candidates += [f"gui/window_icon.{ext}" for ext in ("png", "webp", "jpg", "jpeg", "ico")]
    candidates += [f"icon.{ext}" for ext in ("png", "webp", "jpg", "jpeg", "ico")]
    for relative in candidates:
        if not relative:
            continue
        found = resources.read(relative) if resources else None
        if found and image_type(found[0]):
            image, image_source = found
            result["icon"] = "@asset" if ":" in image_source else image_source
            break
    if image is None:
        for relative in ("icon.png", "icon.webp", "icon.jpg", "icon.ico"):
            path = safe_asset(project, relative)
            try:
                data = path.read_bytes() if path else b""
            except OSError:
                continue
            if image_type(data):
                image, image_source = data, relative
                result["icon"] = relative
                break
    if image is None:
        image = launcher_icon(project)
        if image:
            image_source = "launcher_resource"
            result["icon"] = "@asset"
    if image is not None:
        if result["icon"] == "@asset":
            try:
                _replace(cache.with_suffix(".icon"), image)
            except OSError:
                result["icon"] = None
        if result["icon"]:
            result["icon_mime"] = image_type(image)
            result["icon_revision"] = hashlib.sha256(image).hexdigest()[:16]
            result["metadata_sources"]["icon"] = [image_source]

    # Parser upgrades refresh metadata without walking large NAS games again.
    # Only import, explicit refresh, or a missing size cache computes the full size.
    if project.is_dir() and (refresh or result["size_bytes"] is None):
        size = 0
        for root, dirs, files in os.walk(project, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
            for name in files:
                path = Path(root) / name
                try:
                    if not path.is_symlink() and path.is_file():
                        size += path.stat().st_size
                except OSError:
                    pass
        result["size_bytes"] = size
    try:
        _replace(cache, json.dumps(result, ensure_ascii=False).encode())
        _fallback_cache.pop(cache, None)
    except OSError:
        if len(_fallback_cache) >= 128:
            _fallback_cache.clear()
        _fallback_cache[cache] = result.copy()
    return result
