"""Timestamp and checkpoint serialization contracts."""

from __future__ import annotations

import unittest
from copy import deepcopy

import pytest

from engine.life.canonical import canonical_hash
from engine.life.checkpoint import empty_checkpoint
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.schema import validate_instance
from engine.life.timeutil import canonicalize_timestamps

pytestmark = pytest.mark.contract

class TimestampCanonTests(unittest.TestCase):
    def test_condition_and_expected_end_utc_equivalence(self):
        a = {
            "conditions": [
                {
                    "schema_version": 1,
                    "condition_id": "c1",
                    "condition_type": "fixture",
                    "severity": "LOW",
                    "started_at": "2025-12-31T15:00:00Z",
                    "reassess_at": "2026-01-01T03:00:00Z",
                    "provenance": {"origin": "SIMULATION_BOOTSTRAP"},
                }
            ],
            "context": {
                "location_id": "fixture-home",
                "active_activity": {
                    "schema_version": 1,
                    "activity_instance_id": "act-1",
                    "activity_type": "fixture",
                    "actual_start": "2026-01-01T00:00:00+09:00",
                    "expected_end": "2025-12-31T16:30:00Z",
                    "expected_end_window": {
                        "earliest": "2025-12-31T16:00:00Z",
                        "latest": "2025-12-31T17:00:00Z",
                    },
                },
            },
        }
        b = deepcopy(a)
        b["conditions"][0]["started_at"] = "2026-01-01T00:00:00+09:00"
        b["conditions"][0]["reassess_at"] = "2026-01-01T12:00:00+09:00"
        b["context"]["active_activity"]["expected_end"] = "2026-01-01T01:30:00+09:00"
        b["context"]["active_activity"]["expected_end_window"] = {
            "earliest": "2026-01-01T01:00:00+09:00",
            "latest": "2026-01-01T02:00:00+09:00",
        }
        ca = canonicalize_timestamps(a)
        cb = canonicalize_timestamps(b)
        self.assertEqual(ca["conditions"][0]["started_at"], "2026-01-01T00:00:00+09:00")
        self.assertEqual(ca["conditions"], cb["conditions"])
        self.assertEqual(
            ca["context"]["active_activity"]["expected_end"],
            cb["context"]["active_activity"]["expected_end"],
        )
        self.assertEqual(
            ca["context"]["active_activity"]["expected_end_window"],
            cb["context"]["active_activity"]["expected_end_window"],
        )
        self.assertEqual(canonical_hash(ca), canonical_hash(cb))

    def test_subsecond_rejected(self):
        with self.assertRaises(LifeEngineError) as ctx:
            canonicalize_timestamps({"due_at": "2026-01-01T00:00:00.5+09:00"})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

class AppearanceNamesTests(unittest.TestCase):
    def test_checkpoint_uses_outfit_hair_state_id(self):
        state = empty_checkpoint(
            character_id="fixture-character",
            life_epoch="2026-01-01T00:00:00+09:00",
            world_seed="s",
            behavior_policy_version="fixture-policy-1",
            engine_commit_sha="0" * 40,
            location_id="fixture-home",
            human_state={
                "sleep_debt_min": 0,
                "hunger": 0,
                "physical_fatigue": 0,
                "affect_valence": 0,
                "stress": 0,
                "social_battery": 0,
            },
        )
        self.assertIn("outfit_state_id", state["appearance"])
        self.assertIn("hair_state_id", state["appearance"])
        self.assertNotIn("outfit_state_ref", state["appearance"])

class StrictQueuePayloadTests(unittest.TestCase):
    def test_activity_end_requires_instance_id_only(self):
        ok = {
            "schema_version": 1,
            "event_id": "q1",
            "event_kind": "ACTIVITY_END",
            "due_at": "2026-01-01T01:00:00+09:00",
            "priority": 10,
            "payload": {"activity_instance_id": "act-1"},
        }
        validate_instance(ok, "queued_internal_event")
        bad = deepcopy(ok)
        bad["payload"] = {"activity_instance_id": "act-1", "extra": True}
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(bad, "queued_internal_event")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_decision_wakeup_requires_reason_and_decision_key(self):
        ok = {
            "schema_version": 1,
            "event_id": "q2",
            "event_kind": "DECISION_WAKEUP",
            "due_at": "2026-01-01T01:00:00+09:00",
            "priority": 20,
            "payload": {
                "reason": "threshold:hunger",
                "decision_key": "threshold:hunger",
                "field": "hunger",
            },
        }
        validate_instance(ok, "queued_internal_event")
        missing_field = deepcopy(ok)
        missing_field["payload"] = {
            "reason": "threshold:hunger",
            "decision_key": "threshold:hunger",
        }
        with self.assertRaises(LifeEngineError):
            validate_instance(missing_field, "queued_internal_event")
        generic = {
            "schema_version": 1,
            "event_id": "q2b",
            "event_kind": "DECISION_WAKEUP",
            "due_at": "2026-01-01T01:00:00+09:00",
            "priority": 20,
            "payload": {
                "reason": "commitment_departure",
                "decision_key": "commit:abc:depart",
                "source_kind": "commitment",
                "source_ref": "commit-abc",
            },
        }
        validate_instance(generic, "queued_internal_event")

    def test_idle_reassessment_requires_interval(self):
        ok = {
            "schema_version": 1,
            "event_id": "q3",
            "event_kind": "IDLE_REASSESSMENT",
            "due_at": "2026-01-01T01:00:00+09:00",
            "priority": 50,
            "payload": {"interval_min": 90},
        }
        validate_instance(ok, "queued_internal_event")

    def test_unsupported_kind_and_threshold_check_fail_closed(self):
        for kind in ("THRESHOLD_CHECK", "UNKNOWN_KIND"):
            bad = {
                "schema_version": 1,
                "event_id": "q-bad",
                "event_kind": kind,
                "due_at": "2026-01-01T01:00:00+09:00",
                "priority": 1,
                "payload": {},
            }
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(bad, "queued_internal_event")
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)
