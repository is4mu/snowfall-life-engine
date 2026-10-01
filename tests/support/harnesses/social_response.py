"""Synthetic helpers for social response tests (no I/O)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from engine.life.policy import load_policy
from engine.life.social_opportunities import SocialExogenousOpportunity
from engine.life.social_response import (
    SocialAvailabilityWindowFact,
    PriorSocialResponseFact,
    SocialResponseAdapterResult,
    SocialResponseContext,
    adapt_social_responses,
)

from tests.support.constants import POLICY_V2_CANDIDATE_PATH

MIZUKI = "fixture-remote-friend"
SAYAKA = "fixture-local-friend"
RENA = "fixture-work-contact"
KNOWN = (MIZUKI, SAYAKA, RENA)

NOW = "2026-03-15T19:00:00+09:00"


def policy() -> dict[str, Any]:
    return load_policy(POLICY_V2_CANDIDATE_PATH)


def human_state(**overrides: int) -> dict[str, int]:
    base = {
        "sleep_debt_min": 0,
        "hunger": 400,
        "physical_fatigue": 300,
        "affect_valence": 0,
        "stress": 200,
        "social_battery": 500,
    }
    base.update(overrides)
    return base


def ctx(
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


def opportunity(
    *,
    opportunity_id: str,
    opportunity_kind: str,
    person_id: str,
    archetype_id: str,
    available_local_date: str = "2026-03-15",
    invite_timing_kind: str | None = None,
    target_local_date: str | None = None,
    source_kind: str = "WEEKLY_SLOT",
    source_ref: str | None = None,
) -> SocialExogenousOpportunity:
    return SocialExogenousOpportunity(
        opportunity_id=opportunity_id,
        opportunity_kind=opportunity_kind,
        person_id=person_id,
        archetype_id=archetype_id,
        source_kind=source_kind,
        source_ref=source_ref or f"src-{opportunity_id}",
        available_local_date=available_local_date,
        invite_timing_kind=invite_timing_kind,
        target_local_date=target_local_date,
        activation_random_key=f"act-{opportunity_id}",
        timing_random_key=("timing-" + opportunity_id) if invite_timing_kind else None,
        future_day_random_key=(
            ("future-" + opportunity_id) if invite_timing_kind == "FUTURE_SOFT" else None
        ),
        rule_ids=("slice2g.test",),
    )


def remote_contact(oid: str = "opp-remote-contact", **kw: Any) -> SocialExogenousOpportunity:
    return opportunity(
        opportunity_id=oid,
        opportunity_kind="CONTACT_OPPORTUNITY",
        person_id=MIZUKI,
        archetype_id="REMOTE_CLOSE_BURSTY",
        **kw,
    )


def local_contact(oid: str = "opp-local-contact", **kw: Any) -> SocialExogenousOpportunity:
    return opportunity(
        opportunity_id=oid,
        opportunity_kind="CONTACT_OPPORTUNITY",
        person_id=SAYAKA,
        archetype_id="LOCAL_CLOSE_INVITER",
        **kw,
    )


def post_work_opp(oid: str = "opp-post-work", **kw: Any) -> SocialExogenousOpportunity:
    defaults = {
        "available_local_date": "2026-03-15",
        "target_local_date": "2026-03-15",
        "source_kind": "POST_WORK_CONTEXT",
    }
    defaults.update(kw)
    return opportunity(
        opportunity_id=oid,
        opportunity_kind="POST_WORK_OPPORTUNITY",
        person_id=RENA,
        archetype_id="WORK_CONTEXTUAL",
        **defaults,
    )


def local_invite(
    oid: str = "opp-local-invite",
    *,
    timing: str = "SAME_DAY_SOFT",
    available_local_date: str = "2026-03-15",
    target_local_date: str | None = None,
    **kw: Any,
) -> SocialExogenousOpportunity:
    if target_local_date is None:
        target_local_date = (
            "2026-03-18" if timing == "FUTURE_SOFT" else available_local_date
        )
    return opportunity(
        opportunity_id=oid,
        opportunity_kind="INVITE_OPPORTUNITY",
        person_id=SAYAKA,
        archetype_id="LOCAL_CLOSE_INVITER",
        available_local_date=available_local_date,
        invite_timing_kind=timing,
        target_local_date=target_local_date,
        **kw,
    )


def availability(
    *,
    availability_id: str = "avail-1",
    opportunity_id: str,
    earliest_start: str,
    latest_end: str,
    location_id: str | None = None,
    hard_conflict: bool = False,
) -> SocialAvailabilityWindowFact:
    return SocialAvailabilityWindowFact(
        availability_id=availability_id,
        opportunity_id=opportunity_id,
        earliest_start=earliest_start,
        latest_end=latest_end,
        location_id=location_id,
        hard_conflict=hard_conflict,
    )


def prior(
    *,
    response_id: str,
    opportunity_id: str,
    response: str,
    decided_at: str = "2026-03-15T18:00:00+09:00",
    reason_code: str = "CONTACT_READY",
) -> PriorSocialResponseFact:
    return PriorSocialResponseFact(
        response_id=response_id,
        opportunity_id=opportunity_id,
        response=response,
        decided_at=decided_at,
        reason_code=reason_code,
    )


def adapt(
    *,
    context: SocialResponseContext | Mapping[str, Any] | None = None,
    opportunities: Sequence[SocialExogenousOpportunity | Mapping[str, Any]] = (),
    availability_windows: Sequence[Any] = (),
    prior_responses: Sequence[Any] = (),
    known_person_ids: Sequence[str] = KNOWN,
    behavior_policy: Mapping[str, Any] | None = None,
) -> SocialResponseAdapterResult:
    return adapt_social_responses(
        context=context or ctx(),
        behavior_policy=behavior_policy or policy(),
        known_person_ids=known_person_ids,
        opportunities=list(opportunities),
        availability_windows=list(availability_windows),
        prior_responses=list(prior_responses),
    )


def fingerprint(result: SocialResponseAdapterResult) -> tuple:
    return (
        tuple(r.as_dict() for r in result.responses),
        tuple(
            {
                "opportunity_key": o.opportunity_key,
                "opportunity_class": o.opportunity_class,
                "action_kind": o.action_kind,
                "source_kind": o.source_kind,
                "source_ref": o.source_ref,
                "soft_candidate": o.soft_candidate,
                "time_feasible": o.time_feasible,
                "location_feasible": o.location_feasible,
                "physical_feasible": o.physical_feasible,
                "domain_guard_satisfied": o.domain_guard_satisfied,
                "local_preference_permille": o.local_preference_permille,
                "rule_ids": list(o.rule_ids),
            }
            for o in result.opportunities
        ),
        tuple(p.as_dict() for p in result.commitment_proposals),
        tuple(d.as_dict() for d in result.diagnostics),
    )
