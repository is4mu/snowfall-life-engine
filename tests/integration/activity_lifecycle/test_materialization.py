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

pytestmark = pytest.mark.integration


class ActivityMaterializationFactsDynamicsTests(unittest.TestCase):
    def test_selected_candidate_id_mismatch_reject(self) -> None:
        cand = _candidate()
        mat = _mat_facts(cand, selected_candidate_id="forged-id")
        with self.assertRaises(LifeEngineError):
            _plan(cand, mat=mat, schedule=_schedule(tasks=[_task()]))

    def test_sleep_requires_sleep_none(self) -> None:
        cand = _candidate(
            action_kind="SLEEP_MAIN",
            priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="sleep_pressure:CRITICAL",
            rule_ids=("rule.sleep",),
        )
        with self.assertRaises(LifeEngineError):
            _plan(
                cand,
                mat=_mat_facts(
                    cand,
                    base_activity_class="REST_AWAKE",
                    social_exposure="NONE",
                    sleep_profile=SLEEP_PROF,
                ),
            )
        with self.assertRaises(LifeEngineError):
            _plan(
                cand,
                mat=_mat_facts(
                    cand,
                    base_activity_class="SLEEP",
                    social_exposure="PASSIVE",
                    sleep_profile=SLEEP_PROF,
                ),
            )

    def test_social_contact_requires_active_exposure(self) -> None:
        cmt = _commitment(
            commitment_id="cmt-soc",
            kind="SOCIAL",
            hardness="SOFT",
            source_kind="EXOGENOUS_SOCIAL",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": AS_OF,
                "latest_start": _add_min(AS_OF, 120),
                "duration_min": 60,
                "duration_max": 180,
            },
            location_id=HOME,
            participants=["person-a"],
        )
        cand = _candidate(
            action_kind="SOCIAL_PROMISE",
            priority_tier="SOCIAL_PROMISE",
            source_kind="COMMITMENT",
            source_ref="cmt-soc",
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
                    social_exposure="PASSIVE",
                ),
            )

    def test_invalid_action_dynamics_pair_reject(self) -> None:
        with self.assertRaises(LifeEngineError):
            validate_dynamics_for_activity_type(
                activity_type="STUDY",
                base_activity_class="SLEEP",
                social_exposure="NONE",
            )

    def test_facts_input_non_mutation(self) -> None:
        cand = _candidate()
        mat = _mat_facts(cand)
        before = canonical_json(mat)
        _plan(cand, mat=mat, schedule=_schedule(tasks=[_task()]))
        self.assertEqual(canonical_json(mat), before)

    def test_dynamics_context_survives_and_copies_to_event(self) -> None:
        cand = _candidate()
        plan = _plan(cand, schedule=_schedule(tasks=[_task()]))
        self.assertEqual(plan.dynamics_context.base_activity_class, "SEDENTARY_FOCUSED")
        # Start via 5B1 + install dynamics through full start helper using bundle.
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "pending_queue": {"cursor": 0, "events": []},
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, bundle=bundle)
            result = start_runtime_activity_from_selection(
                bundle=bundle,
                reference_sets=_refs(),
                frame=frame,
                decision_facts=_facts(),
                materialization_facts=_mat_facts(cand),
                behavior_policy=POLICY,
            )
            active = result.active_activity
            self.assertIn("dynamics_context", active)
            self.assertEqual(
                active["dynamics_context"]["base_activity_class"], "SEDENTARY_FOCUSED"
            )
            self.assertIn("runtime_finish_context", active)
            self.assertEqual(
                active["runtime_finish_context"]["task_effort_remaining_at_start_min"],
                120,
            )
            self.assertIsNone(active["runtime_finish_context"]["sleep_need_min"])
            self.assertIsNone(
                active["runtime_finish_context"]["travel_destination_location_id"]
            )
            event = build_actual_event_from_activity(
                active,
                actual_end=_add_min(AS_OF, 30),
                finalized_at=_add_min(AS_OF, 30),
            )
            self.assertEqual(
                event["dynamics_context"]["base_activity_class"], "SEDENTARY_FOCUSED"
            )
            self.assertEqual(event["dynamics_context"]["social_exposure"], "NONE")
            self.assertNotIn("runtime_finish_context", event)

class ActivityMaterializationStartHandoffTests(unittest.TestCase):
    def _start_study(self):
        cand = _candidate()
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "pending_queue": {
                        "cursor": 0,
                        "events": [
                            {
                                "schema_version": 1,
                                "event_id": "wakeup:keep",
                                "event_kind": "IDLE_REASSESSMENT",
                                "due_at": _add_min(AS_OF, 30),
                                "priority": 50,
                                "payload": {"interval_min": 30},
                            }
                        ],
                    },
                    "state_revision": 3,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            before_queue = copy.deepcopy(bundle.current_state["pending_queue"])
            before_rev = bundle.current_state["state_revision"]
            before_sched = copy.deepcopy(dict(bundle.schedule_state))
            before_processed = bundle.current_state["processed_through"]
            frame = _frame(cand, bundle=bundle)
            result = start_runtime_activity_from_selection(
                bundle=bundle,
                reference_sets=_refs(),
                frame=frame,
                decision_facts=_facts(),
                materialization_facts=_mat_facts(cand),
                behavior_policy=POLICY,
            )
            return result, before_queue, before_rev, before_sched, before_processed, cand, frame

    def test_start_via_5b1_identity_and_invariants(self) -> None:
        result, before_queue, before_rev, before_sched, before_processed, cand, frame = (
            self._start_study()
        )
        expected_id = derive_activity_instance_id(
            character_id=CHAR,
            decision_key=frame.resolution.decision_key,
            selected_candidate_id=cand.candidate_id,
        )
        self.assertEqual(result.active_activity["activity_instance_id"], expected_id)
        self.assertEqual(
            result.bundle.current_state["processed_through"], before_processed
        )
        self.assertEqual(result.bundle.current_state["state_revision"], before_rev)
        self.assertEqual(result.bundle.current_state["pending_queue"], before_queue)
        self.assertEqual(dict(result.bundle.schedule_state), before_sched)
        self.assertIsNone(result.activity_end_event)  # study is windowed
        # Input bundle unchanged: original current has no active activity
        # (we don't keep original reference after load, but queue equality above covers C1)

    def test_immediate_social_accept_after_successful_start_only(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        facts = _facts(available_window_min=120)
        pending = _pending_from_decision(decision, cand)
        social_state = build_social_response_state(character_id=CHAR, as_of=AS_OF)

        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            result = start_runtime_activity_from_selection(
                bundle=bundle,
                reference_sets=_refs(),
                frame=frame,
                decision_facts=facts,
                materialization_facts=_mat_facts(
                    cand,
                    base_activity_class="REST_AWAKE",
                    social_exposure="ACTIVE",
                ),
                behavior_policy=POLICY,
                social_responses=(decision,),
                social_response_state=social_state,
                pending_immediate_accept=pending,
            )
            self.assertIsNotNone(result.social_response_state)
            rows = result.social_response_state.responses
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].response, "ACCEPT")
            self.assertEqual(rows[0].opportunity_id, decision.opportunity_id)
            self.assertEqual(
                result.active_activity["details"]["contact_mode"], "CALL"
            )

    def test_failed_social_start_leaves_response_non_terminal(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        facts = _facts(available_window_min=120)
        pending = _pending_from_decision(decision, cand)
        social_state = build_social_response_state(character_id=CHAR, as_of=AS_OF)
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    # Already active → 5B1 start fails
                    "context": {
                        **c["context"],
                        "active_activity": {
                            "schema_version": 1,
                            "activity_instance_id": "preexisting",
                            "activity_type": "REST",
                            "actual_start": AS_OF,
                        },
                    },
                },
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            with self.assertRaises(LifeEngineError):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(
                        cand,
                        base_activity_class="REST_AWAKE",
                        social_exposure="ACTIVE",
                    ),
                    behavior_policy=POLICY,
                    social_responses=(decision,),
                    social_response_state=social_state,
                    pending_immediate_accept=pending,
                )
            # Original social state untouched (no ACCEPT row)
            self.assertEqual(social_state.responses, ())

    def test_no_history_capture_on_start(self) -> None:
        result, *_rest = self._start_study()
        hist = result.bundle.current_state["history"]
        self.assertEqual(hist.get("recent_events") or [], [])
        self.assertEqual(result.active_activity.get("pending_captures") or [], [])

class ActivityMaterializationReviewFixRegressions(unittest.TestCase):
    def test_decision_facts_hash_rejects_altered_route_duration(self) -> None:
        cand = _candidate()
        route = _route(
            route_profile_id="route-1",
            origin_location_id=HOME,
            destination_location_id=CAMPUS,
            duration_min=25,
            duration_max=40,
        )
        frame_facts = _facts(route_profiles=[route], available_window_min=120)
        altered = _facts(
            route_profiles=[
                _route(
                    route_profile_id="route-1",
                    origin_location_id=HOME,
                    destination_location_id=CAMPUS,
                    duration_min=60,
                    duration_max=120,
                )
            ],
            available_window_min=120,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=frame_facts, bundle=bundle)
            with self.assertRaisesRegex(LifeEngineError, "decision_facts_hash"):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=altered,
                    materialization_facts=_mat_facts(cand),
                    behavior_policy=POLICY,
                )

    def test_decision_facts_hash_rejects_altered_available_window(self) -> None:
        cand = _candidate()
        frame_facts = _facts(available_window_min=60)
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=frame_facts, bundle=bundle)
            with self.assertRaisesRegex(LifeEngineError, "decision_facts_hash"):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=_facts(available_window_min=180),
                    materialization_facts=_mat_facts(cand),
                    behavior_policy=POLICY,
                )


    def test_selected_social_without_pending_rejects(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        facts = _facts(available_window_min=120)
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            with self.assertRaisesRegex(LifeEngineError, "pending_immediate_accept"):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(
                        cand,
                        base_activity_class="REST_AWAKE",
                        social_exposure="ACTIVE",
                    ),
                    behavior_policy=POLICY,
                    social_responses=(decision,),
                )

    def test_selected_social_pending_without_social_state_rejects(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        facts = _facts(available_window_min=120)
        pending = _pending_from_decision(decision, cand)
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            with self.assertRaisesRegex(LifeEngineError, "social_response_state"):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(
                        cand,
                        base_activity_class="REST_AWAKE",
                        social_exposure="ACTIVE",
                    ),
                    behavior_policy=POLICY,
                    social_responses=(decision,),
                    pending_immediate_accept=pending,
                )

    def test_stale_pending_decided_at_rejects(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        facts = _facts(available_window_min=120)
        pending = _pending_from_decision(
            decision, cand, decided_at=_add_min(AS_OF, -30)
        )
        social_state = build_social_response_state(character_id=CHAR, as_of=AS_OF)
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            with self.assertRaisesRegex(LifeEngineError, "decided_at"):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(
                        cand,
                        base_activity_class="REST_AWAKE",
                        social_exposure="ACTIVE",
                    ),
                    behavior_policy=POLICY,
                    social_responses=(decision,),
                    social_response_state=social_state,
                    pending_immediate_accept=pending,
                )

    def test_forged_social_responses_cannot_alter_person_mode_duration(self) -> None:
        decision = _social_decision(
            contact_mode="POST_WORK",
            duration_min=30,
            duration_max=90,
            person_id="person-a",
            opportunity_kind="POST_WORK_OPPORTUNITY",
            reason_code="POST_WORK_READY",
        )
        cand = _social_candidate(decision)
        pending = _pending_from_decision(decision, cand)
        forged = SocialResponseDecision(
            response_id=decision.response_id,
            opportunity_id=decision.opportunity_id,
            person_id="forged-person",
            opportunity_kind=decision.opportunity_kind,
            response="ACCEPT",
            reason_code=decision.reason_code,
            contact_mode="CALL",
            duration_min=60,
            duration_max=120,
            resolved_opportunity_key=decision.resolved_opportunity_key,
            commitment_proposal_id=None,
            rule_ids=decision.rule_ids,
        )
        plan = _plan(
            cand,
            mat=_mat_facts(
                cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
            ),
            social_responses=(forged,),
            pending_immediate_accept=pending,
            facts=_facts(available_window_min=120),
        )
        self.assertEqual(plan.start_spec.companions[0]["entity_id"], "person-a")
        self.assertEqual(plan.start_spec.details["contact_mode"], "POST_WORK")
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 30)
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 90)
        )

    def test_pending_wrong_opportunity_kind_rejects(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "opportunity_kind"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, opportunity_kind="INVITE_OPPORTUNITY"
                ),
                facts=_facts(available_window_min=120),
            )

    def test_pending_response_id_mismatch_rejects(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "response_id"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision,
                    cand,
                    response_id=stable_id(
                        "social-response", decision.opportunity_id, "DECLINE"
                    ),
                ),
                facts=_facts(available_window_min=120),
            )

    def test_contact_rejects_local_outing_and_post_work_modes(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "LOCAL_OUTING|CALL or LIGHTWEIGHT"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, contact_mode="LOCAL_OUTING", duration_min=60, duration_max=180
                ),
                facts=_facts(available_window_min=120),
            )
        with self.assertRaisesRegex(LifeEngineError, "CALL or LIGHTWEIGHT|POST_WORK"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision,
                    cand,
                    contact_mode="POST_WORK",
                    duration_min=30,
                    duration_max=90,
                ),
                facts=_facts(available_window_min=120),
            )

    def test_post_work_rejects_call_and_lightweight_modes(self) -> None:
        decision = _social_decision(
            contact_mode="POST_WORK",
            duration_min=30,
            duration_max=90,
            opportunity_kind="POST_WORK_OPPORTUNITY",
            reason_code="POST_WORK_READY",
        )
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "must be POST_WORK"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, contact_mode="CALL", duration_min=15, duration_max=45
                ),
                facts=_facts(available_window_min=120),
            )
        with self.assertRaisesRegex(LifeEngineError, "must be POST_WORK"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision,
                    cand,
                    contact_mode="LIGHTWEIGHT",
                    duration_min=None,
                    duration_max=None,
                ),
                facts=_facts(available_window_min=120),
            )

    def test_wrong_ready_reason_rejects(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "CONTACT_READY"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, reason_code="POST_WORK_READY"
                ),
                facts=_facts(available_window_min=120),
            )
        pw = _social_decision(
            contact_mode="POST_WORK",
            duration_min=30,
            duration_max=90,
            opportunity_kind="POST_WORK_OPPORTUNITY",
            reason_code="POST_WORK_READY",
        )
        cand_pw = _social_candidate(pw)
        with self.assertRaisesRegex(LifeEngineError, "POST_WORK_READY"):
            _plan(
                cand_pw,
                mat=_mat_facts(
                    cand_pw, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    pw, cand_pw, reason_code="CONTACT_READY"
                ),
                facts=_facts(available_window_min=120),
            )

    def test_forged_call_and_post_work_durations_reject(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "window-capped"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, duration_min=60, duration_max=120
                ),
                facts=_facts(available_window_min=180),
            )
        pw = _social_decision(
            contact_mode="POST_WORK",
            duration_min=30,
            duration_max=90,
            opportunity_kind="POST_WORK_OPPORTUNITY",
            reason_code="POST_WORK_READY",
        )
        cand_pw = _social_candidate(pw)
        with self.assertRaisesRegex(LifeEngineError, "window-capped"):
            _plan(
                cand_pw,
                mat=_mat_facts(
                    cand_pw, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    pw, cand_pw, duration_min=15, duration_max=45
                ),
                facts=_facts(available_window_min=180),
            )

    def test_unknown_person_id_rejects_at_start(self) -> None:
        decision = _social_decision(person_id="unknown-person-z")
        cand = _social_candidate(decision)
        facts = _facts(available_window_min=120)
        pending = _pending_from_decision(decision, cand)
        social_state = build_social_response_state(character_id=CHAR, as_of=AS_OF)
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            with self.assertRaisesRegex(LifeEngineError, "known_person_ids"):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(
                        cand,
                        base_activity_class="REST_AWAKE",
                        social_exposure="ACTIVE",
                    ),
                    behavior_policy=POLICY,
                    social_response_state=social_state,
                    pending_immediate_accept=pending,
                )

    def test_selected_response_hash_requires_lowercase_hex(self) -> None:
        decision = _social_decision()
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "64 hex chars"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, selected_response_hash="G" * 64
                ),
                facts=_facts(available_window_min=120),
            )
        with self.assertRaisesRegex(LifeEngineError, "64 hex chars"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, selected_response_hash="A" * 64
                ),
                facts=_facts(available_window_min=120),
            )


    def test_dataclass_malformed_sleep_profile_rejected(self) -> None:
        cand = _candidate(
            action_kind="SLEEP_MAIN",
            priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="sleep:MAIN",
            rule_ids=("rule.sleep",),
        )
        # Direct dataclass with partial/invalid sleep_profile must fail closed.
        bad = RuntimeActivityMaterializationFacts(
            selected_candidate_id=cand.candidate_id,
            base_activity_class="SLEEP",
            social_exposure="NONE",
            meal_source=None,
            meal_extent=None,
            social_contact_mode=None,
            sleep_profile={"sleep_need_min": 420},  # missing required fields
        )
        with self.assertRaises(LifeEngineError):
            parse_runtime_activity_materialization_facts(bad)

    def test_dataclass_non_mapping_sleep_profile_controlled_error(self) -> None:
        cand = _candidate(
            action_kind="SLEEP_MAIN",
            priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="sleep:MAIN",
            rule_ids=("rule.sleep",),
        )
        bad = RuntimeActivityMaterializationFacts(
            selected_candidate_id=cand.candidate_id,
            base_activity_class="SLEEP",
            social_exposure="NONE",
            meal_source=None,
            meal_extent=None,
            social_contact_mode=None,
            sleep_profile="not-a-mapping",  # type: ignore[arg-type]
        )
        with self.assertRaises(LifeEngineError):
            parse_runtime_activity_materialization_facts(bad)

    def test_dynamics_context_dataclass_goes_through_validation(self) -> None:
        bad = DynamicsContext(
            schema_version=1,
            base_activity_class="NOT_A_CLASS",
            social_exposure="NONE",
        )
        with self.assertRaisesRegex(LifeEngineError, "base_activity_class"):
            parse_dynamics_context(bad)

    def test_social_decision_bare_string_rule_ids_rejected(self) -> None:
        bad = SocialResponseDecision(
            response_id=stable_id("social-response", "opp-x", "ACCEPT"),
            opportunity_id="opp-x",
            person_id="person-a",
            opportunity_kind="CONTACT_OPPORTUNITY",
            response="ACCEPT",
            reason_code="CONTACT_READY",
            contact_mode="CALL",
            duration_min=15,
            duration_max=45,
            resolved_opportunity_key=stable_id(
                "social-resolved-opportunity", "opp-x"
            ),
            commitment_proposal_id=None,
            rule_ids="abc",  # type: ignore[arg-type]
        )
        from engine.life.activity_materialization import _parse_social_response_decision

        with self.assertRaisesRegex(LifeEngineError, "rule_ids"):
            _parse_social_response_decision(bad)

class ActivityMaterializationMaterializationContextAndDurationCapTests(unittest.TestCase):
    def test_call_capped_by_available_window_succeeds(self) -> None:
        # policy CALL 15..45, available_window=30 => pending 15..30
        decision = _social_decision(duration_min=15, duration_max=30)
        cand = _social_candidate(decision)
        plan = _plan(
            cand,
            mat=_mat_facts(
                cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
            ),
            pending_immediate_accept=_pending_from_decision(decision, cand),
            facts=_facts(available_window_min=30),
        )
        self.assertEqual(plan.start_spec.details["contact_mode"], "CALL")
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 15)
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 30)
        )

    def test_post_work_capped_by_available_window_succeeds(self) -> None:
        # policy POST_WORK 30..90, available_window=60 => pending 30..60
        decision = _social_decision(
            contact_mode="POST_WORK",
            duration_min=30,
            duration_max=60,
            opportunity_kind="POST_WORK_OPPORTUNITY",
            reason_code="POST_WORK_READY",
        )
        cand = _social_candidate(decision)
        plan = _plan(
            cand,
            mat=_mat_facts(
                cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
            ),
            pending_immediate_accept=_pending_from_decision(decision, cand),
            facts=_facts(available_window_min=60),
        )
        self.assertEqual(plan.start_spec.details["contact_mode"], "POST_WORK")
        self.assertEqual(
            plan.start_spec.expected_end_window["earliest"], _add_min(AS_OF, 30)
        )
        self.assertEqual(
            plan.start_spec.expected_end_window["latest"], _add_min(AS_OF, 60)
        )

    def test_call_duration_cap_off_by_rejects(self) -> None:
        decision = _social_decision(duration_min=15, duration_max=30)
        cand = _social_candidate(decision)
        # larger than exact cap
        with self.assertRaisesRegex(LifeEngineError, "window-capped"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, duration_min=15, duration_max=31
                ),
                facts=_facts(available_window_min=30),
            )
        # smaller than exact cap
        with self.assertRaisesRegex(LifeEngineError, "window-capped"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, duration_min=15, duration_max=29
                ),
                facts=_facts(available_window_min=30),
            )

    def test_post_work_duration_cap_off_by_rejects(self) -> None:
        decision = _social_decision(
            contact_mode="POST_WORK",
            duration_min=30,
            duration_max=60,
            opportunity_kind="POST_WORK_OPPORTUNITY",
            reason_code="POST_WORK_READY",
        )
        cand = _social_candidate(decision)
        with self.assertRaisesRegex(LifeEngineError, "window-capped"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, duration_min=30, duration_max=61
                ),
                facts=_facts(available_window_min=60),
            )
        with self.assertRaisesRegex(LifeEngineError, "window-capped"):
            _plan(
                cand,
                mat=_mat_facts(
                    cand, base_activity_class="REST_AWAKE", social_exposure="ACTIVE"
                ),
                pending_immediate_accept=_pending_from_decision(
                    decision, cand, duration_min=30, duration_max=59
                ),
                facts=_facts(available_window_min=60),
            )

    def test_materialization_context_original_succeeds(self) -> None:
        cand = _candidate()
        facts = _facts()
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            result = start_runtime_activity_from_selection(
                bundle=bundle,
                reference_sets=_refs(),
                frame=frame,
                decision_facts=facts,
                materialization_facts=_mat_facts(cand),
                behavior_policy=POLICY,
            )
            self.assertEqual(
                result.active_activity["activity_type"],
                ACTION_KIND_TO_ACTIVITY_TYPE["STUDY"],
            )

    def test_materialization_context_rejects_policy_version_change(self) -> None:
        cand = _candidate()
        facts = _facts()
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            altered_policy = copy.deepcopy(dict(POLICY))
            altered_policy["behavior_policy_version"] = "forged-policy-version"
            with self.assertRaisesRegex(LifeEngineError, "behavior_policy_version"):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(cand),
                    behavior_policy=altered_policy,
                )

    def test_materialization_context_rejects_same_version_policy_value_change(
        self,
    ) -> None:
        cand = _candidate()
        facts = _facts()
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            altered_policy = copy.deepcopy(dict(POLICY))
            # Keep version; change a numeric social duration so policy hash drifts.
            altered_policy["social_policy"]["contact_mode_durations"]["CALL"][
                "duration_max"
            ] = 99
            with self.assertRaisesRegex(
                LifeEngineError, "materialization_context_hash"
            ):
                start_runtime_activity_from_selection(
                    bundle=bundle,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(cand),
                    behavior_policy=altered_policy,
                )

    def test_materialization_context_rejects_schedule_semantics_change(self) -> None:
        cand = _candidate()
        facts = _facts()
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            # Mutate schedule after frame bind (timing/task semantics).
            drifted_schedule = build_schedule_state(
                character_id=CHAR,
                as_of=AS_OF,
                obligation_tasks=[
                    _task(
                        task_id="task-a",
                        min_work_chunk_min=45,
                        effort_remaining_min=180,
                    )
                ],
            )
            drifted = bundle.__class__(
                current_state=bundle.current_state,
                schedule_state=drifted_schedule,
                relation_state=bundle.relation_state,
                home_state=bundle.home_state,
                consumables_state=bundle.consumables_state,
                wardrobe_state=bundle.wardrobe_state,
                finance_state=bundle.finance_state,
            )
            # domain_refs must still match drifted schedule for bundle validate.
            drifted_current = copy.deepcopy(dict(bundle.current_state))
            drifted_current["domain_refs"] = {
                **drifted_current["domain_refs"],
                "schedule_revision": drifted_schedule["revision"],
                "schedule_hash": drifted_schedule["state_hash"],
            }
            drifted = bundle.__class__(
                current_state=drifted_current,
                schedule_state=drifted_schedule,
                relation_state=bundle.relation_state,
                home_state=bundle.home_state,
                consumables_state=bundle.consumables_state,
                wardrobe_state=bundle.wardrobe_state,
                finance_state=bundle.finance_state,
            )
            with self.assertRaisesRegex(
                LifeEngineError, "materialization_context_hash"
            ):
                start_runtime_activity_from_selection(
                    bundle=drifted,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(cand),
                    behavior_policy=POLICY,
                )

    def test_materialization_context_rejects_location_change(self) -> None:
        cand = _candidate()
        facts = _facts()
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            frame = _frame(cand, decision_facts=facts, bundle=bundle)
            drifted_current = copy.deepcopy(dict(bundle.current_state))
            drifted_current["context"] = {
                **drifted_current["context"],
                "location_id": CAMPUS,
            }
            drifted = bundle.__class__(
                current_state=drifted_current,
                schedule_state=bundle.schedule_state,
                relation_state=bundle.relation_state,
                home_state=bundle.home_state,
                consumables_state=bundle.consumables_state,
                wardrobe_state=bundle.wardrobe_state,
                finance_state=bundle.finance_state,
            )
            with self.assertRaisesRegex(
                LifeEngineError, "materialization_context_hash"
            ):
                start_runtime_activity_from_selection(
                    bundle=drifted,
                    reference_sets=_refs(),
                    frame=frame,
                    decision_facts=facts,
                    materialization_facts=_mat_facts(cand),
                    behavior_policy=POLICY,
                )
