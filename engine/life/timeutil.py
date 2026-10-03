"""Asia/Tokyo timezone-aware timestamps; naive forbidden.

Persisted timestamps are always canonical RFC3339 with explicit +09:00
offset at second precision.

Sub-second rule (Foundation v1): fractional-second inputs are REJECTED
(`INVALID_STATE`). Distinct instants must not silently collapse.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from zoneinfo import ZoneInfo

from .errors import ErrorCode, LifeEngineError

TOKYO = ZoneInfo("Asia/Tokyo")
RFC3339_SECOND = "%Y-%m-%dT%H:%M:%S%z"
CANONICAL_OFFSET = "+09:00"

# Object-path suffixes / keys treated as Foundation timestamps.
_TIMESTAMP_KEY_NAMES = frozenset(
    {
        "proposed_at",
        "approved_at",
        "life_epoch",
        "processed_through",
        "actual_start",
        "actual_end",
        "planned_end",
        "planned_start",
        "expected_end",
        "earliest",
        "latest",
        "finalized_at",
        "due_at",
        "closed_at",
        "last_wake_at",
        "last_main_sleep_end",
        "started_at",
        "reassess_at",
        # Slice 2A Commitment / ObligationTask
        "created_at",
        "earliest_start",
        "latest_start",
        "changed_at",
        "earliest_due",
        "latest_due",
    }
)

_FRACTIONAL_RE = re.compile(r"T\d{2}:\d{2}:\d{2}\.\d+")


def ensure_aware(dt: datetime, *, field: str = "timestamp") -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"naive datetime forbidden: {field}")
    return dt


def parse_rfc3339(value: str, *, field: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"missing timestamp: {field}")
    text = value.strip()
    if _FRACTIONAL_RE.search(text):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"sub-second timestamps rejected (Foundation second precision): {field}={value}",
        )
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        if len(text) >= 6 and text[-3] == ":" and (text[-6] in "+-"):
            dt = datetime.fromisoformat(text)
        else:
            dt = datetime.strptime(text, RFC3339_SECOND)
    except ValueError as exc:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"bad timestamp {field}: {value}") from exc
    aware = ensure_aware(dt, field=field).astimezone(TOKYO)
    if aware.microsecond != 0:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"sub-second timestamps rejected: {field}={value}",
        )
    return aware


def format_rfc3339(dt: datetime) -> str:
    """Canonical persist form: Asia/Tokyo, second precision, explicit +09:00."""
    aware = ensure_aware(dt).astimezone(TOKYO)
    if aware.microsecond != 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "sub-second datetime forbidden in format")
    return aware.strftime("%Y-%m-%dT%H:%M:%S") + CANONICAL_OFFSET


def canonicalize_timestamp(value: str, *, field: str = "timestamp") -> str:
    """Parse any accepted aware instant → single canonical Tokyo RFC3339 string."""
    return format_rfc3339(parse_rfc3339(value, field=field))


def require_canonical_timestamp(value: object, *, field: str = "timestamp") -> str:
    """Reject noncanonical spellings (Z / other offsets / non-exact Tokyo form)."""
    if not isinstance(value, str) or not value:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"missing canonical timestamp: {field}")
    canonical = format_rfc3339(parse_rfc3339(value, field=field))
    if value != canonical:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"noncanonical timestamp rejected: {field}={value!r} (expected {canonical})",
        )
    return canonical


def _is_timestamp_key(key: str) -> bool:
    return key in _TIMESTAMP_KEY_NAMES


def canonicalize_timestamps(obj: Any, *, path: str = "$") -> Any:
    """Object-aware deep canonicalize of Foundation timestamp fields.

    Any dict key whose name is a known Foundation timestamp field is normalized
    (or left null). Nested structures (conditions, expected_end_window, etc.)
    are covered without relying solely on a flat list of top-level keys.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            child_path = f"{path}.{key}"
            if _is_timestamp_key(key):
                if value is None:
                    out[key] = None
                elif isinstance(value, str):
                    out[key] = canonicalize_timestamp(value, field=child_path)
                else:
                    raise LifeEngineError(
                        ErrorCode.INVALID_STATE,
                        f"timestamp field must be string or null: {child_path}",
                    )
            else:
                out[key] = canonicalize_timestamps(value, path=child_path)
        return out
    if isinstance(obj, list):
        return [canonicalize_timestamps(v, path=f"{path}[{i}]") for i, v in enumerate(obj)]
    return obj


def tokyo_date(dt: datetime) -> date:
    return ensure_aware(dt).astimezone(TOKYO).date()


def next_day_midnight(date_str: str) -> datetime:
    """Deterministic logical closure instant: next calendar day 00:00:00+09:00."""
    day = parse_rfc3339(f"{date_str}T00:00:00+09:00").date()
    nxt = day + timedelta(days=1)
    return parse_rfc3339(f"{nxt.isoformat()}T00:00:00+09:00")


def days_between(a: datetime, b: datetime) -> float:
    return (ensure_aware(b) - ensure_aware(a)).total_seconds() / 86400.0


def add_minutes(dt: datetime, minutes: int) -> datetime:
    return ensure_aware(dt) + timedelta(minutes=minutes)
