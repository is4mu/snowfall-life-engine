"""Action-candidate builders, feasibility gates, identity, and evidence contracts."""

from __future__ import annotations

import unittest

import pytest

from engine.life.decisions import (
    build_biological_candidates,
    build_continue_current_candidate,
    build_decision_evidence,
    build_leisure_candidates,
    candidate_id_for,
    collect_action_candidates,
    parse_resolved_opportunity,
    physical_feasibility_rejection,
)
from engine.life.errors import ErrorCode, LifeEngineError

from tests.support.harnesses.resolver import (
    NOW,
    active_ctx,
    hs,
    load_v2_policy,
    opportunity,
)


pytestmark = pytest.mark.unit


class CandidateIdentityTests(unittest.TestCase):
    def test_stable_candidate_id(self) -> None:
        a = candidate_id_for(
            character_id="c1", decision_key="d1", candidate_key="k1"
        )
        b = candidate_id_for(
            character_id="c1", decision_key="d1", candidate_key="k1"
        )
        c = candidate_id_for(
            character_id="c1", decision_key="d1", candidate_key="k2"
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("action-candidate:"))


class SourceLessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_r8_sourceless_study_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            parse_resolved_opportunity(
                {
                    "opportunity_key": "bad",
                    "opportunity_class": "DEADLINE_TASK",
                    "action_kind": "STUDY",
                    "source_kind": "TASK",
                    "source_ref": "",
                    "soft_candidate": False,
                    "time_feasible": True,
                    "location_feasible": True,
                    "physical_feasible": True,
                    "domain_guard_satisfied": True,
                    "local_preference_permille": None,
                    "rule_ids": [],
                }
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_r8_sourceless_work_household_errand_social(self) -> None:
        for action, opp_class, source_kind in (
            ("WORK", "DEADLINE_TASK", "TASK"),
            ("HOUSEHOLD", "ROUTINE_HABIT", "TASK"),
            ("ERRAND", "DEADLINE_TASK", "TASK"),
            ("SOCIAL_PROMISE", "SOCIAL_PROMISE", "SOCIAL"),
            ("SOCIAL_CONTACT", "SOCIAL_PROMISE", "SOCIAL"),
            ("KICKBOXING", "ROUTINE_HABIT", "ROUTINE"),
        ):
            with self.subTest(action=action):
                with self.assertRaises(LifeEngineError):
                    parse_resolved_opportunity(
                        {
                            "opportunity_key": f"bad-{action}",
                            "opportunity_class": opp_class,
                            "action_kind": action,
                            "source_kind": source_kind,
                            "source_ref": "",
                            "soft_candidate": False,
                            "time_feasible": True,
                            "location_feasible": True,
                            "physical_feasible": True,
                            "domain_guard_satisfied": True,
                            "local_preference_permille": None,
                            "rule_ids": [],
                        }
                    )


class BiologicalCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_r9_meal_needed_restorative_urgent_biological(self) -> None:
        meal_needed, _ = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(hunger=450),
            meal_feasible=True,
            rest_feasible=True,
        )
        kinds = {(c.action_kind, c.priority_tier) for c in meal_needed}
        self.assertIn(("MEAL", "RESTORATIVE"), kinds)
        self.assertNotIn(("MEAL", "URGENT_BIOLOGICAL"), kinds)

        urgent, _ = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(hunger=700),
            meal_feasible=True,
            rest_feasible=True,
        )
        kinds_u = {(c.action_kind, c.priority_tier) for c in urgent}
        self.assertIn(("MEAL", "URGENT_BIOLOGICAL"), kinds_u)
        self.assertNotIn(("MEAL", "RESTORATIVE"), kinds_u)

    def test_r10_recent_meal_guard(self) -> None:
        # STANDARD guard = 90min; 30min elapsed → suppress ordinary MEAL_NEEDED
        _, rejected = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(hunger=450),
            meal_feasible=True,
            rest_feasible=True,
            last_meal_at="2026-03-15T11:30:00+09:00",
            meal_extent="STANDARD",
            now=NOW,
        )
        self.assertTrue(any(r.reason == "RECENT_MEAL_GUARD" for r in rejected))

        # URGENT ignores guard
        urgent, rej_u = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(hunger=700),
            meal_feasible=True,
            rest_feasible=True,
            last_meal_at="2026-03-15T11:30:00+09:00",
            meal_extent="STANDARD",
            now=NOW,
        )
        self.assertTrue(any(c.priority_tier == "URGENT_BIOLOGICAL" for c in urgent))
        self.assertFalse(any(r.reason == "RECENT_MEAL_GUARD" for r in rej_u))

    def test_r11_fatigue_bands(self) -> None:
        high, _ = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(physical_fatigue=450),
            meal_feasible=False,
            rest_feasible=True,
        )
        self.assertIn(("REST", "RESTORATIVE"), {(c.action_kind, c.priority_tier) for c in high})

        vhigh, _ = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(physical_fatigue=700),
            meal_feasible=False,
            rest_feasible=True,
        )
        self.assertIn(
            ("REST", "URGENT_BIOLOGICAL"),
            {(c.action_kind, c.priority_tier) for c in vhigh},
        )

        skip, _ = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(physical_fatigue=700),
            meal_feasible=False,
            rest_feasible=False,
        )
        self.assertFalse(any(c.action_kind == "REST" for c in skip))

    def test_r12_sleep_opportunity_matrix(self) -> None:
        cases = [
            # (pressure, opportunity, expect_tier_or_None)
            (349, "FAVORABLE", None),  # LOW
            (350, "NORMAL", None),  # MODERATE + NORMAL = no
            (350, "FAVORABLE", "RESTORATIVE"),
            (700, "NORMAL", "RESTORATIVE"),
            (700, "FAVORABLE", "RESTORATIVE"),
            (700, "BLOCKED", None),
            (950, "NORMAL", "URGENT_BIOLOGICAL"),
            (950, "FAVORABLE", "URGENT_BIOLOGICAL"),
            (950, "BLOCKED", None),
        ]
        for pressure, opp, expect in cases:
            with self.subTest(pressure=pressure, opp=opp):
                cands, _ = build_biological_candidates(
                    character_id="c",
                    decision_key="d",
                    policy=self.policy,
                    human_state=hs(),
                    meal_feasible=False,
                    rest_feasible=False,
                    sleep_pressure=pressure,
                    sleep_opportunity=opp,
                )
                sleeps = [c for c in cands if c.action_kind == "SLEEP_MAIN"]
                if expect is None:
                    self.assertEqual(sleeps, [])
                else:
                    self.assertEqual(len(sleeps), 1)
                    self.assertEqual(sleeps[0].priority_tier, expect)


class ContinueAndLeisureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_r24_continue_keeps_activity_instance_id(self) -> None:
        active = active_ctx(activity_instance_id="act:keep-me")
        cand = build_continue_current_candidate(
            character_id="c", decision_key="d", active=active
        )
        assert cand is not None
        self.assertEqual(cand.action_kind, "CONTINUE_CURRENT")
        self.assertEqual(cand.activity_instance_id, "act:keep-me")
        self.assertEqual(cand.source_kind, "CURRENT_ACTIVITY")

    def test_r25_free_window_under_15_no_leisure(self) -> None:
        cands = build_leisure_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            free_window_key="fw1",
            free_window_min=14,
            feasible_leisure_categories=["MUSIC", "MOVIE", "LOCAL_WALK"],
        )
        self.assertEqual(cands, ())

    def test_r26_25min_short_passive_only(self) -> None:
        cands = build_leisure_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            free_window_key="fw1",
            free_window_min=25,
            feasible_leisure_categories=[
                "PASSIVE_HOME_MEDIA",
                "MUSIC",
                "MOVIE",
                "LOCAL_WALK",
                "UNSTRUCTURED_REST",
            ],
        )
        kinds = {c.action_kind for c in cands}
        self.assertEqual(kinds, {"PASSIVE_HOME_MEDIA", "MUSIC", "UNSTRUCTURED_REST"})
        self.assertNotIn("MOVIE", kinds)
        self.assertNotIn("LOCAL_WALK", kinds)

    def test_r27_45min_caller_feasible_ok(self) -> None:
        cands = build_leisure_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            free_window_key="fw1",
            free_window_min=45,
            feasible_leisure_categories=["MOVIE", "LOCAL_WALK"],
        )
        kinds = {c.action_kind for c in cands}
        self.assertEqual(kinds, {"MOVIE", "LOCAL_WALK"})


class GateAndEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_physical_gate_order_reasons(self) -> None:
        base = opportunity(
            key="opp1",
            opp_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="t1",
        )
        from engine.life.decisions import opportunity_to_candidate

        for field, reason in (
            ("time_feasible", "TIME_INFEASIBLE"),
            ("location_feasible", "LOCATION_INFEASIBLE"),
            ("physical_feasible", "PHYSICAL_INFEASIBLE"),
            ("domain_guard_satisfied", "DOMAIN_GUARD_BLOCKED"),
        ):
            kw = {
                "key": "opp1",
                "opp_class": "DEADLINE_TASK",
                "action_kind": "STUDY",
                "source_kind": "TASK",
                "source_ref": "t1",
                "time_ok": True,
                "loc_ok": True,
                "phys_ok": True,
                "domain_ok": True,
            }
            if field == "time_feasible":
                kw["time_ok"] = False
            elif field == "location_feasible":
                kw["loc_ok"] = False
            elif field == "physical_feasible":
                kw["phys_ok"] = False
            else:
                kw["domain_ok"] = False
            cand = opportunity_to_candidate(
                character_id="c", decision_key="d", opportunity=opportunity(**kw)
            )
            rej = physical_feasibility_rejection(cand)
            assert rej is not None
            self.assertEqual(rej.reason, reason)

        _ = base  # silence

    def test_duplicate_candidate_key_fail_closed(self) -> None:
        opp_a = opportunity(
            key="same",
            opp_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="t1",
        )
        opp_b = opportunity(
            key="same",
            opp_class="ROUTINE_HABIT",
            action_kind="HOUSEHOLD",
            source_kind="TASK",
            source_ref="t2",
        )
        with self.assertRaises(LifeEngineError) as ctx:
            collect_action_candidates(
                character_id="c",
                decision_key="d",
                policy=self.policy,
                opportunities=[opp_a, opp_b],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_decision_evidence_shape(self) -> None:
        ev = build_decision_evidence(
            decision_type="action_choice",
            policy_version="v2-candidate",
            rule_ids=["b", "a"],
            input_state_refs=["hs:hunger", "hs:fatigue"],
            random_key=None,
            selected_result="SELECT_CANDIDATE:MEAL",
        )
        self.assertEqual(ev["rule_ids"], ["a", "b"])
        self.assertEqual(
            set(ev.keys()),
            {
                "decision_type",
                "policy_version",
                "rule_ids",
                "input_state_refs",
                "random_key",
                "selected_result",
            },
        )

    def test_reject_float_and_bool_as_int(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_resolved_opportunity(
                {
                    "opportunity_key": "x",
                    "opportunity_class": "DEADLINE_TASK",
                    "action_kind": "STUDY",
                    "source_kind": "TASK",
                    "source_ref": "t",
                    "soft_candidate": False,
                    "time_feasible": True,
                    "location_feasible": True,
                    "physical_feasible": True,
                    "domain_guard_satisfied": True,
                    "local_preference_permille": 100.5,  # float
                    "rule_ids": [],
                }
            )
        with self.assertRaises(LifeEngineError):
            parse_resolved_opportunity(
                {
                    "opportunity_key": "x",
                    "opportunity_class": "DEADLINE_TASK",
                    "action_kind": "STUDY",
                    "source_kind": "TASK",
                    "source_ref": "t",
                    "soft_candidate": False,
                    "time_feasible": True,
                    "location_feasible": True,
                    "physical_feasible": True,
                    "domain_guard_satisfied": True,
                    "local_preference_permille": True,  # bool-as-int
                    "rule_ids": [],
                }
            )

    def test_incompatible_tier_source_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_resolved_opportunity(
                {
                    "opportunity_key": "x",
                    "opportunity_class": "HARD_COMMITMENT",
                    "action_kind": "HARD_COMMITMENT_ACTIVITY",
                    "source_kind": "LEISURE_WINDOW",
                    "source_ref": "bad",
                    "soft_candidate": False,
                    "time_feasible": True,
                    "location_feasible": True,
                    "physical_feasible": True,
                    "domain_guard_satisfied": True,
                    "local_preference_permille": None,
                    "rule_ids": [],
                }
            )


class FailClosedCandidateRegressionTests(unittest.TestCase):
    """Fail-closed candidate regressions."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_v2_policy()

    def test_blocker1_omitted_meal_feasible_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_biological_candidates(
                character_id="c",
                decision_key="d",
                policy=self.policy,
                human_state=hs(hunger=700),
                rest_feasible=True,
            )
        self.assertIn("meal_feasible", ctx.exception.detail)

    def test_blocker1_omitted_rest_feasible_fail_closed(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_biological_candidates(
                character_id="c",
                decision_key="d",
                policy=self.policy,
                human_state=hs(physical_fatigue=700),
                meal_feasible=True,
            )
        self.assertIn("rest_feasible", ctx.exception.detail)

    def test_blocker1_sleep_pressure_requires_opportunity(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_biological_candidates(
                character_id="c",
                decision_key="d",
                policy=self.policy,
                human_state=hs(),
                meal_feasible=False,
                rest_feasible=False,
                sleep_pressure=700,
            )
        self.assertIn("sleep_opportunity", ctx.exception.detail)

    def test_blocker1_false_feasibility_suppresses(self) -> None:
        cands, _ = build_biological_candidates(
            character_id="c",
            decision_key="d",
            policy=self.policy,
            human_state=hs(hunger=700, physical_fatigue=700),
            meal_feasible=False,
            rest_feasible=False,
        )
        self.assertEqual(cands, ())

    def test_blocker2_work_from_generic_task_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            parse_resolved_opportunity(
                {
                    "opportunity_key": "w",
                    "opportunity_class": "DEADLINE_TASK",
                    "action_kind": "WORK",
                    "source_kind": "TASK",
                    "source_ref": "task:work",
                    "soft_candidate": False,
                    "time_feasible": True,
                    "location_feasible": True,
                    "physical_feasible": True,
                    "domain_guard_satisfied": True,
                    "local_preference_permille": None,
                    "rule_ids": [],
                }
            )
        self.assertIn("incompatible action/tier/source", ctx.exception.detail)

    def test_blocker2_study_from_routine_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_resolved_opportunity(
                {
                    "opportunity_key": "s",
                    "opportunity_class": "ROUTINE_HABIT",
                    "action_kind": "STUDY",
                    "source_kind": "ROUTINE",
                    "source_ref": "routine:study",
                    "soft_candidate": False,
                    "time_feasible": True,
                    "location_feasible": True,
                    "physical_feasible": True,
                    "domain_guard_satisfied": True,
                    "local_preference_permille": None,
                    "rule_ids": [],
                }
            )

    def test_blocker2_social_contact_from_routine_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_resolved_opportunity(
                {
                    "opportunity_key": "sc",
                    "opportunity_class": "ROUTINE_HABIT",
                    "action_kind": "SOCIAL_CONTACT",
                    "source_kind": "ROUTINE",
                    "source_ref": "routine:social",
                    "soft_candidate": False,
                    "time_feasible": True,
                    "location_feasible": True,
                    "physical_feasible": True,
                    "domain_guard_satisfied": True,
                    "local_preference_permille": None,
                    "rule_ids": [],
                }
            )

    def test_blocker2_positive_action_source_pairs(self) -> None:
        cases = [
            ("STUDY", "DEADLINE_TASK", "TASK", "task:1"),
            ("WORK", "HARD_COMMITMENT", "COMMITMENT", "commit:work"),
            ("HOUSEHOLD", "ROUTINE_HABIT", "TASK", "task:hh"),
            ("SOCIAL_CONTACT", "SOCIAL_PROMISE", "SOCIAL", "social:1"),
            ("KICKBOXING", "ROUTINE_HABIT", "ROUTINE", "routine:kb"),
            ("ERRAND", "DEADLINE_TASK", "TASK", "task:errand"),
        ]
        for action, opp_class, source, ref in cases:
            with self.subTest(action=action):
                opp = parse_resolved_opportunity(
                    {
                        "opportunity_key": f"ok-{action}",
                        "opportunity_class": opp_class,
                        "action_kind": action,
                        "source_kind": source,
                        "source_ref": ref,
                        "soft_candidate": False,
                        "time_feasible": True,
                        "location_feasible": True,
                        "physical_feasible": True,
                        "domain_guard_satisfied": True,
                        "local_preference_permille": None,
                        "rule_ids": [],
                    }
                )
                self.assertEqual(opp.action_kind, action)

    def test_blocker3_leisure_cannot_spoof_hard_continue(self) -> None:
        from engine.life.decisions import parse_active_decision_context

        with self.assertRaises(LifeEngineError) as ctx:
            parse_active_decision_context(
                {
                    "activity_instance_id": "act:spoof",
                    "action_kind": "MUSIC",
                    "actual_start": "2026-03-15T11:50:00+09:00",
                    "interruptibility": "REASSESSABLE",
                    "current_priority_tier": "HARD_COMMITMENT",
                    "continuation_feasible": True,
                    "local_preference_permille": 500,
                    "source_kind": "LEISURE_WINDOW",
                    "source_ref": "fw:1",
                    "rule_ids": [],
                }
            )
        self.assertIn("incompatible", ctx.exception.detail)

    def test_blocker4_dataclass_pref_out_of_range_rejected(self) -> None:
        from engine.life.decisions import ResolvedOpportunity, parse_resolved_opportunity

        bad = ResolvedOpportunity(
            opportunity_key="x",
            opportunity_class="DEADLINE_TASK",
            action_kind="STUDY",
            source_kind="TASK",
            source_ref="t",
            soft_candidate=False,
            time_feasible=True,
            location_feasible=True,
            physical_feasible=True,
            domain_guard_satisfied=True,
            local_preference_permille=1001,
            rule_ids=(),
        )
        with self.assertRaises(LifeEngineError):
            parse_resolved_opportunity(bad)

    def test_blocker4_threshold_requires_metric_band(self) -> None:
        from engine.life.decisions import DecisionBoundaryFact, parse_decision_boundary_fact

        with self.assertRaises(LifeEngineError):
            parse_decision_boundary_fact(
                DecisionBoundaryFact(
                    trigger_kind="THRESHOLD",
                    decision_key="threshold:bad",
                    reason_code="BAD",
                    metric=None,
                    target_band=None,
                    source_ref=None,
                )
            )

    def test_blocker4_non_threshold_rejects_metric_fields(self) -> None:
        from engine.life.decisions import parse_decision_boundary_fact

        with self.assertRaises(LifeEngineError):
            parse_decision_boundary_fact(
                {
                    "trigger_kind": "OBLIGATION",
                    "decision_key": "ob:1",
                    "reason_code": "OBLIGATION_DUE",
                    "metric": "HUNGER",
                    "target_band": "URGENT",
                    "source_ref": None,
                }
            )

    def test_blocker4_soft_guard_reason_class_must_be_capacity(self) -> None:
        from engine.life.decisions import SoftDecisionGuard, parse_soft_decision_guard

        with self.assertRaises(LifeEngineError):
            parse_soft_decision_guard(
                SoftDecisionGuard(
                    candidate_key="k",
                    decided_at="2026-03-15T11:00:00+09:00",
                    prior_outcome="DECLINED",
                    reason_class="MOOD",
                    meaningful_input_changed=False,
                )
            )

    def test_blocker4_future_last_meal_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_biological_candidates(
                character_id="c",
                decision_key="d",
                policy=self.policy,
                human_state=hs(hunger=450),
                meal_feasible=True,
                rest_feasible=True,
                last_meal_at="2026-03-15T13:00:00+09:00",
                meal_extent="LIGHT",
                now=NOW,
            )
        self.assertIn("last_meal_at", ctx.exception.detail)

    def test_blocker4_future_actual_start_rejected(self) -> None:
        from engine.life.decisions import parse_active_decision_context

        with self.assertRaises(LifeEngineError) as ctx:
            parse_active_decision_context(
                {
                    "activity_instance_id": "act:1",
                    "action_kind": "STUDY",
                    "actual_start": "2026-03-15T13:00:00+09:00",
                    "interruptibility": "REASSESSABLE",
                    "current_priority_tier": "ROUTINE_HABIT",
                    "continuation_feasible": True,
                    "local_preference_permille": 500,
                    "source_kind": "TASK",
                    "source_ref": "task:1",
                    "rule_ids": [],
                },
                now=NOW,
            )
        self.assertIn("actual_start", ctx.exception.detail)

    def test_cleanup_no_numeric_fallback_constants(self) -> None:
        import engine.life.decisions as d

        self.assertFalse(hasattr(d, "_DEFAULT_REASSESSABLE_MIN_DWELL_MIN"))
        self.assertFalse(hasattr(d, "_DEFAULT_SOFT_RECONSIDER_GUARD_MIN"))
        self.assertFalse(hasattr(d, "_DEFAULT_NEAR_EQUAL_MARGIN_PERMILLE"))
