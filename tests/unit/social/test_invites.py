"""Social invite opportunity activation and timing contracts."""

from __future__ import annotations

import unittest

import pytest
from datetime import date

from tests.support.harnesses.social_opportunities import (
    SAYAKA,
    assignment,
    contact,
    generate,
    policy,
    slots_for,
)


pytestmark = pytest.mark.unit


class InviteOpportunityTests(unittest.TestCase):
    def _local_with_history(self):
        # History only needed for CONTACT; INVITE ignores recency.
        return generate(
            assignments=[assignment(SAYAKA, "LOCAL_CLOSE_INVITER")],
            contacts=[
                contact(
                    contact_id="sayaka-hist",
                    person_id=SAYAKA,
                    actual_end="2026-03-01T12:00:00+09:00",
                    finalized_at="2026-03-01T12:05:00+09:00",
                )
            ],
        )

    def test_v1_invite_activation_base_only(self) -> None:
        """V1: invite activation uses base only (no recency multiplier)."""
        result = self._local_with_history()
        invite = policy()["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"]
        base = invite["base_activation_permille"]
        for r in result.slot_resolutions:
            if r.slot_kind != "INVITE":
                continue
            if r.outcome == "PENDING":
                continue
            self.assertEqual(r.activation_threshold_permille, base)
            self.assertIsNone(r.recency_multiplier_permille)
            self.assertIsNone(r.recency_seconds)
            self.assertIsNone(r.recency_band_key)

    def test_v2_activation_and_timing_keys_differ(self) -> None:
        """V2: activation random and timing random are separate keys."""
        import copy

        from tests.support.harnesses.social_opportunities import policy as load_pol

        pol = copy.deepcopy(load_pol())
        # Force invite activation always (test-only); fixture file unchanged.
        pol["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"][
            "base_activation_permille"
        ] = 1000
        result = generate(
            assignments=[assignment(SAYAKA, "LOCAL_CLOSE_INVITER")],
            contacts=[
                contact(
                    contact_id="sayaka-hist-v2",
                    person_id=SAYAKA,
                    actual_end="2026-03-01T12:00:00+09:00",
                    finalized_at="2026-03-01T12:05:00+09:00",
                )
            ],
            behavior_policy=pol,
        )
        invites = [
            o for o in result.opportunities if o.opportunity_kind == "INVITE_OPPORTUNITY"
        ]
        self.assertTrue(invites)
        for o in invites:
            self.assertIsNotNone(o.timing_random_key)
            self.assertNotEqual(o.activation_random_key, o.timing_random_key)

    def test_v3_same_day_target_equals_slot_date(self) -> None:
        """V3: SAME_DAY → target_local_date == slot.local_date."""
        import copy

        from tests.support.harnesses.social_opportunities import policy as load_pol

        pol = copy.deepcopy(load_pol())
        pol["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"][
            "base_activation_permille"
        ] = 1000
        pol["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"][
            "same_day_split_permille"
        ] = 1000
        pol["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"][
            "future_split_permille"
        ] = 0
        result = generate(
            assignments=[assignment(SAYAKA, "LOCAL_CLOSE_INVITER")],
            contacts=[
                contact(
                    contact_id="sayaka-hist-v3",
                    person_id=SAYAKA,
                    actual_end="2026-03-01T12:00:00+09:00",
                    finalized_at="2026-03-01T12:05:00+09:00",
                )
            ],
            behavior_policy=pol,
        )
        slot_by_id = {s.slot_id: s for s in result.slots}
        same_day = [
            o for o in result.opportunities if o.invite_timing_kind == "SAME_DAY_SOFT"
        ]
        self.assertTrue(same_day)
        for o in same_day:
            slot = slot_by_id[o.source_ref]
            self.assertEqual(o.target_local_date, slot.local_date)
            self.assertIsNone(o.future_day_random_key)

    def test_v4_future_offset_within_inclusive_horizon(self) -> None:
        """V4: FUTURE offset within policy inclusive horizon."""
        import copy

        from tests.support.harnesses.social_opportunities import policy as load_pol

        pol = copy.deepcopy(load_pol())
        invite_pol = pol["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"]
        invite_pol["base_activation_permille"] = 1000
        invite_pol["same_day_split_permille"] = 0
        invite_pol["future_split_permille"] = 1000
        lo = invite_pol["future_horizon_min_days"]
        hi = invite_pol["future_horizon_max_days"]
        result = generate(
            assignments=[assignment(SAYAKA, "LOCAL_CLOSE_INVITER")],
            contacts=[
                contact(
                    contact_id="sayaka-hist-v4",
                    person_id=SAYAKA,
                    actual_end="2026-03-01T12:00:00+09:00",
                    finalized_at="2026-03-01T12:05:00+09:00",
                )
            ],
            behavior_policy=pol,
        )
        slot_by_id = {s.slot_id: s for s in result.slots}
        futures = [
            o for o in result.opportunities if o.invite_timing_kind == "FUTURE_SOFT"
        ]
        self.assertTrue(futures)
        for o in futures:
            slot = slot_by_id[o.source_ref]
            assert o.target_local_date is not None
            offset = (
                date.fromisoformat(o.target_local_date)
                - date.fromisoformat(slot.local_date)
            ).days
            self.assertGreaterEqual(offset, lo)
            self.assertLessEqual(offset, hi)
            self.assertIsNotNone(o.future_day_random_key)

    def test_v5_same_seed_slot_same_timing_offset(self) -> None:
        """V5: same seed/slot → same timing/offset."""
        a = self._local_with_history()
        b = self._local_with_history()
        self.assertEqual(
            [
                (o.opportunity_id, o.invite_timing_kind, o.target_local_date,
                 o.timing_random_key, o.future_day_random_key)
                for o in a.opportunities
                if o.opportunity_kind == "INVITE_OPPORTUNITY"
            ],
            [
                (o.opportunity_id, o.invite_timing_kind, o.target_local_date,
                 o.timing_random_key, o.future_day_random_key)
                for o in b.opportunities
                if o.opportunity_kind == "INVITE_OPPORTUNITY"
            ],
        )

    def test_v6_no_exact_clock_time(self) -> None:
        """V6: no exact clock time fields."""
        result = self._local_with_history()
        for o in result.opportunities:
            payload = o.as_dict()
            for forbidden in ("start_at", "exact_time", "due_at", "planned_start"):
                self.assertNotIn(forbidden, payload)

    def test_v7_no_commitment(self) -> None:
        """V7: no Commitment created."""
        result = self._local_with_history()
        for o in result.opportunities:
            payload = o.as_dict()
            self.assertNotIn("commitment_id", payload)
            self.assertNotIn("accepted", payload)

    def test_v8_no_acceptance_result(self) -> None:
        """V8: no acceptance/decline/defer result."""
        result = self._local_with_history()
        for o in result.opportunities:
            payload = o.as_dict()
            for forbidden in ("accepted", "declined", "deferred", "response"):
                self.assertNotIn(forbidden, payload)
        # Invite slots exist independently of character capacity fields.
        self.assertTrue(slots_for(result, SAYAKA, "INVITE"))
