"""World-state projection, validation, schema, and canonicalization contracts."""

from __future__ import annotations

import copy
import unittest

import pytest
from pathlib import Path

import engine.life.world_state as world_state_module

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.effects import RESERVED_UNIMPLEMENTED_EFFECTS, apply_effect
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.schema import SCHEMA_DIR, SCHEMA_NAMES, load_schema_document
from engine.life.world_state import (
    compute_domain_state_hash,
    project_initial_consumables_state,
    project_initial_finance_state,
    project_initial_home_state,
    project_initial_relation_state,
    project_initial_wardrobe_state,
    validate_consumables_state,
    validate_finance_state,
    validate_home_state,
    validate_relation_state,
    validate_wardrobe_state,
)

AS_OF = "2026-04-01T12:00:00+09:00"
CHAR = "fixture-character"


def _wardrobe_item(item_id: str, **overrides) -> dict:
    base = {
        "item_id": item_id,
        "lifecycle_state": "CLEAN_AVAILABLE",
        "last_worn_at": None,
        "wear_count_since_clean": 0,
        "location_ref": None,
    }
    base.update(overrides)
    return base


def _prov(**overrides) -> dict:
    base = {"origin": "SIMULATION_BOOTSTRAP", "notes": "public-world-state-synthetic"}
    base.update(overrides)
    return base


pytestmark = pytest.mark.contract

class WorldStateSchemaMetaTests(unittest.TestCase):
    def test_new_schemas_draft_2020_12_meta_valid(self) -> None:
        for name in (
            "relation_state",
            "home_state",
            "wardrobe_state",
            "consumables_state",
            "finance_state",
        ):
            schema = load_schema_document(name)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:  # pragma: no cover
                self.fail(f"{name} meta-invalid: {exc}")





class CanonicalWorldStateBoundaryTests(unittest.TestCase):
    """world-state layer must not widen Foundation global unordered/timestamp scanners."""


    def test_pre_slice3a_commitment_persisted_hash_unchanged(self) -> None:
        from engine.life.canonical import persisted_hash, sort_unordered_collections
        from engine.life.timeutil import canonicalize_timestamps

        commitment = {
            "schema_version": 1,
            "commitment_id": "cmt-exact-1",
            "kind": "CLASS",
            "hardness": "HARD",
            "created_at": "2026-01-01T08:00:00+09:00",
            "timing": {
                "timing_kind": "EXACT",
                "planned_start": "2026-01-01T10:00:00+09:00",
                "planned_end": "2026-01-01T11:30:00+09:00",
            },
            "location_id": "fixture-campus",
            "participants": ["fixture-friend-a", "fixture-character"],
            "source_kind": "INTERNAL",
            "provenance": {"origin": "ENGINE_DEFAULT", "notes": "synthetic"},
            "recurrence_id": None,
            "status": "PLANNED",
            "status_history": [
                {
                    "changed_at": "2026-01-01T08:00:00+09:00",
                    "from": None,
                    "to": "PLANNED",
                    "reason": "created",
                    "source_ref": "engine:create",
                }
            ],
            "conditions": [],
            "causes": [],
            "rule_ids": ["r-b", "r-a"],
            "input_state_refs": ["s2", "s1"],
            "location_constraints": ["desk-b", "desk-a"],
        }
        normalized = sort_unordered_collections(canonicalize_timestamps(commitment))
        self.assertEqual(
            normalized["participants"], ["fixture-character", "fixture-friend-a"]
        )
        self.assertEqual(
            persisted_hash(commitment),
            "002da741092959a8e6e1cbd6ed438ba6cf939f5659d40b09665b2bb50c2d24d8",
        )

    def test_reserved_task_progress_opaque_items_not_sorted_by_foundation(self) -> None:
        """Foundation normalize must not sort nested payload.items by item_id.

        strict effect layer strictifies TASK_PROGRESS schema, but Foundation
        ``normalize_persisted_object`` still does not interpret/sort nested
        effect payload lists. This golden freezes that non-interpretation
        behavior (schema validation is not applied here).
        """
        from engine.life.canonical import normalize_persisted_object, persisted_hash

        event = {
            "schema_version": 1,
            "event_id": "evt-task-opaque-1",
            "activity_instance_id": "act-1",
            "event_type": "SELF_CARE",
            "actual_start": "2026-01-01T10:00:00+09:00",
            "actual_end": "2026-01-01T10:30:00+09:00",
            "finalized_at": "2026-01-01T10:30:00+09:00",
            "captures": [],
            "provenance": {"origin": "ACTUAL_EVENT"},
            "details": None,
            "effects": [
                {
                    "schema_version": 1,
                    "effect_type": "TASK_PROGRESS",
                    "payload": {
                        # Intentionally reverse order; Foundation must not sort by item_id.
                        "items": [
                            {"item_id": "b", "lifecycle_state": "DIRTY"},
                            {"item_id": "a", "lifecycle_state": "CLEAN_AVAILABLE"},
                        ]
                    },
                }
            ],
        }
        normalized = normalize_persisted_object(event)
        items = normalized["effects"][0]["payload"]["items"]
        self.assertEqual([it["item_id"] for it in items], ["b", "a"])
        digest = persisted_hash(event)
        # Order-preserving: reversing opaque items must change Foundation hash.
        reversed_event = {
            **event,
            "effects": [
                {
                    "schema_version": 1,
                    "effect_type": "TASK_PROGRESS",
                    "payload": {
                        "items": [
                            {"item_id": "a", "lifecycle_state": "CLEAN_AVAILABLE"},
                            {"item_id": "b", "lifecycle_state": "DIRTY"},
                        ]
                    },
                }
            ],
        }
        self.assertNotEqual(digest, persisted_hash(reversed_event))
        # Frozen against Foundation normalize semantics (nested payload.items order preserved).
        self.assertEqual(
            digest,
            "1a3d671a7c38ba19a7ab36b97769e3e7f4fcc055043ce2cf0d2b08cf7d51a066",
        )

    def test_foundation_does_not_reinterpret_world_timestamp_keys_in_opaque_payload(self) -> None:
        from engine.life.canonical import normalize_persisted_object
        from engine.life.timeutil import canonicalize_timestamps

        opaque = {
            "as_of": "2026-01-01T00:00:00Z",  # would become +09:00 if scanned
            "last_worn_at": "2026-01-01T00:00:00Z",
            "last_contact_at": "2026-01-01T00:00:00Z",
            "last_in_person_at": "2026-01-01T00:00:00Z",
            "items": [{"item_id": "z"}, {"item_id": "a"}],
        }
        wrapped = {"payload": opaque, "created_at": "2026-01-01T08:00:00+09:00"}
        timed = canonicalize_timestamps(wrapped)
        # Foundation timestamp scanner must leave opaque WorldState-looking keys alone.
        self.assertEqual(timed["payload"]["as_of"], "2026-01-01T00:00:00Z")
        self.assertEqual(timed["payload"]["last_worn_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(timed["payload"]["last_contact_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(timed["payload"]["last_in_person_at"], "2026-01-01T00:00:00Z")
        # created_at remains a Foundation timestamp key.
        self.assertEqual(timed["created_at"], "2026-01-01T08:00:00+09:00")

        # TASK_PROGRESS schema is strict in 5B1, but Foundation timestamp scanner
        # still must not reinterpret nested WorldState-looking keys in payloads.
        normalized = normalize_persisted_object(
            {
                "schema_version": 1,
                "event_id": "evt-opaque-ts",
                "activity_instance_id": "act-1",
                "event_type": "SELF_CARE",
                "actual_start": "2026-01-01T10:00:00+09:00",
                "actual_end": "2026-01-01T10:30:00+09:00",
                "finalized_at": "2026-01-01T10:30:00+09:00",
                "captures": [],
                "provenance": {"origin": "ACTUAL_EVENT"},
                "details": None,
                "effects": [
                    {
                        "schema_version": 1,
                        "effect_type": "TASK_PROGRESS",
                        "payload": opaque,
                    }
                ],
            }
        )
        payload = normalized["effects"][0]["payload"]
        self.assertEqual(payload["as_of"], "2026-01-01T00:00:00Z")
        self.assertEqual([it["item_id"] for it in payload["items"]], ["z", "a"])

    def test_world_state_helper_still_order_and_timestamp_canonical(self) -> None:
        from engine.life.world_state import normalize_world_state

        state = project_initial_relation_state(
            character_id=CHAR,
            as_of="2026-03-31T15:00:00Z",
            provenance=_prov(),
            known_person_ids=frozenset({"p2", "p1"}),
            people=[
                {
                    "person_id": "p2",
                    "last_contact_at": "2026-03-30T12:00:00Z",
                    "open_promises": [
                        {"promise_id": "pr-b"},
                        {"promise_id": "pr-a"},
                    ],
                },
                {"person_id": "p1", "open_promises": []},
            ],
        )
        self.assertEqual(state["as_of"], "2026-04-01T00:00:00+09:00")
        self.assertEqual([p["person_id"] for p in state["people"]], ["p1", "p2"])
        self.assertEqual(
            [p["promise_id"] for p in state["people"][1]["open_promises"]],
            ["pr-a", "pr-b"],
        )
        self.assertEqual(
            state["people"][1]["last_contact_at"], "2026-03-30T21:00:00+09:00"
        )
        again = normalize_world_state(state)
        self.assertEqual(again["state_hash"], state["state_hash"])
