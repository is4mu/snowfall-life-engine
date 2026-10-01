"""Household routine aging, prioritization, and feasibility contracts."""

from __future__ import annotations

import copy
import unittest

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.routine_adapters import household_age_band

from tests.support.harnesses.routines import (
    HOME,
    KITCHEN,
    NOW,
    adapt,
    boundary_keys,
    ctx,
    diag_codes,
    household,
    opp_keys,
    policy,
)


def _hh_ctx(**kwargs):
    """Household-focused context: suppress kickboxing self-gen via daily cap."""
    kwargs.setdefault("kickboxing_self_generated_today", 1)
    return ctx(**kwargs)

pytestmark = pytest.mark.unit


class HouseholdAdapterTests(unittest.TestCase):
    def test_h1_fresh_no_candidate(self) -> None:
        """H1: FRESH OPEN → no candidate; HOUSEHOLD_FRESH; future aging boundaries."""
        t = household(
            household_task_id="h1",
            created_at="2026-03-15T10:00:00+09:00",  # 2h old → FRESH
        )
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertEqual(opp_keys(result), [])
        self.assertIn("HOUSEHOLD_FRESH", diag_codes(result, source_ref="h1"))
        keys = boundary_keys(result)
        self.assertTrue(any("aging_start" in k for k in keys))
        self.assertTrue(any("pressing_start" in k for k in keys))
        self.assertTrue(any("overdue_start" in k for k in keys))

    def test_h2_aging_emits_routine_habit(self) -> None:
        """H2: AGING OPEN → HOUSEHOLD / ROUTINE_HABIT / ROUTINE soft."""
        # aging_start=1440min → created 30h ago
        t = household(
            household_task_id="h2",
            created_at="2026-03-14T06:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertEqual(opp_keys(result), ["household:h2:work"])
        opp = result.opportunities[0]
        self.assertEqual(opp.action_kind, "HOUSEHOLD")
        self.assertEqual(opp.opportunity_class, "ROUTINE_HABIT")
        self.assertEqual(opp.source_kind, "ROUTINE")
        self.assertEqual(opp.source_ref, "h2")
        self.assertTrue(opp.soft_candidate)
        self.assertIsNone(opp.local_preference_permille)
        self.assertTrue(opp.domain_guard_satisfied)
        self.assertIn("slice2f.household.work.AGING", opp.rule_ids)

    def test_h3_pressing_beats_aging(self) -> None:
        """H3: PRESSING + AGING → only PRESSING; AGING suppressed."""
        aging = household(
            household_task_id="h3a",
            created_at="2026-03-14T06:00:00+09:00",  # AGING
        )
        pressing = household(
            household_task_id="h3p",
            created_at="2026-03-13T06:00:00+09:00",  # ~54h → PRESSING
        )
        result = adapt(context=_hh_ctx(), household_tasks=[aging, pressing])
        self.assertEqual(opp_keys(result), ["household:h3p:work"])
        self.assertIn(
            "HOUSEHOLD_LOWER_BAND_SUPPRESSED",
            diag_codes(result, source_ref="h3a"),
        )

    def test_h4_overdue_beats_pressing(self) -> None:
        """H4: OVERDUE + PRESSING → only OVERDUE."""
        pressing = household(
            household_task_id="h4p",
            created_at="2026-03-13T06:00:00+09:00",
        )
        overdue = household(
            household_task_id="h4o",
            created_at="2026-03-11T12:00:00+09:00",  # 4d → OVERDUE
        )
        result = adapt(context=_hh_ctx(), household_tasks=[pressing, overdue])
        self.assertEqual(opp_keys(result), ["household:h4o:work"])
        self.assertIn(
            "HOUSEHOLD_LOWER_BAND_SUPPRESSED",
            diag_codes(result, source_ref="h4p"),
        )

    def test_h5_overdue_exact_age_tiebreak_oldest(self) -> None:
        """H5: among OVERDUE, keep oldest exact age only."""
        older = household(
            household_task_id="h5-old",
            created_at="2026-03-10T12:00:00+09:00",
        )
        newer = household(
            household_task_id="h5-new",
            created_at="2026-03-11T12:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[newer, older])
        self.assertEqual(opp_keys(result), ["household:h5-old:work"])

    def test_h6_overdue_exact_age_tie_keeps_multiple(self) -> None:
        """H6: identical exact age OVERDUE ties keep multiple (canonical id order)."""
        a = household(
            household_task_id="h6-a",
            created_at="2026-03-10T12:00:00+09:00",
        )
        b = household(
            household_task_id="h6-b",
            created_at="2026-03-10T12:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[b, a])
        self.assertEqual(
            opp_keys(result),
            ["household:h6-a:work", "household:h6-b:work"],
        )

    def test_h7_aging_peers_all_kept(self) -> None:
        """H7: AGING peers all kept (no numeric preference)."""
        a = household(
            household_task_id="h7a",
            created_at="2026-03-14T08:00:00+09:00",
        )
        b = household(
            household_task_id="h7b",
            created_at="2026-03-14T06:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[b, a])
        self.assertEqual(
            opp_keys(result),
            ["household:h7a:work", "household:h7b:work"],
        )
        self.assertTrue(all(o.local_preference_permille is None for o in result.opportunities))

    def test_h8_done_terminal(self) -> None:
        """H8: DONE → no opportunity, no boundaries; HOUSEHOLD_TERMINAL."""
        t = household(
            household_task_id="h8",
            status="DONE",
            created_at="2026-03-10T12:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertEqual(opp_keys(result), [])
        self.assertEqual(boundary_keys(result), [])
        self.assertIn("HOUSEHOLD_TERMINAL", diag_codes(result, source_ref="h8"))

    def test_h9_cancelled_terminal(self) -> None:
        t = household(household_task_id="h9", status="CANCELLED")
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertEqual(opp_keys(result), [])
        self.assertEqual(boundary_keys(result), [])
        self.assertIn("HOUSEHOLD_TERMINAL", diag_codes(result, source_ref="h9"))

    def test_h10_future_created_fail_closed(self) -> None:
        """H10: created_at > now → fail closed."""
        t = household(
            household_task_id="h10",
            created_at="2026-03-15T13:00:00+09:00",
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)

    def test_h11_location_blocked_still_emits(self) -> None:
        """H11: wrong location → opportunity with location_feasible=false."""
        t = household(
            household_task_id="h11",
            created_at="2026-03-14T06:00:00+09:00",
            location_id=KITCHEN,
        )
        result = adapt(context=_hh_ctx(location=HOME), household_tasks=[t])
        opp = result.opportunities[0]
        self.assertFalse(opp.location_feasible)
        self.assertTrue(opp.time_feasible)
        self.assertIn("HOUSEHOLD_LOCATION_BLOCKED", diag_codes(result, source_ref="h11"))

    def test_h12_window_insufficient_still_emits(self) -> None:
        """H12: available_window < duration_min → time_feasible=false."""
        t = household(
            household_task_id="h12",
            created_at="2026-03-14T06:00:00+09:00",
        )
        # duration_min=15
        result = adapt(
            context=_hh_ctx(available_window_min=10),
            household_tasks=[t],
        )
        opp = result.opportunities[0]
        self.assertFalse(opp.time_feasible)
        self.assertIn(
            "HOUSEHOLD_WINDOW_INSUFFICIENT",
            diag_codes(result, source_ref="h12"),
        )

    def test_h13_never_deadline_tier(self) -> None:
        """H13: OVERDUE household stays ROUTINE_HABIT (not DEADLINE_TASK)."""
        t = household(
            household_task_id="h13",
            created_at="2026-03-01T12:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertEqual(result.opportunities[0].opportunity_class, "ROUTINE_HABIT")
        self.assertTrue(result.opportunities[0].soft_candidate)

    def test_h14_forbidden_fields_rejected(self) -> None:
        """H14: severity/messiness/etc forbidden → fail closed."""
        for field, value in (
            ("severity", 3),
            ("messiness", 10),
            ("cleanliness_score", 0),
            ("clutter_score", 5),
            ("metadata", {"x": 1}),
        ):
            bad = household(household_task_id=f"h14-{field}")
            bad[field] = value
            with self.assertRaises(LifeEngineError):
                adapt(context=_hh_ctx(), household_tasks=[bad])

    def test_h15_input_order_independent_and_bands_from_policy(self) -> None:
        """H15: order independence; age bands read policy thresholds."""
        pol = policy()
        hh = pol["domain_policy"]["household_age"]
        self.assertEqual(
            household_age_band(
                now=NOW,
                created_at="2026-03-15T11:00:00+09:00",
                household_age_policy=hh,
            ),
            "FRESH",
        )
        a = household(household_task_id="h15a", created_at="2026-03-14T06:00:00+09:00")
        b = household(household_task_id="h15b", created_at="2026-03-14T08:00:00+09:00")
        r1 = adapt(context=_hh_ctx(), household_tasks=[a, b])
        r2 = adapt(context=_hh_ctx(), household_tasks=[b, a])
        self.assertEqual(opp_keys(r1), opp_keys(r2))
        self.assertEqual(
            [d.as_dict() for d in r1.diagnostics],
            [d.as_dict() for d in r2.diagnostics],
        )
        # No mutation of input dicts.
        snap = copy.deepcopy(a)
        adapt(context=_hh_ctx(), household_tasks=[a])
        self.assertEqual(a, snap)


class HouseholdRegressionTests(unittest.TestCase):
    def test_r1_integer_age_edge_at_aging_start(self) -> None:
        """Exact aging_start_min boundary uses integer seconds (no float)."""
        from engine.life.routine_adapters import _age_seconds

        # aging_start_min=1440 → exactly 1440*60 seconds old is AGING (not FRESH).
        created = "2026-03-14T12:00:00+09:00"  # exactly 1 day before NOW
        age = _age_seconds(now=NOW, created_at=created)
        self.assertEqual(age, 1440 * 60)
        self.assertIsInstance(age, int)
        pol = policy()["domain_policy"]["household_age"]
        self.assertEqual(
            household_age_band(now=NOW, created_at=created, household_age_policy=pol),
            "AGING",
        )
        # One second before aging threshold remains FRESH.
        just_fresh = "2026-03-14T12:00:01+09:00"
        self.assertEqual(
            household_age_band(now=NOW, created_at=just_fresh, household_age_policy=pol),
            "FRESH",
        )
        t = household(household_task_id="r1-edge", created_at=created)
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertEqual(opp_keys(result), ["household:r1-edge:work"])
        self.assertIn("slice2f.household.work.AGING", result.opportunities[0].rule_ids)

    def test_r1_physical_lazy_household_only(self) -> None:
        """HOUSEHOLD emit requires HOUSEHOLD fact only — KICKBOXING optional."""
        t = household(
            household_task_id="r1-phys-hh",
            created_at="2026-03-14T06:00:00+09:00",
        )
        result = adapt(
            context=_hh_ctx(physical={"HOUSEHOLD": True}),
            household_tasks=[t],
        )
        self.assertEqual(opp_keys(result), ["household:r1-phys-hh:work"])

    def test_r1_physical_missing_on_emit_fail_closed(self) -> None:
        t = household(
            household_task_id="r1-phys-miss",
            created_at="2026-03-14T06:00:00+09:00",
        )
        with self.assertRaises(LifeEngineError):
            adapt(
                context=_hh_ctx(physical={}),
                household_tasks=[t],
            )

    def test_r1_fresh_only_needs_no_household_physical(self) -> None:
        """FRESH-only: no HOUSEHOLD opportunity → no HOUSEHOLD physical required."""
        t = household(
            household_task_id="r1-fresh",
            created_at="2026-03-15T11:00:00+09:00",
        )
        result = adapt(
            context=_hh_ctx(physical={}),
            household_tasks=[t],
        )
        self.assertEqual(opp_keys(result), [])
        self.assertIn("HOUSEHOLD_FRESH", diag_codes(result, source_ref="r1-fresh"))

    def test_r1_requires_external_false_fail_closed(self) -> None:
        import copy

        pol = copy.deepcopy(policy())
        pol["domain_policy"]["household_age"]["requires_external_task_source"] = False
        t = household(
            household_task_id="r1-ext",
            created_at="2026-03-14T06:00:00+09:00",
        )
        with self.assertRaises(LifeEngineError) as caught:
            adapt(context=_hh_ctx(), household_tasks=[t], behavior_policy=pol)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("requires_external_task_source", caught.exception.detail)

    def test_r1_future_done_fail_closed(self) -> None:
        t = household(
            household_task_id="r1-done-future",
            status="DONE",
            created_at="2026-03-15T18:00:00+09:00",
        )
        with self.assertRaises(LifeEngineError):
            adapt(context=_hh_ctx(), household_tasks=[t])

    def test_r1_future_cancelled_fail_closed(self) -> None:
        t = household(
            household_task_id="r1-cancel-future",
            status="CANCELLED",
            created_at="2026-03-16T00:00:00+09:00",
        )
        with self.assertRaises(LifeEngineError):
            adapt(context=_hh_ctx(), household_tasks=[t])

    def test_r1_pressing_rule_id_encodes_band(self) -> None:
        t = household(
            household_task_id="r1-press",
            created_at="2026-03-13T06:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertIn("slice2f.household.work.PRESSING", result.opportunities[0].rule_ids)

    def test_r1_overdue_rule_id_encodes_band(self) -> None:
        t = household(
            household_task_id="r1-od",
            created_at="2026-03-10T12:00:00+09:00",
        )
        result = adapt(context=_hh_ctx(), household_tasks=[t])
        self.assertIn("slice2f.household.work.OVERDUE", result.opportunities[0].rule_ids)
