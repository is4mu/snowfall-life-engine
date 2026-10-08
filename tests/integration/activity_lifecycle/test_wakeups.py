"""Activity lifecycle managed-wakeup and projection integration tests."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from engine.life.activity_lifecycle import (
    RuntimeWakeupProjectionFacts,
    derive_decision_wakeup_context,
    parse_runtime_wakeup_projection_facts,
    reconcile_active_runtime_queue,
)
from engine.life.activity_materialization import build_activity_end_event
from engine.life.canonical import canonical_json
from engine.life.derived import default_synthetic_sleep_profile
from engine.life.errors import LifeEngineError
from engine.life.events import validate_active_activity
from engine.life.fixed_point import empty_rate_remainders
from engine.life.ids import stable_id
from engine.life.invariants import queue_event_sort_key
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.schedule_state import build_schedule_state
from engine.life.timeutil import add_minutes, format_rfc3339, parse_rfc3339
from engine.life.wakeups import (
    KnownDecisionBoundary,
    is_managed_v2_decision_wakeup,
)
from tests.support.builders.wakeups import (
    AS_OF,
    CHAR,
    HORIZON,
    HOME,
    POLICY,
    POLICY_VERSION,
    SLEEP_PROF,
    _add_min,
    _load_bundle,
    _main_sleep_active,
    _refs,
    _runtime_active,
    _task,
    _wakeup_facts,
    build_synthetic_bundle_root,
)

import pytest

pytestmark = pytest.mark.integration


class ActivityWakeupSleepProfileTests(unittest.TestCase):
    def test_matching_main_profile_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(tmp, active=_main_sleep_active())
            result = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(),
            )
            self.assertTrue(result.projection is not None)

    def test_mismatched_need_rejects(self) -> None:
        bad_profile = dict(SLEEP_PROF)
        bad_profile["sleep_need_min"] = SLEEP_PROF["sleep_need_min"] + 30
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(tmp, active=_main_sleep_active())
            with self.assertRaises(LifeEngineError):
                reconcile_active_runtime_queue(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    wakeup_facts=_wakeup_facts(sleep_profile=bad_profile),
                )

    def test_malformed_profile_rejects(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_runtime_wakeup_projection_facts(
                _wakeup_facts(sleep_profile={"sleep_need_min": 480})
            )

    def test_no_synthetic_default(self) -> None:
        raw = _wakeup_facts()
        del raw["sleep_profile"]
        with self.assertRaises(LifeEngineError):
            parse_runtime_wakeup_projection_facts(raw)
        raw2 = _wakeup_facts(sleep_profile=None)
        with self.assertRaises(LifeEngineError):
            parse_runtime_wakeup_projection_facts(raw2)


class ActivityWakeupWakeupContextTests(unittest.TestCase):
    def test_reassessable_null_legal_boundary(self) -> None:
        act = _runtime_active()
        ctx = derive_decision_wakeup_context(
            active_activity=act,
            fatigue_high_relevant=True,
            stress_relevant=False,
            social_low_relevant=True,
        )
        self.assertFalse(ctx.is_sleeping)
        self.assertEqual(ctx.active_interruptibility, "REASSESSABLE")
        self.assertIsNone(ctx.next_legal_reassessment_at)
        self.assertTrue(ctx.fatigue_high_relevant)
        self.assertFalse(ctx.stress_relevant)
        self.assertTrue(ctx.social_low_relevant)

    def test_non_interruptible_uses_planned_end(self) -> None:
        end = _add_min(AS_OF, 60)
        act = _runtime_active(
            planned_end=end,
            expected_end=end,
            expected_end_window=None,
            runtime_context={
                **_runtime_active()["runtime_context"],
                "interruptibility": "NON_INTERRUPTIBLE",
                "action_kind": "HARD_COMMITMENT_ACTIVITY",
                "priority_tier": "HARD_COMMITMENT",
                "source_kind": "COMMITMENT",
                "source_ref": "cmt-1",
            },
        )
        ctx = derive_decision_wakeup_context(
            active_activity=act,
            fatigue_high_relevant=False,
            stress_relevant=False,
            social_low_relevant=False,
        )
        self.assertEqual(ctx.active_interruptibility, "NON_INTERRUPTIBLE")
        self.assertEqual(ctx.next_legal_reassessment_at, end)

    def test_sleep_derives_is_sleeping_true(self) -> None:
        act = _main_sleep_active()
        ctx = derive_decision_wakeup_context(
            active_activity=act,
            fatigue_high_relevant=False,
            stress_relevant=False,
            social_low_relevant=False,
        )
        self.assertTrue(ctx.is_sleeping)


class ActivityWakeupQueueWakeupTests(unittest.TestCase):
    def test_managed_replaced_le1_nonmanaged_preserved(self) -> None:
        stale = {
            "schema_version": 2,
            "event_id": stable_id(
                "decision-wakeup",
                CHAR,
                "stale-key",
                AS_OF,
            ),
            "event_kind": "DECISION_WAKEUP",
            "due_at": _add_min(AS_OF, 15),
            "priority": 50,
            "payload": {
                "trigger_kind": "THRESHOLD",
                "decision_key": "stale-key",
                "reason_code": "THRESHOLD_HUNGER_MEAL_NEEDED_UP",
                "metric": "HUNGER",
                "target_band": "MEAL_NEEDED",
                "direction": "UP",
                "source_kind": None,
                "source_ref": None,
            },
        }
        keep = {
            "schema_version": 1,
            "event_id": "idle:preserve",
            "event_kind": "IDLE_REASSESSMENT",
            "due_at": _add_min(AS_OF, 200),
            "priority": 50,
            "payload": {"interval_min": 200},
        }
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(tmp, queue_events=[stale, keep])
            result = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(),
            )
            events = result.bundle.current_state["pending_queue"]["events"]
            managed = [e for e in events if is_managed_v2_decision_wakeup(e)]
            self.assertLessEqual(len(managed), 1)
            self.assertTrue(any(e["event_id"] == "idle:preserve" for e in events))
            self.assertFalse(any(e["event_id"] == stale["event_id"] for e in events))
            self.assertEqual(events, sorted(events, key=queue_event_sort_key))

    def test_active_and_caller_boundaries_merged(self) -> None:
        caller_due = _add_min(AS_OF, 20)
        caller = KnownDecisionBoundary(
            due_at=caller_due,
            trigger_kind="OBLIGATION",
            decision_key=stable_id("obligation", "task-a", caller_due),
            reason_code="OBLIGATION_DUE",
            source_kind="TASK",
            source_ref="task-a",
        )
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(tmp)
            result = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(known_boundaries=[caller]),
            )
            self.assertEqual(len(result.active_boundaries), 2)
            keys = {c.decision_key for c in result.projection.future_candidates}
            self.assertIn(caller.decision_key, keys)
            for b in result.active_boundaries:
                self.assertIn(b.decision_key, keys)

    def test_duplicate_semantic_key_reject(self) -> None:
        act = _runtime_active()
        earliest = act["expected_end_window"]["earliest"]
        dup = KnownDecisionBoundary(
            due_at=earliest,
            trigger_kind="ACTIVE_REASSESSMENT",
            decision_key=stable_id(
                "active-reassessment",
                act["activity_instance_id"],
                "EARLIEST",
                earliest,
            ),
            reason_code="ACTIVE_EXPECTED_WINDOW_EARLIEST",
            source_kind="CURRENT_ACTIVITY",
            source_ref=act["activity_instance_id"],
        )
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(tmp, active=act)
            with self.assertRaises(LifeEngineError):
                reconcile_active_runtime_queue(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    wakeup_facts=_wakeup_facts(known_boundaries=[dup]),
                )

    def test_immediate_reasons_surfaced_unchanged(self) -> None:
        # High hunger => immediate MEAL_NEEDED reason from Slice 2C.
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(
                tmp,
                human_state={
                    "sleep_debt_min": 0,
                    "hunger": 900,
                    "physical_fatigue": 100,
                    "affect_valence": 0,
                    "stress": 100,
                    "social_battery": 800,
                },
            )
            result = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(),
            )
            self.assertTrue(result.projection.immediate_reassessment_reasons)
            # Must not invent a fake due-now queue event solely for immediates.
            due_now = [
                e
                for e in result.bundle.current_state["pending_queue"]["events"]
                if e["due_at"] == AS_OF
            ]
            # ACTIVITY_END only if planned; this study activity has none.
            self.assertEqual(due_now, [])

    def test_exact_end_insert_during_reconcile(self) -> None:
        end = _add_min(AS_OF, 60)
        act = _runtime_active(
            activity_instance_id="act-hard-end",
            planned_end=end,
            expected_end=end,
            expected_end_window=None,
            runtime_context={
                **_runtime_active()["runtime_context"],
                "interruptibility": "NON_INTERRUPTIBLE",
                "action_kind": "HARD_COMMITMENT_ACTIVITY",
                "priority_tier": "HARD_COMMITMENT",
                "source_kind": "COMMITMENT",
                "source_ref": "cmt-1",
            },
        )
        expected = build_activity_end_event(
            activity_instance_id="act-hard-end", planned_end=end
        )
        assert expected is not None
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(tmp, active=act, queue_events=[])
            result = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(),
            )
            ends = [
                e
                for e in result.bundle.current_state["pending_queue"]["events"]
                if e["event_kind"] == "ACTIVITY_END"
            ]
            self.assertEqual(len(ends), 1)
            self.assertEqual(ends[0]["event_id"], expected["event_id"])
            self.assertEqual(result.active_boundaries, ())

    def test_no_state_history_schedule_domain_mutation(self) -> None:
        keep = {
            "schema_version": 1,
            "event_id": "keep-1",
            "event_kind": "IDLE_REASSESSMENT",
            "due_at": _add_min(AS_OF, 180),
            "priority": 50,
            "payload": {"interval_min": 180},
        }
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _load_bundle(tmp, queue_events=[keep])
            before = {
                "rev": bundle.current_state["state_revision"],
                "hist": copy.deepcopy(bundle.current_state["history"]),
                "hs": copy.deepcopy(bundle.current_state["human_state"]),
                "active": copy.deepcopy(bundle.current_state["context"]["active_activity"]),
                "sched": copy.deepcopy(dict(bundle.schedule_state)),
                "world": canonical_json(
                    {
                        "relation": dict(bundle.relation_state),
                        "home": dict(bundle.home_state),
                        "consumables": dict(bundle.consumables_state),
                        "wardrobe": dict(bundle.wardrobe_state),
                        "finance": dict(bundle.finance_state),
                    }
                ),
            }
            result = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(),
            )
            out = result.bundle
            self.assertEqual(out.current_state["state_revision"], before["rev"])
            self.assertEqual(out.current_state["history"], before["hist"])
            self.assertEqual(out.current_state["human_state"], before["hs"])
            self.assertEqual(
                out.current_state["context"]["active_activity"], before["active"]
            )
            self.assertEqual(dict(out.schedule_state), before["sched"])
            self.assertEqual(
                canonical_json(
                    {
                        "relation": dict(out.relation_state),
                        "home": dict(out.home_state),
                        "consumables": dict(out.consumables_state),
                        "wardrobe": dict(out.wardrobe_state),
                        "finance": dict(out.finance_state),
                    }
                ),
                before["world"],
            )


class ActivityWakeupFactsParserTests(unittest.TestCase):
    def test_unknown_field_and_float_reject(self) -> None:
        raw = _wakeup_facts(extra=1)
        with self.assertRaises(LifeEngineError):
            parse_runtime_wakeup_projection_facts(raw)
        raw2 = _wakeup_facts(elapsed_since_last_main_sleep_end_seconds=1.5)
        with self.assertRaises(LifeEngineError):
            parse_runtime_wakeup_projection_facts(raw2)

    def test_dataclass_roundtrip(self) -> None:
        facts = parse_runtime_wakeup_projection_facts(_wakeup_facts())
        again = parse_runtime_wakeup_projection_facts(facts)
        self.assertEqual(again.as_dict(), facts.as_dict())
        self.assertIsInstance(facts, RuntimeWakeupProjectionFacts)

    def test_malformed_dataclass_sleep_profile_controlled_error(self) -> None:
        bad = RuntimeWakeupProjectionFacts(
            sleep_profile="not-a-mapping",  # type: ignore[arg-type]
            elapsed_since_last_main_sleep_end_seconds=0,
            total_nap_awake_credit_min=0,
            fatigue_high_relevant=False,
            stress_relevant=False,
            social_low_relevant=False,
            known_boundaries=(),
            projection_horizon_end=HORIZON,
        )
        with self.assertRaises(LifeEngineError) as ctx:
            parse_runtime_wakeup_projection_facts(bad)
        self.assertNotIsInstance(ctx.exception, TypeError)
        self.assertNotIsInstance(ctx.exception, AttributeError)

    def test_malformed_dataclass_known_boundaries_item_controlled_error(self) -> None:
        bad = RuntimeWakeupProjectionFacts(
            sleep_profile=dict(SLEEP_PROF),
            elapsed_since_last_main_sleep_end_seconds=0,
            total_nap_awake_credit_min=0,
            fatigue_high_relevant=False,
            stress_relevant=False,
            social_low_relevant=False,
            known_boundaries=("not-a-boundary",),  # type: ignore[arg-type]
            projection_horizon_end=HORIZON,
        )
        with self.assertRaises(LifeEngineError) as ctx:
            parse_runtime_wakeup_projection_facts(bad)
        self.assertNotIsInstance(ctx.exception, TypeError)
        self.assertNotIsInstance(ctx.exception, AttributeError)


class ActivityWakeupConsumedBoundaryTests(unittest.TestCase):
    def test_consumed_earliest_at_now_excluded_latest_remains(self) -> None:
        now = _add_min(AS_OF, 30)
        earliest = now
        latest = _add_min(AS_OF, 90)
        act = _runtime_active(
            activity_instance_id="act-consumed-1",
            actual_start=AS_OF,
            expected_end_window={"earliest": earliest, "latest": latest},
        )
        earliest_key = stable_id(
            "active-reassessment", "act-consumed-1", "EARLIEST", earliest
        )
        latest_key = stable_id(
            "active-reassessment", "act-consumed-1", "LATEST", latest
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": now,
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
                    "pending_queue": {"cursor": 0, "events": []},
                    "context": {
                        **c["context"],
                        "active_activity": act,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            consumed = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(projection_horizon_end=_add_min(now, 24 * 60)),
                consumed_active_boundary_key=earliest_key,
            )
            keys = {b.decision_key for b in consumed.active_boundaries}
            self.assertNotIn(earliest_key, keys)
            self.assertIn(latest_key, keys)
            proj_keys = {c.decision_key for c in consumed.projection.future_candidates}
            self.assertNotIn(earliest_key, proj_keys)
            self.assertIn(latest_key, proj_keys)

            recovered = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(projection_horizon_end=_add_min(now, 24 * 60)),
                consumed_active_boundary_key=None,
            )
            recovered_keys = {b.decision_key for b in recovered.active_boundaries}
            self.assertIn(earliest_key, recovered_keys)
            self.assertIn(latest_key, recovered_keys)

    def test_caller_cannot_resurrect_consumed_active_boundary(self) -> None:
        now = _add_min(AS_OF, 30)
        earliest = now
        latest = _add_min(AS_OF, 90)
        act = _runtime_active(
            activity_instance_id="act-consumed-resurrect",
            actual_start=AS_OF,
            expected_end_window={"earliest": earliest, "latest": latest},
        )
        earliest_key = stable_id(
            "active-reassessment", "act-consumed-resurrect", "EARLIEST", earliest
        )
        resurrect = KnownDecisionBoundary(
            due_at=earliest,
            trigger_kind="ACTIVE_REASSESSMENT",
            decision_key=earliest_key,
            reason_code="ACTIVE_EXPECTED_WINDOW_EARLIEST",
            source_kind="CURRENT_ACTIVITY",
            source_ref="act-consumed-resurrect",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": now,
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
                    "pending_queue": {"cursor": 0, "events": []},
                    "context": {
                        **c["context"],
                        "active_activity": act,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            with self.assertRaises(LifeEngineError) as ctx:
                reconcile_active_runtime_queue(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    wakeup_facts=_wakeup_facts(
                        known_boundaries=[resurrect],
                        projection_horizon_end=_add_min(now, 24 * 60),
                    ),
                    consumed_active_boundary_key=earliest_key,
                )
            self.assertIn("CURRENT_ACTIVITY", ctx.exception.detail)
            self.assertIn("sole authority", ctx.exception.detail)
            # Confirm clean consumed path still excludes EARLIEST and never
            # resurrects due-now managed wakeup for that key.
            clean = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=_wakeup_facts(
                    projection_horizon_end=_add_min(now, 24 * 60)
                ),
                consumed_active_boundary_key=earliest_key,
            )
            proj_keys = {c.decision_key for c in clean.projection.future_candidates}
            self.assertNotIn(earliest_key, proj_keys)
            managed = [
                e
                for e in clean.bundle.current_state["pending_queue"]["events"]
                if is_managed_v2_decision_wakeup(e)
            ]
            for event in managed:
                self.assertNotEqual(
                    event.get("payload", {}).get("decision_key"), earliest_key
                )

    def test_caller_renamed_key_current_activity_reassessment_reject(self) -> None:
        now = _add_min(AS_OF, 30)
        earliest = now
        latest = _add_min(AS_OF, 90)
        act = _runtime_active(
            activity_instance_id="act-renamed-key",
            actual_start=AS_OF,
            expected_end_window={"earliest": earliest, "latest": latest},
        )
        earliest_key = stable_id(
            "active-reassessment", "act-renamed-key", "EARLIEST", earliest
        )
        renamed = KnownDecisionBoundary(
            due_at=earliest,
            trigger_kind="ACTIVE_REASSESSMENT",
            decision_key="arbitrary-renamed-active-reassessment-key",
            reason_code="ACTIVE_EXPECTED_WINDOW_EARLIEST",
            source_kind="CURRENT_ACTIVITY",
            source_ref="act-renamed-key",
        )
        future_inject = KnownDecisionBoundary(
            due_at=latest,
            trigger_kind="ACTIVE_REASSESSMENT",
            decision_key="arbitrary-future-active-reassessment-key",
            reason_code="ACTIVE_EXPECTED_WINDOW_LATEST",
            source_kind="CURRENT_ACTIVITY",
            source_ref="act-renamed-key",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": now,
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
                    "pending_queue": {"cursor": 0, "events": []},
                    "context": {
                        **c["context"],
                        "active_activity": act,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            for bad in (renamed, future_inject):
                with self.assertRaises(LifeEngineError) as ctx:
                    reconcile_active_runtime_queue(
                        bundle=bundle,
                        reference_sets=_refs(),
                        behavior_policy=POLICY,
                        wakeup_facts=_wakeup_facts(
                            known_boundaries=[bad],
                            projection_horizon_end=_add_min(now, 24 * 60),
                        ),
                        consumed_active_boundary_key=earliest_key,
                    )
                self.assertIn("CURRENT_ACTIVITY", ctx.exception.detail)

    def test_caller_wrong_source_ref_current_activity_reassessment_reject(self) -> None:
        now = _add_min(AS_OF, 30)
        earliest = now
        latest = _add_min(AS_OF, 90)
        act = _runtime_active(
            activity_instance_id="act-wrong-ref",
            actual_start=AS_OF,
            expected_end_window={"earliest": earliest, "latest": latest},
        )
        wrong_ref = KnownDecisionBoundary(
            due_at=earliest,
            trigger_kind="ACTIVE_REASSESSMENT",
            decision_key="wrong-ref-active-reassessment-key",
            reason_code="ACTIVE_EXPECTED_WINDOW_EARLIEST",
            source_kind="CURRENT_ACTIVITY",
            source_ref="some-other-activity-id",
        )
        null_ref = KnownDecisionBoundary(
            due_at=earliest,
            trigger_kind="ACTIVE_REASSESSMENT",
            decision_key="null-ref-active-reassessment-key",
            reason_code="ACTIVE_EXPECTED_WINDOW_EARLIEST",
            source_kind="CURRENT_ACTIVITY",
            source_ref=None,
        )
        empty_ref_raw = {
            "due_at": earliest,
            "trigger_kind": "ACTIVE_REASSESSMENT",
            "decision_key": "empty-ref-active-reassessment-key",
            "reason_code": "ACTIVE_EXPECTED_WINDOW_EARLIEST",
            "source_kind": "CURRENT_ACTIVITY",
            "source_ref": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": now,
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
                    "pending_queue": {"cursor": 0, "events": []},
                    "context": {
                        **c["context"],
                        "active_activity": act,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            for bad in (wrong_ref, null_ref):
                with self.assertRaises(LifeEngineError) as ctx:
                    reconcile_active_runtime_queue(
                        bundle=bundle,
                        reference_sets=_refs(),
                        behavior_policy=POLICY,
                        wakeup_facts=_wakeup_facts(
                            known_boundaries=[bad],
                            projection_horizon_end=_add_min(now, 24 * 60),
                        ),
                        consumed_active_boundary_key=None,
                    )
                self.assertIn("CURRENT_ACTIVITY", ctx.exception.detail)
                self.assertIn("sole authority", ctx.exception.detail)
            with self.assertRaises(LifeEngineError) as empty_ctx:
                reconcile_active_runtime_queue(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    wakeup_facts=_wakeup_facts(
                        known_boundaries=[empty_ref_raw],
                        projection_horizon_end=_add_min(now, 24 * 60),
                    ),
                    consumed_active_boundary_key=None,
                )
            # Empty source_ref fails closed (validator or ownership).
            self.assertTrue(
                "source_ref" in empty_ctx.exception.detail
                or "CURRENT_ACTIVITY" in empty_ctx.exception.detail
            )

    def test_forged_stale_future_consumed_key_reject(self) -> None:
        now = _add_min(AS_OF, 30)
        earliest = now
        latest = _add_min(AS_OF, 90)
        act = _runtime_active(
            activity_instance_id="act-consumed-2",
            actual_start=AS_OF,
            expected_end_window={"earliest": earliest, "latest": latest},
        )
        future_key = stable_id(
            "active-reassessment", "act-consumed-2", "LATEST", latest
        )
        other_key = stable_id(
            "active-reassessment", "other-act", "EARLIEST", earliest
        )
        stale_past_key = stable_id(
            "active-reassessment",
            "act-consumed-2",
            "EARLIEST",
            _add_min(AS_OF, 10),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": now,
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
                    "pending_queue": {"cursor": 0, "events": []},
                    "context": {
                        **c["context"],
                        "active_activity": act,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            facts = _wakeup_facts(projection_horizon_end=_add_min(now, 24 * 60))
            for bad_key in ("forged-key", future_key, other_key, stale_past_key):
                with self.assertRaises(LifeEngineError):
                    reconcile_active_runtime_queue(
                        bundle=bundle,
                        reference_sets=_refs(),
                        behavior_policy=POLICY,
                        wakeup_facts=facts,
                        consumed_active_boundary_key=bad_key,
                    )


class ActivityWakeupMissedLatestTests(unittest.TestCase):
    def test_processed_past_latest_fail_closed(self) -> None:
        latest = _add_min(AS_OF, 90)
        past = _add_min(latest, 5)
        act = _runtime_active(
            activity_instance_id="act-missed-latest",
            actual_start=AS_OF,
            expected_end_window={
                "earliest": _add_min(AS_OF, 30),
                "latest": latest,
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": past,
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
                    "pending_queue": {"cursor": 0, "events": []},
                    "context": {
                        **c["context"],
                        "active_activity": act,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            with self.assertRaises(LifeEngineError) as ctx:
                reconcile_active_runtime_queue(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    wakeup_facts=_wakeup_facts(
                        projection_horizon_end=_add_min(past, 24 * 60)
                    ),
                )
            self.assertIn("expected_end_window.latest", ctx.exception.detail)

    def test_processed_equals_latest_surfaces_unless_consumed(self) -> None:
        latest = _add_min(AS_OF, 90)
        act = _runtime_active(
            activity_instance_id="act-at-latest",
            actual_start=AS_OF,
            expected_end_window={
                "earliest": _add_min(AS_OF, 30),
                "latest": latest,
            },
        )
        latest_key = stable_id(
            "active-reassessment", "act-at-latest", "LATEST", latest
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(
                Path(tmp),
                mutate_current=lambda c: {
                    **c,
                    "behavior_policy_version": POLICY_VERSION,
                    "processed_through": latest,
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
                    "pending_queue": {"cursor": 0, "events": []},
                    "context": {
                        **c["context"],
                        "active_activity": act,
                        "location_id": HOME,
                    },
                },
                mutate_schedule=lambda s: build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    obligation_tasks=[_task()],
                ),
            )
            bundle = load_runtime_bundle(root, reference_sets=_refs())
            facts = _wakeup_facts(projection_horizon_end=_add_min(latest, 24 * 60))
            surfaced = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=facts,
                consumed_active_boundary_key=None,
            )
            self.assertEqual(
                {b.decision_key for b in surfaced.active_boundaries}, {latest_key}
            )
            excluded = reconcile_active_runtime_queue(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                wakeup_facts=facts,
                consumed_active_boundary_key=latest_key,
            )
            self.assertEqual(excluded.active_boundaries, ())
