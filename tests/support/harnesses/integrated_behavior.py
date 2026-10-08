"""Integrated synthetic behavior calibration harness (tests only).

Composes the reviewed pure decision layers on explicit decision snapshots.
Does not wire clock/queue/persistence/runtime, invent life history, or tune policy.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from engine.life.decisions import (
    ActionCandidate,
    ActiveDecisionContext,
    DecisionBoundaryFact,
    DecisionResolution,
    ResolvedOpportunity,
    SoftDecisionGuard,
    collect_action_candidates,
    opportunity_to_candidate,
    parse_decision_boundary_fact,
    parse_resolved_opportunity,
    parse_soft_decision_guard,
    physical_feasibility_rejection,
    resolve_action_decision,
    soft_reconsider_blocks,
)
from engine.life.derived import default_synthetic_sleep_profile
from engine.life.domain_adapters import (
    AdapterResult,
    DomainAdapterContext,
    adapt_structural_domains,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.fixed_point import empty_rate_remainders
from engine.life.policy import load_policy
from engine.life.routine_adapters import (
    RoutineAdapterContext,
    RoutineAdapterResult,
    adapt_routine_domains,
)
from engine.life.social_opportunities import (
    SocialExogenousOpportunity,
    SocialGenerationContext,
    SocialGenerationResult,
    generate_social_opportunities,
)
from engine.life.social_response import (
    SocialAvailabilityWindowFact,
    PriorSocialResponseFact,
    SocialCommitmentProposal,
    SocialResponseAdapterResult,
    SocialResponseContext,
    adapt_social_responses,
)
from engine.life.wakeups import (
    DecisionWakeupContext,
    KnownDecisionBoundary,
    ProjectionResult,
    project_decision_wakeups,
)

from tests.support.constants import POLICY_V2_CANDIDATE_PATH

CHARACTER_ID = "fixture-character"
WORLD_SEED = "slice2i-world-seed"
SECONDARY_WORLD_SEEDS = (
    "slice2i-world-seed-b",
    "slice2i-world-seed-c",
)
NOW = "2026-03-15T12:00:00+09:00"
HOME = "fixture-home"
CAMPUS = "fixture-campus"
DESK = "fixture-desk"
GYM = "fixture-gym"

MIZUKI = "fixture-remote-friend"
SAYAKA = "fixture-local-friend"
RENA = "fixture-work-contact"
KNOWN_PERSON_IDS = (MIZUKI, SAYAKA, RENA)

SELECTION_TIERS = (
    "HARD_COMMITMENT",
    "URGENT_BIOLOGICAL",
    "DEADLINE_TASK",
    "SOCIAL_PROMISE",
    "ROUTINE_HABIT",
    "RESTORATIVE",
    "LEISURE_SPONTANEOUS",
)

TIER_RANK = {t: i for i, t in enumerate(SELECTION_TIERS)}


def load_candidate_policy() -> dict[str, Any]:
    """Load the versioned public synthetic Behavior Policy fixture."""
    return load_policy(POLICY_V2_CANDIDATE_PATH)


def human_state(**overrides: int) -> dict[str, int]:
    base = {
        "sleep_debt_min": 0,
        "hunger": 200,
        "physical_fatigue": 200,
        "affect_valence": 0,
        "stress": 150,
        "social_battery": 700,
    }
    base.update(overrides)
    return base


def resting_activity() -> dict[str, str]:
    return {"base_activity_class": "REST_AWAKE", "social_exposure": "NONE"}


def awake_wakeup_context(**overrides: Any) -> DecisionWakeupContext:
    data = dict(
        is_sleeping=False,
        active_interruptibility="NONE",
        next_legal_reassessment_at=None,
        fatigue_high_relevant=False,
        stress_relevant=False,
        social_low_relevant=False,
    )
    data.update(overrides)
    return DecisionWakeupContext(**data)


def structural_ctx(
    *,
    now: str = NOW,
    location: str = HOME,
    available_window_min: int = 120,
    physical: Mapping[str, bool] | None = None,
) -> DomainAdapterContext:
    phys = {
        "WORK": True,
        "HARD_COMMITMENT_ACTIVITY": True,
        "TRAVEL_TO_COMMITMENT": True,
        "SOCIAL_PROMISE": True,
        "ERRAND": True,
        "STUDY": True,
    }
    if physical:
        phys.update(dict(physical))
    return DomainAdapterContext(
        now=now,
        current_location_id=location,
        available_window_min=available_window_min,
        physical_feasible_by_action=phys,
    )


def routine_ctx(
    *,
    now: str = NOW,
    location: str = HOME,
    available_window_min: int = 120,
    physical: Mapping[str, bool] | None = None,
    kickboxing_location_feasible: bool = True,
    kickboxing_self_generated_today: int = 0,
    kickboxing_equivalent_pending_plans: int = 0,
    physical_fatigue_band: str = "LOW",
) -> RoutineAdapterContext:
    if physical is None:
        phys: dict[str, bool] = {"HOUSEHOLD": True, "KICKBOXING": True}
    else:
        phys = dict(physical)
    return RoutineAdapterContext(
        now=now,
        current_location_id=location,
        available_window_min=available_window_min,
        physical_feasible_by_action=phys,
        kickboxing_location_feasible=kickboxing_location_feasible,
        kickboxing_self_generated_today=kickboxing_self_generated_today,
        kickboxing_equivalent_pending_plans=kickboxing_equivalent_pending_plans,
        physical_fatigue_band=physical_fatigue_band,
    )


def social_response_ctx(
    *,
    now: str = NOW,
    sleep_pressure: int = 200,
    available_window_min: int = 120,
    hard_commitment_blocking_now: bool = False,
    social_contact_physical_feasible: bool = True,
    post_work_location_feasible: bool = True,
    human: Mapping[str, int] | None = None,
) -> SocialResponseContext:
    return SocialResponseContext(
        now=now,
        human_state=dict(human if human is not None else human_state()),
        sleep_pressure=sleep_pressure,
        available_window_min=available_window_min,
        hard_commitment_blocking_now=hard_commitment_blocking_now,
        social_contact_physical_feasible=social_contact_physical_feasible,
        post_work_location_feasible=post_work_location_feasible,
    )


def resolver_boundary(
    *,
    trigger_kind: str = "IDLE",
    decision_key: str = "idle:slice2i",
    reason_code: str = "IDLE",
    metric: str | None = None,
    target_band: str | None = None,
    source_ref: str | None = None,
) -> DecisionBoundaryFact:
    return DecisionBoundaryFact(
        trigger_kind=trigger_kind,
        decision_key=decision_key,
        reason_code=reason_code,
        metric=metric,
        target_band=target_band,
        source_ref=source_ref,
    )


def _prov(**overrides: Any) -> dict[str, Any]:
    base = {"origin": "ENGINE_DEFAULT", "notes": "synthetic-slice2i"}
    base.update(overrides)
    return base


def exact_commitment(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "commitment_id": "cmt-exact-1",
        "kind": "WORK",
        "hardness": "HARD",
        "created_at": "2026-03-15T08:00:00+09:00",
        "timing": {
            "timing_kind": "EXACT",
            "planned_start": "2026-03-15T14:00:00+09:00",
            "planned_end": "2026-03-15T18:00:00+09:00",
        },
        "location_id": CAMPUS,
        "participants": [CHARACTER_ID],
        "source_kind": "INTERNAL",
        "provenance": _prov(),
        "recurrence_id": None,
        "status": "PLANNED",
        "status_history": [
            {
                "changed_at": "2026-03-15T08:00:00+09:00",
                "from": None,
                "to": "PLANNED",
                "reason": "created",
                "source_ref": "engine:create",
            }
        ],
    }
    timing_override = overrides.pop("timing", None)
    history_override = overrides.pop("status_history", None)
    created_at = overrides.get("created_at")
    base.update(overrides)
    if timing_override is not None:
        base["timing"] = timing_override
    if history_override is not None:
        base["status_history"] = history_override
    elif created_at is not None:
        # Keep status_history aligned when caller shifts created_at.
        base["status_history"] = [
            {
                "changed_at": created_at,
                "from": None,
                "to": "PLANNED",
                "reason": "created",
                "source_ref": "engine:create",
            }
        ]
    return base


def window_commitment(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "commitment_id": "cmt-window-1",
        "kind": "SOCIAL",
        "hardness": "SOFT",
        "source_kind": "EXOGENOUS_SOCIAL",
        "timing": {
            "timing_kind": "WINDOW",
            "earliest_start": "2026-03-15T18:00:00+09:00",
            "latest_start": "2026-03-15T20:00:00+09:00",
            "duration_min": 60,
            "duration_max": 120,
        },
        "location_id": None,
        "participants": [CHARACTER_ID, SAYAKA],
    }
    defaults.update(overrides)
    return exact_commitment(**defaults)


def obligation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "task-1",
        "domain": "ACADEMIC",
        "created_at": "2026-03-15T09:00:00+09:00",
        "earliest_start": None,
        "due_at": "2026-03-15T18:00:00+09:00",
        "due_window": None,
        "effort_remaining_min": 120,
        "min_work_chunk_min": 30,
        "priority": "NORMAL",
        "location_constraints": [DESK],
        "status": "OPEN",
        "provenance": _prov(),
        "source_kind": "INTERNAL",
        "caused_by_event_id": None,
    }
    base.update(overrides)
    return base


def route(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "route_profile_id": "route-home-campus",
        "origin_location_id": HOME,
        "destination_location_id": CAMPUS,
        "mode": "PUBLIC_TRANSIT",
        "duration_min": 25,
        "duration_max": 45,
        "effort_class": "LIGHT_ACTIVE",
        "provenance": _prov(),
    }
    base.update(overrides)
    return base


def household(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "household_task_id": "hh-1",
        "created_at": "2026-03-13T12:00:00+09:00",
        "status": "OPEN",
        "location_id": HOME,
    }
    base.update(overrides)
    return base


def kickboxing_session(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "kb-1",
        "actual_start": "2026-03-14T18:00:00+09:00",
        "actual_end": "2026-03-14T19:00:00+09:00",
        "finalized_at": "2026-03-14T19:05:00+09:00",
    }
    base.update(overrides)
    return base


def archetype_assignment(person_id: str, archetype_id: str, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "person_id": person_id,
        "archetype_id": archetype_id,
        "provenance": _prov(),
    }
    base.update(overrides)
    return base


def default_assignments() -> list[dict[str, Any]]:
    return [
        archetype_assignment(MIZUKI, "REMOTE_CLOSE_BURSTY"),
        archetype_assignment(SAYAKA, "LOCAL_CLOSE_INVITER"),
        archetype_assignment(RENA, "WORK_CONTEXTUAL"),
    ]


def merge_opportunities(
    *groups: Sequence[ResolvedOpportunity],
) -> tuple[ResolvedOpportunity, ...]:
    """Canonical sort by opportunity_key; fail closed on duplicates."""
    items: list[ResolvedOpportunity] = []
    for group in groups:
        for opp in group:
            items.append(parse_resolved_opportunity(opp))
    keys = [o.opportunity_key for o in items]
    if len(keys) != len(set(keys)):
        dups = sorted({k for k in keys if keys.count(k) > 1})
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"ambiguous duplicate opportunity_key: {dups}",
        )
    return tuple(sorted(items, key=lambda o: o.opportunity_key))


def merge_boundaries(
    *groups: Sequence[KnownDecisionBoundary],
) -> tuple[KnownDecisionBoundary, ...]:
    """Canonical sort by (due_at, trigger_kind, decision_key); fail on duplicate key."""
    items: list[KnownDecisionBoundary] = []
    for group in groups:
        items.extend(list(group))
    by_key: dict[str, KnownDecisionBoundary] = {}
    for b in items:
        prev = by_key.get(b.decision_key)
        if prev is not None:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"duplicate decision_key in merged boundaries: {b.decision_key}",
            )
        by_key[b.decision_key] = b
    ordered = sorted(
        by_key.values(),
        key=lambda b: (b.due_at, b.trigger_kind, b.decision_key),
    )
    return tuple(ordered)


@dataclass(frozen=True)
class SocialGenerationSpec:
    """Explicit 2G arrival facts for one snapshot (optional)."""

    week_start_date: str
    as_of: str
    known_person_ids: tuple[str, ...] = KNOWN_PERSON_IDS
    assignments: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    contacts: tuple[Mapping[str, Any], ...] = ()
    post_work_contexts: tuple[Mapping[str, Any], ...] = ()
    # When set, skip 2G and feed these exogenous opps directly to 2H.
    exogenous_opportunities: tuple[SocialExogenousOpportunity | Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class SocialResponseSpec:
    availability_windows: tuple[SocialAvailabilityWindowFact | Mapping[str, Any], ...] = ()
    prior_responses: tuple[PriorSocialResponseFact | Mapping[str, Any], ...] = ()
    hard_commitment_blocking_now: bool = False
    social_contact_physical_feasible: bool = True
    post_work_location_feasible: bool = True


@dataclass(frozen=True)
class DecisionSnapshot:
    """One explicit synthetic decision snapshot (not a recursive life step)."""

    snapshot_id: str
    now: str
    human_state: Mapping[str, int]
    sleep_pressure: int
    location_id: str = HOME
    available_window_min: int = 120
    world_seed: str = WORLD_SEED
    character_id: str = CHARACTER_ID
    # 2E
    commitments: tuple[Mapping[str, Any], ...] = ()
    obligation_tasks: tuple[Mapping[str, Any], ...] = ()
    route_profiles: tuple[Mapping[str, Any], ...] = ()
    structural_physical: Mapping[str, bool] | None = None
    # 2F
    household_tasks: tuple[Mapping[str, Any], ...] = ()
    kickboxing_sessions: tuple[Mapping[str, Any], ...] = ()
    routine_physical: Mapping[str, bool] | None = None
    kickboxing_location_feasible: bool = True
    kickboxing_self_generated_today: int = 0
    kickboxing_equivalent_pending_plans: int = 0
    physical_fatigue_band: str = "LOW"
    # 2G/2H (None ⇒ skip social layers)
    social_generation: SocialGenerationSpec | None = None
    social_response: SocialResponseSpec | None = None
    # 2C
    wakeup_context: DecisionWakeupContext | Mapping[str, Any] | None = None
    activity_context: Mapping[str, str] | None = None
    rate_remainders: Mapping[str, int] | None = None
    sleep_profile: Any = None
    elapsed_since_last_main_sleep_end_seconds: int = 8 * 3600
    total_nap_awake_credit_min: int = 0
    projection_horizon_end: str | None = None
    # 2D
    resolver_boundary: DecisionBoundaryFact | Mapping[str, Any] | None = None
    active: ActiveDecisionContext | Mapping[str, Any] | None = None
    soft_guards: tuple[SoftDecisionGuard | Mapping[str, Any], ...] = ()
    meal_feasible: bool = True
    rest_feasible: bool = True
    sleep_opportunity: str | None = None
    last_meal_at: str | None = None
    meal_extent: str | None = None
    free_window_key: str | None = None
    free_window_min: int | None = None
    feasible_leisure_categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegratedTelemetry:
    opportunity_keys: tuple[str, ...]
    opportunity_classes: tuple[str, ...]
    boundary_decision_keys: tuple[str, ...]
    social_exogenous_ids: tuple[str, ...]
    social_response_outcomes: tuple[tuple[str, str, str], ...]  # id, kind, response
    commitment_proposal_ids: tuple[str, ...]
    immediate_reassessment_reasons: tuple[str, ...]
    selected_wakeup_trigger: str | None
    selected_wakeup_due_at: str | None
    selected_wakeup_source_kind: str | None
    selected_wakeup_source_ref: str | None
    future_candidate_count: int
    resolution_result_kind: str
    selected_priority_tier: str | None
    selected_action_kind: str | None
    selected_candidate_key: str | None
    selection_mode: str | None
    min_dwell_forced: bool
    random_key: str | None


@dataclass(frozen=True)
class IntegratedSnapshotResult:
    snapshot_id: str
    policy_version: str
    structural: AdapterResult
    routine: RoutineAdapterResult
    social_generation: SocialGenerationResult | None
    social_response: SocialResponseAdapterResult | None
    merged_opportunities: tuple[ResolvedOpportunity, ...]
    merged_boundaries: tuple[KnownDecisionBoundary, ...]
    wakeup: ProjectionResult
    resolution: DecisionResolution
    commitment_proposals: tuple[SocialCommitmentProposal, ...]
    telemetry: IntegratedTelemetry


def _opp_fingerprint(opps: Sequence[ResolvedOpportunity]) -> tuple:
    return tuple(
        (
            o.opportunity_key,
            o.opportunity_class,
            o.action_kind,
            o.source_kind,
            o.source_ref,
            o.soft_candidate,
            o.time_feasible,
            o.location_feasible,
            o.physical_feasible,
            o.domain_guard_satisfied,
            o.local_preference_permille,
            tuple(o.rule_ids),
        )
        for o in opps
    )


def _boundary_fingerprint(bounds: Sequence[KnownDecisionBoundary]) -> tuple:
    return tuple(
        (
            b.due_at,
            b.trigger_kind,
            b.decision_key,
            b.reason_code,
            b.source_kind,
            b.source_ref,
        )
        for b in bounds
    )


def _social_gen_fingerprint(result: IntegratedSnapshotResult) -> tuple:
    if result.social_generation is None:
        return ()
    return tuple(
        (
            o.opportunity_id,
            o.opportunity_kind,
            o.person_id,
            o.available_local_date,
            o.invite_timing_kind,
            o.target_local_date,
        )
        for o in result.social_generation.opportunities
    )


def _social_resp_fingerprint(result: IntegratedSnapshotResult) -> tuple:
    if result.social_response is None:
        return ()
    return tuple(
        (r.opportunity_id, r.response, r.reason_code, r.contact_mode)
        for r in result.social_response.responses
    )


def _wakeup_fingerprint(result: IntegratedSnapshotResult) -> tuple:
    wake = result.wakeup
    sel = wake.selected
    return (
        tuple(wake.immediate_reassessment_reasons),
        None
        if sel is None
        else (sel.due_at, sel.trigger_kind, sel.decision_key, sel.reason_code),
        tuple(
            (c.due_at, c.trigger_kind, c.decision_key, c.reason_code)
            for c in wake.future_candidates
        ),
        wake.sleep_projection_mode,
    )


def _resolver_non_keyed_fingerprint(result: IntegratedSnapshotResult) -> tuple:
    """Resolver fields that must be seed-invariant (excludes keyed winner fields)."""
    res = result.resolution
    return (
        res.result_kind if res.selection_mode != "keyed_tie" else None,
        res.decision_key,
        res.min_dwell_forced,
        tuple(res.considered_candidate_ids),
        tuple((r.candidate_key, r.reason) for r in res.rejected),
        # Include selected fields only when selection is not keyed-random.
        None
        if res.selection_mode == "keyed_tie"
        else (
            res.selected_priority_tier,
            res.selected_action_kind,
            res.selected_candidate_key,
            res.selection_mode,
        ),
    )


def _resolver_keyed_fingerprint(result: IntegratedSnapshotResult) -> tuple:
    """2D keyed same-tier selection outputs (may differ across world_seed)."""
    res = result.resolution
    return (
        res.selected_priority_tier,
        res.selected_action_kind,
        res.selected_candidate_key,
        res.selection_mode,
        res.random_key,
        res.selected_candidate_id,
    )


def seed_invariant_fingerprint(result: IntegratedSnapshotResult) -> tuple:
    """Fingerprint of fields that world_seed must not change.

    Excludes 2G arrival outputs and 2D keyed-tie winner fields.
    Structural/routine opportunities, boundaries, wakeup, and non-keyed
    resolver fields must stay identical across seeds.
    """
    return (
        result.policy_version,
        _opp_fingerprint(result.structural.opportunities),
        _opp_fingerprint(result.routine.opportunities),
        _boundary_fingerprint(result.merged_boundaries),
        _wakeup_fingerprint(result),
        _resolver_non_keyed_fingerprint(result),
        # Commitment proposals are derived from 2H over (possibly 2G) arrivals;
        # proposal *shape* without keyed ids is still checked via response reasons
        # in keyed_owned when social runs. Non-social snapshots have none.
        ()
        if result.social_generation is not None
        or (
            result.social_response is not None
            and result.telemetry.social_exogenous_ids
        )
        else tuple(
            (p.proposal_id, p.source_opportunity_id, p.commitment.get("commitment_id"))
            for p in result.commitment_proposals
        ),
    )


def keyed_owned_fingerprint(result: IntegratedSnapshotResult) -> tuple:
    """Fingerprint of fields owned by existing keyed-random mechanisms (2D/2G)."""
    proposals = tuple(
        (p.proposal_id, p.source_opportunity_id, p.commitment.get("commitment_id"))
        for p in result.commitment_proposals
    )
    social_opps = tuple(
        o.opportunity_key
        for o in result.merged_opportunities
        if o.opportunity_class == "SOCIAL_PROMISE"
    )
    return (
        _social_gen_fingerprint(result),
        _social_resp_fingerprint(result),
        social_opps,
        proposals,
        _resolver_keyed_fingerprint(result),
    )


def fingerprint(result: IntegratedSnapshotResult) -> tuple:
    """Order-independent semantic fingerprint for reverse/repeat checks."""
    return (
        seed_invariant_fingerprint(result),
        keyed_owned_fingerprint(result),
    )


def run_snapshot(
    snapshot: DecisionSnapshot,
    *,
    behavior_policy: Mapping[str, Any] | None = None,
) -> IntegratedSnapshotResult:
    """Compose 2E→2F→(2G→2H)→merge→2C→2D for one explicit snapshot."""
    policy = behavior_policy if behavior_policy is not None else load_candidate_policy()
    policy_version = str(policy.get("behavior_policy_version", ""))

    structural = adapt_structural_domains(
        context=structural_ctx(
            now=snapshot.now,
            location=snapshot.location_id,
            available_window_min=snapshot.available_window_min,
            physical=snapshot.structural_physical,
        ),
        commitments=list(snapshot.commitments),
        obligation_tasks=list(snapshot.obligation_tasks),
        route_profiles=list(snapshot.route_profiles),
    )

    routine = adapt_routine_domains(
        context=routine_ctx(
            now=snapshot.now,
            location=snapshot.location_id,
            available_window_min=snapshot.available_window_min,
            physical=snapshot.routine_physical,
            kickboxing_location_feasible=snapshot.kickboxing_location_feasible,
            kickboxing_self_generated_today=snapshot.kickboxing_self_generated_today,
            kickboxing_equivalent_pending_plans=snapshot.kickboxing_equivalent_pending_plans,
            physical_fatigue_band=snapshot.physical_fatigue_band,
        ),
        behavior_policy=policy,
        household_tasks=list(snapshot.household_tasks),
        kickboxing_sessions=list(snapshot.kickboxing_sessions),
    )

    social_gen: SocialGenerationResult | None = None
    social_resp: SocialResponseAdapterResult | None = None
    social_opps: tuple[ResolvedOpportunity, ...] = ()
    proposals: tuple[SocialCommitmentProposal, ...] = ()

    if snapshot.social_generation is not None:
        gen_spec = snapshot.social_generation
        exogenous: Sequence[SocialExogenousOpportunity | Mapping[str, Any]]
        if gen_spec.exogenous_opportunities:
            exogenous = gen_spec.exogenous_opportunities
            social_gen = None
        else:
            assignments = (
                list(gen_spec.assignments)
                if gen_spec.assignments
                else default_assignments()
            )
            social_gen = generate_social_opportunities(
                context=SocialGenerationContext(
                    character_id=snapshot.character_id,
                    world_seed=snapshot.world_seed,
                    week_start_date=gen_spec.week_start_date,
                    as_of=gen_spec.as_of,
                ),
                behavior_policy=policy,
                known_person_ids=list(gen_spec.known_person_ids),
                assignments=assignments,
                contacts=list(gen_spec.contacts),
                post_work_contexts=list(gen_spec.post_work_contexts),
            )
            exogenous = social_gen.opportunities

        resp_spec = snapshot.social_response or SocialResponseSpec()
        social_resp = adapt_social_responses(
            context=social_response_ctx(
                now=snapshot.now,
                sleep_pressure=snapshot.sleep_pressure,
                available_window_min=snapshot.available_window_min,
                hard_commitment_blocking_now=resp_spec.hard_commitment_blocking_now,
                social_contact_physical_feasible=resp_spec.social_contact_physical_feasible,
                post_work_location_feasible=resp_spec.post_work_location_feasible,
                human=snapshot.human_state,
            ),
            behavior_policy=policy,
            known_person_ids=list(gen_spec.known_person_ids),
            opportunities=list(exogenous),
            availability_windows=list(resp_spec.availability_windows),
            prior_responses=list(resp_spec.prior_responses),
        )
        # Invite proposals stay transient — do NOT auto-merge into resolver opps.
        social_opps = tuple(social_resp.opportunities)
        proposals = tuple(social_resp.commitment_proposals)

    merged_opps = merge_opportunities(
        structural.opportunities,
        routine.opportunities,
        social_opps,
    )
    merged_bounds = merge_boundaries(
        structural.decision_boundaries,
        routine.decision_boundaries,
    )

    # Causal validation for every merged opportunity (Slice-2D table).
    for opp in merged_opps:
        opportunity_to_candidate(
            character_id=snapshot.character_id,
            decision_key=f"causal:{snapshot.snapshot_id}",
            opportunity=opp,
        )

    wakeup = project_decision_wakeups(
        policy=policy,
        character_id=snapshot.character_id,
        human_state=dict(snapshot.human_state),
        rate_remainders=(
            dict(snapshot.rate_remainders)
            if snapshot.rate_remainders is not None
            else empty_rate_remainders()
        ),
        activity_context=dict(snapshot.activity_context or resting_activity()),
        now=snapshot.now,
        wakeup_context=snapshot.wakeup_context or awake_wakeup_context(),
        sleep_profile=(
            snapshot.sleep_profile
            if snapshot.sleep_profile is not None
            else default_synthetic_sleep_profile()
        ),
        elapsed_since_last_main_sleep_end_seconds=(
            snapshot.elapsed_since_last_main_sleep_end_seconds
        ),
        total_nap_awake_credit_min=snapshot.total_nap_awake_credit_min,
        known_boundaries=merged_bounds,
        projection_horizon_end=snapshot.projection_horizon_end,
    )

    boundary = snapshot.resolver_boundary or resolver_boundary(
        decision_key=f"idle:{snapshot.snapshot_id}",
    )
    # Resolver sleep_pressure only when sleep_opportunity is explicit (2D contract).
    sleep_opp = snapshot.sleep_opportunity
    resolve_kwargs: dict[str, Any] = {
        "world_seed": snapshot.world_seed,
        "character_id": snapshot.character_id,
        "policy": policy,
        "boundary": boundary,
        "now": snapshot.now,
        "human_state": dict(snapshot.human_state),
        "active": snapshot.active,
        "opportunities": merged_opps,
        "soft_guards": list(snapshot.soft_guards),
        "meal_feasible": snapshot.meal_feasible,
        "rest_feasible": snapshot.rest_feasible,
        "last_meal_at": snapshot.last_meal_at,
        "meal_extent": snapshot.meal_extent,
        "free_window_key": snapshot.free_window_key,
        "free_window_min": snapshot.free_window_min,
        "feasible_leisure_categories": list(snapshot.feasible_leisure_categories),
    }
    if sleep_opp is not None:
        resolve_kwargs["sleep_pressure"] = snapshot.sleep_pressure
        resolve_kwargs["sleep_opportunity"] = sleep_opp
    resolution = resolve_action_decision(**resolve_kwargs)

    sel = wakeup.selected
    social_outcomes: list[tuple[str, str, str]] = []
    exogenous_ids: list[str] = []
    if social_gen is not None:
        exogenous_ids = [o.opportunity_id for o in social_gen.opportunities]
    elif snapshot.social_generation is not None:
        for o in snapshot.social_generation.exogenous_opportunities:
            if isinstance(o, SocialExogenousOpportunity):
                exogenous_ids.append(o.opportunity_id)
            else:
                exogenous_ids.append(str(o["opportunity_id"]))
    if social_resp is not None:
        social_outcomes = [
            (r.opportunity_id, r.opportunity_kind, r.response)
            for r in social_resp.responses
        ]

    telemetry = IntegratedTelemetry(
        opportunity_keys=tuple(o.opportunity_key for o in merged_opps),
        opportunity_classes=tuple(o.opportunity_class for o in merged_opps),
        boundary_decision_keys=tuple(b.decision_key for b in merged_bounds),
        social_exogenous_ids=tuple(exogenous_ids),
        social_response_outcomes=tuple(social_outcomes),
        commitment_proposal_ids=tuple(p.proposal_id for p in proposals),
        immediate_reassessment_reasons=tuple(wakeup.immediate_reassessment_reasons),
        selected_wakeup_trigger=None if sel is None else sel.trigger_kind,
        selected_wakeup_due_at=None if sel is None else sel.due_at,
        selected_wakeup_source_kind=None if sel is None else sel.source_kind,
        selected_wakeup_source_ref=None if sel is None else sel.source_ref,
        future_candidate_count=len(wakeup.future_candidates),
        resolution_result_kind=resolution.result_kind,
        selected_priority_tier=resolution.selected_priority_tier,
        selected_action_kind=resolution.selected_action_kind,
        selected_candidate_key=resolution.selected_candidate_key,
        selection_mode=resolution.selection_mode,
        min_dwell_forced=resolution.min_dwell_forced,
        random_key=resolution.random_key,
    )

    return IntegratedSnapshotResult(
        snapshot_id=snapshot.snapshot_id,
        policy_version=policy_version,
        structural=structural,
        routine=routine,
        social_generation=social_gen,
        social_response=social_resp,
        merged_opportunities=merged_opps,
        merged_boundaries=merged_bounds,
        wakeup=wakeup,
        resolution=resolution,
        commitment_proposals=proposals,
        telemetry=telemetry,
    )


def reverse_snapshot_inputs(snapshot: DecisionSnapshot) -> DecisionSnapshot:
    """Reverse sequence inputs only (facts unchanged) for order-independence proof."""
    gen = snapshot.social_generation
    resp = snapshot.social_response
    rev_gen = None
    if gen is not None:
        rev_gen = SocialGenerationSpec(
            week_start_date=gen.week_start_date,
            as_of=gen.as_of,
            known_person_ids=tuple(reversed(gen.known_person_ids)),
            assignments=tuple(reversed(gen.assignments)) if gen.assignments else (),
            contacts=tuple(reversed(gen.contacts)),
            post_work_contexts=tuple(reversed(gen.post_work_contexts)),
            exogenous_opportunities=tuple(reversed(gen.exogenous_opportunities)),
        )
    rev_resp = None
    if resp is not None:
        rev_resp = SocialResponseSpec(
            availability_windows=tuple(reversed(resp.availability_windows)),
            prior_responses=tuple(reversed(resp.prior_responses)),
            hard_commitment_blocking_now=resp.hard_commitment_blocking_now,
            social_contact_physical_feasible=resp.social_contact_physical_feasible,
            post_work_location_feasible=resp.post_work_location_feasible,
        )
    return DecisionSnapshot(
        snapshot_id=snapshot.snapshot_id,
        now=snapshot.now,
        human_state=dict(snapshot.human_state),
        sleep_pressure=snapshot.sleep_pressure,
        location_id=snapshot.location_id,
        available_window_min=snapshot.available_window_min,
        world_seed=snapshot.world_seed,
        character_id=snapshot.character_id,
        commitments=tuple(reversed(snapshot.commitments)),
        obligation_tasks=tuple(reversed(snapshot.obligation_tasks)),
        route_profiles=tuple(reversed(snapshot.route_profiles)),
        structural_physical=snapshot.structural_physical,
        household_tasks=tuple(reversed(snapshot.household_tasks)),
        kickboxing_sessions=tuple(reversed(snapshot.kickboxing_sessions)),
        routine_physical=snapshot.routine_physical,
        kickboxing_location_feasible=snapshot.kickboxing_location_feasible,
        kickboxing_self_generated_today=snapshot.kickboxing_self_generated_today,
        kickboxing_equivalent_pending_plans=snapshot.kickboxing_equivalent_pending_plans,
        physical_fatigue_band=snapshot.physical_fatigue_band,
        social_generation=rev_gen,
        social_response=rev_resp,
        wakeup_context=snapshot.wakeup_context,
        activity_context=snapshot.activity_context,
        rate_remainders=snapshot.rate_remainders,
        sleep_profile=snapshot.sleep_profile,
        elapsed_since_last_main_sleep_end_seconds=(
            snapshot.elapsed_since_last_main_sleep_end_seconds
        ),
        total_nap_awake_credit_min=snapshot.total_nap_awake_credit_min,
        projection_horizon_end=snapshot.projection_horizon_end,
        resolver_boundary=snapshot.resolver_boundary,
        active=snapshot.active,
        soft_guards=tuple(reversed(snapshot.soft_guards)),
        meal_feasible=snapshot.meal_feasible,
        rest_feasible=snapshot.rest_feasible,
        sleep_opportunity=snapshot.sleep_opportunity,
        last_meal_at=snapshot.last_meal_at,
        meal_extent=snapshot.meal_extent,
        free_window_key=snapshot.free_window_key,
        free_window_min=snapshot.free_window_min,
        feasible_leisure_categories=tuple(reversed(snapshot.feasible_leisure_categories)),
    )


def contact_history(
    *,
    contact_id: str = "contact-1",
    person_id: str = MIZUKI,
    actual_end: str = "2026-03-08T20:00:00+09:00",
    finalized_at: str = "2026-03-08T20:05:00+09:00",
) -> dict[str, Any]:
    return {
        "contact_id": contact_id,
        "person_id": person_id,
        "actual_end": actual_end,
        "finalized_at": finalized_at,
    }


def post_work_context(
    *,
    work_context_id: str = "work-1",
    actual_end: str = "2026-03-12T18:00:00+09:00",
    finalized_at: str = "2026-03-12T18:05:00+09:00",
    participant_person_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "work_context_id": work_context_id,
        "actual_end": actual_end,
        "finalized_at": finalized_at,
        "participant_person_ids": list(
            participant_person_ids if participant_person_ids is not None else [RENA]
        ),
    }


def deep_copy_policy(policy: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return copy.deepcopy(dict(policy if policy is not None else load_candidate_policy()))


def _decision_thresholds(policy: Mapping[str, Any]) -> dict[str, int]:
    """Mirror Slice-2D threshold extraction (test-side; no production import of private)."""
    thr = policy["decision_thresholds"]
    return {
        "soft_reconsider_guard_min": int(thr["soft_reconsider_guard_min"]),
        "reassessable_min_dwell_min": int(thr["reassessable_min_dwell_min"]),
        "near_equal_margin_permille": int(thr["near_equal_margin_permille"]),
    }


def collect_eligible_candidates(
    *,
    snapshot: DecisionSnapshot,
    opportunities: Sequence[ResolvedOpportunity],
    policy: Mapping[str, Any],
) -> tuple[ActionCandidate, ...]:
    """Rebuild the full Slice-2D eligible set (bio / leisure / CONTINUE / opps)."""
    boundary = snapshot.resolver_boundary or resolver_boundary(
        decision_key=f"idle:{snapshot.snapshot_id}",
    )
    boundary_f = parse_decision_boundary_fact(boundary)
    sleep_opp = snapshot.sleep_opportunity
    collect_kwargs: dict[str, Any] = {
        "character_id": snapshot.character_id,
        "decision_key": boundary_f.decision_key,
        "policy": policy,
        "human_state": dict(snapshot.human_state),
        "active": snapshot.active,
        "opportunities": opportunities,
        "meal_feasible": snapshot.meal_feasible,
        "rest_feasible": snapshot.rest_feasible,
        "last_meal_at": snapshot.last_meal_at,
        "meal_extent": snapshot.meal_extent,
        "now": snapshot.now,
        "free_window_key": snapshot.free_window_key,
        "free_window_min": snapshot.free_window_min,
        "feasible_leisure_categories": list(snapshot.feasible_leisure_categories),
    }
    if sleep_opp is not None:
        collect_kwargs["sleep_pressure"] = snapshot.sleep_pressure
        collect_kwargs["sleep_opportunity"] = sleep_opp
    raw, _early = collect_action_candidates(**collect_kwargs)
    thr = _decision_thresholds(policy)
    guards = [
        parse_soft_decision_guard(g, now=snapshot.now) for g in snapshot.soft_guards
    ]
    eligible: list[ActionCandidate] = []
    for cand in raw:
        if physical_feasibility_rejection(cand) is not None:
            continue
        if soft_reconsider_blocks(
            cand,
            guards=guards,
            now=snapshot.now,
            soft_reconsider_guard_min=thr["soft_reconsider_guard_min"],
        ) is not None:
            continue
        eligible.append(cand)
    return tuple(eligible)


def classify_priority_inversions(
    resolution: DecisionResolution,
    eligible: Sequence[ActionCandidate],
) -> dict[str, int]:
    """Count inversions vs full eligible set. min-dwell is an allowed exception."""
    zeros = {
        "hard_priority_inversion_count": 0,
        "biological_priority_inversion_count": 0,
        "deadline_priority_inversion_count": 0,
        "social_priority_inversion_count": 0,
        "priority_inversion_count": 0,
    }
    if resolution.min_dwell_forced:
        return zeros
    if resolution.selected_priority_tier is None:
        return zeros
    selected_rank = TIER_RANK[resolution.selected_priority_tier]
    hard = bio = deadline = social = total = 0
    for cand in eligible:
        rank = TIER_RANK[cand.priority_tier]
        if rank >= selected_rank:
            continue
        total += 1
        if cand.priority_tier == "HARD_COMMITMENT":
            hard += 1
        elif cand.priority_tier == "URGENT_BIOLOGICAL":
            bio += 1
        elif cand.priority_tier == "DEADLINE_TASK":
            deadline += 1
        elif cand.priority_tier == "SOCIAL_PROMISE":
            social += 1
    return {
        "hard_priority_inversion_count": hard,
        "biological_priority_inversion_count": bio,
        "deadline_priority_inversion_count": deadline,
        "social_priority_inversion_count": social,
        "priority_inversion_count": total,
    }


def assert_no_priority_inversion(
    resolution: DecisionResolution,
    eligible: Sequence[ActionCandidate],
) -> None:
    """If a higher-tier eligible candidate existed, selection must not pick lower.

    Validates against the full Slice-2D eligible set (biological / leisure /
    CONTINUE / merged opportunities). min-dwell forced continuation is exempt.
    """
    counts = classify_priority_inversions(resolution, eligible)
    if counts["priority_inversion_count"]:
        raise AssertionError(
            f"priority inversion: selected {resolution.selected_priority_tier} "
            f"with higher-tier eligible present "
            f"(hard={counts['hard_priority_inversion_count']}, "
            f"bio={counts['biological_priority_inversion_count']}, "
            f"deadline={counts['deadline_priority_inversion_count']}, "
            f"social={counts['social_priority_inversion_count']})"
        )
