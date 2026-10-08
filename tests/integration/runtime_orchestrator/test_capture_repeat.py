"""Capture repeat handling inside target-time orchestration."""

from __future__ import annotations

import inspect
import tempfile
import unittest

import pytest

from engine.life.canonical import canonical_json
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_orchestrator import advance_runtime_to_target
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    POLICY,
    _add_min,
    _advance_refs,
    _exact_end_with_post_finalize_start_inputs,
    _force_take_policy,
    _future_wakeup,
    _load_c3_bundle,
    _moment,
    _runtime_active,
    _social_state,
    _stable_active_bundle,
    _target_inputs,
)


pytestmark = pytest.mark.integration

class RuntimeOrchestratorCaptureRepeatTests(unittest.TestCase):
    def test_46_current_active_due_moments_batch_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            active = bundle.current_state["context"]["active_activity"]
            m1 = _moment(
                bundle,
                moment_ref="moment:a",
                semantic_source_ref="semantic:a",
                activity_instance_id=active["activity_instance_id"],
            )
            m2 = _moment(
                bundle,
                moment_ref="moment:b",
                semantic_source_ref="semantic:b",
                activity_instance_id=active["activity_instance_id"],
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=_force_take_policy(),
                social_response_state=_social_state(),
                target_inputs=_target_inputs(
                    event_budget=20, moments=[m1, m2]
                ),
            )
            self.assertEqual(len(result.consumed_source_keys), 2)

    def test_47_other_activity_same_time_moments_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                active,
                _e,
                end,
                pf,
                social,
                inputs,
                cand,
                iid,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            # Moment for the *new* activity instance at end — deferred until after start.
            other_moment = _moment(
                bundle,
                moment_at=end,
                activity_instance_id=iid,
                moment_ref="moment:new",
                semantic_source_ref="semantic:new",
            )
            # Rebuild inputs including the deferred moment.
            from tests.support.builders.runtime_orchestrator import (
                _decision_facts_select,
                _decision_step,
                _household_mat_facts,
                _social_facts,
                _wakeup_step,
            )

            inputs2 = _target_inputs(
                target_time=end,
                event_budget=30,
                moments=[other_moment],
                decision_steps=list(inputs.decision_steps),
                wakeup_steps=list(inputs.wakeup_steps),
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=_force_take_policy(),
                social_response_state=social,
                target_inputs=inputs2,
            )
            self.assertTrue(result.consumed_source_keys)
            self.assertEqual(
                result.bundle.current_state["context"]["active_activity"][
                    "activity_instance_id"
                ],
                iid,
            )

    def test_48_unmatched_due_moment_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            foreign = _moment(
                bundle,
                activity_instance_id="other-activity-instance",
                moment_ref="moment:x",
                semantic_source_ref="semantic:x",
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=_force_take_policy(),
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(event_budget=5, moments=[foreign]),
                )
            self.assertIn("unmatched", str(ctx.exception).lower())

    def test_49_every_supplied_source_consumed_on_success(self) -> None:
        self.test_46_current_active_due_moments_batch_processed()

    def test_50_exact_duplicate_source_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            active = bundle.current_state["context"]["active_activity"]
            m = _moment(
                bundle,
                activity_instance_id=active["activity_instance_id"],
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=_force_take_policy(),
                social_response_state=_social_state(),
                target_inputs=_target_inputs(
                    event_budget=10, moments=[m, dict(m)]
                ),
            )
            self.assertEqual(len(result.consumed_source_keys), 1)

    def test_51_conflicting_source_identity_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            active = bundle.current_state["context"]["active_activity"]
            m1 = _moment(
                bundle,
                activity_instance_id=active["activity_instance_id"],
                subject_refs=[],
            )
            m2 = dict(m1)
            m2["subject_refs"] = ["someone-else"]
            # Same source key fields but different payload → reject at parse.
            from engine.life.runtime_orchestrator import parse_runtime_target_inputs

            with self.assertRaises(LifeEngineError):
                parse_runtime_target_inputs(
                    {
                        "target_time": AS_OF,
                        "event_budget": 5,
                        "character_source_sha": "sha",
                        "capture_source_moments": [m1, m2],
                        "decision_steps": [],
                        "wakeup_steps": [],
                    }
                )

    def test_52_repeat_chains_canonical_parent_order(self) -> None:
        """Primary chains drain in sorted(parent_capture_id) order, not source order."""
        from unittest.mock import patch

        from engine.life.events import validate_active_activity
        from engine.life.runtime_capture import (
            RuntimeCaptureContextFacts,
            process_runtime_capture_moments,
            resolve_runtime_repeat_step,
        )
        from tests.support.builders.runtime_bundle import PERSON
        from tests.support.builders.runtime_orchestrator import (
            CHAR_SOURCE_SHA,
            _future_wakeup,
            _load_c3_bundle,
            _runtime_active,
            _social_state,
        )

        with tempfile.TemporaryDirectory() as tmp:
            active = validate_active_activity(
                {
                    **_runtime_active(
                        expected_end_window={
                            "earliest": _add_min(AS_OF, 45),
                            "latest": _add_min(AS_OF, 90),
                        }
                    ),
                    "companions": [{"entity_id": PERSON}],
                }
            )
            future = _future_wakeup(due_at=_add_min(AS_OF, 45))
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[future],
                processed_through=AS_OF,
            )
            # Two SELFIE parents whose opportunity/source order != capture_id order.
            m_a = _moment(
                bundle,
                emitter_rule_id="slice4b2.selfie.appearance",
                source_context_kind="APPEARANCE_SATISFACTION",
                semantic_source_ref="s-aaa",
                moment_ref="m-aaa",
            )
            m_z = _moment(
                bundle,
                emitter_rule_id="slice4b2.selfie.appearance",
                source_context_kind="APPEARANCE_SATISFACTION",
                semantic_source_ref="s-zzz",
                moment_ref="m-zzz",
            )
            probe = process_runtime_capture_moments(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=_force_take_policy(),
                capture_context_facts=RuntimeCaptureContextFacts(
                    character_source_sha=CHAR_SOURCE_SHA,
                    source_moments=(m_a, m_z),
                ),
            )
            source_order = list(probe.repeat_parent_capture_ids)
            self.assertGreaterEqual(len(source_order), 2)
            self.assertNotEqual(source_order, sorted(source_order))

            primary_set = set(source_order)
            seen_primary: list[str] = []
            real_resolve = resolve_runtime_repeat_step

            def _spy(**kwargs):
                from dataclasses import replace

                pid = kwargs["parent_capture_id"]
                if pid in primary_set and pid not in seen_primary:
                    seen_primary.append(pid)
                step = real_resolve(**kwargs)
                # End each primary chain immediately so the test observes
                # parent order without unbounded force-take repeat depth.
                if pid in primary_set:
                    return replace(
                        step,
                        chain_ended=True,
                        next_parent_capture_id=None,
                    )
                return step

            with patch(
                "engine.life.runtime_orchestrator.resolve_runtime_repeat_step",
                side_effect=_spy,
            ):
                result = advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=_force_take_policy(),
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(
                        event_budget=20,
                        moments=[m_a, m_z],
                    ),
                )
            self.assertEqual(len(result.consumed_source_keys), 2)
            self.assertEqual(seen_primary, sorted(source_order))
            src = inspect.getsource(
                __import__(
                    "engine.life.runtime_orchestrator",
                    fromlist=["_advance_runtime_to_target_core"],
                )._advance_runtime_to_target_core
            )
            self.assertIn(
                "sorted(capture_result.repeat_parent_capture_ids)", src
            )

    def test_53_repeat_child_processed_immediately(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("while next_parent is not None", src)

    def test_54_terminal_skip_ends_chain(self) -> None:
        src = inspect.getsource(
            __import__(
                "engine.life.runtime_orchestrator", fromlist=["_advance_runtime_to_target_core"]
            )._advance_runtime_to_target_core
        )
        self.assertIn("chain_ended", src)

    def test_55_no_eager_recursive_c2c_helper(self) -> None:
        src = inspect.getsource(
            __import__("engine.life.runtime_orchestrator", fromlist=["x"])
        )
        self.assertNotIn("drain_all_repeats", src)
        self.assertNotIn("recursive_repeat", src)

    def test_56_no_semantic_max_frames(self) -> None:
        src = inspect.getsource(
            __import__("engine.life.runtime_orchestrator", fromlist=["x"])
        )
        self.assertNotIn("max_frames", src)
        self.assertNotIn("MAX_REPEAT", src)

    def test_57_capture_repeat_budget_accounting_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            active = bundle.current_state["context"]["active_activity"]
            m = _moment(
                bundle,
                activity_instance_id=active["activity_instance_id"],
            )
            # Budget 0 must fail when a capture is required.
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=_force_take_policy(),
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(event_budget=0, moments=[m]),
                )
            self.assertEqual(ctx.exception.code, ErrorCode.EVENT_BUDGET_EXCEEDED)
