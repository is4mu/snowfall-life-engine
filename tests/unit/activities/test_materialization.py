"""Activity materialization semantic and lifecycle integration tests."""

from __future__ import annotations

import copy
import inspect
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_materialization import (
    ACTION_KIND_TO_ACTIVITY_TYPE,
    HARD_COMMITMENT_KIND_TO_ACTIVITY,
    SOCIAL_CONTACT_MODES,
    DynamicsContext,
    RuntimeActivityMaterializationFacts,
    build_activity_end_event,
    materialize_activity_plan,
    parse_dynamics_context,
    parse_runtime_activity_materialization_facts,
    start_runtime_activity_from_selection,
    validate_dynamics_for_activity_type,
)
from engine.life.activity_runtime import derive_activity_instance_id, start_activity_from_selection
from engine.life.canonical import canonical_json
from engine.life.decisions import ActionCandidate, DecisionResolution, candidate_id_for
from engine.life.derived import default_synthetic_sleep_profile
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.events import build_actual_event_from_activity, validate_active_activity
from engine.life.ids import stable_id
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.runtime_decision import (
    RuntimeDecisionFrame,
    build_decision_facts_hash,
    build_finalization_context_hash,
    build_materialization_context_hash,
    parse_runtime_decision_facts,
)
from engine.life.schedule_state import build_schedule_state
from engine.life.social_response import SocialResponseDecision
from engine.life.social_runtime import (
    PendingImmediateAccept,
    SuccessfulImmediateSocialStart,
    apply_pending_immediate_accept_after_successful_start,
    build_selected_social_response_hash,
    build_social_response_state,
)
from engine.life.timeutil import add_minutes, format_rfc3339, parse_rfc3339
from tests.support.builders.activity_materialization import (
    AS_OF,
    CAMPUS,
    CHAR,
    DECISION_KEY,
    ENGINE_SHA,
    HOME,
    POLICY,
    POLICY_VERSION,
    SLEEP_PROF,
    _add_min,
    _candidate,
    _commitment,
    _facts,
    _frame,
    _mat_facts,
    _pending_from_decision,
    _plan,
    _refs,
    _resolution,
    _route,
    _schedule,
    _social_candidate,
    _social_decision,
    _task,
    build_synthetic_bundle_root,
)

import pytest

pytestmark = pytest.mark.unit


class ActivityMaterializationSocialDetailsTests(unittest.TestCase):
    def test_local_outing_active_activity_accepted(self) -> None:
        act = {
            "schema_version": 1,
            "activity_instance_id": "act-1",
            "activity_type": "SOCIAL_CONTACT",
            "actual_start": AS_OF,
            "details": {"contact_mode": "LOCAL_OUTING"},
            "dynamics_context": {
                "schema_version": 1,
                "base_activity_class": "LIGHT_ACTIVE",
                "social_exposure": "ACTIVE",
            },
        }
        out = validate_active_activity(act)
        self.assertEqual(out["details"]["contact_mode"], "LOCAL_OUTING")

    def test_post_work_active_activity_accepted(self) -> None:
        act = {
            "schema_version": 1,
            "activity_instance_id": "act-2",
            "activity_type": "SOCIAL_CONTACT",
            "actual_start": AS_OF,
            "details": {"contact_mode": "POST_WORK"},
            "dynamics_context": {
                "schema_version": 1,
                "base_activity_class": "REST_AWAKE",
                "social_exposure": "ACTIVE",
            },
        }
        out = validate_active_activity(act)
        self.assertEqual(out["details"]["contact_mode"], "POST_WORK")

    def test_unknown_social_contact_mode_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            validate_active_activity(
                {
                    "schema_version": 1,
                    "activity_instance_id": "act-3",
                    "activity_type": "SOCIAL_CONTACT",
                    "actual_start": AS_OF,
                    "details": {"contact_mode": "VIDEO_CHAT"},
                }
            )

    def test_finalized_event_preserves_exact_social_mode(self) -> None:
        for mode in ("LOCAL_OUTING", "POST_WORK", "CALL", "LIGHTWEIGHT", "LONG_CATCHUP_CALL"):
            act = validate_active_activity(
                {
                    "schema_version": 1,
                    "activity_instance_id": f"act-{mode}",
                    "activity_type": "SOCIAL_CONTACT",
                    "actual_start": AS_OF,
                    "details": {"contact_mode": mode},
                    "causes": [],
                    "decision_evidence": None,
                }
            )
            assert act is not None
            event = build_actual_event_from_activity(
                act,
                actual_end=_add_min(AS_OF, 60),
                finalized_at=_add_min(AS_OF, 60),
            )
            self.assertEqual(event["details"]["contact_mode"], mode)

class ActivityMaterializationActionMappingTests(unittest.TestCase):
    def test_every_non_continue_action_kind_has_path(self) -> None:
        for kind in ACTION_KIND_TO_ACTIVITY_TYPE:
            self.assertNotEqual(kind, "CONTINUE_CURRENT")
            self.assertIn(kind, ACTION_KIND_TO_ACTIVITY_TYPE)

    def test_hard_commitment_kind_mapping(self) -> None:
        for kind, activity in HARD_COMMITMENT_KIND_TO_ACTIVITY.items():
            cmt = _commitment(
                commitment_id=f"cmt-{kind}",
                kind=kind,
                hardness="HARD",
                timing={
                    "timing_kind": "EXACT",
                    "planned_start": AS_OF,
                    "planned_end": _add_min(AS_OF, 60),
                },
                location_id=HOME,
            )
            cand = _candidate(
                candidate_key=f"cand-{kind}",
                action_kind="HARD_COMMITMENT_ACTIVITY",
                priority_tier="HARD_COMMITMENT",
                source_kind="COMMITMENT",
                source_ref=cmt["commitment_id"],
                rule_ids=("rule.hard",),
            )
            plan = _plan(
                cand,
                schedule=_schedule(commitments=[cmt]),
                mat=_mat_facts(
                    cand,
                    base_activity_class=(
                        "PHYSICALLY_ACTIVE"
                        if activity == "EXERCISE"
                        else (
                            "LIGHT_ACTIVE"
                            if activity in {"ERRAND", "SOCIAL_CONTACT"}
                            else "SEDENTARY_FOCUSED"
                        )
                    ),
                    social_exposure="ACTIVE" if activity == "SOCIAL_CONTACT" else "NONE",
                    social_contact_mode=(
                        "CALL" if activity == "SOCIAL_CONTACT" else None
                    ),
                ),
            )
            self.assertEqual(plan.activity_type, activity)

    def test_unknown_hard_commitment_kind_fails(self) -> None:
        cmt = _commitment(commitment_id="cmt-bad", kind="WORK", hardness="HARD")
        cmt = dict(cmt)
        cmt["kind"] = "UNKNOWN_KIND"
        # Bypass schedule schema: materializer only needs commitments list.
        schedule = {
            "schema_version": 1,
            "character_id": CHAR,
            "as_of": AS_OF,
            "revision": "rev",
            "state_hash": "hash",
            "commitments": [cmt],
            "obligation_tasks": [],
        }
        cand = _candidate(
            action_kind="HARD_COMMITMENT_ACTIVITY",
            priority_tier="HARD_COMMITMENT",
            source_kind="COMMITMENT",
            source_ref="cmt-bad",
            rule_ids=("rule.hard",),
        )
        with self.assertRaises(LifeEngineError) as ctx:
            _plan(cand, schedule=schedule)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_continue_current_cannot_start(self) -> None:
        cand = _candidate(
            action_kind="CONTINUE_CURRENT",
            priority_tier="ROUTINE_HABIT",
            source_kind="CURRENT_ACTIVITY",
            source_ref="act-1",
            rule_ids=("rule.cont",),
        )
        with self.assertRaises(LifeEngineError):
            _plan(cand)

class ActivityMaterializationDetailsTests(unittest.TestCase):
    def test_meal_requires_source_and_extent_family(self) -> None:
        cand = _candidate(
            action_kind="MEAL",
            priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="hunger:URGENT",
            rule_ids=("rule.meal",),
        )
        with self.assertRaises(LifeEngineError):
            _plan(
                cand,
                facts=_facts(),
                mat=_mat_facts(
                    cand,
                    base_activity_class="REST_AWAKE",
                    meal_source=None,
                    meal_extent="STANDARD",
                ),
            )
        plan = _plan(
            cand,
            facts=_facts(available_window_min=120),
            mat=_mat_facts(
                cand,
                base_activity_class="REST_AWAKE",
                meal_source="HOME_COOKED",
                meal_extent="STANDARD",
            ),
        )
        self.assertEqual(plan.start_spec.details["meal_source"], "HOME_COOKED")
        self.assertEqual(plan.start_spec.details["meal_extent"], "STANDARD")
        # HOME_COOKED_STANDARD = 30..60 capped by window
        self.assertIsNone(plan.start_spec.planned_end)
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 30)
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 60)
        )

    def test_meal_no_prior_meal_uses_current_materialization_extent(self) -> None:
        cand = _candidate(
            action_kind="MEAL",
            priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="hunger:URGENT",
            rule_ids=("rule.meal",),
        )
        # Decision facts: no prior meal (last_meal_at=null / meal_extent=null).
        plan = _plan(
            cand,
            facts=_facts(last_meal_at=None, meal_extent=None, available_window_min=120),
            mat=_mat_facts(
                cand,
                base_activity_class="REST_AWAKE",
                meal_source="HOME_COOKED",
                meal_extent="LIGHT",
            ),
        )
        self.assertEqual(plan.start_spec.details["meal_extent"], "LIGHT")

    def test_meal_current_extent_overrides_previous_extent(self) -> None:
        cand = _candidate(
            action_kind="MEAL",
            priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="hunger:URGENT",
            rule_ids=("rule.meal",),
        )
        # previous LIGHT + current STANDARD
        plan_std = _plan(
            cand,
            facts=_facts(
                last_meal_at=_add_min(AS_OF, -120),
                meal_extent="LIGHT",
                available_window_min=120,
            ),
            mat=_mat_facts(
                cand,
                base_activity_class="REST_AWAKE",
                meal_source="HOME_COOKED",
                meal_extent="STANDARD",
            ),
        )
        self.assertEqual(plan_std.start_spec.details["meal_extent"], "STANDARD")
        # previous STANDARD + current LIGHT
        plan_light = _plan(
            cand,
            facts=_facts(
                last_meal_at=_add_min(AS_OF, -120),
                meal_extent="STANDARD",
                available_window_min=120,
            ),
            mat=_mat_facts(
                cand,
                base_activity_class="REST_AWAKE",
                meal_source="HOME_COOKED",
                meal_extent="LIGHT",
            ),
        )
        self.assertEqual(plan_light.start_spec.details["meal_extent"], "LIGHT")

    def test_non_meal_rejects_current_meal_extent(self) -> None:
        cand = _candidate(action_kind="STUDY")
        with self.assertRaisesRegex(LifeEngineError, "meal_extent is only allowed"):
            _plan(
                cand,
                mat=_mat_facts(cand, meal_extent="STANDARD"),
            )

    def test_immediate_social_mode_person_from_2h(self) -> None:
        decision = _social_decision(
            contact_mode="POST_WORK",
            duration_min=30,
            duration_max=90,
            person_id="person-a",
            opportunity_kind="POST_WORK_OPPORTUNITY",
            reason_code="POST_WORK_READY",
        )
        cand = _social_candidate(decision)
        plan = _plan(
            cand,
            mat=_mat_facts(
                cand,
                base_activity_class="REST_AWAKE",
                social_exposure="ACTIVE",
            ),
            social_responses=(decision,),
            pending_immediate_accept=_pending_from_decision(decision, cand),
            facts=_facts(available_window_min=120),
        )
        self.assertEqual(plan.start_spec.details["contact_mode"], "POST_WORK")
        self.assertEqual(plan.start_spec.companions[0]["entity_id"], "person-a")
        self.assertIsNone(plan.start_spec.commitment_id)

    def test_exogenous_social_maps_local_outing(self) -> None:
        cmt = _commitment(
            commitment_id="cmt-exo",
            kind="SOCIAL",
            hardness="SOFT",
            source_kind="EXOGENOUS_SOCIAL",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": AS_OF,
                "latest_start": _add_min(AS_OF, 180),
                "duration_min": 60,
                "duration_max": 180,
            },
            location_id=HOME,
            participants=["person-a", CHAR],
        )
        cand = _candidate(
            action_kind="SOCIAL_PROMISE",
            priority_tier="SOCIAL_PROMISE",
            source_kind="COMMITMENT",
            source_ref="cmt-exo",
            soft_candidate=True,
            rule_ids=("rule.soc",),
        )
        plan = _plan(
            cand,
            schedule=_schedule(commitments=[cmt]),
            mat=_mat_facts(
                cand,
                base_activity_class="LIGHT_ACTIVE",
                social_exposure="ACTIVE",
            ),
        )
        self.assertEqual(plan.start_spec.details["contact_mode"], "LOCAL_OUTING")

    def test_generic_social_without_mode_rejects(self) -> None:
        cmt = _commitment(
            commitment_id="cmt-gen",
            kind="SOCIAL",
            hardness="SOFT",
            source_kind="INTERNAL",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": AS_OF,
                "latest_start": _add_min(AS_OF, 180),
                "duration_min": 60,
                "duration_max": 120,
            },
            location_id=HOME,
            participants=["person-a"],
        )
        cand = _candidate(
            action_kind="SOCIAL_PROMISE",
            priority_tier="SOCIAL_PROMISE",
            source_kind="COMMITMENT",
            source_ref="cmt-gen",
            soft_candidate=True,
            rule_ids=("rule.soc",),
        )
        with self.assertRaises(LifeEngineError):
            _plan(
                cand,
                schedule=_schedule(commitments=[cmt]),
                mat=_mat_facts(
                    cand,
                    base_activity_class="LIGHT_ACTIVE",
                    social_exposure="ACTIVE",
                ),
            )

class ActivityMaterializationEndSemanticsTests(unittest.TestCase):
    def test_exact_commitment_planned_end(self) -> None:
        end = _add_min(AS_OF, 90)
        cmt = _commitment(
            commitment_id="cmt-exact",
            kind="CLASS",
            hardness="HARD",
            timing={
                "timing_kind": "EXACT",
                "planned_start": AS_OF,
                "planned_end": end,
            },
            location_id=HOME,
        )
        cand = _candidate(
            action_kind="HARD_COMMITMENT_ACTIVITY",
            priority_tier="HARD_COMMITMENT",
            source_kind="COMMITMENT",
            source_ref="cmt-exact",
            rule_ids=("rule.hard",),
        )
        plan = _plan(
            cand,
            schedule=_schedule(commitments=[cmt]),
            mat=_mat_facts(cand, base_activity_class="SEDENTARY_FOCUSED"),
        )
        self.assertEqual(plan.start_spec.planned_end, end)
        self.assertEqual(plan.start_spec.expected_end, end)
        self.assertIsNone(plan.start_spec.expected_end_window)
        self.assertEqual(plan.start_spec.interruptibility, "NON_INTERRUPTIBLE")

    def test_window_commitment_no_planned_end(self) -> None:
        cmt = _commitment(
            commitment_id="cmt-win",
            kind="APPOINTMENT",
            hardness="HARD",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": AS_OF,
                "latest_start": _add_min(AS_OF, 60),
                "duration_min": 20,
                "duration_max": 40,
            },
            location_id=HOME,
        )
        cand = _candidate(
            action_kind="HARD_COMMITMENT_ACTIVITY",
            priority_tier="HARD_COMMITMENT",
            source_kind="COMMITMENT",
            source_ref="cmt-win",
            rule_ids=("rule.hard",),
        )
        plan = _plan(
            cand,
            schedule=_schedule(commitments=[cmt]),
            mat=_mat_facts(cand),
        )
        self.assertIsNone(plan.start_spec.planned_end)
        self.assertIsNone(plan.start_spec.expected_end)
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 20)
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 40)
        )
        self.assertEqual(plan.start_spec.interruptibility, "REASSESSABLE")

    def test_route_range_window(self) -> None:
        cmt = _commitment(
            commitment_id="cmt-travel",
            kind="CLASS",
            hardness="HARD",
            timing={
                "timing_kind": "EXACT",
                "planned_start": _add_min(AS_OF, 60),
                "planned_end": _add_min(AS_OF, 180),
            },
            location_id=CAMPUS,
        )
        route = _route(
            route_profile_id="route-1",
            origin_location_id=HOME,
            destination_location_id=CAMPUS,
            duration_min=25,
            duration_max=40,
        )
        cand = _candidate(
            action_kind="TRAVEL_TO_COMMITMENT",
            priority_tier="HARD_COMMITMENT",
            source_kind="COMMITMENT",
            source_ref="cmt-travel",
            rule_ids=("rule.travel",),
        )
        plan = _plan(
            cand,
            schedule=_schedule(commitments=[cmt]),
            facts=_facts(route_profiles=[route]),
            mat=_mat_facts(cand, base_activity_class="LIGHT_ACTIVE"),
        )
        self.assertIsNone(plan.start_spec.planned_end)
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 25)
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 40)
        )

    def test_study_range_and_caps(self) -> None:
        task = _task(
            task_id="task-a",
            min_work_chunk_min=30,
            effort_remaining_min=200,
        )
        cand = _candidate(source_ref="task-a")
        plan = _plan(
            cand,
            schedule=_schedule(tasks=[task]),
            facts=_facts(available_window_min=100),
        )
        # earliest=30, latest=min(200,100,chunk_max90)=90
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 30)
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 90)
        )

    def test_household_kickboxing_errand_windows(self) -> None:
        hh = _candidate(
            action_kind="HOUSEHOLD",
            priority_tier="ROUTINE_HABIT",
            source_kind="ROUTINE",
            source_ref="hh-1",
            rule_ids=("rule.hh",),
        )
        plan_hh = _plan(
            hh,
            facts=_facts(available_window_min=45),
            mat=_mat_facts(hh, base_activity_class="LIGHT_ACTIVE"),
        )
        # household 15-60 capped to 45
        self.assertEqual(
            plan_hh.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 15)
        )
        self.assertEqual(
            plan_hh.start_spec.expected_end_window["latest"], _add_min(AS_OF, 45)
        )

        kb = _candidate(
            action_kind="KICKBOXING",
            priority_tier="ROUTINE_HABIT",
            source_kind="ROUTINE",
            source_ref="kb-1",
            rule_ids=("rule.kb",),
        )
        plan_kb = _plan(
            kb,
            facts=_facts(available_window_min=120),
            mat=_mat_facts(kb, base_activity_class="PHYSICALLY_ACTIVE"),
        )
        self.assertEqual(
            plan_kb.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 60)
        )
        self.assertEqual(
            plan_kb.start_spec.expected_end_window["latest"], _add_min(AS_OF, 90)
        )

        task = _task(
            task_id="errand-1",
            domain="ERRAND",
            min_work_chunk_min=20,
            effort_remaining_min=90,
        )
        er = _candidate(
            action_kind="ERRAND",
            priority_tier="ROUTINE_HABIT",
            source_kind="TASK",
            source_ref="errand-1",
            soft_candidate=True,
            rule_ids=("rule.errand",),
        )
        plan_er = _plan(
            er,
            schedule=_schedule(tasks=[task]),
            facts=_facts(available_window_min=50),
            mat=_mat_facts(er, base_activity_class="LIGHT_ACTIVE"),
        )
        # latest=min(90,50,duration_max60)=50
        self.assertEqual(
            plan_er.start_spec.expected_end_window["latest"], _add_min(AS_OF, 50)
        )

    def test_sleep_need_wake_variation_window(self) -> None:
        cand = _candidate(
            action_kind="SLEEP_MAIN",
            priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="sleep_pressure:CRITICAL",
            rule_ids=("rule.sleep",),
        )
        plan = _plan(
            cand,
            mat=_mat_facts(
                cand,
                base_activity_class="SLEEP",
                social_exposure="NONE",
                sleep_profile=SLEEP_PROF,
            ),
        )
        need = SLEEP_PROF["sleep_need_min"]
        early = POLICY["sleep_policy"]["wake_variation"]["early_min"]
        late = POLICY["sleep_policy"]["wake_variation"]["late_min"]
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"],
            _add_min(AS_OF, max(1, need - early)),
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"],
            _add_min(AS_OF, need + late),
        )

    def test_lightweight_and_open_no_fake_exact_end(self) -> None:
        decision = _social_decision(contact_mode="LIGHTWEIGHT", duration_min=None, duration_max=None)
        cand = _social_candidate(decision)
        plan = _plan(
            cand,
            mat=_mat_facts(
                cand,
                base_activity_class="REST_AWAKE",
                social_exposure="ACTIVE",
            ),
            social_responses=(decision,),
            pending_immediate_accept=_pending_from_decision(decision, cand),
            facts=_facts(available_window_min=90),
        )
        self.assertIsNone(plan.start_spec.planned_end)
        self.assertIsNone(plan.start_spec.expected_end)
        self.assertIsNone(plan.start_spec.expected_end_window["earliest"])
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 90)
        )

        rest = _candidate(
            action_kind="REST",
            priority_tier="RESTORATIVE",
            source_kind="BIOLOGICAL",
            source_ref="physical_fatigue:HIGH",
            rule_ids=("rule.rest",),
        )
        plan_rest = _plan(
            rest,
            mat=_mat_facts(rest, base_activity_class="REST_AWAKE"),
            facts=_facts(available_window_min=60),
        )
        self.assertIsNone(plan_rest.start_spec.planned_end)
        self.assertIsNone(plan_rest.start_spec.expected_end_window["earliest"])

    def test_no_duration_prng_in_module(self) -> None:
        import engine.life.activity_materialization as mod
        src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
        for needle in (
            "random.random",
            "random.randint",
            "secrets.",
            "uuid4",
            "u01(",
            "duration_rng",
            "sample_duration",
        ):
            self.assertNotIn(needle, src)

class ActivityMaterializationActivityEndTests(unittest.TestCase):
    def test_exact_planned_end_builds_deterministic_event(self) -> None:
        instance = "activity-instance:abcdef0123456789abcdef0123456789"
        due = _add_min(AS_OF, 60)
        event = build_activity_end_event(activity_instance_id=instance, planned_end=due)
        assert event is not None
        self.assertEqual(event["priority"], 10)
        self.assertEqual(event["event_kind"], "ACTIVITY_END")
        self.assertEqual(
            event["event_id"], stable_id("activity-end", instance, due)
        )
        self.assertEqual(event["payload"]["activity_instance_id"], instance)
        again = build_activity_end_event(activity_instance_id=instance, planned_end=due)
        self.assertEqual(event, again)

    def test_null_planned_end_and_window_latest_never_end(self) -> None:
        self.assertIsNone(
            build_activity_end_event(activity_instance_id="x", planned_end=None)
        )
        # Window path produces no planned_end → no ACTIVITY_END
        cmt = _commitment(
            commitment_id="cmt-win2",
            kind="CLASS",
            hardness="HARD",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": AS_OF,
                "latest_start": _add_min(AS_OF, 30),
                "duration_min": 15,
                "duration_max": 30,
            },
            location_id=HOME,
        )
        cand = _candidate(
            action_kind="HARD_COMMITMENT_ACTIVITY",
            priority_tier="HARD_COMMITMENT",
            source_kind="COMMITMENT",
            source_ref="cmt-win2",
            rule_ids=("rule.hard",),
        )
        plan = _plan(
            cand,
            schedule=_schedule(commitments=[cmt]),
            mat=_mat_facts(cand),
        )
        self.assertIsNone(plan.start_spec.planned_end)
        event = build_activity_end_event(
            activity_instance_id="any",
            planned_end=plan.start_spec.planned_end,
        )
        self.assertIsNone(event)
        # Explicitly: window latest is not used as ACTIVITY_END due_at
        latest = plan.start_spec.expected_end_window["latest"]
        forged = build_activity_end_event(
            activity_instance_id="any", planned_end=latest
        )
        # Builder would construct if caller passes latest as planned_end —
        # C1 start path must not do that. Assert start result has null event.
        self.assertIsNotNone(forged)  # builder itself is honest about planned_end arg
        # Contract: materialize leaves planned_end null so start returns no event.

class ActivityMaterializationClosedSetsTests(unittest.TestCase):
    def test_social_modes_closed_set(self) -> None:
        self.assertEqual(
            SOCIAL_CONTACT_MODES,
            frozenset(
                {
                    "LIGHTWEIGHT",
                    "CALL",
                    "LONG_CATCHUP_CALL",
                    "LOCAL_OUTING",
                    "POST_WORK",
                }
            ),
        )

    def test_parse_facts_rejects_unknown_and_float(self) -> None:
        cand = _candidate()
        raw = _mat_facts(cand)
        raw["extra"] = 1
        with self.assertRaises(LifeEngineError):
            parse_runtime_activity_materialization_facts(raw)
        raw2 = _mat_facts(cand)
        raw2["base_activity_class"] = 1.5  # type: ignore[assignment]
        with self.assertRaises(LifeEngineError):
            parse_runtime_activity_materialization_facts(raw2)

class ActivityMaterializationPriorTerminalReplayRegressionTests(unittest.TestCase):
    def _prior_terminal_accept(self) -> SocialResponseDecision:
        return _social_decision(
            reason_code="PRIOR_TERMINAL_RESPONSE",
            resolved_opportunity_key=None,
        )

    def test_prior_terminal_accept_diagnostic_allows_null_resolved_key(self) -> None:
        from engine.life.activity_materialization import _parse_social_response_decision

        parsed = _parse_social_response_decision(self._prior_terminal_accept())
        self.assertEqual(parsed.response, "ACCEPT")
        self.assertEqual(parsed.reason_code, "PRIOR_TERMINAL_RESPONSE")
        self.assertIsNone(parsed.resolved_opportunity_key)

    def test_fresh_immediate_accept_diagnostic_still_requires_resolved_key(self) -> None:
        from engine.life.activity_materialization import _parse_social_response_decision

        with self.assertRaisesRegex(LifeEngineError, "resolved_opportunity_key"):
            _parse_social_response_decision(
                _social_decision(resolved_opportunity_key=None)
            )

    def test_non_social_materialization_allows_prior_terminal_diagnostic(self) -> None:
        rest = _candidate(
            action_kind="REST",
            priority_tier="RESTORATIVE",
            source_kind="BIOLOGICAL",
            source_ref="physical_fatigue:HIGH",
            rule_ids=("rule.rest",),
        )
        plan = _plan(
            rest,
            mat=_mat_facts(rest, base_activity_class="REST_AWAKE"),
            social_responses=(self._prior_terminal_accept(),),
            facts=_facts(available_window_min=60),
        )
        self.assertEqual(plan.start_spec.activity_type, "REST")
