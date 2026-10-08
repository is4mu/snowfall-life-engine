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



pytestmark = pytest.mark.unit

class WorldStateProjectionTests(unittest.TestCase):
    # --- Relation ---

    def test_01_empty_relation_valid(self) -> None:
        state = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=frozenset(),
            people=[],
        )
        self.assertEqual(state["people"], [])
        validate_relation_state(state, known_person_ids=frozenset())

    def test_02_known_person_nullable_timestamps(self) -> None:
        known = frozenset({"person-a"})
        state = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=known,
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": None,
                    "last_in_person_at": "2026-03-20T18:00:00+09:00",
                    "open_promises": [],
                }
            ],
        )
        self.assertIsNone(state["people"][0]["last_contact_at"])
        self.assertEqual(
            state["people"][0]["last_in_person_at"], "2026-03-20T18:00:00+09:00"
        )

    def test_03_unknown_relation_person_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_relation_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_person_ids=frozenset({"person-a"}),
                people=[{"person_id": "person-unknown", "open_promises": []}],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_04_duplicate_relation_person_fails(self) -> None:
        known = frozenset({"person-a"})
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_relation_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_person_ids=known,
                people=[
                    {"person_id": "person-a", "open_promises": []},
                    {"person_id": "person-a", "open_promises": []},
                ],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_05_relation_timestamp_after_as_of_fails(self) -> None:
        known = frozenset({"person-a"})
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_relation_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_person_ids=known,
                people=[
                    {
                        "person_id": "person-a",
                        "last_contact_at": "2026-04-02T00:00:00+09:00",
                        "open_promises": [],
                    }
                ],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_06_friendship_score_rejected(self) -> None:
        known = frozenset({"person-a"})
        state = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=known,
            people=[{"person_id": "person-a", "open_promises": []}],
        )
        bad = copy.deepcopy(state)
        bad["people"][0]["friendship"] = 12  # speculative score field
        with self.assertRaises(LifeEngineError) as ctx:
            validate_relation_state(bad, known_person_ids=known)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    # --- Home ---

    def test_07_empty_home_valid(self) -> None:
        state = project_initial_home_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_entity_ids=frozenset(),
            entities=[],
        )
        self.assertEqual(state["entities"], [])

    def test_08_unknown_home_entity_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_home_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_entity_ids=frozenset({"entity-a"}),
                entities=[{"entity_id": "entity-x", "status": "NORMAL"}],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_09_home_does_not_embed_geometry(self) -> None:
        known = frozenset({"entity-a"})
        state = project_initial_home_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_entity_ids=known,
            entities=[{"entity_id": "entity-a", "status": "NORMAL"}],
        )
        bad = copy.deepcopy(state)
        bad["entities"][0]["geometry"] = {"x": 1, "y": 2}
        bad["state_hash"] = compute_domain_state_hash(bad)
        bad["revision"] = bad["state_hash"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_home_state(bad, known_entity_ids=known)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    # --- Wardrobe ---

    def test_10_empty_wardrobe_valid(self) -> None:
        state = project_initial_wardrobe_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_item_ids=frozenset(),
            items=[],
        )
        self.assertEqual(state["items"], [])

    def test_11_approved_wardrobe_item_valid(self) -> None:
        approved = frozenset({"item-tee-1"})
        state = project_initial_wardrobe_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_item_ids=approved,
            items=[
                {
                    "item_id": "item-tee-1",
                    "lifecycle_state": "CLEAN_AVAILABLE",
                    "last_worn_at": None,
                    "wear_count_since_clean": 0,
                    "location_ref": None,
                }
            ],
        )
        self.assertEqual(state["items"][0]["item_id"], "item-tee-1")

    def test_12_unknown_wardrobe_item_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_wardrobe_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_item_ids=frozenset({"item-tee-1"}),
                items=[_wardrobe_item("invented-sneakers")],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_13_prose_invented_wardrobe_not_accepted(self) -> None:
        # Projection fixture: prose-inspired invented ID is not in approved set.
        with self.assertRaises(LifeEngineError):
            project_initial_wardrobe_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(notes="has several sneakers"),
                approved_item_ids=frozenset(),
                items=[_wardrobe_item("prose-sneakers-bundle")],
            )

    # --- Consumables ---

    def test_14_empty_consumables_valid(self) -> None:
        state = project_initial_consumables_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_consumable_ids=frozenset(),
            stocks=[],
        )
        self.assertEqual(state["stocks"], [])

    def test_15_unknown_consumable_status_valid(self) -> None:
        approved = frozenset({"FOOD_STAPLE_PORTION"})
        state = project_initial_consumables_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_consumable_ids=approved,
            stocks=[{"consumable_id": "FOOD_STAPLE_PORTION", "status": "UNKNOWN"}],
        )
        self.assertEqual(state["stocks"][0]["status"], "UNKNOWN")

    def test_16_exact_quantity_binary_float_rejected(self) -> None:
        approved = frozenset({"FOOD_STAPLE_PORTION"})
        state = project_initial_consumables_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_consumable_ids=approved,
            stocks=[{"consumable_id": "FOOD_STAPLE_PORTION", "status": "LOW"}],
        )
        bad = copy.deepcopy(state)
        bad["stocks"][0]["quantity_g"] = 12.5
        with self.assertRaises(LifeEngineError) as ctx:
            # Reject floats before/while validating.
            validate_consumables_state(bad, approved_consumable_ids=approved)
        self.assertIn(
            ctx.exception.code, {ErrorCode.INVALID_STATE, ErrorCode.SCHEMA_INVALID}
        )

    # --- Finance ---

    def test_17_empty_finance_jpy_null_balance(self) -> None:
        state = project_initial_finance_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            balance_minor=None,
        )
        self.assertEqual(state["currency"], "JPY")
        self.assertIsNone(state["balance_minor"])

    def test_18_integer_balance_when_supplied(self) -> None:
        state = project_initial_finance_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            balance_minor=12000,
        )
        self.assertEqual(state["balance_minor"], 12000)

    def test_19_binary_float_balance_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_finance_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                balance_minor=12.5,  # type: ignore[arg-type]
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_20_projection_without_approved_balance_returns_null(self) -> None:
        state = project_initial_finance_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
        )
        self.assertIsNone(state["balance_minor"])

    # --- Hash / determinism ---

    def test_21_same_semantic_state_same_hash(self) -> None:
        kwargs = dict(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=frozenset({"p1", "p2"}),
            people=[
                {"person_id": "p1", "open_promises": []},
                {"person_id": "p2", "open_promises": [{"promise_id": "pr1"}]},
            ],
        )
        a = project_initial_relation_state(**kwargs)
        b = project_initial_relation_state(**kwargs)
        self.assertEqual(a["state_hash"], b["state_hash"])
        self.assertEqual(a["revision"], a["state_hash"])

    def test_22_reordered_unordered_inputs_same_hash(self) -> None:
        known = frozenset({"p1", "p2"})
        a = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=known,
            people=[
                {"person_id": "p1", "open_promises": []},
                {"person_id": "p2", "open_promises": []},
            ],
        )
        b = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=known,
            people=[
                {"person_id": "p2", "open_promises": []},
                {"person_id": "p1", "open_promises": []},
            ],
        )
        self.assertEqual(a["state_hash"], b["state_hash"])
        self.assertEqual([p["person_id"] for p in a["people"]], ["p1", "p2"])

    def test_23_semantic_change_different_hash(self) -> None:
        known = frozenset({"p1"})
        a = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=known,
            people=[{"person_id": "p1", "last_contact_at": None, "open_promises": []}],
        )
        b = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=known,
            people=[
                {
                    "person_id": "p1",
                    "last_contact_at": "2026-03-01T10:00:00+09:00",
                    "open_promises": [],
                }
            ],
        )
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_24_unknown_field_in_every_state_fails(self) -> None:
        cases = [
            (
                project_initial_relation_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    provenance=_prov(),
                    known_person_ids=frozenset(),
                ),
                lambda s: validate_relation_state(s, known_person_ids=frozenset()),
            ),
            (
                project_initial_home_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    provenance=_prov(),
                    known_entity_ids=frozenset(),
                ),
                lambda s: validate_home_state(s, known_entity_ids=frozenset()),
            ),
            (
                project_initial_wardrobe_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    provenance=_prov(),
                    approved_item_ids=frozenset(),
                ),
                lambda s: validate_wardrobe_state(s, approved_item_ids=frozenset()),
            ),
            (
                project_initial_consumables_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    provenance=_prov(),
                    approved_consumable_ids=frozenset(),
                ),
                lambda s: validate_consumables_state(
                    s, approved_consumable_ids=frozenset()
                ),
            ),
            (
                project_initial_finance_state(
                    character_id=CHAR, as_of=AS_OF, provenance=_prov()
                ),
                validate_finance_state,
            ),
        ]
        for state, validator in cases:
            bad = copy.deepcopy(state)
            bad["speculative_extra"] = "nope"
            with self.assertRaises(LifeEngineError) as ctx:
                validator(bad)
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_25_invalid_provenance_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_finance_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance={"origin": "NOT_A_REAL_ORIGIN"},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_26_projection_does_not_mutate_input(self) -> None:
        people = [
            {
                "person_id": "p1",
                "last_contact_at": None,
                "open_promises": [{"promise_id": "x"}],
            }
        ]
        people_before = copy.deepcopy(people)
        prov = _prov()
        prov_before = copy.deepcopy(prov)
        project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=prov,
            known_person_ids=frozenset({"p1"}),
            people=people,
        )
        self.assertEqual(people, people_before)
        self.assertEqual(prov, prov_before)

    def test_27_projection_repeat_deterministic(self) -> None:
        results = [
            project_initial_home_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_entity_ids=frozenset({"e1"}),
                entities=[{"entity_id": "e1", "status": "UNKNOWN"}],
            )
            for _ in range(5)
        ]
        hashes = {r["state_hash"] for r in results}
        self.assertEqual(len(hashes), 1)
        self.assertEqual(results[0], results[1])

    def test_28_no_file_network_io_in_world_state_module(self) -> None:
        src = Path(world_state_module.__file__).read_text(encoding="utf-8")
        for banned in (
            "Path(",
            "open(",
            "urlopen",
            "requests.",
            "http.client",
            "socket.",
            "read_text",
            "write_text",
            "character-bible",
            "wardrobe-bible",
            "social-graph",
            "room-states",
        ):
            self.assertNotIn(banned, src)

    def test_29_reserved_world_effects_remain_unsupported(self) -> None:
        stub_state = {
            "human_state": {
                "sleep_debt_min": 0,
                "hunger": 0,
                "physical_fatigue": 0,
                "affect_valence": 0,
                "stress": 0,
                "social_battery": 0,
            },
            "context": {"location_id": "fixture-home", "active_activity": None},
        }
        # FINANCE_TRANSACTION remains Foundation-unsupported; payload is strict (finance-effect layer).
        self.assertIn("FINANCE_TRANSACTION", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(
                stub_state,
                {
                    "schema_version": 1,
                    "effect_type": "FINANCE_TRANSACTION",
                    "payload": {
                        "transaction_at": "2026-04-01T12:00:00+09:00",
                        "amount_minor": -100,
                        "balance_after_minor": None,
                    },
                },
            )
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

        # RELATION_TOUCH remains Foundation-unsupported; payload is strict (relation-effect layer).
        self.assertIn("RELATION_TOUCH", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(
                stub_state,
                {
                    "schema_version": 1,
                    "effect_type": "RELATION_TOUCH",
                    "payload": {
                        "person_id": "person-a",
                        "contact_at": "2026-04-01T12:00:00+09:00",
                        "in_person_at": None,
                    },
                },
            )
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

        # HOME_TRANSITION remains Foundation-unsupported; payload is strict (home-effect layer).
        self.assertIn("HOME_TRANSITION", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(
                stub_state,
                {
                    "schema_version": 1,
                    "effect_type": "HOME_TRANSITION",
                    "payload": {
                        "transition_kind": "HOME_ENTITY_STATUS_SET",
                        "transition_at": "2026-04-01T12:00:00+09:00",
                        "entity_id": "entity-a",
                        "status": "NORMAL",
                    },
                },
            )
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

        # WARDROBE_TRANSITION remains Foundation-unsupported; payload is strict (wardrobe-effect layer).
        self.assertIn("WARDROBE_TRANSITION", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(
                stub_state,
                {
                    "schema_version": 1,
                    "effect_type": "WARDROBE_TRANSITION",
                    "payload": {
                        "transition_kind": "WARDROBE_ITEM_STATE_SET",
                        "transition_at": "2026-04-01T12:00:00+09:00",
                        "item_id": "item-a",
                        "lifecycle_state": "DIRTY",
                        "last_worn_at": None,
                        "wear_count_since_clean": 1,
                        "location_ref": "hamper",
                    },
                },
            )
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_35_no_binary_float_in_projected_states(self) -> None:
        def walk(obj, path="$"):
            if isinstance(obj, float):
                self.fail(f"binary float at {path}")
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    walk(v, f"{path}.{k}")
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    walk(v, f"{path}[{i}]")

        states = [
            project_initial_relation_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_person_ids=frozenset({"p1"}),
                people=[{"person_id": "p1", "open_promises": []}],
            ),
            project_initial_home_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_entity_ids=frozenset({"e1"}),
                entities=[{"entity_id": "e1", "status": "NORMAL"}],
            ),
            project_initial_wardrobe_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_item_ids=frozenset({"i1"}),
                items=[_wardrobe_item("i1")],
            ),
            project_initial_consumables_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_consumable_ids=frozenset({"c1"}),
                stocks=[{"consumable_id": "c1", "status": "AVAILABLE"}],
            ),
            project_initial_finance_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                balance_minor=100,
            ),
        ]
        for s in states:
            walk(s)
            self.assertRegex(s["state_hash"], r"^[a-f0-9]{64}$")
            self.assertEqual(s["revision"], s["state_hash"])

    def test_schemas_registered(self) -> None:
        for name in (
            "relation_state",
            "home_state",
            "wardrobe_state",
            "consumables_state",
            "finance_state",
        ):
            self.assertIn(name, SCHEMA_NAMES)
            self.assertTrue((SCHEMA_DIR / SCHEMA_NAMES[name]).is_file())


class WorldStateProjectionFailClosedTests(unittest.TestCase):
    """Bad inputs must fail at project_initial_* (not only post-hoc validator)."""

    def test_wardrobe_item_id_only_does_not_invent_clean_or_zero(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_wardrobe_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_item_ids=frozenset({"i1"}),
                items=[{"item_id": "i1"}],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("missing required fields", str(ctx.exception))

    def test_wardrobe_wear_count_float_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            project_initial_wardrobe_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_item_ids=frozenset({"i1"}),
                items=[_wardrobe_item("i1", wear_count_since_clean=1.5)],
            )

    def test_wardrobe_wear_count_string_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            project_initial_wardrobe_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_item_ids=frozenset({"i1"}),
                items=[_wardrobe_item("i1", wear_count_since_clean="2")],
            )

    def test_wardrobe_wear_count_bool_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            project_initial_wardrobe_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_item_ids=frozenset({"i1"}),
                items=[_wardrobe_item("i1", wear_count_since_clean=True)],
            )

    def test_wardrobe_brand_color_style_rejected(self) -> None:
        for speculative in (
            {"brand": "Acme"},
            {"color": "navy"},
            {"style": "casual"},
        ):
            item = _wardrobe_item("i1")
            item.update(speculative)
            with self.assertRaises(LifeEngineError) as ctx:
                project_initial_wardrobe_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    provenance=_prov(),
                    approved_item_ids=frozenset({"i1"}),
                    items=[item],
                )
            self.assertIn("unknown fields", str(ctx.exception))

    def test_consumables_quantity_g_rejected_at_projection(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_consumables_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                approved_consumable_ids=frozenset({"FOOD_STAPLE_PORTION"}),
                stocks=[
                    {
                        "consumable_id": "FOOD_STAPLE_PORTION",
                        "status": "LOW",
                        "quantity_g": 250,
                    }
                ],
            )
        self.assertIn("unknown fields", str(ctx.exception))

    def test_home_cleanliness_and_geometry_rejected_at_projection(self) -> None:
        for speculative in (
            {"cleanliness_pct": 80},
            {"geometry": {"x": 1, "y": 2}},
            {"quantity": 3},
        ):
            entity = {"entity_id": "e1", "status": "NORMAL"}
            entity.update(speculative)
            with self.assertRaises(LifeEngineError) as ctx:
                project_initial_home_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    provenance=_prov(),
                    known_entity_ids=frozenset({"e1"}),
                    entities=[entity],
                )
            self.assertIn("unknown fields", str(ctx.exception))

    def test_relation_person_unknown_field_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_relation_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_person_ids=frozenset({"p1"}),
                people=[
                    {
                        "person_id": "p1",
                        "open_promises": [],
                        "friendship": 3,
                    }
                ],
            )
        self.assertIn("unknown fields", str(ctx.exception))

    def test_relation_open_promise_prose_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_initial_relation_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                known_person_ids=frozenset({"p1"}),
                people=[
                    {
                        "person_id": "p1",
                        "open_promises": [
                            {
                                "promise_id": "pr1",
                                "description": "will call soon",
                            }
                        ],
                    }
                ],
            )
        self.assertIn("unknown fields", str(ctx.exception))

    def test_finance_bool_balance_rejected_at_projection(self) -> None:
        with self.assertRaises(LifeEngineError):
            project_initial_finance_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                balance_minor=True,  # type: ignore[arg-type]
            )

    def test_finance_string_balance_rejected_at_projection(self) -> None:
        with self.assertRaises(LifeEngineError):
            project_initial_finance_state(
                character_id=CHAR,
                as_of=AS_OF,
                provenance=_prov(),
                balance_minor="100",  # type: ignore[arg-type]
            )


class WorldStateValidationNoRepairTests(unittest.TestCase):
    """validate_* must reject missing/null required fields (normalize must not repair)."""

    def _envelope(self, domain: str, **payload) -> dict:
        return {
            "schema_version": 1,
            "domain": domain,
            "character_id": CHAR,
            "revision": "0" * 64,
            "state_hash": "0" * 64,
            "as_of": AS_OF,
            "provenance": _prov(),
            **payload,
        }

    def test_relation_missing_people_rejected(self) -> None:
        bad = self._envelope("relation")
        self.assertNotIn("people", bad)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_relation_state(bad, known_person_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_relation_people_null_rejected(self) -> None:
        bad = self._envelope("relation", people=None)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_relation_state(bad, known_person_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_relation_person_missing_required_fields_rejected(self) -> None:
        for missing in ("last_contact_at", "last_in_person_at", "open_promises"):
            person = {
                "person_id": "p1",
                "last_contact_at": None,
                "last_in_person_at": None,
                "open_promises": [],
            }
            del person[missing]
            bad = self._envelope("relation", people=[person])
            with self.assertRaises(LifeEngineError) as ctx:
                validate_relation_state(bad, known_person_ids=frozenset({"p1"}))
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID, missing)

    def test_home_missing_entities_rejected(self) -> None:
        bad = self._envelope("home")
        self.assertNotIn("entities", bad)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_home_state(bad, known_entity_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_home_entities_null_rejected(self) -> None:
        bad = self._envelope("home", entities=None)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_home_state(bad, known_entity_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_wardrobe_missing_items_rejected(self) -> None:
        bad = self._envelope("wardrobe")
        self.assertNotIn("items", bad)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_wardrobe_state(bad, approved_item_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_wardrobe_items_null_rejected(self) -> None:
        bad = self._envelope("wardrobe", items=None)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_wardrobe_state(bad, approved_item_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_consumables_missing_stocks_rejected(self) -> None:
        bad = self._envelope("consumables")
        self.assertNotIn("stocks", bad)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_consumables_state(bad, approved_consumable_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_consumables_stocks_null_rejected(self) -> None:
        bad = self._envelope("consumables", stocks=None)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_consumables_state(bad, approved_consumable_ids=frozenset())
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_wardrobe_item_missing_last_worn_or_location_rejected(self) -> None:
        for missing in ("last_worn_at", "location_ref"):
            item = _wardrobe_item("i1")
            del item[missing]
            bad = self._envelope("wardrobe", items=[item])
            with self.assertRaises(LifeEngineError) as ctx:
                validate_wardrobe_state(bad, approved_item_ids=frozenset({"i1"}))
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID, missing)

    def test_normalize_does_not_invent_missing_people(self) -> None:
        from engine.life.world_state import normalize_world_state

        raw = self._envelope("relation")
        raw.pop("people", None)
        out = normalize_world_state(raw)
        self.assertNotIn("people", out)

    def test_normalize_does_not_convert_null_collections_to_empty(self) -> None:
        from engine.life.world_state import normalize_world_state

        for domain, key in (
            ("relation", "people"),
            ("home", "entities"),
            ("wardrobe", "items"),
            ("consumables", "stocks"),
        ):
            raw = self._envelope(domain, **{key: None})
            out = normalize_world_state(raw)
            self.assertIsNone(out[key], domain)

    def test_projector_empty_inventory_still_emits_complete_empty_lists(self) -> None:
        # Projection contract: complete empty snapshot (not missing keys).
        rel = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=frozenset(),
        )
        self.assertEqual(rel["people"], [])
        home = project_initial_home_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_entity_ids=frozenset(),
        )
        self.assertEqual(home["entities"], [])
        ward = project_initial_wardrobe_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_item_ids=frozenset(),
        )
        self.assertEqual(ward["items"], [])
        cons = project_initial_consumables_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_consumable_ids=frozenset(),
        )
        self.assertEqual(cons["stocks"], [])


