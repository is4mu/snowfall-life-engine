"""Social availability-slot generation and isolation contracts."""

from __future__ import annotations

import copy
import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.social_opportunities import generate_social_opportunities

from tests.support.harnesses.social_opportunities import (
    KNOWN,
    MIZUKI,
    RENA,
    SAYAKA,
    WEEK_START,
    WORLD_SEED,
    assignment,
    ctx,
    default_assignments,
    generate,
    policy,
    slots_for,
)


pytestmark = pytest.mark.unit


class AvailabilitySlotTests(unittest.TestCase):
    def test_s1_remote_contact_only(self) -> None:
        """S1: REMOTE → policy CONTACT slots only; no INVITE."""
        pol = policy()
        n = pol["social_policy"]["archetypes"]["REMOTE_CLOSE_BURSTY"]["slots_per_week"]
        result = generate(assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")])
        contact = slots_for(result, MIZUKI, "CONTACT")
        invite = slots_for(result, MIZUKI, "INVITE")
        self.assertEqual(len(contact), n)
        self.assertEqual(invite, [])

    def test_s2_local_contact_and_invite(self) -> None:
        """S2: LOCAL → policy CONTACT + INVITE slots."""
        pol = policy()
        arch = pol["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]
        result = generate(assignments=[assignment(SAYAKA, "LOCAL_CLOSE_INVITER")])
        self.assertEqual(
            len(slots_for(result, SAYAKA, "CONTACT")),
            arch["slots_per_week"],
        )
        self.assertEqual(
            len(slots_for(result, SAYAKA, "INVITE")),
            arch["invite"]["slots_per_week"],
        )

    def test_s3_work_contextual_weekly_zero(self) -> None:
        """S3: WORK_CONTEXTUAL → weekly slots 0."""
        result = generate(assignments=[assignment(RENA, "WORK_CONTEXTUAL")])
        self.assertEqual(result.slots, ())

    def test_s4_same_seed_person_week_stable(self) -> None:
        """S4: same seed/person/week → same dates/IDs."""
        a = generate()
        b = generate()
        self.assertEqual(
            [(s.slot_id, s.local_date, s.slot_kind) for s in a.slots],
            [(s.slot_id, s.local_date, s.slot_kind) for s in b.slots],
        )

    def test_s5_reversed_assignments_same_result(self) -> None:
        """S5: reversed assignments → same result."""
        fwd = generate(assignments=default_assignments())
        rev = generate(assignments=list(reversed(default_assignments())))
        self.assertEqual(
            [s.slot_id for s in fwd.slots],
            [s.slot_id for s in rev.slots],
        )
        self.assertEqual(
            [r.outcome for r in fwd.slot_resolutions],
            [r.outcome for r in rev.slot_resolutions],
        )

    def test_s6_unrelated_person_isolation(self) -> None:
        """S6: unrelated person added → existing person slots/draws unchanged."""
        base = generate(assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")])
        other = "friend-unrelated-extra"
        with_extra = generate(
            known_person_ids=(*KNOWN, other),
            assignments=[
                assignment(MIZUKI, "REMOTE_CLOSE_BURSTY"),
                assignment(other, "REMOTE_CLOSE_BURSTY"),
            ],
        )
        base_mizuki = [(s.slot_id, s.local_date) for s in slots_for(base, MIZUKI, "CONTACT")]
        extra_mizuki = [
            (s.slot_id, s.local_date) for s in slots_for(with_extra, MIZUKI, "CONTACT")
        ]
        self.assertEqual(base_mizuki, extra_mizuki)
        base_draws = {
            r.slot_id: (r.activation_draw_permille, r.random_key)
            for r in base.slot_resolutions
            if r.person_id == MIZUKI
        }
        extra_draws = {
            r.slot_id: (r.activation_draw_permille, r.random_key)
            for r in with_extra.slot_resolutions
            if r.person_id == MIZUKI
        }
        self.assertEqual(base_draws, extra_draws)

    def test_s7_unique_local_dates_within_person_kind(self) -> None:
        """S7: person+kind local dates unique."""
        result = generate()
        seen: dict[tuple[str, str], set[str]] = {}
        for s in result.slots:
            key = (s.person_id, s.slot_kind)
            bucket = seen.setdefault(key, set())
            self.assertNotIn(s.local_date, bucket)
            bucket.add(s.local_date)

    def test_s8_slots_per_week_over_7_fail_closed(self) -> None:
        """S8: slots_per_week > 7 → fail closed."""
        pol = copy.deepcopy(policy())
        pol["social_policy"]["archetypes"]["REMOTE_CLOSE_BURSTY"]["slots_per_week"] = 8
        with self.assertRaises(LifeEngineError) as cm:
            generate_social_opportunities(
                context=ctx(),
                behavior_policy=pol,
                known_person_ids=[MIZUKI],
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
            )
        self.assertEqual(cm.exception.code, ErrorCode.INVALID_STATE)

    def test_s9_week_start_not_monday_fail_closed(self) -> None:
        """S9: week_start not Monday → fail closed."""
        with self.assertRaises(LifeEngineError) as cm:
            generate(context=ctx(week_start_date="2026-03-10"))  # Tuesday
        self.assertEqual(cm.exception.code, ErrorCode.INVALID_STATE)
        # Sanity: Monday baseline still works.
        self.assertEqual(WEEK_START, "2026-03-09")
        self.assertEqual(WORLD_SEED, "slice2g-world-seed")

    def test_s10_noncanonical_week_start_fail_closed(self) -> None:
        """week_start must be exact lexical YYYY-MM-DD (not 2026-3-9)."""
        with self.assertRaises(LifeEngineError) as cm:
            generate(context=ctx(week_start_date="2026-3-9"))
        self.assertEqual(cm.exception.code, ErrorCode.INVALID_STATE)

    def test_s11_as_of_before_week_start_fail_closed(self) -> None:
        """as_of before week_start T00:00:00+09:00 → fail closed."""
        with self.assertRaises(LifeEngineError) as cm:
            generate(
                context=ctx(
                    week_start_date="2026-03-16",
                    as_of="2026-03-15T23:59:59+09:00",
                )
            )
        self.assertEqual(cm.exception.code, ErrorCode.INVALID_STATE)

    def test_s12_character_id_intentionally_irrelevant(self) -> None:
        """character_id does not enter keyed RNG (documented; no namespace change)."""
        a = generate(context=ctx(character_id="character-a"))
        b = generate(context=ctx(character_id="character-b-different"))
        self.assertEqual(
            [(s.slot_id, s.local_date) for s in a.slots],
            [(s.slot_id, s.local_date) for s in b.slots],
        )
        self.assertEqual(
            [(r.slot_id, r.activation_draw_permille, r.random_key) for r in a.slot_resolutions],
            [(r.slot_id, r.activation_draw_permille, r.random_key) for r in b.slot_resolutions],
        )
