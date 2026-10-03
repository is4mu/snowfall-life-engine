"""Social response runtime invariance, idempotence, and dependency-boundary tests."""

from __future__ import annotations

import unittest

import pytest
from pathlib import Path

from engine.life.errors import LifeEngineError
from engine.life.ids import stable_id

from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.harnesses.social_response import (
    adapt,
    availability,
    ctx,
    fingerprint,
    local_contact,
    local_invite,
    prior,
    remote_contact,
)


pytestmark = pytest.mark.integration


class SocialResponseRuntimeTests(unittest.TestCase):
    def test_r36_prior_accept_dedupes(self) -> None:
        opp = remote_contact("r36")
        accept_id = stable_id("social-response", "r36", "ACCEPT")
        result = adapt(
            opportunities=[opp],
            prior_responses=[
                prior(
                    response_id=accept_id,
                    opportunity_id="r36",
                    response="ACCEPT",
                    reason_code="CONTACT_READY",
                )
            ],
        )
        r = result.responses[0]
        self.assertEqual(r.reason_code, "PRIOR_TERMINAL_RESPONSE")
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(result.opportunities, ())
        self.assertEqual(result.commitment_proposals, ())

    def test_r37_prior_decline_dedupes(self) -> None:
        opp = remote_contact("r37")
        decline_id = stable_id("social-response", "r37", "DECLINE")
        result = adapt(
            opportunities=[opp],
            prior_responses=[
                prior(
                    response_id=decline_id,
                    opportunity_id="r37",
                    response="DECLINE",
                    reason_code="OPPORTUNITY_EXPIRED",
                )
            ],
        )
        r = result.responses[0]
        self.assertEqual(r.reason_code, "PRIOR_TERMINAL_RESPONSE")
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(result.opportunities, ())

    def test_r38_prior_defer_may_reconsider(self) -> None:
        opp = remote_contact("r38")
        defer_id = stable_id("social-response", "r38", "DEFER")
        result = adapt(
            opportunities=[opp],
            prior_responses=[
                prior(
                    response_id=defer_id,
                    opportunity_id="r38",
                    response="DEFER",
                    reason_code="LOW_SOCIAL_BATTERY",
                )
            ],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(r.reason_code, "CONTACT_READY")
        self.assertEqual(len(result.opportunities), 1)

    def test_r39_semantic_ids_stable(self) -> None:
        opp = remote_contact("r39")
        a = adapt(opportunities=[opp])
        b = adapt(opportunities=[opp])
        self.assertEqual(a.responses[0].response_id, b.responses[0].response_id)
        self.assertEqual(
            a.responses[0].response_id,
            stable_id("social-response", "r39", "ACCEPT"),
        )
        self.assertEqual(
            a.opportunities[0].opportunity_key,
            stable_id("social-resolved-opportunity", "r39"),
        )

    def test_r40_public_policy_unchanged(self) -> None:
        before = POLICY_V2_CANDIDATE_PATH.read_bytes()
        adapt(opportunities=[remote_contact()])
        after = POLICY_V2_CANDIDATE_PATH.read_bytes()
        self.assertEqual(before, after)
        import engine.life.social_response as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from .clock", source)
        self.assertNotIn("from .wakeups", source)

    def test_r41_decision_dependency_is_parse_only(self) -> None:
        import engine.life.social_response as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        self.assertIn("parse_resolved_opportunity", source)
        self.assertNotIn("resolve_decision", source)
        self.assertNotIn("CAUSAL", source)

    def test_r42_runtime_modules_untouched(self) -> None:
        import engine.life.clock as clock_mod
        import engine.life.decisions as decisions_mod
        import engine.life.wakeups as wakeups_mod
        paths = [Path(clock_mod.__file__), Path(wakeups_mod.__file__), Path(decisions_mod.__file__)]
        before = {path: path.read_bytes() for path in paths}
        adapt(
            opportunities=[
                remote_contact(),
                local_invite(
                    "r42i",
                    timing="SAME_DAY_SOFT",
                ),
            ],
            availability_windows=[
                availability(
                    availability_id="a42",
                    opportunity_id="r42i",
                    earliest_start="2026-03-15T19:30:00+09:00",
                    latest_end="2026-03-15T22:30:00+09:00",
                    location_id=None,
                    hard_conflict=False,
                )
            ],
        )
        for path, content in before.items():
            self.assertEqual(content, path.read_bytes(), str(path))

    def test_r43_no_message_prose(self) -> None:
        result = adapt(opportunities=[remote_contact(), local_contact()])
        blob = str(fingerprint(result))
        for banned in ("message", "こんにちは", "hello", "chat"):
            self.assertNotIn(banned, blob.lower() if banned.isascii() else blob)

    def test_r44_no_friendship_closeness_trust_fields(self) -> None:
        result = adapt(opportunities=[remote_contact()])
        blob = str(fingerprint(result)).lower()
        for banned in ("friendship", "closeness", "trust", "acceptance_probability"):
            self.assertNotIn(banned, blob)

    def test_r45_no_binary_float(self) -> None:
        from tests.support.harnesses.social_response import NOW, human_state

        with self.assertRaises(LifeEngineError):
            adapt(
                context={
                    "now": NOW,
                    "human_state": human_state(),
                    "sleep_pressure": 200,
                    "available_window_min": 12.5,
                    "hard_commitment_blocking_now": False,
                    "social_contact_physical_feasible": True,
                    "post_work_location_feasible": True,
                },
                opportunities=[remote_contact()],
            )
        # Successful path remains int-only
        result = adapt(opportunities=[remote_contact()])
        for r in result.responses:
            for v in (r.duration_min, r.duration_max):
                if v is not None:
                    self.assertIsInstance(v, int)
                    self.assertNotIsInstance(v, bool)
