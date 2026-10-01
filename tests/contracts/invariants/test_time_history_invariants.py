"""Time/history invariants independent of historical review rounds."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from engine.life.checkpoint import load_checkpoint
from engine.life.clock import advance, enqueue_event, verify_workspace
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.events import (
    build_actual_event_from_activity,
    derive_event_id,
    validate_active_activity,
)
from engine.life.ids import stable_id
from engine.life.invariants import assert_unique_event_ids, validate_actual_event_times
from engine.life.timeline import append_actual_event, empty_day
from tests.support.builders.workspace import (
    load_state,
    make_workspace,
    policy_path,
    queue_activity_end,
    save_state,
)

pytestmark = pytest.mark.contract

class InvariantTests(unittest.TestCase):
    def test_actual_end_before_start_rejected(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_actual_event_times(
                {
                    "actual_start": "2026-01-01T02:00:00+09:00",
                    "actual_end": "2026-01-01T01:00:00+09:00",
                    "finalized_at": "2026-01-01T02:00:00+09:00",
                }
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_finalized_before_end_rejected(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_actual_event_times(
                {
                    "actual_start": "2026-01-01T01:00:00+09:00",
                    "actual_end": "2026-01-01T02:00:00+09:00",
                    "finalized_at": "2026-01-01T01:30:00+09:00",
                }
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_impossible_active_activity_times(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_active_activity(
                {
                    "schema_version": 1,
                    "activity_instance_id": "a1",
                    "activity_type": "x",
                    "actual_start": "2026-01-01T02:00:00+09:00",
                    "planned_end": "2026-01-01T01:00:00+09:00",
                }
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_duplicate_ledger_ids_rejected(self):
        with self.assertRaises(LifeEngineError) as ctx:
            assert_unique_event_ids(
                [{"event_id": "e1"}, {"event_id": "e1"}]
            )
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_active_already_finalized_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            activity = {
                "schema_version": 1,
                "activity_instance_id": stable_id("act", "dup", "1"),
                "activity_type": "fixture",
                "actual_start": "2026-01-01T00:10:00+09:00",
                "planned_end": "2026-01-01T00:20:00+09:00",
                "location_id": "fixture-home",
                "summary": "once",
                "effects_on_end": [],
            }
            queue_activity_end(ws, activity=activity, due_at="2026-01-01T00:20:00+09:00")
            advance(ws, target="2026-01-01T00:20:00+09:00", policy_path=policy_path())
            # Try to start same activity id again
            state = load_state(ws)
            state["context"]["active_activity"] = activity
            event = {
                "schema_version": 1,
                "event_id": stable_id("activity-end", activity["activity_instance_id"], "again"),
                "event_kind": "ACTIVITY_END",
                "due_at": "2026-01-01T00:40:00+09:00",
                "priority": 10,
                "payload": {"activity_instance_id": activity["activity_instance_id"]},
            }
            state = enqueue_event(state, event)
            save_state(ws, state)
            # Rejected on advance/load verify even for a target before ACTIVITY_END due
            with self.assertRaises(LifeEngineError) as ctx:
                advance(ws, target="2026-01-01T00:30:00+09:00", policy_path=policy_path())
            self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_monotonic_append_rejected(self):
        day = empty_day("2026-01-01")
        e1 = {
            "schema_version": 1,
            "event_id": "e-late",
            "activity_instance_id": "inst-late",
            "event_type": "x",
            "actual_start": "2026-01-01T12:00:00+09:00",
            "actual_end": "2026-01-01T12:00:00+09:00",
            "finalized_at": "2026-01-01T12:00:00+09:00",
            "captures": [],
            "provenance": {"origin": "ACTUAL_EVENT"},
        }
        e0 = {
            "schema_version": 1,
            "event_id": "e-early",
            "activity_instance_id": "inst-early",
            "event_type": "x",
            "actual_start": "2026-01-01T10:00:00+09:00",
            "actual_end": "2026-01-01T10:00:00+09:00",
            "finalized_at": "2026-01-01T10:00:00+09:00",
            "captures": [],
            "provenance": {"origin": "ACTUAL_EVENT"},
        }
        day = append_actual_event(day, e1)
        with self.assertRaises(LifeEngineError) as ctx:
            append_actual_event(day, e0)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

class LoadInvariantTests(unittest.TestCase):
    def test_load_rejects_impossible_active_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            state["context"]["active_activity"] = {
                "schema_version": 1,
                "activity_instance_id": "bad",
                "activity_type": "x",
                "actual_start": "2026-01-01T02:00:00+09:00",
                "planned_end": "2026-01-01T01:00:00+09:00",
            }
            (ws / "current_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(LifeEngineError) as ctx:
                load_checkpoint(ws / "current_state.json")
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_verify_rejects_active_already_finalized_without_processing_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            activity = {
                "schema_version": 1,
                "activity_instance_id": stable_id("act", "fin", "1"),
                "activity_type": "fixture",
                "actual_start": "2026-01-01T00:10:00+09:00",
                "expected_end": "2026-01-01T00:20:00+09:00",
                "location_id": "fixture-home",
                "summary": "once",
                "effects_on_end": [],
            }
            queue_activity_end(ws, activity=activity, due_at="2026-01-01T00:20:00+09:00")
            advance(ws, target="2026-01-01T00:20:00+09:00", policy_path=policy_path())
            state = load_state(ws)
            state["context"]["active_activity"] = activity
            # Queue may include a matching ACTIVITY_END; zero is also valid for open-ended.
            state["pending_queue"]["events"] = [
                {
                    "schema_version": 1,
                    "event_id": stable_id("activity-end", activity["activity_instance_id"], "reopen"),
                    "event_kind": "ACTIVITY_END",
                    "due_at": "2026-01-01T00:40:00+09:00",
                    "priority": 10,
                    "payload": {"activity_instance_id": activity["activity_instance_id"]},
                }
            ]
            save_state(ws, state)
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)
            with self.assertRaises(LifeEngineError) as ctx2:
                advance(ws, target="2026-01-01T00:25:00+09:00", policy_path=policy_path())
            self.assertEqual(ctx2.exception.code, ErrorCode.DUPLICATE_ID)

class DistinctIdTests(unittest.TestCase):
    def test_activity_instance_id_differs_from_event_id(self):
        activity = {
            "schema_version": 1,
            "activity_instance_id": stable_id("activity", "fixture", "walk"),
            "activity_type": "walk",
            "actual_start": "2026-01-01T00:00:00+09:00",
            "expected_end": "2026-01-01T00:30:00+09:00",
        }
        event = build_actual_event_from_activity(
            activity,
            actual_end="2026-01-01T00:30:00+09:00",
            finalized_at="2026-01-01T00:30:00+09:00",
        )
        self.assertEqual(event["activity_instance_id"], activity["activity_instance_id"])
        self.assertNotEqual(event["event_id"], event["activity_instance_id"])
        self.assertEqual(
            event["event_id"],
            derive_event_id(
                activity["activity_instance_id"],
                actual_start=activity["actual_start"],
                actual_end="2026-01-01T00:30:00+09:00",
            ),
        )
        # Stable across calls
        event2 = build_actual_event_from_activity(
            activity,
            actual_end="2026-01-01T00:30:00+09:00",
            finalized_at="2026-01-01T00:30:00+09:00",
        )
        self.assertEqual(event["event_id"], event2["event_id"])
