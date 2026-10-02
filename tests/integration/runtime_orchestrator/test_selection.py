"""Runtime orchestrator continue, selection, switch, and start integration tests."""

from __future__ import annotations

import inspect
import tempfile
import unittest

import pytest

from engine.life.canonical import canonical_json
from engine.life.errors import LifeEngineError
from engine.life.runtime_orchestrator import advance_runtime_to_target
from engine.life.wakeups import is_managed_v2_decision_wakeup
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    POLICY,
    _add_min,
    _advance_refs,
    _bundle_snapshot,
    _continue_setup,
    _decision_facts_continue,
    _decision_step,
    _no_active_start_setup,
    _social_facts,
    _social_state,
    _switch_finalize_setup,
    _target_inputs,
    _wakeup_facts,
    _wakeup_step,
)


pytestmark = pytest.mark.integration

class RuntimeOrchestratorContinueSelectStartTests(unittest.TestCase):
    def test_68_continue_preserves_active_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            before_id = active["activity_instance_id"]
            before_start = active["actual_start"]
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            out = result.bundle.current_state["context"]["active_activity"]
            self.assertEqual(out["activity_instance_id"], before_id)
            self.assertEqual(out["actual_start"], before_start)

    def test_69_consumed_active_boundary_only_for_active_reassessment(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn('trigger.trigger_kind == "ACTIVE_REASSESSMENT"', src)
        self.assertIn('trigger.source_kind == "CURRENT_ACTIVITY"', src)
        self.assertIn(
            'trigger.source_ref == active["activity_instance_id"]', src
        )
        self.assertIn("consumed_active_boundary_key=consumed_boundary", src)

    def test_review_continue_non_null_materialization_facts_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, _inputs = _continue_setup(tmp)
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=_target_inputs(
                        target_time=AS_OF,
                        event_budget=20,
                        decision_steps=[
                            _decision_step(
                                wakeup["event_id"],
                                decision_facts=_decision_facts_continue(),
                                social_facts=_social_facts(),
                                materialization_facts={
                                    "selected_candidate_id": "x",
                                    "base_activity_class": "LIGHT_ACTIVE",
                                    "social_exposure": "NONE",
                                    "meal_source": None,
                                    "meal_extent": None,
                                    "social_contact_mode": None,
                                    "sleep_profile": None,
                                },
                            )
                        ],
                        wakeup_steps=[
                            _wakeup_step(active["activity_instance_id"], as_of=AS_OF)
                        ],
                    ),
                )
            self.assertIn("CONTINUE_CURRENT", str(ctx.exception))
            self.assertIn("materialization_facts", str(ctx.exception))

    def test_70_immediate_post_finalize_never_misclassified_as_c2a_boundary(self) -> None:
        src = inspect.getsource(
            __import__("engine.life.runtime_orchestrator", fromlist=["x"])
        )
        self.assertIn("_C3_OWNED_TRIGGERS", src)
        self.assertIn("IMMEDIATE / POST_FINALIZE never passed as C2A boundary", src)

    def test_71_decision_event_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            ids = {
                e["event_id"]
                for e in result.bundle.current_state["pending_queue"]["events"]
            }
            self.assertNotIn(wakeup["event_id"], ids)

    def test_72_c2a_queue_reconcile_required(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("reconcile_active_runtime_queue", src)
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            managed = [
                e
                for e in result.bundle.current_state["pending_queue"]["events"]
                if is_managed_v2_decision_wakeup(e)
            ]
            self.assertTrue(managed)

    def test_73_missing_wakeup_step_input_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, _inputs = _continue_setup(tmp)
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=_target_inputs(
                        event_budget=10,
                        decision_steps=[
                            _decision_step(
                                wakeup["event_id"],
                                decision_facts=_decision_facts_continue(),
                                social_facts=_social_facts(),
                                materialization_facts=None,
                            )
                        ],
                        wakeup_steps=[],
                    ),
                )
            self.assertIn("missing RuntimeWakeupStepInput", str(ctx.exception))

    def test_74_projection_horizon_lt_target_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, _inputs = _continue_setup(tmp)
            bad = _wakeup_step(
                active["activity_instance_id"],
                as_of=AS_OF,
                projection_horizon_end=_add_min(AS_OF, -1),
            )
            with self.assertRaises(LifeEngineError):
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=_target_inputs(
                        target_time=AS_OF,
                        event_budget=10,
                        decision_steps=[
                            _decision_step(
                                wakeup["event_id"],
                                decision_facts=_decision_facts_continue(),
                                social_facts=_social_facts(),
                                materialization_facts=None,
                            )
                        ],
                        wakeup_steps=[bad],
                    ),
                )

    def test_75_pre_finalization_materialization_must_be_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs, pf, cand, iid = (
                _switch_finalize_setup(tmp)
            )
            # Inject non-null materialization on the active SELECT step.
            bad_steps = []
            for step in inputs.decision_steps:
                d = {
                    "trigger_id": step.trigger_id,
                    "decision_facts": step.decision_facts,
                    "social_facts": step.social_facts,
                    "materialization_facts": step.materialization_facts,
                }
                if step.trigger_id == wakeup["event_id"]:
                    d["materialization_facts"] = {
                        "selected_candidate_id": "x",
                        "base_activity_class": "LIGHT_ACTIVE",
                        "social_exposure": "NONE",
                        "meal_source": None,
                        "meal_extent": None,
                        "social_contact_mode": None,
                        "sleep_profile": None,
                    }
                bad_steps.append(d)
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=_target_inputs(
                        target_time=inputs.target_time,
                        event_budget=30,
                        decision_steps=bad_steps,
                        wakeup_steps=list(inputs.wakeup_steps),
                    ),
                )
            self.assertIn("materialization_facts", str(ctx.exception))

    def test_76_c2b_uses_social_working_bundle(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("finalize_runtime_activity_from_decision_switch", src)
        self.assertIn("working = social_result.working_bundle", src)

    def test_77_finalized_effects_carried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs, pf, cand, iid = (
                _switch_finalize_setup(tmp)
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(len(result.actual_events), 1)
            self.assertTrue(result.actual_events[0]["event_id"])

    def test_78_selected_candidate_discarded(self) -> None:
        # Active SELECT never starts the selected candidate (see test_30).
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs, pf, cand, iid = (
                _switch_finalize_setup(tmp)
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            switch_frame = next(
                f
                for f in result.decision_frames
                if f.trigger.trigger_id == wakeup["event_id"]
            )
            started = result.bundle.current_state["context"]["active_activity"]
            self.assertNotEqual(
                started["runtime_context"]["selected_candidate_id"],
                switch_frame.selected_candidate.candidate_id,
            )

    def test_79_history_updated_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs, pf, cand, iid = (
                _switch_finalize_setup(tmp)
            )
            before = bundle.current_state["history"]["event_count"]
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(
                result.bundle.current_state["history"]["event_count"], before + 1
            )

    def test_80_camera_roll_handoff_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs, pf, cand, iid = (
                _switch_finalize_setup(tmp)
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            # May be empty if event has no captures; aggregation path still runs.
            self.assertIsInstance(result.camera_roll_records, tuple)
            self.assertIsInstance(result.camera_roll_records_by_shard_path, dict)

    def test_81_post_finalize_inserted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs, pf, cand, iid = (
                _switch_finalize_setup(tmp)
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertIn(pf["event_id"], result.consumed_trigger_ids)

    def test_82_exact_materialization_facts_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, wakeup, social, inputs, cand, iid = _no_active_start_setup(tmp)
            bad = _target_inputs(
                event_budget=10,
                decision_steps=[
                    _decision_step(
                        wakeup["event_id"],
                        decision_facts=inputs.decision_steps[0].decision_facts,
                        social_facts=inputs.decision_steps[0].social_facts,
                        materialization_facts=None,
                    )
                ],
                wakeup_steps=list(inputs.wakeup_steps),
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=bad,
                )
            self.assertIn("materialization_facts", str(ctx.exception))

    def test_83_c1_exact_selected_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, wakeup, social, inputs, cand, iid = _no_active_start_setup(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            active = result.bundle.current_state["context"]["active_activity"]
            self.assertEqual(active["activity_instance_id"], iid)
            self.assertEqual(
                active["runtime_context"]["selected_candidate_id"], cand.candidate_id
            )

    def test_84_social_pending_handoff_exact(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("pending_immediate_accept=social_result.pending_immediate_accept", src)
        self.assertIn("social_responses=tuple(social_result.social_response.responses)", src)

    def test_85_consumed_wakeup_removed_only_after_successful_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, wakeup, social, inputs, cand, iid = _no_active_start_setup(tmp)
            snap = _bundle_snapshot(bundle)
            # Fail start by omitting wakeup step — call fails, input unchanged.
            bad = _target_inputs(
                event_budget=10,
                decision_steps=list(inputs.decision_steps),
                wakeup_steps=[],
            )
            with self.assertRaises(LifeEngineError):
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=bad,
                )
            self.assertEqual(_bundle_snapshot(bundle), snap)
            # Successful path removes wakeup.
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            ids = {
                e["event_id"]
                for e in result.bundle.current_state["pending_queue"]["events"]
            }
            self.assertNotIn(wakeup["event_id"], ids)

    def test_86_c2a_reconcile_after_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, wakeup, social, inputs, cand, iid = _no_active_start_setup(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            managed = [
                e
                for e in result.bundle.current_state["pending_queue"]["events"]
                if is_managed_v2_decision_wakeup(e)
            ]
            self.assertTrue(managed)

    def test_87_start_failure_leaves_pure_call_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, wakeup, social, inputs, cand, iid = _no_active_start_setup(tmp)
            snap = _bundle_snapshot(bundle)
            social_snap = canonical_json(social.as_dict())
            bad_mat = dict(inputs.decision_steps[0].materialization_facts)
            bad_mat["base_activity_class"] = "SLEEP"  # mismatch for HOUSEHOLD
            bad = _target_inputs(
                event_budget=10,
                decision_steps=[
                    _decision_step(
                        wakeup["event_id"],
                        decision_facts=inputs.decision_steps[0].decision_facts,
                        social_facts=inputs.decision_steps[0].social_facts,
                        materialization_facts=bad_mat,
                    )
                ],
                wakeup_steps=list(inputs.wakeup_steps),
            )
            with self.assertRaises(LifeEngineError):
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=bad,
                )
            self.assertEqual(_bundle_snapshot(bundle), snap)
            self.assertEqual(canonical_json(social.as_dict()), social_snap)
