"""Shared synthetic builders for activity finalization tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_finalization import (
    finalize_runtime_activity_from_decision_switch,
    finalize_runtime_activity_from_exact_end,
    runtime_activity_reached_completion_boundary,
)
from engine.life.activity_materialization import build_activity_end_event
from engine.life.canonical import canonical_json
from engine.life.errors import LifeEngineError
from engine.life.events import validate_active_activity
from engine.life.fixed_point import empty_rate_remainders
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.runtime_decision import (
    build_decision_facts_hash,
    build_materialization_context_hash,
    parse_runtime_decision_facts,
)
from engine.life.schedule_state import build_schedule_state
from engine.life.timeutil import add_minutes, format_rfc3339, parse_rfc3339
from engine.life.wakeups import (
    FutureCandidate,
    build_v2_decision_wakeup_event,
    is_managed_v2_decision_wakeup,
)
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.builders.runtime_bundle import (
    CHAR,
    PERSON,
    build_synthetic_bundle_root,
    provenance as _prov,
    reference_sets as _refs,
)
from tests.support.builders.schedule import commitment as _commitment, task as _task
from tests.support.builders.runtime_decision import AS_OF, HOME, _facts
from tests.support.builders.activity_materialization import (
    DECISION_KEY,
    _candidate,
    _frame,
    _resolution,
)

POLICY = load_policy(POLICY_V2_CANDIDATE_PATH)
POLICY_VERSION = POLICY["behavior_policy_version"]


def _add_min(ts: str, minutes: int) -> str:
    return format_rfc3339(add_minutes(parse_rfc3339(ts, field="ts"), minutes))


def _finish_ctx(**overrides: Any) -> dict[str, Any]:
    base = {
        "schema_version": 1,
        "sleep_need_min": None,
        "travel_destination_location_id": None,
        "task_effort_remaining_at_start_min": None,
    }
    base.update(overrides)
    return base


def _runtime_active(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "activity_instance_id": "act-c2b-1",
        "activity_type": "STUDY",
        "actual_start": AS_OF,
        "planned_end": None,
        "expected_end": None,
        "expected_end_window": {
            "earliest": _add_min(AS_OF, 30),
            "latest": _add_min(AS_OF, 90),
        },
        "location_id": HOME,
        "companions": [],
        "effects_on_end": [],
        "pending_captures": [],
        "details": None,
        "runtime_context": {
            "schema_version": 1,
            "decision_key": DECISION_KEY,
            "selected_candidate_id": "cand-1",
            "selected_candidate_key": "cand-key",
            "action_kind": "STUDY",
            "priority_tier": "DEADLINE_TASK",
            "source_kind": "TASK",
            "source_ref": "task-a",
            "interruptibility": "REASSESSABLE",
            "local_preference_permille": 400,
            "rule_ids": ["rule.study", "slice5b1.activity-start"],
        },
        "causes": [
            {"cause_type": "DECISION", "ref": DECISION_KEY},
            {"cause_type": "TASK", "ref": "task-a"},
        ],
        "decision_evidence": {
            "decision_type": "ACTION_SELECTION",
            "policy_version": POLICY_VERSION,
            "rule_ids": ["rule.resolver"],
            "input_state_refs": ["trigger:trig-1"],
            "random_key": "b" * 64,
            "selected_result": "cand-1",
        },
        "dynamics_context": {
            "schema_version": 1,
            "base_activity_class": "SEDENTARY_FOCUSED",
            "social_exposure": "NONE",
        },
        "runtime_finish_context": _finish_ctx(task_effort_remaining_at_start_min=120),
    }
    base.update(overrides)
    out = validate_active_activity(base)
    assert out is not None
    return out


def _planned_end_active(
    *,
    planned_end: str | None = None,
    activity_instance_id: str = "act-exact-1",
    **overrides: Any,
) -> dict[str, Any]:
    end = planned_end if planned_end is not None else _add_min(AS_OF, 60)
    return _runtime_active(
        activity_instance_id=activity_instance_id,
        planned_end=end,
        expected_end=end,
        expected_end_window=None,
        runtime_context={
            **_runtime_active()["runtime_context"],
            "interruptibility": "NON_INTERRUPTIBLE",
            "action_kind": "HARD_COMMITMENT_ACTIVITY",
            "priority_tier": "HARD_COMMITMENT",
            "source_kind": "COMMITMENT",
            "source_ref": "cmt-a",
            "rule_ids": ["rule.hard", "slice5b1.activity-start"],
        },
        causes=[
            {"cause_type": "DECISION", "ref": DECISION_KEY},
            {"cause_type": "COMMITMENT", "ref": "cmt-a"},
        ],
        activity_type="CLASS",
        commitment_id="cmt-a",
        details=None,
        dynamics_context={
            "schema_version": 1,
            "base_activity_class": "SEDENTARY_FOCUSED",
            "social_exposure": "NONE",
        },
        runtime_finish_context=_finish_ctx(),
        **overrides,
    )


def _v2_wakeup(*, due_at: str | None = None, decision_key: str = "idle:switch") -> dict:
    return build_v2_decision_wakeup_event(
        character_id=CHAR,
        candidate=FutureCandidate(
            due_at=due_at or AS_OF,
            trigger_kind="IDLE",
            decision_key=decision_key,
            reason_code="IDLE_REASSESSMENT",
            metric=None,
            target_band=None,
            direction=None,
            source_kind=None,
            source_ref=None,
        ),
    )


def _unrelated_event(*, due_at: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": "unrelated:keep",
        "event_kind": "IDLE_REASSESSMENT",
        "due_at": due_at or _add_min(AS_OF, 120),
        "priority": 50,
        "payload": {"interval_min": 30},
    }


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


def _load_bundle(
    tmp: str,
    *,
    active: dict[str, Any] | None = None,
    queue_events: list | None = None,
    human_state: dict[str, int] | None = None,
    processed_through: str | None = None,
    commitments: list | None = None,
    tasks: list | None = None,
    location_id: str = HOME,
    rate_remainders: dict[str, int] | None = None,
    sleep_context: dict[str, Any] | None = None,
    mutate_current=None,
) -> Any:
    act = active if active is not None else _runtime_active()
    hs = human_state or {
        "sleep_debt_min": 0,
        "hunger": 200,
        "physical_fatigue": 100,
        "affect_valence": 0,
        "stress": 100,
        "social_battery": 800,
    }
    events = queue_events if queue_events is not None else []
    from engine.life.invariants import queue_event_sort_key

    events = sorted((dict(e) for e in events), key=queue_event_sort_key)
    processed = processed_through if processed_through is not None else AS_OF
    remainders = rate_remainders if rate_remainders is not None else empty_rate_remainders()
    task_list = tasks if tasks is not None else [_task()]
    cmt_list = commitments if commitments is not None else []

    def _mut(c: dict) -> dict:
        out = {
            **c,
            "behavior_policy_version": POLICY_VERSION,
            "processed_through": processed,
            "state_revision": 11,
            "human_state": hs,
            "integration": {
                "schema_version": 1,
                "rate_remainders": remainders,
            },
            "pending_queue": {"cursor": 0, "events": events},
            "context": {
                **c["context"],
                "active_activity": act,
                "location_id": location_id,
            },
        }
        if sleep_context is not None:
            out["sleep_context"] = sleep_context
        if mutate_current is not None:
            out = mutate_current(out)
        return out

    root = build_synthetic_bundle_root(
        Path(tmp),
        mutate_current=_mut,
        mutate_schedule=lambda s: build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            commitments=cmt_list,
            obligation_tasks=task_list,
        ),
    )
    return load_runtime_bundle(root, reference_sets=_refs())


def _exact_end_setup(
    tmp: str,
    *,
    planned_end: str | None = None,
    extra_events: list | None = None,
    active_overrides: dict | None = None,
    **bundle_kwargs,
):
    end = planned_end if planned_end is not None else _add_min(AS_OF, 60)
    active = _planned_end_active(planned_end=end, **(active_overrides or {}))
    end_event = build_activity_end_event(
        activity_instance_id=active["activity_instance_id"],
        planned_end=end,
    )
    assert end_event is not None
    events = [end_event]
    if extra_events:
        events.extend(extra_events)
    bundle = _load_bundle(
        tmp,
        active=active,
        queue_events=events,
        processed_through=end,
        commitments=[
            _commitment(commitment_id="cmt-a", kind="CLASS", status="PLANNED")
        ],
        **bundle_kwargs,
    )
    return bundle, active, end_event, end


def _switch_setup(
    tmp: str,
    *,
    processed: str | None = None,
    active: dict | None = None,
    selected_cand=None,
    extra_events: list | None = None,
    decision_facts: Mapping[str, Any] | None = None,
    **bundle_kwargs,
):
    at = processed if processed is not None else _add_min(AS_OF, 20)
    act = active if active is not None else _runtime_active()
    cand = selected_cand if selected_cand is not None else _candidate(
        candidate_key="cand-key-next",
        action_kind="MEAL",
        priority_tier="URGENT_BIOLOGICAL",
        source_kind="BIOLOGICAL",
        source_ref="hunger:URGENT",
        rule_ids=("rule.meal",),
    )
    wakeup = _v2_wakeup(due_at=at)
    events = [wakeup]
    if extra_events:
        events.extend(extra_events)
    bundle = _load_bundle(
        tmp,
        active=act,
        queue_events=events,
        processed_through=at,
        **bundle_kwargs,
    )
    facts_raw = decision_facts if decision_facts is not None else _facts()
    from engine.life.runtime_decision import (
        derive_runtime_decision_key,
        runtime_trigger_from_v2_wakeup,
    )

    trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, wakeup)
    runtime_decision_key = derive_runtime_decision_key(
        character_id=CHAR,
        trigger_id=trigger.trigger_id,
    )
    frame = _frame(
        cand,
        bundle=bundle,
        decision_facts=facts_raw,
        trigger=trigger,
        runtime_decision_key=runtime_decision_key,
    )
    return bundle, act, frame, facts_raw, cand, wakeup, at


def _assert_common_result(
    test: unittest.TestCase,
    result,
    *,
    finish_path: str,
    discarded: str | None,
    processed: str,
    revision: int,
    history,
    input_snap: dict,
    input_bundle,
) -> None:
    test.assertEqual(result.finish_path, finish_path)
    test.assertTrue(result.post_finalize_decision_required)
    test.assertEqual(result.discarded_selected_candidate_id, discarded)
    test.assertIsNone(result.bundle.current_state["context"]["active_activity"])
    test.assertEqual(result.bundle.current_state["processed_through"], processed)
    test.assertEqual(result.bundle.current_state["state_revision"], revision)
    test.assertEqual(result.bundle.current_state.get("history"), history)
    test.assertEqual(_bundle_snapshot(input_bundle), input_snap)
    test.assertNotIn("runtime_finish_context", result.actual_event)


