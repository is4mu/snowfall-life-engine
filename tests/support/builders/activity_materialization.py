"""Shared synthetic builders for activity materialization tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_materialization import (
    ACTION_KIND_TO_ACTIVITY_TYPE,
    HARD_COMMITMENT_KIND_TO_ACTIVITY,
    SOCIAL_CONTACT_MODES,
    DynamicsContext,
    RuntimeActivityMaterializationFacts,
    build_activity_end_event,
    materialize_activity_plan,
    parse_dynamics_context,
    parse_runtime_activity_materialization_facts,
    start_runtime_activity_from_selection,
    validate_dynamics_for_activity_type,
)
from engine.life.activity_runtime import derive_activity_instance_id, start_activity_from_selection
from engine.life.canonical import canonical_json
from engine.life.decisions import ActionCandidate, DecisionResolution, candidate_id_for
from engine.life.derived import default_synthetic_sleep_profile
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.events import build_actual_event_from_activity, validate_active_activity
from engine.life.ids import stable_id
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.runtime_decision import (
    RuntimeDecisionFrame,
    build_decision_facts_hash,
    build_finalization_context_hash,
    build_materialization_context_hash,
    parse_runtime_decision_facts,
)
from engine.life.schedule_state import build_schedule_state
from engine.life.social_response import SocialResponseDecision
from engine.life.social_runtime import (
    PendingImmediateAccept,
    SuccessfulImmediateSocialStart,
    apply_pending_immediate_accept_after_successful_start,
    build_selected_social_response_hash,
    build_social_response_state,
)
from engine.life.timeutil import add_minutes, format_rfc3339, parse_rfc3339
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.builders.runtime_bundle import (
    CHAR,
    ENGINE_SHA,
    build_synthetic_bundle_root,
    reference_sets as _refs,
)
from tests.support.builders.schedule import commitment as _commitment, task as _task
from tests.support.builders.runtime_decision import AS_OF, HOME, CAMPUS, _facts, _route

POLICY = load_policy(POLICY_V2_CANDIDATE_PATH)
POLICY_VERSION = POLICY["behavior_policy_version"]
DECISION_KEY = "runtime-decision-fixture-1"
SLEEP_PROF = default_synthetic_sleep_profile()._asdict()


def _add_min(ts: str, minutes: int) -> str:
    return format_rfc3339(add_minutes(parse_rfc3339(ts, field="ts"), minutes))


def _candidate(**overrides: Any) -> ActionCandidate:
    candidate_key = overrides.pop("candidate_key", "cand-key-study")
    decision_key = overrides.pop("decision_key_for_id", DECISION_KEY)
    base = dict(
        candidate_key=candidate_key,
        candidate_id=candidate_id_for(
            character_id=CHAR,
            decision_key=decision_key,
            candidate_key=candidate_key,
        ),
        action_kind="STUDY",
        priority_tier="DEADLINE_TASK",
        source_kind="TASK",
        source_ref="task-a",
        soft_candidate=False,
        time_feasible=True,
        location_feasible=True,
        physical_feasible=True,
        domain_guard_satisfied=True,
        local_preference_permille=400,
        activity_instance_id=None,
        rule_ids=("rule.study",),
    )
    base.update(overrides)
    return ActionCandidate(**base)


def _resolution(cand: ActionCandidate, **overrides: Any) -> DecisionResolution:
    base = dict(
        result_kind="SELECT_CANDIDATE",
        decision_key=DECISION_KEY,
        selected_candidate_id=cand.candidate_id,
        selected_candidate_key=cand.candidate_key,
        selected_action_kind=cand.action_kind,
        selected_priority_tier=cand.priority_tier,
        activity_instance_id=None,
        considered_candidate_ids=(cand.candidate_id,),
        rejected=(),
        rule_ids=("rule.resolver",),
        random_key="b" * 64,
        selection_mode="single",
    )
    base.update(overrides)
    return DecisionResolution(**base)


def _mat_facts(cand: ActionCandidate, **overrides: Any) -> dict[str, Any]:
    base = {
        "selected_candidate_id": cand.candidate_id,
        "base_activity_class": "SEDENTARY_FOCUSED",
        "social_exposure": "NONE",
        "meal_source": None,
        "meal_extent": None,
        "social_contact_mode": None,
        "sleep_profile": None,
    }
    base.update(overrides)
    return base


def _frame(
    cand: ActionCandidate,
    *,
    decision_facts: Mapping[str, Any] | None = None,
    bundle=None,
    behavior_policy: Mapping[str, Any] | None = None,
    reference_sets=None,
    **overrides: Any,
) -> RuntimeDecisionFrame:
    res = _resolution(cand)
    from engine.life.domain_adapters import AdapterResult
    from engine.life.routine_adapters import RoutineAdapterResult
    from engine.life.runtime_decision import RuntimeDecisionTrigger

    trigger = RuntimeDecisionTrigger(
        trigger_id="trig-1",
        occurred_at=AS_OF,
        trigger_kind="IDLE",
        semantic_decision_key="idle:fixture",
        reason_code="IDLE_REASSESSMENT",
        metric=None,
        target_band=None,
        direction=None,
        source_kind=None,
        source_ref=None,
    )
    empty_struct = AdapterResult(
        opportunities=(), decision_boundaries=(), diagnostics=()
    )
    empty_routine = RoutineAdapterResult(
        opportunities=(), decision_boundaries=(), diagnostics=()
    )
    facts_raw = decision_facts if decision_facts is not None else _facts()
    facts_hash = build_decision_facts_hash(
        parse_runtime_decision_facts(facts_raw, now=AS_OF)
    )
    policy = behavior_policy if behavior_policy is not None else POLICY
    refs = reference_sets if reference_sets is not None else _refs()
    ctx_hash = overrides.pop("materialization_context_hash", None)
    if ctx_hash is None and bundle is not None:
        ctx_hash = build_materialization_context_hash(
            current_state=bundle.current_state,
            schedule_state=bundle.schedule_state,
            behavior_policy=policy,
            decision_facts_hash=facts_hash,
        )
    elif ctx_hash is None:
        ctx_hash = "c" * 64
    fin_hash = overrides.pop("finalization_context_hash", None)
    if fin_hash is None and bundle is not None:
        fin_hash = build_finalization_context_hash(
            bundle=bundle,
            reference_sets=refs,
            behavior_policy=policy,
            decision_facts_hash=facts_hash,
        )
    elif fin_hash is None:
        fin_hash = "e" * 64
    base = dict(
        runtime_decision_key=DECISION_KEY,
        trigger=trigger,
        decision_snapshot_hash="a" * 64,
        decision_facts_hash=facts_hash,
        materialization_context_hash=ctx_hash,
        finalization_context_hash=fin_hash,
        input_state_refs=("trigger:t1", "decision-snapshot:a"),
        structural_result=empty_struct,
        routine_result=empty_routine,
        merged_opportunities=(),
        merged_known_boundaries=(),
        raw_candidates=(cand,),
        resolution=res,
        selected_candidate=cand,
    )
    base.update(overrides)
    return RuntimeDecisionFrame(**base)


def _schedule(*, commitments=(), tasks=()):
    return build_schedule_state(
        character_id=CHAR,
        as_of=AS_OF,
        commitments=list(commitments),
        obligation_tasks=list(tasks),
    )


def _plan(
    cand: ActionCandidate,
    *,
    schedule=None,
    facts=None,
    mat=None,
    social_responses=(),
    pending_immediate_accept=None,
    now=AS_OF,
    location=HOME,
):
    return materialize_activity_plan(
        selected_candidate=cand,
        decision_facts=facts if facts is not None else _facts(),
        materialization_facts=mat if mat is not None else _mat_facts(cand),
        schedule_state=schedule if schedule is not None else _schedule(),
        behavior_policy=POLICY,
        now=now,
        current_location_id=location,
        social_responses=social_responses,
        pending_immediate_accept=pending_immediate_accept,
    )


def _social_decision(**overrides: Any) -> SocialResponseDecision:
    opp_id = overrides.pop("opportunity_id", "opp-contact-1")
    base = dict(
        response_id=stable_id("social-response", opp_id, "ACCEPT"),
        opportunity_id=opp_id,
        person_id="person-a",
        opportunity_kind="CONTACT_OPPORTUNITY",
        response="ACCEPT",
        reason_code="CONTACT_READY",
        contact_mode="CALL",
        duration_min=15,
        duration_max=45,
        resolved_opportunity_key=stable_id("social-resolved-opportunity", opp_id),
        commitment_proposal_id=None,
        rule_ids=("slice2h.contact.accept",),
    )
    base.update(overrides)
    return SocialResponseDecision(**base)



def _pending_from_decision(
    decision: SocialResponseDecision,
    cand: ActionCandidate,
    *,
    decided_at: str = AS_OF,
    **overrides: Any,
) -> PendingImmediateAccept:
    base = dict(
        opportunity_id=decision.opportunity_id,
        response_id=decision.response_id,
        decided_at=decided_at,
        reason_code=decision.reason_code,
        selected_opportunity_key=decision.resolved_opportunity_key or "",
        selected_candidate_id=cand.candidate_id,
        selected_candidate_key=cand.candidate_key,
        decision_key=DECISION_KEY,
        character_id=CHAR,
        person_id=decision.person_id,
        opportunity_kind=decision.opportunity_kind,
        contact_mode=decision.contact_mode or "CALL",
        duration_min=decision.duration_min,
        duration_max=decision.duration_max,
        selected_response_hash=build_selected_social_response_hash(decision),
    )
    base.update(overrides)
    return PendingImmediateAccept(**base)

def _social_candidate(decision: SocialResponseDecision, **overrides: Any) -> ActionCandidate:
    key = decision.resolved_opportunity_key or stable_id(
        "social-resolved-opportunity", decision.opportunity_id
    )
    base = dict(
        candidate_key=key,
        action_kind="SOCIAL_CONTACT",
        priority_tier="SOCIAL_PROMISE",
        source_kind="SOCIAL",
        source_ref=decision.opportunity_id,
        soft_candidate=True,
        rule_ids=("rule.social",),
    )
    base.update(overrides)
    return _candidate(**base)


