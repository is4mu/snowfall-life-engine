"""Fail-closed and dependency-boundary tests for social opportunity generation."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.social_opportunities import generate_social_opportunities
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.harnesses.social_opportunities import (
    MIZUKI,
    SAYAKA,
    assignment,
    contact,
    ctx,
    default_assignments,
    generate,
    policy,
    post_work,
)

pytestmark = pytest.mark.unit


class SocialGenerationBoundaryTests(unittest.TestCase):
    def test_x1_duplicate_assignment_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError):
            generate(
                assignments=[
                    assignment(MIZUKI, "REMOTE_CLOSE_BURSTY"),
                    assignment(MIZUKI, "LOCAL_CLOSE_INVITER"),
                ]
            )

    def test_x2_duplicate_contact_id_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError):
            generate(
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
                contacts=[
                    contact(contact_id="same"),
                    contact(contact_id="same", actual_end="2026-03-02T12:00:00+09:00",
                            finalized_at="2026-03-02T12:05:00+09:00"),
                ],
            )

    def test_x3_unknown_assignment_person_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError):
            generate(
                known_person_ids=[MIZUKI],
                assignments=[assignment("nobody", "REMOTE_CLOSE_BURSTY")],
            )

    def test_x4_unknown_contact_person_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError):
            generate(
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
                contacts=[contact(person_id="nobody")],
            )

    def test_x5_forbidden_output_fields_absent(self) -> None:
        result = generate(
            assignments=default_assignments(),
            contacts=[
                contact(contact_id="x5m", person_id=MIZUKI),
                contact(
                    contact_id="x5s",
                    person_id=SAYAKA,
                    actual_end="2026-03-01T12:00:00+09:00",
                    finalized_at="2026-03-01T12:05:00+09:00",
                ),
            ],
            post_work_contexts=[post_work()],
        )
        forbidden = {
            "name",
            "message",
            "text",
            "topic",
            "friendship_score",
            "closeness",
            "trust",
        }
        for item in (
            list(result.slots)
            + list(result.slot_resolutions)
            + list(result.post_work_resolutions)
            + list(result.opportunities)
            + list(result.diagnostics)
        ):
            keys = set(item.as_dict().keys())
            self.assertFalse(keys & forbidden)

    def test_x6_binary_float_reject(self) -> None:
        """X6: binary float rejected — including world_seed itself (both paths)."""
        # Mapping path: float world_seed
        with self.assertRaises(LifeEngineError) as cm:
            generate_social_opportunities(
                context={
                    "character_id": "c",
                    "world_seed": 0.5,
                    "week_start_date": "2026-03-09",
                    "as_of": "2026-03-15T12:00:00+09:00",
                },
                behavior_policy=policy(),
                known_person_ids=[MIZUKI],
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
            )
        self.assertEqual(cm.exception.code, ErrorCode.INVALID_STATE)
        # Dataclass path: float world_seed
        with self.assertRaises(LifeEngineError):
            generate(
                context=ctx(world_seed=0.5),  # type: ignore[arg-type]
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
            )
        # Mapping path: bool rejected
        with self.assertRaises(LifeEngineError):
            generate_social_opportunities(
                context={
                    "character_id": "c",
                    "world_seed": True,
                    "week_start_date": "2026-03-09",
                    "as_of": "2026-03-15T12:00:00+09:00",
                },
                behavior_policy=policy(),
                known_person_ids=[MIZUKI],
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
            )
        # Dataclass path: list rejected
        with self.assertRaises(LifeEngineError):
            generate(
                context=ctx(world_seed=["x"]),  # type: ignore[arg-type]
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
            )
        # Contact float still rejected
        with self.assertRaises(LifeEngineError):
            generate_social_opportunities(
                context={
                    "character_id": "c",
                    "world_seed": "s",
                    "week_start_date": "2026-03-09",
                    "as_of": "2026-03-15T12:00:00+09:00",
                },
                behavior_policy=policy(),
                known_person_ids=[MIZUKI],
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
                contacts=[
                    {
                        "contact_id": "f",
                        "person_id": MIZUKI,
                        "actual_end": 1.5,
                        "finalized_at": "2026-03-01T12:05:00+09:00",
                    }
                ],
            )

    def test_x6b_seed_accepts_strict_types(self) -> None:
        """Strict SeedLike accepts non-empty str / bytes / non-bool int."""
        for seed in ("ok-seed", b"ok-bytes", 42):
            result = generate(
                context=ctx(world_seed=seed),
                assignments=[assignment(MIZUKI, "REMOTE_CLOSE_BURSTY")],
            )
            self.assertTrue(result.slots)

    def test_x7_no_global_prng(self) -> None:
        import engine.life.social_opportunities as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("random.random", src)
        self.assertNotIn("import random", src)
        self.assertNotIn("numpy", src)

    def test_x8_input_mutation_none(self) -> None:
        asn = default_assignments()
        contacts = [contact(contact_id="mut", person_id=MIZUKI)]
        pw = [post_work()]
        asn_before = copy.deepcopy(asn)
        contacts_before = copy.deepcopy(contacts)
        pw_before = copy.deepcopy(pw)
        generate(assignments=asn, contacts=contacts, post_work_contexts=pw)
        self.assertEqual(asn, asn_before)
        self.assertEqual(contacts, contacts_before)
        self.assertEqual(pw, pw_before)

    def test_x9_public_policy_fixture_not_mutated(self) -> None:
        before = POLICY_V2_CANDIDATE_PATH.read_bytes()
        generate(assignments=default_assignments())
        after = POLICY_V2_CANDIDATE_PATH.read_bytes()
        self.assertEqual(before, after)

    def test_x11_generator_does_not_depend_on_decision_resolver(self) -> None:
        import engine.life.social_opportunities as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from .decisions", source)
        self.assertNotIn("import decisions", source)
        self.assertNotIn("resolve_action_decision", source)
        self.assertNotIn("opportunity_to_candidate", source)

    def test_x12_generator_does_not_depend_on_runtime_clock_or_wakeups(self) -> None:
        import engine.life.social_opportunities as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        for banned in (
            "from .clock",
            "import clock",
            "from .wakeups",
            "import wakeups",
            "advance(",
            "CurrentState",
            "enqueue_event",
        ):
            self.assertNotIn(banned, source)
