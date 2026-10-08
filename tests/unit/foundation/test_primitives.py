"""Foundation unit tests for fixed-point state, keyed RNG, and stable IDs."""

from __future__ import annotations

import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.fixed_point import (
    apply_delta,
    clamp_human_state,
    empty_rate_remainders,
    integrate_rates,
    validate_human_state,
)
from engine.life.ids import IdRegistry, stable_id
from engine.life.rng import u01
from tests.support.assertions.invariants import assert_partition_equivalent

pytestmark = pytest.mark.unit


class FixedPointTests(unittest.TestCase):
    def test_exactly_six_fields(self):
        good = {
            "sleep_debt_min": 0,
            "hunger": 1,
            "physical_fatigue": 2,
            "affect_valence": -3,
            "stress": 4,
            "social_battery": 5,
        }
        self.assertEqual(validate_human_state(good)["hunger"], 1)
        bad = dict(good)
        bad["alertness"] = 1
        with self.assertRaises(LifeEngineError):
            validate_human_state(bad)

    def test_float_rejected(self):
        state = {
            "sleep_debt_min": 0,
            "hunger": 1.0,
            "physical_fatigue": 0,
            "affect_valence": 0,
            "stress": 0,
            "social_battery": 0,
        }
        with self.assertRaises(LifeEngineError):
            validate_human_state(state)

    def test_clamp(self):
        state = {
            "sleep_debt_min": 9999,
            "hunger": -10,
            "physical_fatigue": 500,
            "affect_valence": 2000,
            "stress": 0,
            "social_battery": 1000,
        }
        out = clamp_human_state(state)
        self.assertEqual(out["sleep_debt_min"], 720)
        self.assertEqual(out["hunger"], 0)
        self.assertEqual(out["affect_valence"], 1000)

    def test_apply_delta(self):
        base = {
            "sleep_debt_min": 0,
            "hunger": 100,
            "physical_fatigue": 100,
            "affect_valence": 0,
            "stress": 100,
            "social_battery": 800,
        }
        out = apply_delta(base, {"hunger": 50})
        self.assertEqual(out["hunger"], 150)

    def test_integrate_partition_invariant(self):
        base = {
            "sleep_debt_min": 0,
            "hunger": 100,
            "physical_fatigue": 100,
            "affect_valence": 0,
            "stress": 100,
            "social_battery": 800,
        }
        rem0 = empty_rate_remainders()
        rates = {
            "hunger": 50,
            "physical_fatigue": 20,
            "affect_valence": 0,
            "stress": 10,
            "social_battery": 0,
        }
        one, rem_one = integrate_rates(
            base,
            rates=rates,
            elapsed_seconds=3723,
            remainders=rem0,
        )
        hs, rem = base, rem0
        for chunk in (1100, 901, 1722):
            hs, rem = integrate_rates(
                hs,
                rates=rates,
                elapsed_seconds=chunk,
                remainders=rem,
            )
        assert_partition_equivalent(
            whole_state=one,
            whole_remainders=rem_one,
            partitioned_state=hs,
            partitioned_remainders=rem,
        )


class RngTests(unittest.TestCase):
    def test_keyed_stability_and_independence(self):
        a = u01("seed", "ns", "e1", "coin", "0")
        b = u01("seed", "ns", "e1", "coin", "0")
        self.assertEqual(a, b)
        other = u01("seed", "ns", "e1", "coin", "1")
        a2 = u01("seed", "ns", "e1", "coin", "0")
        self.assertEqual(a, a2)
        self.assertNotEqual(a, other)
        self.assertTrue(0 <= a <= 1_000_000_000)


class IdTests(unittest.TestCase):
    def test_stable_id_deterministic(self):
        self.assertEqual(stable_id("act", "x", 1), stable_id("act", "x", 1))

    def test_collision_different_semantics(self):
        reg = IdRegistry()
        reg.register("id1", "hash-a")
        with self.assertRaises(LifeEngineError) as ctx:
            reg.register("id1", "hash-b")
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)
