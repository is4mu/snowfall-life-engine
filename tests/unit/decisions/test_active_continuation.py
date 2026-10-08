"""Active same-source resolution must continue rather than restart when feasible."""

from __future__ import annotations

import unittest

import pytest

from tests.support.harnesses.resolver import (
    active_ctx,
    boundary,
    load_v2_policy,
    opportunity,
    resolve,
)


pytestmark = pytest.mark.unit


class ActiveContinuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_same_source_same_action_same_tier_prefers_continue(self) -> None:
        active = active_ctx(
            action_kind="STUDY",
            current_priority_tier="DEADLINE_TASK",
            source_kind="TASK",
            source_ref="task:1",
            actual_start="2026-03-15T11:40:00+09:00",
            pref=None,
        )
        duplicate = opportunity(
            key="task:task:1:work",
            opp_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="task:1",
            pref=None,
        )
        out = resolve(
            self.policy,
            active=active,
            opportunities=[duplicate],
            boundary_fact=boundary(
                trigger_kind="ACTIVE_REASSESSMENT",
                decision_key="active:same-source",
                reason_code="ACTIVE_REASSESSMENT",
                metric=None,
                target_band=None,
            ),
        )
        self.assertEqual(out.result_kind, "CONTINUE_CURRENT")
        self.assertEqual(out.activity_instance_id, active.activity_instance_id)
        rejected = {
            (row.candidate_key, row.reason, row.rule_ids) for row in out.rejected
        }
        self.assertIn(
            (
                duplicate.opportunity_key,
                "ACTIVE_SOURCE_ALREADY_RUNNING",
                ("slice2d.active.same_source_running",),
            ),
            rejected,
        )

    def test_different_source_same_action_same_tier_remains_eligible(self) -> None:
        active = active_ctx(
            action_kind="STUDY",
            current_priority_tier="DEADLINE_TASK",
            source_kind="TASK",
            source_ref="task:1",
            actual_start="2026-03-15T11:40:00+09:00",
            pref=0,
        )
        other = opportunity(
            key="task:task:2:work",
            opp_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="task:2",
            pref=1000,
        )
        out = resolve(
            self.policy,
            active=active,
            opportunities=[other],
            boundary_fact=boundary(
                trigger_kind="ACTIVE_REASSESSMENT",
                decision_key="active:same-source",
                reason_code="ACTIVE_REASSESSMENT",
                metric=None,
                target_band=None,
            ),
        )
        self.assertEqual(out.result_kind, "SELECT_CANDIDATE")
        self.assertEqual(out.selected_candidate_key, other.opportunity_key)
        self.assertFalse(
            any(row.reason == "ACTIVE_SOURCE_ALREADY_RUNNING" for row in out.rejected)
        )

    def test_same_source_can_restart_when_continuation_is_not_feasible(self) -> None:
        active = active_ctx(
            action_kind="STUDY",
            current_priority_tier="DEADLINE_TASK",
            continuation_feasible=False,
            source_kind="TASK",
            source_ref="task:1",
            actual_start="2026-03-15T11:40:00+09:00",
            pref=None,
        )
        replacement = opportunity(
            key="task:task:1:work",
            opp_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="task:1",
            pref=None,
        )
        out = resolve(
            self.policy,
            active=active,
            opportunities=[replacement],
            boundary_fact=boundary(
                trigger_kind="ACTIVE_REASSESSMENT",
                decision_key="active:same-source",
                reason_code="ACTIVE_REASSESSMENT",
                metric=None,
                target_band=None,
            ),
        )
        self.assertEqual(out.result_kind, "SELECT_CANDIDATE")
        self.assertEqual(out.selected_candidate_key, replacement.opportunity_key)
        self.assertFalse(
            any(row.reason == "ACTIVE_SOURCE_ALREADY_RUNNING" for row in out.rejected)
        )
