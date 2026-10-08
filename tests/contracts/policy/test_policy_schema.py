"""Behavior-policy schema, dispatch, and approval contracts."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import pytest

from engine.life.canonical import canonical_hash
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.policy import (
    assert_policy_allowed,
    assert_policy_approved,
    assert_policy_matches_checkpoint,
    hash_policy,
    load_policy,
    normalize_policy,
)
from engine.life.schema import reject_binary_floats, validate_instance
from tests.support.constants import POLICY_PATH, POLICY_V2_CANDIDATE_PATH


def _v1() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def _v2() -> dict:
    return json.loads(POLICY_V2_CANDIDATE_PATH.read_text(encoding="utf-8"))


pytestmark = pytest.mark.contract


class PolicyDispatchTests(unittest.TestCase):
    def test_v1_fixture_valid(self):
        data = _v1()
        validate_instance(data, "behavior_policy")
        loaded = load_policy(POLICY_PATH)
        self.assertEqual(loaded["schema_version"], 1)
        self.assertEqual(loaded["policy_mode"], "TEST_FIXTURE")

    def test_v1_production_invalid(self):
        data = _v1()
        data["policy_mode"] = "PRODUCTION"
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(data, "behavior_policy")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_v2_test_fixture_invalid(self):
        data = _v2()
        data["policy_mode"] = "TEST_FIXTURE"
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(data, "behavior_policy")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_v2_candidate_valid(self):
        data = _v2()
        validate_instance(data, "behavior_policy")
        loaded = load_policy(POLICY_V2_CANDIDATE_PATH)
        self.assertEqual(loaded["schema_version"], 2)
        self.assertEqual(loaded["policy_mode"], "PRODUCTION")
        self.assertEqual(loaded["character_id"], "fixture-character")
        self.assertTrue(str(loaded["behavior_policy_version"]).startswith("public-fixture-"))

    def test_unknown_schema_version_invalid(self):
        data = _v1()
        data["schema_version"] = 99
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_VERSION)

    def test_binary_float_invalid(self):
        data = _v2()
        data["time_resolution"]["human_quantum_min"] = 5.0
        with self.assertRaises(LifeEngineError) as ctx:
            reject_binary_floats(data)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        with self.assertRaises(LifeEngineError) as ctx2:
            normalize_policy(data)
        self.assertEqual(ctx2.exception.code, ErrorCode.INVALID_STATE)

    def test_unknown_field_invalid(self):
        data = _v2()
        data["unexpected_top_level"] = 1
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(data, "behavior_policy")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_production_mode_rejects_v1_fixture(self):
        policy = load_policy(POLICY_PATH)
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_allowed(policy, production_mode=True)
        self.assertEqual(ctx.exception.code, ErrorCode.FIXTURE_POLICY_FORBIDDEN)

    def test_loading_v2_is_not_approval(self):
        policy = load_policy(POLICY_V2_CANDIDATE_PATH)
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_approved(policy, None)
        self.assertEqual(ctx.exception.code, ErrorCode.POLICY_NOT_APPROVED)

    def test_checkpoint_version_match_preserved(self):
        policy = load_policy(POLICY_PATH)
        state = {
            "behavior_policy_version": policy["behavior_policy_version"],
            "character_id": policy["character_id"],
            "timezone": policy["timezone"],
        }
        assert_policy_matches_checkpoint(state, policy)
        state["behavior_policy_version"] = "other"
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_matches_checkpoint(state, policy)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_VERSION)

class PolicyApprovalTests(unittest.TestCase):
    def _approval_for(self, policy: dict) -> dict:
        digest = hash_policy(policy)
        return {
            "schema_version": 1,
            "behavior_policy_version": policy["behavior_policy_version"],
            "policy_hash": digest,
            "approved_at": "2026-01-01T00:00:00+09:00",
            "approved_by": "synthetic-reviewer",
            "review_ref": "synthetic-review-1",
        }

    def test_synthetic_approval_ok(self):
        policy = _v2()
        approval = self._approval_for(policy)
        assert_policy_approved(policy, approval)

    def test_v1_fixture_approval_rejected(self):
        policy = _v1()
        # Build a hash-matching synthetic approval; gate must still reject fixture.
        from engine.life.canonical import canonical_hash
        from engine.life.policy import normalize_policy as norm

        normalized = norm(policy)
        approval = {
            "schema_version": 1,
            "behavior_policy_version": normalized["behavior_policy_version"],
            "policy_hash": canonical_hash(normalized),
            "approved_at": "2026-01-01T00:00:00+09:00",
            "approved_by": "synthetic-reviewer",
            "review_ref": "synthetic-review-v1",
        }
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_approved(policy, approval)
        self.assertEqual(ctx.exception.code, ErrorCode.POLICY_NOT_APPROVED)

    def test_missing_approval(self):
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_approved(_v2(), None)
        self.assertEqual(ctx.exception.code, ErrorCode.POLICY_NOT_APPROVED)

    def test_wrong_hash(self):
        policy = _v2()
        approval = self._approval_for(policy)
        approval["policy_hash"] = "0" * 64
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_approved(policy, approval)
        self.assertEqual(ctx.exception.code, ErrorCode.POLICY_HASH_MISMATCH)

    def test_version_mismatch(self):
        policy = _v2()
        approval = self._approval_for(policy)
        approval["behavior_policy_version"] = "other-version"
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_approved(policy, approval)
        self.assertEqual(ctx.exception.code, ErrorCode.POLICY_HASH_MISMATCH)

    def test_one_value_change_invalidates(self):
        policy = _v2()
        approval = self._approval_for(policy)
        assert_policy_approved(policy, approval)
        policy2 = copy.deepcopy(policy)
        policy2["decision_thresholds"]["near_equal_margin_permille"] = 51
        self.assertNotEqual(hash_policy(policy), hash_policy(policy2))
        with self.assertRaises(LifeEngineError) as ctx:
            assert_policy_approved(policy2, approval)
        self.assertEqual(ctx.exception.code, ErrorCode.POLICY_HASH_MISMATCH)

    def test_approval_not_committed_with_public_fixtures(self):
        fixtures = Path(__file__).resolve().parents[2] / "fixtures" / "policy"
        self.assertFalse(any(fixtures.glob("*approval*")))

    def test_temp_approval_roundtrip(self):
        policy = _v2()
        approval = self._approval_for(policy)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "approval.json"
            path.write_text(json.dumps(approval) + "\n", encoding="utf-8")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            assert_policy_approved(policy, loaded)
            self.assertEqual(loaded["policy_hash"], canonical_hash(normalize_policy(policy)))
