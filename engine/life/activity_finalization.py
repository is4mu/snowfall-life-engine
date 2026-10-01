"""Life Engine v2 Slice 5B2C2B — decision-boundary finish / factual finalization.

Pure/in-memory transition from an integrated runtime ActiveActivity at
``CurrentState.processed_through`` to one finalized ActualEvent plus the
post-event RuntimeBundle.

Owns exact ACTIVITY_END finish, decision-switch finish (SELECT_CANDIDATE only),
stale selected-candidate discard, event-derived effects, mechanical settlements,
and stale active-event/wakeup queue cleanup.

Does **not** start the pre-finalization selected candidate, re-run the resolver,
build a fresh post-finalization decision (5B2C3), capture/Camera Roll, wire
``clock.py``, bump ``state_revision``, or write disk/Git/life branch.
Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from .activity_materialization import build_activity_end_event
from .canonical import canonical_json
from .checkpoint import validate_checkpoint
from .derived import (
    SLEEP_NEED_MIN_HI,
    SLEEP_NEED_MIN_LO,
    meal_target_hunger,
    settle_main_sleep_debt,
)
from .effects import normalize_effect
from .errors import ErrorCode, LifeEngineError
from .events import build_actual_event_from_activity, finalize_activity, validate_active_activity
from .invariants import assert_queue_canonically_sorted, queue_event_sort_key
from .policy import normalize_policy
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .runtime_decision import (
    RuntimeDecisionFacts,
    RuntimeDecisionFrame,
    bind_runtime_trigger_to_queued_wakeup,
    build_decision_facts_hash,
    build_finalization_context_hash,
    build_materialization_context_hash,
    derive_runtime_decision_key,
    parse_runtime_decision_facts,
)
from .runtime_effects import apply_finalized_event_effects
from .schema import reject_binary_floats, validate_instance
from .schedule_reducer import normalize_commitment_complete_effect, normalize_task_progress_effect
from .timeutil import parse_rfc3339, require_canonical_timestamp
from .wakeups import is_managed_v2_decision_wakeup

FinishPath = Literal["EXACT_ACTIVITY_END", "DECISION_SWITCH"]

_RUNTIME_ACTIVE_REQUIRED = frozenset(
    {
        "runtime_context",
        "causes",
        "decision_evidence",
        "dynamics_context",
        "runtime_finish_context",
    }
)

_IN_PERSON_CONTACT_MODES = frozenset({"LOCAL_OUTING", "POST_WORK"})
_REMOTE_CONTACT_MODES = frozenset({"LIGHTWEIGHT", "CALL", "LONG_CATCHUP_CALL"})


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


def _require_v2_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        _fail("behavior_policy must be a mapping")
    normalized = normalize_policy(dict(policy))
    if normalized.get("schema_version") != 2 or normalized.get("policy_mode") != "PRODUCTION":
        _fail(
            "5B2C2B finalization requires schema_version=2 policy_mode=PRODUCTION "
            f"(got schema_version={normalized.get('schema_version')!r} "
            f"policy_mode={normalized.get('policy_mode')!r})"
        )
    return normalized


def _require_runtime_active_activity(activity: Mapping[str, Any] | None) -> dict[str, Any]:
    if activity is None:
        _fail("runtime-started ActiveActivity required for C2B finalization")
    if not isinstance(activity, Mapping):
        _fail("active_activity must be a mapping")
    missing = _RUNTIME_ACTIVE_REQUIRED - set(activity.keys())
    if missing:
        _fail(
            "legacy/Foundation ActiveActivity missing runtime fields: "
            f"{sorted(missing)}"
        )
    for key in _RUNTIME_ACTIVE_REQUIRED:
        if activity.get(key) is None:
            _fail(f"ActiveActivity.{key} required for C2B finalization")
    effects = activity.get("effects_on_end")
    if effects is None:
        effects = []
    if not isinstance(effects, list):
        _fail("effects_on_end must be an array")
    if len(effects) != 0:
        _fail(
            "runtime-started ActiveActivity must enter C2B with effects_on_end == [] "
            "(C2B derives authoritative ActualEvent.effects)"
        )
    validated = validate_active_activity(dict(activity))
    assert validated is not None
    for key in _RUNTIME_ACTIVE_REQUIRED:
        if key not in validated or validated[key] is None:
            _fail(f"ActiveActivity.{key} must survive validation")
    if list(validated.get("effects_on_end") or []) != []:
        _fail("effects_on_end must remain empty after validation")
    return validated


def parse_runtime_finish_context(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate runtime_finish_context shape (schema + true-int / non-empty rules)."""
    if not isinstance(raw, Mapping):
        _fail("runtime_finish_context must be an object")
    reject_binary_floats(dict(raw), path="$.runtime_finish_context")
    allowed = frozenset(
        {
            "schema_version",
            "sleep_need_min",
            "travel_destination_location_id",
            "task_effort_remaining_at_start_min",
        }
    )
    unknown = set(raw.keys()) - allowed
    if unknown:
        _fail(f"runtime_finish_context unknown fields: {sorted(unknown)}")
    for key in allowed:
        if key not in raw:
            _fail(f"runtime_finish_context.{key} required")
    if raw.get("schema_version") != 1:
        _fail("runtime_finish_context.schema_version must be 1")

    sleep_need = raw["sleep_need_min"]
    if sleep_need is not None:
        sleep_need = _require_true_int(sleep_need, "sleep_need_min")
        if sleep_need < SLEEP_NEED_MIN_LO or sleep_need > SLEEP_NEED_MIN_HI:
            _fail(
                f"sleep_need_min out of approved synthetic range "
                f"[{SLEEP_NEED_MIN_LO}, {SLEEP_NEED_MIN_HI}]: {sleep_need}"
            )

    travel = raw["travel_destination_location_id"]
    if travel is not None:
        travel = _require_str(travel, "travel_destination_location_id")

    task_remaining = raw["task_effort_remaining_at_start_min"]
    if task_remaining is not None:
        task_remaining = _require_true_int(
            task_remaining, "task_effort_remaining_at_start_min"
        )
        if task_remaining < 0:
            _fail("task_effort_remaining_at_start_min must be >= 0")

    return {
        "schema_version": 1,
        "sleep_need_min": sleep_need,
        "travel_destination_location_id": travel,
        "task_effort_remaining_at_start_min": task_remaining,
    }


def validate_runtime_finish_context_for_action(
    finish_context: Mapping[str, Any],
    *,
    action_kind: str,
    source_kind: str,
) -> dict[str, Any]:
    """Strict action-specific gating for frozen finish inputs."""
    ctx = parse_runtime_finish_context(finish_context)
    sleep_need = ctx["sleep_need_min"]
    travel = ctx["travel_destination_location_id"]
    task_remaining = ctx["task_effort_remaining_at_start_min"]

    if action_kind == "SLEEP_MAIN":
        if sleep_need is None:
            _fail("SLEEP_MAIN requires runtime_finish_context.sleep_need_min")
        if travel is not None or task_remaining is not None:
            _fail("SLEEP_MAIN finish context must leave travel/task fields null")
    elif action_kind == "TRAVEL_TO_COMMITMENT":
        if travel is None:
            _fail(
                "TRAVEL_TO_COMMITMENT requires "
                "runtime_finish_context.travel_destination_location_id"
            )
        if sleep_need is not None or task_remaining is not None:
            _fail("TRAVEL finish context must leave sleep/task fields null")
    elif action_kind in {"STUDY", "ERRAND"} and source_kind == "TASK":
        if task_remaining is None:
            _fail(
                "STUDY/ERRAND TASK requires "
                "runtime_finish_context.task_effort_remaining_at_start_min"
            )
        if sleep_need is not None or travel is not None:
            _fail("TASK finish context must leave sleep/travel fields null")
    else:
        if sleep_need is not None or travel is not None or task_remaining is not None:
            _fail(
                f"action_kind={action_kind!r} source_kind={source_kind!r} "
                "requires all runtime_finish_context nullable fields null"
            )
    return ctx


def runtime_activity_reached_completion_boundary(
    active: Mapping[str, Any],
    *,
    actual_end: str,
) -> bool:
    """Minimum-completion predicate for discrete completion effects."""
    end = require_canonical_timestamp(actual_end, field="actual_end")
    planned = active.get("planned_end")
    if planned is not None:
        planned_c = require_canonical_timestamp(planned, field="planned_end")
        return end == planned_c

    window = active.get("expected_end_window")
    if isinstance(window, Mapping):
        earliest = window.get("earliest")
        if earliest is not None:
            earliest_c = require_canonical_timestamp(earliest, field="expected_end_window.earliest")
            return parse_rfc3339(end, field="actual_end") >= parse_rfc3339(
                earliest_c, field="expected_end_window.earliest"
            )
    return False


def _elapsed_floor_minutes(*, actual_start: str, actual_end: str) -> int:
    start = parse_rfc3339(actual_start, field="actual_start")
    end = parse_rfc3339(actual_end, field="actual_end")
    delta = end - start
    seconds = delta.total_seconds()
    if seconds != int(seconds):
        _fail("actual elapsed seconds must be whole seconds")
    elapsed = int(seconds)
    if elapsed < 0:
        _fail("actual_end before actual_start")
    return elapsed // 60


def _assert_finish_instant(
    active: Mapping[str, Any],
    *,
    actual_end: str,
    processed_through: str,
) -> str:
    end = require_canonical_timestamp(actual_end, field="actual_end")
    processed = require_canonical_timestamp(processed_through, field="processed_through")
    if end != processed:
        _fail(
            "actual_end/finalized_at must equal CurrentState.processed_through "
            f"(actual_end={end}, processed_through={processed})"
        )
    start = require_canonical_timestamp(active["actual_start"], field="actual_start")
    if parse_rfc3339(end, field="actual_end") < parse_rfc3339(start, field="actual_start"):
        _fail("actual_end before actual_start")

    planned = active.get("planned_end")
    if planned is not None:
        planned_c = require_canonical_timestamp(planned, field="planned_end")
        if parse_rfc3339(end, field="actual_end") > parse_rfc3339(
            planned_c, field="planned_end"
        ):
            _fail("actual_end after planned_end")

    window = active.get("expected_end_window")
    if isinstance(window, Mapping):
        latest = window.get("latest")
        if latest is not None:
            latest_c = require_canonical_timestamp(latest, field="expected_end_window.latest")
            if parse_rfc3339(end, field="actual_end") > parse_rfc3339(
                latest_c, field="expected_end_window.latest"
            ):
                _fail("actual_end after expected_end_window.latest")
    return end


def _human_state_delta_effect(payload: Mapping[str, int]) -> dict[str, Any]:
    effect = {
        "schema_version": 1,
        "effect_type": "HUMAN_STATE_DELTA",
        "payload": dict(payload),
    }
    return normalize_effect(effect)


def _location_set_effect(location_id: str) -> dict[str, Any]:
    effect = {
        "schema_version": 1,
        "effect_type": "LOCATION_SET",
        "payload": {"location_id": location_id},
    }
    return normalize_effect(effect)


def _relation_touch_effect(
    *,
    person_id: str,
    contact_at: str,
    in_person_at: str | None,
) -> dict[str, Any]:
    from .relation_reducer import normalize_relation_touch_effect

    effect = {
        "schema_version": 1,
        "effect_type": "RELATION_TOUCH",
        "payload": {
            "person_id": person_id,
            "contact_at": contact_at,
            "in_person_at": in_person_at,
        },
    }
    return normalize_relation_touch_effect(effect)


def _task_progress_effect(
    *,
    task_id: str,
    progress_at: str,
    effort_spent_min: int,
    effort_remaining_after_min: int,
    status_after: str,
) -> dict[str, Any]:
    effect = {
        "schema_version": 1,
        "effect_type": "TASK_PROGRESS",
        "payload": {
            "task_id": task_id,
            "progress_at": progress_at,
            "effort_spent_min": effort_spent_min,
            "effort_remaining_after_min": effort_remaining_after_min,
            "status_after": status_after,
        },
    }
    return normalize_task_progress_effect(effect)


def _commitment_complete_effect(
    *,
    commitment_id: str,
    completed_at: str,
) -> dict[str, Any]:
    effect = {
        "schema_version": 1,
        "effect_type": "COMMITMENT_COMPLETE",
        "payload": {
            "commitment_id": commitment_id,
            "completed_at": completed_at,
        },
    }
    return normalize_commitment_complete_effect(effect)


def derive_runtime_finalization_effects(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    active_activity: Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
    actual_end: str,
    reference_sets: RuntimeReferenceSets,
) -> tuple[dict[str, Any], ...]:
    """Derive authoritative ActualEvent.effects in canonical order."""
    validated = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    active = _require_runtime_active_activity(active_activity)
    policy = _require_v2_policy(behavior_policy)
    end = _assert_finish_instant(
        active,
        actual_end=actual_end,
        processed_through=validated.current_state["processed_through"],
    )

    runtime_ctx = active["runtime_context"]
    action_kind = _require_str(runtime_ctx.get("action_kind"), "runtime_context.action_kind")
    source_kind = _require_str(runtime_ctx.get("source_kind"), "runtime_context.source_kind")
    source_ref = _require_str(runtime_ctx.get("source_ref"), "runtime_context.source_ref")
    finish_ctx = validate_runtime_finish_context_for_action(
        active["runtime_finish_context"],
        action_kind=action_kind,
        source_kind=source_kind,
    )
    completed = runtime_activity_reached_completion_boundary(active, actual_end=end)
    human = validated.current_state["human_state"]
    schedule = validated.schedule_state

    human_effects: list[dict[str, Any]] = []
    location_effects: list[dict[str, Any]] = []
    relation_effects: list[dict[str, Any]] = []
    task_effects: list[dict[str, Any]] = []
    commitment_effects: list[dict[str, Any]] = []

    # A. MAIN SLEEP
    if active["activity_type"] == "SLEEP":
        details = active.get("details")
        if not isinstance(details, Mapping) or details.get("sleep_kind") != "MAIN":
            _fail("SLEEP activity requires details.sleep_kind == MAIN for C2B")
        sleep_need = finish_ctx["sleep_need_min"]
        assert sleep_need is not None
        duration_min = _elapsed_floor_minutes(
            actual_start=active["actual_start"], actual_end=end
        )
        settled = settle_main_sleep_debt(
            current_debt_min=human["sleep_debt_min"],
            actual_main_sleep_duration_min=duration_min,
            sleep_need_min=sleep_need,
            policy=policy,
        )
        delta = settled - human["sleep_debt_min"]
        human_effects.append(_human_state_delta_effect({"sleep_debt_min": delta}))

    # B. MEAL
    if active["activity_type"] == "MEAL" and completed:
        details = active.get("details")
        if not isinstance(details, Mapping):
            _fail("MEAL requires details")
        extent = _require_str(details.get("meal_extent"), "details.meal_extent")
        target = meal_target_hunger(extent, policy)
        delta = target - human["hunger"]
        human_effects.append(_human_state_delta_effect({"hunger": delta}))

    # C. TRAVEL
    if action_kind == "TRAVEL_TO_COMMITMENT" and completed:
        dest = finish_ctx["travel_destination_location_id"]
        assert dest is not None
        location_effects.append(_location_set_effect(dest))

    # D. SOCIAL_CONTACT
    if active["activity_type"] == "SOCIAL_CONTACT":
        if parse_rfc3339(end, field="actual_end") > parse_rfc3339(
            active["actual_start"], field="actual_start"
        ):
            details = active.get("details")
            if not isinstance(details, Mapping):
                _fail("SOCIAL_CONTACT requires details")
            contact_mode = _require_str(details.get("contact_mode"), "details.contact_mode")
            if contact_mode in _IN_PERSON_CONTACT_MODES:
                in_person_at: str | None = end
            elif contact_mode in _REMOTE_CONTACT_MODES:
                in_person_at = None
            else:
                _fail(f"unsupported social contact_mode: {contact_mode!r}")

            companions = list(active.get("companions") or [])
            # Preserve companions on the activity/ActualEvent exactly; exclude
            # the application character from RelationState RELATION_TOUCH derivation.
            character_id = _require_str(
                validated.current_state.get("character_id"), "character_id"
            )
            person_ids = sorted(
                {
                    _require_str(c.get("entity_id"), "companions.entity_id")
                    for c in companions
                    if isinstance(c, Mapping)
                }
            )
            external_ids = [pid for pid in person_ids if pid != character_id]
            known = set(reference_sets.known_person_ids)
            for person_id in external_ids:
                if person_id not in known:
                    _fail(
                        f"SOCIAL_CONTACT companion must be known person: {person_id!r}"
                    )
                relation_effects.append(
                    _relation_touch_effect(
                        person_id=person_id,
                        contact_at=end,
                        in_person_at=in_person_at,
                    )
                )

    # E. TASK progress
    if source_kind == "TASK" and action_kind in {"STUDY", "ERRAND"}:
        start_remaining = finish_ctx["task_effort_remaining_at_start_min"]
        assert start_remaining is not None
        tasks = schedule.get("obligation_tasks") or []
        match = next((t for t in tasks if t.get("task_id") == source_ref), None)
        if match is None:
            _fail(f"TASK progress requires current Schedule task: {source_ref!r}")
        current_remaining = _require_true_int(
            match.get("effort_remaining_min"), "effort_remaining_min"
        )
        if current_remaining != start_remaining:
            _fail(
                "current task effort_remaining_min must equal frozen start remaining "
                f"(current={current_remaining}, frozen={start_remaining})"
            )
        status = _require_str(match.get("status"), "task.status")
        if status not in {"OPEN", "IN_PROGRESS"}:
            _fail(f"TASK progress requires OPEN|IN_PROGRESS, got {status!r}")

        spent = min(
            _elapsed_floor_minutes(actual_start=active["actual_start"], actual_end=end),
            start_remaining,
        )
        if spent >= 1:
            remaining_after = start_remaining - spent
            status_after = "DONE" if remaining_after == 0 else "IN_PROGRESS"
            task_effects.append(
                _task_progress_effect(
                    task_id=source_ref,
                    progress_at=end,
                    effort_spent_min=spent,
                    effort_remaining_after_min=remaining_after,
                    status_after=status_after,
                )
            )

    # F. COMMITMENT_COMPLETE
    if (
        source_kind == "COMMITMENT"
        and action_kind != "TRAVEL_TO_COMMITMENT"
        and completed
    ):
        commitment_id = active.get("commitment_id")
        if commitment_id is None:
            _fail("COMMITMENT_COMPLETE requires active.commitment_id")
        commitment_id = _require_str(commitment_id, "commitment_id")
        if commitment_id != source_ref:
            _fail(
                "active.commitment_id must equal runtime_context.source_ref "
                f"({commitment_id!r} != {source_ref!r})"
            )
        commitments = schedule.get("commitments") or []
        row = next(
            (c for c in commitments if c.get("commitment_id") == commitment_id),
            None,
        )
        if row is None:
            _fail(f"COMMITMENT_COMPLETE unknown commitment_id: {commitment_id}")
        if row.get("status") != "PLANNED":
            _fail(
                f"COMMITMENT_COMPLETE requires PLANNED commitment, got {row.get('status')!r}"
            )
        commitment_effects.append(
            _commitment_complete_effect(
                commitment_id=commitment_id,
                completed_at=end,
            )
        )

    return tuple(
        human_effects
        + location_effects
        + relation_effects
        + task_effects
        + commitment_effects
    )


def apply_runtime_event_mechanical_settlements(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    actual_event: Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
) -> RuntimeBundle:
    """Replay-deterministic mechanical metadata updates from ActualEvent alone."""
    prior = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    event = dict(actual_event)
    if "schema_version" not in event:
        event["schema_version"] = 1
    validate_instance(event, "actual_event")

    current = deepcopy(dict(prior.current_state))
    event_type = event.get("event_type")
    details = event.get("details")
    effects = list(event.get("effects") or [])
    actual_end = require_canonical_timestamp(event["actual_end"], field="actual_end")

    if event_type == "MEAL":
        has_hunger = any(
            isinstance(e, Mapping)
            and e.get("effect_type") == "HUMAN_STATE_DELTA"
            and isinstance(e.get("payload"), Mapping)
            and "hunger" in e["payload"]
            for e in effects
        )
        if has_hunger:
            remainders = dict(current["integration"]["rate_remainders"])
            remainders["hunger"] = 0
            current["integration"] = {
                **current["integration"],
                "rate_remainders": remainders,
            }

    if (
        event_type == "SLEEP"
        and isinstance(details, Mapping)
        and details.get("sleep_kind") == "MAIN"
    ):
        has_sleep_debt = any(
            isinstance(e, Mapping)
            and e.get("effect_type") == "HUMAN_STATE_DELTA"
            and isinstance(e.get("payload"), Mapping)
            and "sleep_debt_min" in e["payload"]
            for e in effects
        )
        if has_sleep_debt:
            sleep_ctx = dict(current["sleep_context"])
            circadian = sleep_ctx.get("circadian_profile_ref")
            sleep_ctx["last_wake_at"] = actual_end
            sleep_ctx["last_main_sleep_end"] = actual_end
            sleep_ctx["circadian_profile_ref"] = circadian
            current["sleep_context"] = sleep_ctx

    current = validate_checkpoint(current)
    return validate_runtime_bundle(
        RuntimeBundle(
            current_state=current,
            schedule_state=deepcopy(dict(prior.schedule_state)),
            relation_state=deepcopy(dict(prior.relation_state)),
            home_state=deepcopy(dict(prior.home_state)),
            consumables_state=deepcopy(dict(prior.consumables_state)),
            wardrobe_state=deepcopy(dict(prior.wardrobe_state)),
            finance_state=deepcopy(dict(prior.finance_state)),
        ),
        reference_sets=reference_sets,
    )


def _cleanup_queue_after_finalization(
    events: Sequence[Mapping[str, Any]],
    *,
    finalized_activity_instance_id: str,
) -> list[dict[str, Any]]:
    """Remove ACTIVITY_END for finalized activity and all managed v2 wakeups."""
    kept: list[dict[str, Any]] = []
    for raw in events:
        event = dict(raw)
        if event.get("event_kind") == "ACTIVITY_END":
            payload = event.get("payload") or {}
            if (
                isinstance(payload, Mapping)
                and payload.get("activity_instance_id") == finalized_activity_instance_id
            ):
                continue
        if is_managed_v2_decision_wakeup(event):
            continue
        kept.append(event)
    kept.sort(key=queue_event_sort_key)
    assert_queue_canonically_sorted(kept)
    return kept


@dataclass(frozen=True)
class RuntimeActivityFinishResult:
    bundle: RuntimeBundle
    actual_event: Mapping[str, Any]
    finish_path: FinishPath
    reached_completion_boundary: bool
    discarded_selected_candidate_id: str | None
    post_finalize_decision_required: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle": {
                "current_state": dict(self.bundle.current_state),
                "schedule_state": dict(self.bundle.schedule_state),
                "relation_state": dict(self.bundle.relation_state),
                "home_state": dict(self.bundle.home_state),
                "consumables_state": dict(self.bundle.consumables_state),
                "wardrobe_state": dict(self.bundle.wardrobe_state),
                "finance_state": dict(self.bundle.finance_state),
            },
            "actual_event": dict(self.actual_event),
            "finish_path": self.finish_path,
            "reached_completion_boundary": self.reached_completion_boundary,
            "discarded_selected_candidate_id": self.discarded_selected_candidate_id,
            "post_finalize_decision_required": self.post_finalize_decision_required,
        }


def _finalize_runtime_activity(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    finish_path: FinishPath,
    discarded_selected_candidate_id: str | None,
) -> RuntimeActivityFinishResult:
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state
    processed = current_in["processed_through"]
    revision_before = current_in["state_revision"]
    history_before = deepcopy(current_in.get("history"))
    policy = _require_v2_policy(behavior_policy)
    if policy["behavior_policy_version"] != current_in["behavior_policy_version"]:
        _fail(
            "behavior_policy_version mismatch: "
            f"policy={policy['behavior_policy_version']!r} "
            f"current={current_in['behavior_policy_version']!r}"
        )

    active = _require_runtime_active_activity(current_in["context"].get("active_activity"))
    actual_end = _assert_finish_instant(
        active, actual_end=processed, processed_through=processed
    )
    reached = runtime_activity_reached_completion_boundary(active, actual_end=actual_end)

    derived = derive_runtime_finalization_effects(
        bundle=validated_in,
        active_activity=active,
        behavior_policy=policy,
        actual_end=actual_end,
        reference_sets=reference_sets,
    )

    # Copy active → set effects → reuse Foundation finalize (clears active once).
    state_work = deepcopy(dict(current_in))
    active_copy = deepcopy(active)
    active_copy["effects_on_end"] = list(derived)
    # Ensure schema accepts effects_on_end before finalize.
    validated_active = validate_active_activity(active_copy)
    assert validated_active is not None
    state_work["context"]["active_activity"] = validated_active
    state_out, actual_event = finalize_activity(
        state_work,
        actual_end=actual_end,
        finalized_at=actual_end,
    )
    if state_out["context"].get("active_activity") is not None:
        _fail("active_activity must be cleared exactly once")
    if "runtime_finish_context" in actual_event:
        _fail("runtime_finish_context must not be copied into ActualEvent")

    # Queue cleanup on post-clear state.
    cleaned_events = _cleanup_queue_after_finalization(
        state_out["pending_queue"].get("events") or [],
        finalized_activity_instance_id=active["activity_instance_id"],
    )
    state_out = deepcopy(state_out)
    state_out["pending_queue"] = {"cursor": 0, "events": cleaned_events}
    state_out = validate_checkpoint(state_out)

    if state_out["processed_through"] != processed:
        _fail("processed_through must remain unchanged")
    if state_out["state_revision"] != revision_before:
        _fail("state_revision must remain unchanged")
    if state_out.get("history") != history_before:
        _fail("history must remain unchanged")

    mid_bundle = RuntimeBundle(
        current_state=state_out,
        schedule_state=deepcopy(dict(validated_in.schedule_state)),
        relation_state=deepcopy(dict(validated_in.relation_state)),
        home_state=deepcopy(dict(validated_in.home_state)),
        consumables_state=deepcopy(dict(validated_in.consumables_state)),
        wardrobe_state=deepcopy(dict(validated_in.wardrobe_state)),
        finance_state=deepcopy(dict(validated_in.finance_state)),
    )
    mid_bundle = validate_runtime_bundle(mid_bundle, reference_sets=reference_sets)

    after_effects = apply_finalized_event_effects(
        bundle=mid_bundle,
        actual_event=actual_event,
        reference_sets=reference_sets,
    )
    after_mech = apply_runtime_event_mechanical_settlements(
        bundle=after_effects,
        actual_event=actual_event,
        reference_sets=reference_sets,
    )

    if after_mech.current_state["processed_through"] != processed:
        _fail("processed_through must remain unchanged after settlements")
    if after_mech.current_state["state_revision"] != revision_before:
        _fail("state_revision must remain unchanged after settlements")
    if after_mech.current_state.get("history") != history_before:
        _fail("history must remain unchanged after settlements")
    if after_mech.current_state["context"].get("active_activity") is not None:
        _fail("active_activity must remain null after C2B")

    # Guard: no replacement wakeup projected.
    for event in after_mech.current_state["pending_queue"]["events"]:
        if is_managed_v2_decision_wakeup(event):
            _fail("C2B must not project replacement managed v2 DECISION_WAKEUP")

    return RuntimeActivityFinishResult(
        bundle=after_mech,
        actual_event=dict(actual_event),
        finish_path=finish_path,
        reached_completion_boundary=reached,
        discarded_selected_candidate_id=discarded_selected_candidate_id,
        post_finalize_decision_required=True,
    )


def finalize_runtime_activity_from_exact_end(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    activity_end_event: Mapping[str, Any],
) -> RuntimeActivityFinishResult:
    """Exact ACTIVITY_END finish path. No decision frame / resolver call."""
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state
    active = _require_runtime_active_activity(current_in["context"].get("active_activity"))

    planned = active.get("planned_end")
    if planned is None:
        _fail("exact-end path requires active.planned_end != null")
    planned_c = require_canonical_timestamp(planned, field="planned_end")
    processed = require_canonical_timestamp(
        current_in["processed_through"], field="processed_through"
    )
    if planned_c != processed:
        _fail("exact-end requires planned_end == processed_through")

    # Strict supplied ACTIVITY_END vs C1 builder.
    supplied = dict(activity_end_event)
    validate_instance(supplied, "queued_internal_event")
    if supplied.get("event_kind") != "ACTIVITY_END":
        _fail("exact-end requires event_kind == ACTIVITY_END")
    payload = supplied.get("payload")
    if not isinstance(payload, Mapping):
        _fail("ACTIVITY_END payload must be an object")
    instance = _require_str(payload.get("activity_instance_id"), "payload.activity_instance_id")
    if instance != active["activity_instance_id"]:
        _fail("ACTIVITY_END activity_instance_id mismatch vs active")

    expected = build_activity_end_event(
        activity_instance_id=active["activity_instance_id"],
        planned_end=planned_c,
    )
    assert expected is not None
    due = require_canonical_timestamp(supplied.get("due_at"), field="due_at")
    if due != planned_c or due != processed:
        _fail("ACTIVITY_END due_at must equal planned_end == processed_through")

    # Authoritative queue bind: exactly one row; queued == supplied == C1 builder.
    queue_events = list(current_in["pending_queue"].get("events") or [])
    matches = [e for e in queue_events if e.get("event_id") == expected["event_id"]]
    if len(matches) != 1:
        _fail(
            "exact-end requires exactly one queued ACTIVITY_END with expected "
            f"event_id={expected['event_id']!r} count={len(matches)}"
        )
    queued_row = dict(matches[0])
    validate_instance(queued_row, "queued_internal_event")
    if canonical_json(queued_row) != canonical_json(expected):
        _fail(
            "queued ACTIVITY_END must equal C1 deterministic builder output "
            "(due_at/priority/payload/event_id)"
        )
    if canonical_json(supplied) != canonical_json(expected):
        _fail(
            "supplied activity_end_event must equal C1 deterministic builder output "
            "and the authoritative queued row"
        )

    return _finalize_runtime_activity(
        bundle=validated_in,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        finish_path="EXACT_ACTIVITY_END",
        discarded_selected_candidate_id=None,
    )


def finalize_runtime_activity_from_decision_switch(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    frame: RuntimeDecisionFrame,
    decision_facts: RuntimeDecisionFacts | Mapping[str, Any],
) -> RuntimeActivityFinishResult:
    """Decision-boundary finish. SELECT_CANDIDATE only; never starts that candidate."""
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state
    active = _require_runtime_active_activity(current_in["context"].get("active_activity"))
    processed = require_canonical_timestamp(
        current_in["processed_through"], field="processed_through"
    )

    if frame.resolution.result_kind == "CONTINUE_CURRENT":
        _fail("CONTINUE_CURRENT must not call C2B decision-switch finalization")
    if frame.resolution.result_kind != "SELECT_CANDIDATE":
        _fail(
            "decision-switch requires resolution.result_kind == SELECT_CANDIDATE "
            f"(got {frame.resolution.result_kind!r})"
        )

    # Authoritative queued managed v2 DECISION_WAKEUP bind (does not pop).
    bound_trigger = bind_runtime_trigger_to_queued_wakeup(current_in, frame.trigger)
    if bound_trigger.occurred_at != processed:
        _fail("decision-switch trigger.occurred_at must equal processed_through")
    expected_decision_key = derive_runtime_decision_key(
        character_id=current_in["character_id"],
        trigger_id=bound_trigger.trigger_id,
    )
    if frame.runtime_decision_key != expected_decision_key:
        _fail(
            "frame.runtime_decision_key must equal "
            "derive_runtime_decision_key(character_id, trigger_id)"
        )

    # Same-instant ACTIVITY_END owns priority 10 before DECISION_WAKEUP 50.
    for event in current_in["pending_queue"].get("events") or []:
        if event.get("event_kind") != "ACTIVITY_END":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, Mapping):
            continue
        if payload.get("activity_instance_id") != active["activity_instance_id"]:
            continue
        due = require_canonical_timestamp(event.get("due_at"), field="due_at")
        if due == processed:
            _fail(
                "decision-switch rejected: ACTIVITY_END due at the same "
                "processed_through owns the instant before DECISION_WAKEUP"
            )

    policy = _require_v2_policy(behavior_policy)
    if policy["behavior_policy_version"] != current_in["behavior_policy_version"]:
        _fail(
            "behavior_policy_version mismatch: "
            f"policy={policy['behavior_policy_version']!r} "
            f"current={current_in['behavior_policy_version']!r}"
        )

    parsed_facts = parse_runtime_decision_facts(decision_facts, now=processed)
    facts_hash = build_decision_facts_hash(parsed_facts)
    if facts_hash != frame.decision_facts_hash:
        _fail(
            "decision_facts_hash mismatch vs RuntimeDecisionFrame "
            "(post-decision facts drift is rejected)"
        )

    # Keep C1 materialization_context_hash semantics intact.
    mat_hash = build_materialization_context_hash(
        current_state=current_in,
        schedule_state=validated_in.schedule_state,
        behavior_policy=behavior_policy,
        decision_facts_hash=facts_hash,
    )
    if mat_hash != frame.materialization_context_hash:
        _fail(
            "materialization_context_hash mismatch vs RuntimeDecisionFrame "
            "(post-decision CurrentState/ScheduleState/Behavior Policy drift "
            "is rejected)"
        )

    # C2B-specific full finalization bind (human/sleep/queue/active/domains).
    fin_hash = build_finalization_context_hash(
        bundle=validated_in,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        decision_facts_hash=facts_hash,
    )
    if fin_hash != frame.finalization_context_hash:
        _fail(
            "finalization_context_hash mismatch vs RuntimeDecisionFrame "
            "(post-decision finish-relevant state drift is rejected)"
        )

    if frame.selected_candidate is None:
        _fail("SELECT_CANDIDATE requires frame.selected_candidate")
    selected = frame.selected_candidate
    if selected.candidate_id != frame.resolution.selected_candidate_id:
        _fail("selected_candidate.candidate_id mismatch vs resolution")
    if selected.candidate_key != frame.resolution.selected_candidate_key:
        _fail("selected_candidate.candidate_key mismatch vs resolution")
    if selected.action_kind != frame.resolution.selected_action_kind:
        _fail("selected_candidate.action_kind mismatch vs resolution")
    if selected.priority_tier != frame.resolution.selected_priority_tier:
        _fail("selected_candidate.priority_tier mismatch vs resolution")

    # Discard selected candidate for start authority; never start it.
    discarded = selected.candidate_id

    return _finalize_runtime_activity(
        bundle=validated_in,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        finish_path="DECISION_SWITCH",
        discarded_selected_candidate_id=discarded,
    )
