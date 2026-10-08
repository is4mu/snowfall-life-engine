"""Finance-state reducer: integer accounting, replay, strict payload, and isolation tests."""

from __future__ import annotations

import copy
import unittest

import pytest
from pathlib import Path

import engine.life.finance_reducer as finance_reducer_module

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.effects import RESERVED_UNIMPLEMENTED_EFFECTS, apply_effect
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.finance_reducer import (
    normalize_finance_transaction_effect,
    reduce_finance_transaction,
)
from engine.life.home_reducer import normalize_home_transition_effect, reduce_home_transition
from engine.life.relation_reducer import normalize_relation_touch_effect, reduce_relation_touch
from engine.life.schema import load_schema_document, validate_instance
from engine.life.wardrobe_reducer import (
    normalize_wardrobe_transition_effect,
    reduce_wardrobe_transition,
)
from engine.life.world_state import (
    project_initial_finance_state,
    project_initial_home_state,
    project_initial_relation_state,
    project_initial_wardrobe_state,
    validate_finance_state,
)

AS_OF = "2026-04-01T12:00:00+09:00"
CHAR = "fixture-character"
EVENT_ID = "evt-finance-transaction-1"


def _prov(**overrides) -> dict:
    base = {"origin": "SIMULATION_BOOTSTRAP", "notes": "public-finance-synthetic"}
    base.update(overrides)
    return base


def _finance(*, balance_minor: int | None = None, as_of: str = AS_OF) -> dict:
    return project_initial_finance_state(
        character_id=CHAR,
        as_of=as_of,
        provenance=_prov(),
        balance_minor=balance_minor,
    )


def _effect(
    *,
    transaction_at: str = "2026-04-01T15:00:00+09:00",
    amount_minor: int = -500,
    balance_after_minor: int | None = None,
    **extra,
) -> dict:
    payload = {
        "transaction_at": transaction_at,
        "amount_minor": amount_minor,
        "balance_after_minor": balance_after_minor,
    }
    payload.update(extra)
    return {
        "schema_version": 1,
        "effect_type": "FINANCE_TRANSACTION",
        "payload": payload,
    }


def _reduce(
    state: dict,
    effect: dict,
    *,
    source_event_id: str = EVENT_ID,
) -> dict:
    return reduce_finance_transaction(
        state,
        effect,
        source_event_id=source_event_id,
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

class FinanceEffectSchemaTests(unittest.TestCase):
    def test_effect_schema_draft_2020_12_meta_valid(self) -> None:
        schema = load_schema_document("effect")
        self.assertEqual(
            schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
        )
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:  # pragma: no cover
            self.fail(f"effect schema meta-invalid: {exc}")


class FinanceEffectPayloadTests(unittest.TestCase):
    def test_01_strict_finance_transaction_structurally_valid(self) -> None:
        effect = _effect(amount_minor=-100, balance_after_minor=None)
        validate_instance(effect, "effect")
        out = normalize_finance_transaction_effect(effect)
        self.assertEqual(out["effect_type"], "FINANCE_TRANSACTION")
        self.assertEqual(out["payload"]["amount_minor"], -100)

    def test_02_unknown_payload_field_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(_effect(category="food"), "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_03_missing_transaction_at_rejected(self) -> None:
        effect = _effect()
        del effect["payload"]["transaction_at"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_04_missing_amount_minor_rejected(self) -> None:
        effect = _effect()
        del effect["payload"]["amount_minor"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_05_missing_balance_after_minor_rejected(self) -> None:
        effect = _effect()
        del effect["payload"]["balance_after_minor"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(effect, "effect")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_06_bad_transaction_at_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_finance_transaction_effect(
                _effect(transaction_at="not-a-timestamp")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_07_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_finance_transaction_effect(
                _effect(transaction_at="2026-04-01T15:00:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_08_fractional_timestamp_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_finance_transaction_effect(
                _effect(transaction_at="2026-04-01T15:00:00.5+09:00")
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_09_z_offset_canonicalizes_to_plus_09(self) -> None:
        out = normalize_finance_transaction_effect(
            _effect(transaction_at="2026-04-01T06:00:00Z", balance_after_minor=None)
        )
        self.assertEqual(out["payload"]["transaction_at"], "2026-04-01T15:00:00+09:00")

    def test_10_amount_integer_positive_valid(self) -> None:
        out = normalize_finance_transaction_effect(
            _effect(amount_minor=1000, balance_after_minor=None)
        )
        self.assertEqual(out["payload"]["amount_minor"], 1000)

    def test_11_amount_integer_negative_valid(self) -> None:
        out = normalize_finance_transaction_effect(
            _effect(amount_minor=-250, balance_after_minor=None)
        )
        self.assertEqual(out["payload"]["amount_minor"], -250)

    def test_12_amount_zero_valid(self) -> None:
        out = normalize_finance_transaction_effect(
            _effect(amount_minor=0, balance_after_minor=None)
        )
        self.assertEqual(out["payload"]["amount_minor"], 0)

    def test_13_amount_float_rejected(self) -> None:
        effect = _effect(amount_minor=1.5, balance_after_minor=None)  # type: ignore[arg-type]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_finance_transaction_effect(effect)
        self.assertIn(
            ctx.exception.code, (ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE)
        )

    def test_14_amount_bool_rejected(self) -> None:
        effect = _effect(amount_minor=True, balance_after_minor=None)  # type: ignore[arg-type]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_finance_transaction_effect(effect)
        self.assertIn(
            ctx.exception.code, (ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE)
        )

    def test_15_amount_numeric_string_rejected(self) -> None:
        effect = _effect()
        effect["payload"]["amount_minor"] = "100"
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_finance_transaction_effect(effect)
        self.assertIn(
            ctx.exception.code, (ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE)
        )

    def test_16_balance_after_integer_valid(self) -> None:
        out = normalize_finance_transaction_effect(
            _effect(amount_minor=-100, balance_after_minor=900)
        )
        self.assertEqual(out["payload"]["balance_after_minor"], 900)

    def test_17_balance_after_null_valid(self) -> None:
        out = normalize_finance_transaction_effect(
            _effect(amount_minor=-100, balance_after_minor=None)
        )
        self.assertIsNone(out["payload"]["balance_after_minor"])

    def test_18_balance_after_float_bool_string_rejected(self) -> None:
        for bad in (1.5, True, "900"):
            effect = _effect(amount_minor=-100, balance_after_minor=None)
            effect["payload"]["balance_after_minor"] = bad
            with self.assertRaises(LifeEngineError) as ctx:
                normalize_finance_transaction_effect(effect)
            self.assertIn(
                ctx.exception.code,
                (ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE),
                msg=f"bad={bad!r}",
            )

    def test_19_category_merchant_memo_account_payment_rejected(self) -> None:
        for key, value in (
            ("category", "food"),
            ("merchant", "Cafe"),
            ("memo", "lunch"),
            ("account", "checking"),
            ("payment_method", "card"),
            ("description", "prose"),
            ("transaction_id", "tx-1"),
        ):
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(_effect(**{key: value}), "effect")
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID, msg=key)

    def test_20_normalizer_does_not_mutate_caller_input(self) -> None:
        effect = _effect(transaction_at="2026-04-01T06:00:00Z", balance_after_minor=None)
        snap = copy.deepcopy(effect)
        normalize_finance_transaction_effect(effect)
        self.assertEqual(effect, snap)


class FinanceKnownBalanceReducerTests(unittest.TestCase):
    def test_21_known_balance_positive_amount_valid(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(amount_minor=500, balance_after_minor=1500),
        )
        self.assertEqual(out["balance_minor"], 1500)

    def test_22_known_balance_negative_amount_valid(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(amount_minor=-300, balance_after_minor=700),
        )
        self.assertEqual(out["balance_minor"], 700)

    def test_23_known_balance_zero_valid(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(amount_minor=0, balance_after_minor=1000),
        )
        self.assertEqual(out["balance_minor"], 1000)

    def test_24_known_balance_mismatched_balance_after_rejected(self) -> None:
        prior = _finance(balance_minor=1000)
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(prior, _effect(amount_minor=-100, balance_after_minor=800))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_25_known_balance_null_balance_after_rejected(self) -> None:
        prior = _finance(balance_minor=1000)
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(prior, _effect(amount_minor=-100, balance_after_minor=None))
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_26_output_balance_exactly_explicit_balance_after(self) -> None:
        prior = _finance(balance_minor=2000)
        out = _reduce(
            prior,
            _effect(amount_minor=-250, balance_after_minor=1750),
        )
        self.assertEqual(out["balance_minor"], 1750)

    def test_27_older_transaction_rejected(self) -> None:
        prior = _finance(balance_minor=1000, as_of="2026-04-01T15:00:00+09:00")
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(
                prior,
                _effect(
                    transaction_at="2026-04-01T14:59:59+09:00",
                    amount_minor=0,
                    balance_after_minor=1000,
                ),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_28_equal_transaction_at_allowed(self) -> None:
        prior = _finance(balance_minor=1000, as_of="2026-04-01T15:00:00+09:00")
        out = _reduce(
            prior,
            _effect(
                transaction_at="2026-04-01T15:00:00+09:00",
                amount_minor=-50,
                balance_after_minor=950,
            ),
        )
        self.assertEqual(out["as_of"], "2026-04-01T15:00:00+09:00")
        self.assertEqual(out["balance_minor"], 950)

    def test_29_prior_state_not_mutated(self) -> None:
        prior = _finance(balance_minor=1000)
        snap = copy.deepcopy(prior)
        _reduce(prior, _effect(amount_minor=-100, balance_after_minor=900))
        self.assertEqual(prior, snap)

    def test_30_effect_input_not_mutated(self) -> None:
        prior = _finance(balance_minor=1000)
        effect = _effect(
            transaction_at="2026-04-01T06:00:00Z",
            amount_minor=-100,
            balance_after_minor=900,
        )
        snap = copy.deepcopy(effect)
        _reduce(prior, effect)
        self.assertEqual(effect, snap)

    def test_31_output_as_of_equals_transaction_at(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(
                transaction_at="2026-04-01T18:00:00+09:00",
                amount_minor=-100,
                balance_after_minor=900,
            ),
        )
        self.assertEqual(out["as_of"], "2026-04-01T18:00:00+09:00")

    def test_32_provenance_actual_event_plus_source_event_id(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(amount_minor=-100, balance_after_minor=900),
            source_event_id="evt-custom-1",
        )
        self.assertEqual(out["provenance"]["origin"], "ACTUAL_EVENT")
        self.assertEqual(out["provenance"]["source_event_id"], "evt-custom-1")

    def test_33_source_file_source_rule_id_not_fabricated(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(amount_minor=-100, balance_after_minor=900),
        )
        self.assertNotIn("source_file", out["provenance"])
        self.assertNotIn("source_rule_id", out["provenance"])

    def test_34_revision_equals_state_hash(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(amount_minor=-100, balance_after_minor=900),
        )
        self.assertEqual(out["revision"], out["state_hash"])
        validate_finance_state(out)

    def test_35_semantic_balance_change_changes_hash(self) -> None:
        prior = _finance(balance_minor=1000)
        out_a = _reduce(
            prior,
            _effect(amount_minor=-100, balance_after_minor=900),
        )
        out_b = _reduce(
            prior,
            _effect(amount_minor=-200, balance_after_minor=800),
            source_event_id="evt-other",
        )
        self.assertNotEqual(out_a["state_hash"], out_b["state_hash"])


class FinanceUnknownBalanceTests(unittest.TestCase):
    def test_36_null_prior_null_balance_after_remains_null(self) -> None:
        prior = _finance(balance_minor=None)
        out = _reduce(
            prior,
            _effect(amount_minor=-500, balance_after_minor=None),
        )
        self.assertIsNone(out["balance_minor"])

    def test_37_null_prior_integer_balance_after_rejected(self) -> None:
        prior = _finance(balance_minor=None)
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(
                prior,
                _effect(amount_minor=-500, balance_after_minor=1000),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_38_amount_never_fabricates_initial_balance(self) -> None:
        prior = _finance(balance_minor=None)
        out = _reduce(
            prior,
            _effect(amount_minor=99999, balance_after_minor=None),
        )
        self.assertIsNone(out["balance_minor"])
        self.assertNotEqual(out["balance_minor"], 99999)

    def test_39_repeated_unknown_balance_replay_idempotent(self) -> None:
        prior = _finance(balance_minor=None)
        effect = _effect(amount_minor=-500, balance_after_minor=None)
        once = _reduce(prior, effect)
        twice = _reduce(once, effect)
        self.assertIsNone(twice["balance_minor"])
        self.assertEqual(twice["state_hash"], once["state_hash"])
        self.assertEqual(twice["revision"], once["revision"])


class FinanceReplayTests(unittest.TestCase):
    def test_40_same_input_deterministic(self) -> None:
        prior = _finance(balance_minor=5000)
        effect = _effect(amount_minor=-200, balance_after_minor=4800)
        a = _reduce(prior, effect)
        b = _reduce(prior, effect)
        self.assertEqual(a, b)
        self.assertEqual(a["state_hash"], b["state_hash"])

    def test_41_immediate_replay_does_not_double_apply_amount(self) -> None:
        prior = _finance(balance_minor=1000)
        effect = _effect(amount_minor=-100, balance_after_minor=900)
        once = _reduce(prior, effect)
        self.assertEqual(once["balance_minor"], 900)
        twice = _reduce(once, effect)
        self.assertEqual(twice["balance_minor"], 900)

    def test_42_immediate_replay_preserves_hash_revision(self) -> None:
        prior = _finance(balance_minor=1000)
        effect = _effect(amount_minor=-100, balance_after_minor=900)
        once = _reduce(prior, effect)
        twice = _reduce(once, effect)
        self.assertEqual(twice["state_hash"], once["state_hash"])
        self.assertEqual(twice["revision"], once["revision"])
        self.assertEqual(twice, once)

    def test_43_different_source_event_id_not_same_event_replay(self) -> None:
        prior = _finance(balance_minor=1000)
        effect = _effect(amount_minor=-100, balance_after_minor=900)
        once = _reduce(prior, effect, source_event_id="evt-a")
        # Same amount/balance_after against already-updated state would fail
        # arithmetic unless treated as replay — different event_id must fail.
        with self.assertRaises(LifeEngineError) as ctx:
            _reduce(once, effect, source_event_id="evt-b")
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_44_no_transaction_ledger_cursor_added(self) -> None:
        prior = _finance(balance_minor=1000)
        out = _reduce(
            prior,
            _effect(amount_minor=-100, balance_after_minor=900),
        )
        forbidden = {
            "transactions",
            "transaction_ids",
            "seen_event_ids",
            "ledger",
            "cursor",
            "transactions_cursor",
            "replay_cursor",
            "dedupe",
        }
        self.assertTrue(forbidden.isdisjoint(out.keys()))
        src = Path(finance_reducer_module.__file__).read_text(encoding="utf-8")
        for banned in (
            "transactions_cursor",
            "seen_event_ids",
            "transaction_ledger",
            "dedupe_registry",
            "replay_cursor",
        ):
            self.assertNotIn(banned, src)


class FinanceReducerBoundaryTests(unittest.TestCase):
    def test_45_foundation_apply_effect_finance_unsupported(self) -> None:
        self.assertIn("FINANCE_TRANSACTION", RESERVED_UNIMPLEMENTED_EFFECTS)
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(
                _stub_state(),
                _effect(amount_minor=-100, balance_after_minor=None),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_46_relation_touch_strict_behavior_unchanged(self) -> None:
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

    def test_47_home_transition_strict_behavior_unchanged(self) -> None:
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

    def test_48_wardrobe_transition_strict_behavior_unchanged(self) -> None:
        effect = {
            "schema_version": 1,
            "effect_type": "WARDROBE_TRANSITION",
            "payload": {
                "transition_kind": "WARDROBE_ITEM_STATE_SET",
                "transition_at": "2026-04-01T06:00:00Z",
                "item_id": "item-a",
                "lifecycle_state": "DIRTY",
                "last_worn_at": None,
                "wear_count_since_clean": 1,
                "location_ref": "hamper",
            },
        }
        normalized = normalize_wardrobe_transition_effect(effect)
        self.assertEqual(
            normalized["payload"]["transition_at"], "2026-04-01T15:00:00+09:00"
        )
        wardrobe = project_initial_wardrobe_state(
            character_id=CHAR,
            as_of=AS_OF,
            provenance=_prov(),
            approved_item_ids=frozenset({"item-a"}),
            items=[],
        )
        out = reduce_wardrobe_transition(
            wardrobe,
            effect,
            source_event_id="evt-ward",
            approved_item_ids=frozenset({"item-a"}),
        )
        self.assertEqual(out["items"][0]["item_id"], "item-a")
        with self.assertRaises(LifeEngineError) as ctx:
            apply_effect(_stub_state(), effect)
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_EFFECT)

    def test_49_task_progress_remains_opaque_unsupported(self) -> None:
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
        # FINANCE is no longer opaque.
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

    def test_56_no_runtime_writes_from_reducer_module(self) -> None:
        src = Path(finance_reducer_module.__file__).read_text(encoding="utf-8")
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

    def test_currency_preserved_jpy(self) -> None:
        prior = _finance(balance_minor=100)
        out = _reduce(
            prior,
            _effect(amount_minor=0, balance_after_minor=100),
        )
        self.assertEqual(out["currency"], "JPY")
