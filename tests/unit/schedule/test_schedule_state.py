"""ScheduleState schema, normalization, hashing, validation, and build contracts."""

from __future__ import annotations

import copy
import unittest

import pytest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.schema import SCHEMA_DIR, SCHEMA_NAMES, load_schema_document
from engine.life.schedule_state import (
    build_schedule_state,
    compute_schedule_state_hash,
    normalize_schedule_state,
    validate_schedule_state,
)

from tests.support.builders.schedule import (
    AS_OF,
    CHARACTER_ID as CHAR,
    commitment as _commitment,
    obligation_task as _task,
)


pytestmark = pytest.mark.unit


class ScheduleSchemaTests(unittest.TestCase):
    def test_schedule_state_schema_registered_and_meta_valid(self) -> None:
        self.assertIn("schedule_state", SCHEMA_NAMES)
        self.assertTrue((SCHEMA_DIR / SCHEMA_NAMES["schedule_state"]).is_file())
        schema = load_schema_document("schedule_state")
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:  # pragma: no cover
            self.fail(f"schedule_state meta-invalid: {exc}")


class ScheduleStateTests(unittest.TestCase):
    def test_01_valid_empty_schedule(self) -> None:
        state = build_schedule_state(character_id=CHAR, as_of=AS_OF)
        self.assertEqual(state["commitments"], [])
        self.assertEqual(state["obligation_tasks"], [])
        self.assertEqual(state["revision"], state["state_hash"])
        validate_schedule_state(state)

    def test_02_deterministic_hash_revision(self) -> None:
        a = build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            commitments=[_commitment()],
            obligation_tasks=[_task()],
        )
        b = build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            commitments=[_commitment()],
            obligation_tasks=[_task()],
        )
        self.assertEqual(a["state_hash"], b["state_hash"])
        self.assertEqual(a["revision"], a["state_hash"])
        self.assertEqual(compute_schedule_state_hash(a), a["state_hash"])

    def test_03_commitment_reorder_invariant(self) -> None:
        c1 = _commitment(commitment_id="cmt-z")
        c2 = _commitment(commitment_id="cmt-a")
        forward = build_schedule_state(
            character_id=CHAR, as_of=AS_OF, commitments=[c1, c2]
        )
        reverse = build_schedule_state(
            character_id=CHAR, as_of=AS_OF, commitments=[c2, c1]
        )
        self.assertEqual(forward["state_hash"], reverse["state_hash"])
        self.assertEqual(
            [c["commitment_id"] for c in forward["commitments"]],
            ["cmt-a", "cmt-z"],
        )

    def test_04_task_reorder_invariant(self) -> None:
        t1 = _task(task_id="task-z")
        t2 = _task(task_id="task-a")
        forward = build_schedule_state(
            character_id=CHAR, as_of=AS_OF, obligation_tasks=[t1, t2]
        )
        reverse = build_schedule_state(
            character_id=CHAR, as_of=AS_OF, obligation_tasks=[t2, t1]
        )
        self.assertEqual(forward["state_hash"], reverse["state_hash"])
        self.assertEqual(
            [t["task_id"] for t in forward["obligation_tasks"]],
            ["task-a", "task-z"],
        )

    def test_05_duplicate_commitment_reject(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_schedule_state(
                character_id=CHAR,
                as_of=AS_OF,
                commitments=[
                    _commitment(commitment_id="dup"),
                    _commitment(commitment_id="dup"),
                ],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_06_duplicate_task_reject(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_schedule_state(
                character_id=CHAR,
                as_of=AS_OF,
                obligation_tasks=[
                    _task(task_id="dup"),
                    _task(task_id="dup"),
                ],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_07_commitment_created_after_as_of_reject(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_schedule_state(
                character_id=CHAR,
                as_of=AS_OF,
                commitments=[
                    _commitment(
                        created_at="2026-04-01T13:00:00+09:00",
                        status_history=[
                            {
                                "changed_at": "2026-04-01T13:00:00+09:00",
                                "from": None,
                                "to": "PLANNED",
                                "reason": "created",
                                "source_ref": "engine:create",
                            }
                        ],
                    )
                ],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("after as_of", ctx.exception.detail)

    def test_08_status_history_after_as_of_reject(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_schedule_state(
                character_id=CHAR,
                as_of=AS_OF,
                commitments=[
                    _commitment(
                        created_at="2026-04-01T08:00:00+09:00",
                        status="CANCELLED",
                        status_history=[
                            {
                                "changed_at": "2026-04-01T08:00:00+09:00",
                                "from": None,
                                "to": "PLANNED",
                                "reason": "created",
                                "source_ref": "engine:create",
                            },
                            {
                                "changed_at": "2026-04-01T15:00:00+09:00",
                                "from": "PLANNED",
                                "to": "CANCELLED",
                                "reason": "cancelled",
                                "source_ref": "engine:cancel",
                            },
                        ],
                    )
                ],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("after as_of", ctx.exception.detail)

    def test_09_task_created_after_as_of_reject(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_schedule_state(
                character_id=CHAR,
                as_of=AS_OF,
                obligation_tasks=[_task(created_at="2026-04-01T18:00:00+09:00")],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("after as_of", ctx.exception.detail)

    def test_10_binary_float_reject(self) -> None:
        bad = build_schedule_state(character_id=CHAR, as_of=AS_OF)
        bad["schema_version"] = 1.0  # type: ignore[assignment]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_schedule_state(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_11_unknown_field_reject(self) -> None:
        state = build_schedule_state(character_id=CHAR, as_of=AS_OF)
        state["standing_rules"] = []
        with self.assertRaises(LifeEngineError) as ctx:
            validate_schedule_state(state)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_12_no_input_mutation(self) -> None:
        raw = {
            "schema_version": 1,
            "character_id": CHAR,
            "as_of": AS_OF,
            "revision": "0" * 64,
            "state_hash": "0" * 64,
            "commitments": [_commitment(commitment_id="cmt-z"), _commitment(commitment_id="cmt-a")],
            "obligation_tasks": [],
        }
        digest = compute_schedule_state_hash(raw)
        raw["revision"] = digest
        raw["state_hash"] = digest
        before = copy.deepcopy(raw)
        validate_schedule_state(raw)
        normalize_schedule_state(raw)
        self.assertEqual(raw, before)

    def test_13_semantic_change_changes_hash(self) -> None:
        a = build_schedule_state(
            character_id=CHAR, as_of=AS_OF, commitments=[_commitment(commitment_id="c1")]
        )
        b = build_schedule_state(
            character_id=CHAR, as_of=AS_OF, commitments=[_commitment(commitment_id="c2")]
        )
        self.assertNotEqual(a["state_hash"], b["state_hash"])

    def test_14_recurrence_id_does_not_generate_records(self) -> None:
        state = build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            commitments=[_commitment(recurrence_id="opaque-recurrence-1")],
        )
        self.assertEqual(len(state["commitments"]), 1)
        self.assertEqual(state["commitments"][0]["recurrence_id"], "opaque-recurrence-1")
        # No standing recurrence field / auto-generated siblings.
        self.assertNotIn("recurrence_rules", state)
        self.assertNotIn("standing_commitments", state)

    def test_malformed_commitment_id_is_life_engine_error(self) -> None:
        """Sort-key ID type errors must not leak raw TypeError."""
        from engine.life.schedule_state import normalize_schedule_state

        bad = {
            "schema_version": 1,
            "character_id": CHAR,
            "as_of": AS_OF,
            "revision": "0" * 64,
            "state_hash": "0" * 64,
            "commitments": [_commitment(commitment_id=42)],  # type: ignore[arg-type]
            "obligation_tasks": [],
        }
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_schedule_state(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("commitment_id", ctx.exception.detail)

    def test_malformed_task_id_is_life_engine_error(self) -> None:
        from engine.life.schedule_state import normalize_schedule_state

        bad = {
            "schema_version": 1,
            "character_id": CHAR,
            "as_of": AS_OF,
            "revision": "0" * 64,
            "state_hash": "0" * 64,
            "commitments": [],
            "obligation_tasks": [_task(task_id=["not-a-string"])],  # type: ignore[arg-type]
        }
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_schedule_state(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("task_id", ctx.exception.detail)

    def test_future_planned_timing_allowed(self) -> None:
        """planned_start/end after as_of are allowed (schedule purpose)."""
        state = build_schedule_state(
            character_id=CHAR,
            as_of=AS_OF,
            commitments=[_commitment()],
            obligation_tasks=[_task()],
        )
        self.assertGreater(
            state["commitments"][0]["timing"]["planned_start"],
            AS_OF,
        )
        self.assertGreater(state["obligation_tasks"][0]["due_at"], AS_OF)
