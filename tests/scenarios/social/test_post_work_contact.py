"""Post-work social contact scenarios through the public social-opportunity generator."""

from __future__ import annotations

import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from tests.support.harnesses.social_opportunities import (
    KNOWN,
    MIZUKI,
    RENA,
    assignment,
    ctx,
    diag_codes,
    generate,
    post_work,
)

pytestmark = pytest.mark.scenario


class PostWorkContactScenarioTests(unittest.TestCase):
    def test_w1_participant_stable_activation_once(self) -> None:
        """W1: WORK_CONTEXTUAL person in participants → one stable activation."""
        result = generate(
            assignments=[assignment(RENA, "WORK_CONTEXTUAL")],
            post_work_contexts=[post_work(work_context_id="w1", participant_person_ids=[RENA])],
        )
        self.assertEqual(len(result.post_work_resolutions), 1)
        self.assertEqual(result.slots, ())

    def test_w2_same_context_person_same_key_outcome(self) -> None:
        """W2: same context/person → same key/outcome."""
        pw = [post_work(work_context_id="w2", participant_person_ids=[RENA])]
        asn = [assignment(RENA, "WORK_CONTEXTUAL")]
        a = generate(assignments=asn, post_work_contexts=pw)
        b = generate(assignments=asn, post_work_contexts=pw)
        self.assertEqual(
            [r.as_dict() for r in a.post_work_resolutions],
            [r.as_dict() for r in b.post_work_resolutions],
        )
        self.assertEqual(
            [o.opportunity_id for o in a.opportunities],
            [o.opportunity_id for o in b.opportunities],
        )

    def test_w3_participant_absent_no_opportunity(self) -> None:
        """W3: participant absent → no opportunity."""
        result = generate(
            assignments=[assignment(RENA, "WORK_CONTEXTUAL")],
            post_work_contexts=[
                post_work(
                    work_context_id="w3",
                    participant_person_ids=[MIZUKI],  # Rena not present
                )
            ],
            known_person_ids=KNOWN,
        )
        self.assertEqual(result.post_work_resolutions, ())
        self.assertFalse(
            any(o.opportunity_kind == "POST_WORK_OPPORTUNITY" for o in result.opportunities)
        )
        self.assertIn("WORK_CONTEXT_PERSON_NOT_PRESENT", diag_codes(result))

    def test_w4_unknown_participant_fail_closed(self) -> None:
        """W4: unknown participant → fail closed."""
        with self.assertRaises(LifeEngineError) as cm:
            generate(
                assignments=[assignment(RENA, "WORK_CONTEXTUAL")],
                post_work_contexts=[
                    post_work(
                        work_context_id="w4",
                        participant_person_ids=[RENA, "unknown-person"],
                    )
                ],
            )
        self.assertEqual(cm.exception.code, ErrorCode.INVALID_STATE)

    def test_w5_future_unfinalized_fail_closed(self) -> None:
        """W5: future/unfinalized context → fail closed."""
        with self.assertRaises(LifeEngineError):
            generate(
                assignments=[assignment(RENA, "WORK_CONTEXTUAL")],
                post_work_contexts=[
                    post_work(
                        work_context_id="w5",
                        actual_end="2026-03-20T18:00:00+09:00",
                        finalized_at="2026-03-20T18:05:00+09:00",
                    )
                ],
                context=ctx(as_of="2026-03-15T12:00:00+09:00"),
            )

    def test_w6_duplicate_work_context_id_fail_closed(self) -> None:
        """W6: duplicate work_context_id → fail closed."""
        with self.assertRaises(LifeEngineError):
            generate(
                assignments=[assignment(RENA, "WORK_CONTEXTUAL")],
                post_work_contexts=[
                    post_work(work_context_id="dup"),
                    post_work(work_context_id="dup"),
                ],
            )

    def test_w7_multiple_contexts_one_decision_each_no_weekly(self) -> None:
        """W7: multiple work contexts → one decision each; no weekly Rena slots."""
        result = generate(
            assignments=[assignment(RENA, "WORK_CONTEXTUAL")],
            post_work_contexts=[
                post_work(work_context_id="w7a", participant_person_ids=[RENA]),
                post_work(
                    work_context_id="w7b",
                    actual_end="2026-03-13T18:00:00+09:00",
                    finalized_at="2026-03-13T18:05:00+09:00",
                    participant_person_ids=[RENA],
                ),
            ],
        )
        self.assertEqual(len(result.post_work_resolutions), 2)
        self.assertEqual(result.slots, ())
        ids = {r.work_context_id for r in result.post_work_resolutions}
        self.assertEqual(ids, {"w7a", "w7b"})
