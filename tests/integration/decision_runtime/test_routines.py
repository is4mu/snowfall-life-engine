"""Routine adapters integrated with the decision resolver."""

from __future__ import annotations

import copy
import unittest

import pytest

from engine.life.decisions import (
    OPPORTUNITY_CLASSES,
    opportunity_to_candidate,
    parse_resolved_opportunity,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.policy import load_policy

from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.harnesses.resolver import boundary, hs, resolve
from tests.support.harnesses.routines import (
    HOME,
    adapt,
    ctx,
    diag_codes,
    household,
    opp_keys,
    policy,
    session,
)


pytestmark = pytest.mark.integration


class RoutineResolverIntegrationTests(unittest.TestCase):
    def test_i1_household_causal_pair(self) -> None:
        """I1: HOUSEHOLD / ROUTINE_HABIT / ROUTINE validates through decision resolver."""
        t = household(
            household_task_id="i1",
            created_at="2026-03-14T06:00:00+09:00",
        )
        result = adapt(household_tasks=[t])
        opp = result.opportunities[0]
        cand = opportunity_to_candidate(
            character_id="fixture-character",
            decision_key="i1",
            opportunity=opp,
        )
        self.assertEqual(cand.action_kind, "HOUSEHOLD")
        out = resolve(
            policy=policy(),
            human_state=hs(),
            opportunities=[opp],
            boundary_fact=boundary(trigger_kind="IDLE", decision_key="idle:i1", reason_code="IDLE", metric=None, target_band=None),
        )
        self.assertEqual(out.selected_action_kind, "HOUSEHOLD")
        self.assertEqual(out.selected_priority_tier, "ROUTINE_HABIT")

    def test_i2_kickboxing_causal_pair(self) -> None:
        result = adapt(kickboxing_sessions=[])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "KICKBOXING")
        cand = opportunity_to_candidate(
            character_id="fixture-character",
            decision_key="i2",
            opportunity=opp,
        )
        self.assertEqual(cand.action_kind, "KICKBOXING")
        out = resolve(
            policy=policy(),
            human_state=hs(),
            opportunities=[opp],
            boundary_fact=boundary(trigger_kind="IDLE", decision_key="idle:i2", reason_code="IDLE", metric=None, target_band=None),
        )
        self.assertEqual(out.selected_action_kind, "KICKBOXING")

    def test_i3_same_tier_household_and_kickboxing(self) -> None:
        """I3: both ROUTINE_HABIT — decision resolver same-tier selection (no adapter rank)."""
        t = household(
            household_task_id="i3",
            created_at="2026-03-14T06:00:00+09:00",
        )
        result = adapt(household_tasks=[t], kickboxing_sessions=[])
        keys = opp_keys(result)
        self.assertIn("household:i3:work", keys)
        self.assertIn("routine:kickboxing:self:2026-03-15", keys)
        self.assertTrue(all(o.opportunity_class == "ROUTINE_HABIT" for o in result.opportunities))
        self.assertTrue(all(o.local_preference_permille is None for o in result.opportunities))

    def test_i4_hard_commitment_beats_routine(self) -> None:
        """I4: HARD_COMMITMENT candidate beats ROUTINE household/kickboxing."""
        t = household(
            household_task_id="i4",
            created_at="2026-03-14T06:00:00+09:00",
        )
        routine = adapt(household_tasks=[t], kickboxing_sessions=[])
        hard_opp = parse_resolved_opportunity(
            {
                "opportunity_key": "commitment:hard:activity",
                "opportunity_class": "HARD_COMMITMENT",
                "action_kind": "WORK",
                "source_kind": "COMMITMENT",
                "source_ref": "hard-1",
                "soft_candidate": False,
                "time_feasible": True,
                "location_feasible": True,
                "physical_feasible": True,
                "domain_guard_satisfied": True,
                "local_preference_permille": None,
                "rule_ids": ["test.hard"],
            }
        )
        opps = list(routine.opportunities) + [hard_opp]
        for o in opps:
            opportunity_to_candidate(
                character_id="fixture-character",
                decision_key="i4",
                opportunity=o,
            )
        out = resolve(
            policy=policy(),
            human_state=hs(),
            opportunities=opps,
            boundary_fact=boundary(trigger_kind="IDLE", decision_key="idle:i4", reason_code="IDLE", metric=None, target_band=None),
        )
        self.assertEqual(out.selected_action_kind, "WORK")
        self.assertEqual(out.selected_priority_tier, "HARD_COMMITMENT")

    def test_i5_no_rest_invented(self) -> None:
        result = adapt(household_tasks=[], kickboxing_sessions=[])
        self.assertTrue(all(o.action_kind != "REST" for o in result.opportunities))
        self.assertTrue(
            all(o.action_kind in {"HOUSEHOLD", "KICKBOXING"} for o in result.opportunities)
        )

    def test_i6_empty_household_still_valid(self) -> None:
        """I6: empty household backlog is valid; kickboxing may still emit."""
        result = adapt(household_tasks=[], kickboxing_sessions=[])
        self.assertEqual(opp_keys(result), ["routine:kickboxing:self:2026-03-15"])

    def test_i7_empty_both_with_low_exercise(self) -> None:
        """I7: empty household + LOW exercise → empty opportunities valid."""
        s1 = session(session_id="i7a")
        s2 = session(
            session_id="i7b",
            actual_start="2026-03-12T18:00:00+09:00",
            actual_end="2026-03-12T19:00:00+09:00",
            finalized_at="2026-03-12T19:05:00+09:00",
        )
        result = adapt(household_tasks=[], kickboxing_sessions=[s1, s2])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("EXERCISE_LOW_OPPORTUNITY", diag_codes(result))

    def test_i8_no_input_mutation_no_causal_widen(self) -> None:
        """I8: adapter does not mutate inputs; causal table unchanged for this slice."""
        t = household(
            household_task_id="i8",
            created_at="2026-03-14T06:00:00+09:00",
        )
        s = session(session_id="i8s")
        t_snap, s_snap = copy.deepcopy(t), copy.deepcopy(s)
        adapt(household_tasks=[t], kickboxing_sessions=[s])
        self.assertEqual(t, t_snap)
        self.assertEqual(s, s_snap)
        # HOUSEHOLD ROUTINE + KICKBOXING ROUTINE already present pre-existing.
        from engine.life import decisions as dec

        self.assertIn(("ROUTINE_HABIT", "ROUTINE"), dec._ACTION_TIER_SOURCE["HOUSEHOLD"])
        self.assertIn(("ROUTINE_HABIT", "ROUTINE"), dec._ACTION_TIER_SOURCE["KICKBOXING"])
        self.assertNotIn(
            ("DEADLINE_TASK", "ROUTINE"),
            dec._ACTION_TIER_SOURCE["HOUSEHOLD"],
        )

    def test_i9_public_policy_fixture_contract(self) -> None:
        """I9: public synthetic policy remains valid and adds no new routine tier."""
        loaded = load_policy(POLICY_V2_CANDIDATE_PATH)
        self.assertEqual(loaded["character_id"], "fixture-character")
        self.assertTrue(
            str(loaded["behavior_policy_version"]).startswith("public-fixture-")
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(
                context=ctx(available_window_min=True),  # type: ignore[arg-type]
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("ROUTINE_HABIT", OPPORTUNITY_CLASSES)
