"""Partition/restart equivalence and transactional time-history integration."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import pytest

from engine.life.canonical import canonical_hash
from engine.life.clock import (
    advance,
    enqueue_event,
    reconcile_threshold_wakeups,
    semantic_state_view,
    snapshot_workspace_bytes,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.fixed_point import empty_rate_remainders, integrate_rates
from engine.life.ids import stable_id
from engine.life.policy import load_policy
from engine.life.timeutil import format_rfc3339, parse_rfc3339
from engine.life.timeline import logical_closed_at
from tests.support.assertions.invariants import assert_partition_equivalent
from tests.support.builders.workspace import (
    load_state,
    make_workspace,
    policy_path,
    queue_activity_end,
    save_state,
)

pytestmark = pytest.mark.integration

class TransactionalAdvanceTests(unittest.TestCase):
    def test_failure_after_first_finalize_leaves_workspace_unchanged(self):
        """Valid-at-load state; fail mid-advance via max_events ceiling (not dangling ref)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = deepcopy(load_policy(policy_path()))
            policy["idle_reassessment"]["max_events_per_advance"] = 1
            policy["idle_reassessment"]["interval_min"] = 30
            ppath = root / "policy.json"
            ppath.write_text(json.dumps(policy), encoding="utf-8")
            ws = make_workspace(root / "ws")
            a1 = {
                "schema_version": 1,
                "activity_instance_id": stable_id("act", "tx", "one"),
                "activity_type": "fixture_a",
                "actual_start": "2026-01-01T00:10:00+09:00",
                "expected_end": "2026-01-01T00:20:00+09:00",
                "location_id": "fixture-home",
                "summary": "first",
                "effects_on_end": [],
            }
            queue_activity_end(ws, activity=a1, due_at="2026-01-01T00:20:00+09:00")
            # Load-valid: exactly one ACTIVITY_END matching active. Ceiling trips after
            # first processed event once idle/threshold wakeups are also due.
            before = snapshot_workspace_bytes(ws)
            with self.assertRaises(LifeEngineError) as ctx:
                advance(ws, target="2026-01-01T03:00:00+09:00", policy_path=ppath)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
            self.assertIn("max_events_per_advance", ctx.exception.detail)
            after = snapshot_workspace_bytes(ws)
            self.assertEqual(before, after)

class ThresholdReconcileTests(unittest.TestCase):
    def test_effect_delays_crossing_replaces_wakeup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            activity = {
                "schema_version": 1,
                "activity_instance_id": stable_id("act", "thr", "delay"),
                "activity_type": "fixture_meal",
                "actual_start": "2026-01-01T00:30:00+09:00",
                "planned_end": "2026-01-01T01:00:00+09:00",
                "location_id": "fixture-home",
                "summary": "eat",
                "effects_on_end": [
                    {
                        "schema_version": 1,
                        "effect_type": "HUMAN_STATE_DELTA",
                        "payload": {"hunger": -80},
                    }
                ],
            }
            # Pre-schedule a threshold wakeup that would be stale after meal effect
            state = load_state(ws)
            policy = load_policy(policy_path())
            state = reconcile_threshold_wakeups(
                state,
                policy,
                from_time=parse_rfc3339(state["processed_through"]),
            )
            thr_before = [
                e
                for e in state["pending_queue"]["events"]
                if e.get("payload", {}).get("reason") == "threshold:hunger"
            ]
            self.assertEqual(len(thr_before), 1)
            due_before = thr_before[0]["due_at"]
            save_state(ws, state)
            queue_activity_end(ws, activity=activity, due_at="2026-01-01T01:00:00+09:00")
            advance(ws, target="2026-01-01T01:00:00+09:00", policy_path=policy_path())
            after = load_state(ws)
            thr_after = [
                e
                for e in after["pending_queue"]["events"]
                if e.get("payload", {}).get("reason") == "threshold:hunger"
            ]
            self.assertEqual(len(thr_after), 1)
            self.assertNotEqual(due_before, thr_after[0]["due_at"])
            self.assertGreater(thr_after[0]["due_at"], due_before)

    def test_threshold_already_reached_removes_future_wakeup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp), human_state={
                "sleep_debt_min": 0,
                "hunger": 450,  # above threshold 400
                "physical_fatigue": 100,
                "affect_valence": 0,
                "stress": 100,
                "social_battery": 800,
            })
            policy = load_policy(policy_path())
            state = load_state(ws)
            # Inject a stale future wakeup
            stale = {
                "schema_version": 1,
                "event_id": "stale-hunger",
                "event_kind": "DECISION_WAKEUP",
                "due_at": "2026-01-01T05:00:00+09:00",
                "priority": 50,
                "payload": {
                    "reason": "threshold:hunger",
                    "decision_key": "threshold:hunger",
                    "field": "hunger",
                },
            }
            state = enqueue_event(state, stale)
            state = reconcile_threshold_wakeups(
                state,
                policy,
                from_time=parse_rfc3339(state["processed_through"]),
            )
            thr = [
                e
                for e in state["pending_queue"]["events"]
                if e.get("payload", {}).get("reason") == "threshold:hunger"
            ]
            self.assertEqual(thr, [])

    def test_long_vs_split_with_intervening_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            long_ws = make_workspace(root / "L")
            split_ws = make_workspace(root / "S")
            activity = {
                "schema_version": 1,
                "activity_instance_id": stable_id("act", "eq2", "meal"),
                "activity_type": "fixture_meal",
                "actual_start": "2026-01-01T01:00:00+09:00",
                "planned_end": "2026-01-01T01:30:00+09:00",
                "location_id": "fixture-home",
                "summary": "meal",
                "effects_on_end": [
                    {
                        "schema_version": 1,
                        "effect_type": "HUMAN_STATE_DELTA",
                        "payload": {"hunger": -50},
                    }
                ],
            }
            for ws in (long_ws, split_ws):
                queue_activity_end(ws, activity=deepcopy(activity), due_at="2026-01-01T01:30:00+09:00")
            target = "2026-01-01T04:00:00+09:00"
            advance(long_ws, target=target, policy_path=policy_path())
            for t in ("2026-01-01T01:30:00+09:00", "2026-01-01T02:30:00+09:00", target):
                advance(split_ws, target=t, policy_path=policy_path())
            self.assertEqual(
                semantic_state_view(load_state(long_ws)),
                semantic_state_view(load_state(split_ws)),
            )
            long_days = sorted((long_ws / "timeline").glob("*.json"))
            split_days = sorted((split_ws / "timeline").glob("*.json"))
            self.assertEqual([p.name for p in long_days], [p.name for p in split_days])
            for lp, sp in zip(long_days, split_days):
                self.assertEqual(
                    json.loads(lp.read_text(encoding="utf-8")),
                    json.loads(sp.read_text(encoding="utf-8")),
                )

class SemanticEquivalenceTests(unittest.TestCase):
    def test_oneshot_vs_split_semantic_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = make_workspace(root / "one")
            split = make_workspace(root / "split")
            target = "2026-01-01T03:17:41+09:00"
            advance(one, target=target, policy_path=policy_path())
            for t in (
                "2026-01-01T00:41:07+09:00",
                "2026-01-01T01:55:19+09:00",
                target,
            ):
                advance(split, target=t, policy_path=policy_path())
            self.assertEqual(
                semantic_state_view(load_state(one)),
                semantic_state_view(load_state(split)),
            )
            self.assertEqual(
                canonical_hash(semantic_state_view(load_state(one))),
                canonical_hash(semantic_state_view(load_state(split))),
            )

class FailClosedCeilingTests(unittest.TestCase):
    def test_max_events_per_advance_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Tiny ceiling policy
            policy = load_policy(policy_path())
            policy = deepcopy(policy)
            policy["idle_reassessment"]["max_events_per_advance"] = 1
            policy["idle_reassessment"]["interval_min"] = 30
            ppath = Path(tmp) / "policy.json"
            ppath.write_text(json.dumps(policy), encoding="utf-8")
            ws = make_workspace(Path(tmp) / "ws")
            before = snapshot_workspace_bytes(ws)
            with self.assertRaises(LifeEngineError) as ctx:
                advance(ws, target="2026-01-01T03:00:00+09:00", policy_path=ppath)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
            self.assertIn("max_events_per_advance", ctx.exception.detail)
            self.assertEqual(before, snapshot_workspace_bytes(ws))

class DeterministicClosedAtTests(unittest.TestCase):
    def test_oneshot_vs_split_closed_at_across_midnight(self):
        """Daytime event, no overnight — closed_at must be next-day midnight either path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            one = make_workspace(root / "one")
            split = make_workspace(root / "split")
            activity = {
                "schema_version": 1,
                "activity_instance_id": stable_id("act", "day", "1"),
                "activity_type": "fixture_day",
                "actual_start": "2026-01-01T12:00:00+09:00",
                "expected_end": "2026-01-01T12:30:00+09:00",
                "location_id": "fixture-home",
                "summary": "daytime",
                "effects_on_end": [],
            }
            for ws in (one, split):
                queue_activity_end(ws, activity=deepcopy(activity), due_at="2026-01-01T12:30:00+09:00")
            advance(one, target="2026-01-02T06:00:00+09:00", policy_path=policy_path())
            advance(split, target="2026-01-01T12:30:00+09:00", policy_path=policy_path())
            advance(split, target="2026-01-02T01:00:00+09:00", policy_path=policy_path())
            advance(split, target="2026-01-02T06:00:00+09:00", policy_path=policy_path())
            d1 = json.loads((one / "timeline" / "2026-01-01.json").read_text(encoding="utf-8"))
            d2 = json.loads((split / "timeline" / "2026-01-01.json").read_text(encoding="utf-8"))
            self.assertEqual(d1, d2)
            self.assertEqual(d1["status"], "CLOSED")
            self.assertEqual(d1["closed_at"], "2026-01-02T00:00:00+09:00")
            self.assertEqual(format_rfc3339(logical_closed_at(d1)), "2026-01-02T00:00:00+09:00")

class RemainderCoverageTests(unittest.TestCase):
    def test_signed_valence_partition_invariant(self):
        base = {
            "sleep_debt_min": 0,
            "hunger": 100,
            "physical_fatigue": 100,
            "affect_valence": 200,
            "stress": 100,
            "social_battery": 800,
        }
        rates = {
            "hunger": 0,
            "physical_fatigue": 0,
            "affect_valence": -17,  # toward neutral
            "stress": 0,
            "social_battery": 3,
        }
        rem0 = empty_rate_remainders()
        one, rem_one = integrate_rates(base, rates=rates, elapsed_seconds=5000, remainders=rem0)
        hs, rem = base, rem0
        for chunk in (1300, 1700, 2000):
            hs, rem = integrate_rates(hs, rates=rates, elapsed_seconds=chunk, remainders=rem)
        assert_partition_equivalent(
            whole_state=one,
            whole_remainders=rem_one,
            partitioned_state=hs,
            partitioned_remainders=rem,
        )
        self.assertIn("affect_valence", rem)
        self.assertIn("social_battery", rem)

class OvernightClosedAtTests(unittest.TestCase):
    def test_overnight_closed_at_is_finalize_end_not_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            early = make_workspace(root / "early")
            late = make_workspace(root / "late")
            activity = {
                "schema_version": 1,
                "activity_instance_id": stable_id("act", "overnight", "rr3"),
                "activity_type": "fixture_overnight",
                "actual_start": "2026-01-01T23:30:00+09:00",
                "expected_end": "2026-01-02T01:00:00+09:00",
                "location_id": "fixture-home",
                "summary": "overnight",
                "effects_on_end": [],
            }
            for ws in (early, late):
                state = load_state(ws)
                state["processed_through"] = "2026-01-01T23:00:00+09:00"
                save_state(ws, state)
                queue_activity_end(ws, activity=deepcopy(activity), due_at="2026-01-02T01:00:00+09:00")
            advance(early, target="2026-01-02T01:00:00+09:00", policy_path=policy_path())
            advance(late, target="2026-01-02T01:00:00+09:00", policy_path=policy_path())
            advance(late, target="2026-01-02T06:00:00+09:00", policy_path=policy_path())
            d1 = json.loads((early / "timeline" / "2026-01-01.json").read_text(encoding="utf-8"))
            d2 = json.loads((late / "timeline" / "2026-01-01.json").read_text(encoding="utf-8"))
            self.assertEqual(d1["closed_at"], "2026-01-02T01:00:00+09:00")
            self.assertEqual(d1["closed_at"], d2["closed_at"])
            self.assertEqual(d1, d2)
