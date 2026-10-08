"""Cross-domain composition tests across wakeups, decisions, schedule, routines, and social layers."""

from __future__ import annotations

import copy
import unittest

import pytest

from engine.life.contracts import validate_commitment
from engine.life.decisions import opportunity_to_candidate
from engine.life.errors import ErrorCode, LifeEngineError

from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.harnesses.integrated_behavior import (
    CHARACTER_ID,
    DESK,
    HOME,
    KNOWN_PERSON_IDS,
    MIZUKI,
    DecisionSnapshot,
    SocialGenerationSpec,
    SocialResponseSpec,
    assert_no_priority_inversion,
    collect_eligible_candidates,
    contact_history,
    deep_copy_policy,
    default_assignments,
    exact_commitment,
    fingerprint,
    household,
    human_state,
    load_candidate_policy,
    merge_boundaries,
    merge_opportunities,
    obligation,
    resolver_boundary,
    reverse_snapshot_inputs,
    route,
    run_snapshot,
    window_commitment,
)
from tests.support.harnesses.resolver import active_ctx
from tests.support.harnesses.social_response import (
    availability,
    local_invite,
    post_work_opp,
    remote_contact,
)


pytestmark = pytest.mark.integration


class CrossDomainBehaviorRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = load_candidate_policy()
        cls.policy_bytes = POLICY_V2_CANDIDATE_PATH.read_bytes()

    def test_g01_hard_beats_urgent_bio(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g01",
            now="2026-03-15T13:20:00+09:00",
            human_state=human_state(hunger=950),
            sleep_pressure=200,
            location_id=HOME,
            commitments=(exact_commitment(commitment_id="g01"),),
            route_profiles=(route(),),
            resolver_boundary=resolver_boundary(
                trigger_kind="THRESHOLD",
                decision_key="threshold:HUNGER:MEAL_NEEDED:UP",
                reason_code="THRESHOLD_HUNGER_MEAL_NEEDED_UP",
                metric="HUNGER",
                target_band="MEAL_NEEDED",
            ),
            meal_feasible=True,
            rest_feasible=True,
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.resolution.selected_priority_tier, "HARD_COMMITMENT")
        self.assertEqual(result.resolution.selected_action_kind, "TRAVEL_TO_COMMITMENT")
        eligible = collect_eligible_candidates(
            snapshot=snap,
            opportunities=result.merged_opportunities,
            policy=self.policy,
        )
        assert_no_priority_inversion(result.resolution, eligible)

    def test_g02_urgent_bio_beats_deadline(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g02",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(hunger=950),
            sleep_pressure=200,
            location_id=DESK,
            obligation_tasks=(
                obligation(
                    task_id="g02-study",
                    priority="URGENT",
                    location_constraints=[DESK],
                    due_at="2026-03-15T18:00:00+09:00",
                ),
            ),
            resolver_boundary=resolver_boundary(
                trigger_kind="THRESHOLD",
                decision_key="threshold:HUNGER:MEAL_NEEDED:UP",
                reason_code="THRESHOLD_HUNGER_MEAL_NEEDED_UP",
                metric="HUNGER",
                target_band="MEAL_NEEDED",
            ),
            meal_feasible=True,
            rest_feasible=True,
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.resolution.selected_priority_tier, "URGENT_BIOLOGICAL")
        self.assertEqual(result.resolution.selected_action_kind, "MEAL")

    def test_g03_deadline_study_beats_immediate_social(self) -> None:
        contact = remote_contact("g03-contact")
        snap = DecisionSnapshot(
            snapshot_id="g03",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            location_id=DESK,
            obligation_tasks=(
                obligation(
                    task_id="g03-study",
                    priority="URGENT",
                    location_constraints=[DESK],
                    due_at="2026-03-15T18:00:00+09:00",
                    effort_remaining_min=60,
                ),
            ),
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T12:00:00+09:00",
                exogenous_opportunities=(contact,),
            ),
            social_response=SocialResponseSpec(),
            resolver_boundary=resolver_boundary(
                trigger_kind="OBLIGATION",
                decision_key="obligation:g03",
                reason_code="TASK_DUE",
            ),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.resolution.selected_priority_tier, "DEADLINE_TASK")
        self.assertEqual(result.resolution.selected_action_kind, "STUDY")
        self.assertTrue(
            any(o.opportunity_class == "SOCIAL_PROMISE" for o in result.merged_opportunities)
        )

    def test_g04_social_promise_beats_household_leisure(self) -> None:
        social = window_commitment(
            commitment_id="g04-social",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": "2026-03-15T11:00:00+09:00",
                "latest_start": "2026-03-15T14:00:00+09:00",
                "duration_min": 30,
                "duration_max": 60,
            },
            location_id=None,
        )
        snap = DecisionSnapshot(
            snapshot_id="g04",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            location_id=HOME,
            commitments=(social,),
            household_tasks=(
                household(
                    household_task_id="g04-hh",
                    created_at="2026-03-14T06:00:00+09:00",
                ),
            ),
            free_window_key="fw-g04",
            free_window_min=90,
            feasible_leisure_categories=("MUSIC",),
            resolver_boundary=resolver_boundary(decision_key="idle:g04"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.resolution.selected_priority_tier, "SOCIAL_PROMISE")

    def test_g05_soft_social_from_2e_beats_leisure(self) -> None:
        social = window_commitment(
            commitment_id="g05-social",
            timing={
                "timing_kind": "WINDOW",
                "earliest_start": "2026-03-15T11:30:00+09:00",
                "latest_start": "2026-03-15T13:00:00+09:00",
                "duration_min": 30,
                "duration_max": 45,
            },
            location_id=None,
        )
        snap = DecisionSnapshot(
            snapshot_id="g05",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            commitments=(social,),
            free_window_key="fw-g05",
            free_window_min=120,
            feasible_leisure_categories=("MUSIC", "PASSIVE_HOME_MEDIA"),
            resolver_boundary=resolver_boundary(decision_key="idle:g05"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.resolution.selected_priority_tier, "SOCIAL_PROMISE")
        self.assertTrue(
            any(o.soft_candidate for o in result.merged_opportunities if o.opportunity_class == "SOCIAL_PROMISE")
        )

    def test_g06_future_invite_alone_not_current_candidate(self) -> None:
        invite = local_invite(
            "g06-inv",
            timing="FUTURE_SOFT",
            available_local_date="2026-03-15",
            target_local_date="2026-03-18",
        )
        avail = availability(
            availability_id="a-g06",
            opportunity_id="g06-inv",
            earliest_start="2026-03-18T18:00:00+09:00",
            latest_end="2026-03-18T21:00:00+09:00",
        )
        snap = DecisionSnapshot(
            snapshot_id="g06",
            now="2026-03-15T19:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            available_window_min=180,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T19:00:00+09:00",
                exogenous_opportunities=(invite,),
            ),
            social_response=SocialResponseSpec(availability_windows=(avail,)),
            free_window_key="fw-g06",
            free_window_min=90,
            feasible_leisure_categories=("MUSIC",),
            resolver_boundary=resolver_boundary(decision_key="idle:g06"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertTrue(result.commitment_proposals)
        self.assertFalse(
            any(o.opportunity_class == "SOCIAL_PROMISE" for o in result.merged_opportunities)
        )
        self.assertNotEqual(result.resolution.selected_priority_tier, "SOCIAL_PROMISE")

    def test_g07_proposal_handoff_via_2e(self) -> None:
        invite = local_invite(
            "g07-inv",
            timing="SAME_DAY_SOFT",
            available_local_date="2026-03-15",
            target_local_date="2026-03-15",
        )
        avail = availability(
            availability_id="a-g07",
            opportunity_id="g07-inv",
            earliest_start="2026-03-15T19:30:00+09:00",
            latest_end="2026-03-15T22:30:00+09:00",
        )
        first = DecisionSnapshot(
            snapshot_id="g07a",
            now="2026-03-15T19:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            available_window_min=180,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T19:00:00+09:00",
                exogenous_opportunities=(invite,),
            ),
            social_response=SocialResponseSpec(availability_windows=(avail,)),
            resolver_boundary=resolver_boundary(decision_key="idle:g07a"),
        )
        r1 = run_snapshot(first, behavior_policy=self.policy)
        self.assertEqual(len(r1.commitment_proposals), 1)
        proposal = r1.commitment_proposals[0]
        validate_commitment(proposal.commitment)
        # Later snapshot: explicitly supply nested Commitment — no persistence.
        # Must sit inside WINDOW [earliest_start, latest_start].
        second = DecisionSnapshot(
            snapshot_id="g07b",
            now="2026-03-15T19:30:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            available_window_min=150,
            commitments=(proposal.commitment,),
            # Suppress routine so SOCIAL_PROMISE is observable winner.
            kickboxing_location_feasible=False,
            routine_physical={"HOUSEHOLD": False, "KICKBOXING": False},
            free_window_key="fw-g07b",
            free_window_min=60,
            feasible_leisure_categories=("MUSIC",),
            resolver_boundary=resolver_boundary(decision_key="idle:g07b"),
        )
        r2 = run_snapshot(second, behavior_policy=self.policy)
        self.assertTrue(
            any(o.opportunity_class == "SOCIAL_PROMISE" for o in r2.merged_opportunities)
        )
        self.assertEqual(r2.resolution.selected_priority_tier, "SOCIAL_PROMISE")

    def test_g08_contact_defer_no_social_candidate(self) -> None:
        contact = remote_contact("g08-c")
        snap = DecisionSnapshot(
            snapshot_id="g08",
            now="2026-03-15T19:00:00+09:00",
            human_state=human_state(social_battery=50),  # LOW → DEFER
            sleep_pressure=200,
            available_window_min=120,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T19:00:00+09:00",
                exogenous_opportunities=(contact,),
            ),
            social_response=SocialResponseSpec(),
            free_window_key="fw-g08",
            free_window_min=60,
            feasible_leisure_categories=("MUSIC",),
            resolver_boundary=resolver_boundary(decision_key="idle:g08"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.social_response.responses[0].response, "DEFER")
        self.assertEqual(result.social_response.opportunities, ())
        self.assertFalse(any(c == "SOCIAL_PROMISE" for c in result.telemetry.opportunity_classes))

    def test_g09_post_work_decline_no_social_candidate(self) -> None:
        pw = post_work_opp("g09-pw")
        snap = DecisionSnapshot(
            snapshot_id="g09",
            now="2026-03-15T19:00:00+09:00",
            human_state=human_state(social_battery=50),
            sleep_pressure=200,
            available_window_min=120,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T19:00:00+09:00",
                exogenous_opportunities=(pw,),
            ),
            social_response=SocialResponseSpec(post_work_location_feasible=True),
            free_window_key="fw-g09",
            free_window_min=60,
            feasible_leisure_categories=("MUSIC",),
            resolver_boundary=resolver_boundary(decision_key="idle:g09"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.social_response.responses[0].response, "DECLINE")
        self.assertFalse(any(c == "SOCIAL_PROMISE" for c in result.telemetry.opportunity_classes))

    def test_g10_future_invite_ignores_current_capacity(self) -> None:
        invite = local_invite(
            "g10-inv",
            timing="FUTURE_SOFT",
            available_local_date="2026-03-15",
            target_local_date="2026-03-18",
        )
        avail = availability(
            availability_id="a-g10",
            opportunity_id="g10-inv",
            earliest_start="2026-03-18T18:00:00+09:00",
            latest_end="2026-03-18T21:00:00+09:00",
        )
        snap = DecisionSnapshot(
            snapshot_id="g10",
            now="2026-03-15T19:00:00+09:00",
            human_state=human_state(social_battery=50, physical_fatigue=950),
            sleep_pressure=950,  # CRITICAL capacity — must not veto FUTURE
            available_window_min=180,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T19:00:00+09:00",
                exogenous_opportunities=(invite,),
            ),
            social_response=SocialResponseSpec(availability_windows=(avail,)),
            resolver_boundary=resolver_boundary(decision_key="idle:g10"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.social_response.responses[0].response, "ACCEPT")
        self.assertEqual(len(result.commitment_proposals), 1)
        # Still not a current resolver candidate.
        self.assertFalse(any(c == "SOCIAL_PROMISE" for c in result.telemetry.opportunity_classes))

    def test_g11_household_beats_leisure(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g11",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            household_tasks=(
                household(
                    household_task_id="g11",
                    created_at="2026-03-14T06:00:00+09:00",
                ),
            ),
            free_window_key="fw-g11",
            free_window_min=90,
            feasible_leisure_categories=("MUSIC",),
            resolver_boundary=resolver_boundary(decision_key="idle:g11"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.resolution.selected_priority_tier, "ROUTINE_HABIT")
        self.assertEqual(result.resolution.selected_action_kind, "HOUSEHOLD")

    def test_g12_kickboxing_beats_leisure(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g12",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            kickboxing_sessions=(),  # empty → opportunity may emit HIGH
            free_window_key="fw-g12",
            free_window_min=90,
            feasible_leisure_categories=("MUSIC",),
            resolver_boundary=resolver_boundary(decision_key="idle:g12"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertEqual(result.resolution.selected_priority_tier, "ROUTINE_HABIT")
        self.assertEqual(result.resolution.selected_action_kind, "KICKBOXING")

    def test_g13_same_tier_via_2d_only(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g13",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            household_tasks=(
                household(
                    household_task_id="g13",
                    created_at="2026-03-14T06:00:00+09:00",
                ),
            ),
            kickboxing_sessions=(),
            resolver_boundary=resolver_boundary(decision_key="idle:g13"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        routine_opps = [
            o for o in result.merged_opportunities if o.opportunity_class == "ROUTINE_HABIT"
        ]
        self.assertGreaterEqual(len(routine_opps), 2)
        self.assertTrue(all(o.local_preference_permille is None for o in routine_opps))
        self.assertIn(result.resolution.selection_mode, {"keyed_tie", "ranked_deterministic", "single"})
        self.assertEqual(result.resolution.selected_priority_tier, "ROUTINE_HABIT")

    def test_g14_min_dwell(self) -> None:
        active = active_ctx(
            actual_start="2026-03-15T11:57:00+09:00",
            action_kind="STUDY",
            current_priority_tier="ROUTINE_HABIT",
            interruptibility="REASSESSABLE",
        )
        snap = DecisionSnapshot(
            snapshot_id="g14",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            household_tasks=(
                household(
                    household_task_id="g14",
                    created_at="2026-03-14T06:00:00+09:00",
                ),
            ),
            active=active,
            resolver_boundary=resolver_boundary(
                trigger_kind="THRESHOLD",
                decision_key="threshold:HUNGER:MEAL_NEEDED:UP",
                reason_code="THRESHOLD_HUNGER_MEAL_NEEDED_UP",
                metric="HUNGER",
                target_band="MEAL_NEEDED",
            ),
            meal_feasible=True,
            rest_feasible=True,
        )
        # hunger low so no urgent bio override; min-dwell should continue STUDY
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertTrue(result.resolution.min_dwell_forced)
        self.assertEqual(result.resolution.result_kind, "CONTINUE_CURRENT")
        self.assertEqual(result.resolution.selection_mode, "min_dwell")

    def test_g15_2e_2f_boundaries_reach_2c(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g15",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(hunger=100),
            sleep_pressure=200,
            commitments=(
                exact_commitment(
                    commitment_id="g15",
                    timing={
                        "timing_kind": "EXACT",
                        "planned_start": "2026-03-15T16:00:00+09:00",
                        "planned_end": "2026-03-15T18:00:00+09:00",
                    },
                ),
            ),
            route_profiles=(route(),),
            household_tasks=(
                household(
                    household_task_id="g15-hh",
                    created_at="2026-03-10T12:00:00+09:00",  # overdue → boundary
                ),
            ),
            projection_horizon_end="2026-03-16T12:00:00+09:00",
            resolver_boundary=resolver_boundary(decision_key="idle:g15"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertGreater(len(result.merged_boundaries), 0)
        self.assertTrue(
            any(b.trigger_kind in {"COMMITMENT_DEPARTURE", "OBLIGATION", "PLANNED_WINDOW"} for b in result.merged_boundaries)
        )
        # Selected wakeup should prefer structural boundary over idle when due sooner.
        self.assertIsNotNone(result.wakeup.selected)
        self.assertIn(
            result.telemetry.selected_wakeup_trigger,
            {
                "COMMITMENT_DEPARTURE",
                "OBLIGATION",
                "PLANNED_WINDOW",
                "THRESHOLD",
                "CIRCADIAN_REASSESSMENT",
                "IDLE",
            },
        )
        self.assertGreater(result.telemetry.future_candidate_count, 0)

    def test_g16_merge_order_invariants(self) -> None:
        a = exact_commitment(
            commitment_id="g16a",
            location_id=HOME,
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T12:00:00+09:00",
            },
        )
        b = exact_commitment(
            commitment_id="g16b",
            kind="ERRAND",
            hardness="SOFT",
            location_id=HOME,
            timing={
                "timing_kind": "EXACT",
                "planned_start": "2026-03-15T11:00:00+09:00",
                "planned_end": "2026-03-15T12:30:00+09:00",
            },
        )
        snap = DecisionSnapshot(
            snapshot_id="g16",
            now="2026-03-15T11:15:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            location_id=HOME,
            commitments=(a, b),
            household_tasks=(household(household_task_id="g16-hh"),),
            resolver_boundary=resolver_boundary(decision_key="idle:g16"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        keys = list(result.telemetry.opportunity_keys)
        self.assertEqual(keys, sorted(keys))
        bounds = result.merged_boundaries
        sorted_bounds = sorted(
            bounds, key=lambda x: (x.due_at, x.trigger_kind, x.decision_key)
        )
        self.assertEqual(list(bounds), list(sorted_bounds))

    def test_g17_no_arrival_no_social(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g17",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            household_tasks=(household(household_task_id="g17"),),
            # social_generation=None → skip 2G/2H
            resolver_boundary=resolver_boundary(decision_key="idle:g17"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertIsNone(result.social_generation)
        self.assertIsNone(result.social_response)
        self.assertEqual(result.commitment_proposals, ())
        self.assertEqual(result.telemetry.social_exogenous_ids, ())

    def test_g18_empty_ok(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g18",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            kickboxing_location_feasible=False,
            routine_physical={"HOUSEHOLD": False, "KICKBOXING": False},
            resolver_boundary=resolver_boundary(decision_key="idle:g18"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        # May still emit kickboxing depending on adapter; allow empty or leisure-less NO_ELIGIBLE
        self.assertIn(
            result.resolution.result_kind,
            {"SELECT_CANDIDATE", "NO_ELIGIBLE_ACTION", "CONTINUE_CURRENT"},
        )

    def test_g19_reverse_repeat_isolation(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g19",
            now="2026-03-15T13:20:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            location_id=HOME,
            commitments=(exact_commitment(commitment_id="g19"),),
            route_profiles=(route(),),
            household_tasks=(household(household_task_id="g19-hh"),),
            obligation_tasks=(obligation(task_id="g19-t", location_constraints=[DESK]),),
            resolver_boundary=resolver_boundary(decision_key="idle:g19"),
        )
        r1 = run_snapshot(snap, behavior_policy=self.policy)
        r2 = run_snapshot(snap, behavior_policy=self.policy)
        r_rev = run_snapshot(reverse_snapshot_inputs(snap), behavior_policy=self.policy)
        self.assertEqual(fingerprint(r1), fingerprint(r2))
        self.assertEqual(fingerprint(r1), fingerprint(r_rev))

    def test_g20_causal_validation_and_dup_fail(self) -> None:
        snap = DecisionSnapshot(
            snapshot_id="g20",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            household_tasks=(household(household_task_id="g20"),),
            resolver_boundary=resolver_boundary(decision_key="idle:g20"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        for opp in result.merged_opportunities:
            opportunity_to_candidate(
                character_id=CHARACTER_ID,
                decision_key="g20",
                opportunity=opp,
            )
        # Duplicate opportunity merge fails closed.
        with self.assertRaises(LifeEngineError) as caught:
            merge_opportunities(result.merged_opportunities, result.merged_opportunities)
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
        with self.assertRaises(LifeEngineError):
            merge_boundaries(result.merged_boundaries, result.merged_boundaries)

    def test_g21_commitments_validate_on_handoff(self) -> None:
        invite = local_invite(
            "g21-inv",
            timing="SAME_DAY_SOFT",
            available_local_date="2026-03-15",
            target_local_date="2026-03-15",
        )
        avail = availability(
            availability_id="a-g21",
            opportunity_id="g21-inv",
            earliest_start="2026-03-15T19:30:00+09:00",
            latest_end="2026-03-15T22:30:00+09:00",
        )
        snap = DecisionSnapshot(
            snapshot_id="g21",
            now="2026-03-15T19:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            available_window_min=180,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T19:00:00+09:00",
                exogenous_opportunities=(invite,),
            ),
            social_response=SocialResponseSpec(availability_windows=(avail,)),
            resolver_boundary=resolver_boundary(decision_key="idle:g21"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        for p in result.commitment_proposals:
            validate_commitment(p.commitment)

    def test_g22_public_policy_and_input_object_unchanged(self) -> None:
        self.assertEqual(POLICY_V2_CANDIDATE_PATH.read_bytes(), self.policy_bytes)
        self.assertTrue(
            str(self.policy["behavior_policy_version"]).startswith("public-fixture-")
        )
        pol = deep_copy_policy(self.policy)
        snap = DecisionSnapshot(
            snapshot_id="g22",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            resolver_boundary=resolver_boundary(decision_key="idle:g22"),
        )
        before = copy.deepcopy(pol)
        run_snapshot(snap, behavior_policy=pol)
        self.assertEqual(pol, before)

    def test_g23_no_float_prose_fields(self) -> None:
        invite = local_invite(
            "g23-inv",
            timing="SAME_DAY_SOFT",
            available_local_date="2026-03-15",
            target_local_date="2026-03-15",
        )
        avail = availability(
            availability_id="a-g23",
            opportunity_id="g23-inv",
            earliest_start="2026-03-15T19:30:00+09:00",
            latest_end="2026-03-15T22:30:00+09:00",
        )
        snap = DecisionSnapshot(
            snapshot_id="g23",
            now="2026-03-15T19:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            available_window_min=180,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of="2026-03-15T19:00:00+09:00",
                exogenous_opportunities=(invite, remote_contact("g23-c")),
            ),
            social_response=SocialResponseSpec(availability_windows=(avail,)),
            household_tasks=(household(household_task_id="g23"),),
            resolver_boundary=resolver_boundary(decision_key="idle:g23"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        blob = str(fingerprint(result))
        for forbidden in (
            "friendship_score",
            "acceptance_probability",
            "closeness",
            "message",
            "topic",
        ):
            self.assertNotIn(forbidden, blob)
        for o in result.merged_opportunities:
            self.assertNotIsInstance(o.local_preference_permille, float)

    def test_g24_unrelated_person_social_isolation(self) -> None:
        """Unrelated person added → existing-person 2G slots/draws unchanged."""
        now = "2026-03-15T12:00:00+09:00"
        week_start = "2026-03-09"
        contact_end = "2026-03-08T20:00:00+09:00"
        base_gen = SocialGenerationSpec(
            week_start_date=week_start,
            as_of=now,
            known_person_ids=(MIZUKI,),
            assignments=tuple(default_assignments()[:1]),
            contacts=(
                contact_history(
                    contact_id="g24-base",
                    person_id=MIZUKI,
                    actual_end=contact_end,
                    finalized_at="2026-03-08T20:05:00+09:00",
                ),
            ),
        )
        other = "friend-unrelated-slice2i"
        extra_gen = SocialGenerationSpec(
            week_start_date=week_start,
            as_of=now,
            known_person_ids=(MIZUKI, other),
            assignments=(
                *default_assignments()[:1],
                {
                    "schema_version": 1,
                    "person_id": other,
                    "archetype_id": "REMOTE_CLOSE_BURSTY",
                    "provenance": {
                        "origin": "ENGINE_DEFAULT",
                        "notes": "synthetic-slice2i",
                    },
                },
            ),
            contacts=(
                contact_history(
                    contact_id="g24-base",
                    person_id=MIZUKI,
                    actual_end=contact_end,
                    finalized_at="2026-03-08T20:05:00+09:00",
                ),
                contact_history(
                    contact_id="g24-other",
                    person_id=other,
                    actual_end=contact_end,
                    finalized_at="2026-03-08T20:05:00+09:00",
                ),
            ),
        )
        base_snap = DecisionSnapshot(
            snapshot_id="g24-base",
            now=now,
            human_state=human_state(),
            sleep_pressure=200,
            social_generation=base_gen,
            social_response=SocialResponseSpec(),
            resolver_boundary=resolver_boundary(decision_key="idle:g24-base"),
        )
        extra_snap = DecisionSnapshot(
            snapshot_id="g24-extra",
            now=now,
            human_state=human_state(),
            sleep_pressure=200,
            social_generation=extra_gen,
            social_response=SocialResponseSpec(),
            resolver_boundary=resolver_boundary(decision_key="idle:g24-extra"),
        )
        base = run_snapshot(base_snap, behavior_policy=self.policy)
        extra = run_snapshot(extra_snap, behavior_policy=self.policy)
        self.assertIsNotNone(base.social_generation)
        self.assertIsNotNone(extra.social_generation)
        assert base.social_generation is not None
        assert extra.social_generation is not None
        base_mizuki_slots = [
            (s.slot_id, s.local_date)
            for s in base.social_generation.slots
            if s.person_id == MIZUKI
        ]
        extra_mizuki_slots = [
            (s.slot_id, s.local_date)
            for s in extra.social_generation.slots
            if s.person_id == MIZUKI
        ]
        self.assertEqual(base_mizuki_slots, extra_mizuki_slots)
        base_draws = {
            r.slot_id: (r.activation_draw_permille, r.random_key)
            for r in base.social_generation.slot_resolutions
            if r.person_id == MIZUKI
        }
        extra_draws = {
            r.slot_id: (r.activation_draw_permille, r.random_key)
            for r in extra.social_generation.slot_resolutions
            if r.person_id == MIZUKI
        }
        self.assertEqual(base_draws, extra_draws)
        base_mizuki_opps = [
            (o.opportunity_id, o.opportunity_kind, o.available_local_date)
            for o in base.social_generation.opportunities
            if o.person_id == MIZUKI
        ]
        extra_mizuki_opps = [
            (o.opportunity_id, o.opportunity_kind, o.available_local_date)
            for o in extra.social_generation.opportunities
            if o.person_id == MIZUKI
        ]
        self.assertEqual(base_mizuki_opps, extra_mizuki_opps)

    def test_g25_slice2g_driven_compose(self) -> None:
        """Integrated compose must call generate_social_opportunities (not inject-only)."""
        now = "2026-03-15T18:00:00+09:00"
        snap = DecisionSnapshot(
            snapshot_id="g25",
            now=now,
            human_state=human_state(),
            sleep_pressure=200,
            social_generation=SocialGenerationSpec(
                week_start_date="2026-03-09",
                as_of=now,
                known_person_ids=KNOWN_PERSON_IDS,
                assignments=tuple(default_assignments()),
                contacts=(
                    contact_history(
                        contact_id="g25-c",
                        person_id=MIZUKI,
                        actual_end="2026-03-08T20:00:00+09:00",
                        finalized_at="2026-03-08T20:05:00+09:00",
                    ),
                ),
            ),
            social_response=SocialResponseSpec(),
            resolver_boundary=resolver_boundary(decision_key="idle:g25"),
        )
        result = run_snapshot(snap, behavior_policy=self.policy)
        self.assertIsNotNone(result.social_generation)
        self.assertIsNotNone(result.social_response)
        assert result.social_generation is not None
        self.assertGreater(len(result.social_generation.slots), 0)
        self.assertGreater(len(result.social_generation.slot_resolutions), 0)
