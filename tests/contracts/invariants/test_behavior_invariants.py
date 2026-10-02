"""Fast cross-domain invariants for deterministic public behavior composition."""

from __future__ import annotations

import copy
import unittest

import pytest

from tests.support.harnesses.integrated_behavior import (
    WORLD_SEED,
    DecisionSnapshot,
    assert_no_priority_inversion,
    collect_eligible_candidates,
    deep_copy_policy,
    exact_commitment,
    fingerprint,
    household,
    human_state,
    load_candidate_policy,
    obligation,
    resolver_boundary,
    reverse_snapshot_inputs,
    route,
    run_snapshot,
    seed_invariant_fingerprint,
)

pytestmark = pytest.mark.contract


class BehaviorInvariantContractTests(unittest.TestCase):
    def test_repeat_reverse_seed_proofs(self) -> None:
        policy = load_candidate_policy()
        snap = DecisionSnapshot(
            snapshot_id="iso-1",
            now="2026-03-15T13:20:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            commitments=(exact_commitment(commitment_id="iso-c"),),
            route_profiles=(route(),),
            household_tasks=(household(household_task_id="iso-hh"),),
            obligation_tasks=(
                obligation(task_id="iso-t", location_constraints=["fixture-desk"]),
            ),
            resolver_boundary=resolver_boundary(decision_key="idle:iso-1"),
            projection_horizon_end="2026-03-16T13:20:00+09:00",
        )
        a = run_snapshot(snap, behavior_policy=policy)
        b = run_snapshot(snap, behavior_policy=policy)
        c = run_snapshot(reverse_snapshot_inputs(snap), behavior_policy=policy)
        self.assertEqual(fingerprint(a), fingerprint(b))
        self.assertEqual(fingerprint(a), fingerprint(c))

        snap2 = DecisionSnapshot(
            snapshot_id="iso-2",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(hunger=950),
            sleep_pressure=200,
            location_id="fixture-desk",
            obligation_tasks=(
                obligation(
                    task_id="iso-study",
                    priority="URGENT",
                    location_constraints=["fixture-desk"],
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
            world_seed=WORLD_SEED,
        )
        r1 = run_snapshot(snap2, behavior_policy=policy)
        r2 = run_snapshot(
            DecisionSnapshot(
                snapshot_id="iso-2b",
                now=snap2.now,
                human_state=snap2.human_state,
                sleep_pressure=snap2.sleep_pressure,
                location_id=snap2.location_id,
                obligation_tasks=snap2.obligation_tasks,
                resolver_boundary=snap2.resolver_boundary,
                meal_feasible=True,
                rest_feasible=True,
                world_seed="other-seed",
            ),
            behavior_policy=policy,
        )
        self.assertEqual(seed_invariant_fingerprint(r1), seed_invariant_fingerprint(r2))
        self.assertEqual(fingerprint(r1), fingerprint(r2))
        self.assertEqual(r1.resolution.selected_priority_tier, "URGENT_BIOLOGICAL")
        eligible = collect_eligible_candidates(
            snapshot=snap2,
            opportunities=r1.merged_opportunities,
            policy=policy,
        )
        assert_no_priority_inversion(r1.resolution, eligible)

    def test_policy_deep_copy_isolation(self) -> None:
        pol = deep_copy_policy()
        before = copy.deepcopy(pol)
        snap = DecisionSnapshot(
            snapshot_id="iso-pol",
            now="2026-03-15T12:00:00+09:00",
            human_state=human_state(),
            sleep_pressure=200,
            household_tasks=(household(household_task_id="iso-pol-hh"),),
            resolver_boundary=resolver_boundary(decision_key="idle:iso-pol"),
        )
        run_snapshot(snap, behavior_policy=pol)
        self.assertEqual(pol, before)

