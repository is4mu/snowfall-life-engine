"""Obligation-task adaptation, deadline promotion, and feasibility contract tests."""

from __future__ import annotations

import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError

from tests.support.harnesses.domain_adapters import DESK, HOME, adapt, ctx, diag_codes, obligation, opp_keys


pytestmark = pytest.mark.unit


class ObligationTaskAdapterTests(unittest.TestCase):
    def test_t1_academic_routine(self) -> None:
        """T1: ACADEMIC OPEN far from deadline → STUDY / ROUTINE_HABIT."""
        # effort 120, due 18:00 → latest_required = 16:00; now=12:00 → ROUTINE
        t = obligation(task_id="t1", domain="ACADEMIC", priority="NORMAL")
        result = adapt(context=ctx(location=DESK), obligation_tasks=[t])
        self.assertEqual(opp_keys(result), ["task:t1:work"])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "STUDY")
        self.assertEqual(opp.opportunity_class, "ROUTINE_HABIT")
        self.assertTrue(opp.soft_candidate)
        self.assertEqual(opp.source_kind, "TASK")

    def test_t2_errand_task(self) -> None:
        """T2: ERRAND domain → ERRAND action."""
        t = obligation(
            task_id="t2",
            domain="ERRAND",
            due_at="2026-03-16T18:00:00+09:00",
            effort_remaining_min=60,
            location_constraints=[HOME],
        )
        result = adapt(context=ctx(location=HOME), obligation_tasks=[t])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "ERRAND")
        self.assertEqual(opp.opportunity_class, "ROUTINE_HABIT")

    def test_t3_work_domain_unsupported(self) -> None:
        """T3: WORK task domain must not map to WORK activity."""
        t = obligation(task_id="t3", domain="WORK", location_constraints=[DESK])
        result = adapt(context=ctx(location=DESK), obligation_tasks=[t])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("UNSUPPORTED_TASK_DOMAIN", diag_codes(result, source_ref="t3"))

    def test_t4_personal_unsupported(self) -> None:
        t = obligation(task_id="t4", domain="PERSONAL", location_constraints=[])
        result = adapt(obligation_tasks=[t])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("UNSUPPORTED_TASK_DOMAIN", diag_codes(result, source_ref="t4"))

    def test_t5_admin_unsupported(self) -> None:
        t = obligation(task_id="t5", domain="ADMIN", location_constraints=[])
        result = adapt(obligation_tasks=[t])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("UNSUPPORTED_TASK_DOMAIN", diag_codes(result, source_ref="t5"))

    def test_t6_urgent_is_deadline(self) -> None:
        """T6: priority=URGENT → DEADLINE_TASK even far from deadline."""
        t = obligation(
            task_id="t6",
            priority="URGENT",
            due_at="2026-03-20T18:00:00+09:00",
            effort_remaining_min=60,
            location_constraints=[DESK],
        )
        result = adapt(context=ctx(location=DESK), obligation_tasks=[t])
        opp = result.opportunities[0]
        self.assertEqual(opp.opportunity_class, "DEADLINE_TASK")
        self.assertFalse(opp.soft_candidate)

    def test_t7_deadline_promotion_by_latest_required(self) -> None:
        """T7: now >= latest_required_work_start_at → DEADLINE_TASK."""
        # due 14:00, effort 120 → latest_required = 12:00; now=12:00 → DEADLINE
        t = obligation(
            task_id="t7",
            priority="NORMAL",
            due_at="2026-03-15T14:00:00+09:00",
            effort_remaining_min=120,
            location_constraints=[DESK],
        )
        result = adapt(context=ctx(location=DESK), obligation_tasks=[t])
        self.assertEqual(result.opportunities[0].opportunity_class, "DEADLINE_TASK")
        self.assertFalse(result.opportunities[0].soft_candidate)

    def test_t8_high_alone_not_deadline(self) -> None:
        """T8: HIGH alone ≠ DEADLINE_TASK."""
        t = obligation(
            task_id="t8",
            priority="HIGH",
            due_at="2026-03-20T18:00:00+09:00",
            effort_remaining_min=60,
            location_constraints=[DESK],
        )
        result = adapt(context=ctx(location=DESK), obligation_tasks=[t])
        self.assertEqual(result.opportunities[0].opportunity_class, "ROUTINE_HABIT")
        self.assertTrue(result.opportunities[0].soft_candidate)

    def test_t9_earliest_start_future(self) -> None:
        """T9: earliest_start > now → no candidate; future OBLIGATION boundary."""
        t = obligation(
            task_id="t9",
            earliest_start="2026-03-15T15:00:00+09:00",
            due_at="2026-03-15T20:00:00+09:00",
            location_constraints=[DESK],
        )
        result = adapt(context=ctx(location=DESK), obligation_tasks=[t])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("NOT_YET_ELIGIBLE", diag_codes(result, source_ref="t9"))
        keys = [b.decision_key for b in result.decision_boundaries]
        self.assertTrue(any("earliest_start" in k for k in keys))

    def test_t10_feasibility_flags_and_boundaries(self) -> None:
        """T10: insufficient window + wrong location → diagnostics; still emits flags."""
        t = obligation(
            task_id="t10",
            min_work_chunk_min=60,
            effort_remaining_min=90,
            location_constraints=[DESK],
            due_at="2026-03-15T18:00:00+09:00",
        )
        result = adapt(
            context=ctx(location=HOME, available_window_min=30),
            obligation_tasks=[t],
        )
        self.assertEqual(opp_keys(result), ["task:t10:work"])
        opp = result.opportunities[0]
        self.assertFalse(opp.time_feasible)
        self.assertFalse(opp.location_feasible)
        self.assertIn("INSUFFICIENT_WORK_WINDOW", diag_codes(result, source_ref="t10"))
        self.assertIn("LOCATION_CONSTRAINT_BLOCKED", diag_codes(result, source_ref="t10"))
        # Future obligation boundaries present (due + latest_required).
        self.assertGreaterEqual(len(result.decision_boundaries), 1)
        self.assertTrue(all(b.trigger_kind == "OBLIGATION" for b in result.decision_boundaries))

    def test_done_and_cancelled_no_candidate(self) -> None:
        done = obligation(
            task_id="td",
            status="DONE",
            effort_remaining_min=0,
            location_constraints=[],
            due_at="2026-03-20T18:00:00+09:00",
        )
        cancelled = obligation(
            task_id="tc",
            status="CANCELLED",
            effort_remaining_min=10,
            location_constraints=[],
            due_at="2026-03-20T18:00:00+09:00",
            earliest_start="2026-03-16T10:00:00+09:00",
        )
        r1 = adapt(obligation_tasks=[done])
        r2 = adapt(obligation_tasks=[cancelled])
        self.assertEqual(opp_keys(r1), [])
        self.assertEqual(opp_keys(r2), [])
        # Regression: terminal tasks emit no future OBLIGATION boundaries.
        self.assertEqual(r1.decision_boundaries, ())
        self.assertEqual(r2.decision_boundaries, ())

    def test_future_created_task_fail_closed(self) -> None:
        """Recommended: created_at > now must not silently become actionable."""
        t = obligation(
            task_id="tfuture",
            created_at="2026-03-15T15:00:00+09:00",
            due_at="2026-03-16T18:00:00+09:00",
            location_constraints=[],
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(context=ctx(now="2026-03-15T12:00:00+09:00"), obligation_tasks=[t])
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("created_at after now", caught.exception.detail)

    def test_empty_location_constraints_ok(self) -> None:
        t = obligation(task_id="tloc", location_constraints=[], due_at="2026-03-20T18:00:00+09:00")
        result = adapt(obligation_tasks=[t])
        self.assertTrue(result.opportunities[0].location_feasible)

    def test_duplicate_task_id_fail_closed(self) -> None:
        t = obligation(task_id="dup")
        with self.assertRaises(LifeEngineError) as caught:
            adapt(obligation_tasks=[t, dict(t)])
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
