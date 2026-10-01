"""Categorical decision resolver precedence, guards, selection, and edge contracts."""

from __future__ import annotations

import unittest

import pytest

from engine.life.decisions import resolve_action_decision
from engine.life.errors import ErrorCode, LifeEngineError

from tests.support.harnesses.resolver import (
    CHARACTER_ID,
    NOW,
    active_ctx,
    boundary,
    deadline_opp,
    hard_opp,
    hs,
    leisure_opp,
    integrate_oneshot_vs_split,
    load_v2_policy,
    opportunity,
    restorative_opp,
    routine_opp,
    social_opp,
    resolve,
    soft_guard,
)


pytestmark = pytest.mark.unit


class TierPrecedenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_r1_hard_beats_urgent_hunger(self) -> None:
        out = resolve(
            self.policy,
            human_state=hs(hunger=700),
            opportunities=[hard_opp()],
            meal_feasible=True,
        )
        self.assertEqual(out.selected_priority_tier, "HARD_COMMITMENT")
        self.assertEqual(out.selected_action_kind, "HARD_COMMITMENT_ACTIVITY")

    def test_r2_urgent_hunger_beats_deadline(self) -> None:
        out = resolve(
            self.policy,
            human_state=hs(hunger=700),
            opportunities=[deadline_opp()],
            meal_feasible=True,
        )
        self.assertEqual(out.selected_priority_tier, "URGENT_BIOLOGICAL")
        self.assertEqual(out.selected_action_kind, "MEAL")

    def test_r3_deadline_beats_social(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[deadline_opp(), social_opp()],
        )
        self.assertEqual(out.selected_priority_tier, "DEADLINE_TASK")

    def test_r4_social_beats_routine(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[social_opp(), routine_opp()],
        )
        self.assertEqual(out.selected_priority_tier, "SOCIAL_PROMISE")

    def test_r5_routine_beats_restorative(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[routine_opp(), restorative_opp()],
        )
        self.assertEqual(out.selected_priority_tier, "ROUTINE_HABIT")

    def test_r6_restorative_beats_leisure(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[restorative_opp(), leisure_opp()],
        )
        self.assertEqual(out.selected_priority_tier, "RESTORATIVE")

    def test_r7_higher_tier_rank0_beats_lower_rank1000(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[
                hard_opp(pref=0),
                deadline_opp(pref=1000),
            ],
        )
        self.assertEqual(out.selected_priority_tier, "HARD_COMMITMENT")
        self.assertEqual(out.random_key, None)


class MinDwellAndSoftGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_r13_reassessable_3min_threshold_continue(self) -> None:
        active = active_ctx(actual_start="2026-03-15T11:57:00+09:00")
        out = resolve(
            self.policy,
            active=active,
            opportunities=[routine_opp()],  # non-urgent challenger
            boundary_fact=boundary(trigger_kind="THRESHOLD"),
            now=NOW,
        )
        self.assertEqual(out.result_kind, "CONTINUE_CURRENT")
        self.assertEqual(out.activity_instance_id, "act:1")

    def test_r14_after_15min_normal_competition(self) -> None:
        active = active_ctx(actual_start="2026-03-15T11:40:00+09:00")  # 20 min
        out = resolve(
            self.policy,
            active=active,
            opportunities=[deadline_opp()],
            boundary_fact=boundary(trigger_kind="THRESHOLD"),
            now=NOW,
        )
        # DEADLINE > ROUTINE (continue's tier) → deadline wins
        self.assertEqual(out.selected_priority_tier, "DEADLINE_TASK")
        self.assertNotEqual(out.result_kind, "CONTINUE_CURRENT")

    def test_r15_hard_or_urgent_overrides_min_dwell(self) -> None:
        active = active_ctx(actual_start="2026-03-15T11:57:00+09:00")
        out_hard = resolve(
            self.policy,
            active=active,
            opportunities=[hard_opp()],
            boundary_fact=boundary(trigger_kind="THRESHOLD"),
            now=NOW,
        )
        self.assertEqual(out_hard.selected_priority_tier, "HARD_COMMITMENT")

        out_urgent = resolve(
            self.policy,
            active=active,
            human_state=hs(hunger=700),
            meal_feasible=True,
            boundary_fact=boundary(trigger_kind="THRESHOLD"),
            now=NOW,
        )
        self.assertEqual(out_urgent.selected_priority_tier, "URGENT_BIOLOGICAL")

    def test_r16_non_threshold_no_min_dwell(self) -> None:
        active = active_ctx(actual_start="2026-03-15T11:57:00+09:00")
        out = resolve(
            self.policy,
            active=active,
            opportunities=[deadline_opp()],
            boundary_fact=boundary(
                trigger_kind="OBLIGATION",
                decision_key="obligation:1",
                reason_code="OBLIGATION_DUE",
                metric=None,
                target_band=None,
            ),
            now=NOW,
        )
        self.assertEqual(out.selected_priority_tier, "DEADLINE_TASK")

    def test_r17_soft_capacity_decline_suppress(self) -> None:
        soft_key = "leisure:MUSIC:fw1"
        out = resolve(
            self.policy,
            free_window_key="fw1",
            free_window_min=45,
            feasible_leisure_categories=["MUSIC"],
            soft_guards=[
                soft_guard(
                    candidate_key=soft_key,
                    decided_at="2026-03-15T11:45:00+09:00",
                    meaningful_input_changed=False,
                )
            ],
            now=NOW,
        )
        self.assertEqual(out.result_kind, "NO_ELIGIBLE_ACTION")
        self.assertTrue(any(r.reason == "SOFT_RECONSIDER_GUARD" for r in out.rejected))

    def test_r18_meaningful_input_changed_clears(self) -> None:
        soft_key = "leisure:MUSIC:fw1"
        out = resolve(
            self.policy,
            free_window_key="fw1",
            free_window_min=45,
            feasible_leisure_categories=["MUSIC"],
            soft_guards=[
                soft_guard(
                    candidate_key=soft_key,
                    decided_at="2026-03-15T11:45:00+09:00",
                    meaningful_input_changed=True,
                )
            ],
            now=NOW,
        )
        self.assertEqual(out.result_kind, "SELECT_CANDIDATE")
        self.assertEqual(out.selected_action_kind, "MUSIC")


class WithinTierSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_r19_rank_diff_over_50_best_no_rng(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[
                deadline_opp("d:best", pref=900),
                deadline_opp("d:worse", pref=800),  # diff 100 > 50
            ],
        )
        self.assertEqual(out.selected_candidate_key, "d:best")
        self.assertIsNone(out.random_key)

    def test_r20_rank_diff_le_50_keyed_tie(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[
                deadline_opp("d:a", pref=900),
                deadline_opp("d:b", pref=860),  # diff 40 <= 50
            ],
        )
        self.assertIsNotNone(out.random_key)
        self.assertIn(out.selected_candidate_key, {"d:a", "d:b"})

    def test_r21_all_ranks_null_keyed_tie(self) -> None:
        out = resolve(
            self.policy,
            opportunities=[
                deadline_opp("d:a", pref=None),
                deadline_opp("d:b", pref=None),
            ],
        )
        self.assertIsNotNone(out.random_key)
        self.assertIn(out.selected_candidate_key, {"d:a", "d:b"})

    def test_r22_mixed_ranked_null_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            resolve(
                self.policy,
                opportunities=[
                    deadline_opp("d:a", pref=900),
                    deadline_opp("d:b", pref=None),
                ],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_r23_reverse_insertion_same_winner_key(self) -> None:
        a = [
            deadline_opp("d:a", pref=880),
            deadline_opp("d:b", pref=900),
            deadline_opp("d:c", pref=870),
        ]
        b = list(reversed(a))
        out_a = resolve(self.policy, opportunities=a)
        out_b = resolve(self.policy, opportunities=b)
        self.assertEqual(out_a.selected_candidate_id, out_b.selected_candidate_id)
        self.assertEqual(out_a.random_key, out_b.random_key)
        self.assertEqual(out_a.considered_candidate_ids, out_b.considered_candidate_ids)


class EdgeAndIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_r28_no_candidates_no_eligible_action(self) -> None:
        out = resolve(self.policy, human_state=hs(hunger=100, physical_fatigue=100))
        self.assertEqual(out.result_kind, "NO_ELIGIBLE_ACTION")
        self.assertIsNone(out.selected_candidate_id)

    def test_r29_oneshot_vs_split_same_resolution(self) -> None:
        start = hs(hunger=100, physical_fatigue=100)
        hs_a, hs_b = integrate_oneshot_vs_split(
            self.policy,
            start_hs=start,
            total_seconds=7200,
            parts=(2400, 2400, 2400),
        )
        self.assertEqual(hs_a, hs_b)
        # Same semantic boundary after identical state
        out_a = resolve(
            self.policy,
            human_state=hs_a,
            opportunities=[deadline_opp()],
            meal_feasible=True,
            boundary_fact=boundary(decision_key="boundary:r29"),
        )
        out_b = resolve(
            self.policy,
            human_state=hs_b,
            opportunities=[deadline_opp()],
            meal_feasible=True,
            boundary_fact=boundary(decision_key="boundary:r29"),
        )
        self.assertEqual(out_a.selected_candidate_id, out_b.selected_candidate_id)
        self.assertEqual(out_a.random_key, out_b.random_key)
        self.assertEqual(out_a.considered_candidate_ids, out_b.considered_candidate_ids)

    def test_r30_lower_tier_add_remove_higher_unchanged(self) -> None:
        base = resolve(self.policy, opportunities=[hard_opp()])
        with_lower = resolve(
            self.policy,
            opportunities=[hard_opp(), leisure_opp(), routine_opp()],
        )
        without_lower = resolve(self.policy, opportunities=[hard_opp()])
        self.assertEqual(base.selected_candidate_id, with_lower.selected_candidate_id)
        self.assertEqual(base.selected_candidate_id, without_lower.selected_candidate_id)
        self.assertEqual(base.random_key, with_lower.random_key)

    def test_single_candidate_no_rng(self) -> None:
        out = resolve(self.policy, opportunities=[deadline_opp()])
        self.assertIsNone(out.random_key)
        self.assertEqual(out.result_kind, "SELECT_CANDIDATE")

    def test_world_seed_required(self) -> None:
        with self.assertRaises(LifeEngineError):
            resolve_action_decision(
                world_seed="",
                character_id=CHARACTER_ID,
                policy=self.policy,
                boundary=boundary(),
                now=NOW,
                opportunities=[deadline_opp("d:a", pref=None), deadline_opp("d:b", pref=None)],
            )


