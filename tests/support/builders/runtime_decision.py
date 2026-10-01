"""Shared synthetic builders for RuntimeDecision integration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_runtime import (
    ActivityStartSpec,
    start_activity_from_selection,
)
from engine.life.checkpoint import empty_checkpoint
from engine.life.decisions import (
    ActionCandidate,
    DecisionResolution,
    candidate_id_for,
)
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.runtime_decision import (
    build_runtime_decision_frame,
    runtime_trigger_from_v2_wakeup,
)
from engine.life.wakeups import FutureCandidate, build_v2_decision_wakeup_event
from tests.support.builders.runtime_bundle import (
    AS_OF,
    CHAR,
    build_synthetic_bundle_root,
    reference_sets as _refs,
)
from tests.support.builders.schedule import commitment as _commitment, task as _task
from tests.support.constants import POLICY_V2_CANDIDATE_PATH

POLICY = load_policy(POLICY_V2_CANDIDATE_PATH)
POLICY_VERSION = POLICY["behavior_policy_version"]
HOME = "fixture-home"
CAMPUS = "fixture-campus"


def _v2_wakeup(
    *,
    due_at: str = AS_OF,
    trigger_kind: str = "IDLE",
    decision_key: str = "idle:fixture",
    reason_code: str = "IDLE_REASSESSMENT",
    metric: str | None = None,
    target_band: str | None = None,
    direction: str | None = None,
    source_kind: str | None = None,
    source_ref: str | None = None,
    character_id: str = CHAR,
) -> dict[str, Any]:
    return build_v2_decision_wakeup_event(
        character_id=character_id,
        candidate=FutureCandidate(
            due_at=due_at,
            trigger_kind=trigger_kind,
            decision_key=decision_key,
            reason_code=reason_code,
            metric=metric,
            target_band=target_band,
            direction=direction,
            source_kind=source_kind,
            source_ref=source_ref,
        ),
    )


def _threshold_wakeup(**overrides: Any) -> dict[str, Any]:
    base = dict(
        trigger_kind="THRESHOLD",
        decision_key="threshold:HUNGER:MEAL_NEEDED:UP",
        reason_code="THRESHOLD_HUNGER_MEAL_NEEDED_UP",
        metric="HUNGER",
        target_band="MEAL_NEEDED",
        direction="UP",
    )
    base.update(overrides)
    return _v2_wakeup(**base)


def _structural_phys(**overrides: bool) -> dict[str, bool]:
    base = {
        "WORK": True,
        "HARD_COMMITMENT_ACTIVITY": True,
        "TRAVEL_TO_COMMITMENT": True,
        "SOCIAL_PROMISE": True,
        "ERRAND": True,
        "STUDY": True,
    }
    base.update(overrides)
    return base


def _routine_phys(**overrides: bool) -> dict[str, bool]:
    base = {"HOUSEHOLD": True, "KICKBOXING": True}
    base.update(overrides)
    return base


def _facts(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "available_window_min": 120,
        "route_profiles": [],
        "structural_physical_feasible_by_action": _structural_phys(),
        "household_tasks": [],
        "kickboxing_sessions": [],
        "routine_physical_feasible_by_action": _routine_phys(),
        "kickboxing_location_feasible": False,
        "kickboxing_self_generated_today": 0,
        "kickboxing_equivalent_pending_plans": 0,
        "continuation_feasible": None,
        "meal_feasible": False,
        "rest_feasible": False,
        "sleep_pressure": None,
        "sleep_opportunity": None,
        "last_meal_at": None,
        "meal_extent": None,
        "soft_guards": [],
        "free_window_key": None,
        "free_window_min": None,
        "feasible_leisure_categories": [],
    }
    base.update(overrides)
    return base


def _route(**overrides: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "route_profile_id": "route-home-campus",
        "origin_location_id": HOME,
        "destination_location_id": CAMPUS,
        "mode": "PUBLIC_TRANSIT",
        "duration_min": 25,
        "duration_max": 45,
        "effort_class": "LIGHT_ACTIVE",
        "provenance": {"origin": "ENGINE_DEFAULT", "notes": "runtime-decision-synthetic"},
    }
    base.update(overrides)
    return base


def _household(**overrides: Any) -> dict[str, Any]:
    base = {
        "household_task_id": "hh-1",
        "created_at": "2026-03-30T12:00:00+09:00",
        "status": "OPEN",
        "location_id": HOME,
    }
    base.update(overrides)
    return base


def _load_bundle(
    tmp: Path,
    *,
    events: list[dict[str, Any]] | None = None,
    mutate_current: Any = None,
    mutate_schedule: Any = None,
    human_state: Mapping[str, int] | None = None,
    active_activity: Mapping[str, Any] | None = None,
    world_seed: str | None = None,
):
    event_list = list(events or [])

    def _mutate(current: dict) -> dict:
        current = dict(current)
        current["behavior_policy_version"] = POLICY_VERSION
        if world_seed is not None:
            current["world_seed"] = world_seed
        if human_state is not None:
            current["human_state"] = dict(human_state)
        if active_activity is not None:
            current["context"] = dict(current["context"])
            current["context"]["active_activity"] = dict(active_activity)
        current["pending_queue"] = {"cursor": 0, "events": list(event_list)}
        if mutate_current is not None:
            current = mutate_current(current)
        return current

    root = build_synthetic_bundle_root(
        tmp,
        mutate_current=_mutate,
        mutate_schedule=mutate_schedule,
    )
    return load_runtime_bundle(root, reference_sets=_refs())


def _frame_for(
    tmp: Path,
    *,
    event: dict[str, Any] | None = None,
    facts: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    mutate_schedule: Any = None,
    human_state: Mapping[str, int] | None = None,
    active_activity: Mapping[str, Any] | None = None,
    world_seed: str | None = None,
    mutate_current: Any = None,
):
    ev = event if event is not None else _v2_wakeup()
    bundle = _load_bundle(
        tmp,
        events=[ev],
        mutate_schedule=mutate_schedule,
        human_state=human_state,
        active_activity=active_activity,
        world_seed=world_seed,
        mutate_current=mutate_current,
    )
    trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, ev)
    return build_runtime_decision_frame(
        bundle=bundle,
        reference_sets=_refs(),
        behavior_policy=policy if policy is not None else POLICY,
        trigger=trigger,
        facts=facts if facts is not None else _facts(),
    ), bundle, trigger, ev


def _started_active() -> dict[str, Any]:
    state = empty_checkpoint(
        character_id=CHAR,
        life_epoch="2026-04-01T00:00:00+09:00",
        world_seed="fixture-world-seed-runtime-decision",
        behavior_policy_version=POLICY_VERSION,
        engine_commit_sha="a" * 40,
        location_id=HOME,
        human_state={
            "sleep_debt_min": 0,
            "hunger": 100,
            "physical_fatigue": 100,
            "affect_valence": 0,
            "stress": 100,
            "social_battery": 800,
        },
    )
    state["processed_through"] = AS_OF
    cand = ActionCandidate(
        candidate_key="cand-key-study",
        candidate_id=candidate_id_for(
            character_id=CHAR,
            decision_key="decision-key-1",
            candidate_key="cand-key-study",
        ),
        action_kind="STUDY",
        priority_tier="DEADLINE_TASK",
        source_kind="TASK",
        source_ref="task:task-a",
        soft_candidate=False,
        time_feasible=True,
        location_feasible=True,
        physical_feasible=True,
        domain_guard_satisfied=True,
        local_preference_permille=400,
        activity_instance_id=None,
        rule_ids=("rule.study",),
    )
    res = DecisionResolution(
        result_kind="SELECT_CANDIDATE",
        decision_key="decision-key-1",
        selected_candidate_id=cand.candidate_id,
        selected_candidate_key=cand.candidate_key,
        selected_action_kind=cand.action_kind,
        selected_priority_tier=cand.priority_tier,
        activity_instance_id=None,
        considered_candidate_ids=(cand.candidate_id,),
        rejected=(),
        rule_ids=("rule.resolver", "rule.study"),
        random_key="b" * 64,
        selection_mode="single",
    )
    started = start_activity_from_selection(
        current_state=state,
        policy_version=POLICY_VERSION,
        resolution=res,
        selected_candidate=cand,
        start_spec=ActivityStartSpec(
            activity_type="STUDY_BLOCK",
            actual_start=AS_OF,
            interruptibility="REASSESSABLE",
            planned_end=None,
            expected_end="2026-04-01T14:00:00+09:00",
            expected_end_window=None,
            location_id="fixture-desk",
            companions=(),
            commitment_id=None,
            details=None,
            effects_on_end=(),
        ),
        input_state_refs=("state:current", "state:schedule"),
    )
    return started["context"]["active_activity"]


