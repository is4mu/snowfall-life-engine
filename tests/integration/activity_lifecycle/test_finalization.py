"""Activity finalization, completion boundary, queue, and context-hash integration tests."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_finalization import (
    finalize_runtime_activity_from_decision_switch,
    finalize_runtime_activity_from_exact_end,
    runtime_activity_reached_completion_boundary,
)
from engine.life.activity_materialization import build_activity_end_event
from engine.life.canonical import canonical_json
from engine.life.errors import LifeEngineError
from engine.life.events import validate_active_activity
from engine.life.fixed_point import empty_rate_remainders
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.runtime_decision import (
    build_decision_facts_hash,
    build_materialization_context_hash,
    parse_runtime_decision_facts,
)
from engine.life.schedule_state import build_schedule_state
from engine.life.timeutil import add_minutes, format_rfc3339, parse_rfc3339
from engine.life.wakeups import (
    FutureCandidate,
    build_v2_decision_wakeup_event,
    is_managed_v2_decision_wakeup,
)
from tests.support.builders.finalization import (
    AS_OF,
    DECISION_KEY,
    CHAR,
    HOME,
    PERSON,
    POLICY,
    POLICY_VERSION,
    _add_min,
    _assert_common_result,
    _bundle_snapshot,
    _candidate,
    _commitment,
    _exact_end_setup,
    _facts,
    _finish_ctx,
    _frame,
    _load_bundle,
    _planned_end_active,
    _refs,
    _resolution,
    _runtime_active,
    _switch_setup,
    _task,
    _unrelated_event,
    _v2_wakeup,
    build_synthetic_bundle_root,
)
from tests.support.builders.runtime_bundle import provenance as _prov

import pytest

pytestmark = pytest.mark.integration


class ActivityFinalizationExactEndTests(unittest.TestCase):
    """Cases 8–14."""

    def test_08_exact_deterministic_activity_end_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            snap = _bundle_snapshot(bundle)
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            _assert_common_result(
                self,
                result,
                finish_path="EXACT_ACTIVITY_END",
                discarded=None,
                processed=end,
                revision=11,
                history=snap["current_state"].get("history"),
                input_snap=snap,
                input_bundle=bundle,
            )
            self.assertTrue(result.reached_completion_boundary)

    def test_09_due_at_equals_processed_equals_planned_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            end = _add_min(AS_OF, 45)
            bundle, active, end_event, _ = _exact_end_setup(tmp, planned_end=end)
            self.assertEqual(end_event["due_at"], end)
            self.assertEqual(bundle.current_state["processed_through"], end)
            self.assertEqual(active["planned_end"], end)
            finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )

    def test_10_wrong_event_id_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            bad = dict(end_event)
            bad["event_id"] = "forged-activity-end-id"
            # Must still be in queue with matching id for presence check — forge after.
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_exact_end(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    activity_end_event=bad,
                )

    def test_11_wrong_instance_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            bad = dict(end_event)
            bad["payload"] = {"activity_instance_id": "other-instance"}
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_exact_end(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    activity_end_event=bad,
                )

    def test_12_early_late_exact_end_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            end = _add_min(AS_OF, 60)
            active = _planned_end_active(planned_end=end)
            end_event = build_activity_end_event(
                activity_instance_id=active["activity_instance_id"],
                planned_end=end,
            )
            assert end_event is not None
            # Early: processed before planned_end
            early_bundle = _load_bundle(
                tmp,
                active=active,
                queue_events=[end_event],
                processed_through=_add_min(AS_OF, 30),
                commitments=[_commitment(commitment_id="cmt-a", kind="CLASS")],
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_exact_end(
                    bundle=early_bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    activity_end_event=end_event,
                )
        with tempfile.TemporaryDirectory() as tmp:
            end = _add_min(AS_OF, 60)
            # Load at exact end (valid queue), then drift processed_through late.
            bundle, active, end_event, _ = _exact_end_setup(tmp, planned_end=end)
            from engine.life.runtime_bundle import RuntimeBundle

            late_state = copy.deepcopy(dict(bundle.current_state))
            late_state["processed_through"] = _add_min(AS_OF, 90)
            late_bundle = RuntimeBundle(
                current_state=late_state,
                schedule_state=bundle.schedule_state,
                relation_state=bundle.relation_state,
                home_state=bundle.home_state,
                consumables_state=bundle.consumables_state,
                wardrobe_state=bundle.wardrobe_state,
                finance_state=bundle.finance_state,
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_exact_end(
                    bundle=late_bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    activity_end_event=end_event,
                )

    def test_13_exact_path_no_resolver(self) -> None:
        import inspect

        import engine.life.activity_finalization as mod

        src = inspect.getsource(mod.finalize_runtime_activity_from_exact_end)
        self.assertNotIn("build_runtime_decision_frame", src)
        self.assertNotIn("resolve_decision", src)
        self.assertNotIn("SELECT_CANDIDATE", src)

    def test_14_consumed_activity_end_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            eid = end_event["event_id"]
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            ids = [e["event_id"] for e in result.bundle.current_state["pending_queue"]["events"]]
            self.assertNotIn(eid, ids)
            for e in result.bundle.current_state["pending_queue"]["events"]:
                if e.get("event_kind") == "ACTIVITY_END":
                    self.assertNotEqual(
                        (e.get("payload") or {}).get("activity_instance_id"),
                        active["activity_instance_id"],
                    )


class ActivityFinalizationDecisionSwitchTests(unittest.TestCase):
    """Cases 15–24."""

    def test_15_select_candidate_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            self.assertEqual(frame.resolution.result_kind, "SELECT_CANDIDATE")
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            self.assertEqual(result.finish_path, "DECISION_SWITCH")
            self.assertTrue(result.post_finalize_decision_required)

    def test_16_continue_current_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            cont = _candidate(
                candidate_key="cont",
                action_kind="CONTINUE_CURRENT",
                priority_tier="ROUTINE_HABIT",
                source_kind="CURRENT_ACTIVITY",
                source_ref=act["activity_instance_id"],
                rule_ids=("rule.cont",),
            )
            from engine.life.runtime_decision import RuntimeDecisionTrigger

            trigger = RuntimeDecisionTrigger(
                trigger_id="trig-cont",
                occurred_at=at,
                trigger_kind="IDLE",
                semantic_decision_key="idle:switch",
                reason_code="IDLE_REASSESSMENT",
                metric=None,
                target_band=None,
                direction=None,
                source_kind=None,
                source_ref=None,
            )
            res = _resolution(
                cont,
                result_kind="CONTINUE_CURRENT",
                selected_candidate_id=None,
                selected_candidate_key=None,
                selected_action_kind=None,
                selected_priority_tier=None,
            )
            bad_frame = _frame(
                cont,
                bundle=bundle,
                decision_facts=facts,
                trigger=trigger,
                resolution=res,
                selected_candidate=None,
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=bad_frame,
                    decision_facts=facts,
                )

    def test_17_trigger_time_equals_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            from engine.life.runtime_decision import RuntimeDecisionTrigger

            trigger = RuntimeDecisionTrigger(
                trigger_id="trig-bad",
                occurred_at=_add_min(at, 5),
                trigger_kind="IDLE",
                semantic_decision_key="idle:switch",
                reason_code="IDLE_REASSESSMENT",
                metric=None,
                target_band=None,
                direction=None,
                source_kind=None,
                source_ref=None,
            )
            bad = _frame(cand, bundle=bundle, decision_facts=facts, trigger=trigger)
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=bad,
                    decision_facts=facts,
                )

    def test_18_decision_facts_hash_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            drifted = dict(facts)
            drifted["available_window_min"] = 999
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=drifted,
                )

    def test_19_materialization_context_hash_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            bad = _frame(
                cand,
                bundle=bundle,
                decision_facts=facts,
                materialization_context_hash="d" * 64,
                trigger=frame.trigger,
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=bad,
                    decision_facts=facts,
                )

    def test_20_stale_policy_schedule_location_facts_drift_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            # Location drift after frame was built
            drifted_state = copy.deepcopy(dict(bundle.current_state))
            drifted_state["context"] = {
                **drifted_state["context"],
                "location_id": "fixture-elsewhere",
            }
            from engine.life.runtime_bundle import RuntimeBundle, validate_runtime_bundle

            drifted_bundle = validate_runtime_bundle(
                RuntimeBundle(
                    current_state=drifted_state,
                    schedule_state=copy.deepcopy(dict(bundle.schedule_state)),
                    relation_state=copy.deepcopy(dict(bundle.relation_state)),
                    home_state=copy.deepcopy(dict(bundle.home_state)),
                    consumables_state=copy.deepcopy(dict(bundle.consumables_state)),
                    wardrobe_state=copy.deepcopy(dict(bundle.wardrobe_state)),
                    finance_state=copy.deepcopy(dict(bundle.finance_state)),
                ),
                reference_sets=_refs(),
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted_bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )

    def test_21_selected_candidate_never_started(self) -> None:
        import ast

        import engine.life.activity_finalization as mod

        self.assertFalse(hasattr(mod, "start_runtime_activity_from_selection"))
        self.assertFalse(hasattr(mod, "start_activity_from_selection"))
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.assertNotEqual(alias.name, "start_runtime_activity_from_selection")
                    self.assertNotEqual(alias.name, "start_activity_from_selection")
            if isinstance(node, ast.Name) and node.id in {
                "start_runtime_activity_from_selection",
                "start_activity_from_selection",
            }:
                self.fail(f"forbidden call/name: {node.id}")
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            self.assertIsNone(result.bundle.current_state["context"]["active_activity"])
            self.assertNotEqual(
                result.actual_event.get("activity_instance_id"),
                cand.candidate_id,
            )

    def test_22_returned_active_activity_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            self.assertIsNone(result.bundle.current_state["context"]["active_activity"])

    def test_23_discarded_candidate_id_diagnostic_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            self.assertEqual(result.discarded_selected_candidate_id, cand.candidate_id)
            # No start authority: active remains null, no new activity for discarded id
            self.assertIsNone(result.bundle.current_state["context"]["active_activity"])

    def test_24_fresh_post_finalization_decision_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            self.assertTrue(result.post_finalize_decision_required)
            for e in result.bundle.current_state["pending_queue"]["events"]:
                self.assertFalse(is_managed_v2_decision_wakeup(e))


class ActivityFinalizationCompletionBoundaryTests(unittest.TestCase):
    """Cases 25–30."""

    def test_25_exact_at_planned_true(self) -> None:
        end = _add_min(AS_OF, 60)
        act = _planned_end_active(planned_end=end)
        self.assertTrue(
            runtime_activity_reached_completion_boundary(act, actual_end=end)
        )

    def test_26_exact_early_false(self) -> None:
        end = _add_min(AS_OF, 60)
        act = _planned_end_active(planned_end=end)
        self.assertFalse(
            runtime_activity_reached_completion_boundary(
                act, actual_end=_add_min(AS_OF, 30)
            )
        )

    def test_27_window_before_earliest_false(self) -> None:
        act = _runtime_active()
        self.assertFalse(
            runtime_activity_reached_completion_boundary(
                act, actual_end=_add_min(AS_OF, 10)
            )
        )

    def test_28_at_earliest_true(self) -> None:
        act = _runtime_active()
        earliest = act["expected_end_window"]["earliest"]
        self.assertTrue(
            runtime_activity_reached_completion_boundary(act, actual_end=earliest)
        )

    def test_29_between_earliest_latest_true(self) -> None:
        act = _runtime_active()
        mid = _add_min(AS_OF, 60)
        self.assertTrue(
            runtime_activity_reached_completion_boundary(act, actual_end=mid)
        )

    def test_30_open_earliest_null_false(self) -> None:
        act = _runtime_active(
            planned_end=None,
            expected_end=None,
            expected_end_window={"earliest": None, "latest": _add_min(AS_OF, 90)},
        )
        self.assertFalse(
            runtime_activity_reached_completion_boundary(
                act, actual_end=_add_min(AS_OF, 90)
            )
        )
        act2 = _runtime_active(
            planned_end=None,
            expected_end=None,
            expected_end_window=None,
        )
        self.assertFalse(
            runtime_activity_reached_completion_boundary(
                act2, actual_end=_add_min(AS_OF, 30)
            )
        )


class ActivityFinalizationQueueTests(unittest.TestCase):
    """Cases 69–74."""

    def test_69_stale_managed_wakeup_removed_on_exact_finish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            end = _add_min(AS_OF, 60)
            # due_at must be >= processed_through for a valid checkpoint queue
            stale = _v2_wakeup(due_at=end, decision_key="stale-key")
            bundle, active, end_event, _ = _exact_end_setup(
                tmp, planned_end=end, extra_events=[stale]
            )
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            for e in result.bundle.current_state["pending_queue"]["events"]:
                self.assertFalse(is_managed_v2_decision_wakeup(e))

    def test_70_switch_trigger_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            wid = wakeup["event_id"]
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            ids = [e["event_id"] for e in result.bundle.current_state["pending_queue"]["events"]]
            self.assertNotIn(wid, ids)

    def test_71_future_activity_end_for_interrupted_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planned = _add_min(AS_OF, 90)
            act = _planned_end_active(planned_end=planned)
            future_end = build_activity_end_event(
                activity_instance_id=act["activity_instance_id"],
                planned_end=planned,
            )
            assert future_end is not None
            bundle, _, frame, facts, cand, wakeup, at = _switch_setup(
                tmp,
                processed=_add_min(AS_OF, 20),
                active=act,
                extra_events=[future_end],
                commitments=[_commitment(commitment_id="cmt-a")],
                tasks=[],
            )
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            for e in result.bundle.current_state["pending_queue"]["events"]:
                if e.get("event_kind") == "ACTIVITY_END":
                    self.fail("ACTIVITY_END for interrupted activity must be removed")

    def test_72_unrelated_non_managed_events_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            keep = _unrelated_event()
            bundle, active, end_event, end = _exact_end_setup(
                tmp, extra_events=[keep]
            )
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            ids = [e["event_id"] for e in result.bundle.current_state["pending_queue"]["events"]]
            self.assertIn("unrelated:keep", ids)

    def test_73_canonical_queue_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            keep_a = {
                **_unrelated_event(due_at=_add_min(AS_OF, 200)),
                "event_id": "unrelated:late",
            }
            keep_b = {
                **_unrelated_event(due_at=_add_min(AS_OF, 100)),
                "event_id": "unrelated:early",
                "priority": 40,
            }
            bundle, active, end_event, end = _exact_end_setup(
                tmp, extra_events=[keep_a, keep_b]
            )
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            events = result.bundle.current_state["pending_queue"]["events"]
            from engine.life.invariants import assert_queue_canonically_sorted

            assert_queue_canonically_sorted(events)
            self.assertEqual(result.bundle.current_state["pending_queue"]["cursor"], 0)

    def test_74_no_replacement_wakeup_projected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, act, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            for e in result.bundle.current_state["pending_queue"]["events"]:
                self.assertFalse(is_managed_v2_decision_wakeup(e))


class ActivityFinalizationGlobalMutationTests(unittest.TestCase):
    """Cases 75–79."""

    def test_75_processed_through_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            before = bundle.current_state["processed_through"]
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            self.assertEqual(result.bundle.current_state["processed_through"], before)
            self.assertEqual(bundle.current_state["processed_through"], before)

    def test_76_state_revision_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            before = bundle.current_state["state_revision"]
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            self.assertEqual(result.bundle.current_state["state_revision"], before)

    def test_77_history_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            before = copy.deepcopy(bundle.current_state.get("history"))
            result = finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            self.assertEqual(result.bundle.current_state.get("history"), before)

    def test_78_input_bundle_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            snap = _bundle_snapshot(bundle)
            finalize_runtime_activity_from_exact_end(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                activity_end_event=end_event,
            )
            self.assertEqual(canonical_json(_bundle_snapshot(bundle)), canonical_json(snap))

    def test_79_runtime_start_effects_on_end_nonempty_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            end = _add_min(AS_OF, 60)
            # Bypass validate_active_activity effects check by building then injecting
            active = _planned_end_active(planned_end=end)
            active = copy.deepcopy(active)
            active["effects_on_end"] = [
                {
                    "schema_version": 1,
                    "effect_type": "HUMAN_STATE_DELTA",
                    "payload": {"hunger": -1},
                }
            ]
            end_event = build_activity_end_event(
                activity_instance_id=active["activity_instance_id"],
                planned_end=end,
            )
            assert end_event is not None
            # Install without re-validating through _runtime_active
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": end,
                    "state_revision": 11,
                    "human_state": {
                        "sleep_debt_min": 0,
                        "hunger": 200,
                        "physical_fatigue": 100,
                        "affect_valence": 0,
                        "stress": 100,
                        "social_battery": 800,
                    },
                    "integration": {
                        "schema_version": 1,
                        "rate_remainders": empty_rate_remainders(),
                    },
                    "pending_queue": {"cursor": 0, "events": [end_event]},
                    "context": {
                        **c["context"],
                        "active_activity": active,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    commitments=[_commitment(commitment_id="cmt-a")],
                ),
            )
            # load_runtime_bundle may re-validate active — if it strips or rejects,
            # exercise finalize path via manual bundle when possible.
            try:
                bundle = load_runtime_bundle(root, reference_sets=_refs())
            except LifeEngineError:
                # If checkpoint validation rejects non-empty effects with runtime
                # fields inconsistently, build via validate after clearing and
                # re-inject for the C2B gate only.
                active2 = _planned_end_active(planned_end=end)
                bundle = _load_bundle(
                    tmp,
                    active=active2,
                    queue_events=[end_event],
                    processed_through=end,
                    commitments=[_commitment(commitment_id="cmt-a")],
                )
                from engine.life.runtime_bundle import RuntimeBundle, validate_runtime_bundle

                state = copy.deepcopy(dict(bundle.current_state))
                poisoned = copy.deepcopy(active2)
                poisoned["effects_on_end"] = [
                    {
                        "schema_version": 1,
                        "effect_type": "HUMAN_STATE_DELTA",
                        "payload": {"hunger": -1},
                    }
                ]
                state["context"]["active_activity"] = poisoned
                bundle = RuntimeBundle(
                    current_state=state,
                    schedule_state=bundle.schedule_state,
                    relation_state=bundle.relation_state,
                    home_state=bundle.home_state,
                    consumables_state=bundle.consumables_state,
                    wardrobe_state=bundle.wardrobe_state,
                    finance_state=bundle.finance_state,
                )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_exact_end(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    activity_end_event=end_event,
                )


class ActivityFinalizationQueueBindRegressions(unittest.TestCase):
    """Review correction: authoritative queued scheduler occurrence bind."""

    def test_decision_trigger_id_not_in_queue_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            from engine.life.runtime_decision import RuntimeDecisionTrigger

            forged = RuntimeDecisionTrigger(
                trigger_id="trig-missing",
                occurred_at=at,
                trigger_kind="IDLE",
                semantic_decision_key="idle:switch",
                reason_code="IDLE_REASSESSMENT",
                metric=None,
                target_band=None,
                direction=None,
                source_kind=None,
                source_ref=None,
            )
            bad = _frame(
                cand,
                bundle=bundle,
                decision_facts=facts,
                trigger=forged,
                runtime_decision_key=frame.runtime_decision_key,
                finalization_context_hash=frame.finalization_context_hash,
                materialization_context_hash=frame.materialization_context_hash,
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=bad,
                    decision_facts=facts,
                )

    def test_queued_trigger_semantic_mismatch_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, cand, wakeup, at = _switch_setup(tmp)
            from dataclasses import replace

            mutated = replace(frame.trigger, reason_code="THRESHOLD_CROSSING")
            bad = _frame(
                cand,
                bundle=bundle,
                decision_facts=facts,
                trigger=mutated,
                runtime_decision_key=frame.runtime_decision_key,
                finalization_context_hash=frame.finalization_context_hash,
                materialization_context_hash=frame.materialization_context_hash,
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=bad,
                    decision_facts=facts,
                )

    def test_same_instant_activity_end_blocks_decision_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            end = _add_min(AS_OF, 60)
            act = _planned_end_active(planned_end=end)
            end_event = build_activity_end_event(
                activity_instance_id=act["activity_instance_id"],
                planned_end=end,
            )
            assert end_event is not None
            wakeup = _v2_wakeup(due_at=end)
            bundle = _load_bundle(
                tmp,
                active=act,
                queue_events=[end_event, wakeup],
                processed_through=end,
                commitments=[_commitment(commitment_id="cmt-a")],
            )
            from engine.life.runtime_decision import (
                derive_runtime_decision_key,
                runtime_trigger_from_v2_wakeup,
            )

            trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, wakeup)
            cand = _candidate(
                candidate_key="cand-key-next",
                action_kind="MEAL",
                priority_tier="URGENT_BIOLOGICAL",
                source_kind="BIOLOGICAL",
                source_ref="hunger:URGENT",
                rule_ids=("rule.meal",),
            )
            facts = _facts()
            frame = _frame(
                cand,
                bundle=bundle,
                decision_facts=facts,
                trigger=trigger,
                runtime_decision_key=derive_runtime_decision_key(
                    character_id=CHAR, trigger_id=trigger.trigger_id
                ),
            )
            with self.assertRaises(LifeEngineError) as ctx:
                finalize_runtime_activity_from_decision_switch(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )
            self.assertIn("ACTIVITY_END", str(ctx.exception))

    def test_exact_end_queued_row_wrong_due_or_priority_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, active, end_event, end = _exact_end_setup(tmp)
            # Corrupt queued row while supplying a correct event object.
            from engine.life.runtime_bundle import RuntimeBundle

            state = copy.deepcopy(dict(bundle.current_state))
            corrupted = copy.deepcopy(end_event)
            corrupted["priority"] = 99
            state["pending_queue"] = {"cursor": 0, "events": [corrupted]}
            poisoned = RuntimeBundle(
                current_state=state,
                schedule_state=bundle.schedule_state,
                relation_state=bundle.relation_state,
                home_state=bundle.home_state,
                consumables_state=bundle.consumables_state,
                wardrobe_state=bundle.wardrobe_state,
                finance_state=bundle.finance_state,
            )
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_exact_end(
                    bundle=poisoned,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    activity_end_event=end_event,
                )


class ActivityFinalizationFinalizationContextHashRegressions(unittest.TestCase):
    """Review correction: C2B finalization_context_hash drift rejects."""

    def _mutate_current(self, bundle, mutator):
        from engine.life.runtime_bundle import RuntimeBundle, validate_runtime_bundle

        state = copy.deepcopy(dict(bundle.current_state))
        mutator(state)
        out = RuntimeBundle(
            current_state=state,
            schedule_state=bundle.schedule_state,
            relation_state=bundle.relation_state,
            home_state=bundle.home_state,
            consumables_state=bundle.consumables_state,
            wardrobe_state=bundle.wardrobe_state,
            finance_state=bundle.finance_state,
        )
        return validate_runtime_bundle(out, reference_sets=_refs())

    def test_human_state_drift_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, _, _, _ = _switch_setup(tmp)

            def mut(state):
                state["human_state"] = {
                    **state["human_state"],
                    "hunger": state["human_state"]["hunger"] + 17,
                }

            drifted = self._mutate_current(bundle, mut)
            with self.assertRaises(LifeEngineError) as ctx:
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )
            self.assertIn("finalization_context_hash", str(ctx.exception))

    def test_active_details_drift_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            act = _runtime_active(
                activity_type="MEAL",
                details={"meal_source": "HOME_COOKED", "meal_extent": "STANDARD"},
                runtime_context={
                    **_runtime_active()["runtime_context"],
                    "action_kind": "MEAL",
                    "priority_tier": "URGENT_BIOLOGICAL",
                    "source_kind": "BIOLOGICAL",
                    "source_ref": "hunger:URGENT",
                },
                runtime_finish_context=_finish_ctx(),
                causes=[
                    {"cause_type": "DECISION", "ref": DECISION_KEY},
                    {"cause_type": "BIOLOGICAL", "ref": "hunger:URGENT"},
                ],
            )
            bundle, _, frame, facts, _, _, _ = _switch_setup(tmp, active=act)

            def mut(state):
                active = copy.deepcopy(state["context"]["active_activity"])
                active["details"] = {
                    "meal_source": "HOME_COOKED",
                    "meal_extent": "LIGHT",
                }
                state["context"]["active_activity"] = active

            drifted = self._mutate_current(bundle, mut)
            with self.assertRaises(LifeEngineError) as ctx:
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )
            self.assertIn("finalization_context_hash", str(ctx.exception))

    def test_companions_drift_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            act = _runtime_active(
                activity_type="SOCIAL_CONTACT",
                details={"contact_mode": "CALL"},
                companions=[{"entity_id": PERSON}],
                runtime_context={
                    **_runtime_active()["runtime_context"],
                    "action_kind": "SOCIAL_CONTACT",
                    "priority_tier": "SOCIAL_PROMISE",
                    "source_kind": "SOCIAL",
                    "source_ref": "opp:1",
                },
                runtime_finish_context=_finish_ctx(),
                dynamics_context={
                    "schema_version": 1,
                    "base_activity_class": "SEDENTARY_FOCUSED",
                    "social_exposure": "ACTIVE",
                },
                causes=[
                    {"cause_type": "DECISION", "ref": DECISION_KEY},
                    {"cause_type": "SOCIAL", "ref": "opp:1"},
                ],
            )
            bundle, _, frame, facts, _, _, _ = _switch_setup(tmp, active=act)

            def mut(state):
                active = copy.deepcopy(state["context"]["active_activity"])
                active["companions"] = []
                state["context"]["active_activity"] = active

            drifted = self._mutate_current(bundle, mut)
            with self.assertRaises(LifeEngineError) as ctx:
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )
            self.assertIn("finalization_context_hash", str(ctx.exception))

    def test_runtime_finish_context_drift_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, _, _, _ = _switch_setup(tmp)

            def mut(state):
                active = copy.deepcopy(state["context"]["active_activity"])
                active["runtime_finish_context"] = _finish_ctx(
                    task_effort_remaining_at_start_min=90
                )
                state["context"]["active_activity"] = active

            drifted = self._mutate_current(bundle, mut)
            with self.assertRaises(LifeEngineError) as ctx:
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )
            self.assertIn("finalization_context_hash", str(ctx.exception))

    def test_planned_or_window_drift_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, _, _, _ = _switch_setup(tmp)

            def mut(state):
                active = copy.deepcopy(state["context"]["active_activity"])
                window = dict(active["expected_end_window"])
                window["latest"] = _add_min(window["latest"], 15)
                active["expected_end_window"] = window
                state["context"]["active_activity"] = active

            drifted = self._mutate_current(bundle, mut)
            with self.assertRaises(LifeEngineError) as ctx:
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )
            self.assertIn("finalization_context_hash", str(ctx.exception))

    def test_pending_queue_trigger_removed_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, _, _, _ = _switch_setup(tmp)

            def mut(state):
                state["pending_queue"] = {"cursor": 0, "events": []}

            drifted = self._mutate_current(bundle, mut)
            with self.assertRaises(LifeEngineError):
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )

    def test_relation_state_drift_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, _, _, _ = _switch_setup(tmp)
            from engine.life.runtime_bundle import RuntimeBundle, validate_runtime_bundle
            from engine.life.world_state import project_initial_relation_state

            # Rebuild relation with different as_of to change revision/hash.
            relation = project_initial_relation_state(
                character_id=CHAR,
                as_of=_add_min(AS_OF, 1),
                provenance=_prov(),
                known_person_ids=frozenset({PERSON}),
                people=[{"person_id": PERSON, "open_promises": []}],
            )
            state = copy.deepcopy(dict(bundle.current_state))
            state["domain_refs"] = {
                **state["domain_refs"],
                "relation_state_revision": relation["revision"],
            }
            drifted = RuntimeBundle(
                current_state=state,
                schedule_state=bundle.schedule_state,
                relation_state=relation,
                home_state=bundle.home_state,
                consumables_state=bundle.consumables_state,
                wardrobe_state=bundle.wardrobe_state,
                finance_state=bundle.finance_state,
            )
            drifted = validate_runtime_bundle(drifted, reference_sets=_refs())
            with self.assertRaises(LifeEngineError) as ctx:
                finalize_runtime_activity_from_decision_switch(
                    bundle=drifted,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    frame=frame,
                    decision_facts=facts,
                )
            self.assertIn("finalization_context_hash", str(ctx.exception))

    def test_exact_original_context_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle, _, frame, facts, cand, _, at = _switch_setup(tmp)
            snap = _bundle_snapshot(bundle)
            result = finalize_runtime_activity_from_decision_switch(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                frame=frame,
                decision_facts=facts,
            )
            _assert_common_result(
                self,
                result,
                finish_path="DECISION_SWITCH",
                discarded=cand.candidate_id,
                processed=at,
                revision=bundle.current_state["state_revision"],
                history=bundle.current_state.get("history"),
                input_snap=snap,
                input_bundle=bundle,
            )
