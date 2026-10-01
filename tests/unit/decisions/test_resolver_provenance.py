"""Resolver aggregate rule IDs remain canonical, unique, and provenance-safe."""

from __future__ import annotations

import unittest

import pytest

from tests.support.harnesses.resolver import NOW, active_ctx, boundary, hs, load_v2_policy, resolve


pytestmark = pytest.mark.unit


class ResolverProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_overlapping_active_and_biological_rule_ids_deduplicate(self) -> None:
        active = active_ctx(
            action_kind="MEAL",
            actual_start="2026-03-15T11:57:00+09:00",
            current_priority_tier="URGENT_BIOLOGICAL",
            source_kind="BIOLOGICAL",
            source_ref="hunger:URGENT",
            pref=None,
            rule_ids=("slice2d.bio.meal.urgent", "slice5b1.activity_start"),
        )
        out = resolve(
            self.policy,
            active=active,
            human_state=hs(hunger=700),
            meal_feasible=True,
            boundary_fact=boundary(trigger_kind="THRESHOLD"),
            now=NOW,
        )
        self.assertEqual(tuple(sorted(set(out.rule_ids))), out.rule_ids)
        self.assertEqual(out.rule_ids.count("slice2d.bio.meal.urgent"), 1)

    def test_overlapping_structural_rule_ids_deduplicate(self) -> None:
        from tests.support.harnesses.resolver import opportunity

        shared_rule = "slice2e.task.work"
        opp_a = opportunity(
            key="task:a",
            opp_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="task:a",
            rule_ids=(shared_rule,),
        )
        opp_b = opportunity(
            key="task:b",
            opp_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="task:b",
            rule_ids=(shared_rule,),
        )
        out = resolve(self.policy, opportunities=[opp_a, opp_b])
        self.assertEqual(tuple(sorted(set(out.rule_ids))), out.rule_ids)
        self.assertEqual(out.rule_ids.count(shared_rule), 1)
