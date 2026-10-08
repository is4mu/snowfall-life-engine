"""Synthetic harness helpers for resolver tests (no I/O)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from engine.life.decisions import (
    ActiveDecisionContext,
    DecisionBoundaryFact,
    ResolvedOpportunity,
    SoftDecisionGuard,
    resolve_action_decision,
)
from engine.life.dynamics import integrate_v2_interval
from engine.life.policy import load_policy

from tests.support.constants import POLICY_V2_CANDIDATE_PATH

WORLD_SEED = "slice2d-world-seed"
CHARACTER_ID = "fixture-character"
NOW = "2026-03-15T12:00:00+09:00"


def load_v2_policy() -> dict[str, Any]:
    return load_policy(POLICY_V2_CANDIDATE_PATH)


def hs(**overrides: int) -> dict[str, int]:
    base = {
        "sleep_debt_min": 0,
        "hunger": 100,
        "physical_fatigue": 100,
        "affect_valence": 0,
        "stress": 100,
        "social_battery": 800,
    }
    base.update(overrides)
    return base


def empty_remainders() -> dict[str, int]:
    from engine.life.fixed_point import empty_rate_remainders

    return empty_rate_remainders()


def boundary(
    *,
    trigger_kind: str = "THRESHOLD",
    decision_key: str = "threshold:HUNGER:MEAL_NEEDED:UP",
    reason_code: str = "THRESHOLD_HUNGER_MEAL_NEEDED_UP",
    metric: str | None = "HUNGER",
    target_band: str | None = "MEAL_NEEDED",
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


def opportunity(
    *,
    key: str,
    opp_class: str,
    action_kind: str,
    source_kind: str,
    source_ref: str,
    soft: bool = False,
    time_ok: bool = True,
    loc_ok: bool = True,
    phys_ok: bool = True,
    domain_ok: bool = True,
    pref: int | None = None,
    rule_ids: Sequence[str] = (),
) -> ResolvedOpportunity:
    return ResolvedOpportunity(
        opportunity_key=key,
        opportunity_class=opp_class,
        action_kind=action_kind,
        source_kind=source_kind,
        source_ref=source_ref,
        soft_candidate=soft,
        time_feasible=time_ok,
        location_feasible=loc_ok,
        physical_feasible=phys_ok,
        domain_guard_satisfied=domain_ok,
        local_preference_permille=pref,
        rule_ids=tuple(sorted(rule_ids)),
    )


def active_ctx(
    *,
    activity_instance_id: str = "act:1",
    action_kind: str = "STUDY",
    actual_start: str = "2026-03-15T11:57:00+09:00",
    interruptibility: str = "REASSESSABLE",
    current_priority_tier: str = "ROUTINE_HABIT",
    continuation_feasible: bool = True,
    pref: int | None = 500,
    source_kind: str = "TASK",
    source_ref: str = "task:study-1",
    rule_ids: Sequence[str] = ("active.study",),
) -> ActiveDecisionContext:
    return ActiveDecisionContext(
        activity_instance_id=activity_instance_id,
        action_kind=action_kind,
        actual_start=actual_start,
        interruptibility=interruptibility,
        current_priority_tier=current_priority_tier,
        continuation_feasible=continuation_feasible,
        local_preference_permille=pref,
        source_kind=source_kind,
        source_ref=source_ref,
        rule_ids=tuple(sorted(rule_ids)),
    )


def soft_guard(
    *,
    candidate_key: str,
    decided_at: str = "2026-03-15T11:45:00+09:00",
    prior_outcome: str = "DECLINED",
    reason_class: str = "CAPACITY",
    meaningful_input_changed: bool = False,
) -> SoftDecisionGuard:
    return SoftDecisionGuard(
        candidate_key=candidate_key,
        decided_at=decided_at,
        prior_outcome=prior_outcome,
        reason_class=reason_class,
        meaningful_input_changed=meaningful_input_changed,
    )


def resolve(
    policy: Mapping[str, Any],
    *,
    human_state: Mapping[str, Any] | None = None,
    opportunities: Sequence[Any] = (),
    active: Any = None,
    soft_guards: Sequence[Any] = (),
    boundary_fact: DecisionBoundaryFact | None = None,
    now: str = NOW,
    world_seed: str = WORLD_SEED,
    character_id: str = CHARACTER_ID,
    **kwargs: Any,
):
    # Caller must supply biological feasibility explicitly when human_state is present.
    if human_state is not None:
        kwargs.setdefault("meal_feasible", True)
        kwargs.setdefault("rest_feasible", True)
    return resolve_action_decision(
        world_seed=world_seed,
        character_id=character_id,
        policy=policy,
        boundary=boundary_fact or boundary(),
        now=now,
        human_state=human_state,
        active=active,
        opportunities=opportunities,
        soft_guards=soft_guards,
        **kwargs,
    )


def integrate_oneshot_vs_split(
    policy: Mapping[str, Any],
    *,
    start_hs: Mapping[str, int],
    total_seconds: int,
    parts: Sequence[int],
    context: Mapping[str, str] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    ctx = context or {"base_activity_class": "REST_AWAKE", "social_exposure": "NONE"}
    rem0 = empty_remainders()
    hs_a, _ = integrate_v2_interval(dict(start_hs), dict(rem0), policy, ctx, total_seconds)
    hs_b = dict(start_hs)
    rem_b = dict(rem0)
    assert sum(parts) == total_seconds
    for part in parts:
        hs_b, rem_b = integrate_v2_interval(hs_b, rem_b, policy, ctx, part)
    return hs_a, hs_b


def hard_opp(key: str = "hard:1", pref: int | None = None) -> ResolvedOpportunity:
    return opportunity(
        key=key,
        opp_class="HARD_COMMITMENT",
        action_kind="HARD_COMMITMENT_ACTIVITY",
        source_kind="COMMITMENT",
        source_ref="commit:1",
        pref=pref,
    )


def deadline_opp(key: str = "deadline:1", pref: int | None = None) -> ResolvedOpportunity:
    return opportunity(
        key=key,
        opp_class="DEADLINE_TASK",
        action_kind="STUDY",
        source_kind="TASK",
        source_ref="task:1",
        pref=pref,
    )


def social_opp(key: str = "social:1", pref: int | None = None) -> ResolvedOpportunity:
    return opportunity(
        key=key,
        opp_class="SOCIAL_PROMISE",
        action_kind="SOCIAL_PROMISE",
        source_kind="SOCIAL",
        source_ref="social:1",
        pref=pref,
    )


def routine_opp(key: str = "routine:1", pref: int | None = None) -> ResolvedOpportunity:
    return opportunity(
        key=key,
        opp_class="ROUTINE_HABIT",
        action_kind="HOUSEHOLD",
        source_kind="TASK",
        source_ref="task:hh",
        pref=pref,
    )


def restorative_opp(key: str = "restorative:1", pref: int | None = None) -> ResolvedOpportunity:
    return opportunity(
        key=key,
        opp_class="RESTORATIVE",
        action_kind="REST",
        source_kind="ROUTINE",
        source_ref="routine:nap-slot",
        pref=pref,
    )


def leisure_opp(key: str = "leisure:1", pref: int | None = None) -> ResolvedOpportunity:
    return opportunity(
        key=key,
        opp_class="LEISURE_SPONTANEOUS",
        action_kind="MUSIC",
        source_kind="LEISURE_WINDOW",
        source_ref="fw:1",
        soft=True,
        pref=pref,
    )
