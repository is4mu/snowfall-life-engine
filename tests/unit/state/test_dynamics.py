"""Fixed-point Human State dynamics, affect, impulses, and validation."""

from __future__ import annotations

import copy
import json
import unittest

import pytest

from engine.life.dynamics import (
    apply_affect_impulse,
    apply_stress_impulse,
    integrate_affect_return_to_neutral,
    integrate_v2_interval,
    require_v2_production_format,
    resolve_v2_rates,
    validate_activity_context,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.fixed_point import (
    SECONDS_PER_HOUR,
    empty_rate_remainders,
    from_scaled,
    integrate_scaled_field,
    saturation_seconds_in_segment,
    to_scaled,
)
from engine.life.policy import load_policy

from tests.support.constants import POLICY_PATH, POLICY_V2_CANDIDATE_PATH

pytestmark = pytest.mark.unit


BASE = {
    "sleep_debt_min": 0,
    "hunger": 100,
    "physical_fatigue": 100,
    "affect_valence": 0,
    "stress": 100,
    "social_battery": 800,
}


class PolicyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_policy(POLICY_V2_CANDIDATE_PATH)
        cls.v1 = load_policy(POLICY_PATH)

    def test_v1_rejected(self):
        with self.assertRaises(LifeEngineError) as ctx:
            require_v2_production_format(self.v1)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_VERSION)

    def test_v2_candidate_accepted_without_approval(self):
        pol = require_v2_production_format(self.v2)
        self.assertEqual(pol["schema_version"], 2)

    def test_malformed_float_rate_rejected(self):
        bad = copy.deepcopy(self.v2)
        bad["state_dynamics"]["base_activity_rates"]["SLEEP"]["hunger_per_hour"] = 15.5
        with self.assertRaises(LifeEngineError):
            require_v2_production_format(bad)
        with self.assertRaises(LifeEngineError):
            integrate_v2_interval(
                BASE,
                empty_rate_remainders(),
                bad,
                {"base_activity_class": "SLEEP", "social_exposure": "NONE"},
                60,
            )

    def test_invalid_base_activity(self):
        with self.assertRaises(LifeEngineError):
            resolve_v2_rates(self.v2, {"base_activity_class": "YOGA", "social_exposure": "NONE"})

    def test_invalid_social_exposure(self):
        with self.assertRaises(LifeEngineError):
            resolve_v2_rates(
                self.v2,
                {"base_activity_class": "REST_AWAKE", "social_exposure": "PARTY"},
            )

    def test_caller_sleep_social_token_rejected(self):
        with self.assertRaises(LifeEngineError):
            validate_activity_context(
                {"base_activity_class": "SLEEP", "social_exposure": "SLEEP"}
            )
        with self.assertRaises(LifeEngineError):
            resolve_v2_rates(
                self.v2,
                {"base_activity_class": "SLEEP", "social_exposure": "SLEEP"},
            )

    def test_social_exposure_required_even_for_sleep(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_activity_context({"base_activity_class": "SLEEP"})
        self.assertIn("social_exposure required", ctx.exception.detail)
        with self.assertRaises(LifeEngineError):
            validate_activity_context(
                {"base_activity_class": "SLEEP", "social_exposure": None}
            )
        with self.assertRaises(LifeEngineError):
            resolve_v2_rates(self.v2, {"base_activity_class": "SLEEP"})

    def test_sleep_base_uses_internal_sleep_rate(self):
        rates_none = resolve_v2_rates(
            self.v2, {"base_activity_class": "SLEEP", "social_exposure": "NONE"}
        )
        rates_active = resolve_v2_rates(
            self.v2, {"base_activity_class": "SLEEP", "social_exposure": "ACTIVE"}
        )
        self.assertEqual(rates_none["social_battery"], 50)
        self.assertEqual(rates_active["social_battery"], 50)

    def test_bool_as_int_rejected(self):
        with self.assertRaises(LifeEngineError):
            integrate_v2_interval(
                BASE,
                empty_rate_remainders(),
                self.v2,
                {"base_activity_class": "REST_AWAKE", "social_exposure": "NONE"},
                True,  # type: ignore[arg-type]
            )

    def test_float_elapsed_rejected(self):
        with self.assertRaises(LifeEngineError):
            integrate_v2_interval(
                BASE,
                empty_rate_remainders(),
                self.v2,
                {"base_activity_class": "REST_AWAKE", "social_exposure": "NONE"},
                1.5,  # type: ignore[arg-type]
            )

    def test_negative_elapsed_rejected(self):
        with self.assertRaises(LifeEngineError):
            integrate_v2_interval(
                BASE,
                empty_rate_remainders(),
                self.v2,
                {"base_activity_class": "REST_AWAKE", "social_exposure": "NONE"},
                -1,
            )

    def test_fixture_values_unchanged(self):
        raw = json.loads(POLICY_V2_CANDIDATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["state_dynamics"]["base_activity_rates"]["SLEEP"]["hunger_per_hour"], 15)
        self.assertEqual(raw["state_dynamics"]["affect_impulses"]["LARGE"], 350)
        self.assertEqual(raw["sleep_policy"]["circadian_bias_mechanics"]["late_awake_cap_bias"], 220)


class FixedPointV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_policy(POLICY_V2_CANDIDATE_PATH)

    def test_positive_split_invariance(self):
        ctx = {"base_activity_class": "LIGHT_ACTIVE", "social_exposure": "NONE"}
        one, rem_one = integrate_v2_interval(BASE, empty_rate_remainders(), self.v2, ctx, 7200)
        state, rem = dict(BASE), empty_rate_remainders()
        for _ in range(8):
            state, rem = integrate_v2_interval(state, rem, self.v2, ctx, 900)
        self.assertEqual(state, one)
        self.assertEqual(rem, rem_one)

    def test_negative_rate_split_invariance(self):
        ctx = {"base_activity_class": "SLEEP", "social_exposure": "NONE"}
        start = dict(BASE)
        start["physical_fatigue"] = 500
        start["stress"] = 400
        one, rem_one = integrate_v2_interval(start, empty_rate_remainders(), self.v2, ctx, 5400)
        state, rem = dict(start), empty_rate_remainders()
        for chunk in (1800, 1800, 1800):
            state, rem = integrate_v2_interval(state, rem, self.v2, ctx, chunk)
        self.assertEqual(state, one)
        self.assertEqual(rem, rem_one)

    def test_upper_saturation_no_hidden_overshoot(self):
        start = dict(BASE)
        start["hunger"] = 990
        rem0 = empty_rate_remainders()
        rem0["hunger"] = 3500
        ctx = {"base_activity_class": "PHYSICALLY_ACTIVE", "social_exposure": "NONE"}
        state, rem = integrate_v2_interval(start, rem0, self.v2, ctx, 3600)
        self.assertEqual(state["hunger"], 1000)
        self.assertEqual(rem["hunger"], 0)
        start2 = dict(state)
        start2["physical_fatigue"] = 1000
        rem2 = dict(rem)
        rem2["physical_fatigue"] = 0
        ctx_sleep = {"base_activity_class": "SLEEP", "social_exposure": "NONE"}
        after, rem_after = integrate_v2_interval(start2, rem2, self.v2, ctx_sleep, 1)
        self.assertLess(after["physical_fatigue"], 1000)
        self.assertEqual(after["physical_fatigue"], 999)
        self.assertEqual(rem_after["physical_fatigue"], 3535)

    def test_lower_saturation_split(self):
        start = dict(BASE)
        start["physical_fatigue"] = 5
        ctx = {"base_activity_class": "SLEEP", "social_exposure": "NONE"}
        one, rem_one = integrate_v2_interval(start, empty_rate_remainders(), self.v2, ctx, 3600)
        state, rem = dict(start), empty_rate_remainders()
        for _ in range(6):
            state, rem = integrate_v2_interval(state, rem, self.v2, ctx, 600)
        self.assertEqual(state["physical_fatigue"], 0)
        self.assertEqual(rem["physical_fatigue"], 0)
        self.assertEqual(state, one)
        self.assertEqual(rem, rem_one)

    def test_remainder_bounds(self):
        ctx = {"base_activity_class": "SEDENTARY_FOCUSED", "social_exposure": "PASSIVE"}
        _, rem = integrate_v2_interval(BASE, empty_rate_remainders(), self.v2, ctx, 1)
        for k, v in rem.items():
            self.assertGreaterEqual(v, 0)
            self.assertLess(v, 3600)

    def test_sleep_debt_not_integrated(self):
        start = dict(BASE)
        start["sleep_debt_min"] = 120
        ctx = {"base_activity_class": "PHYSICALLY_ACTIVE", "social_exposure": "ACTIVE"}
        out, _ = integrate_v2_interval(start, empty_rate_remainders(), self.v2, ctx, 7200)
        self.assertEqual(out["sleep_debt_min"], 120)

    def test_inputs_not_mutated(self):
        state = dict(BASE)
        rem = empty_rate_remainders()
        ctx = {"base_activity_class": "REST_AWAKE", "social_exposure": "NONE"}
        integrate_v2_interval(state, rem, self.v2, ctx, 100)
        self.assertEqual(state, BASE)
        self.assertEqual(rem, empty_rate_remainders())

    def test_scaled_helper_unit(self):
        f, r = integrate_scaled_field(
            field_value=10, remainder=100, rate_per_hour=1, elapsed_seconds=3500, lo=0, hi=1000
        )
        self.assertEqual(to_scaled(f, r), 10 * 3600 + 100 + 3500)

    def test_saturation_analytics_upper(self):
        sat = saturation_seconds_in_segment(
            field_value=999, remainder=0, rate_per_hour=60, elapsed_seconds=3600, lo=0, hi=1000
        )
        self.assertEqual(sat["hit_upper"], 1)
        self.assertGreater(sat["upper_seconds"], 0)
        self.assertEqual(sat["lower_seconds"], 0)


class AffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_policy(POLICY_V2_CANDIDATE_PATH)
        cls.bands = cls.v2["state_dynamics"]["affect_return_to_baseline"]["bands"]

    def test_positive_negative_signed_scaled_symmetry(self):
        p, pr = integrate_affect_return_to_neutral(
            valence=500, remainder=0, bands=self.bands, elapsed_seconds=5000
        )
        n, nr = integrate_affect_return_to_neutral(
            valence=-500, remainder=0, bands=self.bands, elapsed_seconds=5000
        )
        self.assertEqual(to_scaled(p, pr), -to_scaled(n, nr))

    def test_nonzero_remainder_near_zero(self):
        # valence==0 with nonzero rem must NOT wipe latent; continue toward zero.
        v, r = integrate_affect_return_to_neutral(
            valence=0, remainder=1000, bands=self.bands, elapsed_seconds=10
        )
        self.assertEqual(to_scaled(v, r), 1000 - 15 * 10)
        # Reach exact zero
        v2, r2 = integrate_affect_return_to_neutral(
            valence=0, remainder=15, bands=self.bands, elapsed_seconds=3600
        )
        self.assertEqual((v2, r2), (0, 0))

    def test_mirrored_signed_scaled_with_remainder(self):
        p, pr = integrate_affect_return_to_neutral(
            valence=200, remainder=500, bands=self.bands, elapsed_seconds=4000
        )
        n, nr = integrate_affect_return_to_neutral(
            valence=-200, remainder=500, bands=self.bands, elapsed_seconds=4000
        )
        # Binding signed scaled: start +200 rem500 vs -200 rem500 are NOT mirrors
        # (to_scaled asymmetric). Mirror pair uses opposite signed scaled starts.
        n2, nr2 = integrate_affect_return_to_neutral(
            valence=-201, remainder=3100, bands=self.bands, elapsed_seconds=4000
        )
        # -201*3600+3100 = -(200*3600+500)
        self.assertEqual(to_scaled(-201, 3100), -to_scaled(200, 500))
        self.assertEqual(to_scaled(p, pr), -to_scaled(n2, nr2))

    def test_400_boundary_crossing(self):
        v, r = integrate_affect_return_to_neutral(
            valence=450, remainder=0, bands=self.bands, elapsed_seconds=3600
        )
        self.assertLess(v, 400)
        self.assertGreater(v, 300)

    def test_150_boundary_crossing(self):
        v, r = integrate_affect_return_to_neutral(
            valence=200, remainder=0, bands=self.bands, elapsed_seconds=7200
        )
        self.assertLess(v, 150)

    def test_exact_zero_stop(self):
        v, r = integrate_affect_return_to_neutral(
            valence=15, remainder=0, bands=self.bands, elapsed_seconds=3600 * 10
        )
        self.assertEqual(v, 0)
        self.assertEqual(r, 0)

    def test_split_invariance_with_remainder(self):
        one, orem = integrate_affect_return_to_neutral(
            valence=500, remainder=800, bands=self.bands, elapsed_seconds=10000
        )
        v, r = 500, 800
        left = 10000
        while left:
            step = 700 if left > 700 else left
            v, r = integrate_affect_return_to_neutral(
                valence=v, remainder=r, bands=self.bands, elapsed_seconds=step
            )
            left -= step
        self.assertEqual((v, r), (one, orem))

    def test_mirror_500(self):
        p, pr = integrate_affect_return_to_neutral(
            valence=500, remainder=0, bands=self.bands, elapsed_seconds=3600
        )
        n, nr = integrate_affect_return_to_neutral(
            valence=-500, remainder=0, bands=self.bands, elapsed_seconds=3600
        )
        self.assertEqual(to_scaled(p, pr), -to_scaled(n, nr))
        self.assertEqual(p, 430)
        self.assertEqual(n, -430)

    def _assert_oneshot_matches_one_plus_rest(self, valence: int, remainder: int, total: int):
        one = integrate_affect_return_to_neutral(
            valence=valence, remainder=remainder, bands=self.bands, elapsed_seconds=total
        )
        first = integrate_affect_return_to_neutral(
            valence=valence, remainder=remainder, bands=self.bands, elapsed_seconds=1
        )
        rest = integrate_affect_return_to_neutral(
            valence=first[0],
            remainder=first[1],
            bands=self.bands,
            elapsed_seconds=total - 1,
        )
        self.assertEqual(one, rest)
        self.assertEqual(to_scaled(*one), to_scaled(*rest))
        return one

    def test_exact_boundary_400_partition(self):
        """Exact ±400 band lo: one-shot must match 1+(n-1) (higher rate only for boundary tick)."""
        pos = self._assert_oneshot_matches_one_plus_rest(400, 0, 3600)
        neg = self._assert_oneshot_matches_one_plus_rest(-400, 0, 3600)
        self.assertEqual(to_scaled(*pos), -to_scaled(*neg))
        self.assertEqual(pos, (364, 3565))
        self.assertEqual(neg, (-365, 35))

    def test_exact_boundary_150_partition(self):
        pos = self._assert_oneshot_matches_one_plus_rest(150, 0, 3600)
        neg = self._assert_oneshot_matches_one_plus_rest(-150, 0, 3600)
        self.assertEqual(to_scaled(*pos), -to_scaled(*neg))
        self.assertEqual(pos, (134, 3580))
        self.assertEqual(neg, (-135, 20))

    def test_exact_landing_on_400_then_continue(self):
        # 10s at rate 70 from 400*3600+700 lands exactly on 400; next tick leaves band.
        start_v, start_r = from_scaled(400 * SECONDS_PER_HOUR + 700)
        landed = integrate_affect_return_to_neutral(
            valence=start_v, remainder=start_r, bands=self.bands, elapsed_seconds=10
        )
        self.assertEqual(landed, (400, 0))
        after = integrate_affect_return_to_neutral(
            valence=landed[0], remainder=landed[1], bands=self.bands, elapsed_seconds=1
        )
        oneshot = integrate_affect_return_to_neutral(
            valence=start_v, remainder=start_r, bands=self.bands, elapsed_seconds=11
        )
        self.assertEqual(oneshot, after)
        self.assertEqual(after, (399, 3530))

    def test_exact_landing_on_150_then_continue(self):
        # Rate in 150–399 band is 35; land exactly on 150 then leave with 1s tick.
        start_v, start_r = from_scaled(150 * SECONDS_PER_HOUR + 35 * 5)
        landed = integrate_affect_return_to_neutral(
            valence=start_v, remainder=start_r, bands=self.bands, elapsed_seconds=5
        )
        self.assertEqual(landed, (150, 0))
        after = integrate_affect_return_to_neutral(
            valence=landed[0], remainder=landed[1], bands=self.bands, elapsed_seconds=1
        )
        oneshot = integrate_affect_return_to_neutral(
            valence=start_v, remainder=start_r, bands=self.bands, elapsed_seconds=6
        )
        self.assertEqual(oneshot, after)
        self.assertLess(to_scaled(*after), 150 * SECONDS_PER_HOUR)

    def test_exact_boundary_nonzero_remainder_partition(self):
        # Near ±400 with nonzero rem: still partition-invariant vs 1+(n-1).
        for valence, rem in ((400, 500), (-400, 500), (150, 900), (-150, 900)):
            self._assert_oneshot_matches_one_plus_rest(valence, rem, 5000)

    def test_signed_exact_boundary_scaled_symmetry(self):
        for abs_v in (400, 150):
            p = integrate_affect_return_to_neutral(
                valence=abs_v, remainder=0, bands=self.bands, elapsed_seconds=7200
            )
            n = integrate_affect_return_to_neutral(
                valence=-abs_v, remainder=0, bands=self.bands, elapsed_seconds=7200
            )
            self.assertEqual(to_scaled(*p), -to_scaled(*n))


class ImpulseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = load_policy(POLICY_V2_CANDIDATE_PATH)

    def test_named_stress_only(self):
        out, rem = apply_stress_impulse(BASE, empty_rate_remainders(), self.v2, "MAJOR")
        self.assertEqual(out["stress"], 350)
        self.assertEqual(rem["stress"], 0)
        with self.assertRaises(LifeEngineError):
            apply_stress_impulse(BASE, empty_rate_remainders(), self.v2, "HUGE")

    def test_named_affect_only(self):
        out, rem = apply_affect_impulse(
            BASE, empty_rate_remainders(), self.v2, magnitude="MEDIUM", sign="NEGATIVE"
        )
        self.assertEqual(out["affect_valence"], -180)
        with self.assertRaises(LifeEngineError):
            apply_affect_impulse(
                BASE, empty_rate_remainders(), self.v2, magnitude="TINY", sign="POSITIVE"
            )

    def test_impulse_preserves_nonzero_remainder(self):
        rem = empty_rate_remainders()
        rem["stress"] = 500
        out, out_rem = apply_stress_impulse(BASE, rem, self.v2, "MINOR")
        self.assertEqual(out["stress"], 150)
        self.assertEqual(out_rem["stress"], 500)

    def test_impulse_saturation_clears_remainder(self):
        start = dict(BASE)
        start["stress"] = 900
        rem = empty_rate_remainders()
        rem["stress"] = 100
        out, out_rem = apply_stress_impulse(start, rem, self.v2, "MAJOR")
        self.assertEqual(out["stress"], 1000)
        self.assertEqual(out_rem["stress"], 0)

    def test_affect_impulse_sign_crossing_preserves_frac(self):
        start = dict(BASE)
        start["affect_valence"] = -50
        rem = empty_rate_remainders()
        rem["affect_valence"] = 100
        out, out_rem = apply_affect_impulse(
            start, rem, self.v2, magnitude="SMALL", sign="POSITIVE"
        )
        # scaled = -50*3600+100 + 80*3600 = 30*3600+100
        self.assertEqual(out["affect_valence"], 30)
        self.assertEqual(out_rem["affect_valence"], 100)
