"""Slice 5B1 — pure finalized ActualEvent effect dispatcher for RuntimeBundle.

Routes effects across Foundation CurrentState, Slice 3 domain reducers, and
ScheduleState. Does not widen Foundation apply_effect, bump state_revision,
append history/timeline, or persist to disk/Git.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .effects import apply_effect, normalize_effect
from .errors import ErrorCode, LifeEngineError
from .events import normalize_actual_event_for_persistence
from .finance_reducer import normalize_finance_transaction_effect, reduce_finance_transaction
from .home_reducer import (
    normalize_home_transition_effect,
    reduce_consumables_transition,
    reduce_home_transition,
)
from .relation_reducer import normalize_relation_touch_effect, reduce_relation_touch
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .schedule_reducer import (
    complete_commitment,
    normalize_commitment_complete_effect,
    normalize_task_progress_effect,
    reduce_task_progress,
)
from .timeutil import parse_rfc3339
from .wardrobe_reducer import (
    normalize_wardrobe_transition_effect,
    reduce_wardrobe_transition,
)


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_interval(
    *,
    ts: str,
    field: str,
    actual_start: str,
    actual_end: str,
) -> None:
    instant = parse_rfc3339(ts, field=field)
    start = parse_rfc3339(actual_start, field="actual_start")
    end = parse_rfc3339(actual_end, field="actual_end")
    if instant < start or instant > end:
        _fail(
            f"{field} outside ActualEvent interval "
            f"({actual_start} .. {actual_end}): {ts}"
        )


def apply_finalized_event_effects(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    actual_event: Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
) -> RuntimeBundle:
    """Apply finalized ActualEvent.effects to an in-memory RuntimeBundle.

    Pure: inputs are not mutated. Any failure leaves caller objects unchanged.
    Effect order is authoritative ActualEvent.effects order.
    """
    # Snapshot caller inputs for non-mutation guarantee (work on copies only).
    prior = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    event = normalize_actual_event_for_persistence(dict(actual_event))

    current = deepcopy(dict(prior.current_state))
    schedule = deepcopy(dict(prior.schedule_state))
    relation = deepcopy(dict(prior.relation_state))
    home = deepcopy(dict(prior.home_state))
    consumables = deepcopy(dict(prior.consumables_state))
    wardrobe = deepcopy(dict(prior.wardrobe_state))
    finance = deepcopy(dict(prior.finance_state))

    processed = parse_rfc3339(current["processed_through"], field="processed_through")
    actual_end = parse_rfc3339(event["actual_end"], field="actual_end")
    if actual_end > processed:
        _fail("ActualEvent.actual_end after CurrentState.processed_through")

    active = current["context"].get("active_activity")
    if (
        isinstance(active, Mapping)
        and active.get("activity_instance_id") == event["activity_instance_id"]
    ):
        _fail(
            "cannot apply effects while same activity_instance_id is still active_activity"
        )

    event_id = event["event_id"]
    actual_start = event["actual_start"]
    actual_end_s = event["actual_end"]
    prior_revision = current["state_revision"]
    prior_history = deepcopy(current.get("history"))

    for raw_effect in event.get("effects") or []:
        effect = normalize_effect(raw_effect)
        effect_type = effect["effect_type"]

        if effect_type == "HUMAN_STATE_DELTA":
            current = apply_effect(current, effect)
            continue

        if effect_type == "LOCATION_SET":
            current = apply_effect(current, effect)
            continue

        if effect_type == "RELATION_TOUCH":
            typed = normalize_relation_touch_effect(effect)
            _require_interval(
                ts=typed["payload"]["contact_at"],
                field="contact_at",
                actual_start=actual_start,
                actual_end=actual_end_s,
            )
            relation = reduce_relation_touch(
                relation,
                typed,
                source_event_id=event_id,
                known_person_ids=reference_sets.known_person_ids,
            )
            current["domain_refs"]["relation_state_revision"] = relation["revision"]
            continue

        if effect_type == "HOME_TRANSITION":
            typed = normalize_home_transition_effect(effect)
            kind = typed["payload"]["transition_kind"]
            _require_interval(
                ts=typed["payload"]["transition_at"],
                field="transition_at",
                actual_start=actual_start,
                actual_end=actual_end_s,
            )
            if kind == "HOME_ENTITY_STATUS_SET":
                home = reduce_home_transition(
                    home,
                    typed,
                    source_event_id=event_id,
                    known_entity_ids=reference_sets.known_home_entity_ids,
                )
                current["domain_refs"]["home_revision"] = home["revision"]
            elif kind == "CONSUMABLE_STATUS_SET":
                consumables = reduce_consumables_transition(
                    consumables,
                    typed,
                    source_event_id=event_id,
                    approved_consumable_ids=reference_sets.approved_consumable_ids,
                )
                current["domain_refs"]["consumables_revision"] = consumables["revision"]
            else:
                _fail(f"HOME_TRANSITION unknown transition_kind: {kind!r}")
            continue

        if effect_type == "WARDROBE_TRANSITION":
            typed = normalize_wardrobe_transition_effect(effect)
            _require_interval(
                ts=typed["payload"]["transition_at"],
                field="transition_at",
                actual_start=actual_start,
                actual_end=actual_end_s,
            )
            wardrobe = reduce_wardrobe_transition(
                wardrobe,
                typed,
                source_event_id=event_id,
                approved_item_ids=reference_sets.approved_wardrobe_item_ids,
            )
            current["domain_refs"]["wardrobe_revision"] = wardrobe["revision"]
            continue

        if effect_type == "FINANCE_TRANSACTION":
            typed = normalize_finance_transaction_effect(effect)
            _require_interval(
                ts=typed["payload"]["transaction_at"],
                field="transaction_at",
                actual_start=actual_start,
                actual_end=actual_end_s,
            )
            finance = reduce_finance_transaction(
                finance,
                typed,
                source_event_id=event_id,
            )
            current["domain_refs"]["finance_revision"] = finance["revision"]
            continue

        if effect_type == "TASK_PROGRESS":
            typed = normalize_task_progress_effect(effect)
            _require_interval(
                ts=typed["payload"]["progress_at"],
                field="progress_at",
                actual_start=actual_start,
                actual_end=actual_end_s,
            )
            schedule = reduce_task_progress(
                schedule,
                typed,
                source_event_id=event_id,
            )
            current["domain_refs"]["schedule_revision"] = schedule["revision"]
            current["domain_refs"]["schedule_hash"] = schedule["state_hash"]
            continue

        if effect_type == "COMMITMENT_COMPLETE":
            typed = normalize_commitment_complete_effect(effect)
            _require_interval(
                ts=typed["payload"]["completed_at"],
                field="completed_at",
                actual_start=actual_start,
                actual_end=actual_end_s,
            )
            schedule = complete_commitment(
                schedule,
                commitment_id=typed["payload"]["commitment_id"],
                completed_at=typed["payload"]["completed_at"],
                source_event_id=event_id,
            )
            current["domain_refs"]["schedule_revision"] = schedule["revision"]
            current["domain_refs"]["schedule_hash"] = schedule["state_hash"]
            continue

        _fail(f"unsupported finalized effect_type: {effect_type!r}")

    # Operational write counter is owned by 5C — never bump here.
    if current["state_revision"] != prior_revision:
        _fail("state_revision must not change in 5B1 effect dispatch")
    if current.get("history") != prior_history:
        _fail("history must not change in 5B1 effect dispatch")

    return validate_runtime_bundle(
        RuntimeBundle(
            current_state=current,
            schedule_state=schedule,
            relation_state=relation,
            home_state=home,
            consumables_state=consumables,
            wardrobe_state=wardrobe,
            finance_state=finance,
        ),
        reference_sets=reference_sets,
    )
