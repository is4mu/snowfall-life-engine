"""Home and consumables reducers: strict transitions, replay, and isolation tests."""

from __future__ import annotations

import copy
import unittest

import pytest
from pathlib import Path

import engine.life.home_reducer as home_reducer_module

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.effects import RESERVED_UNIMPLEMENTED_EFFECTS, apply_effect
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.home_reducer import (
    normalize_home_transition_effect,
    reduce_consumables_transition,
    reduce_home_transition,
)
from engine.life.relation_reducer import normalize_relation_touch_effect, reduce_relation_touch
from engine.life.schema import load_schema_document, validate_instance
from engine.life.world_state import (
    compute_domain_state_hash,
    project_initial_consumables_state,
    project_initial_home_state,
    project_initial_relation_state,
    validate_consumables_state,
    validate_home_state,
)

AS_OF = "2026-04-01T12:00:00+09:00"
CHAR = "fixture-character"
KNOWN_ENTITIES = frozenset({"entity-a", "entity-b", "entity-c"})
APPROVED_STOCKS = frozenset({"stock-a", "stock-b", "stock-c"})
EVENT_ID = "evt-home-transition-1"


def _prov(**overrides) -> dict:
    base = {"origin": "SIMULATION_BOOTSTRAP", "notes": "public-home-synthetic"}
    base.update(overrides)
    return base


def _home(
    *,
    entities: list[dict] | None = None,
    as_of: str = AS_OF,
    known: frozenset[str] = KNOWN_ENTITIES,
) -> dict:
    return project_initial_home_state(
        character_id=CHAR,
        as_of=as_of,
        provenance=_prov(),
        known_entity_ids=known,
        entities=entities if entities is not None else [],
    )


def _consumables(
    *,
    stocks: list[dict] | None = None,
    as_of: str = AS_OF,
    approved: frozenset[str] = APPROVED_STOCKS,
) -> dict:
    return project_initial_consumables_state(
        character_id=CHAR,
        as_of=as_of,
        provenance=_prov(),
        approved_consumable_ids=approved,
        stocks=stocks if stocks is not None else [],
    )


def _home_effect(
    *,
    entity_id: str = "entity-a",
    status: str = "NEEDS_ATTENTION",
    transition_at: str = "2026-04-01T15:00:00+09:00",
    **extra,
) -> dict:
    payload = {
        "transition_kind": "HOME_ENTITY_STATUS_SET",
        "transition_at": transition_at,
        "entity_id": entity_id,
        "status": status,
    }
    payload.update(extra)
    return {
        "schema_version": 1,
        "effect_type": "HOME_TRANSITION",
        "payload": payload,
    }


def _consumable_effect(
    *,
    consumable_id: str = "stock-a",
    status: str = "LOW",
    transition_at: str = "2026-04-01T15:00:00+09:00",
    **extra,
) -> dict:
    payload = {
        "transition_kind": "CONSUMABLE_STATUS_SET",
        "transition_at": transition_at,
        "consumable_id": consumable_id,
        "status": status,
    }
    payload.update(extra)
    return {
        "schema_version": 1,
        "effect_type": "HOME_TRANSITION",
        "payload": payload,
    }


def _reduce_home(state: dict, effect: dict, *, source_event_id: str = EVENT_ID, known=KNOWN_ENTITIES):
    return reduce_home_transition(
        state,
        effect,
        source_event_id=source_event_id,
        known_entity_ids=known,
    )


def _reduce_stock(
    state: dict,
    effect: dict,
    *,
    source_event_id: str = EVENT_ID,
    approved=APPROVED_STOCKS,
):
    return reduce_consumables_transition(
        state,
        effect,
        source_event_id=source_event_id,
        approved_consumable_ids=approved,
    )


def _stub_state() -> dict:
    return {
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


pytestmark = pytest.mark.unit

class HomeEffectSchemaTests(unittest.TestCase):
    def test_effect_schema_draft_2020_12_meta_valid(self) -> None:
        schema = load_schema_document("effect")
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:  # pragma: no cover
            self.fail(f"effect schema meta-invalid: {exc}")


class HomeEffectPayloadTests(unittest.TestCase):
    def test_01_home_entity_status_set_structural_valid(self) -> None:
        effect = _home_effect()
        validate_instance(effect, "effect")
        out = normalize_home_transition_effect(effect)
        self.assertEqual(out["payload"]["transition_kind"], "HOME_ENTITY_STATUS_SET")

    def test_02_consumable_status_set_structural_valid(self) -> None:
        effect = _consumable_effect()
        validate_instance(effect, "effect")
        out = normalize_home_transition_effect(effect)
        self.assertEqual(out["payload"]["transition_kind"], "CONSUMABLE_STATUS_SET")

    def test_03_unknown_transition_kind_fails(self) -> None:
        effect = _home_effect()
        effect["payload"]["transition_kind"] = "ROOM_CLEAN_PERCENT"
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_04_unknown_payload_field_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(_home_effect(notes="prose"), "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_05_missing_transition_at_fails(self) -> None:
        effect = _home_effect()
        del effect["payload"]["transition_at"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_06_bad_timestamp_rejected_by_semantic_normalizer(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_home_transition_effect(
                _home_effect(transition_at="not-a-timestamp")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_07_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_home_transition_effect(
                _home_effect(transition_at="2026-04-01T15:00:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_08_fractional_timestamp_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_home_transition_effect(
                _home_effect(transition_at="2026-04-01T15:00:00.5+09:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_09_z_and_other_offset_canonicalize_to_plus_09(self) -> None:
        z_out = normalize_home_transition_effect(
            _home_effect(transition_at="2026-04-01T06:00:00Z")
        )
        self.assertEqual(z_out["payload"]["transition_at"], "2026-04-01T15:00:00+09:00")
        other = normalize_home_transition_effect(
            _home_effect(transition_at="2026-04-01T01:00:00-05:00")
        )
        self.assertEqual(other["payload"]["transition_at"], "2026-04-01T15:00:00+09:00")

    def test_10_normalizer_does_not_mutate_caller_input(self) -> None:
        effect = _home_effect(transition_at="2026-04-01T06:00:00Z")
        snapshot = copy.deepcopy(effect)
        normalize_home_transition_effect(effect)
        self.assertEqual(effect, snapshot)

    def test_11_home_branch_rejects_consumable_only_fields(self) -> None:
        effect = _home_effect(consumable_id="stock-a")
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_12_consumables_branch_rejects_home_only_fields(self) -> None:
        effect = _consumable_effect(entity_id="entity-a")
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_13_exact_quantity_fields_rejected(self) -> None:
        for key, value in (
            ("quantity", 3),
            ("count", 2),
            ("grams", 100),
            ("ml", 250),
        ):
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(_consumable_effect(**{key: value}), "effect")
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_14_brand_product_price_expiry_rejected(self) -> None:
        for key, value in (
            ("brand", "Acme"),
            ("product", "Milk"),
            ("price", 120),
            ("expiry", "2026-05-01"),
        ):
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(_consumable_effect(**{key: value}), "effect")
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_15_geometry_location_coordinate_rejected(self) -> None:
        for key, value in (
            ("geometry", {"x": 1, "y": 2}),
            ("location_coordinate", [35.0, 139.0]),
            ("coordinates", [1, 2]),
        ):
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(_home_effect(**{key: value}), "effect")
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)


class HomeReducerTests(unittest.TestCase):
    def test_16_known_existing_entity_status_update(self) -> None:
        prior = _home(
            entities=[{"entity_id": "entity-a", "status": "NORMAL"}]
        )
        out = _reduce_home(prior, _home_effect(status="NEEDS_ATTENTION"))
        row = next(e for e in out["entities"] if e["entity_id"] == "entity-a")
        self.assertEqual(row["status"], "NEEDS_ATTENTION")

    def test_17_approved_absent_entity_creates_minimal_row(self) -> None:
        prior = _home()
        out = _reduce_home(prior, _home_effect(entity_id="entity-c", status="NORMAL"))
        self.assertEqual(len(out["entities"]), 1)
        self.assertEqual(
            out["entities"][0],
            {"entity_id": "entity-c", "status": "NORMAL"},
        )

    def test_18_unknown_entity_fails(self) -> None:
        prior = _home()
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce_home(prior, _home_effect(entity_id="entity-unknown"))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_19_unrelated_entities_unchanged(self) -> None:
        prior = _home(
            entities=[
                {"entity_id": "entity-a", "status": "NORMAL"},
                {"entity_id": "entity-b", "status": "UNKNOWN"},
            ]
        )
        before_b = copy.deepcopy(
            next(e for e in prior["entities"] if e["entity_id"] == "entity-b")
        )
        out = _reduce_home(prior, _home_effect(entity_id="entity-a", status="NEEDS_ATTENTION"))
        after_b = next(e for e in out["entities"] if e["entity_id"] == "entity-b")
        self.assertEqual(after_b, before_b)

    def test_20_prior_state_not_mutated(self) -> None:
        prior = _home(entities=[{"entity_id": "entity-a", "status": "NORMAL"}])
        snap = copy.deepcopy(prior)
        _reduce_home(prior, _home_effect(status="NEEDS_ATTENTION"))
        self.assertEqual(prior, snap)

    def test_21_effect_input_not_mutated(self) -> None:
        prior = _home()
        effect = _home_effect(transition_at="2026-04-01T06:00:00Z")
        snap = copy.deepcopy(effect)
        _reduce_home(prior, effect)
        self.assertEqual(effect, snap)

    def test_22_older_transition_fails(self) -> None:
        prior = _home(as_of="2026-04-01T18:00:00+09:00")
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce_home(
                prior, _home_effect(transition_at="2026-04-01T15:00:00+09:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_23_equal_transition_at_as_of_replay_allowed(self) -> None:
        prior = _home(as_of="2026-04-01T15:00:00+09:00")
        out = _reduce_home(
            prior, _home_effect(transition_at="2026-04-01T15:00:00+09:00")
        )
        self.assertEqual(out["as_of"], "2026-04-01T15:00:00+09:00")

    def test_24_immediate_replay_idempotent(self) -> None:
        prior = _home()
        effect = _home_effect()
        once = _reduce_home(prior, effect)
        twice = _reduce_home(once, effect)
        self.assertEqual(once["state_hash"], twice["state_hash"])
        self.assertEqual(once["revision"], twice["revision"])
        self.assertEqual(once["entities"], twice["entities"])

    def test_25_output_as_of_equals_transition_at(self) -> None:
        prior = _home()
        ts = "2026-04-01T16:30:00+09:00"
        out = _reduce_home(prior, _home_effect(transition_at=ts))
        self.assertEqual(out["as_of"], ts)

    def test_26_provenance_actual_event_plus_source(self) -> None:
        prior = _home()
        out = _reduce_home(prior, _home_effect(), source_event_id="evt-abc")
        self.assertEqual(
            out["provenance"],
            {"origin": "ACTUAL_EVENT", "source_event_id": "evt-abc"},
        )

    def test_27_source_file_not_fabricated(self) -> None:
        prior = _home()
        out = _reduce_home(prior, _home_effect())
        self.assertNotIn("source_file", out["provenance"])
        self.assertNotIn("source_rule_id", out["provenance"])

    def test_28_revision_equals_state_hash(self) -> None:
        prior = _home()
        out = _reduce_home(prior, _home_effect())
        self.assertEqual(out["revision"], out["state_hash"])
        self.assertEqual(out["state_hash"], compute_domain_state_hash(out))

    def test_29_semantic_status_change_changes_hash(self) -> None:
        prior = _home(entities=[{"entity_id": "entity-a", "status": "NORMAL"}])
        a = _reduce_home(prior, _home_effect(status="NEEDS_ATTENTION"))
        b = _reduce_home(prior, _home_effect(status="UNKNOWN"))
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_30_canonical_entity_ordering_preserved(self) -> None:
        prior = _home(
            entities=[{"entity_id": "entity-b", "status": "NORMAL"}]
        )
        out = _reduce_home(
            prior, _home_effect(entity_id="entity-a", status="NORMAL")
        )
        ids = [e["entity_id"] for e in out["entities"]]
        self.assertEqual(ids, sorted(ids))

    def test_home_reducer_rejects_consumable_kind(self) -> None:
        prior = _home()
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce_home(prior, _consumable_effect())
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)


class ConsumablesReducerTests(unittest.TestCase):
    def test_31_approved_existing_stock_status_update(self) -> None:
        prior = _consumables(
            stocks=[{"consumable_id": "stock-a", "status": "AVAILABLE"}]
        )
        out = _reduce_stock(prior, _consumable_effect(status="LOW"))
        row = next(s for s in out["stocks"] if s["consumable_id"] == "stock-a")
        self.assertEqual(row["status"], "LOW")

    def test_32_approved_absent_stock_creates_minimal_row(self) -> None:
        prior = _consumables()
        out = _reduce_stock(
            prior, _consumable_effect(consumable_id="stock-c", status="OUT")
        )
        self.assertEqual(len(out["stocks"]), 1)
        self.assertEqual(
            out["stocks"][0],
            {"consumable_id": "stock-c", "status": "OUT"},
        )

    def test_33_unknown_consumable_id_fails(self) -> None:
        prior = _consumables()
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce_stock(prior, _consumable_effect(consumable_id="stock-unknown"))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_34_unrelated_stocks_unchanged(self) -> None:
        prior = _consumables(
            stocks=[
                {"consumable_id": "stock-a", "status": "AVAILABLE"},
                {"consumable_id": "stock-b", "status": "UNKNOWN"},
            ]
        )
        before_b = copy.deepcopy(
            next(s for s in prior["stocks"] if s["consumable_id"] == "stock-b")
        )
        out = _reduce_stock(prior, _consumable_effect(consumable_id="stock-a", status="LOW"))
        after_b = next(s for s in out["stocks"] if s["consumable_id"] == "stock-b")
        self.assertEqual(after_b, before_b)

    def test_35_no_exact_quantity_generated(self) -> None:
        prior = _consumables()
        out = _reduce_stock(prior, _consumable_effect(status="AVAILABLE"))
        for stock in out["stocks"]:
            self.assertEqual(set(stock.keys()), {"consumable_id", "status"})
            for banned in ("quantity", "count", "grams", "ml", "brand", "product"):
                self.assertNotIn(banned, stock)

    def test_36_immediate_replay_idempotent(self) -> None:
        prior = _consumables()
        effect = _consumable_effect()
        once = _reduce_stock(prior, effect)
        twice = _reduce_stock(once, effect)
        self.assertEqual(once["state_hash"], twice["state_hash"])
        self.assertEqual(once["stocks"], twice["stocks"])

    def test_37_older_transition_fails(self) -> None:
        prior = _consumables(as_of="2026-04-01T18:00:00+09:00")
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce_stock(
                prior, _consumable_effect(transition_at="2026-04-01T15:00:00+09:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_38_output_as_of_provenance_hash_correct(self) -> None:
        prior = _consumables()
        ts = "2026-04-01T17:00:00+09:00"
        out = _reduce_stock(
            prior,
            _consumable_effect(transition_at=ts),
            source_event_id="evt-stock-1",
        )
        self.assertEqual(out["as_of"], ts)
        self.assertEqual(
            out["provenance"],
            {"origin": "ACTUAL_EVENT", "source_event_id": "evt-stock-1"},
        )
        self.assertEqual(out["revision"], out["state_hash"])
        self.assertEqual(out["state_hash"], compute_domain_state_hash(out))

    def test_39_canonical_stock_ordering_preserved(self) -> None:
        prior = _consumables(
            stocks=[{"consumable_id": "stock-b", "status": "AVAILABLE"}]
        )
        out = _reduce_stock(
            prior, _consumable_effect(consumable_id="stock-a", status="LOW")
        )
        ids = [s["consumable_id"] for s in out["stocks"]]
        self.assertEqual(ids, sorted(ids))

    def test_consumables_reducer_rejects_home_kind(self) -> None:
        prior = _consumables()
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce_stock(prior, _home_effect())
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_validate_after_reduce(self) -> None:
        prior_h = _home()
        out_h = _reduce_home(prior_h, _home_effect(entity_id="entity-c"))
        again_h = validate_home_state(out_h, known_entity_ids=KNOWN_ENTITIES)
        self.assertEqual(again_h["state_hash"], out_h["state_hash"])

        prior_c = _consumables()
        out_c = _reduce_stock(prior_c, _consumable_effect(consumable_id="stock-c"))
        again_c = validate_consumables_state(
            out_c, approved_consumable_ids=APPROVED_STOCKS
        )
        self.assertEqual(again_c["state_hash"], out_c["state_hash"])


class HomeReducerBoundaryTests(unittest.TestCase):
    def test_40_foundation_apply_effect_home_transition_unsupported(self) -> None:
        self.assertIn("HOME_TRANSITION", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(_stub_state(), _home_effect())
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_41_relation_touch_strict_behavior_unchanged(self) -> None:
        effect = {
            "schema_version": 1,
            "effect_type": "RELATION_TOUCH",
            "payload": {
                "person_id": "person-a",
                "contact_at": "2026-04-01T06:00:00Z",
                "in_person_at": None,
            },
        }
        normalized = normalize_relation_touch_effect(effect)
        self.assertEqual(
            normalized["payload"]["contact_at"], "2026-04-01T15:00:00+09:00"
        )
        relation = project_initial_relation_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_person_ids=frozenset({"person-a"}),
            people=[],
        )
        out = reduce_relation_touch(
            relation,
            effect,
            source_event_id="evt-rel",
            known_person_ids=frozenset({"person-a"}),
        )
        self.assertEqual(out["people"][0]["person_id"], "person-a")
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(_stub_state(), effect)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_42_other_reserved_remain_opaque_unsupported(self) -> None:
        for effect_type in (
            "TASK_PROGRESS",
        ):
            # strict effect layer: TASK_PROGRESS is strict; Foundation apply_effect still rejects.
            strict = {
                "schema_version": 1,
                "effect_type": effect_type,
                "payload": {
                    "task_id": "task-a",
                    "progress_at": "2026-04-01T15:00:00+09:00",
                    "effort_spent_min": 30,
                    "effort_remaining_after_min": 90,
                    "status_after": "IN_PROGRESS",
                },
            }
            validate_instance(strict, "effect")
            with self.assertRaises(LifeEngineError) as ctx:
                apply_effect(_stub_state(), strict)
            self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(
                    {
                        "schema_version": 1,
                        "effect_type": effect_type,
                        "payload": {"opaque": True, "anything": 1},
                    },
                    "effect",
                )
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

        # WARDROBE_TRANSITION became strict in wardrobe-effect layer; still Foundation-unsupported.
        wardrobe_transition = {
            "schema_version": 1,
            "effect_type": "WARDROBE_TRANSITION",
            "payload": {
                "transition_kind": "WARDROBE_ITEM_STATE_SET",
                "transition_at": "2026-04-01T15:00:00+09:00",
                "item_id": "item-a",
                "lifecycle_state": "DIRTY",
                "last_worn_at": None,
                "wear_count_since_clean": 1,
                "location_ref": "hamper",
            },
        }
        validate_instance(wardrobe_transition, "effect")
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(_stub_state(), wardrobe_transition)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

        # FINANCE_TRANSACTION became strict in finance-effect layer; still Foundation-unsupported.
        finance_transaction = {
            "schema_version": 1,
            "effect_type": "FINANCE_TRANSACTION",
            "payload": {
                "transaction_at": "2026-04-01T15:00:00+09:00",
                "amount_minor": -100,
                "balance_after_minor": None,
            },
        }
        validate_instance(finance_transaction, "effect")
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(_stub_state(), finance_transaction)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_50_no_runtime_writes_from_reducer_module(self) -> None:
        src = Path(home_reducer_module.__file__).read_text(encoding="utf-8")
        for banned in (
            "Path(",
            "open(",
            "urlopen",
            "requests.",
            "write_text",
            "write_bytes",
            "social-graph",
            "character-bible",
            "environment/home",
        ):
            self.assertNotIn(banned, src)
