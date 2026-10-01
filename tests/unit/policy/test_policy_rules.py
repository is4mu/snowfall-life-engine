"""Behavior-policy semantic invariants for the public synthetic v2 fixture."""

from __future__ import annotations

import copy
import json
import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.policy import load_policy, normalize_policy
from tests.support.constants import POLICY_V2_CANDIDATE_PATH


def _v2() -> dict:
    return json.loads(POLICY_V2_CANDIDATE_PATH.read_text(encoding="utf-8"))


pytestmark = pytest.mark.unit


class PolicyInvariantTests(unittest.TestCase):
    def test_monotonic_bands(self):
        data = _v2()
        data["derived_bands"]["hunger"]["URGENT"] = 100
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("derived_bands.hunger", ctx.exception.detail)

    def test_min_le_max(self):
        data = _v2()
        data["meal_policy"]["duration_families"]["LIGHT"]["duration_min"] = 90
        data["meal_policy"]["duration_families"]["LIGHT"]["duration_max"] = 10
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_knot_x_strict_increasing(self):
        data = _v2()
        data["sleep_policy"]["sleep_debt_knots"][2]["x"] = 60
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("strictly increasing", ctx.exception.detail)

    def test_provenance_sorted_unique_coverage(self):
        data = _v2()
        entries = list(data["provenance_index"])
        entries.append(copy.deepcopy(entries[0]))
        data["provenance_index"] = entries
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

        data = _v2()
        data["provenance_index"] = list(reversed(data["provenance_index"]))
        with self.assertRaises(LifeEngineError) as ctx2:
            normalize_policy(data)
        self.assertIn("canonical sorted", ctx2.exception.detail)

        data = _v2()
        data["provenance_index"] = [
            e for e in data["provenance_index"] if e["path"] != "sleep_policy"
        ]
        with self.assertRaises(LifeEngineError) as ctx3:
            normalize_policy(data)
        self.assertIn("missing coverage", ctx3.exception.detail)

    def test_invite_split_1000(self):
        data = _v2()
        invite = data["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"]
        invite["same_day_split_permille"] = 400
        invite["future_split_permille"] = 650
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertIn("1000", ctx.exception.detail)

    def test_bootstrap_only_clock_fields_rejected(self):
        data = _v2()
        data["sleep_policy"]["preferred_wake_time"] = "07:00"
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        # additionalProperties on sleep_policy → SCHEMA_INVALID, or invariant if sneaked elsewhere
        self.assertIn(
            ctx.exception.code,
            {ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE},
        )

        data = _v2()
        data["meal_policy"]["skip_meal_probability"] = 10
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_person_specific_social_fields_impossible(self):
        data = _v2()
        data["social_policy"]["person_id"] = "someone"
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

        data = _v2()
        data["social_policy"]["archetypes"]["REMOTE_CLOSE_BURSTY"]["friend_id"] = "x"
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_recency_bands_contiguous_ordered(self):
        data = _v2()
        bands = data["social_policy"]["archetypes"]["REMOTE_CLOSE_BURSTY"]["recency_multipliers"]
        bands[1]["min_hours_inclusive"] = 30  # gap/overlap vs prior max 24
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertIn("recency", ctx.exception.detail)

        data = _v2()
        bands = data["social_policy"]["archetypes"]["REMOTE_CLOSE_BURSTY"]["recency_multipliers"]
        bands[0]["max_hours_exclusive"] = None  # null max not final
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_activation_permille_bounds(self):
        data = _v2()
        data["social_policy"]["archetypes"]["REMOTE_CLOSE_BURSTY"]["base_activation_permille"] = 1200
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_sleep_knot_y_bounds_and_monotonic(self):
        data = _v2()
        data["sleep_policy"]["homeostatic_awake_fraction_knots"][-1]["y"] = 1001
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertIn("homeostatic", ctx.exception.detail)

        data = _v2()
        knots = data["sleep_policy"]["sleep_debt_knots"]
        knots[2]["y"] = knots[1]["y"] - 1
        with self.assertRaises(LifeEngineError) as ctx2:
            normalize_policy(data)
        self.assertIn("nondecreasing", ctx2.exception.detail)

    def test_affect_and_stress_magnitude_labels(self):
        data = _v2()
        data["state_dynamics"]["affect_impulses"]["MEDIUM"] = 10
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertIn("SMALL < MEDIUM < LARGE", ctx.exception.detail)

        data = _v2()
        data["state_dynamics"]["stress_impulses"]["RELIEF_MINOR"] = 5
        with self.assertRaises(LifeEngineError) as ctx2:
            normalize_policy(data)
        self.assertIn("RELIEF", ctx2.exception.detail)

        data = _v2()
        data["state_dynamics"]["stress_impulses"]["MINOR"] = 0
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_wake_variation_no_duplicate_quantum(self):
        data = _v2()
        data["sleep_policy"]["wake_variation"]["quantum_min"] = 5
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_calibration_generation_integer(self):
        data = _v2()
        data["calibration_metadata"]["calibration_generation"] = "not-an-int"
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_public_fixture_uses_synthetic_engine_provenance(self):
        data = load_policy(POLICY_V2_CANDIDATE_PATH)
        by_path = {entry["path"]: entry for entry in data["provenance_index"]}
        self.assertEqual(set(by_path), {
            "behavior_modes",
            "capture_policy",
            "decision_thresholds",
            "derived_bands",
            "domain_policy",
            "engine_safety",
            "meal_policy",
            "planning_policy",
            "schedule_generation",
            "sleep_policy",
            "social_policy",
            "state_dynamics",
            "time_resolution",
        })
        for path, entry in by_path.items():
            self.assertEqual(entry["origin"], "ENGINE_DEFAULT")
            self.assertEqual(entry["source_kind"], "ENGINE_RULE")
            self.assertEqual(entry["source_ref"], "public-synthetic-policy-defaults")
            self.assertEqual(entry["semantic_anchor"], path)
            self.assertIsNone(entry["source_commit_sha"])
