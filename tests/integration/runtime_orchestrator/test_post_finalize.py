"""Post-finalize wakeup construction and runtime orchestration."""

from __future__ import annotations

import tempfile
import unittest

import pytest

from engine.life.canonical import canonical_json
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.ids import stable_id
from engine.life.runtime_decision import derive_managed_v2_decision_wakeup_event_id
from engine.life.runtime_orchestrator import (
    advance_runtime_to_target,
    build_post_finalize_decision_wakeup,
    derive_post_finalize_decision_key,
)
from engine.life.wakeups import KNOWN_BOUNDARY_TRIGGERS, V2_DECISION_WAKEUP_PRIORITY
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    CHAR,
    POLICY,
    _advance_refs,
    _bundle_snapshot,
    _exact_end_with_post_finalize_start_inputs,
    _switch_finalize_setup,
)


pytestmark = pytest.mark.integration

class RuntimeOrchestratorPostFinalizeBuilderTests(unittest.TestCase):
    def _event(self) -> dict:
        return {
            "event_id": "evt-finalize-1",
            "actual_end": AS_OF,
        }

    def test_21_exact_deterministic_builder(self) -> None:
        a = build_post_finalize_decision_wakeup(
            character_id=CHAR, actual_event=self._event()
        )
        b = build_post_finalize_decision_wakeup(
            character_id=CHAR, actual_event=self._event()
        )
        self.assertEqual(a, b)

    def test_22_schema_v2_priority_50(self) -> None:
        ev = build_post_finalize_decision_wakeup(
            character_id=CHAR, actual_event=self._event()
        )
        self.assertEqual(ev["schema_version"], 2)
        self.assertEqual(ev["event_kind"], "DECISION_WAKEUP")
        self.assertEqual(ev["priority"], V2_DECISION_WAKEUP_PRIORITY)
        self.assertEqual(ev["priority"], 50)
        self.assertEqual(ev["payload"]["trigger_kind"], "POST_FINALIZE")
        self.assertNotIn("POST_FINALIZE", KNOWN_BOUNDARY_TRIGGERS)

    def test_23_stable_decision_event_ids(self) -> None:
        event = self._event()
        key = derive_post_finalize_decision_key(
            character_id=CHAR,
            actual_event_id=event["event_id"],
            actual_end=event["actual_end"],
        )
        expected_key = stable_id(
            "post-finalize-decision",
            CHAR,
            event["event_id"],
            event["actual_end"],
        )
        self.assertEqual(key, expected_key)
        ev = build_post_finalize_decision_wakeup(
            character_id=CHAR, actual_event=event
        )
        self.assertEqual(ev["payload"]["decision_key"], key)
        self.assertEqual(
            ev["event_id"],
            derive_managed_v2_decision_wakeup_event_id(
                character_id=CHAR,
                decision_key=key,
                due_at=event["actual_end"],
            ),
        )

    def test_24_source_ref_equals_finalized_event(self) -> None:
        event = self._event()
        ev = build_post_finalize_decision_wakeup(
            character_id=CHAR, actual_event=event
        )
        self.assertEqual(ev["payload"]["source_kind"], "ACTUAL_EVENT")
        self.assertEqual(ev["payload"]["source_ref"], event["event_id"])
        self.assertEqual(ev["payload"]["reason_code"], "POST_FINALIZE_FRESH_DECISION")
        self.assertIsNone(ev["payload"]["metric"])
        self.assertEqual(ev["due_at"], event["actual_end"])

    def test_25_exact_replay_idempotent_shape(self) -> None:
        a = build_post_finalize_decision_wakeup(
            character_id=CHAR, actual_event=self._event()
        )
        b = build_post_finalize_decision_wakeup(
            character_id=CHAR, actual_event=self._event()
        )
        self.assertEqual(a["event_id"], b["event_id"])
        self.assertEqual(a["payload"]["decision_key"], b["payload"]["decision_key"])

    def test_26_conflicting_managed_event_reject(self) -> None:
        # Conflicting enqueue: same event_id different payload fails closed.
        from engine.life.runtime_orchestrator import _enqueue_queue_event
        from tests.support.builders.runtime_orchestrator import _no_active_bundle
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            # Empty managed queue (post-C2B cleanup shape).
            bundle = _no_active_bundle(tmp, queue_events=[])
            ev = build_post_finalize_decision_wakeup(
                character_id=CHAR, actual_event=self._event()
            )
            current = _enqueue_queue_event(bundle.current_state, ev)
            conflict = {
                **ev,
                "payload": {**ev["payload"], "reason_code": "OTHER"},
            }
            with self.assertRaises(LifeEngineError) as ctx:
                _enqueue_queue_event(current, conflict)
            self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

            # Different managed event while one already present also fails.
            other = build_post_finalize_decision_wakeup(
                character_id=CHAR,
                actual_event={
                    "event_id": "evt-finalize-2",
                    "actual_end": AS_OF,
                },
            )
            with self.assertRaises(LifeEngineError):
                _enqueue_queue_event(current, other)


class RuntimeOrchestratorPostFinalizeRuntimeTests(unittest.TestCase):
    def test_27_exact_end_finalize_post_finalize_due_now(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                _active,
                _end_event,
                end,
                pf,
                social,
                inputs,
                _cand,
                instance_id,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            snap = _bundle_snapshot(bundle)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(len(result.actual_events), 1)
            self.assertIn(pf["event_id"], result.consumed_trigger_ids)
            active = result.bundle.current_state["context"]["active_activity"]
            self.assertEqual(active["activity_instance_id"], instance_id)
            self.assertEqual(_bundle_snapshot(bundle), snap)

    def test_28_decision_switch_finalize_post_finalize_due_now(self) -> None:
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
            self.assertIn(wakeup["event_id"], result.consumed_trigger_ids)
            self.assertIn(pf["event_id"], result.consumed_trigger_ids)
            new_active = result.bundle.current_state["context"]["active_activity"]
            self.assertEqual(new_active["activity_instance_id"], iid)
            self.assertNotEqual(new_active["activity_instance_id"], active["activity_instance_id"])

    def test_29_fresh_frame_built_against_post_event_bundle(self) -> None:
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
            self.assertEqual(len(result.decision_frames), 1)
            frame = result.decision_frames[0]
            self.assertEqual(frame.trigger.trigger_id, pf["event_id"])
            self.assertEqual(frame.trigger.trigger_kind, "POST_FINALIZE")
            # History advanced once for the finalized event before the fresh frame.
            self.assertEqual(result.bundle.current_state["history"]["event_count"], 1)

    def test_30_stale_pre_finalization_candidate_never_starts(self) -> None:
        import inspect

        from engine.life import runtime_orchestrator as mod

        src = inspect.getsource(mod._advance_runtime_to_target_core)
        # Active SELECT path must finalize/discard — never C1-start the selection.
        self.assertIn("Active SELECT: discard-only", src)
        self.assertIn("materialization_facts == null", src)
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
            # New activity is from POST_FINALIZE start, not the switch selected candidate.
            new_active = result.bundle.current_state["context"]["active_activity"]
            self.assertEqual(new_active["activity_instance_id"], iid)
            # Switch decision frame should not equal the started instance's decision.
            switch_frames = [
                f
                for f in result.decision_frames
                if f.trigger.trigger_id == wakeup["event_id"]
            ]
            self.assertEqual(len(switch_frames), 1)
            switch_cand = switch_frames[0].selected_candidate
            assert switch_cand is not None
            # Started activity comes from post-finalize candidate, not switch selection.
            self.assertNotEqual(
                new_active["runtime_context"]["selected_candidate_id"],
                switch_cand.candidate_id,
            )

    def test_review_forged_due_now_post_finalize_rejected_before_start(self) -> None:
        """Blocker: input POST_FINALIZE must not become start authority."""
        from tests.support.builders.runtime_orchestrator import (
            _decision_facts_select,
            _decision_step,
            _household_mat_facts,
            _no_active_bundle,
            _predict_select,
            _social_facts,
            _social_state,
            _target_inputs,
            _wakeup_step,
        )

        with tempfile.TemporaryDirectory() as tmp:
            forged = build_post_finalize_decision_wakeup(
                character_id=CHAR,
                actual_event={
                    "event_id": "forged-actual-event",
                    "actual_end": AS_OF,
                },
            )
            bundle = _no_active_bundle(tmp, queue_events=[forged])
            social = _social_state()
            df = _decision_facts_select()
            sf = _social_facts()
            _result, cand, instance_id = _predict_select(
                bundle, forged, decision_facts=df, social_facts=sf, social_state=social
            )
            inputs = _target_inputs(
                target_time=AS_OF,
                event_budget=20,
                decision_steps=[
                    _decision_step(
                        forged["event_id"],
                        decision_facts=df,
                        social_facts=sf,
                        materialization_facts=_household_mat_facts(cand.candidate_id),
                    )
                ],
                wakeup_steps=[_wakeup_step(instance_id, as_of=AS_OF)],
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=social,
                    target_inputs=inputs,
                )
            self.assertIn("C3-owned", str(ctx.exception))
            self.assertIsNone(
                bundle.current_state["context"]["active_activity"]
            )

    def test_review_future_post_finalize_in_input_rejected_at_entry(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _add_min,
            _no_active_bundle,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            forged = build_post_finalize_decision_wakeup(
                character_id=CHAR,
                actual_event={
                    "event_id": "forged-future-actual",
                    "actual_end": _add_min(AS_OF, 30),
                },
            )
            bundle = _no_active_bundle(tmp, queue_events=[forged])
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(
                        target_time=_add_min(AS_OF, 60),
                        event_budget=5,
                    ),
                )
            self.assertIn("C3-owned", str(ctx.exception))
