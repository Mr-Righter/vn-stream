"""Confined paths and durable atomic filesystem publication."""

import json
import os
from pathlib import Path
from uuid import uuid4

from vn_stream.i18n import text


def safe(base, relative):
    parts = Path(relative)
    if parts.is_absolute() or ".." in parts.parts or "\\" in str(relative) or "\0" in str(relative):
        raise ValueError(text("content.invalid"))
    path = base
    for part in parts.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(text("content.invalid"))
    return path


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        sync_folder(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_folder(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
