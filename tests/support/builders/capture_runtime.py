"""Shared synthetic runtime-capture builders for public tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from engine.life.capture_emitters import (
    EMITTER_RULE_TO_CAPTURE_KIND,
    build_capture_source_moment,
)
from engine.life.events import validate_active_activity
from engine.life.fixed_point import empty_rate_remainders
from engine.life.policy import load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.runtime_capture import RuntimeCaptureContextFacts
from engine.life.schedule_state import build_schedule_state
from engine.life.timeutil import add_minutes, format_rfc3339, parse_rfc3339
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.builders.runtime_bundle import (
    CHAR,
    ENGINE_SHA,
    PERSON,
    WARDROBE_ITEM,
    build_synthetic_bundle_root,
    reference_sets as _refs,
)
from tests.support.builders.schedule import _commitment, _task
from tests.support.builders.runtime_decision import AS_OF, HOME
from tests.support.builders.activity_materialization import DECISION_KEY
from tests.support.builders.finalization import (
    _finish_ctx,
    _planned_end_active,
    _runtime_active,
)

POLICY = load_policy(POLICY_V2_CANDIDATE_PATH)
POLICY_VERSION = POLICY["behavior_policy_version"]
CHAR_SOURCE_SHA = "public-character-source-sha"


def _add_min(ts: str, minutes: int) -> str:
    return format_rfc3339(add_minutes(parse_rfc3339(ts, field="ts"), minutes))


def _force_take_policy() -> dict:
    policy = copy.deepcopy(POLICY)
    for kind in policy["capture_policy"]["activation_permille_by_kind"]:
        policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
    return policy


def _force_skip_policy() -> dict:
    policy = copy.deepcopy(POLICY)
    for kind in policy["capture_policy"]["activation_permille_by_kind"]:
        policy["capture_policy"]["activation_permille_by_kind"][kind] = 0
    return policy


def _visual_context(bundle, **overrides) -> dict:
    base = {
        "character_id": CHAR,
        "character_source_sha": CHAR_SOURCE_SHA,
        "engine_commit_sha": bundle.current_state["engine_commit_sha"],
        "behavior_policy_version": POLICY_VERSION,
        "location_id": bundle.current_state["context"]["location_id"],
        "appearance": {
            "outfit_state_id": bundle.current_state["appearance"]["outfit_state_id"],
            "outfit_item_ids": [WARDROBE_ITEM],
            "makeup_level": bundle.current_state["appearance"]["makeup_level"],
            "hair_state_id": bundle.current_state["appearance"]["hair_state_id"],
        },
        "home_revision": bundle.home_state["revision"],
        "wardrobe_revision": bundle.wardrobe_state["revision"],
        "photographer_ref": None,
        "companion_refs": [],
        "lighting_context": "NATURAL",
    }
    appearance_over = overrides.pop("appearance", None)
    base.update(overrides)
    if appearance_over is not None:
        app = dict(base["appearance"])
        app.update(appearance_over)
        base["appearance"] = app
    return base


def _moment(
    bundle,
    *,
    emitter_rule_id: str = "slice4b2.daily.detail",
    source_context_kind: str = "HOME_DETAIL",
    semantic_source_ref: str = "semantic:home-detail:1",
    moment_ref: str = "moment:1",
    moment_at: str | None = None,
    subject_refs: list | None = None,
    visual_context: dict | None = None,
    input_state_refs: list | None = None,
    activity_instance_id: str | None = None,
) -> dict:
    active = bundle.current_state["context"]["active_activity"]
    vc = visual_context if visual_context is not None else _visual_context(bundle)
    kind = EMITTER_RULE_TO_CAPTURE_KIND[emitter_rule_id]
    refs = [] if subject_refs is None else list(subject_refs)
    if kind == "SELFIE":
        vc = dict(vc)
        vc["photographer_ref"] = CHAR
        if CHAR not in refs:
            refs = [CHAR] + refs
    if kind == "PEOPLE":
        if not refs:
            refs = [PERSON]
        vc = dict(vc)
        companions = [r for r in refs if r != CHAR]
        vc["companion_refs"] = sorted(set(vc.get("companion_refs") or []) | set(companions))
        if vc.get("photographer_ref") is None:
            vc["photographer_ref"] = CHAR
    return build_capture_source_moment(
        emitter_rule_id=emitter_rule_id,
        source_context_kind=source_context_kind,
        activity_instance_id=(
            activity_instance_id
            if activity_instance_id is not None
            else active["activity_instance_id"]
        ),
        semantic_source_ref=semantic_source_ref,
        moment_ref=moment_ref,
        moment_at=(
            moment_at
            if moment_at is not None
            else bundle.current_state["processed_through"]
        ),
        subject_refs=refs,
        visual_context=vc,
        input_state_refs=(
            ["state:src-b", "state:src-a"]
            if input_state_refs is None
            else input_state_refs
        ),
    )


def _facts(bundle, moments: list | None = None) -> RuntimeCaptureContextFacts:
    return RuntimeCaptureContextFacts(
        character_source_sha=CHAR_SOURCE_SHA,
        source_moments=tuple(moments if moments is not None else []),
    )


def _load_bundle(
    tmp: str,
    *,
    active: dict[str, Any] | None = None,
    queue_events: list | None = None,
    human_state: dict[str, int] | None = None,
    processed_through: str | None = None,
    commitments: list | None = None,
    tasks: list | None = None,
    location_id: str = HOME,
    companions: list | None = None,
    appearance: dict | None = None,
    mutate_current=None,
    persons: frozenset[str] | None = None,
    wardrobe: frozenset[str] | None = None,
) -> Any:
    act = active if active is not None else _runtime_active()
    if companions is not None:
        act = validate_active_activity({**act, "companions": companions})
        assert act is not None
    hs = human_state or {
        "sleep_debt_min": 0,
        "hunger": 200,
        "physical_fatigue": 100,
        "affect_valence": 0,
        "stress": 100,
        "social_battery": 800,
    }
    events = queue_events if queue_events is not None else []
    from engine.life.invariants import queue_event_sort_key

    events = sorted((dict(e) for e in events), key=queue_event_sort_key)
    processed = processed_through if processed_through is not None else AS_OF
    task_list = tasks if tasks is not None else [_task()]
    cmt_list = commitments if commitments is not None else []
    app = appearance if appearance is not None else {
        "outfit_state_id": None,
        "makeup_level": None,
        "hair_state_id": None,
    }

    def _mut(c: dict) -> dict:
        out = {
            **c,
            "behavior_policy_version": POLICY_VERSION,
            "processed_through": processed,
            "state_revision": 11,
            "human_state": hs,
            "appearance": app,
            "integration": {
                "schema_version": 1,
                "rate_remainders": empty_rate_remainders(),
            },
            "pending_queue": {"cursor": 0, "events": events},
            "context": {
                **c["context"],
                "active_activity": act,
                "location_id": location_id,
            },
        }
        if mutate_current is not None:
            out = mutate_current(out)
        return out

    root = build_synthetic_bundle_root(
        Path(tmp),
        mutate_current=_mut,
        mutate_schedule=lambda s: build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            commitments=cmt_list,
            obligation_tasks=task_list,
        ),
    )
    return load_runtime_bundle(
        root,
        reference_sets=_refs(persons=persons, wardrobe=wardrobe),
    )


def _bundle_snapshot(bundle) -> dict[str, Any]:
    return {
        "current_state": copy.deepcopy(dict(bundle.current_state)),
        "schedule_state": copy.deepcopy(dict(bundle.schedule_state)),
        "relation_state": copy.deepcopy(dict(bundle.relation_state)),
        "home_state": copy.deepcopy(dict(bundle.home_state)),
        "consumables_state": copy.deepcopy(dict(bundle.consumables_state)),
        "wardrobe_state": copy.deepcopy(dict(bundle.wardrobe_state)),
        "finance_state": copy.deepcopy(dict(bundle.finance_state)),
    }


def _pending(bundle) -> list:
    active = bundle.current_state["context"]["active_activity"]
    return list(active.get("pending_captures") or [])
