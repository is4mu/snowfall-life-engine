"""Fail-closed Foundation invariants (Requirement 18 / contract §21)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from .errors import ErrorCode, LifeEngineError
from .timeutil import canonicalize_timestamp, format_rfc3339, parse_rfc3339, require_canonical_timestamp

# Closed v2 THRESHOLD (metric, target_band, direction) — keep in sync with
# schemas/life/queued_internal_event.schema.json and engine.life.wakeups.
_ALLOWED_V2_THRESHOLD_TUPLES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("HUNGER", "MEAL_NEEDED", "UP"),
        ("HUNGER", "URGENT", "UP"),
        ("PHYSICAL_FATIGUE", "HIGH", "UP"),
        ("PHYSICAL_FATIGUE", "VERY_HIGH", "UP"),
        ("STRESS", "HIGH", "UP"),
        ("STRESS", "VERY_HIGH", "UP"),
        ("SOCIAL_BATTERY", "LOW", "DOWN"),
        ("SLEEP_PRESSURE", "HIGH", "UP"),
        ("SLEEP_PRESSURE", "CRITICAL", "UP"),
    }
)


def assert_time_order(earlier: str, later: str, *, label: str) -> None:
    a = parse_rfc3339(earlier, field=f"{label}.earlier")
    b = parse_rfc3339(later, field=f"{label}.later")
    if b < a:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label}: {later} < {earlier}")


def validate_active_activity_times(activity: Mapping[str, Any]) -> None:
    start = activity["actual_start"]
    planned = activity.get("planned_end")
    if planned is not None:
        assert_time_order(start, planned, label="active_activity.planned_end>=actual_start")
    expected_end = activity.get("expected_end")
    if expected_end is not None:
        assert_time_order(start, expected_end, label="active_activity.expected_end>=actual_start")
    window = activity.get("expected_end_window")
    if isinstance(window, dict):
        earliest = window.get("earliest")
        latest = window.get("latest")
        if earliest is not None:
            assert_time_order(start, earliest, label="active_activity.window.earliest>=actual_start")
        if earliest is not None and latest is not None:
            assert_time_order(earliest, latest, label="active_activity.window.latest>=earliest")
        elif latest is not None:
            assert_time_order(start, latest, label="active_activity.window.latest>=actual_start")
        if expected_end is not None:
            if earliest is not None:
                assert_time_order(
                    earliest,
                    expected_end,
                    label="active_activity.earliest<=expected_end",
                )
            if latest is not None:
                assert_time_order(
                    expected_end,
                    latest,
                    label="active_activity.expected_end<=latest",
                )


def validate_actual_event_times(event: Mapping[str, Any]) -> None:
    assert_time_order(
        event["actual_start"],
        event["actual_end"],
        label="actual_event.actual_end>=actual_start",
    )
    assert_time_order(
        event["actual_end"],
        event["finalized_at"],
        label="actual_event.finalized_at>=actual_end",
    )


def assert_monotonic_timeline_events(events: Sequence[Mapping[str, Any]]) -> None:
    prev_key: tuple[Any, str] | None = None
    for event in events:
        key = (parse_rfc3339(event["actual_start"], field="actual_start"), event["event_id"])
        if prev_key is not None and key < prev_key:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"timeline ordering violation: {event['event_id']} before prior",
            )
        prev_key = key


def assert_unique_event_ids(events: Iterable[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for event in events:
        eid = event["event_id"]
        if eid in seen:
            raise LifeEngineError(ErrorCode.DUPLICATE_ID, f"ledger duplicate: {eid}")
        seen.add(eid)


def assert_active_not_already_finalized(
    active: Mapping[str, Any] | None,
    finalized_instance_ids: Iterable[str],
) -> None:
    if active is None:
        return
    aid = active["activity_instance_id"]
    if aid in set(finalized_instance_ids):
        raise LifeEngineError(
            ErrorCode.DUPLICATE_ID,
            f"active_activity already finalized: {aid}",
        )


def collect_finalized_instance_ids(events: Iterable[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for e in events:
        inst = e.get("activity_instance_id")
        if inst:
            out.add(inst)
    return out


def collect_finalized_ids(events: Iterable[Mapping[str, Any]]) -> set[str]:
    return {e["event_id"] for e in events}


def assert_unique_condition_ids(conditions: Sequence[Mapping[str, Any]]) -> None:
    seen: set[str] = set()
    for cond in conditions:
        cid = cond.get("condition_id")
        if not cid:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "condition missing condition_id")
        if cid in seen:
            raise LifeEngineError(ErrorCode.DUPLICATE_ID, f"duplicate condition_id: {cid}")
        seen.add(cid)


_HUMAN_FIELDS = frozenset(
    {"hunger", "physical_fatigue", "stress", "affect_valence", "social_battery"}
)


def queue_event_sort_key(event: Mapping[str, Any]) -> tuple[datetime, int, str]:
    """Canonical scheduler order: due_at → priority → event_id."""
    return (
        parse_rfc3339(event["due_at"], field="due_at"),
        int(event["priority"]),
        str(event["event_id"]),
    )


def assert_queue_canonically_sorted(events: Sequence[Mapping[str, Any]]) -> None:
    expected = sorted(events, key=queue_event_sort_key)
    if list(events) != list(expected):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "pending_queue.events not in canonical order (due_at, priority, event_id)",
        )


def validate_pending_queue(state: Mapping[str, Any]) -> None:
    """Queue-level integrity (#5 Req 18): unique IDs, refs, cursor, order, temporal bounds.

    Active activity may have zero or one pending ACTIVITY_END (open-ended activities
    are valid; a matching end is optional until resolved).
    """
    queue = state["pending_queue"]
    cursor = queue.get("cursor")
    events = queue.get("events") or []
    if cursor != 0:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "pending_queue.cursor must be 0 (Foundation compacted queue)",
        )
    if not isinstance(events, list):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "pending_queue.events must be a list")

    assert_queue_canonically_sorted(events)

    processed = parse_rfc3339(state["processed_through"], field="processed_through")
    active = state["context"].get("active_activity")
    if active is not None:
        start = parse_rfc3339(active["actual_start"], field="actual_start")
        if start > processed:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "active_activity.actual_start > processed_through",
            )

    seen_ids: set[str] = set()
    activity_ends: list[dict[str, Any]] = []
    managed_v2_wakeups = 0
    for event in events:
        eid = event["event_id"]
        if eid in seen_ids:
            raise LifeEngineError(ErrorCode.DUPLICATE_ID, f"pending queue duplicate: {eid}")
        seen_ids.add(eid)

        due = parse_rfc3339(event["due_at"], field="due_at")
        if due < processed:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"pending event due_at < processed_through: {eid}",
            )

        kind = event["event_kind"]
        payload = event.get("payload") or {}
        if (
            int(event.get("schema_version", 1)) == 2
            and kind == "DECISION_WAKEUP"
        ):
            managed_v2_wakeups += 1
            if managed_v2_wakeups > 1:
                raise LifeEngineError(
                    ErrorCode.INVALID_STATE,
                    "managed v2 DECISION_WAKEUP count must be <= 1",
                )
        if kind == "ACTIVITY_END":
            activity_ends.append(dict(event))
        elif kind == "DECISION_WAKEUP":
            schema_version = int(event.get("schema_version", 1))
            if schema_version == 2:
                # Slice 2C managed semantic wakeup — fail closed on due_at spelling
                require_canonical_timestamp(event.get("due_at"), field="due_at")
                trigger = payload.get("trigger_kind")
                if not payload.get("decision_key"):
                    raise LifeEngineError(
                        ErrorCode.INVALID_STATE, "v2 DECISION_WAKEUP missing decision_key"
                    )
                if not payload.get("reason_code"):
                    raise LifeEngineError(
                        ErrorCode.INVALID_STATE, "v2 DECISION_WAKEUP missing reason_code"
                    )
                if trigger == "THRESHOLD":
                    metric = payload.get("metric")
                    band = payload.get("target_band")
                    direction = payload.get("direction")
                    if metric is None:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE, "v2 THRESHOLD requires metric"
                        )
                    if band is None:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE, "v2 THRESHOLD requires target_band"
                        )
                    if direction is None:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE, "v2 THRESHOLD requires direction"
                        )
                    tup = (metric, band, direction)
                    if tup not in _ALLOWED_V2_THRESHOLD_TUPLES:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE,
                            f"invalid v2 THRESHOLD tuple: {tup!r}",
                        )
                else:
                    if payload.get("metric") is not None:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE,
                            "v2 non-THRESHOLD metric must be null",
                        )
                    if payload.get("target_band") is not None:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE,
                            "v2 non-THRESHOLD target_band must be null",
                        )
                    if payload.get("direction") is not None:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE,
                            "v2 non-THRESHOLD direction must be null",
                        )
            else:
                reason = str(payload.get("reason") or "")
                field = payload.get("field")
                if not payload.get("decision_key"):
                    raise LifeEngineError(
                        ErrorCode.INVALID_STATE, "DECISION_WAKEUP missing decision_key"
                    )
                if reason.startswith("threshold:"):
                    if field is None:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE,
                            "threshold DECISION_WAKEUP requires field",
                        )
                    expected = reason.split(":", 1)[1]
                    if field != expected:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE,
                            f"threshold reason/field mismatch: {reason} vs {field}",
                        )
                    if field not in _HUMAN_FIELDS:
                        raise LifeEngineError(
                            ErrorCode.INVALID_STATE, f"bad threshold field: {field}"
                        )

    if active is None:
        if activity_ends:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "ACTIVITY_END without active_activity",
            )
        return

    instance_id = active["activity_instance_id"]
    matching = [
        e
        for e in activity_ends
        if (e.get("payload") or {}).get("activity_instance_id") == instance_id
    ]
    if len(activity_ends) != len(matching):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "ACTIVITY_END activity_instance_id does not match active_activity",
        )
    if len(matching) > 1:
        raise LifeEngineError(
            ErrorCode.DUPLICATE_ID,
            f"multiple ACTIVITY_END for active instance {instance_id}",
        )
    # zero or one matching ACTIVITY_END is valid
    for end_event in matching:
        assert_time_order(
            active["actual_start"],
            end_event["due_at"],
            label="ACTIVITY_END.due_at>=active.actual_start",
        )


def assert_persisted_canonical(obj: Mapping[str, Any], *, label: str) -> None:
    """Fail closed if object is not already in Foundation persisted representation."""
    from .canonical import normalize_persisted_object

    normalized = normalize_persisted_object(dict(obj))
    if dict(obj) != normalized:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"non-canonical persisted representation: {label}",
        )


def validate_timeline_day_semantics(
    day: Mapping[str, Any],
    *,
    processed_through: str | None = None,
    active_activity: Mapping[str, Any] | None = None,
) -> None:
    """Day metadata + belonging/monotonic/closed_at + canonical persistence."""
    from .timeline import belonging_date, day_has_unresolved_activity, logical_closed_at

    assert_persisted_canonical(day, label=f"timeline/{day.get('date')}")

    date_str = day["date"]
    events = list(day.get("actual_events") or [])
    for event in events:
        belonging = belonging_date(event["actual_start"])
        if belonging != date_str:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"event {event.get('event_id')} belongs to {belonging}, day is {date_str}",
            )
    assert_monotonic_timeline_events(events)

    status = day["status"]
    closed_at = day.get("closed_at")
    if status == "OPEN":
        if closed_at is not None:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"OPEN day {date_str} has closed_at")
        if processed_through is not None:
            as_of = parse_rfc3339(processed_through, field="processed_through")
            fake_state = {"context": {"active_activity": active_activity}}
            if (
                not day_has_unresolved_activity(fake_state, date_str)
                and as_of >= logical_closed_at(dict(day))
            ):
                raise LifeEngineError(
                    ErrorCode.INVALID_STATE,
                    f"elapsed OPEN day must be CLOSED: {date_str}",
                )
    elif status == "CLOSED":
        if closed_at is None:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"CLOSED day {date_str} missing closed_at")
        canon = canonicalize_timestamp(closed_at, field="closed_at")
        expected = format_rfc3339(logical_closed_at(dict(day)))
        if canon != expected:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"closed_at {canon} != logical_closed_at {expected} for {date_str}",
            )
    else:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"bad day status: {status}")
