"""Foundation contract tests for schemas, canonical serialization, and time."""

from __future__ import annotations

from datetime import datetime
import json
import unittest

import pytest

from engine.life.bootstrap import hash_proposal
from engine.life.canonical import canonical_bytes, canonical_hash, canonical_json
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.schema import validate_instance
from engine.life.timeutil import (
    canonicalize_timestamp,
    ensure_aware,
    parse_rfc3339,
)
from tests.support.constants import POLICY_PATH, PROPOSAL_PATH

pytestmark = pytest.mark.contract


class SchemaTests(unittest.TestCase):
    def test_fixture_policy_validates(self):
        data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        validate_instance(data, "behavior_policy")
        self.assertEqual(data["policy_mode"], "TEST_FIXTURE")

    def test_unknown_field_rejected(self):
        data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        data["unexpected_field"] = 1
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(data, "behavior_policy")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_proposal_validates(self):
        data = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
        validate_instance(data, "bootstrap_proposal")

    def test_effect_payload_strict(self):
        with self.assertRaises(LifeEngineError):
            validate_instance(
                {
                    "schema_version": 1,
                    "effect_type": "HUMAN_STATE_DELTA",
                    "payload": {"hunger": 1, "extra": 2},
                },
                "effect",
            )


class TimeTests(unittest.TestCase):
    def test_naive_timestamp_rejected(self):
        with self.assertRaises(LifeEngineError) as ctx:
            ensure_aware(datetime(2026, 1, 1, 0, 0, 0), field="t")
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_rfc3339_tokyo(self):
        dt = parse_rfc3339("2026-01-01T00:00:00+09:00")
        self.assertEqual(str(dt.tzinfo), "Asia/Tokyo")

    def test_canonical_timestamp_equivalence(self):
        a = canonicalize_timestamp("2026-01-01T00:00:00Z")
        b = canonicalize_timestamp("2026-01-01T09:00:00+09:00")
        self.assertEqual(a, b)
        self.assertEqual(a, "2026-01-01T09:00:00+09:00")

    def test_proposal_hash_equivalent_timestamps(self):
        base = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))
        utc = dict(base)
        utc["proposed_at"] = "2025-12-31T15:00:00Z"
        utc["life_epoch"] = "2025-12-31T15:00:00Z"
        tokyo = dict(base)
        tokyo["proposed_at"] = "2026-01-01T00:00:00+09:00"
        tokyo["life_epoch"] = "2026-01-01T00:00:00+09:00"
        self.assertEqual(hash_proposal(utc), hash_proposal(tokyo))


class CanonicalTests(unittest.TestCase):
    def test_key_order_independent(self):
        a = {"z": 1, "a": 2}
        b = {"a": 2, "z": 1}
        self.assertEqual(canonical_json(a), canonical_json(b))
        self.assertEqual(canonical_hash(a), canonical_hash(b))

    def test_nfc_normalization(self):
        composed = "é"
        decomposed = "e\u0301"
        self.assertEqual(canonical_hash(composed), canonical_hash(decomposed))
        self.assertEqual(canonical_bytes(composed), canonical_bytes(decomposed))

    def test_binary_float_forbidden(self):
        with self.assertRaises(TypeError):
            canonical_hash({"x": 1.5})
