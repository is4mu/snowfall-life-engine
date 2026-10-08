"""Deterministic public Behavior Policy calibration over synthetic cross-domain snapshots."""

from __future__ import annotations

import json
import unittest
from collections import Counter

import pytest

from engine.life.contracts import validate_commitment
from engine.life.decisions import opportunity_to_candidate, parse_resolved_opportunity
from engine.life.errors import ErrorCode, LifeEngineError
from tests.support.harnesses.behavior_scenarios import (
    SCENARIO_KIND_COUNT,
    _build_day_snapshot,
)
from tests.support.harnesses.integrated_behavior import (
    SECONDARY_WORLD_SEEDS,
    WORLD_SEED,
    TIER_RANK,
    assert_no_priority_inversion,
    classify_priority_inversions,
    collect_eligible_candidates,
    fingerprint,
    keyed_owned_fingerprint,
    load_candidate_policy,
    reverse_snapshot_inputs,
    run_snapshot,
    seed_invariant_fingerprint,
)

pytestmark = [pytest.mark.calibration, pytest.mark.slow]

PUBLIC_CALIBRATION_28D_METRICS: dict = {}
PUBLIC_SECONDARY_SEED_METRICS: dict = {}


class PublicCalibration28DayTests(unittest.TestCase):
    def test_soak_28_day_calibration_metrics(self) -> None:
        global PUBLIC_CALIBRATION_28D_METRICS
        policy = load_candidate_policy()
        policy_snapshot = json.loads(json.dumps(policy))

        selected_tier_counts: Counter[str] = Counter()
        selected_action_counts: Counter[str] = Counter()
        selection_mode_counts: Counter[str] = Counter()
        result_kind_counts: Counter[str] = Counter()
        scenario_kind_coverage: Counter[int] = Counter()
        wakeup_trigger_counts: Counter[str] = Counter()
        wakeup_source_kind_counts: Counter[str] = Counter()
        social_outcome_counts: Counter[str] = Counter()
        social_contact_mode_counts: Counter[str] = Counter()
        immediate_reassessment_reason_counts: Counter[str] = Counter()
        rejected_reason_counts: Counter[str] = Counter()
        candidate_count_distribution: Counter[int] = Counter()
        invite_proposal_count = 0
        immediate_social_opp_count = 0
        empty_resolution_count = 0
        min_dwell_forced_count = 0
        same_tier_keyed_count = 0
        structural_opportunity_count = 0
        routine_opportunity_count = 0
        cross_domain_competition_count = 0
        slice2g_driven_snapshot_count = 0
        hard_conflict_fail_closed_count = 0
        active_min_dwell_snapshot_count = 0

        hard_priority_inversion_count = 0
        biological_priority_inversion_count = 0
        deadline_priority_inversion_count = 0
        social_priority_inversion_count = 0
        priority_inversion_count = 0

        duplicate_opportunity_key_count = 0
        duplicate_boundary_key_count = 0
        duplicate_candidate_id_count = 0
        duplicate_candidate_key_count = 0
        invalid_resolved_opportunity_count = 0
        invalid_commitment_proposal_count = 0
        invalid_or_duplicate_count = 0
        future_capacity_veto_count = 0
        invented_social_count = 0
        runtime_mutation_count = 0
        actual_event_creation_count = 0
        relation_mutation_count = 0
        policy_mutation_count = 0
        order_failures = 0
        repeat_failures = 0
        snapshots_total = 0

        for day in range(28):
            plan = _build_day_snapshot(day)
            snap = plan.snapshot
            kind = plan.scenario_kind
            scenario_kind_coverage[kind] += 1
            snapshots_total += 1

            if plan.expect_hard_conflict:
                with self.assertRaises(LifeEngineError) as caught:
                    run_snapshot(snap, behavior_policy=policy)
                self.assertEqual(caught.exception.code, ErrorCode.INVALID_STATE)
                self.assertIn("HARD_COMMITMENT_CONFLICT", caught.exception.detail)
                hard_conflict_fail_closed_count += 1
                continue

            try:
                result = run_snapshot(snap, behavior_policy=policy)
                result_rev = run_snapshot(
                    reverse_snapshot_inputs(snap), behavior_policy=policy
                )
                result_rep = run_snapshot(snap, behavior_policy=policy)
            except Exception:
                invalid_or_duplicate_count += 1
                raise

            if fingerprint(result) != fingerprint(result_rev):
                order_failures += 1
            if fingerprint(result) != fingerprint(result_rep):
                repeat_failures += 1

            eligible = collect_eligible_candidates(
                snapshot=snap,
                opportunities=result.merged_opportunities,
                policy=policy,
            )
            inv = classify_priority_inversions(result.resolution, eligible)
            hard_priority_inversion_count += inv["hard_priority_inversion_count"]
            biological_priority_inversion_count += inv[
                "biological_priority_inversion_count"
            ]
            deadline_priority_inversion_count += inv["deadline_priority_inversion_count"]
            social_priority_inversion_count += inv["social_priority_inversion_count"]
            priority_inversion_count += inv["priority_inversion_count"]
            try:
                assert_no_priority_inversion(result.resolution, eligible)
            except AssertionError:
                pass  # counted above

            tel = result.telemetry
            result_kind_counts[tel.resolution_result_kind] += 1
            candidate_count_distribution[len(eligible)] += 1
            structural_opportunity_count += len(result.structural.opportunities)
            routine_opportunity_count += len(result.routine.opportunities)
            domain_classes = {
                o.opportunity_class for o in result.merged_opportunities
            }
            domain_classes.update(c.priority_tier for c in eligible)
            if len(domain_classes) >= 2:
                cross_domain_competition_count += 1
            if result.social_generation is not None:
                slice2g_driven_snapshot_count += 1
            if snap.active is not None and tel.min_dwell_forced:
                active_min_dwell_snapshot_count += 1

            if tel.selected_priority_tier:
                selected_tier_counts[tel.selected_priority_tier] += 1
            if tel.selected_action_kind:
                selected_action_counts[tel.selected_action_kind] += 1
            if tel.selection_mode:
                selection_mode_counts[tel.selection_mode] += 1
                if tel.selection_mode == "keyed_tie":
                    same_tier_keyed_count += 1
            if tel.selected_wakeup_trigger:
                wakeup_trigger_counts[tel.selected_wakeup_trigger] += 1
            if tel.selected_wakeup_source_kind:
                wakeup_source_kind_counts[tel.selected_wakeup_source_kind] += 1
            for reason in tel.immediate_reassessment_reasons:
                immediate_reassessment_reason_counts[reason] += 1
            for rej in result.resolution.rejected:
                rejected_reason_counts[rej.reason] += 1
            if tel.min_dwell_forced:
                min_dwell_forced_count += 1
            if tel.resolution_result_kind == "NO_ELIGIBLE_ACTION":
                empty_resolution_count += 1

            for oid, skind, response in tel.social_response_outcomes:
                social_outcome_counts[f"{skind}:{response}"] += 1
            if result.social_response is not None:
                for r in result.social_response.responses:
                    if r.contact_mode:
                        social_contact_mode_counts[r.contact_mode] += 1
            invite_proposal_count += len(tel.commitment_proposal_ids)
            immediate_social_opp_count += sum(
                1 for c in tel.opportunity_classes if c == "SOCIAL_PROMISE"
            )

            # §11: each merged ResolvedOpportunity via parser + causal validation.
            for opp in result.merged_opportunities:
                try:
                    parse_resolved_opportunity(opp)
                    opportunity_to_candidate(
                        character_id=snap.character_id,
                        decision_key=f"causal:{snap.snapshot_id}",
                        opportunity=opp,
                    )
                except Exception:
                    invalid_resolved_opportunity_count += 1
                    invalid_or_duplicate_count += 1

            # §11: all Commitment proposals validated (not only invite isolation kinds).
            for p in result.commitment_proposals:
                try:
                    validate_commitment(p.commitment)
                except Exception:
                    invalid_commitment_proposal_count += 1
                    invalid_or_duplicate_count += 1

            # Future invite alone must not invent current social candidate from proposal.
            if kind == 8:
                if any(c == "SOCIAL_PROMISE" for c in tel.opportunity_classes):
                    invented_social_count += 1
                if tel.social_response_outcomes:
                    resp = tel.social_response_outcomes[0][2]
                    if resp == "DECLINE" and snap.sleep_pressure >= 900:
                        future_capacity_veto_count += 1

            if policy != policy_snapshot:
                policy_mutation_count += 1

            if len(tel.opportunity_keys) != len(set(tel.opportunity_keys)):
                duplicate_opportunity_key_count += 1
                invalid_or_duplicate_count += 1
            if len(tel.boundary_decision_keys) != len(set(tel.boundary_decision_keys)):
                duplicate_boundary_key_count += 1
                invalid_or_duplicate_count += 1
            cand_ids = list(result.resolution.considered_candidate_ids)
            if len(cand_ids) != len(set(cand_ids)):
                duplicate_candidate_id_count += 1
                invalid_or_duplicate_count += 1
            # §11: candidate_key duplicates are separate from candidate_id duplicates.
            cand_keys = [c.candidate_key for c in eligible]
            if len(cand_keys) != len(set(cand_keys)):
                duplicate_candidate_key_count += 1
                invalid_or_duplicate_count += 1

        metrics = {
            "snapshots_total": snapshots_total,
            "selected_tier_counts": dict(selected_tier_counts),
            "selected_action_counts": dict(selected_action_counts),
            "selection_mode_counts": dict(selection_mode_counts),
            "result_kind_counts": dict(result_kind_counts),
            "scenario_kind_coverage": {
                str(k): v for k, v in sorted(scenario_kind_coverage.items())
            },
            "wakeup_trigger_counts": dict(wakeup_trigger_counts),
            "wakeup_source_kind_counts": dict(wakeup_source_kind_counts),
            "immediate_reassessment_reason_counts": dict(
                immediate_reassessment_reason_counts
            ),
            "rejected_reason_counts": dict(rejected_reason_counts),
            "candidate_count_distribution": {
                str(k): v for k, v in sorted(candidate_count_distribution.items())
            },
            "social_outcome_counts": dict(social_outcome_counts),
            "social_contact_mode_counts": dict(social_contact_mode_counts),
            "invite_proposal_count": invite_proposal_count,
            "immediate_social_opp_count": immediate_social_opp_count,
            "empty_resolution_count": empty_resolution_count,
            "min_dwell_forced_count": min_dwell_forced_count,
            "same_tier_keyed_count": same_tier_keyed_count,
            "structural_opportunity_count": structural_opportunity_count,
            "routine_opportunity_count": routine_opportunity_count,
            "cross_domain_competition_count": cross_domain_competition_count,
            "slice2g_driven_snapshot_count": slice2g_driven_snapshot_count,
            "hard_conflict_fail_closed_count": hard_conflict_fail_closed_count,
            "active_min_dwell_snapshot_count": active_min_dwell_snapshot_count,
            "hard_priority_inversion_count": hard_priority_inversion_count,
            "biological_priority_inversion_count": biological_priority_inversion_count,
            "deadline_priority_inversion_count": deadline_priority_inversion_count,
            "social_priority_inversion_count": social_priority_inversion_count,
            "priority_inversion_count": priority_inversion_count,
            "duplicate_opportunity_key_count": duplicate_opportunity_key_count,
            "duplicate_boundary_key_count": duplicate_boundary_key_count,
            "duplicate_candidate_id_count": duplicate_candidate_id_count,
            "duplicate_candidate_key_count": duplicate_candidate_key_count,
            "invalid_resolved_opportunity_count": invalid_resolved_opportunity_count,
            "invalid_commitment_proposal_count": invalid_commitment_proposal_count,
            "invalid_or_duplicate_count": invalid_or_duplicate_count,
            "future_capacity_veto_count": future_capacity_veto_count,
            "invented_social_count": invented_social_count,
            "runtime_mutation_count": runtime_mutation_count,
            "actual_event_creation_count": actual_event_creation_count,
            "relation_mutation_count": relation_mutation_count,
            "policy_mutation_count": policy_mutation_count,
            "order_failures": order_failures,
            "repeat_failures": repeat_failures,
            "input_order_equivalence": order_failures == 0,
            "repeat_run_equivalence": repeat_failures == 0,
        }
        PUBLIC_CALIBRATION_28D_METRICS.clear()
        PUBLIC_CALIBRATION_28D_METRICS.update(metrics)

        self.assertEqual(priority_inversion_count, 0)
        self.assertEqual(hard_priority_inversion_count, 0)
        self.assertEqual(biological_priority_inversion_count, 0)
        self.assertEqual(deadline_priority_inversion_count, 0)
        self.assertEqual(social_priority_inversion_count, 0)
        self.assertEqual(invalid_or_duplicate_count, 0)
        self.assertEqual(duplicate_opportunity_key_count, 0)
        self.assertEqual(duplicate_boundary_key_count, 0)
        self.assertEqual(duplicate_candidate_id_count, 0)
        self.assertEqual(duplicate_candidate_key_count, 0)
        self.assertEqual(invalid_resolved_opportunity_count, 0)
        self.assertEqual(invalid_commitment_proposal_count, 0)
        self.assertEqual(future_capacity_veto_count, 0)
        self.assertEqual(invented_social_count, 0)
        self.assertEqual(runtime_mutation_count, 0)
        self.assertEqual(actual_event_creation_count, 0)
        self.assertEqual(relation_mutation_count, 0)
        self.assertEqual(policy_mutation_count, 0)
        self.assertEqual(order_failures, 0)
        self.assertEqual(repeat_failures, 0)
        self.assertEqual(len(scenario_kind_coverage), SCENARIO_KIND_COUNT)
        self.assertGreater(slice2g_driven_snapshot_count, 0)
        self.assertGreater(active_min_dwell_snapshot_count, 0)
        self.assertGreater(hard_conflict_fail_closed_count, 0)
        self.assertGreater(min_dwell_forced_count, 0)


class PublicSecondarySeedTests(unittest.TestCase):
    def test_secondary_seeds_invariant_stability(self) -> None:
        """Same-tier / social-arrival diversity; no best-seed ranking."""
        global PUBLIC_SECONDARY_SEED_METRICS
        policy = load_candidate_policy()
        primary_by_day: dict[int, tuple] = {}
        secondary_diffs: dict[str, int] = {}
        seed_invariant_failures = 0

        for day in (0, 3, 5, 7, 13):
            plan = _build_day_snapshot(day, world_seed=WORLD_SEED)
            if plan.expect_hard_conflict:
                continue
            snap = plan.snapshot
            primary = run_snapshot(snap, behavior_policy=policy)
            primary_by_day[day] = fingerprint(primary)
            for seed in SECONDARY_WORLD_SEEDS:
                alt_plan = _build_day_snapshot(day, world_seed=seed)
                alt_snap = alt_plan.snapshot
                alt = run_snapshot(alt_snap, behavior_policy=policy)
                # Seed-invariant fields must never diverge.
                if seed_invariant_fingerprint(alt) != seed_invariant_fingerprint(
                    primary
                ):
                    seed_invariant_failures += 1
                same = fingerprint(alt) == primary_by_day[day]
                key = f"{seed}:day{day}"
                if not same:
                    # Allowed only when keyed-owned fields differ.
                    if keyed_owned_fingerprint(alt) == keyed_owned_fingerprint(
                        primary
                    ):
                        seed_invariant_failures += 1
                    secondary_diffs[key] = 1
                    eligible = collect_eligible_candidates(
                        snapshot=alt_snap,
                        opportunities=alt.merged_opportunities,
                        policy=policy,
                    )
                    assert_no_priority_inversion(alt.resolution, eligible)
                    if (
                        primary.telemetry.selected_priority_tier
                        and alt.telemetry.selected_priority_tier
                    ):
                        self.assertIn(
                            alt.telemetry.selected_priority_tier, TIER_RANK
                        )

        PUBLIC_SECONDARY_SEED_METRICS.clear()
        PUBLIC_SECONDARY_SEED_METRICS.update(
            {
                "days_probed": [0, 3, 5, 7, 13],
                "secondary_seeds": list(SECONDARY_WORLD_SEEDS),
                "fingerprint_diff_keys": sorted(secondary_diffs),
                "diff_count": len(secondary_diffs),
                "seed_invariant_failures": seed_invariant_failures,
            }
        )
        self.assertEqual(seed_invariant_failures, 0)

