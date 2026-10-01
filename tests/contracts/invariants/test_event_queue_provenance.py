"""Event queue, provenance, timeline-day, and canonical persistence contracts."""

from __future__ import annotations

import json
import tempfile
import unittest

import pytest
from copy import deepcopy
from pathlib import Path

from engine.life.canonical import persisted_hash, normalize_persisted_object
from engine.life.checkpoint import write_checkpoint
from engine.life.clock import enqueue_event, verify_workspace
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.invariants import validate_active_activity_times, validate_pending_queue
from engine.life.schema import validate_instance
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
    }
    base.update(kwargs)
    return base


class TimelineDaySemanticTests(unittest.TestCase):
    def _write_day(self, ws: Path, day: dict) -> None:
        path = ws / "timeline" / f"{day['date']}.json"
        path.write_text(json.dumps(day, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_misplaced_event_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            day = empty_day("2026-01-01")
            day["actual_events"] = [
                _event(actual_start="2026-01-02T01:00:00+09:00", actual_end="2026-01-02T01:00:00+09:00",
                       finalized_at="2026-01-02T01:00:00+09:00")
            ]
            self._write_day(ws, day)
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_incorrect_closed_at_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            day = empty_day("2026-01-01")
            day["actual_events"] = [_event()]
            day = close_day(day)
            day["closed_at"] = "2026-01-02T03:00:00+09:00"  # not logical
            self._write_day(ws, day)
            state = load_state(ws)
            state["processed_through"] = "2026-01-02T06:00:00+09:00"
            # Bypass write_checkpoint normalization of day; only update checkpoint time.
            (ws / "current_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_closed_with_null_closed_at_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            day = empty_day("2026-01-01")
            day["actual_events"] = [_event()]
            day["status"] = "CLOSED"
            day["closed_at"] = None
            self._write_day(ws, day)
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_open_with_non_null_closed_at_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            day = empty_day("2026-01-01")
            day["actual_events"] = [_event()]
            day["status"] = "OPEN"
            day["closed_at"] = "2026-01-02T00:00:00+09:00"
            self._write_day(ws, day)
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_elapsed_open_day_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            day = empty_day("2026-01-01")
            day["actual_events"] = [_event()]
            day["status"] = "OPEN"
            day["closed_at"] = None
            self._write_day(ws, day)
            state = load_state(ws)
            state["processed_through"] = "2026-01-02T06:00:00+09:00"
            (ws / "current_state.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            with self.assertRaises(LifeEngineError) as ctx:
                verify_workspace(ws)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
            self.assertIn("elapsed OPEN", ctx.exception.detail)


class PendingQueueInvariantTests(unittest.TestCase):
    def test_duplicate_queue_event_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            ev = {
                "schema_version": 1,
                "event_id": "dup",
                "event_kind": "IDLE_REASSESSMENT",
                "due_at": "2026-01-01T01:00:00+09:00",
                "priority": 100,
                "payload": {"interval_min": 120},
            }
            state = enqueue_event(state, ev)
            state["pending_queue"]["events"].append(deepcopy(ev))
            with self.assertRaises(LifeEngineError) as ctx:
                write_checkpoint(ws / "current_state.json", state)
            self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_activity_end_must_match_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            state["context"]["active_activity"] = {
                "schema_version": 1,
                "activity_instance_id": "act-real",
                "activity_type": "x",
                "actual_start": "2026-01-01T00:00:00+09:00",
            }
            state["pending_queue"]["events"] = [
                {
                    "schema_version": 1,
                    "event_id": "end-wrong",
                    "event_kind": "ACTIVITY_END",
                    "due_at": "2026-01-01T01:00:00+09:00",
                    "priority": 10,
                    "payload": {"activity_instance_id": "act-other"},
                }
            ]
            with self.assertRaises(LifeEngineError) as ctx:
                validate_pending_queue(state)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_threshold_reason_field_mismatch_rejected(self):
        state = {
            "processed_through": "2026-01-01T00:00:00+09:00",
            "context": {"active_activity": None},
            "pending_queue": {
                "cursor": 0,
                "events": [
                    {
                        "schema_version": 1,
                        "event_id": "w1",
                        "event_kind": "DECISION_WAKEUP",
                        "due_at": "2026-01-01T01:00:00+09:00",
                        "priority": 50,
                        "payload": {
                            "reason": "threshold:hunger",
                            "decision_key": "threshold:hunger",
                            "field": "stress",
                        },
                    }
                ],
            },
        }
        with self.assertRaises(LifeEngineError) as ctx:
            validate_pending_queue(state)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_cursor_nonzero_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            state["pending_queue"]["cursor"] = 1
            with self.assertRaises(LifeEngineError):
                write_checkpoint(ws / "current_state.json", state)


class UnorderedCanonicalTests(unittest.TestCase):
    def test_conditions_order_independent_hash(self):
        a = {
            "conditions": [
                {
                    "schema_version": 1,
                    "condition_id": "b",
                    "condition_type": "t",
                    "severity": "LOW",
                    "started_at": "2026-01-01T00:00:00+09:00",
                    "reassess_at": None,
                    "provenance": {"origin": "ENGINE_DEFAULT"},
                },
                {
                    "schema_version": 1,
                    "condition_id": "a",
                    "condition_type": "t",
                    "severity": "HIGH",
                    "started_at": "2026-01-01T01:00:00+09:00",
                    "reassess_at": None,
                    "provenance": {"origin": "ENGINE_DEFAULT"},
                },
            ]
        }
        b = deepcopy(a)
        b["conditions"] = list(reversed(b["conditions"]))
        self.assertEqual(persisted_hash(a), persisted_hash(b))
        na = normalize_persisted_object(a)
        self.assertEqual(na["conditions"][0]["condition_id"], "a")

    def test_participants_and_causes_order_independent(self):
        a = {
            "participants": [{"entity_id": "z"}, {"entity_id": "a"}],
            "causes": [
                {"cause_type": "x", "ref": "2"},
                {"cause_type": "x", "ref": "1"},
            ],
        }
        b = {
            "participants": [{"entity_id": "a"}, {"entity_id": "z"}],
            "causes": [
                {"cause_type": "x", "ref": "1"},
                {"cause_type": "x", "ref": "2"},
            ],
        }
        self.assertEqual(persisted_hash(a), persisted_hash(b))

    def test_timestamp_spellings_hash_equal_via_persisted_hash(self):
        a = {"due_at": "2025-12-31T15:00:00Z"}
        b = {"due_at": "2026-01-01T00:00:00+09:00"}
        self.assertEqual(persisted_hash(a), persisted_hash(b))


class GenericWakeupAndProvenanceTests(unittest.TestCase):
    def test_generic_decision_wakeup_without_field(self):
        ok = {
            "schema_version": 1,
            "event_id": "g1",
            "event_kind": "DECISION_WAKEUP",
            "due_at": "2026-01-01T08:00:00+09:00",
            "priority": 40,
            "payload": {
                "reason": "day_boundary",
                "decision_key": "day:2026-01-02",
                "source_kind": "calendar",
                "source_ref": "tokyo-midnight",
            },
        }
        validate_instance(ok, "queued_internal_event")

    def test_actual_event_requires_provenance(self):
        event = _event()
        validate_instance(event, "actual_event")
        bad = deepcopy(event)
        bad["provenance"] = None
        with self.assertRaises(LifeEngineError):
            validate_instance(bad, "actual_event")

    def test_condition_requires_severity_started_at_provenance(self):
        cond = {
            "schema_version": 1,
            "condition_id": "c1",
            "condition_type": "cold",
            "severity": "LOW",
            "started_at": "2026-01-01T00:00:00+09:00",
            "reassess_at": None,
            "provenance": {"origin": "SIMULATION_BOOTSTRAP"},
        }
        # Embed in minimal checkpoint slice via schema item check through current_state
        with tempfile.TemporaryDirectory() as tmp:
            ws = make_workspace(Path(tmp))
            state = load_state(ws)
            state["conditions"] = [cond]
            write_checkpoint(ws / "current_state.json", state)
            bad = deepcopy(cond)
            bad["severity"] = None
            state["conditions"] = [bad]
            with self.assertRaises(LifeEngineError):
                write_checkpoint(ws / "current_state.json", state)


class ExpectedEndWindowTests(unittest.TestCase):
    def test_expected_end_must_lie_in_window(self):
        activity = {
            "schema_version": 1,
            "activity_instance_id": "a1",
            "activity_type": "x",
            "actual_start": "2026-01-01T00:00:00+09:00",
            "expected_end": "2026-01-01T03:00:00+09:00",
            "expected_end_window": {
                "earliest": "2026-01-01T01:00:00+09:00",
                "latest": "2026-01-01T02:00:00+09:00",
            },
        }
        with self.assertRaises(LifeEngineError) as ctx:
            validate_active_activity_times(activity)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
