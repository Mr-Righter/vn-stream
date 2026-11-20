"""Explicit response construction; durable records are never modified here."""

from datetime import UTC, datetime
from math import isfinite

from vn_stream.api.middleware import request_locale
from vn_stream.i18n import localize_payload


def timestamp(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not isfinite(value):
            return None
        try:
            return (
                datetime.fromtimestamp(value, UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        except (OverflowError, OSError, ValueError):
            return None
    return value


def success(value):
    return {"data": localize_payload(value, request_locale.get())}


def paginated(value, *, page: int, page_size: int, total: int):
    return {
        **success(value),
        "pagination": {"page": page, "page_size": page_size, "total": total},
    }


def task_progress(record):
    if record.get("state") == "completed":
        return 100
    total = record.get("total_bytes")
    return min(100, max(0, int(100 * (record.get("completed_bytes") or 0) / total))) if total else 0
