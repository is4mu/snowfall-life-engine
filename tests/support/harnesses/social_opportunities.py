"""Synthetic helpers for social opportunity tests (no I/O)."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from engine.life.policy import load_policy
from engine.life.social_opportunities import (
    SocialGenerationContext,
    SocialGenerationResult,
    generate_social_opportunities,
)

from tests.support.constants import POLICY_V2_CANDIDATE_PATH

CHARACTER_ID = "fixture-character"
WORLD_SEED = "slice2g-world-seed"
WEEK_START = "2026-03-09"  # Monday
AS_OF = "2026-03-15T23:59:59+09:00"

MIZUKI = "fixture-remote-friend"
SAYAKA = "fixture-local-friend"
RENA = "fixture-work-contact"

KNOWN = (MIZUKI, SAYAKA, RENA)


def policy() -> dict[str, Any]:
    return load_policy(POLICY_V2_CANDIDATE_PATH)


def _prov(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"origin": "ENGINE_DEFAULT", "notes": "synthetic-slice2g"}
    base.update(overrides)
    return base


def assignment(person_id: str, archetype_id: str, **overrides: Any) -> dict[str, Any]:
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
        assignment(MIZUKI, "REMOTE_CLOSE_BURSTY"),
        assignment(SAYAKA, "LOCAL_CLOSE_INVITER"),
        assignment(RENA, "WORK_CONTEXTUAL"),
    ]


def ctx(
    *,
    character_id: str = CHARACTER_ID,
    world_seed: Any = WORLD_SEED,
    week_start_date: str = WEEK_START,
    as_of: str = AS_OF,
) -> SocialGenerationContext:
    return SocialGenerationContext(
        character_id=character_id,
        world_seed=world_seed,
        week_start_date=week_start_date,
        as_of=as_of,
    )


def contact(
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


def post_work(
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


def generate(
    *,
    context: SocialGenerationContext | Mapping[str, Any] | None = None,
    known_person_ids: Sequence[str] = KNOWN,
    assignments: Sequence[Mapping[str, Any]] | None = None,
    contacts: Sequence[Mapping[str, Any]] = (),
    post_work_contexts: Sequence[Mapping[str, Any]] = (),
    behavior_policy: Mapping[str, Any] | None = None,
) -> SocialGenerationResult:
    return generate_social_opportunities(
        context=context or ctx(),
        behavior_policy=behavior_policy or policy(),
        known_person_ids=known_person_ids,
        assignments=list(assignments if assignments is not None else default_assignments()),
        contacts=contacts,
        post_work_contexts=post_work_contexts,
    )


def slot_kinds_for(result: SocialGenerationResult, person_id: str) -> list[str]:
    return [s.slot_kind for s in result.slots if s.person_id == person_id]


def slots_for(result: SocialGenerationResult, person_id: str, slot_kind: str):
    return [
        s
        for s in result.slots
        if s.person_id == person_id and s.slot_kind == slot_kind
    ]


def opp_kinds(result: SocialGenerationResult) -> list[str]:
    return [o.opportunity_kind for o in result.opportunities]


def diag_codes(result: SocialGenerationResult, *, source_ref: str | None = None) -> list[str]:
    out = []
    for d in result.diagnostics:
        if source_ref is None or d.source_ref == source_ref:
            out.append(d.code)
    return out


def deep_copy_policy() -> dict[str, Any]:
    return copy.deepcopy(policy())
