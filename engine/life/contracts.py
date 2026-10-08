"""Slice 2A planning contract validators (Commitment / Obligation / Route / Social)."""

from __future__ import annotations

from typing import Any, Mapping

from .canonical import sort_unordered_collections
from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamps, parse_rfc3339


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _unique_strings(values: list[str], *, label: str) -> None:
    if len(values) != len(set(values)):
        _fail(f"{label}: duplicate ids")


def _is_bootstrap_import(provenance: Mapping[str, Any]) -> bool:
    """Explicit bootstrap/import exception for historical-creation guard."""
    return provenance.get("origin") == "SIMULATION_BOOTSTRAP"


def _timing_historical_boundary(timing: Mapping[str, Any]):
    if timing["timing_kind"] == "EXACT":
        return parse_rfc3339(timing["planned_end"], field="timing.planned_end")
    return parse_rfc3339(timing["latest_start"], field="timing.latest_start")


def validate_commitment(data: Mapping[str, Any]) -> dict[str, Any]:
    obj = canonicalize_timestamps(dict(data))
    obj = sort_unordered_collections(obj)
    reject_binary_floats(obj)
    validate_instance(obj, "commitment")

    timing = obj["timing"]
    if timing["timing_kind"] == "EXACT":
        start = parse_rfc3339(timing["planned_start"], field="timing.planned_start")
        end = parse_rfc3339(timing["planned_end"], field="timing.planned_end")
        if end < start:
            _fail("commitment EXACT planned_end < planned_start")
    else:
        earliest = parse_rfc3339(timing["earliest_start"], field="timing.earliest_start")
        latest = parse_rfc3339(timing["latest_start"], field="timing.latest_start")
        if latest < earliest:
            _fail("commitment WINDOW earliest_start > latest_start")
        if int(timing["duration_min"]) <= 0:
            _fail("commitment WINDOW duration_min must be > 0")
        if int(timing["duration_min"]) > int(timing["duration_max"]):
            _fail("commitment WINDOW duration_min > duration_max")

    history = obj["status_history"]
    if history[0]["from"] is not None or history[0]["to"] != "PLANNED":
        _fail("commitment status_history initial transition must be null -> PLANNED")

    prev_at = None
    prev_to = None
    for i, entry in enumerate(history):
        changed = parse_rfc3339(entry["changed_at"], field=f"status_history[{i}].changed_at")
        if prev_at is not None and changed < prev_at:
            _fail("commitment status_history timestamps must be monotonic")
        if i == 0:
            if entry["from"] is not None:
                _fail("commitment status_history[0].from must be null")
        else:
            if entry["from"] != prev_to:
                _fail(
                    f"commitment status_history[{i}].from must equal prior to "
                    f"(got {entry['from']!r}, prior {prev_to!r})"
                )
        prev_at = changed
        prev_to = entry["to"]

    if history[-1]["to"] != obj["status"]:
        _fail("commitment status must equal latest status_history.to")

    created = parse_rfc3339(obj["created_at"], field="created_at")
    first_hist = parse_rfc3339(history[0]["changed_at"], field="status_history[0].changed_at")
    if created > first_hist:
        _fail("commitment created_at after first status_history.changed_at")

    # Historical-creation guard: normal commitments cannot be invented after timing ended.
    boundary = _timing_historical_boundary(timing)
    if created > boundary and not _is_bootstrap_import(obj["provenance"]):
        _fail(
            "commitment created_at after timing became historical "
            "(requires provenance.origin=SIMULATION_BOOTSTRAP)"
        )

    _unique_strings(list(obj["participants"]), label="commitment.participants")
    return obj


def validate_obligation_task(data: Mapping[str, Any]) -> dict[str, Any]:
    obj = canonicalize_timestamps(dict(data))
    obj = sort_unordered_collections(obj)
    reject_binary_floats(obj)
    validate_instance(obj, "obligation_task")

    due_at = obj.get("due_at")
    due_window = obj.get("due_window")
    if due_at is not None and due_window is not None:
        _fail("obligation_task due_at and due_window are mutually exclusive")
    if due_window is not None:
        earliest = parse_rfc3339(due_window["earliest_due"], field="due_window.earliest_due")
        latest = parse_rfc3339(due_window["latest_due"], field="due_window.latest_due")
        if earliest > latest:
            _fail("obligation_task due_window earliest_due > latest_due")

    effort = int(obj["effort_remaining_min"])
    chunk = int(obj["min_work_chunk_min"])
    status = obj["status"]
    if chunk <= 0:
        _fail("obligation_task min_work_chunk_min must be > 0")
    if effort < 0:
        _fail("obligation_task effort_remaining_min must be >= 0")
    if status in {"OPEN", "IN_PROGRESS"}:
        if effort <= 0:
            _fail(f"obligation_task {status} requires effort_remaining_min > 0")
        if chunk > effort:
            _fail(
                "obligation_task min_work_chunk_min must be <= effort_remaining_min "
                f"for {status}"
            )
    elif status == "DONE":
        if effort != 0:
            _fail("obligation_task DONE requires effort_remaining_min == 0")
    # CANCELLED may preserve a remaining estimate.

    created = parse_rfc3339(obj["created_at"], field="created_at")
    earliest_start = obj.get("earliest_start")
    earliest_dt = None
    if earliest_start is not None:
        earliest_dt = parse_rfc3339(earliest_start, field="earliest_start")
        if earliest_dt < created:
            _fail("obligation_task earliest_start before created_at")

    deadline = None
    if due_at is not None:
        deadline = parse_rfc3339(due_at, field="due_at")
    elif due_window is not None:
        deadline = parse_rfc3339(due_window["latest_due"], field="due_window.latest_due")
    if deadline is not None:
        if created > deadline:
            _fail("obligation_task created_at after deadline")
        if earliest_dt is not None and earliest_dt > deadline:
            _fail("obligation_task earliest_start after deadline")

    _unique_strings(list(obj["location_constraints"]), label="obligation_task.location_constraints")
    return obj


def validate_route_profile(data: Mapping[str, Any]) -> dict[str, Any]:
    obj = dict(data)
    reject_binary_floats(obj)
    validate_instance(obj, "route_profile")

    if int(obj["duration_min"]) <= 0:
        _fail("route_profile duration_min must be > 0")
    if int(obj["duration_min"]) > int(obj["duration_max"]):
        _fail("route_profile duration_min > duration_max")
    if obj["origin_location_id"] == obj["destination_location_id"]:
        _fail("route_profile origin_location_id must differ from destination_location_id")
    return obj


def validate_social_archetype_assignment(data: Mapping[str, Any]) -> dict[str, Any]:
    obj = dict(data)
    reject_binary_floats(obj)
    validate_instance(obj, "social_archetype_assignment")
    return obj
