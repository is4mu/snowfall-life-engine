"""Synthetic RuntimeBundle filesystem builder for public tests."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from engine.life.checkpoint import empty_checkpoint
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.schedule_state import build_schedule_state
from engine.life.world_state import (
    project_initial_consumables_state,
    project_initial_finance_state,
    project_initial_home_state,
    project_initial_relation_state,
    project_initial_wardrobe_state,
)

AS_OF = "2026-04-01T12:00:00+09:00"
CHARACTER_ID = "fixture-character"
ENGINE_SHA = "a" * 40
PERSON_ID = "person-a"
HOME_ENTITY_ID = "entity-lamp"
WARDROBE_ITEM_ID = "item-tee"
CONSUMABLE_ID = "food-staple"

# Stable compatibility names used by runtime-test builders.
CHAR = CHARACTER_ID
PERSON = PERSON_ID
HOME_ENTITY = HOME_ENTITY_ID
WARDROBE_ITEM = WARDROBE_ITEM_ID
CONSUMABLE = CONSUMABLE_ID


def provenance(**overrides: Any) -> dict[str, Any]:
    base = {"origin": "SIMULATION_BOOTSTRAP", "notes": "public-synthetic-runtime-bundle"}
    base.update(overrides)
    return base


def reference_sets(
    *,
    persons: frozenset[str] | None = None,
    homes: frozenset[str] | None = None,
    wardrobe: frozenset[str] | None = None,
    consumables: frozenset[str] | None = None,
) -> RuntimeReferenceSets:
    return RuntimeReferenceSets(
        known_person_ids=persons if persons is not None else frozenset({PERSON_ID}),
        known_home_entity_ids=homes if homes is not None else frozenset({HOME_ENTITY_ID}),
        approved_wardrobe_item_ids=(
            wardrobe if wardrobe is not None else frozenset({WARDROBE_ITEM_ID})
        ),
        approved_consumable_ids=(
            consumables if consumables is not None else frozenset({CONSUMABLE_ID})
        ),
    )


def world_states(
    character_id: str = CHARACTER_ID,
    as_of: str = AS_OF,
) -> dict[str, dict[str, Any]]:
    refs = reference_sets()
    return {
        "relation": project_initial_relation_state(
            character_id=character_id,
            as_of=as_of,
            provenance=provenance(),
            known_person_ids=refs.known_person_ids,
            people=[{"person_id": PERSON_ID, "open_promises": []}],
        ),
        "home": project_initial_home_state(
            character_id=character_id,
            as_of=as_of,
            provenance=provenance(),
            known_entity_ids=refs.known_home_entity_ids,
            entities=[{"entity_id": HOME_ENTITY_ID, "status": "NORMAL"}],
        ),
        "consumables": project_initial_consumables_state(
            character_id=character_id,
            as_of=as_of,
            provenance=provenance(),
            approved_consumable_ids=refs.approved_consumable_ids,
            stocks=[{"consumable_id": CONSUMABLE_ID, "status": "AVAILABLE"}],
        ),
        "wardrobe": project_initial_wardrobe_state(
            character_id=character_id,
            as_of=as_of,
            provenance=provenance(),
            approved_item_ids=refs.approved_wardrobe_item_ids,
            items=[
                {
                    "item_id": WARDROBE_ITEM_ID,
                    "lifecycle_state": "CLEAN_AVAILABLE",
                    "wear_count_since_clean": 0,
                }
            ],
        ),
        "finance": project_initial_finance_state(
            character_id=character_id,
            as_of=as_of,
            provenance=provenance(),
            balance_minor=None,
        ),
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def snapshot_tree(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def build_runtime_bundle_root(
    tmp: Path,
    *,
    character_id: str = CHARACTER_ID,
    as_of: str = AS_OF,
    processed_through: str | None = None,
    engine_commit_sha: str = ENGINE_SHA,
    include_timeline: bool = False,
    mutate_current: Any = None,
    mutate_schedule: Any = None,
    world_overrides: dict[str, dict[str, Any]] | None = None,
) -> Path:
    root = tmp / "bundle"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    worlds = world_states(character_id=character_id, as_of=as_of)
    if world_overrides:
        worlds.update(world_overrides)

    schedule = build_schedule_state(character_id=character_id, as_of=as_of)
    if mutate_schedule is not None:
        schedule = mutate_schedule(schedule)

    current = empty_checkpoint(
        character_id=character_id,
        life_epoch="2026-04-01T00:00:00+09:00",
        world_seed="public-runtime-world-seed",
        behavior_policy_version="public-fixture-runtime-1",
        engine_commit_sha=engine_commit_sha,
        location_id="fixture-home",
        human_state={
            "sleep_debt_min": 0,
            "hunger": 100,
            "physical_fatigue": 100,
            "affect_valence": 0,
            "stress": 100,
            "social_battery": 800,
        },
    )
    current["processed_through"] = processed_through or as_of
    current["domain_refs"] = {
        "home_revision": worlds["home"]["revision"],
        "social_graph_revision": None,
        "finance_revision": worlds["finance"]["revision"],
        "wardrobe_revision": worlds["wardrobe"]["revision"],
        "relation_state_revision": worlds["relation"]["revision"],
        "consumables_revision": worlds["consumables"]["revision"],
        "schedule_revision": schedule["revision"],
        "schedule_hash": schedule["state_hash"],
        "social_response_revision": None,
    }
    if mutate_current is not None:
        current = mutate_current(current)

    write_json(root / "state/current.json", current)
    write_json(root / "schedule/state.json", schedule)
    write_json(root / "relations/state.json", worlds["relation"])
    write_json(root / "home/state.json", worlds["home"])
    write_json(root / "consumables/state.json", worlds["consumables"])
    write_json(root / "wardrobe/state.json", worlds["wardrobe"])
    write_json(root / "finance/state.json", worlds["finance"])

    if include_timeline:
        (root / "timeline").mkdir()

    return root


def build_synthetic_bundle_root(*args: Any, **kwargs: Any) -> Path:
    """Compatibility wrapper for shared runtime builders."""
    return build_runtime_bundle_root(*args, **kwargs)
