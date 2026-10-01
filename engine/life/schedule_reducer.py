"""Slice 5B1 / 5B2C2B — pure ScheduleState reducers (TASK_PROGRESS / COMMITMENT_COMPLETE).

Does not generate recurrence, invent progress from duration, implement
CANCELLED/MISSED/DEFERRED, or decide when to emit commitment completion
(5B2C2B owns derivation). No disk/Git/life branch.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .canonical import canonical_json
from .contracts import validate_commitment
from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .schedule_state import build_schedule_state, validate_schedule_state
from .timeutil import canonicalize_timestamp, parse_rfc3339

COMPLETION_REASON = "actual-event-completed"


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label}: expected non-empty string")
    return value


def _require_true_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label}: expected true int (no bool/float/string)")
    return value


def normalize_task_progress_effect(effect: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic normalize for strict TASK_PROGRESS; does not mutate caller input."""
    data = dict(effect)
    if "schema_version" not in data:
        data["schema_version"] = 1
    reject_binary_floats(data, path="$.effect")
    validate_instance(data, "effect")
    if data.get("effect_type") != "TASK_PROGRESS":
        _fail(f"expected TASK_PROGRESS, got {data.get('effect_type')!r}")
    raw_payload = data.get("payload")
    if not isinstance(raw_payload, Mapping):
        _fail("TASK_PROGRESS payload must be an object")

    task_id = _require_str(raw_payload.get("task_id"), "task_id")
    progress_at = canonicalize_timestamp(
        _require_str(raw_payload.get("progress_at"), "progress_at"),
        field="progress_at",
    )
    spent = _require_true_int(raw_payload.get("effort_spent_min"), "effort_spent_min")
    remaining = _require_true_int(
        raw_payload.get("effort_remaining_after_min"), "effort_remaining_after_min"
    )
    status_after = _require_str(raw_payload.get("status_after"), "status_after")
    if status_after not in {"IN_PROGRESS", "DONE"}:
        _fail(f"status_after: unsupported {status_after!r}")
    if spent < 1:
        _fail("effort_spent_min must be >= 1")
    if remaining < 0:
        _fail("effort_remaining_after_min must be >= 0")

    data["payload"] = {
        "task_id": task_id,
        "progress_at": progress_at,
        "effort_spent_min": spent,
        "effort_remaining_after_min": remaining,
        "status_after": status_after,
    }
    return data


def normalize_commitment_complete_effect(effect: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic normalize for strict COMMITMENT_COMPLETE; does not mutate caller input."""
    data = dict(effect)
    if "schema_version" not in data:
        data["schema_version"] = 1
    reject_binary_floats(data, path="$.effect")
    validate_instance(data, "effect")
    if data.get("effect_type") != "COMMITMENT_COMPLETE":
        _fail(f"expected COMMITMENT_COMPLETE, got {data.get('effect_type')!r}")
    raw_payload = data.get("payload")
    if not isinstance(raw_payload, Mapping):
        _fail("COMMITMENT_COMPLETE payload must be an object")

    commitment_id = _require_str(raw_payload.get("commitment_id"), "commitment_id")
    completed_at = canonicalize_timestamp(
        _require_str(raw_payload.get("completed_at"), "completed_at"),
        field="completed_at",
    )
    data["payload"] = {
        "commitment_id": commitment_id,
        "completed_at": completed_at,
    }
    return data


def reduce_task_progress(
    schedule_state: Mapping[str, Any],
    effect: Mapping[str, Any],
    *,
    source_event_id: str,
) -> dict[str, Any]:
    """Pure ScheduleState transition for one strict TASK_PROGRESS effect."""
    _require_str(source_event_id, "source_event_id")
    prior = validate_schedule_state(schedule_state)
    typed = normalize_task_progress_effect(effect)
    payload = typed["payload"]

    task_id = payload["task_id"]
    progress_at = payload["progress_at"]
    spent = payload["effort_spent_min"]
    remaining_after = payload["effort_remaining_after_min"]
    status_after = payload["status_after"]

    if parse_rfc3339(progress_at, field="progress_at") < parse_rfc3339(
        prior["as_of"], field="as_of"
    ):
        _fail(
            f"TASK_PROGRESS progress_at before schedule.as_of "
            f"(progress_at={progress_at}, as_of={prior['as_of']})"
        )

    tasks = deepcopy(prior["obligation_tasks"])
    match_idx = next((i for i, t in enumerate(tasks) if t["task_id"] == task_id), None)
    if match_idx is None:
        _fail(f"TASK_PROGRESS unknown task_id: {task_id}")

    task = tasks[match_idx]
    prior_status = task["status"]
    if prior_status not in {"OPEN", "IN_PROGRESS"}:
        _fail(f"TASK_PROGRESS requires OPEN|IN_PROGRESS, got {prior_status!r}")

    prior_remaining = task["effort_remaining_min"]
    if spent > prior_remaining:
        _fail(
            f"effort_spent_min {spent} exceeds prior effort_remaining_min {prior_remaining}"
        )
    expected_remaining = prior_remaining - spent
    if remaining_after != expected_remaining:
        _fail(
            f"effort_remaining_after_min arithmetic mismatch: "
            f"expected {expected_remaining}, got {remaining_after}"
        )
    if remaining_after == 0:
        if status_after != "DONE":
            _fail("remaining_after == 0 requires status_after DONE")
    else:
        if status_after != "IN_PROGRESS":
            _fail("remaining_after > 0 requires status_after IN_PROGRESS")

    # Preserve unrelated task fields; update only effort/status.
    task["effort_remaining_min"] = remaining_after
    task["status"] = status_after
    tasks[match_idx] = task

    return build_schedule_state(
        character_id=prior["character_id"],
        as_of=progress_at,
        commitments=deepcopy(prior["commitments"]),
        obligation_tasks=tasks,
    )


def insert_commitment(
    schedule_state: Mapping[str, Any],
    commitment: Mapping[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    """Insert a Commitment into ScheduleState. Idempotent for exact same fact."""
    prior = validate_schedule_state(schedule_state)
    as_of_canon = canonicalize_timestamp(_require_str(as_of, "as_of"), field="as_of")
    if parse_rfc3339(as_of_canon, field="as_of") < parse_rfc3339(
        prior["as_of"], field="as_of"
    ):
        _fail("insert_commitment as_of before schedule.as_of")

    validated = validate_commitment(commitment)
    if parse_rfc3339(validated["created_at"], field="created_at") > parse_rfc3339(
        as_of_canon, field="as_of"
    ):
        _fail("commitment.created_at after as_of")

    commitments = deepcopy(prior["commitments"])
    cid = validated["commitment_id"]
    existing = next((c for c in commitments if c["commitment_id"] == cid), None)
    if existing is not None:
        if canonical_json(existing) == canonical_json(validated):
            # Exact same normalized fact → idempotent reseal at as_of.
            return build_schedule_state(
                character_id=prior["character_id"],
                as_of=as_of_canon,
                commitments=commitments,
                obligation_tasks=deepcopy(prior["obligation_tasks"]),
            )
        _fail(f"commitment_id conflict with different content: {cid}")

    commitments.append(validated)
    return build_schedule_state(
        character_id=prior["character_id"],
        as_of=as_of_canon,
        commitments=commitments,
        obligation_tasks=deepcopy(prior["obligation_tasks"]),
    )


def complete_commitment(
    schedule_state: Mapping[str, Any],
    *,
    commitment_id: str,
    completed_at: str,
    source_event_id: str,
) -> dict[str, Any]:
    """PLANNED → COMPLETED only. Narrow helper; no CANCELLED/MISSED/DEFERRED."""
    prior = validate_schedule_state(schedule_state)
    cid = _require_str(commitment_id, "commitment_id")
    source = _require_str(source_event_id, "source_event_id")
    completed = canonicalize_timestamp(
        _require_str(completed_at, "completed_at"), field="completed_at"
    )
    if parse_rfc3339(completed, field="completed_at") < parse_rfc3339(
        prior["as_of"], field="as_of"
    ):
        _fail("completed_at before schedule.as_of")

    commitments = deepcopy(prior["commitments"])
    match_idx = next(
        (i for i, c in enumerate(commitments) if c["commitment_id"] == cid), None
    )
    if match_idx is None:
        _fail(f"complete_commitment unknown commitment_id: {cid}")

    row = commitments[match_idx]
    history_entry = {
        "changed_at": completed,
        "from": "PLANNED",
        "to": "COMPLETED",
        "reason": COMPLETION_REASON,
        "source_ref": source,
    }

    if row["status"] == "COMPLETED":
        # Exact already-applied same completion → unchanged (still resealed ok).
        if any(
            h.get("changed_at") == completed
            and h.get("from") == "PLANNED"
            and h.get("to") == "COMPLETED"
            and h.get("reason") == COMPLETION_REASON
            and h.get("source_ref") == source
            for h in row["status_history"]
        ):
            return build_schedule_state(
                character_id=prior["character_id"],
                as_of=completed,
                commitments=commitments,
                obligation_tasks=deepcopy(prior["obligation_tasks"]),
            )
        _fail(f"commitment {cid} already COMPLETED with conflicting history")

    if row["status"] != "PLANNED":
        _fail(
            f"complete_commitment requires PLANNED, got {row['status']!r} "
            f"(no CANCELLED/MISSED/DEFERRED helper)"
        )

    row["status"] = "COMPLETED"
    row["status_history"] = list(row["status_history"]) + [history_entry]
    commitments[match_idx] = row

    return build_schedule_state(
        character_id=prior["character_id"],
        as_of=completed,
        commitments=commitments,
        obligation_tasks=deepcopy(prior["obligation_tasks"]),
    )
