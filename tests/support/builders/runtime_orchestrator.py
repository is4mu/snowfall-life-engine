"""Shared synthetic target-time runtime orchestrator builders."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Sequence

from engine.life.activity_finalization import finalize_runtime_activity_from_exact_end
from engine.life.activity_materialization import build_activity_end_event
from engine.life.activity_runtime import derive_activity_instance_id
from engine.life.checkpoint import validate_checkpoint
from engine.life.fixed_point import empty_rate_remainders
from engine.life.invariants import queue_event_sort_key
from engine.life.runtime_bundle import RuntimeBundle, load_runtime_bundle
from engine.life.runtime_decision import runtime_trigger_from_v2_wakeup
from engine.life.runtime_orchestrator import (
    RuntimeDecisionStepInput,
    RuntimeTargetInputs,
    RuntimeWakeupStepInput,
    build_post_finalize_decision_wakeup,
)
from engine.life.schedule_state import build_schedule_state
from engine.life.social_runtime import (
    build_runtime_social_decision,
    build_social_response_state,
)
from engine.life.wakeups import FutureCandidate, build_v2_decision_wakeup_event
from tests.support.builders.runtime_bundle import CHAR, build_synthetic_bundle_root, reference_sets as _refs
from tests.support.builders.schedule import commitment as _commitment, task as _task
from tests.support.builders.runtime_decision import AS_OF, HOME, _facts, _household
from tests.support.builders.social_runtime import (
    _decision_facts as _social_decision_facts,
    _relation_with_friends,
    _social_facts as _raw_social_facts,
    _social_refs,
)
from tests.support.builders.wakeups import HORIZON, SLEEP_PROF, _wakeup_facts
from tests.support.builders.finalization import (
    POLICY,
    POLICY_VERSION,
    _add_min,
    _load_bundle,
    _planned_end_active,
    _runtime_active,
    _v2_wakeup,
)
from tests.support.builders.capture_runtime import (
    CHAR_SOURCE_SHA,
    _force_skip_policy,
    _force_take_policy,
    _moment,
)

__all__ = [
    "AS_OF",
    "CHAR",
    "CHAR_SOURCE_SHA",
    "HOME",
    "HORIZON",
    "POLICY",
    "POLICY_VERSION",
    "SLEEP_PROF",
    "_add_min",
    "_advance_refs",
    "_bundle_snapshot",
    "_continue_setup",
    "_decision_facts_select",
    "_decision_step",
    "_exact_end_ready_setup",
    "_facts",
    "_force_skip_policy",
    "_force_take_policy",
    "_future_wakeup",
    "_household_mat_facts",
    "_load_bundle",
    "_load_c3_bundle",
    "_moment",
    "_no_active_bundle",
    "_no_active_start_setup",
    "_planned_end_active",
    "_predict_select",
    "_refs",
    "_runtime_active",
    "_social_facts",
    "_social_state",
    "_stable_active_bundle",
    "_target_inputs",
    "_v2_wakeup",
    "_wakeup_facts",
    "_wakeup_step",
]


def _advance_refs():
    """Reference sets that include social-graph persons used by default social facts."""
    return _social_refs()


def _social_state(*, as_of: str = AS_OF, responses: Sequence[Any] = ()):
    return build_social_response_state(
        character_id=CHAR,
        as_of=as_of,
        responses=list(responses),
    )


def _social_facts(**overrides: Any) -> dict[str, Any]:
    """Non-social-selecting facts so HOUSEHOLD/STUDY paths stay predictable."""
    base = _raw_social_facts(
        social_contact_physical_feasible=False,
        post_work_location_feasible=False,
    )
    base.update(overrides)
    return base


def _decision_facts_select(**overrides: Any) -> dict[str, Any]:
    """Facts that yield SELECT_CANDIDATE HOUSEHOLD when no active / after finalize."""
    base = _social_decision_facts(
        household_tasks=[_household()],
        routine_physical_feasible_by_action={
            "HOUSEHOLD": True,
            "KICKBOXING": False,
        },
        continuation_feasible=None,
    )
    base.update(overrides)
    return base


def _decision_facts_continue(**overrides: Any) -> dict[str, Any]:
    base = _decision_facts_select(continuation_feasible=True)
    base.update(overrides)
    return base


def _decision_facts_switch(**overrides: Any) -> dict[str, Any]:
    """Active SELECT path: continuation infeasible so CONTINUE is not eligible."""
    base = _decision_facts_select(continuation_feasible=False)
    base.update(overrides)
    return base


def _decision_step(
    trigger_id: str,
    decision_facts: Mapping[str, Any] | None = None,
    social_facts: Mapping[str, Any] | None = None,
    materialization_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "trigger_id": trigger_id,
        "decision_facts": (
            dict(decision_facts)
            if decision_facts is not None
            else _decision_facts_select()
        ),
        "social_facts": (
            dict(social_facts) if social_facts is not None else _social_facts()
        ),
        "materialization_facts": materialization_facts,
    }


def _wakeup_step(
    activity_instance_id: str,
    as_of: str = AS_OF,
    projection_facts: Mapping[str, Any] | None = None,
    *,
    projection_horizon_end: str | None = None,
    **fact_overrides: Any,
) -> dict[str, Any]:
    if projection_facts is not None:
        facts = dict(projection_facts)
    else:
        horizon = projection_horizon_end or HORIZON
        facts = _wakeup_facts(projection_horizon_end=horizon, **fact_overrides)
    # Ensure horizon covers typical targets used in C3 tests.
    if projection_horizon_end is not None:
        facts["projection_horizon_end"] = projection_horizon_end
    return {
        "activity_instance_id": activity_instance_id,
        "as_of": as_of,
        "projection_facts": facts,
    }


def _target_inputs(
    target_time: str = AS_OF,
    event_budget: int = 100,
    moments: Sequence[Mapping[str, Any]] | None = None,
    decision_steps: Sequence[Mapping[str, Any]] | None = None,
    wakeup_steps: Sequence[Mapping[str, Any]] | None = None,
    character_source_sha: str = CHAR_SOURCE_SHA,
    capture_source_moments: Sequence[Mapping[str, Any]] | None = None,
) -> RuntimeTargetInputs:
    caps = moments if moments is not None else capture_source_moments
    if caps is None:
        caps = ()
    steps = decision_steps if decision_steps is not None else ()
    wakes = wakeup_steps if wakeup_steps is not None else ()
    return RuntimeTargetInputs(
        target_time=target_time,
        event_budget=event_budget,
        character_source_sha=character_source_sha,
        capture_source_moments=tuple(dict(m) for m in caps),
        decision_steps=tuple(
            s
            if isinstance(s, RuntimeDecisionStepInput)
            else RuntimeDecisionStepInput(
                trigger_id=s["trigger_id"],
                decision_facts=s["decision_facts"],
                social_facts=s["social_facts"],
                materialization_facts=s.get("materialization_facts"),
            )
            for s in steps
        ),
        wakeup_steps=tuple(
            s
            if isinstance(s, RuntimeWakeupStepInput)
            else RuntimeWakeupStepInput(
                activity_instance_id=s["activity_instance_id"],
                as_of=s["as_of"],
                projection_facts=s["projection_facts"],
            )
            for s in wakes
        ),
    )


def _future_wakeup(
    *,
    due_at: str | None = None,
    decision_key: str = "idle:future",
    trigger_kind: str = "IDLE",
) -> dict[str, Any]:
    return build_v2_decision_wakeup_event(
        character_id=CHAR,
        candidate=FutureCandidate(
            due_at=due_at or _add_min(AS_OF, 60),
            trigger_kind=trigger_kind,
            decision_key=decision_key,
            reason_code="IDLE_REASSESSMENT",
            metric=None,
            target_band=None,
            direction=None,
            source_kind=None,
            source_ref=None,
        ),
    )


def _bundle_snapshot(bundle) -> dict[str, Any]:
    return {
        "current_state": copy.deepcopy(dict(bundle.current_state)),
        "schedule_state": copy.deepcopy(dict(bundle.schedule_state)),
        "relation_state": copy.deepcopy(dict(bundle.relation_state)),
        "home_state": copy.deepcopy(dict(bundle.home_state)),
        "consumables_state": copy.deepcopy(dict(bundle.consumables_state)),
        "wardrobe_state": copy.deepcopy(dict(bundle.wardrobe_state)),
        "finance_state": copy.deepcopy(dict(bundle.finance_state)),
    }


def _load_c3_bundle(
    tmp: str,
    *,
    active: dict[str, Any] | None | object = ...,
    queue_events: list | None = None,
    processed_through: str | None = None,
    commitments: list | None = None,
    tasks: list | None = None,
    world_seed: str = "public-orchestrator-seed",
    human_state: dict[str, int] | None = None,
    location_id: str = HOME,
    mutate_current=None,
    use_social_relation: bool = True,
):
    """Bundle loader that allows active=None and social-graph persons."""
    sentinel = ...
    if active is sentinel:
        act: dict[str, Any] | None = _runtime_active()
    else:
        act = active  # type: ignore[assignment]
    events = sorted(
        (dict(e) for e in (queue_events if queue_events is not None else [])),
        key=queue_event_sort_key,
    )
    processed = processed_through if processed_through is not None else AS_OF
    hs = human_state or {
        "sleep_debt_min": 0,
        "hunger": 200,
        "physical_fatigue": 100,
        "affect_valence": 0,
        "stress": 100,
        "social_battery": 800,
    }
    task_list = tasks if tasks is not None else [_task()]
    cmt_list = commitments if commitments is not None else []

    def _mut(c: dict) -> dict:
        out = {
            **c,
            "behavior_policy_version": POLICY_VERSION,
            "processed_through": processed,
            "world_seed": world_seed,
            "state_revision": 11,
            "human_state": hs,
            "integration": {
                "schema_version": 1,
                "rate_remainders": empty_rate_remainders(),
            },
            "pending_queue": {"cursor": 0, "events": events},
            "context": {
                **c["context"],
                "active_activity": act,
                "location_id": location_id,
            },
        }
        if mutate_current is not None:
            out = mutate_current(out)
        return out

    world_overrides = None
    if use_social_relation:
        world_overrides = {"relation": _relation_with_friends()}

    root = build_synthetic_bundle_root(
        Path(tmp),
        mutate_current=_mut,
        mutate_schedule=lambda s: build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            commitments=cmt_list,
            obligation_tasks=task_list,
        ),
        world_overrides=world_overrides,
    )
    return load_runtime_bundle(root, reference_sets=_advance_refs())


def _no_active_bundle(
    tmp: str,
    *,
    queue_events: list | None = None,
    processed_through: str | None = None,
    **kwargs,
):
    return _load_c3_bundle(
        tmp,
        active=None,
        queue_events=queue_events,
        processed_through=processed_through,
        **kwargs,
    )


def _stable_active_bundle(tmp: str, *, target: str = AS_OF, **kwargs):
    """Window active with future managed wakeup; no due-now work at target.

    While processed_through < earliest, C2A liveness requires managed due <= earliest.
    """
    earliest = _add_min(target, 45)
    latest = _add_min(target, 90)
    active = _runtime_active(
        expected_end_window={"earliest": earliest, "latest": latest}
    )
    future = _future_wakeup(due_at=earliest)
    return _load_c3_bundle(
        tmp,
        active=active,
        queue_events=[future],
        processed_through=target,
        **kwargs,
    )


def _household_mat_facts(candidate_id: str) -> dict[str, Any]:
    return {
        "selected_candidate_id": candidate_id,
        "base_activity_class": "LIGHT_ACTIVE",
        "social_exposure": "NONE",
        "meal_source": None,
        "meal_extent": None,
        "social_contact_mode": None,
        "sleep_profile": None,
    }


def _predict_select(
    bundle,
    wakeup: Mapping[str, Any],
    *,
    decision_facts: Mapping[str, Any] | None = None,
    social_facts: Mapping[str, Any] | None = None,
    social_state=None,
):
    """Run social-aware decision once to learn SELECT result / instance id."""
    trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, wakeup)
    state = social_state if social_state is not None else _social_state(
        as_of=bundle.current_state["processed_through"]
    )
    result = build_runtime_social_decision(
        bundle=bundle,
        reference_sets=_advance_refs(),
        behavior_policy=POLICY,
        trigger=trigger,
        facts=decision_facts if decision_facts is not None else _decision_facts_select(),
        social_response_state=state,
        social_facts=social_facts if social_facts is not None else _social_facts(),
    )
    cand = result.frame.selected_candidate
    assert cand is not None, result.frame.resolution.result_kind
    instance_id = derive_activity_instance_id(
        character_id=CHAR,
        decision_key=result.frame.runtime_decision_key,
        selected_candidate_id=cand.candidate_id,
    )
    return result, cand, instance_id


def _no_active_start_setup(tmp: str, *, due_at: str | None = None):
    """No-active + due-now IDLE wakeup ready to SELECT/start HOUSEHOLD."""
    at = due_at if due_at is not None else AS_OF
    wakeup = _v2_wakeup(due_at=at, decision_key="idle:c3-start")
    bundle = _no_active_bundle(tmp, queue_events=[wakeup], processed_through=at)
    social = _social_state(as_of=at)
    df = _decision_facts_select()
    sf = _social_facts()
    _result, cand, instance_id = _predict_select(
        bundle, wakeup, decision_facts=df, social_facts=sf, social_state=social
    )
    inputs = _target_inputs(
        target_time=at,
        event_budget=20,
        decision_steps=[
            _decision_step(
                wakeup["event_id"],
                decision_facts=df,
                social_facts=sf,
                materialization_facts=_household_mat_facts(cand.candidate_id),
            )
        ],
        wakeup_steps=[_wakeup_step(instance_id, as_of=at)],
    )
    return bundle, wakeup, social, inputs, cand, instance_id


def _continue_setup(tmp: str, *, at: str | None = None):
    """Active + due-now decision that resolves CONTINUE_CURRENT."""
    now = at if at is not None else AS_OF
    active = _runtime_active(actual_start=now)
    wakeup = _v2_wakeup(due_at=now, decision_key="idle:c3-continue")
    bundle = _load_c3_bundle(
        tmp, active=active, queue_events=[wakeup], processed_through=now
    )
    social = _social_state(as_of=now)
    df = _decision_facts_continue()
    sf = _social_facts()
    inputs = _target_inputs(
        target_time=now,
        event_budget=20,
        decision_steps=[
            _decision_step(
                wakeup["event_id"],
                decision_facts=df,
                social_facts=sf,
                materialization_facts=None,
            )
        ],
        wakeup_steps=[
            _wakeup_step(active["activity_instance_id"], as_of=now)
        ],
    )
    return bundle, active, wakeup, social, inputs


def _exact_end_ready_setup(tmp: str, *, planned_end: str | None = None):
    """Exact planned_end active with ACTIVITY_END due now; ready to finalize."""
    end = planned_end if planned_end is not None else AS_OF
    active = _planned_end_active(
        planned_end=end, actual_start=_add_min(end, -60)
    )
    end_event = build_activity_end_event(
        activity_instance_id=active["activity_instance_id"],
        planned_end=end,
    )
    assert end_event is not None
    bundle = _load_c3_bundle(
        tmp,
        active=active,
        queue_events=[end_event],
        processed_through=end,
        commitments=[
            _commitment(commitment_id="cmt-a", kind="CLASS", status="PLANNED")
        ],
    )
    return bundle, active, end_event, end


def _exact_end_with_post_finalize_start_inputs(tmp: str):
    """Exact-end setup plus predicted POST_FINALIZE decision/start tape."""
    bundle, active, end_event, end = _exact_end_ready_setup(tmp)
    fin = finalize_runtime_activity_from_exact_end(
        bundle=bundle,
        reference_sets=_advance_refs(),
        behavior_policy=POLICY,
        activity_end_event=end_event,
    )
    pf = build_post_finalize_decision_wakeup(
        character_id=CHAR, actual_event=fin.actual_event
    )
    cur = dict(fin.bundle.current_state)
    cur["pending_queue"] = {"cursor": 0, "events": [pf]}
    probe = RuntimeBundle(
        current_state=validate_checkpoint(cur),
        schedule_state=fin.bundle.schedule_state,
        relation_state=fin.bundle.relation_state,
        home_state=fin.bundle.home_state,
        consumables_state=fin.bundle.consumables_state,
        wardrobe_state=fin.bundle.wardrobe_state,
        finance_state=fin.bundle.finance_state,
    )
    social = _social_state(as_of=end)
    df = _decision_facts_select()
    sf = _social_facts()
    _result, cand, instance_id = _predict_select(
        probe, pf, decision_facts=df, social_facts=sf, social_state=social
    )
    inputs = _target_inputs(
        target_time=end,
        event_budget=20,
        decision_steps=[
            _decision_step(
                pf["event_id"],
                decision_facts=df,
                social_facts=sf,
                materialization_facts=_household_mat_facts(cand.candidate_id),
            )
        ],
        wakeup_steps=[_wakeup_step(instance_id, as_of=end)],
    )
    return bundle, active, end_event, end, pf, social, inputs, cand, instance_id


def _switch_finalize_setup(tmp: str, *, at: str | None = None):
    """Active + due-now decision that SELECT_CANDIDATE switches (finalize, no start)."""
    now = at if at is not None else _add_min(AS_OF, 20)
    # Past min-dwell so CONTINUE is not forced; continuation_feasible=False.
    active = _runtime_active(actual_start=_add_min(now, -30))
    wakeup = _v2_wakeup(due_at=now, decision_key="idle:c3-switch")
    bundle = _load_c3_bundle(
        tmp, active=active, queue_events=[wakeup], processed_through=now
    )
    social = _social_state(as_of=now)
    df = _decision_facts_switch()
    sf = _social_facts()
    # Probe SELECT on active bundle (materialization must be null for active SELECT).
    result = build_runtime_social_decision(
        bundle=bundle,
        reference_sets=_advance_refs(),
        behavior_policy=POLICY,
        trigger=runtime_trigger_from_v2_wakeup(bundle.current_state, wakeup),
        facts=df,
        social_response_state=social,
        social_facts=sf,
    )
    assert result.frame.resolution.result_kind == "SELECT_CANDIDATE", (
        result.frame.resolution.result_kind
    )
    # After switch finalize, POST_FINALIZE will fire — predict start from dry finalize.
    from engine.life.activity_finalization import (
        finalize_runtime_activity_from_decision_switch,
    )

    fin = finalize_runtime_activity_from_decision_switch(
        bundle=bundle,
        reference_sets=_advance_refs(),
        behavior_policy=POLICY,
        frame=result.frame,
        decision_facts=df,
    )
    pf = build_post_finalize_decision_wakeup(
        character_id=CHAR, actual_event=fin.actual_event
    )
    cur = dict(fin.bundle.current_state)
    cur["pending_queue"] = {"cursor": 0, "events": [pf]}
    probe = RuntimeBundle(
        current_state=validate_checkpoint(cur),
        schedule_state=fin.bundle.schedule_state,
        relation_state=fin.bundle.relation_state,
        home_state=fin.bundle.home_state,
        consumables_state=fin.bundle.consumables_state,
        wardrobe_state=fin.bundle.wardrobe_state,
        finance_state=fin.bundle.finance_state,
    )
    _r2, cand2, instance_id = _predict_select(
        probe, pf, decision_facts=_decision_facts_select(), social_facts=sf, social_state=social
    )
    inputs = _target_inputs(
        target_time=now,
        event_budget=30,
        decision_steps=[
            _decision_step(
                wakeup["event_id"],
                decision_facts=df,
                social_facts=sf,
                materialization_facts=None,
            ),
            _decision_step(
                pf["event_id"],
                decision_facts=_decision_facts_select(),
                social_facts=sf,
                materialization_facts=_household_mat_facts(cand2.candidate_id),
            ),
        ],
        wakeup_steps=[_wakeup_step(instance_id, as_of=now)],
    )
    return bundle, active, wakeup, social, inputs, pf, cand2, instance_id
