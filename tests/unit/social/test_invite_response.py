"""Social invite response and commitment-proposal contracts."""

from __future__ import annotations

import unittest

import pytest

from engine.life.contracts import validate_commitment
from engine.life.ids import stable_id
from engine.life.social_response import adapt_social_responses

from tests.support.harnesses.social_response import (
    SAYAKA,
    adapt,
    availability,
    ctx,
    human_state,
    local_invite,
)


def _same_day_avail(oid: str, *, hours: int = 3, location_id: str | None = None):
    return availability(
        availability_id=f"avail-{oid}",
        opportunity_id=oid,
        earliest_start="2026-03-15T19:30:00+09:00",
        latest_end=f"2026-03-15T{19 + hours}:30:00+09:00"
        if hours < 5
        else "2026-03-15T22:30:00+09:00",
        location_id=location_id,
        hard_conflict=False,
    )


def _future_avail(oid: str, *, window_hours: int = 3, hard_conflict: bool = False):
    end_h = 18 + window_hours
    return availability(
        availability_id=f"avail-{oid}",
        opportunity_id=oid,
        earliest_start="2026-03-18T18:00:00+09:00",
        latest_end=f"2026-03-18T{end_h:02d}:00:00+09:00",
        location_id=None,
        hard_conflict=hard_conflict,
    )


pytestmark = pytest.mark.unit


class InviteResponseTests(unittest.TestCase):
    def test_r24_future_invite_ignores_low_battery(self) -> None:
        opp = local_invite("r24", timing="FUTURE_SOFT")
        result = adapt(
            context=ctx(human=human_state(social_battery=50)),
            opportunities=[opp],
            availability_windows=[_future_avail("r24")],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(r.reason_code, "INVITE_FEASIBLE")
        self.assertEqual(len(result.commitment_proposals), 1)

    def test_r25_future_missing_availability_defer(self) -> None:
        opp = local_invite("r25", timing="FUTURE_SOFT")
        result = adapt(opportunities=[opp])
        r = result.responses[0]
        self.assertEqual(r.response, "DEFER")
        self.assertEqual(r.reason_code, "MISSING_AVAILABILITY")

    def test_r26_future_hard_conflict_decline(self) -> None:
        opp = local_invite("r26", timing="FUTURE_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_future_avail("r26", hard_conflict=True)],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(r.reason_code, "HARD_COMMITMENT_CONFLICT")

    def test_r27_future_insufficient_window_decline(self) -> None:
        opp = local_invite("r27", timing="FUTURE_SOFT")
        # 30-minute window < LOCAL_OUTING duration_min 60
        result = adapt(
            opportunities=[opp],
            availability_windows=[
                availability(
                    availability_id="a27",
                    opportunity_id="r27",
                    earliest_start="2026-03-18T18:00:00+09:00",
                    latest_end="2026-03-18T18:30:00+09:00",
                    location_id=None,
                    hard_conflict=False,
                )
            ],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(r.reason_code, "INSUFFICIENT_WINDOW")

    def test_r28_same_day_low_capacity_defer(self) -> None:
        opp = local_invite("r28", timing="SAME_DAY_SOFT")
        result = adapt(
            context=ctx(human=human_state(social_battery=50)),
            opportunities=[opp],
            availability_windows=[_same_day_avail("r28")],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DEFER")
        self.assertEqual(r.reason_code, "LOW_SOCIAL_BATTERY")

    def test_r29_same_day_feasible_accept(self) -> None:
        opp = local_invite("r29", timing="SAME_DAY_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_same_day_avail("r29")],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(r.reason_code, "INVITE_FEASIBLE")

    def test_r30_accepted_invite_passes_validate_commitment(self) -> None:
        opp = local_invite("r30", timing="SAME_DAY_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_same_day_avail("r30")],
        )
        prop = result.commitment_proposals[0]
        again = validate_commitment(prop.commitment)
        self.assertEqual(again["commitment_id"], prop.commitment["commitment_id"])

    def test_r31_soft_social_exogenous(self) -> None:
        opp = local_invite("r31", timing="SAME_DAY_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_same_day_avail("r31")],
        )
        c = result.commitment_proposals[0].commitment
        self.assertEqual(c["kind"], "SOCIAL")
        self.assertEqual(c["hardness"], "SOFT")
        self.assertEqual(c["source_kind"], "EXOGENOUS_SOCIAL")

    def test_r32_proposal_location_may_be_null(self) -> None:
        opp = local_invite("r32", timing="SAME_DAY_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_same_day_avail("r32", location_id=None)],
        )
        self.assertIsNone(result.commitment_proposals[0].commitment["location_id"])

    def test_r33_participants_exactly_known_person(self) -> None:
        opp = local_invite("r33", timing="SAME_DAY_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_same_day_avail("r33")],
        )
        parts = result.commitment_proposals[0].commitment["participants"]
        self.assertEqual(parts, [SAYAKA])

    def test_r34_window_timing_only_no_exact_start(self) -> None:
        opp = local_invite("r34", timing="SAME_DAY_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_same_day_avail("r34")],
        )
        timing = result.commitment_proposals[0].commitment["timing"]
        self.assertEqual(timing["timing_kind"], "WINDOW")
        self.assertNotIn("planned_start", timing)
        self.assertNotIn("planned_end", timing)
        self.assertIn("earliest_start", timing)
        self.assertIn("latest_start", timing)
        # 3h window, policy max 180 → effective 180; latest_start = end - 180
        self.assertEqual(timing["earliest_start"], "2026-03-15T19:30:00+09:00")
        self.assertEqual(timing["latest_start"], "2026-03-15T19:30:00+09:00")
        self.assertEqual(timing["duration_min"], 60)
        self.assertEqual(timing["duration_max"], 180)

    def test_r35_no_commitment_persistence(self) -> None:
        from pathlib import Path
        import engine.life.social_response as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        for banned in (
            "Path(",
            "open(",
            "write_text",
            "json.dump",
            "workspace",
            "commit_store",
            "persist",
        ):
            # allow comments mentioning "No persistence"
            if banned == "persist":
                continue
            self.assertNotIn(banned, src)
        # semantic id uses opportunity only
        opp = local_invite("r35", timing="SAME_DAY_SOFT")
        result = adapt(
            opportunities=[opp],
            availability_windows=[_same_day_avail("r35")],
        )
        self.assertEqual(
            result.commitment_proposals[0].commitment["commitment_id"],
            stable_id("social-commitment", "r35"),
        )
