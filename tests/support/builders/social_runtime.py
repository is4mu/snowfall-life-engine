"""Shared synthetic builders for runtime social composition tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_runtime import ActivityStartSpec, start_activity_from_selection
from engine.life.policy import load_policy
from engine.life.runtime_bundle import RuntimeReferenceSets, load_runtime_bundle
from engine.life.runtime_decision import runtime_trigger_from_v2_wakeup
from engine.life.social_runtime import (
    SuccessfulImmediateSocialStart,
    build_runtime_social_decision,
    build_social_response_state,
)
from engine.life.world_state import project_initial_relation_state
from tests.support.builders.runtime_bundle import (
    AS_OF,
    CHAR,
    PERSON,
    build_synthetic_bundle_root,
    provenance as _prov,
    reference_sets as _refs,
)
from tests.support.builders.runtime_decision import (
    _facts,
    _structural_phys,
    _v2_wakeup,
)
from tests.support.builders.schedule import commitment as _commitment
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.harnesses.social_opportunities import (
    KNOWN as SOCIAL_KNOWN,
    MIZUKI,
    RENA,
    SAYAKA,
    contact,
    default_assignments,
)
from tests.support.harnesses.social_response import opportunity as make_opp

POLICY = load_policy(POLICY_V2_CANDIDATE_PATH)
POLICY_VERSION = POLICY["behavior_policy_version"]
HORIZON = POLICY["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"][
    "future_horizon_max_days"
]

SEED_CONTACT = "public-social-contact-27"
SEED_PRIOR_WEEK_INVITE = "public-social-invite-1"
SEED_SAME_DAY_INVITE = "public-social-invite-42"
SEED_BOTH = "public-social-both-38"
SEED_POST_WORK = "public-social-post-work-21"
SEED_MULTI_INVITE = "public-social-multi-53"


def _successful_start_from_result(result) -> SuccessfulImmediateSocialStart:
    """Materialize authoritative ActiveActivity via 5B1 start bridge (test-only)."""
    pending = result.pending_immediate_accept
    assert pending is not None
    selected = result.frame.selected_candidate
    assert selected is not None
    resolution = result.frame.resolution
    state = result.working_bundle.current_state
    started = start_activity_from_selection(
        current_state=state,
        policy_version=POLICY_VERSION,
        resolution=resolution,
        selected_candidate=selected,
        start_spec=ActivityStartSpec(
            activity_type="SOCIAL_CONTACT",
            actual_start=state["processed_through"],
            interruptibility="REASSESSABLE",
            planned_end=None,
            expected_end=None,
            expected_end_window=None,
            location_id=state["context"].get("location_id"),
            companions=(),
            commitment_id=None,
            details={"contact_mode": "CALL"},
            effects_on_end=(),
        ),
        input_state_refs=("state:current", "state:schedule"),
    )
    return SuccessfulImmediateSocialStart(
        active_activity=started["context"]["active_activity"]
    )


def _social_refs() -> RuntimeReferenceSets:
    return _refs(persons=frozenset({PERSON, *SOCIAL_KNOWN}))


def _relation_with_friends() -> dict:
    return _relation_with_friends_at(AS_OF)


def _run_social(
    tmp: Path,
    *,
    world_seed: str = SEED_CONTACT,
    social_state: Any = None,
    social_facts: Mapping[str, Any] | None = None,
    facts: Mapping[str, Any] | None = None,
    mutate_schedule: Any = None,
    human_state: Mapping[str, int] | None = None,
    event: dict[str, Any] | None = None,
    as_of: str = AS_OF,
):
    ev = event if event is not None else _v2_wakeup(due_at=as_of)
    bundle = _load_social_bundle(
        tmp,
        events=[ev],
        world_seed=world_seed,
        mutate_schedule=mutate_schedule,
        human_state=human_state,
        as_of=as_of,
    )
    trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, ev)
    state = social_state
    if state is None:
        state = build_social_response_state(
            character_id=CHAR,
            as_of=as_of,
        )
    return build_runtime_social_decision(
        bundle=bundle,
        reference_sets=_social_refs(),
        behavior_policy=POLICY,
        trigger=trigger,
        facts=facts if facts is not None else _decision_facts(),
        social_response_state=state,
        social_facts=social_facts if social_facts is not None else _social_facts(),
    ), bundle, trigger, ev


def _load_social_bundle(
    tmp: Path,
    *,
    events: list[dict[str, Any]] | None = None,
    world_seed: str = SEED_CONTACT,
    mutate_schedule: Any = None,
    human_state: Mapping[str, int] | None = None,
    as_of: str = AS_OF,
):
    event_list = list(events or [])

    def _mutate(current: dict) -> dict:
        current = dict(current)
        current["behavior_policy_version"] = POLICY_VERSION
        current["world_seed"] = world_seed
        if human_state is not None:
            current["human_state"] = dict(human_state)
        current["pending_queue"] = {"cursor": 0, "events": list(event_list)}
        return current

    root = build_synthetic_bundle_root(
        tmp,
        as_of=as_of,
        processed_through=as_of,
        mutate_current=_mutate,
        mutate_schedule=mutate_schedule,
        world_overrides={"relation": _relation_with_friends_at(as_of)},
    )
    return load_runtime_bundle(root, reference_sets=_social_refs())


def _relation_with_friends_at(as_of: str) -> dict:
    return project_initial_relation_state(
        character_id=CHAR,
        as_of=as_of,
        provenance=_prov(notes="social-runtime-synthetic"),
        known_person_ids=_social_refs().known_person_ids,
        people=[
            {"person_id": pid, "open_promises": []}
            for pid in sorted({PERSON, *SOCIAL_KNOWN})
        ],
    )


def _social_facts(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "archetype_assignments": default_assignments(),
        "contact_facts": [
            contact(
                actual_end="2026-03-20T12:00:00+09:00",
                finalized_at="2026-03-20T12:05:00+09:00",
            )
        ],
        "post_work_contexts": [],
        "availability_windows": [],
        "hard_commitment_blocking_now": False,
        "social_contact_physical_feasible": True,
        "post_work_location_feasible": True,
    }
    base.update(overrides)
    return base


def _decision_facts(**overrides: Any) -> dict[str, Any]:
    base = _facts(
        sleep_pressure=200,
        sleep_opportunity="NORMAL",
        meal_feasible=False,
        rest_feasible=False,
    )
    base.update(overrides)
    return base


