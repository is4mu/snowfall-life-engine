"""Active activity and ActualEvent finalize (immutable once written)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .canonical import canonical_hash, normalize_persisted_object
from .captures import (
    normalize_capture_list_for_persistence,
    validate_capture_fact,
    validate_captures_for_actual_event,
)
from .errors import ErrorCode, LifeEngineError
from .ids import IdRegistry, stable_id
from .invariants import validate_active_activity_times, validate_actual_event_times
from .schema import validate_instance
from .timeutil import canonicalize_timestamp, canonicalize_timestamps, parse_rfc3339


def normalize_pending_captures(activity: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Local ActiveActivity pending_captures normalization (not global key rules).

    Raw/persisted pending arrays with duplicate capture_id rows (identical or
    conflicting) are malformed and rejected — no repair/collapse.
    """
    pending = activity.get("pending_captures")
    if pending is None:
        return []
    if not isinstance(pending, list):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "pending_captures must be array")
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for cap_raw in pending:
        cap = validate_capture_fact(cap_raw)
        if cap["capture_id"] in seen_ids:
            raise LifeEngineError(
                ErrorCode.DUPLICATE_ID,
                f"duplicate capture_id in pending_captures: {cap['capture_id']}",
            )
        seen_ids.add(cap["capture_id"])
        validated.append(cap)
    return normalize_capture_list_for_persistence(validated)


def validate_active_activity(activity: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if activity is None:
        return None
    data = canonicalize_timestamps(dict(activity))
    if "schema_version" not in data:
        data["schema_version"] = 1
    # Normalize pending captures locally before schema validate of nested items.
    if "pending_captures" in data:
        data["pending_captures"] = normalize_pending_captures(data)
    validate_instance(data, "active_activity")
    validate_active_activity_times(data)
    # Ownership: each pending capture must belong to this activity.
    for cap in data.get("pending_captures") or []:
        if cap["activity_instance_id"] != data["activity_instance_id"]:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "pending capture.activity_instance_id must equal active_activity.activity_instance_id",
            )
        if parse_rfc3339(cap["captured_at"], field="captured_at") < parse_rfc3339(
            data["actual_start"], field="actual_start"
        ):
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "pending captured_at must be >= active_activity.actual_start",
            )
    return data


def derive_event_id(activity_instance_id: str, *, actual_start: str, actual_end: str) -> str:
    """Distinct ActualEvent ID namespace from activity_instance_id."""
    return stable_id("actual-event", activity_instance_id, actual_start, actual_end)


def normalize_actual_event_for_persistence(event: Mapping[str, Any]) -> dict[str, Any]:
    """Authoritative ActualEvent normalization + capture semantic validation.

    Preserves Foundation timestamp/unordered semantics via normalize_persisted_object,
    then validates and sorts **only** top-level ActualEvent ``captures``.

    Enforces for every capture-bearing event (including externally supplied ones):
    - ActualEvent time invariants
    - each capture passes ``validate_capture_fact``
    - capture.activity_instance_id == event.activity_instance_id
    - actual_start <= captured_at <= actual_end
    - capture_id uniqueness (duplicate rows rejected; no repair/collapse)
    - canonical capture order by (captured_at, capture_id)

    Does **not** reinterpret arbitrary ``payload.captures`` or other unrelated objects.
    Does **not** add generic ``captures`` to global ``_UNORDERED_LIST_KEYS``.
    Empty ``captures: []`` remains valid and hash-compatible with baseline.
    """
    base = normalize_persisted_object(dict(event))
    if not isinstance(base, dict):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "actual_event must be object")
    if "schema_version" not in base:
        base["schema_version"] = 1
    if "captures" not in base:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "actual_event.captures required")
    if not isinstance(base["captures"], list):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "actual_event.captures must be array")
    validate_actual_event_times(base)
    base["captures"] = validate_captures_for_actual_event(base, base["captures"])
    validate_instance(base, "actual_event")
    return base


def build_actual_event_from_activity(
    activity: Mapping[str, Any],
    *,
    actual_end: str,
    finalized_at: str,
) -> dict[str, Any]:
    activity = validate_active_activity(dict(activity))
    assert activity is not None
    end = canonicalize_timestamp(actual_end, field="actual_end")
    fin = canonicalize_timestamp(finalized_at, field="finalized_at")
    instance_id = activity["activity_instance_id"]
    event_id = derive_event_id(instance_id, actual_start=activity["actual_start"], actual_end=end)
    pending = list(activity.get("pending_captures") or [])
    # Slice 5B1: preserve runtime-started provenance into ActualEvent.
    # Legacy/Foundation activities without these fields stay compatible.
    causes = list(activity["causes"]) if "causes" in activity else []
    decision_evidence = (
        deepcopy(activity["decision_evidence"])
        if "decision_evidence" in activity
        else None
    )
    event = {
        "schema_version": 1,
        "event_id": event_id,
        "activity_instance_id": instance_id,
        "event_type": activity["activity_type"],
        "actual_start": activity["actual_start"],
        "actual_end": end,
        "finalized_at": fin,
        "location_id": activity.get("location_id"),
        "summary": activity.get("summary", ""),
        "effects": list(activity.get("effects_on_end", [])),
        "captures": pending,
        "participants": list(activity.get("companions", [])),
        "causes": causes,
        # Foundation finalize records an actual ledger event (not canon).
        "provenance": {"origin": "ACTUAL_EVENT"},
        "decision_evidence": decision_evidence,
    }
    # details: copy when present; omit when missing (ActualEvent permits missing
    # except SLEEP/MEAL/SOCIAL_CONTACT, which runtime-started activities must carry).
    if "details" in activity:
        event["details"] = deepcopy(activity["details"])
    # Slice 5B2C1: preserve dynamics_context when present (C2 finalization audit).
    if "dynamics_context" in activity and activity["dynamics_context"] is not None:
        event["dynamics_context"] = deepcopy(activity["dynamics_context"])
    if event["location_id"] is None:
        del event["location_id"]
    # Do not copy runtime_context wholesale into ActualEvent.
    # Authoritative semantic validation + local capture canonicalization.
    event = normalize_actual_event_for_persistence(event)
    if event_id == instance_id:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "event_id must differ from activity_instance_id")
    return event


def finalize_activity(
    state: dict[str, Any],
    *,
    actual_end: str,
    finalized_at: str | None = None,
    registry: IdRegistry | None = None,
    known_finalized_instance_ids: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert active_activity once into finalized ActualEvent; retry is idempotent."""
    active = state["context"].get("active_activity")
    if active is None:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "no active_activity to finalize")

    instance_id = active["activity_instance_id"]
    if known_finalized_instance_ids and instance_id in known_finalized_instance_ids:
        raise LifeEngineError(
            ErrorCode.DUPLICATE_ID,
            f"active_activity already finalized: {instance_id}",
        )

    end = actual_end
    fin = finalized_at or actual_end
    event = build_actual_event_from_activity(active, actual_end=end, finalized_at=fin)
    semantic = canonical_hash(normalize_actual_event_for_persistence(event))
    reg = registry or IdRegistry()
    reg.register(event["event_id"], semantic)

    out = deepcopy(state)
    out["context"]["active_activity"] = None
    return out, event


def assert_finalized_immutable(prior: Mapping[str, Any], candidate: Mapping[str, Any]) -> None:
    if dict(prior) != dict(candidate):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "finalized ActualEvent is immutable")
