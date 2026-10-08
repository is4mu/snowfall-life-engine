"""Runtime orchestrator no-active fail-closed and start recovery tests."""

from __future__ import annotations

import tempfile
import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_orchestrator import advance_runtime_to_target
from tests.support.builders.finalization import _v2_wakeup
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    POLICY,
    _add_min,
    _advance_refs,
    _no_active_bundle,
    _no_active_start_setup,
    _social_state,
    _stable_active_bundle,
    _target_inputs,
)


pytestmark = pytest.mark.integration

class RuntimeOrchestratorNoActiveTests(unittest.TestCase):
    def test_08_no_active_no_due_now_decision_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _no_active_bundle(tmp, queue_events=[])
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(
                        target_time=_add_min(AS_OF, 30),
                        event_budget=10,
                    ),
                )
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_09_no_active_future_only_decision_cannot_advance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            future = _v2_wakeup(due_at=_add_min(AS_OF, 30), decision_key="idle:later")
            bundle = _no_active_bundle(tmp, queue_events=[future])
            with self.assertRaises(LifeEngineError):
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(
                        target_time=_add_min(AS_OF, 60),
                        event_budget=10,
                    ),
                )

    def test_10_no_active_due_now_v2_decision_can_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, wakeup, social, inputs, cand, instance_id = (
                _no_active_start_setup(tmp)
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            active = result.bundle.current_state["context"]["active_activity"]
            self.assertIsNotNone(active)
            self.assertEqual(active["activity_instance_id"], instance_id)
            self.assertEqual(active["activity_type"], "HOUSEHOLD")
            self.assertEqual(result.bundle.current_state["processed_through"], AS_OF)
            self.assertIn(wakeup["event_id"], result.consumed_trigger_ids)

    def test_11_no_rest_idle_activity_synthesis(self) -> None:
        import inspect

        from engine.life import runtime_orchestrator as mod

        src = inspect.getsource(mod)
        self.assertNotIn("REST_AWAKE", src)
        self.assertNotIn("default_idle", src)
        self.assertNotIn("synthesize_idle", src)

    def test_12_successful_return_never_no_active(self) -> None:
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
