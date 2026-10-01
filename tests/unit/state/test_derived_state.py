"""Derived Human State bands, sleep pressure, circadian, debt, and hunger ETA."""

from __future__ import annotations

import unittest

import pytest

from engine.life.derived import (
    build_circadian_anchors,
    circadian_bias_at_minute,
    classify_hunger_band,
    classify_physical_fatigue_band,
    classify_sleep_pressure_band,
    classify_social_battery_band,
    classify_stress_band,
    compute_sleep_pressure_breakdown,
    default_synthetic_sleep_profile,
    effective_sleep_start_score,
    hunger_threshold_seconds,
    nap_awake_load_credit_min,
    piecewise_linear_interpolate,
    settle_main_sleep_debt,
    settle_nap_sleep_debt,
    stress_sleep_onset_friction,
    validate_synthetic_sleep_profile,
)
from engine.life.dynamics import resolve_v2_rates
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.policy import load_policy

from tests.support.constants import POLICY_V2_CANDIDATE_PATH


pytestmark = pytest.mark.unit


class BandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_policy(POLICY_V2_CANDIDATE_PATH)

    def _thr_cases(self, classify, thresholds, labels_before_first="LOW"):
        for name, thr in thresholds.items():
            self.assertEqual(classify(thr - 1, self.v2), self._expected(thr - 1, thresholds, labels_before_first))
            self.assertEqual(classify(thr, self.v2), name if name != labels_before_first else name)
            self.assertEqual(classify(thr + 1, self.v2), name)

    def _expected(self, value, thresholds, low_label):
        # Recompute expected via same ascending rule
        order = [low_label] + list(thresholds.keys())
        # thresholds dict insertion order matches schema order
        current = low_label
        for label, thr in thresholds.items():
            if value >= thr:
                current = label
            else:
                break
        return current

    def test_hunger_thresholds(self):
        thr = self.v2["derived_bands"]["hunger"]
        for t in (250, 450, 700):
            self.assertNotEqual(classify_hunger_band(t - 1, self.v2), classify_hunger_band(t, self.v2))
            self.assertEqual(classify_hunger_band(t, self.v2), classify_hunger_band(t + 1, self.v2))
        self.assertEqual(classify_hunger_band(249, self.v2), "LOW")
        self.assertEqual(classify_hunger_band(250, self.v2), "NOTICEABLE")
        self.assertEqual(classify_hunger_band(450, self.v2), "MEAL_NEEDED")
        self.assertEqual(classify_hunger_band(700, self.v2), "URGENT")

    def test_fatigue_thresholds(self):
        self.assertEqual(classify_physical_fatigue_band(199, self.v2), "LOW")
        self.assertEqual(classify_physical_fatigue_band(200, self.v2), "MODERATE")
        self.assertEqual(classify_physical_fatigue_band(450, self.v2), "HIGH")
        self.assertEqual(classify_physical_fatigue_band(700, self.v2), "VERY_HIGH")

    def test_stress_thresholds(self):
        self.assertEqual(classify_stress_band(299, self.v2), "LOW")
        self.assertEqual(classify_stress_band(300, self.v2), "MODERATE")
        self.assertEqual(classify_stress_band(550, self.v2), "HIGH")
        self.assertEqual(classify_stress_band(800, self.v2), "VERY_HIGH")

    def test_social_thresholds(self):
        self.assertEqual(classify_social_battery_band(249, self.v2), "LOW")
        self.assertEqual(classify_social_battery_band(250, self.v2), "MODERATE")
        self.assertEqual(classify_social_battery_band(600, self.v2), "HIGH")

    def test_sleep_pressure_thresholds(self):
        self.assertEqual(classify_sleep_pressure_band(349, self.v2), "LOW")
        self.assertEqual(classify_sleep_pressure_band(350, self.v2), "MODERATE")
        self.assertEqual(classify_sleep_pressure_band(700, self.v2), "HIGH")
        self.assertEqual(classify_sleep_pressure_band(950, self.v2), "CRITICAL")


class PiecewiseTests(unittest.TestCase):
    def test_exact_knots_between_below_above(self):
        knots = [{"x": 0, "y": 0}, {"x": 100, "y": 50}, {"x": 200, "y": 100}]
        self.assertEqual(piecewise_linear_interpolate(0, knots), 0)
        self.assertEqual(piecewise_linear_interpolate(100, knots), 50)
        self.assertEqual(piecewise_linear_interpolate(200, knots), 100)
        self.assertEqual(piecewise_linear_interpolate(50, knots), 25)
        self.assertEqual(piecewise_linear_interpolate(-10, knots), 0)
        self.assertEqual(piecewise_linear_interpolate(500, knots), 100)
        # trunc toward -∞: (100-50)*(150-100)//(200-100) = 50*50//100 = 25 → 50+25=75
        self.assertEqual(piecewise_linear_interpolate(150, knots), 75)
        # non-divisible: 33 * 50 // 100 = 16
        self.assertEqual(piecewise_linear_interpolate(33, knots), 16)


class SleepDerivedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_policy(POLICY_V2_CANDIDATE_PATH)
        cls.prof = default_synthetic_sleep_profile()

    def test_profile_validation(self):
        validate_synthetic_sleep_profile(self.prof)
        with self.assertRaises(LifeEngineError):
            validate_synthetic_sleep_profile({**self.prof._asdict(), "sleep_need_min": 100})
        with self.assertRaises(LifeEngineError):
            validate_synthetic_sleep_profile({**self.prof._asdict(), "preferred_wake_window_start_minute_local": 2000})

    def test_nap_credit(self):
        self.assertEqual(nap_awake_load_credit_min(30, self.v2), 22)
        self.assertEqual(nap_awake_load_credit_min(100, self.v2), 45)
        with self.assertRaises(LifeEngineError):
            nap_awake_load_credit_min(-1, self.v2)

    def test_homeostatic_debt_fatigue_components(self):
        bd = compute_sleep_pressure_breakdown(
            policy=self.v2,
            profile=self.prof,
            sleep_debt_min=60,
            physical_fatigue=250,
            minute_local=12 * 60,
            elapsed_since_last_main_sleep_end_min=480,
        )
        self.assertEqual(bd.debt, 40)
        self.assertEqual(bd.fatigue, 30)
        self.assertGreaterEqual(bd.homeostatic, 0)
        self.assertEqual(bd.total, max(0, min(1000, bd.homeostatic + bd.debt + bd.fatigue + bd.circadian)))

    def test_circadian_every_anchor(self):
        anchors = build_circadian_anchors(self.prof, self.v2)
        self.assertEqual(len(anchors), 8)
        for a in anchors[:-1]:
            self.assertEqual(circadian_bias_at_minute(a.minute_local, self.prof, self.v2), a.bias)

    def test_midnight_wrap_and_same_minute(self):
        # sleep window wraps 23:00–01:00
        b_pre = circadian_bias_at_minute(23 * 60, self.prof, self.v2)
        b_post = circadian_bias_at_minute(0, self.prof, self.v2)
        self.assertEqual(b_pre, 120)
        self.assertGreater(b_post, 120)
        # same minute-of-day ⇒ same bias (date-independent)
        self.assertEqual(
            circadian_bias_at_minute(500, self.prof, self.v2),
            circadian_bias_at_minute(500, self.prof, self.v2),
        )

    def test_sleep_pressure_clamp(self):
        bd = compute_sleep_pressure_breakdown(
            policy=self.v2,
            profile=self.prof,
            sleep_debt_min=720,
            physical_fatigue=1000,
            minute_local=0,  # late awake / sleep window side
            elapsed_since_last_main_sleep_end_min=20 * 60,
        )
        self.assertLessEqual(bd.total, 1000)
        self.assertGreaterEqual(bd.total, 0)

    def test_friction_thresholds(self):
        self.assertEqual(stress_sleep_onset_friction(549, self.v2), 0)
        self.assertEqual(stress_sleep_onset_friction(550, self.v2), 40)
        self.assertEqual(stress_sleep_onset_friction(799, self.v2), 40)
        self.assertEqual(stress_sleep_onset_friction(800, self.v2), 80)
        self.assertEqual(effective_sleep_start_score(400, 800, self.v2), 320)

    def test_main_settlement(self):
        self.assertEqual(
            settle_main_sleep_debt(
                current_debt_min=0, actual_main_sleep_duration_min=400, sleep_need_min=480, policy=self.v2
            ),
            80,
        )
        self.assertEqual(
            settle_main_sleep_debt(
                current_debt_min=200, actual_main_sleep_duration_min=600, sleep_need_min=480, policy=self.v2
            ),
            80,  # surplus 120 capped
        )
        self.assertEqual(
            settle_main_sleep_debt(
                current_debt_min=50, actual_main_sleep_duration_min=480, sleep_need_min=480, policy=self.v2
            ),
            50,
        )

    def test_nap_settlement(self):
        self.assertEqual(
            settle_nap_sleep_debt(current_debt_min=100, nap_duration_min=30, policy=self.v2),
            85,
        )
        self.assertEqual(
            settle_nap_sleep_debt(current_debt_min=100, nap_duration_min=90, policy=self.v2),
            70,  # cap 30
        )
        self.assertEqual(
            settle_nap_sleep_debt(current_debt_min=10, nap_duration_min=30, policy=self.v2),
            0,
        )


class HungerProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_policy(POLICY_V2_CANDIDATE_PATH)

    def test_exact_eta_and_already_crossed(self):
        rates = resolve_v2_rates(
            self.v2, {"base_activity_class": "SEDENTARY_FOCUSED", "social_exposure": "NONE"}
        )
        eta = hunger_threshold_seconds(
            hunger=100,
            hunger_remainder=0,
            hunger_rate_per_hour=rates["hunger"],
            threshold_name="NOTICEABLE",
            policy=self.v2,
        )
        self.assertIsNotNone(eta)
        self.assertGreater(eta, 0)
        self.assertEqual(
            hunger_threshold_seconds(
                hunger=250,
                hunger_remainder=0,
                hunger_rate_per_hour=rates["hunger"],
                threshold_name="NOTICEABLE",
                policy=self.v2,
            ),
            0,
        )

    def test_nonpositive_rate(self):
        self.assertIsNone(
            hunger_threshold_seconds(
                hunger=0,
                hunger_remainder=0,
                hunger_rate_per_hour=0,
                threshold_name="MEAL_NEEDED",
                policy=self.v2,
            )
        )

    def test_activity_difference(self):
        sed = resolve_v2_rates(
            self.v2, {"base_activity_class": "SEDENTARY_FOCUSED", "social_exposure": "NONE"}
        )["hunger"]
        phys = resolve_v2_rates(
            self.v2, {"base_activity_class": "PHYSICALLY_ACTIVE", "social_exposure": "NONE"}
        )["hunger"]
        e1 = hunger_threshold_seconds(
            hunger=100, hunger_remainder=0, hunger_rate_per_hour=sed, threshold_name="URGENT", policy=self.v2
        )
        e2 = hunger_threshold_seconds(
            hunger=100, hunger_remainder=0, hunger_rate_per_hour=phys, threshold_name="URGENT", policy=self.v2
        )
        self.assertLess(e2, e1)

    def test_remainder_edge_partition(self):
        a = hunger_threshold_seconds(
            hunger=249, hunger_remainder=3599, hunger_rate_per_hour=60, threshold_name="NOTICEABLE", policy=self.v2
        )
        self.assertEqual(a, 1)
        # one-shot vs conceptual: already almost there
        b = hunger_threshold_seconds(
            hunger=249, hunger_remainder=0, hunger_rate_per_hour=60, threshold_name="NOTICEABLE", policy=self.v2
        )
        self.assertGreater(b, a)
