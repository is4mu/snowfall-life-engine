"""Active-activity temporal, queue-order, and timeline canonicality contracts."""

from __future__ import annotations

import json
import tempfile
import unittest

import pytest
from copy import deepcopy
from pathlib import Path

from engine.life.canonical import normalize_persisted_object, persisted_hash
from engine.life.checkpoint import write_checkpoint
from engine.life.clock import enqueue_event, verify_workspace
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.invariants import validate_pending_queue
from engine.life.timeline import close_day, empty_day

from tests.support.builders.workspace import load_state, make_workspace, save_state

pytestmark = pytest.mark.contract


def _event(**kwargs):
    base = {
        "schema_version": 1,
        "event_id": "e1",
        "activity_instance_id": "inst-1",
        "event_type": "fixture",
        "actual_start": "2026-01-01T12:00:00+09:00",
        "actual_end": "2026-01-01T12:30:00+09:00",
        "finalized_at": "2026-01-01T12:30:00+09:00",
        "captures": [],
        "provenance": {"origin": "ACTUAL_EVENT"},
        "participants": [],
        "causes": [],
    }
    base.update(kwargs)
    return normalize_persisted_object(base)


class ZeroOrOneActivityEndTests(unittest.TestCase):
    def test_active_with_window_and_no_activity_end_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            state["context"]["active_activity"] = {
                "schema_version": 1,
                "activity_instance_id": "sleep-1",
                "activity_type": "sleep",
                "actual_start": "2026-01-01T00:00:00+09:00",
                "expected_end_window": {
                    "earliest": "2026-01-01T06:00:00+09:00",
                    "latest": "2026-01-01T09:00:00+09:00",
                },
            }
            state["pending_queue"]["events"] = [
                {
                    "schema_version": 1,
                    "event_id": "wake-reassess",
                    "event_kind": "DECISION_WAKEUP",
                    "due_at": "2026-01-01T06:00:00+09:00",
                    "priority": 40,
                    "payload": {
                        "reason": "idle_reassessment",
                        "decision_key": "sleep-1:reassess",
                    },
                }
            ]
            write_checkpoint(ws / "current_state.json", state)
            verify_workspace(ws)

    def test_active_with_one_matching_end_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            state["context"]["active_activity"] = {
                "schema_version": 1,
                "activity_instance_id": "walk-1",
                "activity_type": "walk",
                "actual_start": "2026-01-01T00:00:00+09:00",
                "expected_end": "2026-01-01T00:30:00+09:00",
            }
            state["pending_queue"]["events"] = [
                {
                    "schema_version": 1,
                    "event_id": "end-walk-1",
                    "event_kind": "ACTIVITY_END",
                    "due_at": "2026-01-01T00:30:00+09:00",
                    "priority": 10,
                    "payload": {"activity_instance_id": "walk-1"},
                }
            ]
            write_checkpoint(ws / "current_state.json", state)
            verify_workspace(ws)

    def test_mismatched_and_multiple_ends_fail(self):
        state = {
            "processed_through": "2026-01-01T00:00:00+09:00",
            "context": {
                "active_activity": {
                    "schema_version": 1,
                    "activity_instance_id": "walk-1",
                    "activity_type": "walk",
                    "actual_start": "2026-01-01T00:00:00+09:00",
                }
            },
            "pending_queue": {
                "cursor": 0,
                "events": [
                    {
                        "schema_version": 1,
                        "event_id": "end-other",
                        "event_kind": "ACTIVITY_END",
                        "due_at": "2026-01-01T00:30:00+09:00",
                        "priority": 10,
                        "payload": {"activity_instance_id": "other"},
                    }
                ],
            },
        }
        with self.assertRaises(LifeEngineError) as ctx:
            validate_pending_queue(state)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

        state["pending_queue"]["events"] = [
            {
                "schema_version": 1,
                "event_id": "end-a",
                "event_kind": "ACTIVITY_END",
                "due_at": "2026-01-01T00:20:00+09:00",
                "priority": 10,
                "payload": {"activity_instance_id": "walk-1"},
            },
            {
                "schema_version": 1,
                "event_id": "end-b",
                "event_kind": "ACTIVITY_END",
                "due_at": "2026-01-01T00:30:00+09:00",
                "priority": 10,
                "payload": {"activity_instance_id": "walk-1"},
            },
        ]
        with self.assertRaises(LifeEngineError) as ctx2:
            validate_pending_queue(state)
        self.assertEqual(ctx2.exception.code, ErrorCode.DUPLICATE_ID)

    def test_temporal_invariants(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            state["processed_through"] = "2026-01-01T01:00:00+09:00"
            state["context"]["active_activity"] = {
                "schema_version": 1,
                "activity_instance_id": "future",
                "activity_type": "x",
                "actual_start": "2026-01-01T02:00:00+09:00",
            }
            with self.assertRaises(LifeEngineError) as ctx:
                write_checkpoint(ws / "current_state.json", state)
            self.assertIn("actual_start", ctx.exception.detail)

            state = load_state(ws)
            state["processed_through"] = "2026-01-01T01:00:00+09:00"
            state["pending_queue"]["events"] = [
                {
                    "schema_version": 1,
                    "event_id": "past",
                    "event_kind": "IDLE_REASSESSMENT",
                    "due_at": "2026-01-01T00:30:00+09:00",
                    "priority": 100,
                    "payload": {"interval_min": 120},
                }
            ]
            with self.assertRaises(LifeEngineError) as ctx2:
                write_checkpoint(ws / "current_state.json", state)
            self.assertIn("due_at", ctx2.exception.detail)


class QueueOrderTests(unittest.TestCase):
    def test_reversed_queue_rejected_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            a = {
                "schema_version": 1,
                "event_id": "aaa",
                "event_kind": "IDLE_REASSESSMENT",
                "due_at": "2026-01-01T01:00:00+09:00",
                "priority": 10,
                "payload": {"interval_min": 120},
            }
            b = {
                "schema_version": 1,
                "event_id": "zzz",
                "event_kind": "IDLE_REASSESSMENT",
                "due_at": "2026-01-01T01:00:00+09:00",
                "priority": 10,
                "payload": {"interval_min": 120},
            }
            state = enqueue_event(state, a)
            state = enqueue_event(state, b)
            # Corrupt: reverse canonical order
            state["pending_queue"]["events"] = list(reversed(state["pending_queue"]["events"]))
            with self.assertRaises(LifeEngineError) as ctx:
                write_checkpoint(ws / "current_state.json", state)
            self.assertIn("canonical order", ctx.exception.detail)


class TotalUnorderedSortTests(unittest.TestCase):
    def test_tied_participant_primary_keys_hash_equal(self):
        a = {
            "participants": [
                {"entity_id": "a", "role": "x"},
                {"entity_id": "a", "role": "y"},
            ]
        }
        b = {
            "participants": [
                {"entity_id": "a", "role": "y"},
                {"entity_id": "a", "role": "x"},
            ]
        }
        self.assertEqual(persisted_hash(a), persisted_hash(b))
        na = normalize_persisted_object(a)
        self.assertEqual(na["participants"][0]["role"], "x")  # canonical_json tie-break

    def test_tied_condition_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            cond = {
                "schema_version": 1,
                "condition_id": "same",
                "condition_type": "cold",
                "severity": "LOW",
                "started_at": "2026-01-01T00:00:00+09:00",
                "reassess_at": None,
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }
            state["conditions"] = [cond, {**cond, "severity": "HIGH"}]
            with self.assertRaises(LifeEngineError) as ctx:
                write_checkpoint(ws / "current_state.json", state)
            self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)


class TimelineCanonicalPersistenceTests(unittest.TestCase):
    def test_noncanonical_timestamp_offset_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            day = empty_day("2026-01-01")
            day["actual_events"] = [
                _event(
                    actual_start="2026-01-01T12:00:00+09:00",
                    actual_end="2026-01-01T12:30:00+09:00",
                    finalized_at="2026-01-01T12:30:00+09:00",
                )
            ]
            day = close_day(day)
            # Corrupt: equivalent Z spelling instead of canonical +09:00
            day["actual_events"][0]["actual_start"] = "2026-01-01T03:00:00Z"
            path = ws / "timeline" / "2026-01-01.json"
            path.write_text(json.dumps(day, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            state = load_state(ws)
            state["processed_through"] = "2026-01-02T06:00:00+09:00"
            # Keep history inconsistent aside — verify should fail on non-canonical day first.
            (ws / "current_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertIn("non-canonical", ctx.exception.detail)

    def test_reversed_participants_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            day = empty_day("2026-01-01")
            ev = _event(
                participants=[
                    {"entity_id": "a", "role": "lead"},
                    {"entity_id": "b", "role": "friend"},
                ]
            )
            # _event already normalized; reverse after
            ev["participants"] = list(reversed(ev["participants"]))
            day["actual_events"] = [ev]
            day = close_day(day)
            path = ws / "timeline" / "2026-01-01.json"
            path.write_text(json.dumps(day, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            state = load_state(ws)
            state["processed_through"] = "2026-01-02T06:00:00+09:00"
            (ws / "current_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertIn("non-canonical", ctx.exception.detail)
