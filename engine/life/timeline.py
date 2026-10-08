"""Timeline day OPEN/CLOSED ledger helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from .errors import ErrorCode, LifeEngineError
from .events import normalize_actual_event_for_persistence
from .invariants import assert_monotonic_timeline_events
from .schema import validate_instance
from .timeutil import (
    format_rfc3339,
    next_day_midnight,
    parse_rfc3339,
    tokyo_date,
)


def empty_day(date_str: str) -> dict[str, Any]:
    day = {
        "schema_version": 1,
        "date": date_str,
        "timezone": "Asia/Tokyo",
        "status": "OPEN",
        "closed_at": None,
        "actual_events": [],
    }
    validate_instance(day, "timeline_day")
    return day


def belonging_date(actual_start: str) -> str:
    return tokyo_date(parse_rfc3339(actual_start, field="actual_start")).isoformat()


def logical_closed_at(day: dict[str, Any]) -> datetime:
    """Deterministic closure instant (heartbeat-independent).

    - If any finalized event on this day ends after the calendar day (overnight),
      closed_at is that event's actual_end (latest such end).
    - Otherwise closed_at is next-day 00:00:00+09:00.
    """
    day_date = parse_rfc3339(f"{day['date']}T00:00:00+09:00").date()
    overnight_ends: list[datetime] = []
    for event in day.get("actual_events", []):
        end = parse_rfc3339(event["actual_end"], field="actual_end")
        if tokyo_date(end) > day_date:
            overnight_ends.append(end)
    if overnight_ends:
        return max(overnight_ends)
    return next_day_midnight(day["date"])


def close_day(day: dict[str, Any], closed_at: datetime | None = None) -> dict[str, Any]:
    out = deepcopy(day)
    if out["status"] == "CLOSED":
        return out
    instant = closed_at if closed_at is not None else logical_closed_at(out)
    out["status"] = "CLOSED"
    out["closed_at"] = format_rfc3339(instant)
    validate_instance(out, "timeline_day")
    return out


def append_actual_event(day: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    # Authoritative capture semantic validation + ActualEvent-local canonicalization.
    event = normalize_actual_event_for_persistence(dict(event))
    if day["status"] == "CLOSED":
        raise LifeEngineError(ErrorCode.CLOSED_DAY_MUTATION, day["date"])
    expected = belonging_date(event["actual_start"])
    if day["date"] != expected:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"event belongs to {expected}, day is {day['date']}",
        )
    out = deepcopy(day)
    existing_ids = {e["event_id"] for e in out["actual_events"]}
    if event["event_id"] in existing_ids:
        prior = next(e for e in out["actual_events"] if e["event_id"] == event["event_id"])
        if prior != event:
            raise LifeEngineError(ErrorCode.DUPLICATE_ID, event["event_id"])
        return out
    if out["actual_events"]:
        last = out["actual_events"][-1]
        assert_monotonic_timeline_events([last, event])
    out["actual_events"].append(event)
    validate_instance(out, "timeline_day")
    return out


def day_has_unresolved_activity(state: dict[str, Any], date_str: str) -> bool:
    active = state["context"].get("active_activity")
    if active is None:
        return False
    return belonging_date(active["actual_start"]) == date_str


def should_close_day(state: dict[str, Any], day: dict[str, Any], *, as_of: datetime) -> bool:
    """Closable once as_of reaches deterministic logical_closed_at and no unresolved activity."""
    if day["status"] == "CLOSED":
        return False
    if day_has_unresolved_activity(state, day["date"]):
        return False
    return as_of >= logical_closed_at(day)
