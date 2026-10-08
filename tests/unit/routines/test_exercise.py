"""Kickboxing routine adaptation, history-window, and feasibility contracts."""

from __future__ import annotations

import copy
import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.routine_adapters import (
    count_kickboxing_sessions_in_window,
    kickboxing_opportunity_level,
    parse_kickboxing_session_fact,
)

from tests.support.harnesses.routines import (
    NOW,
    adapt,
    ctx,
    diag_codes,
    opp_keys,
    policy,
    session,
)


pytestmark = pytest.mark.unit


class ExerciseAdapterTests(unittest.TestCase):
    def test_e1_zero_sessions_high(self) -> None:
        """E1: 0 sessions in window → HIGH → self-generated opportunity."""
        result = adapt(kickboxing_sessions=[])
        self.assertEqual(opp_keys(result), ["routine:kickboxing:self:2026-03-15"])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "KICKBOXING")
        self.assertEqual(opp.opportunity_class, "ROUTINE_HABIT")
        self.assertEqual(opp.source_kind, "ROUTINE")
        self.assertEqual(opp.source_ref, "exercise-history:kickboxing:2026-03-15")
        self.assertTrue(opp.soft_candidate)
        self.assertTrue(opp.domain_guard_satisfied)
        self.assertIsNone(opp.local_preference_permille)
        self.assertIn("slice2f.exercise.self.HIGH", opp.rule_ids)

    def test_e2_one_session_normal(self) -> None:
        """E2: 1 session → NORMAL → still eligible."""
        result = adapt(kickboxing_sessions=[session()])
        self.assertEqual(opp_keys(result), ["routine:kickboxing:self:2026-03-15"])
        opp = result.opportunities[0]
        self.assertEqual(opp.source_ref, "exercise-history:kickboxing:2026-03-15")
        self.assertIn("slice2f.exercise.self.NORMAL", opp.rule_ids)
        pol = policy()["domain_policy"]["exercise_kickboxing"]
        level = kickboxing_opportunity_level(1, pol["opportunity_by_count_in_window"])
        self.assertEqual(level, "NORMAL")

    def test_e3_two_or_more_low_suppress(self) -> None:
        """E3: 2+ sessions → LOW → no opportunity; EXERCISE_LOW_OPPORTUNITY."""
        s1 = session(session_id="e3a", actual_end="2026-03-14T19:00:00+09:00")
        s2 = session(
            session_id="e3b",
            actual_start="2026-03-13T18:00:00+09:00",
            actual_end="2026-03-13T19:00:00+09:00",
            finalized_at="2026-03-13T19:05:00+09:00",
        )
        result = adapt(kickboxing_sessions=[s1, s2])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("EXERCISE_LOW_OPPORTUNITY", diag_codes(result))

    def test_e4_exact_lower_bound_expired(self) -> None:
        """E4: actual_end == lower_bound does not count (exact bound expired)."""
        # window=7 days; now=2026-03-15T12:00 → lower=2026-03-08T12:00
        edge = session(
            session_id="e4",
            actual_start="2026-03-08T11:00:00+09:00",
            actual_end="2026-03-08T12:00:00+09:00",
            finalized_at="2026-03-08T12:05:00+09:00",
        )
        count = count_kickboxing_sessions_in_window(
            [parse_kickboxing_session_fact(edge, now=NOW)],
            now=NOW,
            history_window_days=7,
        )
        self.assertEqual(count, 0)
        result = adapt(kickboxing_sessions=[edge])
        # 0 counted → HIGH → opportunity
        self.assertEqual(opp_keys(result), ["routine:kickboxing:self:2026-03-15"])

    def test_e5_just_inside_window_counts(self) -> None:
        """E5: actual_end just after lower_bound counts."""
        # Foundation second precision: +1 minute past exact lower bound.
        inside = session(
            session_id="e5",
            actual_start="2026-03-08T12:00:00+09:00",
            actual_end="2026-03-08T12:01:00+09:00",
            finalized_at="2026-03-08T12:05:00+09:00",
        )
        count = count_kickboxing_sessions_in_window(
            [parse_kickboxing_session_fact(inside, now=NOW)],
            now=NOW,
            history_window_days=7,
        )
        self.assertEqual(count, 1)

    def test_e6_daily_cap_blocks(self) -> None:
        """E6: self_generated_today >= max → EXERCISE_DAILY_CAP_REACHED."""
        result = adapt(
            context=ctx(kickboxing_self_generated_today=1),
            kickboxing_sessions=[],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("EXERCISE_DAILY_CAP_REACHED", diag_codes(result))

    def test_e7_pending_plans_block(self) -> None:
        result = adapt(
            context=ctx(kickboxing_equivalent_pending_plans=1),
            kickboxing_sessions=[],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("EXERCISE_PENDING_PLAN_BLOCKED", diag_codes(result))

    def test_e8_fatigue_very_high_blocks(self) -> None:
        """E8: fatigue >= block band (VERY_HIGH) → EXERCISE_FATIGUE_BLOCKED."""
        result = adapt(
            context=ctx(physical_fatigue_band="VERY_HIGH"),
            kickboxing_sessions=[],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("EXERCISE_FATIGUE_BLOCKED", diag_codes(result))

    def test_e9_fatigue_high_allowed_when_block_is_very_high(self) -> None:
        result = adapt(
            context=ctx(physical_fatigue_band="HIGH"),
            kickboxing_sessions=[],
        )
        self.assertEqual(opp_keys(result), ["routine:kickboxing:self:2026-03-15"])

    def test_e10_location_blocked_still_emits(self) -> None:
        """E10: location infeasible → still emit with flags false."""
        result = adapt(
            context=ctx(kickboxing_location_feasible=False),
            kickboxing_sessions=[],
        )
        opp = result.opportunities[0]
        self.assertFalse(opp.location_feasible)
        self.assertTrue(opp.domain_guard_satisfied)
        self.assertIn("EXERCISE_LOCATION_BLOCKED", diag_codes(result))

    def test_e11_window_insufficient_still_emits(self) -> None:
        # duration_min=60
        result = adapt(
            context=ctx(available_window_min=30),
            kickboxing_sessions=[],
        )
        opp = result.opportunities[0]
        self.assertFalse(opp.time_feasible)
        self.assertIn("EXERCISE_WINDOW_INSUFFICIENT", diag_codes(result))

    def test_e12_physical_blocked_still_emits(self) -> None:
        result = adapt(
            context=ctx(physical={"HOUSEHOLD": True, "KICKBOXING": False}),
            kickboxing_sessions=[],
        )
        opp = result.opportunities[0]
        self.assertFalse(opp.physical_feasible)
        self.assertIn("EXERCISE_PHYSICAL_BLOCKED", diag_codes(result))

    def test_e13_future_session_fail_closed(self) -> None:
        bad = session(
            session_id="e13",
            actual_start="2026-03-15T13:00:00+09:00",
            actual_end="2026-03-15T14:00:00+09:00",
            finalized_at="2026-03-15T14:05:00+09:00",
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(kickboxing_sessions=[bad])
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)

    def test_e14_start_after_end_fail_closed(self) -> None:
        bad = session(
            session_id="e14",
            actual_start="2026-03-14T20:00:00+09:00",
            actual_end="2026-03-14T19:00:00+09:00",
        )
        with self.assertRaises(LifeEngineError):
            adapt(kickboxing_sessions=[bad])

    def test_e15_finalized_before_end_fail_closed(self) -> None:
        bad = session(
            session_id="e15",
            finalized_at="2026-03-14T18:30:00+09:00",
        )
        with self.assertRaises(LifeEngineError):
            adapt(kickboxing_sessions=[bad])

    def test_e16_duplicate_session_id_fail_closed(self) -> None:
        a = session(session_id="dup")
        b = session(
            session_id="dup",
            actual_start="2026-03-13T18:00:00+09:00",
            actual_end="2026-03-13T19:00:00+09:00",
            finalized_at="2026-03-13T19:05:00+09:00",
        )
        with self.assertRaises(LifeEngineError):
            adapt(kickboxing_sessions=[a, b])

    def test_e17_low_does_not_cancel_history(self) -> None:
        """E7/E17: LOW suppresses new self-gen only; sessions remain accepted facts."""
        s1 = session(session_id="e17a")
        s2 = session(
            session_id="e17b",
            actual_start="2026-03-12T18:00:00+09:00",
            actual_end="2026-03-12T19:00:00+09:00",
            finalized_at="2026-03-12T19:05:00+09:00",
        )
        snap = [copy.deepcopy(s1), copy.deepcopy(s2)]
        result = adapt(kickboxing_sessions=[s1, s2])
        self.assertEqual(opp_keys(result), [])
        self.assertEqual([s1, s2], snap)

    def test_e18_levels_from_policy_not_hardcoded(self) -> None:
        """E18: opportunity levels come from policy map keys."""
        pol = policy()["domain_policy"]["exercise_kickboxing"]["opportunity_by_count_in_window"]
        self.assertEqual(kickboxing_opportunity_level(0, pol), "HIGH")
        self.assertEqual(kickboxing_opportunity_level(1, pol), "NORMAL")
        self.assertEqual(kickboxing_opportunity_level(5, pol), "LOW")
        # No EXERCISE→KICKBOXING inference path exists in adapter API.


class ExerciseRegressionTests(unittest.TestCase):
    def test_r1_physical_lazy_low_no_household_fact(self) -> None:
        """LOW kickboxing suppress: no KICKBOXING emit → HOUSEHOLD physical unused."""
        s1 = session(session_id="r1a")
        s2 = session(
            session_id="r1b",
            actual_start="2026-03-12T18:00:00+09:00",
            actual_end="2026-03-12T19:00:00+09:00",
            finalized_at="2026-03-12T19:05:00+09:00",
        )
        result = adapt(
            context=ctx(physical={}),  # empty physical map OK — nothing emitted
            kickboxing_sessions=[s1, s2],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("EXERCISE_LOW_OPPORTUNITY", diag_codes(result))

    def test_r1_physical_lazy_kickboxing_only_map(self) -> None:
        result = adapt(
            context=ctx(physical={"KICKBOXING": True}),
            kickboxing_sessions=[],
        )
        self.assertEqual(opp_keys(result), ["routine:kickboxing:self:2026-03-15"])
        self.assertEqual(
            result.opportunities[0].source_ref,
            "exercise-history:kickboxing:2026-03-15",
        )

    def test_r1_physical_missing_on_kickboxing_emit_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError):
            adapt(context=ctx(physical={}), kickboxing_sessions=[])
