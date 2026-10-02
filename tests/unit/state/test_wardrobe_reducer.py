"""Wardrobe-state reducer: strict transition payload, replay, and isolation tests."""

from __future__ import annotations

import copy
import unittest

import pytest
from pathlib import Path

import engine.life.wardrobe_reducer as wardrobe_reducer_module

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.effects import RESERVED_UNIMPLEMENTED_EFFECTS, apply_effect
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.home_reducer import normalize_home_transition_effect, reduce_home_transition
from engine.life.relation_reducer import normalize_relation_touch_effect, reduce_relation_touch
from engine.life.schema import load_schema_document, validate_instance
from engine.life.wardrobe_reducer import (
    normalize_wardrobe_transition_effect,
    reduce_wardrobe_transition,
)
from engine.life.world_state import (
    compute_domain_state_hash,
    project_initial_home_state,
    project_initial_relation_state,
    project_initial_wardrobe_state,
    validate_wardrobe_state,
)

AS_OF = "2026-04-01T12:00:00+09:00"
CHAR = "fixture-character"
APPROVED = frozenset({"item-a", "item-b", "item-c"})
EVENT_ID = "evt-wardrobe-transition-1"
LIFECYCLES = (
    "CLEAN_AVAILABLE",
    "DIRTY",
    "WASHING",
    "DRYING",
    "DAMAGED",
    "UNAVAILABLE",
)


def _prov(**overrides) -> dict:
    base = {"origin": "SIMULATION_BOOTSTRAP", "notes": "public-wardrobe-synthetic"}
    base.update(overrides)
    return base


def _item(
    item_id: str = "item-a",
    *,
    lifecycle_state: str = "CLEAN_AVAILABLE",
    last_worn_at: str | None = None,
    wear_count_since_clean: int = 0,
    location_ref: str | None = "closet",
) -> dict:
    return {
        "item_id": item_id,
        "lifecycle_state": lifecycle_state,
        "last_worn_at": last_worn_at,
        "wear_count_since_clean": wear_count_since_clean,
        "location_ref": location_ref,
    }


def _wardrobe(
    *,
    items: list[dict] | None = None,
    as_of: str = AS_OF,
    approved: frozenset[str] = APPROVED,
) -> dict:
    return project_initial_wardrobe_state(
        character_id=CHAR,
        as_of=as_of,
        provenance=_prov(),
        approved_item_ids=approved,
        items=items if items is not None else [],
    )


def _effect(
    *,
    item_id: str = "item-a",
    lifecycle_state: str = "DIRTY",
    last_worn_at: str | None = "2026-04-01T14:00:00+09:00",
    wear_count_since_clean: int = 1,
    location_ref: str | None = "hamper",
    transition_at: str = "2026-04-01T15:00:00+09:00",
    **extra,
) -> dict:
    payload = {
        "transition_kind": "WARDROBE_ITEM_STATE_SET",
        "transition_at": transition_at,
        "item_id": item_id,
        "lifecycle_state": lifecycle_state,
        "last_worn_at": last_worn_at,
        "wear_count_since_clean": wear_count_since_clean,
        "location_ref": location_ref,
    }
    payload.update(extra)
    return {
        "schema_version": 1,
        "effect_type": "WARDROBE_TRANSITION",
        "payload": payload,
    }


def _reduce(
    state: dict,
    effect: dict,
    *,
    source_event_id: str = EVENT_ID,
    approved: frozenset[str] = APPROVED,
) -> dict:
    return reduce_wardrobe_transition(
        state,
        effect,
        source_event_id=source_event_id,
        approved_item_ids=approved,
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

class WardrobeEffectSchemaTests(unittest.TestCase):
    def test_effect_schema_draft_2020_12_meta_valid(self) -> None:
        schema = load_schema_document("effect")
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:  # pragma: no cover
            self.fail(f"effect schema meta-invalid: {exc}")


class WardrobeEffectPayloadTests(unittest.TestCase):
    def test_01_strict_item_state_set_structurally_valid(self) -> None:
        effect = _effect()
        validate_instance(effect, "effect")
        out = normalize_wardrobe_transition_effect(effect)
        self.assertEqual(out["payload"]["transition_kind"], "WARDROBE_ITEM_STATE_SET")

    def test_02_unknown_transition_kind_fails(self) -> None:
        effect = _effect()
        effect["payload"]["transition_kind"] = "WEAR_INCREMENT"
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_03_unknown_payload_field_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(_effect(notes="prose"), "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_04_missing_transition_at_fails(self) -> None:
        effect = _effect()
        del effect["payload"]["transition_at"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_05_missing_item_id_fails(self) -> None:
        effect = _effect()
        del effect["payload"]["item_id"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_06_missing_lifecycle_state_fails(self) -> None:
        effect = _effect()
        del effect["payload"]["lifecycle_state"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_07_missing_wear_count_since_clean_fails(self) -> None:
        effect = _effect()
        del effect["payload"]["wear_count_since_clean"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_08_missing_last_worn_at_fails(self) -> None:
        effect = _effect()
        del effect["payload"]["last_worn_at"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_09_missing_location_ref_fails(self) -> None:
        effect = _effect()
        del effect["payload"]["location_ref"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_10_invalid_lifecycle_enum_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(_effect(lifecycle_state="FOLDED"), "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_11_negative_wear_count_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(_effect(wear_count_since_clean=-1), "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_12_binary_float_fails(self) -> None:
        effect = _effect(wear_count_since_clean=1.5)  # type: ignore[arg-type]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_wardrobe_transition_effect(effect)
        self.assertIn(
            ctx.exception.code, (ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE)
        )

    def test_13_bad_transition_at_fails_semantic_normalize(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_wardrobe_transition_effect(
                _effect(transition_at="not-a-timestamp")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_14_naive_transition_at_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_wardrobe_transition_effect(
                _effect(transition_at="2026-04-01T15:00:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_15_fractional_transition_at_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_wardrobe_transition_effect(
                _effect(transition_at="2026-04-01T15:00:00.5+09:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_16_z_and_other_offset_canonicalize_to_plus_09(self) -> None:
        z_out = normalize_wardrobe_transition_effect(
            _effect(
                transition_at="2026-04-01T06:00:00Z",
                last_worn_at="2026-04-01T05:00:00Z",
            )
        )
        self.assertEqual(z_out["payload"]["transition_at"], "2026-04-01T15:00:00+09:00")
        other = normalize_wardrobe_transition_effect(
            _effect(
                transition_at="2026-04-01T01:00:00-05:00",
                last_worn_at=None,
            )
        )
        self.assertEqual(other["payload"]["transition_at"], "2026-04-01T15:00:00+09:00")

    def test_17_non_null_last_worn_at_canonicalizes(self) -> None:
        out = normalize_wardrobe_transition_effect(
            _effect(
                transition_at="2026-04-01T15:00:00+09:00",
                last_worn_at="2026-04-01T05:00:00Z",
            )
        )
        self.assertEqual(out["payload"]["last_worn_at"], "2026-04-01T14:00:00+09:00")

    def test_18_last_worn_at_after_transition_at_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_wardrobe_transition_effect(
                _effect(
                    transition_at="2026-04-01T15:00:00+09:00",
                    last_worn_at="2026-04-01T16:00:00+09:00",
                )
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_19_normalizer_does_not_mutate_caller_input(self) -> None:
        effect = _effect(transition_at="2026-04-01T06:00:00Z", last_worn_at=None)
        snapshot = copy.deepcopy(effect)
        normalize_wardrobe_transition_effect(effect)
        self.assertEqual(effect, snapshot)

    def test_20_brand_color_style_outfit_image_purchase_rejected(self) -> None:
        for key, value in (
            ("brand", "Acme"),
            ("color", "navy"),
            ("style", "casual"),
            ("size", "M"),
            ("outfit_id", "o1"),
            ("image_prompt", "photo"),
            ("purchase_price", 1200),
            ("store", "mall"),
        ):
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(_effect(**{key: value}), "effect")
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)


class WardrobeReducerTests(unittest.TestCase):
    def test_21_approved_existing_item_state_set_valid(self) -> None:
        prior = _wardrobe(items=[_item("item-a")])
        out = _reduce(prior, _effect(lifecycle_state="DIRTY", wear_count_since_clean=2))
        row = next(i for i in out["items"] if i["item_id"] == "item-a")
        self.assertEqual(row["lifecycle_state"], "DIRTY")
        self.assertEqual(row["wear_count_since_clean"], 2)
        self.assertEqual(row["location_ref"], "hamper")

    def test_22_approved_absent_item_creates_exactly_supplied_complete_v1_row(self) -> None:
        prior = _wardrobe()
        effect = _effect(
            item_id="item-c",
            lifecycle_state="WASHING",
            last_worn_at="2026-04-01T10:00:00+09:00",
            wear_count_since_clean=3,
            location_ref="washer",
        )
        out = _reduce(prior, effect)
        self.assertEqual(len(out["items"]), 1)
        self.assertEqual(
            out["items"][0],
            {
                "item_id": "item-c",
                "lifecycle_state": "WASHING",
                "last_worn_at": "2026-04-01T10:00:00+09:00",
                "wear_count_since_clean": 3,
                "location_ref": "washer",
            },
        )

    def test_23_unknown_item_fails(self) -> None:
        prior = _wardrobe()
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(prior, _effect(item_id="item-unknown"))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_24_unrelated_items_unchanged(self) -> None:
        prior = _wardrobe(
            items=[
                _item("item-a", lifecycle_state="CLEAN_AVAILABLE"),
                _item("item-b", lifecycle_state="DIRTY", wear_count_since_clean=1),
            ]
        )
        before_b = copy.deepcopy(
            next(i for i in prior["items"] if i["item_id"] == "item-b")
        )
        out = _reduce(prior, _effect(item_id="item-a", lifecycle_state="DIRTY"))
        after_b = next(i for i in out["items"] if i["item_id"] == "item-b")
        self.assertEqual(after_b, before_b)

    def test_25_prior_state_not_mutated(self) -> None:
        prior = _wardrobe(items=[_item("item-a")])
        snap = copy.deepcopy(prior)
        _reduce(prior, _effect(lifecycle_state="DIRTY"))
        self.assertEqual(prior, snap)

    def test_26_effect_input_not_mutated(self) -> None:
        prior = _wardrobe()
        effect = _effect(transition_at="2026-04-01T06:00:00Z", last_worn_at=None)
        snap = copy.deepcopy(effect)
        _reduce(prior, effect)
        self.assertEqual(effect, snap)

    def test_27_older_transition_fails(self) -> None:
        prior = _wardrobe(as_of="2026-04-01T18:00:00+09:00")
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(prior, _effect(transition_at="2026-04-01T15:00:00+09:00"))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_28_equal_transition_at_as_of_allowed(self) -> None:
        prior = _wardrobe(as_of="2026-04-01T15:00:00+09:00")
        out = _reduce(prior, _effect(transition_at="2026-04-01T15:00:00+09:00"))
        self.assertEqual(out["as_of"], "2026-04-01T15:00:00+09:00")

    def test_29_immediate_replay_idempotent(self) -> None:
        prior = _wardrobe()
        effect = _effect()
        once = _reduce(prior, effect)
        twice = _reduce(once, effect)
        self.assertEqual(once["state_hash"], twice["state_hash"])
        self.assertEqual(once["revision"], twice["revision"])
        self.assertEqual(once["items"], twice["items"])
        self.assertEqual(once["as_of"], twice["as_of"])

    def test_30_output_as_of_equals_transition_at(self) -> None:
        prior = _wardrobe()
        out = _reduce(prior, _effect(transition_at="2026-04-01T16:30:00+09:00"))
        self.assertEqual(out["as_of"], "2026-04-01T16:30:00+09:00")

    def test_31_provenance_actual_event_plus_source_event_id(self) -> None:
        prior = _wardrobe()
        out = _reduce(prior, _effect(), source_event_id="evt-explicit-42")
        self.assertEqual(out["provenance"]["origin"], "ACTUAL_EVENT")
        self.assertEqual(out["provenance"]["source_event_id"], "evt-explicit-42")

    def test_32_source_file_source_rule_id_not_fabricated(self) -> None:
        prior = _wardrobe()
        out = _reduce(prior, _effect())
        self.assertNotIn("source_file", out["provenance"])
        self.assertNotIn("source_rule_id", out["provenance"])

    def test_33_revision_equals_state_hash(self) -> None:
        prior = _wardrobe()
        out = _reduce(prior, _effect())
        self.assertEqual(out["revision"], out["state_hash"])
        self.assertEqual(out["state_hash"], compute_domain_state_hash(out))

    def test_34_explicit_lifecycle_change_changes_hash(self) -> None:
        prior = _wardrobe(items=[_item("item-a", lifecycle_state="CLEAN_AVAILABLE")])
        a = _reduce(prior, _effect(lifecycle_state="DIRTY", wear_count_since_clean=1))
        b = _reduce(prior, _effect(lifecycle_state="WASHING", wear_count_since_clean=1))
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_35_explicit_wear_count_change_changes_hash(self) -> None:
        prior = _wardrobe(items=[_item("item-a")])
        a = _reduce(prior, _effect(wear_count_since_clean=1))
        b = _reduce(prior, _effect(wear_count_since_clean=2))
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_36_explicit_last_worn_at_change_changes_hash(self) -> None:
        prior = _wardrobe(items=[_item("item-a")])
        a = _reduce(
            prior,
            _effect(last_worn_at="2026-04-01T13:00:00+09:00"),
        )
        b = _reduce(
            prior,
            _effect(last_worn_at="2026-04-01T14:00:00+09:00"),
        )
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_37_explicit_location_ref_change_changes_hash(self) -> None:
        prior = _wardrobe(items=[_item("item-a")])
        a = _reduce(prior, _effect(location_ref="closet"))
        b = _reduce(prior, _effect(location_ref="hamper"))
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_38_canonical_item_ordering_preserved(self) -> None:
        prior = _wardrobe(
            items=[
                _item("item-c"),
                _item("item-a"),
                _item("item-b"),
            ]
        )
        out = _reduce(prior, _effect(item_id="item-b", lifecycle_state="DIRTY"))
        ids = [i["item_id"] for i in out["items"]]
        self.assertEqual(ids, sorted(ids))

    def test_39_reducer_emits_no_fields_outside_wardrobe_state_v1(self) -> None:
        prior = _wardrobe(items=[_item("item-a")])
        out = _reduce(prior, _effect())
        allowed_top = {
            "schema_version",
            "domain",
            "character_id",
            "revision",
            "state_hash",
            "as_of",
            "provenance",
            "items",
        }
        self.assertEqual(set(out.keys()), allowed_top)
        allowed_item = {
            "item_id",
            "lifecycle_state",
            "last_worn_at",
            "wear_count_since_clean",
            "location_ref",
        }
        for row in out["items"]:
            self.assertEqual(set(row.keys()), allowed_item)
        validate_wardrobe_state(out, approved_item_ids=APPROVED)

    def test_explicit_state_set_not_hidden_increment(self) -> None:
        """Wear count is absolute SET; replaying same effect does not += 1."""
        prior = _wardrobe(items=[_item("item-a", wear_count_since_clean=0)])
        effect = _effect(wear_count_since_clean=5)
        once = _reduce(prior, effect)
        twice = _reduce(once, effect)
        row = next(i for i in twice["items"] if i["item_id"] == "item-a")
        self.assertEqual(row["wear_count_since_clean"], 5)


class WardrobeReducerBoundaryTests(unittest.TestCase):
    def test_40_foundation_apply_effect_wardrobe_unsupported(self) -> None:
        self.assertIn("WARDROBE_TRANSITION", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(_stub_state(), _effect())
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

    def test_42_home_transition_strict_behavior_unchanged(self) -> None:
        effect = {
            "schema_version": 1,
            "effect_type": "HOME_TRANSITION",
            "payload": {
                "transition_kind": "HOME_ENTITY_STATUS_SET",
                "transition_at": "2026-04-01T06:00:00Z",
                "entity_id": "entity-a",
                "status": "NORMAL",
            },
        }
        normalized = normalize_home_transition_effect(effect)
        self.assertEqual(
            normalized["payload"]["transition_at"], "2026-04-01T15:00:00+09:00"
        )
        home = project_initial_home_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            known_entity_ids=frozenset({"entity-a"}),
            entities=[],
        )
        out = reduce_home_transition(
            home,
            effect,
            source_event_id="evt-home",
            known_entity_ids=frozenset({"entity-a"}),
        )
        self.assertEqual(out["entities"][0]["entity_id"], "entity-a")
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(_stub_state(), effect)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_43_task_progress_remains_opaque_finance_strict_unsupported(self) -> None:
        # strict effect layer: TASK_PROGRESS is strict; Foundation apply_effect still rejects.
        strict = {
            "schema_version": 1,
            "effect_type": "TASK_PROGRESS",
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
                    "effect_type": "TASK_PROGRESS",
                    "payload": {"opaque": True, "anything": 1},
                },
                "effect",
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

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
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(
                {
                    "schema_version": 1,
                    "effect_type": "FINANCE_TRANSACTION",
                    "payload": {"opaque": True},
                },
                "effect",
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_50_no_runtime_writes_from_reducer_module(self) -> None:
        src = Path(wardrobe_reducer_module.__file__).read_text(encoding="utf-8")
        for banned in (
            "Path(",
            "open(",
            "urlopen",
            "requests.",
            "write_text",
            "write_bytes",
            "social-graph",
            "character-bible",
            "wardrobe-bible",
            "environment/home",
        ):
            self.assertNotIn(banned, src)

    def test_all_lifecycle_enums_accepted(self) -> None:
        prior = _wardrobe()
        for i, lifecycle in enumerate(LIFECYCLES):
            out = _reduce(
                prior,
                _effect(
                    item_id="item-a",
                    lifecycle_state=lifecycle,
                    transition_at=f"2026-04-01T{15 + i:02d}:00:00+09:00",
                    last_worn_at=None,
                ),
            )
            row = next(r for r in out["items"] if r["item_id"] == "item-a")
            self.assertEqual(row["lifecycle_state"], lifecycle)
            prior = out
