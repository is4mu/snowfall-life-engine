"""Commitment adaptation, timing, routing, and fail-closed contract tests."""

from __future__ import annotations

import copy
import unittest

import pytest

from engine.life.domain_adapters import adapt_structural_domains, effective_commitment_status_at
from engine.life.errors import ErrorCode, LifeEngineError

from tests.support.harnesses.domain_adapters import (
    CAMPUS,
    HOME,
    NOW,
    adapt,
    ctx,
    diag_codes,
    exact_commitment,
    opp_keys,
    route,
    window_commitment,
)


pytestmark = pytest.mark.unit


class CommitmentAdapterTests(unittest.TestCase):
    def test_c1_hard_work_collocated_activity(self) -> None:
        """C1: HARD WORK already at destination → WORK activity, ROUTE_NOT_REQUIRED."""
        c = exact_commitment(
            commitment_id="c1",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T15:00:00+09:00",
            },
            location_id=CAMPUS,
        )
        result = adapt(context=ctx(location=CAMPUS), commitments=[c])
        self.assertEqual(opp_keys(result), ["commitment:c1:activity"])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "WORK")
        self.assertEqual(opp.opportunity_class, "HARD_COMMITMENT")
        self.assertFalse(opp.soft_candidate)
        self.assertTrue(opp.location_feasible)
        self.assertIn("ROUTE_NOT_REQUIRED", diag_codes(result, source_ref="c1"))

    def test_c2_hard_work_travel_timing(self) -> None:
        """C2: remote HARD WORK inside travel window → TRAVEL_TO_COMMITMENT."""
        # duration_max=45 → latest_safe_departure = 14:00-45m = 13:15
        c = exact_commitment(commitment_id="c2", location_id=CAMPUS)
        result = adapt(
            context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route()],
        )
        self.assertIn("commitment:c2:travel", opp_keys(result))
        travel = next(o for o in result.opportunities if o.opportunity_key.endswith(":travel"))
        self.assertEqual(travel.action_kind, "TRAVEL_TO_COMMITMENT")
        self.assertEqual(travel.opportunity_class, "HARD_COMMITMENT")
        self.assertFalse(travel.soft_candidate)
        deps = [b for b in result.decision_boundaries if b.trigger_kind == "COMMITMENT_DEPARTURE"]
        self.assertEqual(len(deps), 0)  # departure 13:15 is already past at 13:20

    def test_c3_before_safe_departure_not_yet_eligible(self) -> None:
        """C3: before latest_safe_departure → boundary only, NOT_YET_ELIGIBLE."""
        c = exact_commitment(commitment_id="c3")
        result = adapt(
            context=ctx(now="2026-03-15T12:00:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route()],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("NOT_YET_ELIGIBLE", diag_codes(result, source_ref="c3"))
        deps = [b for b in result.decision_boundaries if b.trigger_kind == "COMMITMENT_DEPARTURE"]
        self.assertEqual(len(deps), 1)
        self.assertEqual(deps[0].due_at, "2026-03-15T13:15:00+09:00")
        planned = [b for b in result.decision_boundaries if b.trigger_kind == "PLANNED_WINDOW"]
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0].due_at, "2026-03-15T14:00:00+09:00")

    def test_c4_reverse_route_rejected(self) -> None:
        """C4: reverse-only route → ROUTE_DIRECTION_MISMATCH, no travel."""
        c = exact_commitment(commitment_id="c4")
        rev = route(
            route_profile_id="route-campus-home",
            origin_location_id=CAMPUS,
            destination_location_id=HOME,
        )
        result = adapt(
            context=ctx(now="2026-03-15T13:20:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[rev],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("ROUTE_DIRECTION_MISMATCH", diag_codes(result, source_ref="c4"))

    def test_c5_future_cancellation_does_not_leak(self) -> None:
        """C5: future CANCELLED transition must not leak into the past."""
        c = exact_commitment(
            commitment_id="c5",
            status="CANCELLED",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T15:00:00+09:00",
            },
            location_id=CAMPUS,
            status_history=[
                {
                    "changed_at": "2026-03-15T08:00:00+09:00",
                    "from": None,
                    "to": "PLANNED",
                    "reason": "created",
                    "source_ref": "engine:create",
                },
                {
                    "changed_at": "2026-03-15T16:00:00+09:00",
                    "from": "PLANNED",
                    "to": "CANCELLED",
                    "reason": "friend_cancelled",
                    "source_ref": "exogenous:1",
                },
            ],
        )
        # At noon the future cancel is invisible → still PLANNED.
        self.assertEqual(effective_commitment_status_at(c, now=NOW), "PLANNED")
        result = adapt(context=ctx(location=CAMPUS), commitments=[c])
        self.assertEqual(opp_keys(result), ["commitment:c5:activity"])
        # After cancel becomes visible → terminal.
        later = adapt(context=ctx(now="2026-03-15T16:30:00+09:00", location=CAMPUS), commitments=[c])
        self.assertEqual(opp_keys(later), [])
        self.assertIn("TERMINAL_STATUS", diag_codes(later, source_ref="c5"))

    def test_c6_soft_social_mapping(self) -> None:
        """C6: SOFT SOCIAL → SOCIAL_PROMISE / SOCIAL_PROMISE / soft."""
        c = window_commitment(
            commitment_id="c6",
            kind="SOCIAL",
            hardness="SOFT",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": "2026-03-15T11:00:00+09:00",
                "latest_start": "2026-03-15T14:00:00+09:00",
                "duration_min": 60,
                "duration_max": 90,
            },
            location_id=None,
        )
        result = adapt(commitments=[c])
        self.assertEqual(opp_keys(result), ["commitment:c6:activity"])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "SOCIAL_PROMISE")
        self.assertEqual(opp.opportunity_class, "SOCIAL_PROMISE")
        self.assertTrue(opp.soft_candidate)

    def test_c7_soft_work_mapping(self) -> None:
        """C7: SOFT WORK → WORK / ROUTINE_HABIT / soft."""
        c = exact_commitment(
            commitment_id="c7",
            kind="WORK",
            hardness="SOFT",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T13:00:00+09:00",
            },
            location_id=HOME,
        )
        result = adapt(context=ctx(location=HOME), commitments=[c])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "WORK")
        self.assertEqual(opp.opportunity_class, "ROUTINE_HABIT")
        self.assertTrue(opp.soft_candidate)

    def test_c8_soft_errand_mapping(self) -> None:
        """C8: SOFT ERRAND → ERRAND / ROUTINE_HABIT / COMMITMENT causal."""
        c = exact_commitment(
            commitment_id="c8",
            kind="ERRAND",
            hardness="SOFT",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:30:00+09:00",
                "planned_end": "2026-03-15T12:30:00+09:00",
            },
            location_id=HOME,
        )
        result = adapt(context=ctx(location=HOME), commitments=[c])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "ERRAND")
        self.assertEqual(opp.opportunity_class, "ROUTINE_HABIT")
        self.assertTrue(opp.soft_candidate)

    def test_c9_unsupported_soft_class(self) -> None:
        """C9: SOFT CLASS → no candidate + UNSUPPORTED_SOFT_COMMITMENT_KIND."""
        c = exact_commitment(
            commitment_id="c9",
            kind="CLASS",
            hardness="SOFT",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T12:30:00+09:00",
            },
            location_id=CAMPUS,
        )
        result = adapt(context=ctx(location=CAMPUS), commitments=[c])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("UNSUPPORTED_SOFT_COMMITMENT_KIND", diag_codes(result, source_ref="c9"))

    def test_c10_soft_exercise_never_kickboxing(self) -> None:
        """C10: SOFT EXERCISE unsupported — never infer KICKBOXING."""
        c = exact_commitment(
            commitment_id="c10",
            kind="EXERCISE",
            hardness="SOFT",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T12:00:00+09:00",
            },
            location_id=HOME,
        )
        result = adapt(context=ctx(location=HOME), commitments=[c])
        self.assertEqual(opp_keys(result), [])
        self.assertTrue(all(o.action_kind != "KICKBOXING" for o in result.opportunities))
        self.assertIn("UNSUPPORTED_SOFT_COMMITMENT_KIND", diag_codes(result, source_ref="c10"))

    def test_c11_hard_conflict_fail_closed(self) -> None:
        """C11: two distinct HARD commitments active → HARD_COMMITMENT_CONFLICT."""
        a = exact_commitment(
            commitment_id="c11a",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T15:00:00+09:00",
            },
            location_id=CAMPUS,
        )
        b = exact_commitment(
            commitment_id="c11b",
            kind="APPOINTMENT",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:30:00+09:00",
                "planned_end": "2026-03-15T12:30:00+09:00",
            },
            location_id=CAMPUS,
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(context=ctx(location=CAMPUS), commitments=[a, b])
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("HARD_COMMITMENT_CONFLICT", caught.exception.detail)

    def test_late_travel_emits_time_infeasible(self) -> None:
        """Regression: late travel emits with time_feasible=false."""
        # EXACT 14:00–14:30, duration_max=60 → departure 13:00; now=13:45
        # arrival 14:45 > planned_end → time_feasible=false but still emitted.
        c = exact_commitment(
            commitment_id="late-travel",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T14:00:00+09:00",
                "planned_end": "2026-03-15T14:30:00+09:00",
            },
            location_id=CAMPUS,
        )
        result = adapt(
            context=ctx(now="2026-03-15T13:45:00+09:00", location=HOME),
            commitments=[c],
            route_profiles=[route(duration_min=40, duration_max=60)],
        )
        self.assertEqual(opp_keys(result), ["commitment:late-travel:travel"])
        travel = result.opportunities[0]
        self.assertEqual(travel.action_kind, "TRAVEL_TO_COMMITMENT")
        self.assertFalse(travel.time_feasible)
        self.assertTrue(travel.physical_feasible)

    def test_hard_conflict_includes_late_travel(self) -> None:
        """Late time-infeasible travel still counts as HARD-active for conflict."""
        late = exact_commitment(
            commitment_id="conf-late",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T14:00:00+09:00",
                "planned_end": "2026-03-15T14:30:00+09:00",
            },
            location_id=CAMPUS,
        )
        other = exact_commitment(
            commitment_id="conf-other",
            kind="CLASS",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T13:00:00+09:00",
                "planned_end": "2026-03-15T15:00:00+09:00",
            },
            location_id=HOME,
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(
                context=ctx(now="2026-03-15T13:45:00+09:00", location=HOME),
                commitments=[late, other],
                route_profiles=[route(duration_min=40, duration_max=60)],
            )
        self.assertIn("HARD_COMMITMENT_CONFLICT", caught.exception.detail)

    def test_zero_width_window_decision_keys(self) -> None:
        """Regression: earliest==latest must not collide decision_key."""
        c = window_commitment(
            commitment_id="zw",
            kind="SOCIAL",
            hardness="SOFT",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": "2026-03-15T18:00:00+09:00",
                "latest_start": "2026-03-15T18:00:00+09:00",
                "duration_min": 30,
                "duration_max": 30,
            },
            location_id=None,
        )
        r1 = adapt(context=ctx(now="2026-03-15T12:00:00+09:00"), commitments=[c])
        r2 = adapt(context=ctx(now="2026-03-15T12:00:00+09:00"), commitments=[c])
        keys = [b.decision_key for b in r1.decision_boundaries]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(keys), 2)
        self.assertTrue(any("earliest_start" in k for k in keys))
        self.assertTrue(any("latest_start" in k for k in keys))
        self.assertEqual(keys, [b.decision_key for b in r2.decision_boundaries])

    def test_c12_hard_non_work_activity_kind(self) -> None:
        """C12: HARD CLASS → HARD_COMMITMENT_ACTIVITY (not WORK)."""
        c = exact_commitment(
            commitment_id="c12",
            kind="CLASS",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T12:30:00+09:00",
            },
            location_id=CAMPUS,
        )
        result = adapt(context=ctx(location=CAMPUS), commitments=[c])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "HARD_COMMITMENT_ACTIVITY")
        self.assertEqual(opp.opportunity_class, "HARD_COMMITMENT")

    def test_c13_window_open_and_boundaries(self) -> None:
        """C13: WINDOW open emits activity; future edges become boundaries only when future."""
        c = window_commitment(
            commitment_id="c13",
            kind="SOCIAL",
            hardness="SOFT",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": "2026-03-15T11:00:00+09:00",
                "latest_start": "2026-03-15T14:00:00+09:00",
                "duration_min": 30,
                "duration_max": 60,
            },
            location_id=None,
        )
        result = adapt(commitments=[c])
        self.assertEqual(opp_keys(result), ["commitment:c13:activity"])
        # earliest already past; latest may still be future boundary
        futures = [b.due_at for b in result.decision_boundaries]
        self.assertIn("2026-03-15T14:00:00+09:00", futures)

    def test_c14_after_window_no_candidate(self) -> None:
        """C14: after EXACT planned_end → no normal candidate."""
        c = exact_commitment(
            commitment_id="c14",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T08:00:00+09:00",
                "planned_end": "2026-03-15T10:00:00+09:00",
            },
            location_id=HOME,
            created_at="2026-03-14T08:00:00+09:00",
            status_history=[
                {
                    "changed_at": "2026-03-14T08:00:00+09:00",
                    "from": None,
                    "to": "PLANNED",
                    "reason": "created",
                    "source_ref": "engine:create",
                }
            ],
        )
        result = adapt(context=ctx(location=HOME), commitments=[c])
        self.assertEqual(opp_keys(result), [])

    def test_c15_null_location_route_not_required(self) -> None:
        """C15: location_id null → route not required; activity location_feasible."""
        c = exact_commitment(
            commitment_id="c15",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T13:00:00+09:00",
            },
            location_id=None,
        )
        result = adapt(commitments=[c], route_profiles=[route()])
        self.assertEqual(opp_keys(result), ["commitment:c15:activity"])
        self.assertTrue(result.opportunities[0].location_feasible)
        self.assertIn("ROUTE_NOT_REQUIRED", diag_codes(result, source_ref="c15"))

    def test_order_independence_and_no_mutation(self) -> None:
        a = exact_commitment(commitment_id="za", location_id=HOME, timing={
            "timing_kind": "EXACT",
            "planned_start": "2026-03-15T11:00:00+09:00",
            "planned_end": "2026-03-15T12:00:00+09:00",
        })
        b = exact_commitment(
            commitment_id="aa",
            kind="SOCIAL",
            hardness="SOFT",
            location_id=HOME,
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T12:00:00+09:00",
            },
        )
        raw = [a, b]
        snapshot = copy.deepcopy(raw)
        r1 = adapt_structural_domains(context=ctx(location=HOME), commitments=raw)
        r2 = adapt_structural_domains(context=ctx(location=HOME), commitments=list(reversed(raw)))
        self.assertEqual(opp_keys(r1), opp_keys(r2))
        self.assertEqual(raw, snapshot)

    def test_missing_physical_fact_fail_closed(self) -> None:
        from engine.life.domain_adapters import DomainAdapterContext

        c = exact_commitment(
            commitment_id="phys",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T13:00:00+09:00",
            },
            location_id=HOME,
        )
        bare = DomainAdapterContext(
            now=NOW,
            current_location_id=HOME,
            available_window_min=120,
            physical_feasible_by_action={"STUDY": True},  # WORK omitted
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(context=bare, commitments=[c])
        self.assertIn("physical feasibility", caught.exception.detail)

    def test_never_upgrade_soft_near_start(self) -> None:
        c = exact_commitment(
            commitment_id="soft-near",
            kind="WORK",
            hardness="SOFT",
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T12:00:00+09:00",
                "planned_end": "2026-03-15T13:00:00+09:00",
            },
            location_id=HOME,
        )
        result = adapt(context=ctx(now="2026-03-15T12:00:00+09:00", location=HOME), commitments=[c])
        self.assertEqual(result.opportunities[0].opportunity_class, "ROUTINE_HABIT")
        self.assertTrue(result.opportunities[0].soft_candidate)
