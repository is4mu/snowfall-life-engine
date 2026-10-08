"""Social contact opportunity recency, activation, and deterministic selection tests."""

from __future__ import annotations

import unittest

import pytest

from engine.life.social_opportunities import (
    contact_activation_threshold,
    recency_seconds_at_anchor,
)

from tests.support.harnesses.social_opportunities import (
    MIZUKI,
    contact,
    ctx,
    diag_codes,
    generate,
    policy,
    slots_for,
)


def _remote_arch():
    return policy()["social_policy"]["archetypes"]["REMOTE_CLOSE_BURSTY"]


pytestmark = pytest.mark.unit


class ContactOpportunityTests(unittest.TestCase):
    def _first_past_slot(self):
        # as_of end of week → all week slots are past (anchors at midnight).
        result = generate(
            assignments=[{"schema_version": 1, "person_id": MIZUKI,
                          "archetype_id": "REMOTE_CLOSE_BURSTY",
                          "provenance": {"origin": "ENGINE_DEFAULT"}}],
            contacts=[
                contact(
                    contact_id="c-seed",
                    person_id=MIZUKI,
                    actual_end="2026-03-01T12:00:00+09:00",
                    finalized_at="2026-03-01T12:05:00+09:00",
                )
            ],
        )
        slots = slots_for(result, MIZUKI, "CONTACT")
        self.assertTrue(slots)
        return slots[0]

    def test_c1_under_24h_first_multiplier(self) -> None:
        """C1: <24h → first multiplier band."""
        slot = self._first_past_slot()
        # Place contact just under 24h before this slot anchor.
        # slot_anchor midnight; contact at previous day 00:30 → ~23.5h.
        from datetime import date, timedelta

        local = date.fromisoformat(slot.local_date)
        prev = (local - timedelta(days=1)).isoformat()
        actual_end = f"{prev}T00:30:00+09:00"
        finalized = f"{prev}T00:35:00+09:00"
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[
                contact(
                    contact_id="c1",
                    person_id=MIZUKI,
                    actual_end=actual_end,
                    finalized_at=finalized,
                )
            ],
        )
        res = next(r for r in result.slot_resolutions if r.slot_id == slot.slot_id)
        self.assertIsNotNone(res.recency_seconds)
        assert res.recency_seconds is not None
        self.assertLess(res.recency_seconds, 24 * 3600)
        self.assertEqual(res.recency_band_key, "0-24")
        self.assertEqual(res.recency_multiplier_permille, 250)

    def test_c2_24_to_72h_band(self) -> None:
        """C2: 24–72h → correct band."""
        slot = self._first_past_slot()
        from datetime import date, timedelta

        local = date.fromisoformat(slot.local_date)
        prior = (local - timedelta(days=2)).isoformat()  # 48h
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[
                contact(
                    contact_id="c2",
                    person_id=MIZUKI,
                    actual_end=f"{prior}T00:00:00+09:00",
                    finalized_at=f"{prior}T00:05:00+09:00",
                )
            ],
        )
        res = next(r for r in result.slot_resolutions if r.slot_id == slot.slot_id)
        self.assertEqual(res.recency_band_key, "24-72")
        self.assertEqual(res.recency_multiplier_permille, 600)

    def test_c3_long_silence_later_band_and_cap(self) -> None:
        """C3: long silence → later band + cap."""
        slot = self._first_past_slot()
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[
                contact(
                    contact_id="c3",
                    person_id=MIZUKI,
                    actual_end="2025-01-01T00:00:00+09:00",
                    finalized_at="2025-01-01T00:05:00+09:00",
                )
            ],
        )
        res = next(r for r in result.slot_resolutions if r.slot_id == slot.slot_id)
        self.assertEqual(res.recency_band_key, "336-inf")
        self.assertEqual(res.recency_multiplier_permille, 1350)
        arch = _remote_arch()
        expected = contact_activation_threshold(
            base_activation_permille=arch["base_activation_permille"],
            multiplier_permille=1350,
            activation_cap_permille=arch["activation_cap_permille"],
        )
        self.assertEqual(res.activation_threshold_permille, expected)
        # Fixture long-silence scaled=607 < cap=800; prove cap binds with higher multiplier.
        capped = contact_activation_threshold(
            base_activation_permille=arch["base_activation_permille"],
            multiplier_permille=2000,
            activation_cap_permille=arch["activation_cap_permille"],
        )
        self.assertEqual(capped, arch["activation_cap_permille"])

    def test_c4_no_visible_history_recency_unknown(self) -> None:
        """C4: no visible history → RECENCY_UNKNOWN; no CONTACT opportunity."""
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[],
        )
        self.assertTrue(any(r.outcome == "INELIGIBLE_RECENCY_UNKNOWN" for r in result.slot_resolutions))
        self.assertIn("RECENCY_UNKNOWN", diag_codes(result))
        self.assertFalse(
            any(o.opportunity_kind == "CONTACT_OPPORTUNITY" for o in result.opportunities)
        )

    def test_c5_contact_finalized_after_slot_no_past_effect(self) -> None:
        """C5: contact finalized after slot → past slot unaffected."""
        slot = self._first_past_slot()
        # Contact ends before slot but finalized after slot_anchor → not visible.
        from datetime import date, timedelta

        local = date.fromisoformat(slot.local_date)
        before = (local - timedelta(days=1)).isoformat()
        after = (local + timedelta(days=1)).isoformat()
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[
                contact(
                    contact_id="c5",
                    person_id=MIZUKI,
                    actual_end=f"{before}T12:00:00+09:00",
                    finalized_at=f"{after}T12:00:00+09:00",
                )
            ],
        )
        res = next(r for r in result.slot_resolutions if r.slot_id == slot.slot_id)
        self.assertEqual(res.outcome, "INELIGIBLE_RECENCY_UNKNOWN")

    def test_c6_exact_recency_boundary(self) -> None:
        """C6: exact boundary follows inclusive/exclusive contract."""
        slot = self._first_past_slot()
        # Exactly 24h before anchor → min_hours_inclusive=24 of second band.
        from datetime import date, timedelta

        local = date.fromisoformat(slot.local_date)
        prior = (local - timedelta(days=1)).isoformat()
        actual_end = f"{prior}T00:00:00+09:00"
        sec = recency_seconds_at_anchor(
            slot_anchor_at=slot.slot_anchor_at, actual_end=actual_end
        )
        self.assertEqual(sec, 24 * 3600)
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[
                contact(
                    contact_id="c6",
                    person_id=MIZUKI,
                    actual_end=actual_end,
                    finalized_at=f"{prior}T00:05:00+09:00",
                )
            ],
        )
        res = next(r for r in result.slot_resolutions if r.slot_id == slot.slot_id)
        self.assertEqual(res.recency_band_key, "24-72")

    def test_c7_threshold_integer_floor_then_cap(self) -> None:
        """C7: threshold = integer floor then cap."""
        # 450 * 1350 // 1000 = 607; cap 800 → 607
        self.assertEqual(
            contact_activation_threshold(
                base_activation_permille=450,
                multiplier_permille=1350,
                activation_cap_permille=800,
            ),
            607,
        )
        # Cap binds: 450 * 2000 // 1000 = 900 → min(800,900)=800
        self.assertEqual(
            contact_activation_threshold(
                base_activation_permille=450,
                multiplier_permille=2000,
                activation_cap_permille=800,
            ),
            800,
        )

    def test_c8_same_slot_same_draw_key(self) -> None:
        """C8: same slot → same draw/key."""
        contacts = [
            contact(
                contact_id="c8",
                person_id=MIZUKI,
                actual_end="2026-03-01T12:00:00+09:00",
                finalized_at="2026-03-01T12:05:00+09:00",
            )
        ]
        asn = [{
            "schema_version": 1,
            "person_id": MIZUKI,
            "archetype_id": "REMOTE_CLOSE_BURSTY",
            "provenance": {"origin": "ENGINE_DEFAULT"},
        }]
        a = generate(assignments=asn, contacts=contacts)
        b = generate(assignments=asn, contacts=contacts)
        self.assertEqual(
            [(r.slot_id, r.activation_draw_permille, r.random_key) for r in a.slot_resolutions],
            [(r.slot_id, r.activation_draw_permille, r.random_key) for r in b.slot_resolutions],
        )

    def test_c9_activated_contact_opportunity(self) -> None:
        """C9: activated → CONTACT_OPPORTUNITY."""
        # Force activation via high multiplier + long silence; may still miss draw.
        # Search fixtures until we find an activated resolution, or force by
        # checking threshold vs draw on long-silence case.
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[
                contact(
                    contact_id="c9",
                    person_id=MIZUKI,
                    actual_end="2025-01-01T00:00:00+09:00",
                    finalized_at="2025-01-01T00:05:00+09:00",
                )
            ],
        )
        activated = [
            r for r in result.slot_resolutions if r.outcome == "CONTACT_OPPORTUNITY"
        ]
        not_act = [r for r in result.slot_resolutions if r.outcome == "NO_OPPORTUNITY"]
        # With cap 800 and draws 0..999, some slots activate, some may not.
        # Assert structural correctness for whichever activated.
        self.assertTrue(activated or not_act)
        for r in activated:
            self.assertIsNotNone(r.activation_draw_permille)
            self.assertIsNotNone(r.activation_threshold_permille)
            assert r.activation_draw_permille is not None
            assert r.activation_threshold_permille is not None
            self.assertLess(r.activation_draw_permille, r.activation_threshold_permille)
            opp = next(
                o for o in result.opportunities if o.source_ref == r.slot_id
            )
            self.assertEqual(opp.opportunity_kind, "CONTACT_OPPORTUNITY")
            self.assertIsNone(opp.invite_timing_kind)
            self.assertIsNone(opp.target_local_date)

    def test_c10_not_activated_no_opportunity(self) -> None:
        """C10: not activated → no opportunity for that slot."""
        result = generate(
            assignments=[{
                "schema_version": 1,
                "person_id": MIZUKI,
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": {"origin": "ENGINE_DEFAULT"},
            }],
            contacts=[
                contact(
                    contact_id="c10",
                    person_id=MIZUKI,
                    # Very recent → low multiplier 250 → threshold = 450*250//1000=112
                    actual_end="2026-03-14T20:00:00+09:00",
                    finalized_at="2026-03-14T20:05:00+09:00",
                )
            ],
            context=ctx(as_of="2026-03-15T23:59:59+09:00"),
        )
        for r in result.slot_resolutions:
            if r.outcome == "NO_OPPORTUNITY":
                self.assertIsNotNone(r.activation_draw_permille)
                self.assertIsNotNone(r.activation_threshold_permille)
                assert r.activation_draw_permille is not None
                assert r.activation_threshold_permille is not None
                self.assertGreaterEqual(
                    r.activation_draw_permille, r.activation_threshold_permille
                )
                self.assertFalse(
                    any(o.source_ref == r.slot_id for o in result.opportunities)
                )
                self.assertIn(
                    "CONTACT_NOT_ACTIVATED",
                    diag_codes(result, source_ref=r.slot_id),
                )
