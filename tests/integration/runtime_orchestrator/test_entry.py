"""Runtime orchestrator entry, target validation, and pure-call contracts."""

from __future__ import annotations

import copy
import tempfile
import unittest

import pytest

from engine.life.canonical import canonical_json
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_orchestrator import (
    advance_runtime_to_target,
    parse_runtime_target_inputs,
)
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    POLICY,
    _add_min,
    _advance_refs,
    _social_state,
    _stable_active_bundle,
    _target_inputs,
)


pytestmark = pytest.mark.integration

class RuntimeOrchestratorEntryTests(unittest.TestCase):
    def test_01_target_before_processed_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(
                        target_time=_add_min(AS_OF, -1),
                        event_budget=0,
                    ),
                )
            self.assertEqual(ctx.exception.code, ErrorCode.TIME_REVERSAL)

    def test_02_target_equals_processed_stable_no_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            before = canonical_json(dict(bundle.current_state))
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
            )
            self.assertEqual(result.bundle.current_state["processed_through"], AS_OF)
            self.assertIsNotNone(
                result.bundle.current_state["context"]["active_activity"]
            )
            self.assertEqual(result.microsteps_used, 0)
            self.assertEqual(canonical_json(dict(bundle.current_state)), before)

    def test_03_policy_character_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            bad_policy = copy.deepcopy(POLICY)
            bad_policy["character_id"] = "other-character"
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=bad_policy,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(event_budget=0),
                )
            self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_VERSION)

    def test_04_social_state_character_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            from engine.life.social_runtime import build_social_response_state

            bad = build_social_response_state(
                character_id="other-character",
                as_of=AS_OF,
            )
            with self.assertRaises(LifeEngineError):
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=bad,
                    target_inputs=_target_inputs(event_budget=0),
                )

    def test_05_malformed_target_inputs_controlled(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_runtime_target_inputs(
                {
                    "target_time": AS_OF,
                    "event_budget": 1.5,
                    "character_source_sha": "x",
                    "capture_source_moments": [],
                    "decision_steps": [],
                    "wakeup_steps": [],
                }
            )
        with self.assertRaises(LifeEngineError):
            parse_runtime_target_inputs(
                {
                    "target_time": AS_OF,
                    "event_budget": 1,
                    "character_source_sha": "x",
                    "capture_source_moments": [],
                    "decision_steps": [],
                    "wakeup_steps": [],
                    "extra": True,
                }
            )
        with self.assertRaises(LifeEngineError):
            parse_runtime_target_inputs(
                {
                    "target_time": AS_OF,
                    # missing event_budget — no default
                    "character_source_sha": "x",
                    "capture_source_moments": [],
                    "decision_steps": [],
                    "wakeup_steps": [],
                }
            )

    def test_06_input_non_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            social = _social_state()
            inputs = _target_inputs(event_budget=0)
            snap_bundle = canonical_json(dict(bundle.current_state))
            snap_social = canonical_json(social.as_dict())
            snap_inputs = canonical_json(inputs.as_dict())
            advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(canonical_json(dict(bundle.current_state)), snap_bundle)
            self.assertEqual(canonical_json(social.as_dict()), snap_social)
            self.assertEqual(canonical_json(inputs.as_dict()), snap_inputs)

    def test_07_no_wall_clock(self) -> None:
        import inspect

        from engine.life import runtime_orchestrator as mod

        src = inspect.getsource(mod)
        self.assertNotIn("datetime.now(", src)
        self.assertNotIn("time.time(", src)
        self.assertNotIn("from .clock import", src)

    def test_review_activity_end_same_id_wrong_due_rejected(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _exact_end_ready_setup,
            _load_c3_bundle,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            _bundle, active, end_event, end = _exact_end_ready_setup(tmp)
            forged = dict(end_event)
            forged["due_at"] = _add_min(end, 30)
            # Preserve forged event_id while mutating due (identity-only bypass).
            self.assertEqual(forged["event_id"], end_event["event_id"])
            self.assertNotEqual(forged["due_at"], end_event["due_at"])
            bad = _load_c3_bundle(
                tmp + "-bad-due",
                active=dict(active),
                queue_events=[forged],
                processed_through=end,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bad,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(as_of=end),
                    target_inputs=_target_inputs(target_time=end, event_budget=5),
                )
            msg = str(ctx.exception)
            self.assertTrue(
                "ACTIVITY_END" in msg or "conflicting" in msg or "canonical" in msg,
                msg,
            )

    def test_review_activity_end_same_id_wrong_priority_rejected(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _exact_end_ready_setup,
            _load_c3_bundle,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            _bundle, active, end_event, end = _exact_end_ready_setup(tmp)
            forged = dict(end_event)
            forged["priority"] = int(end_event["priority"]) + 1
            bad = _load_c3_bundle(
                tmp + "-prio",
                active=dict(active),
                queue_events=[forged],
                processed_through=end,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bad,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(as_of=end),
                    target_inputs=_target_inputs(target_time=end, event_budget=5),
                )
            msg = str(ctx.exception)
            self.assertTrue(
                "ACTIVITY_END" in msg or "conflicting" in msg or "canonical" in msg,
                msg,
            )

    def test_review_open_window_spurious_activity_end_rejected(self) -> None:
        from engine.life.activity_materialization import build_activity_end_event
        from tests.support.builders.runtime_orchestrator import (
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            active = _runtime_active()
            future = _future_wakeup(due_at=_add_min(AS_OF, 45))
            spurious = build_activity_end_event(
                activity_instance_id=active["activity_instance_id"],
                planned_end=_add_min(AS_OF, 30),
            )
            assert spurious is not None
            # Force ACTIVITY_END into open/window queue alongside managed wakeup.
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[future, spurious],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
                )
            self.assertIn("ACTIVITY_END", str(ctx.exception))

    def test_review_correct_c1_activity_end_passes_and_consumes_at_target(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _exact_end_with_post_finalize_start_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                active,
                _e,
                end,
                _pf,
                social,
                inputs,
                _c,
                iid,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(result.bundle.current_state["processed_through"], end)
            self.assertEqual(len(result.actual_events), 1)
            new_active = result.bundle.current_state["context"]["active_activity"]
            self.assertEqual(new_active["activity_instance_id"], iid)
            self.assertNotEqual(
                new_active["activity_instance_id"], active["activity_instance_id"]
            )

    def test_review_future_managed_wrong_event_id_rejected(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            active = _runtime_active()
            future = _future_wakeup(due_at=_add_min(AS_OF, 45))
            forged = dict(future)
            forged["event_id"] = "forged-wrong-managed-id"
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[forged],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
                )
            self.assertIn("event_id", str(ctx.exception))

    def test_review_managed_priority_not_50_rejected(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            active = _runtime_active()
            future = _future_wakeup(due_at=_add_min(AS_OF, 45))
            forged = dict(future)
            forged["priority"] = 9
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[forged],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
                )
            self.assertIn("priority", str(ctx.exception))

    def test_review_priority_below_activity_end_cannot_reorder(self) -> None:
        """Forged managed priority < ACTIVITY_END must reject, not reorder head."""
        from engine.life.activity_materialization import build_activity_end_event
        from engine.life.invariants import queue_event_sort_key
        from tests.support.builders.runtime_orchestrator import (
            _exact_end_ready_setup,
            _load_c3_bundle,
            _social_state,
            _target_inputs,
            _v2_wakeup,
        )

        with tempfile.TemporaryDirectory() as tmp:
            _bundle, active, end_event, end = _exact_end_ready_setup(tmp)
            wakeup = _v2_wakeup(due_at=end, decision_key="idle:reorder")
            forged = dict(wakeup)
            forged["priority"] = 5
            # Canonical sort would put forged before ACTIVITY_END if accepted.
            ordered = sorted([end_event, forged], key=queue_event_sort_key)
            self.assertEqual(ordered[0]["event_id"], forged["event_id"])
            bad = _load_c3_bundle(
                tmp + "-reorder",
                active=dict(active),
                queue_events=[end_event, forged],
                processed_through=end,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bad,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(as_of=end),
                    target_inputs=_target_inputs(target_time=end, event_budget=5),
                )
            self.assertIn("priority", str(ctx.exception))

    def test_review_processed_before_earliest_wakeup_after_earliest_rejected(
        self,
    ) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            earliest = _add_min(AS_OF, 30)
            latest = _add_min(AS_OF, 90)
            active = _runtime_active(
                expected_end_window={"earliest": earliest, "latest": latest}
            )
            # processed < earliest, but wakeup after earliest (still <= latest).
            wakeup = _future_wakeup(due_at=_add_min(AS_OF, 45))
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[wakeup],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
                )
            self.assertIn("earliest", str(ctx.exception))

    def test_review_wakeup_after_latest_rejected(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            earliest = _add_min(AS_OF, 30)
            latest = _add_min(AS_OF, 90)
            active = _runtime_active(
                expected_end_window={"earliest": earliest, "latest": latest}
            )
            wakeup = _future_wakeup(due_at=_add_min(AS_OF, 120))
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[wakeup],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
                )
            self.assertIn("latest", str(ctx.exception))

    def test_review_processed_eq_earliest_later_wakeup_le_latest_passes(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            earliest = AS_OF
            latest = _add_min(AS_OF, 90)
            active = _runtime_active(
                actual_start=_add_min(AS_OF, -30),
                expected_end_window={"earliest": earliest, "latest": latest},
            )
            # processed == earliest: one-shot earliest may already be consumed;
            # later wakeup <= latest is allowed.
            wakeup = _future_wakeup(due_at=_add_min(AS_OF, 45))
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[wakeup],
                processed_through=AS_OF,
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
            )
            self.assertEqual(result.microsteps_used, 0)
            self.assertIsNotNone(
                result.bundle.current_state["context"]["active_activity"]
            )

    def test_review_target_eq_latest_cannot_stable_return_old_active(self) -> None:
        from tests.support.builders.runtime_orchestrator import (
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
            _target_inputs,
        )

        with tempfile.TemporaryDirectory() as tmp:
            earliest = _add_min(AS_OF, -30)
            latest = AS_OF
            active = _runtime_active(
                actual_start=_add_min(AS_OF, -60),
                expected_end_window={"earliest": earliest, "latest": latest},
            )
            # Wakeup after latest would allow deadlocked stable-return at latest.
            wakeup = _future_wakeup(due_at=_add_min(AS_OF, 30))
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[wakeup],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(target_time=AS_OF, event_budget=0),
                )
            self.assertIn("latest", str(ctx.exception))

    def test_review_valid_c2a_produced_queue_unchanged_pass(self) -> None:
        """Canonical stable open/window queue from fixtures remains green."""
        with tempfile.TemporaryDirectory() as tmp:
            from tests.support.builders.runtime_orchestrator import _stable_active_bundle, _social_state

            bundle = _stable_active_bundle(tmp)
            before = canonical_json(
                list(bundle.current_state["pending_queue"]["events"])
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            after = canonical_json(
                list(result.bundle.current_state["pending_queue"]["events"])
            )
            self.assertEqual(before, after)
            self.assertEqual(result.microsteps_used, 0)
