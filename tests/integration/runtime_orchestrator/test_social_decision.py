"""Social-aware decision composition inside target-time orchestration."""

from __future__ import annotations

import inspect
import tempfile
import unittest

import pytest

from engine.life.errors import LifeEngineError
from engine.life.runtime_orchestrator import advance_runtime_to_target
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    POLICY,
    _advance_refs,
    _continue_setup,
    _decision_facts_continue,
    _decision_step,
    _no_active_start_setup,
    _social_facts,
    _social_state,
    _target_inputs,
    _wakeup_step,
)


pytestmark = pytest.mark.integration

class RuntimeOrchestratorDecisionSocialTests(unittest.TestCase):
    def test_58_exact_decision_input_required_by_trigger_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            self.assertEqual(inputs.decision_steps[0].trigger_id, wakeup["event_id"])
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertIn(wakeup["event_id"], result.consumed_trigger_ids)

    def test_59_missing_decision_input_reject(self) -> None:
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
                        decision_steps=[],
                        wakeup_steps=[
                            _wakeup_step(active["activity_instance_id"], as_of=AS_OF)
                        ],
                    ),
                )
            self.assertIn("missing", str(ctx.exception).lower())

    def test_60_social_aware_builder_used_once_per_decision(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertEqual(src.count("build_runtime_social_decision("), 1)

    def test_61_no_second_resolver(self) -> None:
        src = inspect.getsource(
            __import__("engine.life.runtime_orchestrator", fromlist=["x"])
        )
        self.assertNotIn("resolve_decision(", src)
        self.assertNotIn("build_runtime_decision_frame(", src)

    def test_62_continuation_hard_guard_applied(self) -> None:
        src = inspect.getsource(
            __import__("engine.life.runtime_orchestrator", fromlist=["x"])
        )
        self.assertIn("apply_active_window_continuation_guard", src)

    def test_63_continue_pending_immediate_accept_forbidden(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("CONTINUE_CURRENT forbids pending_immediate_accept", src)

    def test_64_invite_schedule_mutation_carried_through_continue(self) -> None:
        # CONTINUE path uses social working bundle from build_runtime_social_decision.
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("social_result.working_bundle", src)
        self.assertIn("apply_continue_current", src)

    def test_65_decline_defer_state_carried(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("social_working = social_result.social_response_state", src)

    def test_66_active_selected_immediate_accept_discarded_on_switch(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("Provisional immediate ACCEPT discarded", src)

    def test_67_no_active_selected_immediate_accept_terminalizes_after_start(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("pending_immediate_accept=social_result.pending_immediate_accept", src)
        with tempfile.TemporaryDirectory() as tmp:
            bundle, wakeup, social, inputs, cand, iid = _no_active_start_setup(tmp)
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertIsNotNone(
                result.bundle.current_state["context"]["active_activity"]
            )
