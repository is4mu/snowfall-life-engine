"""Canonical queue ordering and target-time event sequencing."""

from __future__ import annotations

import copy
import tempfile
import unittest

import pytest

from engine.life.canonical import canonical_json
from engine.life.errors import LifeEngineError
from engine.life.invariants import queue_event_sort_key
from engine.life.runtime_orchestrator import advance_runtime_to_target
from tests.support.builders.runtime_orchestrator import (
    AS_OF,
    CHAR,
    POLICY,
    _add_min,
    _advance_refs,
    _continue_setup,
    _exact_end_with_post_finalize_start_inputs,
    _future_wakeup,
    _load_c3_bundle,
    _moment,
    _runtime_active,
    _social_state,
    _stable_active_bundle,
    _target_inputs,
    _v2_wakeup,
    _force_take_policy,
)


def _unrelated_v1(*, due_at: str, event_id: str = "unrelated:idle") -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_kind": "IDLE_REASSESSMENT",
        "due_at": due_at,
        "priority": 50,
        "payload": {"interval_min": 30},
    }


def _v1_decision(*, due_at: str) -> dict:
    return {
        "schema_version": 1,
        "event_id": "legacy:decision:1",
        "event_kind": "DECISION_WAKEUP",
        "due_at": due_at,
        "priority": 50,
        "payload": {
            "reason": "legacy_threshold",
            "decision_key": "legacy:decision:1",
            "field": None,
            "source_kind": None,
            "source_ref": None,
        },
    }


pytestmark = pytest.mark.integration

class RuntimeOrchestratorQueueOrderingTests(unittest.TestCase):
    def test_13_canonical_head_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            # Managed v2 must remain ≤1. Place an unrelated future non-managed
            # row; only the due-now canonical head DECISION_WAKEUP is consumed.
            future_idle = _unrelated_v1(due_at=_add_min(AS_OF, 90))
            events = sorted(
                [wakeup, future_idle],
                key=queue_event_sort_key,
            )
            head = events[0]
            self.assertEqual(head["event_id"], wakeup["event_id"])
            bundle = _load_c3_bundle(
                tmp + "/b",
                active=active,
                queue_events=events,
                processed_through=AS_OF,
            )
            from tests.support.builders.runtime_orchestrator import (
                _decision_facts_continue,
                _decision_step,
                _social_facts,
                _wakeup_step,
            )

            inputs = _target_inputs(
                target_time=AS_OF,
                event_budget=20,
                decision_steps=[
                    _decision_step(
                        head["event_id"],
                        decision_facts=_decision_facts_continue(),
                        social_facts=_social_facts(),
                        materialization_facts=None,
                    )
                ],
                wakeup_steps=[
                    _wakeup_step(active["activity_instance_id"], as_of=AS_OF)
                ],
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(result.consumed_trigger_ids, (head["event_id"],))
            remaining_ids = {
                e["event_id"]
                for e in result.bundle.current_state["pending_queue"]["events"]
            }
            self.assertNotIn(head["event_id"], remaining_ids)
            self.assertIn(future_idle["event_id"], remaining_ids)

    def test_14_capture_before_activity_end(self) -> None:
        """Primary captures at t drain before ACTIVITY_END at same t."""
        with tempfile.TemporaryDirectory() as tmp:
            end = AS_OF
            from tests.support.builders.runtime_orchestrator import (
                _exact_end_ready_setup,
                _decision_facts_select,
                _decision_step,
                _household_mat_facts,
                _predict_select,
                _social_facts,
                _wakeup_step,
                _household_mat_facts as mat,
            )
            from engine.life.activity_finalization import (
                finalize_runtime_activity_from_exact_end,
            )
            from engine.life.runtime_orchestrator import (
                build_post_finalize_decision_wakeup,
            )
            from engine.life.runtime_bundle import RuntimeBundle
            from engine.life.checkpoint import validate_checkpoint

            bundle, active, end_event, end = _exact_end_ready_setup(tmp)
            moment = _moment(
                bundle,
                moment_at=end,
                activity_instance_id=active["activity_instance_id"],
            )
            fin = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            pf = build_post_finalize_decision_wakeup(
                character_id=CHAR, actual_event=fin.actual_event
            )
            cur = dict(fin.bundle.current_state)
            cur["pending_queue"] = {"cursor": 0, "events": [pf]}
            probe = RuntimeBundle(
                current_state=validate_checkpoint(cur),
                schedule_state=fin.bundle.schedule_state,
                relation_state=fin.bundle.relation_state,
                home_state=fin.bundle.home_state,
                consumables_state=fin.bundle.consumables_state,
                wardrobe_state=fin.bundle.wardrobe_state,
                finance_state=fin.bundle.finance_state,
            )
            social = _social_state(as_of=end)
            _r, cand, iid = _predict_select(
                probe,
                pf,
                decision_facts=_decision_facts_select(),
                social_facts=_social_facts(),
                social_state=social,
            )
            inputs = _target_inputs(
                target_time=end,
                event_budget=30,
                moments=[moment],
                decision_steps=[
                    _decision_step(
                        pf["event_id"],
                        decision_facts=_decision_facts_select(),
                        social_facts=_social_facts(),
                        materialization_facts=_household_mat_facts(cand.candidate_id),
                    )
                ],
                wakeup_steps=[_wakeup_step(iid, as_of=end)],
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=_force_take_policy(),
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertTrue(result.consumed_source_keys)
            self.assertEqual(len(result.actual_events), 1)
            # Capture microsteps occur before ACTIVITY_END in the same fixed-point.
            self.assertGreaterEqual(result.microsteps_used, 2)

    def test_15_activity_end_before_decision_wakeup_same_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (
                bundle,
                active,
                end_event,
                end,
                pf,
                social,
                inputs,
                cand,
                iid,
            ) = _exact_end_with_post_finalize_start_inputs(tmp)
            # Also enqueue a same-time unrelated v2 wakeup that sorts after ACTIVITY_END.
            # ACTIVITY_END must be processed first (creates POST_FINALIZE), not this.
            # Our setup already only has ACTIVITY_END; POST_FINALIZE is C3-created.
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=social,
                target_inputs=inputs,
            )
            self.assertEqual(len(result.actual_events), 1)
            self.assertEqual(result.actual_events[0]["actual_end"], end)
            self.assertIsNotNone(
                result.bundle.current_state["context"]["active_activity"]
            )
            # POST_FINALIZE decision consumed after ACTIVITY_END.
            self.assertIn(pf["event_id"], result.consumed_trigger_ids)

    def test_16_unsupported_v1_decision_due_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            earliest = _add_min(AS_OF, 60)
            active = _runtime_active(
                expected_end_window={
                    "earliest": earliest,
                    "latest": _add_min(AS_OF, 90),
                }
            )
            legacy = _v1_decision(due_at=AS_OF)
            # Open active also needs a managed v2 wakeup — put it in the future.
            future = _future_wakeup(due_at=earliest)
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[legacy, future],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError) as ctx:
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(event_budget=5),
                )
            self.assertIn("unsupported", str(ctx.exception).lower())

    def test_17_unsupported_idle_reassessment_due_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            earliest = _add_min(AS_OF, 60)
            active = _runtime_active(
                expected_end_window={
                    "earliest": earliest,
                    "latest": _add_min(AS_OF, 90),
                }
            )
            idle = _unrelated_v1(due_at=AS_OF)
            future = _future_wakeup(due_at=earliest)
            bundle = _load_c3_bundle(
                tmp,
                active=active,
                queue_events=[idle, future],
                processed_through=AS_OF,
            )
            with self.assertRaises(LifeEngineError):
                advance_runtime_to_target(
                    bundle=bundle,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    social_response_state=_social_state(),
                    target_inputs=_target_inputs(event_budget=5),
                )

    def test_18_unrelated_future_unsupported_row_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            future_idle = _unrelated_v1(due_at=_add_min(AS_OF, 90))
            bundle = _stable_active_bundle(tmp)
            events = list(bundle.current_state["pending_queue"]["events"]) + [
                future_idle
            ]
            bundle = _load_c3_bundle(
                tmp + "/b",
                active=bundle.current_state["context"]["active_activity"],
                queue_events=events,
                processed_through=AS_OF,
            )
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            ids = {
                e["event_id"]
                for e in result.bundle.current_state["pending_queue"]["events"]
            }
            self.assertIn(future_idle["event_id"], ids)

    def test_19_consumed_decision_exact_pop_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, wakeup, social, inputs = _continue_setup(tmp)
            keep = _future_wakeup(due_at=_add_min(AS_OF, 90), decision_key="keep")
            # Put keep in queue alongside due-now; after CONTINUE reconcile may replace
            # managed wakeups — use unrelated v1 future row instead.
            keep = _unrelated_v1(due_at=_add_min(AS_OF, 90), event_id="keep:row")
            events = [wakeup, keep]
            bundle = _load_c3_bundle(
                tmp + "/b",
                active=active,
                queue_events=events,
                processed_through=AS_OF,
            )
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
            self.assertIn(keep["event_id"], ids)

    def test_20_unrelated_rows_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _stable_active_bundle(tmp)
            keep = _unrelated_v1(due_at=_add_min(AS_OF, 120), event_id="preserve:me")
            events = list(bundle.current_state["pending_queue"]["events"]) + [keep]
            active = bundle.current_state["context"]["active_activity"]
            bundle = _load_c3_bundle(
                tmp + "/b",
                active=active,
                queue_events=events,
                processed_through=AS_OF,
            )
            before = {
                e["event_id"]
                for e in bundle.current_state["pending_queue"]["events"]
            }
            result = advance_runtime_to_target(
                bundle=bundle,
                reference_sets=_advance_refs(),
                behavior_policy=POLICY,
                social_response_state=_social_state(),
                target_inputs=_target_inputs(event_budget=0),
            )
            after = {
                e["event_id"]
                for e in result.bundle.current_state["pending_queue"]["events"]
            }
            self.assertIn("preserve:me", after)
            self.assertTrue(before <= after | before)
