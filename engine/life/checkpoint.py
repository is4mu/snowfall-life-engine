"""Current state / checkpoint construction and validation."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from . import ENGINE_VERSION
from .canonical import normalize_persisted_object
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .fixed_point import empty_rate_remainders, validate_human_state, validate_rate_remainders
from .history import EMPTY_HISTORY_HASH
from .invariants import assert_active_not_already_finalized, assert_unique_condition_ids, validate_pending_queue
from .schema import reject_binary_floats, validate_instance
from .timeutil import parse_rfc3339
from .versioning import ensure_supported_engine, migrate_state


def empty_checkpoint(
    *,
    character_id: str,
    life_epoch: str,
    world_seed: str,
    behavior_policy_version: str,
    engine_commit_sha: str,
    location_id: str,
    human_state: dict[str, int],
    circadian_profile_ref: str | None = "fixture:circadian-synthetic",
) -> dict[str, Any]:
    from .timeutil import canonicalize_timestamp

    epoch = canonicalize_timestamp(life_epoch, field="life_epoch")
    state = {
        "schema_version": 1,
        "state_revision": 0,
        "engine_version": ENGINE_VERSION,
        "engine_commit_sha": engine_commit_sha,
        "behavior_policy_version": behavior_policy_version,
        "character_id": character_id,
        "timezone": "Asia/Tokyo",
        "life_epoch": epoch,
        "processed_through": epoch,
        "world_seed": world_seed,
        "context": {
            "location_id": location_id,
            "active_activity": None,
        },
        "human_state": validate_human_state(human_state),
        "sleep_context": {
            "last_wake_at": None,
            "last_main_sleep_end": None,
            "circadian_profile_ref": circadian_profile_ref,
        },
        "conditions": [],
        "appearance": {
            "outfit_state_id": None,
            "makeup_level": None,
            "hair_state_id": None,
        },
        "domain_refs": {
            "home_revision": None,
            "social_graph_revision": None,
            "finance_revision": None,
            "wardrobe_revision": None,
            "relation_state_revision": None,
            # Slice 5A: ConsumablesState binding. null until a RuntimeBundle is assembled.
            # Schema version remains 1 (pre-life contract completion; actual life unstarted).
            "consumables_revision": None,
            "schedule_revision": None,
            "schedule_hash": None,
            # Slice 5C1: SocialResponseState binding. null until a persistent 5C snapshot.
            "social_response_revision": None,
        },
        "integration": {
            "schema_version": 1,
            "rate_remainders": empty_rate_remainders(),
        },
        "pending_queue": {
            "cursor": 0,
            "events": [],
        },
        "history": {
            "head_event_id": None,
            "history_hash": EMPTY_HISTORY_HASH,
            "event_count": 0,
        },
    }
    validate_checkpoint(state)
    return state


def validate_checkpoint(
    state: dict[str, Any],
    *,
    finalized_instance_ids: set[str] | None = None,
) -> dict[str, Any]:
    reject_binary_floats(state)
    normalized = normalize_persisted_object(state)
    state.clear()
    state.update(normalized)
    validate_instance(state, "current_state")
    ensure_supported_engine(state["engine_version"])
    processed = parse_rfc3339(state["processed_through"], field="processed_through")
    epoch = parse_rfc3339(state["life_epoch"], field="life_epoch")
    if processed < epoch:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "processed_through < life_epoch")
    validate_human_state(state["human_state"])
    validate_rate_remainders(state["integration"]["rate_remainders"])
    assert_unique_condition_ids(state.get("conditions") or [])
    active = state["context"].get("active_activity")
    if active is not None:
        validate_active_activity(active)
        if finalized_instance_ids is not None:
            assert_active_not_already_finalized(active, finalized_instance_ids)
    validate_pending_queue(state)
    return state


def load_checkpoint(path: Path | str, *, finalized_instance_ids: set[str] | None = None) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data = migrate_state(data)
    return validate_checkpoint(data, finalized_instance_ids=finalized_instance_ids)


def write_checkpoint(path: Path | str, state: dict[str, Any]) -> None:
    validate_checkpoint(state)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def bump_revision(state: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(state)
    out["state_revision"] = int(out["state_revision"]) + 1
    return out
