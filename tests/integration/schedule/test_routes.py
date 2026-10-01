"""Route adaptation and decision-resolver integration contracts."""

from __future__ import annotations

import copy
import unittest

import pytest

from engine.life.decisions import (
    opportunity_to_candidate,
    parse_resolved_opportunity,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.policy import load_policy

from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.harnesses.domain_adapters import (
    CAMPUS,
    DESK,
    HOME,
    adapt,
    ctx,
    diag_codes,
    exact_commitment,
    obligation,
    opp_keys,
    route,
    window_commitment,
)
from tests.support.harnesses.resolver import boundary, hs, resolve


pytestmark = pytest.mark.integration


class RouteAdapterTests(unittest.TestCase):
    def test_r1_duration_max_for_safe_departure(self) -> None:
        """R1: structural safety uses duration_max (not min / average)."""
        c = exact_commitment(commitment_id="r1")
        # duration_max=45 → departure 13:15; if min(25) were used would be 13:35
        result = adapt(
            context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route(duration_min=25, duration_max=45)],
        )
        self.assertIn("commitment:r1:travel", opp_keys(result))
        # At 13:20 with duration_max, arrival 14:05 <= planned_end 18:00
        # If someone used duration_min incorrectly for eligibility at 13:30-ish — covered by departure math:
        early = adapt(
            context=ctx(now="2026-03-15T13:10:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route(duration_min=25, duration_max=45)],
        )
        self.assertEqual(opp_keys(early), [])
        deps = [b for b in early.decision_boundaries if b.trigger_kind == "COMMITMENT_DEPARTURE"]
        self.assertEqual(deps[0].due_at, "2026-03-15T13:15:00+09:00")

    def test_r2_reverse_forbidden(self) -> None:
        """R2: reverse route must not auto-use."""
        c = exact_commitment(commitment_id="r2")
        rev = route(
            route_profile_id="rev",
            origin_location_id=CAMPUS,
            destination_location_id=HOME,
        )
        result = adapt(
            context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[rev],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("ROUTE_DIRECTION_MISMATCH", diag_codes(result, source_ref="r2"))

    def test_r3_ambiguous_nonidentical_routes_fail_closed(self) -> None:
        """R3: multiple forward matches with different durations → fail closed."""
        c = exact_commitment(commitment_id="r3")
        r_a = route(route_profile_id="a", duration_min=20, duration_max=40)
        r_b = route(route_profile_id="b", duration_min=30, duration_max=50)
        with self.assertRaises(LifeEngineError) as caught:
            adapt(
                context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
                commitments=[c],
                route_profiles=[r_a, r_b],
            )
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("ambiguous", caught.exception.detail)

    def test_r3b_identical_semantics_ok(self) -> None:
        c = exact_commitment(commitment_id="r3b")
        r_a = route(route_profile_id="a")
        r_b = route(route_profile_id="b")  # same semantics, different id
        result = adapt(
            context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[r_b, r_a],
        )
        self.assertIn("commitment:r3b:travel", opp_keys(result))


class ResolverIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_policy(POLICY_V2_CANDIDATE_PATH)

    def test_i1_slice2d_causal_validation(self) -> None:
        """I1: adapter opportunities parse / convert under decision resolver causal table."""
        cases = [
            exact_commitment(
                commitment_id="i1-hard",
                timing={
                    "timing_kind": "EXACT",
                    "planned_start": "2026-03-15T11:00:00+09:00",
                    "planned_end": "2026-03-15T15:00:00+09:00",
                },
                location_id=CAMPUS,
            ),
            exact_commitment(
                commitment_id="i1-soft-errand",
                kind="ERRAND",
                hardness="SOFT",
                timing={
                    "timing_kind": "EXACT",
                    "planned_start": "2026-03-15T11:00:00+09:00",
                    "planned_end": "2026-03-15T12:00:00+09:00",
                },
                location_id=HOME,
            ),
            exact_commitment(
                commitment_id="i1-soft-social-travel",
                kind="SOCIAL",
                hardness="SOFT",
                timing={
                    "timing_kind": "EXACT",
                    "planned_start": "2026-03-15T14:00:00+09:00",
                    "planned_end": "2026-03-15T16:00:00+09:00",
                },
                location_id=CAMPUS,
            ),
        ]
        result = adapt(
            context=ctx(now="2026-03-15T13:30:00+09:00", location=HOME),
            commitments=cases,
            route_profiles=[route()],
            obligation_tasks=[
                obligation(
                    task_id="i1-study",
                    priority="URGENT",
                    location_constraints=[DESK],
                    due_at="2026-03-15T18:00:00+09:00",
                )
            ],
        )
        self.assertGreaterEqual(len(result.opportunities), 2)
        for opp in result.opportunities:
            parsed = parse_resolved_opportunity(opp)
            cand = opportunity_to_candidate(
                character_id="fixture-character",
                decision_key="i1",
                opportunity=parsed,
            )
            self.assertEqual(cand.action_kind, opp.action_kind)

    def test_i2_hard_travel_beats_urgent_hunger(self) -> None:
        """I2: HARD TRAVEL_TO_COMMITMENT beats URGENT biological MEAL."""
        c = exact_commitment(commitment_id="i2")
        adapted = adapt(
            context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route()],
        )
        travel = next(o for o in adapted.opportunities if o.action_kind == "TRAVEL_TO_COMMITMENT")
        out = resolve(
            self.policy,
            human_state=hs(hunger=950),
            opportunities=[travel],
            boundary_fact=boundary(decision_key="i2"),
            now="2026-03-15T13:20:00+09:00",
            meal_feasible=True,
            rest_feasible=True,
        )
        self.assertEqual(out.selected_priority_tier, "HARD_COMMITMENT")
        self.assertEqual(out.selected_action_kind, "TRAVEL_TO_COMMITMENT")

    def test_i3_deadline_study_beats_soft_social(self) -> None:
        """I3: DEADLINE STUDY beats soft SOCIAL_PROMISE."""
        social = window_commitment(
            commitment_id="i3-social",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": "2026-03-15T11:00:00+09:00",
                "latest_start": "2026-03-15T14:00:00+09:00",
                "duration_min": 30,
                "duration_max": 60,
            },
            location_id=None,
        )
        task = obligation(
            task_id="i3-study",
            priority="URGENT",
            location_constraints=[DESK],
            due_at="2026-03-15T18:00:00+09:00",
            effort_remaining_min=60,
        )
        adapted = adapt(
            context=ctx(location=DESK),
            commitments=[social],
            obligation_tasks=[task],
        )
        out = resolve(
            self.policy,
            human_state=hs(),
            opportunities=list(adapted.opportunities),
            boundary_fact=boundary(
                decision_key="i3",
                trigger_kind="OBLIGATION",
                metric=None,
                target_band=None,
                reason_code="TASK_DUE",
            ),
            now="2026-03-15T12:00:00+09:00",
        )
        self.assertEqual(out.selected_priority_tier, "DEADLINE_TASK")
        self.assertEqual(out.selected_action_kind, "STUDY")
    def test_i4_no_runtime_mutation(self) -> None:
        """I4: adapter must not mutate input mappings."""
        c = exact_commitment(commitment_id="i4", location_id=HOME, timing={
            "timing_kind": "EXACT",
            "planned_start": "2026-03-15T11:00:00+09:00",
            "planned_end": "2026-03-15T12:00:00+09:00",
        })
        t = obligation(task_id="i4t", location_constraints=[HOME])
        r = route()
        snap = (copy.deepcopy(c), copy.deepcopy(t), copy.deepcopy(r))
        adapt(context=ctx(location=HOME), commitments=[c], obligation_tasks=[t], route_profiles=[r])
        self.assertEqual((c, t, r), snap)

    def test_i5_activity_and_travel_coexist_with_location_false(self) -> None:
        """I5: during window away from location → activity(location_feasible=false)+travel."""
        c = exact_commitment(
            commitment_id="i5",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T12:00:00+09:00",
                "planned_end": "2026-03-15T16:00:00+09:00",
            },
            location_id=CAMPUS,
        )
        result = adapt(
            context=ctx(now="2026-03-15T12:30:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route()],
        )
        keys = set(opp_keys(result))
        self.assertEqual(keys, {"commitment:i5:activity", "commitment:i5:travel"})
        act = next(o for o in result.opportunities if o.opportunity_key.endswith(":activity"))
        self.assertFalse(act.location_feasible)

    def test_i6_soft_travel_causal_ok(self) -> None:
        """I6: SOFT SOCIAL travel uses SOCIAL_PROMISE / TRAVEL_TO_COMMITMENT causal pair."""
        c = exact_commitment(
            commitment_id="i6",
            kind="SOCIAL",
            hardness="SOFT",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T14:00:00+09:00",
                "planned_end": "2026-03-15T16:00:00+09:00",
            },
            location_id=CAMPUS,
        )
        result = adapt(
            context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route()],
        )
        travel = next(o for o in result.opportunities if o.action_kind == "TRAVEL_TO_COMMITMENT")
        self.assertEqual(travel.opportunity_class, "SOCIAL_PROMISE")
        self.assertTrue(travel.soft_candidate)
        # No HARD COMMITMENT_DEPARTURE for SOFT.
        self.assertFalse(any(b.trigger_kind == "COMMITMENT_DEPARTURE" for b in result.decision_boundaries))
        opportunity_to_candidate(
            character_id="fixture-character",
            decision_key="i6",
            opportunity=travel,
        )
