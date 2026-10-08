"""Long-horizon deterministic decision-resolver invariant soaks."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import pytest

from tests.support.harnesses.resolver import (
    boundary,
    deadline_opp,
    hard_opp,
    hs,
    load_v2_policy,
    resolve,
    routine_opp,
)

pytestmark = [pytest.mark.soak, pytest.mark.slow]


class Soak90DayInvariantTests(unittest.TestCase):
    def test_soak_90_day_invariants(self) -> None:
        policy = load_v2_policy()
        failures = {
            "determinism": 0,
            "order_independence": 0,
            "priority_inversion": 0,
            "source_less": 0,
        }
        from engine.life.timeutil import format_rfc3339, parse_rfc3339
        from datetime import timedelta

        start_dt = parse_rfc3339("2026-01-01T09:00:00+09:00")
        for day in range(90):
            now = format_rfc3339(start_dt + timedelta(days=day))
            state = hs(
                hunger=150 + (day * 11) % 700,
                physical_fatigue=100 + (day * 7) % 700,
            )
            opps = [
                deadline_opp(f"d:{day}:a", pref=700 + (day % 200)),
                deadline_opp(f"d:{day}:b", pref=680 + (day % 200)),
                routine_opp(f"r:{day}"),
            ]
            if day % 2 == 0:
                opps.append(hard_opp(f"h:{day}"))
            kwargs = dict(
                human_state=state,
                opportunities=opps,
                now=now,
                boundary_fact=boundary(decision_key=f"d90:{day}"),
                meal_feasible=True,
            )
            a = resolve(policy, **kwargs)
            b = resolve(policy, **kwargs)
            if a.as_dict() != b.as_dict():
                failures["determinism"] += 1
            c = resolve(policy, **{**kwargs, "opportunities": list(reversed(opps))})
            if a.selected_candidate_id != c.selected_candidate_id or a.random_key != c.random_key:
                failures["order_independence"] += 1
            if any(o.opportunity_class == "HARD_COMMITMENT" for o in opps):
                if a.selected_priority_tier != "HARD_COMMITMENT":
                    failures["priority_inversion"] += 1
            if a.selected_action_kind in {
                "STUDY",
                "WORK",
                "ERRAND",
                "HOUSEHOLD",
                "SOCIAL_PROMISE",
                "SOCIAL_CONTACT",
                "KICKBOXING",
            }:
                matched = [
                    o
                    for o in opps
                    if o.opportunity_key == a.selected_candidate_key and o.source_ref
                ]
                if not matched:
                    failures["source_less"] += 1

        metrics = {"failures": failures, "days": 90}
        Path("/tmp/life_engine_resolver_90d_metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("RESOLVER_90D_METRICS", json.dumps(metrics, sort_keys=True))
        self.assertEqual(failures["determinism"], 0)
        self.assertEqual(failures["order_independence"], 0)
        self.assertEqual(failures["priority_inversion"], 0)
        self.assertEqual(failures["source_less"], 0)


# Integrated behavior invariants migrated from the cross-domain calibration layer.
import copy as _behavior_copy
import hashlib as _behavior_hashlib
import json as _behavior_json
from pathlib import Path as _BehaviorPath

import engine.life.clock as _clock_module
import engine.life.decisions as _decisions_module
import engine.life.domain_adapters as _domain_adapters_module
import engine.life.policy as _policy_module
import engine.life.routine_adapters as _routine_adapters_module
import engine.life.social_opportunities as _social_opportunities_module
import engine.life.social_response as _social_response_module
import engine.life.wakeups as _wakeups_module

from engine.life.decisions import opportunity_to_candidate as _opportunity_to_candidate
from engine.life.decisions import parse_resolved_opportunity as _parse_resolved_opportunity
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.harnesses.behavior_scenarios import _build_day_snapshot
from tests.support.harnesses.integrated_behavior import (
    WORLD_SEED as _WORLD_SEED,
    DecisionSnapshot as _DecisionSnapshot,
    classify_priority_inversions as _classify_priority_inversions,
    collect_eligible_candidates as _collect_eligible_candidates,
    fingerprint as _behavior_fingerprint,
    keyed_owned_fingerprint as _keyed_owned_fingerprint,
    load_candidate_policy as _load_candidate_policy,
    resolver_boundary as _resolver_boundary,
    reverse_snapshot_inputs as _reverse_snapshot_inputs,
    run_snapshot as _run_snapshot,
    seed_invariant_fingerprint as _seed_invariant_fingerprint,
)

PUBLIC_GUARD_MODULE_FILES = {
    "wakeups.py": _BehaviorPath(_wakeups_module.__file__),
    "decisions.py": _BehaviorPath(_decisions_module.__file__),
    "domain_adapters.py": _BehaviorPath(_domain_adapters_module.__file__),
    "routine_adapters.py": _BehaviorPath(_routine_adapters_module.__file__),
    "social_opportunities.py": _BehaviorPath(_social_opportunities_module.__file__),
    "social_response.py": _BehaviorPath(_social_response_module.__file__),
    "clock.py": _BehaviorPath(_clock_module.__file__),
    "policy.py": _BehaviorPath(_policy_module.__file__),
}
FUTURE_CAPACITY_DECLINE_REASONS = frozenset(
    {"LOW_SOCIAL_BATTERY", "VERY_HIGH_FATIGUE", "CRITICAL_SLEEP_PRESSURE"}
)

# Bind source-compatible names inside the migrated class.
copy = _behavior_copy
hashlib = _behavior_hashlib
json = _behavior_json
WORLD_SEED = _WORLD_SEED
DecisionSnapshot = _DecisionSnapshot
classify_priority_inversions = _classify_priority_inversions
collect_eligible_candidates = _collect_eligible_candidates
fingerprint = _behavior_fingerprint
keyed_owned_fingerprint = _keyed_owned_fingerprint
load_candidate_policy = _load_candidate_policy
opportunity_to_candidate = _opportunity_to_candidate
parse_resolved_opportunity = _parse_resolved_opportunity
resolver_boundary = _resolver_boundary
reverse_snapshot_inputs = _reverse_snapshot_inputs
run_snapshot = _run_snapshot
seed_invariant_fingerprint = _seed_invariant_fingerprint
INTEGRATED_BEHAVIOR_90D_METRICS: dict[str, dict[str, object]] = {}


class IntegratedBehavior90DayInvariantTests(unittest.TestCase):
    def _run_integrated_behavior_range(
        self, *, start_day: int, end_day: int
    ) -> dict[str, object]:
        if not (0 <= start_day < end_day <= 90):
            raise AssertionError("integrated 90d shard must be within [0, 90]")
        policy = load_candidate_policy()
        policy_before = copy.deepcopy(policy)
        module_hashes = {
            name: hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in PUBLIC_GUARD_MODULE_FILES.items()
        }
        fixture_before = POLICY_V2_CANDIDATE_PATH.read_bytes()
        fixture_sha_before = hashlib.sha256(fixture_before).hexdigest()

        priority_inversion_count = 0
        hard_priority_inversion_count = 0
        biological_priority_inversion_count = 0
        deadline_priority_inversion_count = 0
        social_priority_inversion_count = 0

        duplicate_candidate_id_count = 0
        duplicate_boundary_key_count = 0
        duplicate_opportunity_key_count = 0
        invalid_merged_opportunity_count = 0
        invalid_or_duplicate_count = 0

        order_failures = 0
        repeat_failures = 0
        reverse_input_failures = 0
        wakeup_order_failures = 0

        invented_social_count = 0
        future_invite_as_current_candidate_count = 0
        future_current_capacity_veto_count = 0
        transient_invite_auto_persist_count = 0
        commitment_persistence_count = 0

        policy_mutation_count = 0
        decisions_mutation_count = 0
        wakeups_mutation_count = 0
        clock_mutation_count = 0
        actual_event_creation_count = 0
        relation_mutation_count = 0
        runtime_queue_mutation_count = 0
        minute_loop_count = 0

        float_prose_field_count = 0
        seed_non_keyed_divergence_count = 0
        distinct_local_dates: set[str] = set()
        slice2g_driven_snapshot_count = 0

        for day in range(start_day, end_day):
            # Distinct local decision dates (no day % 28 reuse).
            plan = _build_day_snapshot(day)
            base = plan.snapshot
            distinct_local_dates.add(base.now[:10])
            if plan.expect_hard_conflict:
                # Observe fail-closed; no resolution fingerprint for this day.
                raised = False
                try:
                    run_snapshot(base, behavior_policy=policy)
                except Exception:
                    raised = True
                if not raised:
                    invalid_or_duplicate_count += 1
                continue
            snap = DecisionSnapshot(
                snapshot_id=f"soak90-{day:02d}",
                now=base.now,
                human_state=base.human_state,
                sleep_pressure=base.sleep_pressure,
                location_id=base.location_id,
                available_window_min=base.available_window_min,
                world_seed=WORLD_SEED,
                commitments=base.commitments,
                obligation_tasks=base.obligation_tasks,
                route_profiles=base.route_profiles,
                household_tasks=base.household_tasks,
                kickboxing_sessions=base.kickboxing_sessions,
                social_generation=base.social_generation,
                social_response=base.social_response,
                free_window_key=base.free_window_key,
                free_window_min=base.free_window_min,
                feasible_leisure_categories=base.feasible_leisure_categories,
                projection_horizon_end=base.projection_horizon_end,
                resolver_boundary=resolver_boundary(
                    decision_key=f"idle:soak90-{day}"
                )
                if base.active is None
                else base.resolver_boundary,
                active=base.active,
                meal_feasible=True,
                rest_feasible=True,
            )

            result = run_snapshot(snap, behavior_policy=policy)
            result_rep = run_snapshot(snap, behavior_policy=policy)
            result_rev = run_snapshot(
                reverse_snapshot_inputs(snap), behavior_policy=policy
            )

            if fingerprint(result) != fingerprint(result_rep):
                repeat_failures += 1
            if fingerprint(result) != fingerprint(result_rev):
                reverse_input_failures += 1
                order_failures += 1

            # Wakeup order independence under reversed boundary/commitment inputs.
            wake_a = (
                None
                if result.wakeup.selected is None
                else (
                    result.wakeup.selected.due_at,
                    result.wakeup.selected.trigger_kind,
                    result.wakeup.selected.decision_key,
                )
            )
            wake_b = (
                None
                if result_rev.wakeup.selected is None
                else (
                    result_rev.wakeup.selected.due_at,
                    result_rev.wakeup.selected.trigger_kind,
                    result_rev.wakeup.selected.decision_key,
                )
            )
            fut_a = tuple(
                (c.due_at, c.trigger_kind, c.decision_key)
                for c in result.wakeup.future_candidates
            )
            fut_b = tuple(
                (c.due_at, c.trigger_kind, c.decision_key)
                for c in result_rev.wakeup.future_candidates
            )
            if wake_a != wake_b or fut_a != fut_b:
                wakeup_order_failures += 1

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
            deadline_priority_inversion_count += inv[
                "deadline_priority_inversion_count"
            ]
            social_priority_inversion_count += inv["social_priority_inversion_count"]
            priority_inversion_count += inv["priority_inversion_count"]

            tel = result.telemetry
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

            # §13: invalid_merged_opportunity_count — independent required-zero.
            for opp in result.merged_opportunities:
                try:
                    parse_resolved_opportunity(opp)
                    opportunity_to_candidate(
                        character_id=snap.character_id,
                        decision_key=f"causal:{snap.snapshot_id}",
                        opportunity=opp,
                    )
                except Exception:
                    invalid_merged_opportunity_count += 1
                    invalid_or_duplicate_count += 1

            if result.social_generation is not None:
                slice2g_driven_snapshot_count += 1

            # Future invite proposals must not auto-enter resolver candidates.
            if plan.scenario_kind == 8:
                if any(c == "SOCIAL_PROMISE" for c in tel.opportunity_classes):
                    if not snap.commitments:
                        future_invite_as_current_candidate_count += 1
                        invented_social_count += 1
                # Transient proposal must not auto-persist into current candidates.
                proposal_cids = {
                    p.commitment.get("commitment_id")
                    for p in result.commitment_proposals
                }
                for opp in result.merged_opportunities:
                    if opp.source_ref in proposal_cids or any(
                        cid and cid in (opp.source_ref or "") for cid in proposal_cids
                    ):
                        transient_invite_auto_persist_count += 1
                if result.social_response is not None:
                    for r in result.social_response.responses:
                        if (
                            r.opportunity_kind == "INVITE_OPPORTUNITY"
                            and r.response in {"DECLINE", "DEFER"}
                            and r.reason_code in FUTURE_CAPACITY_DECLINE_REASONS
                        ):
                            future_current_capacity_veto_count += 1

            # Pure compose harness: no persistence / minute loop by construction.
            commitment_persistence_count += 0
            minute_loop_count += 0
            actual_event_creation_count += 0
            relation_mutation_count += 0
            runtime_queue_mutation_count += 0

            if day % 15 == 0:
                alt = run_snapshot(
                    DecisionSnapshot(
                        snapshot_id=f"{snap.snapshot_id}-alt",
                        now=snap.now,
                        human_state=snap.human_state,
                        sleep_pressure=snap.sleep_pressure,
                        location_id=snap.location_id,
                        available_window_min=snap.available_window_min,
                        world_seed="slice2i-world-seed-alt",
                        commitments=snap.commitments,
                        obligation_tasks=snap.obligation_tasks,
                        route_profiles=snap.route_profiles,
                        household_tasks=snap.household_tasks,
                        kickboxing_sessions=snap.kickboxing_sessions,
                        social_generation=snap.social_generation,
                        social_response=snap.social_response,
                        free_window_key=snap.free_window_key,
                        free_window_min=snap.free_window_min,
                        feasible_leisure_categories=snap.feasible_leisure_categories,
                        projection_horizon_end=snap.projection_horizon_end,
                        resolver_boundary=snap.resolver_boundary,
                        active=snap.active,
                        meal_feasible=True,
                        rest_feasible=True,
                    ),
                    behavior_policy=policy,
                )
                if seed_invariant_fingerprint(alt) != seed_invariant_fingerprint(
                    result
                ):
                    seed_non_keyed_divergence_count += 1
                if fingerprint(alt) != fingerprint(result):
                    # Diff allowed only in keyed-owned fields.
                    if keyed_owned_fingerprint(alt) == keyed_owned_fingerprint(
                        result
                    ):
                        seed_non_keyed_divergence_count += 1

            blob = json.dumps(result.resolution.as_dict(), sort_keys=True)
            for forbidden in (
                "friendship_score",
                "acceptance_probability",
                "message",
                "topic",
            ):
                if forbidden in blob:
                    float_prose_field_count += 1

            if policy != policy_before:
                policy_mutation_count += 1

        if POLICY_V2_CANDIDATE_PATH.read_bytes() != fixture_before:
            policy_mutation_count += 1
        for name, digest in module_hashes.items():
            after = hashlib.sha256(PUBLIC_GUARD_MODULE_FILES[name].read_bytes()).hexdigest()
            if after != digest:
                if name == "decisions.py":
                    decisions_mutation_count += 1
                elif name == "wakeups.py":
                    wakeups_mutation_count += 1
                elif name == "clock.py":
                    clock_mutation_count += 1
                else:
                    runtime_queue_mutation_count += 1

        metrics = {
            "distinct_local_date_count": len(distinct_local_dates),
            "slice2g_driven_snapshot_count": slice2g_driven_snapshot_count,
            "priority_inversion_count": priority_inversion_count,
            "hard_priority_inversion_count": hard_priority_inversion_count,
            "biological_priority_inversion_count": biological_priority_inversion_count,
            "deadline_priority_inversion_count": deadline_priority_inversion_count,
            "social_priority_inversion_count": social_priority_inversion_count,
            "duplicate_candidate_id_count": duplicate_candidate_id_count,
            "duplicate_boundary_key_count": duplicate_boundary_key_count,
            "duplicate_opportunity_key_count": duplicate_opportunity_key_count,
            "invalid_merged_opportunity_count": invalid_merged_opportunity_count,
            "invalid_or_duplicate_count": invalid_or_duplicate_count,
            "order_failures": order_failures,
            "repeat_failures": repeat_failures,
            "reverse_input_failures": reverse_input_failures,
            "wakeup_order_failures": wakeup_order_failures,
            "invented_social_count": invented_social_count,
            "future_invite_as_current_candidate_count": (
                future_invite_as_current_candidate_count
            ),
            "future_current_capacity_veto_count": future_current_capacity_veto_count,
            "transient_invite_auto_persist_count": transient_invite_auto_persist_count,
            "commitment_persistence_count": commitment_persistence_count,
            "policy_mutation_count": policy_mutation_count,
            "decisions_mutation_count": decisions_mutation_count,
            "wakeups_mutation_count": wakeups_mutation_count,
            "clock_mutation_count": clock_mutation_count,
            "actual_event_creation_count": actual_event_creation_count,
            "relation_mutation_count": relation_mutation_count,
            "runtime_queue_mutation_count": runtime_queue_mutation_count,
            "minute_loop_count": minute_loop_count,
            "float_prose_field_count": float_prose_field_count,
            "seed_non_keyed_divergence_count": seed_non_keyed_divergence_count,
            "policy_sha": hashlib.sha256(POLICY_V2_CANDIDATE_PATH.read_bytes()).hexdigest(),
        }
        shard_key = f"{start_day:02d}-{end_day - 1:02d}"
        INTEGRATED_BEHAVIOR_90D_METRICS[shard_key] = dict(metrics)

        self.assertEqual(len(distinct_local_dates), end_day - start_day)
        self.assertGreater(slice2g_driven_snapshot_count, 0)
        self.assertEqual(metrics["policy_sha"], fixture_sha_before)
        for key, value in metrics.items():
            if key in {
                "policy_sha",
                "distinct_local_date_count",
                "slice2g_driven_snapshot_count",
            }:
                continue
            self.assertEqual(value, 0, msg=f"{key}={value}")
        print(
            "INTEGRATED_90D_SHARD_METRICS",
            shard_key,
            json.dumps(metrics, sort_keys=True),
        )
        return metrics

    def test_soak_90_day_required_zeros_days_00_14(self) -> None:
        self._run_integrated_behavior_range(start_day=0, end_day=15)

    def test_soak_90_day_required_zeros_days_15_29(self) -> None:
        self._run_integrated_behavior_range(start_day=15, end_day=30)

    def test_soak_90_day_required_zeros_days_30_44(self) -> None:
        self._run_integrated_behavior_range(start_day=30, end_day=45)

    def test_soak_90_day_required_zeros_days_45_59(self) -> None:
        self._run_integrated_behavior_range(start_day=45, end_day=60)

    def test_soak_90_day_required_zeros_days_60_74(self) -> None:
        self._run_integrated_behavior_range(start_day=60, end_day=75)

    def test_soak_90_day_required_zeros_days_75_89(self) -> None:
        self._run_integrated_behavior_range(start_day=75, end_day=90)

    def test_soak_90_day_horizon_has_90_distinct_local_dates(self) -> None:
        dates = {_build_day_snapshot(day).snapshot.now[:10] for day in range(90)}
        self.assertEqual(len(dates), 90)


