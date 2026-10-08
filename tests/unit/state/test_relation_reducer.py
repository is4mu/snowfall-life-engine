"""Relation-state reducer normalization, strict payload, replay, and isolation tests."""

from __future__ import annotations

import copy
import unittest

import pytest
from pathlib import Path

import engine.life.relation_reducer as relation_reducer_module

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.effects import RESERVED_UNIMPLEMENTED_EFFECTS, apply_effect
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.relation_reducer import normalize_relation_touch_effect, reduce_relation_touch
from engine.life.schema import load_schema_document, validate_instance
from engine.life.world_state import (
    compute_domain_state_hash,
    project_initial_relation_state,
    validate_relation_state,
)

AS_OF = "2026-04-01T12:00:00+09:00"
CHAR = "fixture-character"
KNOWN = frozenset({"person-a", "person-b", "person-c"})
EVENT_ID = "evt-relation-touch-1"


def _prov(**overrides) -> dict:
    base = {"origin": "SIMULATION_BOOTSTRAP", "notes": "public-relation-synthetic"}
    base.update(overrides)
    return base


def _relation(
    *,
    people: list[dict] | None = None,
    as_of: str = AS_OF,
    known: frozenset[str] = KNOWN,
) -> dict:
    return project_initial_relation_state(
        character_id=CHAR,
        as_of=as_of,
        provenance=_prov(),
        known_person_ids=known,
        people=people if people is not None else [],
    )


def _touch(
    *,
    person_id: str = "person-a",
    contact_at: str = "2026-04-01T15:00:00+09:00",
    in_person_at=None,
    **extra,
) -> dict:
    payload = {
        "person_id": person_id,
        "contact_at": contact_at,
        "in_person_at": in_person_at,
    }
    payload.update(extra)
    return {
        "schema_version": 1,
        "effect_type": "RELATION_TOUCH",
        "payload": payload,
    }


def _reduce(state: dict, effect: dict, *, source_event_id: str = EVENT_ID, known=KNOWN):
    return reduce_relation_touch(
        state,
        effect,
        source_event_id=source_event_id,
        known_person_ids=known,
    )


pytestmark = pytest.mark.unit

class RelationEffectSchemaTests(unittest.TestCase):
    def test_effect_schema_draft_2020_12_meta_valid(self) -> None:
        schema = load_schema_document("effect")
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:  # pragma: no cover
            self.fail(f"effect schema meta-invalid: {exc}")


class RelationEffectNormalizerTests(unittest.TestCase):
    """Direct tests for normalize_relation_touch_effect semantic layer."""

    def test_z_offset_canonicalizes_to_plus_09(self) -> None:
        effect = _touch(
            contact_at="2026-04-01T06:00:00Z",
            in_person_at="2026-04-01T06:00:00Z",
        )
        out = normalize_relation_touch_effect(effect)
        self.assertEqual(out["payload"]["contact_at"], "2026-04-01T15:00:00+09:00")
        self.assertEqual(out["payload"]["in_person_at"], "2026-04-01T15:00:00+09:00")

    def test_other_accepted_offset_canonicalizes_to_plus_09(self) -> None:
        effect = _touch(
            contact_at="2026-04-01T01:00:00-05:00",
            in_person_at=None,
        )
        out = normalize_relation_touch_effect(effect)
        self.assertEqual(out["payload"]["contact_at"], "2026-04-01T15:00:00+09:00")
        self.assertIsNone(out["payload"]["in_person_at"])

    def test_bad_timestamp_string_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_relation_touch_effect(_touch(contact_at="not-a-timestamp"))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_relation_touch_effect(
                _touch(contact_at="2026-04-01T15:00:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_fractional_timestamp_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_relation_touch_effect(
                _touch(contact_at="2026-04-01T15:00:00.5+09:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_malformed_in_person_at_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_relation_touch_effect(
                _touch(
                    contact_at="2026-04-01T15:00:00+09:00",
                    in_person_at="yesterday",
                )
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_different_instant_rejected_by_normalizer(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_relation_touch_effect(
                _touch(
                    contact_at="2026-04-01T15:00:00+09:00",
                    in_person_at="2026-04-01T16:00:00+09:00",
                )
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_caller_input_unchanged_after_normalize(self) -> None:
        effect = _touch(contact_at="2026-04-01T06:00:00Z", in_person_at=None)
        snapshot = copy.deepcopy(effect)
        normalize_relation_touch_effect(effect)
        self.assertEqual(effect, snapshot)


class RelationEffectPayloadTests(unittest.TestCase):
    def test_01_strict_relation_touch_payload_validates(self) -> None:
        effect = _touch(in_person_at=None)
        normalized = normalize_relation_touch_effect(effect)
        validate_instance(effect, "effect")
        self.assertEqual(
            normalized["payload"]["contact_at"], "2026-04-01T15:00:00+09:00"
        )

    def test_02_unknown_payload_field_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(_touch(closeness=1), "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_03_missing_person_id_fails(self) -> None:
        effect = _touch()
        del effect["payload"]["person_id"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_04_missing_contact_at_fails(self) -> None:
        effect = _touch()
        del effect["payload"]["contact_at"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_05_bad_inexact_timestamp_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_relation_touch_effect(
                _touch(contact_at="2026-04-01T15:00:00.5+09:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_06_in_person_at_null_valid(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": None,
                    "last_in_person_at": "2026-03-20T18:00:00+09:00",
                    "open_promises": [],
                }
            ]
        )
        out = _reduce(prior, _touch(in_person_at=None))
        normalize_relation_touch_effect(_touch(in_person_at=None))
        self.assertEqual(out["people"][0]["last_contact_at"], "2026-04-01T15:00:00+09:00")
        self.assertEqual(
            out["people"][0]["last_in_person_at"], "2026-03-20T18:00:00+09:00"
        )

    def test_07_in_person_at_equal_instant_valid(self) -> None:
        prior = _relation()
        # Same instant via different spelling (Z vs +09:00).
        effect = _touch(
            contact_at="2026-04-01T06:00:00Z",
            in_person_at="2026-04-01T15:00:00+09:00",
        )
        normalized = normalize_relation_touch_effect(effect)
        self.assertEqual(
            normalized["payload"]["contact_at"], "2026-04-01T15:00:00+09:00"
        )
        self.assertEqual(
            normalized["payload"]["in_person_at"], "2026-04-01T15:00:00+09:00"
        )
        out = _reduce(prior, effect)
        self.assertEqual(out["people"][0]["last_contact_at"], "2026-04-01T15:00:00+09:00")
        self.assertEqual(
            out["people"][0]["last_in_person_at"], "2026-04-01T15:00:00+09:00"
        )

    def test_08_in_person_at_different_instant_fails(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_relation_touch_effect(
                _touch(
                    contact_at="2026-04-01T15:00:00+09:00",
                    in_person_at="2026-04-01T16:00:00+09:00",
                )
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)


class RelationReducerTests(unittest.TestCase):
    def test_09_unknown_person_id_fails(self) -> None:
        prior = _relation()
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(prior, _touch(person_id="person-unknown"))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_10_existing_remote_updates_last_contact_only(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": "2026-03-01T10:00:00+09:00",
                    "last_in_person_at": "2026-02-01T10:00:00+09:00",
                    "open_promises": [{"promise_id": "p1"}],
                }
            ]
        )
        out = _reduce(prior, _touch(in_person_at=None))
        row = out["people"][0]
        self.assertEqual(row["last_contact_at"], "2026-04-01T15:00:00+09:00")
        self.assertEqual(row["last_in_person_at"], "2026-02-01T10:00:00+09:00")
        self.assertEqual(row["open_promises"], [{"promise_id": "p1"}])

    def test_11_existing_in_person_updates_both_timestamps(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": "2026-03-01T10:00:00+09:00",
                    "last_in_person_at": "2026-02-01T10:00:00+09:00",
                    "open_promises": [],
                }
            ]
        )
        ts = "2026-04-01T15:00:00+09:00"
        out = _reduce(prior, _touch(contact_at=ts, in_person_at=ts))
        row = out["people"][0]
        self.assertEqual(row["last_contact_at"], ts)
        self.assertEqual(row["last_in_person_at"], ts)

    def test_12_new_approved_person_creates_minimal_row(self) -> None:
        prior = _relation(people=[])
        ts = "2026-04-01T15:00:00+09:00"
        out = _reduce(prior, _touch(person_id="person-b", contact_at=ts, in_person_at=None))
        self.assertEqual(len(out["people"]), 1)
        row = out["people"][0]
        self.assertEqual(
            row,
            {
                "person_id": "person-b",
                "last_contact_at": ts,
                "last_in_person_at": None,
                "open_promises": [],
            },
        )

    def test_13_new_unknown_person_does_not_create_row(self) -> None:
        prior = _relation(people=[])
        with self.assertRaises(LifeEngineError):
            _reduce(prior, _touch(person_id="person-x"))
        self.assertEqual(prior["people"], [])

    def test_14_existing_open_promises_preserved_exactly(self) -> None:
        promises = [{"promise_id": "z-last"}, {"promise_id": "a-first"}]
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": None,
                    "last_in_person_at": None,
                    "open_promises": promises,
                }
            ]
        )
        out = _reduce(prior, _touch())
        # Canonical order by promise_id, contents preserved.
        self.assertEqual(
            out["people"][0]["open_promises"],
            [{"promise_id": "a-first"}, {"promise_id": "z-last"}],
        )

    def test_15_other_people_rows_unchanged(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": "2026-03-01T10:00:00+09:00",
                    "last_in_person_at": None,
                    "open_promises": [],
                },
                {
                    "person_id": "person-b",
                    "last_contact_at": "2026-03-10T10:00:00+09:00",
                    "last_in_person_at": "2026-03-05T10:00:00+09:00",
                    "open_promises": [{"promise_id": "keep"}],
                },
            ]
        )
        before_b = copy.deepcopy(
            next(p for p in prior["people"] if p["person_id"] == "person-b")
        )
        out = _reduce(prior, _touch(person_id="person-a"))
        after_b = next(p for p in out["people"] if p["person_id"] == "person-b")
        self.assertEqual(after_b, before_b)

    def test_16_prior_state_not_mutated(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": None,
                    "last_in_person_at": None,
                    "open_promises": [{"promise_id": "p1"}],
                }
            ]
        )
        snapshot = copy.deepcopy(prior)
        _reduce(prior, _touch())
        self.assertEqual(prior, snapshot)

    def test_17_effect_input_not_mutated(self) -> None:
        prior = _relation()
        effect = _touch(contact_at="2026-04-01T06:00:00Z")
        snapshot = copy.deepcopy(effect)
        _reduce(prior, effect)
        self.assertEqual(effect, snapshot)

    def test_18_same_input_deterministic(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": None,
                    "last_in_person_at": None,
                    "open_promises": [],
                }
            ]
        )
        effect = _touch()
        a = _reduce(prior, effect)
        b = _reduce(prior, effect)
        self.assertEqual(a, b)
        self.assertEqual(a["state_hash"], b["state_hash"])

    def test_19_same_effect_replay_on_immediate_result_idempotent(self) -> None:
        prior = _relation()
        effect = _touch()
        once = _reduce(prior, effect)
        twice = _reduce(once, effect)
        self.assertEqual(once["state_hash"], twice["state_hash"])
        self.assertEqual(once["revision"], twice["revision"])
        self.assertEqual(once["people"], twice["people"])
        self.assertEqual(once["as_of"], twice["as_of"])
        self.assertEqual(once["provenance"], twice["provenance"])

    def test_20_older_effect_than_as_of_fails(self) -> None:
        prior = _relation(as_of="2026-04-01T18:00:00+09:00")
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(prior, _touch(contact_at="2026-04-01T15:00:00+09:00"))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_21_equal_contact_at_as_of_replay_allowed(self) -> None:
        prior = _relation(as_of="2026-04-01T15:00:00+09:00")
        out = _reduce(prior, _touch(contact_at="2026-04-01T15:00:00+09:00"))
        self.assertEqual(out["as_of"], "2026-04-01T15:00:00+09:00")

    def test_22_output_as_of_equals_contact_at(self) -> None:
        prior = _relation()
        ts = "2026-04-01T16:30:00+09:00"
        out = _reduce(prior, _touch(contact_at=ts))
        self.assertEqual(out["as_of"], ts)

    def test_23_output_provenance_actual_event_plus_source(self) -> None:
        prior = _relation()
        out = _reduce(prior, _touch(), source_event_id="evt-abc")
        self.assertEqual(
            out["provenance"],
            {"origin": "ACTUAL_EVENT", "source_event_id": "evt-abc"},
        )

    def test_24_source_file_not_fabricated(self) -> None:
        prior = _relation()
        out = _reduce(prior, _touch())
        self.assertNotIn("source_file", out["provenance"])
        self.assertNotIn("source_rule_id", out["provenance"])

    def test_25_revision_equals_state_hash_and_semantic_change(self) -> None:
        prior = _relation()
        a = _reduce(prior, _touch(contact_at="2026-04-01T15:00:00+09:00"))
        b = _reduce(prior, _touch(contact_at="2026-04-01T16:00:00+09:00"))
        self.assertEqual(a["revision"], a["state_hash"])
        self.assertEqual(a["state_hash"], compute_domain_state_hash(a))
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_26_people_ordering_canonical(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-b",
                    "last_contact_at": None,
                    "last_in_person_at": None,
                    "open_promises": [],
                }
            ]
        )
        out = _reduce(prior, _touch(person_id="person-a"))
        ids = [p["person_id"] for p in out["people"]]
        self.assertEqual(ids, sorted(ids))

    def test_27_open_promises_ordering_remains_canonical(self) -> None:
        prior = _relation(
            people=[
                {
                    "person_id": "person-a",
                    "last_contact_at": None,
                    "last_in_person_at": None,
                    "open_promises": [
                        {"promise_id": "p-b"},
                        {"promise_id": "p-a"},
                    ],
                }
            ]
        )
        out = _reduce(prior, _touch())
        ids = [p["promise_id"] for p in out["people"][0]["open_promises"]]
        self.assertEqual(ids, ["p-a", "p-b"])

    def test_28_foundation_apply_effect_still_unsupported(self) -> None:
        stub = {
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
        self.assertIn("RELATION_TOUCH", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(stub, _touch())
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_29_other_reserved_effects_remain_opaque_unsupported(self) -> None:
        stub = {
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
                apply_effect(stub, strict)
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

        # HOME_TRANSITION became strict in home-effect layer; still Foundation-unsupported.
        home_transition = {
            "schema_version": 1,
            "effect_type": "HOME_TRANSITION",
            "payload": {
                "transition_kind": "HOME_ENTITY_STATUS_SET",
                "transition_at": "2026-04-01T15:00:00+09:00",
                "entity_id": "entity-a",
                "status": "NORMAL",
            },
        }
        validate_instance(home_transition, "effect")
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(stub, home_transition)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

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
            apply_effect(stub, wardrobe_transition)
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
            apply_effect(stub, finance_transaction)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_34_no_runtime_writes_from_reducer_module(self) -> None:
        src = Path(relation_reducer_module.__file__).read_text(encoding="utf-8")
        for banned in (
            "Path(",
            "open(",
            "urlopen",
            "requests.",
            "write_text",
            "write_bytes",
            "social-graph",
            "character-bible",
        ):
            self.assertNotIn(banned, src)

    def test_validate_after_reduce(self) -> None:
        prior = _relation()
        out = _reduce(prior, _touch(person_id="person-c", in_person_at=None))
        again = validate_relation_state(out, known_person_ids=KNOWN)
        self.assertEqual(again["state_hash"], out["state_hash"])
