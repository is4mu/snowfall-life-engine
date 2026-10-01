"""Slice 5A ScheduleState — pure normalize / hash / validate / build.

Persisted concrete Commitment + ObligationTask snapshot. Read/validate only.
No recurrence generation, no status auto-transitions, no disk I/O, no behavior
orchestration. Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_hash, canonical_json, sort_unordered_collections
from .contracts import validate_commitment, validate_obligation_task
from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, canonicalize_timestamps, parse_rfc3339

SCHEDULE_STATE_HASH_EXCLUSIONS = frozenset({"state_hash", "revision"})


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def semantic_schedule_state_view(state: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy excluding self-referential identity fields."""
    return {
        k: deepcopy(v) for k, v in state.items() if k not in SCHEDULE_STATE_HASH_EXCLUSIONS
    }


def _sort_by_id(
    records: list[Any],
    *,
    id_key: str,
    label: str,
) -> list[Any]:
    if not isinstance(records, list):
        _fail(f"{label}: expected list")
    keyed: list[tuple[str, str, Any]] = []
    for i, item in enumerate(records):
        if not isinstance(item, dict):
            _fail(f"{label}[{i}]: expected object")
        primary = item.get(id_key)
        if not isinstance(primary, str) or not primary:
            _fail(f"{label}[{i}].{id_key}: expected non-empty string")
        keyed.append((primary, canonical_json(item), item))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in keyed]


def _normalize_commitment_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize existing timestamps / unordered lists only (no invent)."""
    obj = sort_unordered_collections(canonicalize_timestamps(dict(row)))
    return obj


def _normalize_task_row(row: Mapping[str, Any]) -> dict[str, Any]:
    obj = sort_unordered_collections(canonicalize_timestamps(dict(row)))
    return obj


def normalize_schedule_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize existing ScheduleState values only.

    - canonicalize ``as_of`` and child timestamps
    - sort commitments by ``commitment_id``
    - sort obligation_tasks by ``task_id``
    - no string/int/list coercion, no invent-missing, no input mutation
    """
    if not isinstance(state, Mapping):
        _fail("schedule_state: expected object")
    reject_binary_floats(dict(state), path="$.schedule_state")
    obj = deepcopy(dict(state))

    if "as_of" in obj and obj["as_of"] is not None:
        if not isinstance(obj["as_of"], str):
            _fail("as_of: timestamp must be string")
        obj["as_of"] = canonicalize_timestamp(obj["as_of"], field="as_of")

    if "commitments" in obj and obj["commitments"] is not None:
        commitments = obj["commitments"]
        if not isinstance(commitments, list):
            _fail("commitments: expected list")
        normalized: list[dict[str, Any]] = []
        for i, item in enumerate(commitments):
            if not isinstance(item, Mapping):
                _fail(f"commitments[{i}]: expected object")
            normalized.append(_normalize_commitment_row(item))
        obj["commitments"] = _sort_by_id(
            normalized, id_key="commitment_id", label="commitments"
        )

    if "obligation_tasks" in obj and obj["obligation_tasks"] is not None:
        tasks = obj["obligation_tasks"]
        if not isinstance(tasks, list):
            _fail("obligation_tasks: expected list")
        normalized_tasks: list[dict[str, Any]] = []
        for i, item in enumerate(tasks):
            if not isinstance(item, Mapping):
                _fail(f"obligation_tasks[{i}]: expected object")
            normalized_tasks.append(_normalize_task_row(item))
        obj["obligation_tasks"] = _sort_by_id(
            normalized_tasks, id_key="task_id", label="obligation_tasks"
        )

    return obj


def compute_schedule_state_hash(state: Mapping[str, Any]) -> str:
    """SHA-256 over normalized semantic payload (excludes revision/state_hash)."""
    normalized = normalize_schedule_state(state)
    view = semantic_schedule_state_view(normalized)
    return canonical_hash(view)


def _unique_ids(values: list[str], *, label: str) -> None:
    if len(values) != len(set(values)):
        _fail(f"{label}: duplicate ids")


def _require_not_after_as_of(ts: str, *, as_of: str, field: str) -> None:
    instant = parse_rfc3339(ts, field=field)
    boundary = parse_rfc3339(as_of, field="as_of")
    if instant > boundary:
        _fail(f"{field} after as_of")


def _validate_no_future_leakage(obj: Mapping[str, Any]) -> None:
    """Reject creation / status-history facts after snapshot as_of.

    Planned future timing / due dates are allowed (purpose of the schedule).
    """
    as_of = obj["as_of"]
    for i, commitment in enumerate(obj["commitments"]):
        _require_not_after_as_of(
            commitment["created_at"],
            as_of=as_of,
            field=f"commitments[{i}].created_at",
        )
        for j, entry in enumerate(commitment["status_history"]):
            _require_not_after_as_of(
                entry["changed_at"],
                as_of=as_of,
                field=f"commitments[{i}].status_history[{j}].changed_at",
            )
    for i, task in enumerate(obj["obligation_tasks"]):
        _require_not_after_as_of(
            task["created_at"],
            as_of=as_of,
            field=f"obligation_tasks[{i}].created_at",
        )


def validate_schedule_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Strict ScheduleState validation. Does not mutate the caller's mapping."""
    if not isinstance(state, Mapping):
        _fail("schedule_state: expected object")
    reject_binary_floats(dict(state), path="$.schedule_state")
    obj = normalize_schedule_state(state)
    validate_instance(obj, "schedule_state")

    commitments = obj["commitments"]
    tasks = obj["obligation_tasks"]
    commitment_ids = [c["commitment_id"] for c in commitments]
    task_ids = [t["task_id"] for t in tasks]
    _unique_ids(commitment_ids, label="schedule.commitments")
    _unique_ids(task_ids, label="schedule.obligation_tasks")

    validated_commitments: list[dict[str, Any]] = []
    for commitment in commitments:
        # Existing Commitment validator; recurrence_id remains opaque.
        validated_commitments.append(validate_commitment(commitment))
    validated_tasks: list[dict[str, Any]] = []
    for task in tasks:
        validated_tasks.append(validate_obligation_task(task))

    obj["commitments"] = _sort_by_id(
        validated_commitments, id_key="commitment_id", label="commitments"
    )
    obj["obligation_tasks"] = _sort_by_id(
        validated_tasks, id_key="task_id", label="obligation_tasks"
    )

    _validate_no_future_leakage(obj)

    digest = compute_schedule_state_hash(obj)
    if obj.get("state_hash") != digest:
        _fail("schedule state_hash does not match semantic content")
    if obj.get("revision") != digest:
        _fail("schedule revision does not match semantic content")
    if obj["revision"] != obj["state_hash"]:
        _fail("schedule revision must equal state_hash")
    return obj


def build_schedule_state(
    *,
    character_id: str,
    as_of: str,
    commitments: Sequence[Mapping[str, Any]] | None = None,
    obligation_tasks: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a sealed ScheduleState from explicit commitment/task facts only.

    Does not invent missing commitments/tasks or generate recurrence records.
    """
    if not isinstance(character_id, str) or not character_id:
        _fail("character_id: expected non-empty string")
    if not isinstance(as_of, str) or not as_of:
        _fail("as_of: expected non-empty string")
    if commitments is None:
        commitments_in: list[Mapping[str, Any]] = []
    elif isinstance(commitments, (list, tuple)):
        commitments_in = list(commitments)
    else:
        _fail("commitments: expected list")
    if obligation_tasks is None:
        tasks_in: list[Mapping[str, Any]] = []
    elif isinstance(obligation_tasks, (list, tuple)):
        tasks_in = list(obligation_tasks)
    else:
        _fail("obligation_tasks: expected list")

    as_of_canon = canonicalize_timestamp(as_of, field="as_of")
    draft: dict[str, Any] = {
        "schema_version": 1,
        "character_id": character_id,
        "as_of": as_of_canon,
        "commitments": [dict(c) for c in commitments_in],
        "obligation_tasks": [dict(t) for t in tasks_in],
        # Placeholders replaced after hash.
        "revision": "0" * 64,
        "state_hash": "0" * 64,
    }
    digest = compute_schedule_state_hash(draft)
    draft["revision"] = digest
    draft["state_hash"] = digest
    return validate_schedule_state(draft)
