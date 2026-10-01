"""Shared synthetic builders for activity lifecycle wakeup tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engine.life.activity_materialization import build_activity_end_event
from engine.life.derived import default_synthetic_sleep_profile
from engine.life.fixed_point import empty_rate_remainders
from engine.life.invariants import queue_event_sort_key
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.schedule_state import build_schedule_state
from engine.life.timeutil import add_minutes, format_rfc3339, parse_rfc3339
from tests.support.builders.runtime_bundle import (
    CHAR,
    build_synthetic_bundle_root,
    reference_sets as _refs,
)
from tests.support.builders.runtime_decision import AS_OF, HOME
from tests.support.builders.schedule import task as _task
from tests.support.constants import POLICY_V2_CANDIDATE_PATH

POLICY = load_policy(POLICY_V2_CANDIDATE_PATH)
POLICY_VERSION = POLICY["behavior_policy_version"]
SLEEP_PROF = dict(default_synthetic_sleep_profile()._asdict())
DECISION_KEY = "runtime-decision-c2a-wakeups"
HORIZON = "2026-04-02T12:00:00+09:00"


def _add_min(ts: str, minutes: int) -> str:
    return format_rfc3339(add_minutes(parse_rfc3339(ts, field="ts"), minutes))


def _runtime_active(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "activity_instance_id": "act-wakeup-1",
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
    }
    base.update(overrides)
    out = validate_active_activity(base)
    assert out is not None
    return out


def _main_sleep_active(*, sleep_need_min: int | None = None) -> dict[str, Any]:
    need = SLEEP_PROF["sleep_need_min"] if sleep_need_min is None else sleep_need_min
    wake = POLICY["sleep_policy"]["wake_variation"]
    early = wake["early_min"]
    late = wake["late_min"]
    earliest = _add_min(AS_OF, max(1, need - early))
    latest = _add_min(AS_OF, need + late)
    return _runtime_active(
        activity_instance_id="act-sleep-main",
        activity_type="SLEEP",
        planned_end=None,
        expected_end=None,
        expected_end_window={"earliest": earliest, "latest": latest},
        details={"sleep_kind": "MAIN"},
        runtime_context={
            **_runtime_active()["runtime_context"],
            "action_kind": "SLEEP_MAIN",
            "priority_tier": "URGENT_BIOLOGICAL",
            "source_kind": "BIOLOGICAL",
            "source_ref": "sleep:main",
            "interruptibility": "REASSESSABLE",
            "rule_ids": ["rule.sleep", "slice5b1.activity-start"],
        },
        dynamics_context={
            "schema_version": 1,
            "base_activity_class": "SLEEP",
            "social_exposure": "NONE",
        },
        causes=[
            {"cause_type": "DECISION", "ref": DECISION_KEY},
            {"cause_type": "BIOLOGICAL", "ref": "sleep:main"},
        ],
    )


def _wakeup_facts(**overrides: Any) -> dict[str, Any]:
    base = {
        "sleep_profile": dict(SLEEP_PROF),
        "elapsed_since_last_main_sleep_end_seconds": 8 * 3600,
        "total_nap_awake_credit_min": 0,
        "fatigue_high_relevant": False,
        "stress_relevant": False,
        "social_low_relevant": False,
        "known_boundaries": [],
        "projection_horizon_end": HORIZON,
    }
    base.update(overrides)
    return base


def _load_bundle(
    tmp: str,
    *,
    active: dict[str, Any] | None = None,
    queue_events: list | None = None,
    human_state: dict[str, int] | None = None,
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

    root = build_synthetic_bundle_root(
        Path(tmp),
        mutate_current=lambda c: {
            **c,
            "behavior_policy_version": POLICY_VERSION,
            "processed_through": AS_OF,
            "state_revision": 11,
            "human_state": hs,
            "integration": {
                "schema_version": 1,
                "rate_remainders": empty_rate_remainders(),
            },
            "pending_queue": {"cursor": 0, "events": events},
            "context": {**c["context"], "active_activity": act, "location_id": HOME},
        },
        mutate_schedule=lambda s: build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            obligation_tasks=[_task()],
        ),
    )
    return load_runtime_bundle(root, reference_sets=_refs())


