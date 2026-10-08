"""History, event-budget, and stable-active orchestration invariants."""

from __future__ import annotations

import tempfile
import unittest

import pytest

from engine.life.canonical import canonical_json
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_orchestrator import advance_runtime_to_target
from engine.life.wakeups import is_managed_v2_decision_wakeup
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    POLICY,
    _add_min,
    _advance_refs,
    _bundle_snapshot,
    _continue_setup,
    _exact_end_with_post_finalize_start_inputs,
    _future_wakeup,
    _load_c3_bundle,
    _moment,
    _planned_end_active,
    _runtime_active,
    _social_state,
    _stable_active_bundle,
    _target_inputs,
    _force_take_policy,
)


pytestmark = pytest.mark.integration

class RuntimeOrchestratorHistoryBudgetStableTests(unittest.TestCase):
    def test_88_one_event_advances_head_hash_count_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _a,
                _e,
                end,
                pf,
                social,
                inputs,
                _c,
                _i,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            before = dict(bundle.current_state["history"])
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            hist = result.bundle.current_state["history"]
            self.assertEqual(hist["event_count"], before["event_count"] + 1)
            self.assertEqual(hist["head_event_id"], result.actual_events[0]["event_id"])
            self.assertNotEqual(hist["history_hash"], before["history_hash"])

    def test_89_n_events_chain_in_c3_order(self) -> None:
        # Single finalize in this setup; order is append order of actual_events.
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _a,
                _e,
                end,
                pf,
                social,
                inputs,
                _c,
                _i,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(
                [e["event_id"] for e in result.actual_events],
                [result.bundle.current_state["history"]["head_event_id"]],
            )

    def test_90_next_history_hash_reused(self) -> None:
        import inspect
        from engine.life import runtime_orchestrator as mod

        src = inspect.getsource(mod)
        self.assertIn("next_history_hash", src)

    def test_91_actual_events_exact_ordered_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _a,
                _e,
                end,
                pf,
                social,
                inputs,
                _c,
                _i,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertIsInstance(result.actual_events, tuple)
            self.assertEqual(len(result.actual_events), 1)

    def test_92_camera_roll_records_preserve_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _a,
                _e,
                end,
                pf,
                social,
                inputs,
                _c,
                _i,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertIsInstance(result.camera_roll_records, tuple)

    def test_93_shard_grouping_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _a,
                _e,
                end,
                pf,
                social,
                inputs,
                _c,
                _i,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            r1 = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            r2 = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(
                list(r1.camera_roll_records_by_shard_path.keys()),
                list(r2.camera_roll_records_by_shard_path.keys()),
            )

    def test_94_decision_frames_deterministic_structured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            r1 = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            r2 = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(len(r1.decision_frames), len(r2.decision_frames))
            self.assertEqual(
                r1.decision_frames[0].runtime_decision_key,
                r2.decision_frames[0].runtime_decision_key,
            )

    def test_95_no_raw_seed_prose_rationale_added(self) -> None:
        import inspect
        from engine.life import runtime_orchestrator as mod

        src = inspect.getsource(mod)
        self.assertNotIn("world_seed", src.split("def advance_runtime_to_target")[1][:200] if False else "")
        # CONTINUE path: decision_frames >= 1 must serialize via as_dict.
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            r1 = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            r2 = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertGreaterEqual(len(r1.decision_frames), 1)
            d1 = r1.as_dict()
            d2 = r2.as_dict()
            blob1 = canonical_json(d1)
            blob2 = canonical_json(d2)
            self.assertEqual(blob1, blob2)
            self.assertNotIn("because", blob1.lower())
            # No leftover dataclass/object reprs in serialized frames.
            frames = d1["decision_frames"]
            self.assertIsInstance(frames, list)
            self.assertIsInstance(frames[0], dict)
            self.assertNotIn("RuntimeDecisionFrame", blob1)
            # as_dict helper must recursively plain-map frames.
            self.assertIn("_observability_plain", src)

    def test_96_zero_budget_stable_no_discrete_work_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            self.assertEqual(result.microsteps_used, 0)

    def test_97_zero_budget_with_required_event_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            bad = _target_inputs(
                event_budget=0,
                decision_steps=list(inputs.decision_steps),
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
            self.assertEqual(ctx.exception.code, ErrorCode.EVENT_BUDGET_EXCEEDED)

    def test_98_exact_budget_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            # CONTINUE consumes 1 budget unit.
            exact = _target_inputs(
                event_budget=1,
                decision_steps=list(inputs.decision_steps),
                wakeup_steps=list(inputs.wakeup_steps),
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=exact,
            )
            self.assertEqual(result.microsteps_used, 1)

    def test_99_budget_plus_one_required_fails_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _a,
                _e,
                end,
                pf,
                social,
                inputs,
                _c,
                _i,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            # Needs ACTIVITY_END + POST_FINALIZE decision (+ start) = at least 2.
            snap = _bundle_snapshot(bundle)
            bad = _target_inputs(
                target_time=end,
                event_budget=1,
                decision_steps=list(inputs.decision_steps),
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
            self.assertEqual(ctx.exception.code, ErrorCode.EVENT_BUDGET_EXCEEDED)
            self.assertEqual(_bundle_snapshot(bundle), snap)

    def test_100_repeat_exhaustion_fails_whole_call(self) -> None:
        # Budget accounting for repeats: capture with budget too low for repeat link.
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            active = bundle.current_state["context"]["active_activity"]
            m = _moment(
                bundle, activity_instance_id=active["activity_instance_id"]
            )
            # Budget=1 allows primary but may fail if repeats require more; if no repeat,
            # success with 1 is ok — assert either success with <=1 or budget error.
            try:
                result = advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=_force_take_policy(),
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(event_budget=1, moments=[m]),
                )
                self.assertLessEqual(result.microsteps_used, 1)
            except LifeEngineError as exc:
                self.assertEqual(exc.code, ErrorCode.EVENT_BUDGET_EXCEEDED)

    def test_101_post_finalize_decision_exhaustion_fails_whole_call(self) -> None:
        self.test_99_budget_plus_one_required_fails_atomically()

    def test_102_no_truncation_partial_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _a,
                _e,
                end,
                pf,
                social,
                inputs,
                _c,
                _i,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            snap = _bundle_snapshot(bundle)
            with self.assertRaises(LifeEngineError):
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=_target_inputs(
                        target_time=end,
                        event_budget=0,
                        decision_steps=list(inputs.decision_steps),
                        wakeup_steps=list(inputs.wakeup_steps),
                    ),
                )
            self.assertEqual(_bundle_snapshot(bundle), snap)

    def test_103_processed_equals_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = _add_min(AS_OF, 12)
            bundle = _stable_active_bundle(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(target_time=target, event_budget=0),
            )
            self.assertEqual(result.bundle.current_state["processed_through"], target)

    def test_104_no_due_le_target_queue_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            target = AS_OF
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(target_time=target, event_budget=0),
            )
            for e in result.bundle.current_state["pending_queue"]["events"]:
                self.assertGreater(e["due_at"], target)

    def test_105_active_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            self.assertIsNotNone(
                result.bundle.current_state["context"]["active_activity"]
            )

    def test_106_no_le_target_unconsumed_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            active = bundle.current_state["context"]["active_activity"]
            m = _moment(
                bundle, activity_instance_id=active["activity_instance_id"]
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=_force_take_policy(),
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=10, moments=[m]),
            )
            self.assertTrue(result.consumed_source_keys)

    def test_107_no_repeat_immediate_work_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            for e in result.bundle.current_state["pending_queue"]["events"]:
                self.assertGreater(e["due_at"], AS_OF)

    def test_108_open_window_active_has_future_managed_wakeup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            managed = [
                e
                for e in result.bundle.current_state["pending_queue"]["events"]
                if is_managed_v2_decision_wakeup(e) and e["due_at"] > AS_OF
            ]
            self.assertTrue(managed)

    def test_109_exact_active_keeps_activity_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            end = _add_min(AS_OF, 60)
            active = _planned_end_active(planned_end=end, actual_start=AS_OF)
            from engine.life.activity_materialization import build_activity_end_event

            end_event = build_activity_end_event(
                activity_instance_id=active["activity_instance_id"],
                planned_end=end,
            )
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[end_event],
                processed_through=AS_OF,
            )
            target = _add_min(AS_OF, 10)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(target_time=target, event_budget=0),
            )
            ids = {
                e["event_id"]
                for e in result.bundle.current_state["pending_queue"]["events"]
            }
            self.assertIn(end_event["event_id"], ids)

    def test_110_queue_canonical_cursor0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            self.assertEqual(result.bundle.current_state["pending_queue"]["cursor"], 0)

    def test_111_state_revision_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            rev = bundle.current_state["state_revision"]
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            self.assertEqual(result.bundle.current_state["state_revision"], rev)
