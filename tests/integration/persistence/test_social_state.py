"""Social-response state persistence and CurrentState binding integration tests."""

from __future__ import annotations

import copy
import tempfile
import unittest

import pytest
from pathlib import Path

from engine.life.errors import LifeEngineError
from engine.life.runtime_persistence import (
    SOCIAL_RESPONSE_RELPATH,
    load_runtime_persistent_snapshot,
    write_runtime_json,
)
from engine.life.schema import validate_instance
from engine.life.social_runtime import (
    build_social_response_state,
    validate_social_response_state,
)
from tests.support.builders.persistence import (
    AS_OF,
    CHAR,
    _refs,
    build_5c1_persistent_root,
    build_response_row,
    read_json,
    social_with_responses,
)


pytestmark = pytest.mark.integration

class PersistenceSocialPersistenceTests(unittest.TestCase):
    def test_01_schema_strict(self) -> None:
        social = build_social_response_state(character_id=CHAR, as_of=AS_OF)
        validate_instance(social.as_dict(), "social_response_state")
        validated = validate_social_response_state(social.as_dict())
        self.assertEqual(validated.revision, social.revision)
        self.assertEqual(validated.state_hash, social.revision)

    def test_02_unknown_field_reject(self) -> None:
        social = build_social_response_state(character_id=CHAR, as_of=AS_OF).as_dict()
        social["prose_rationale"] = "nope"
        with self.assertRaises(Exception):
            validate_instance(social, "social_response_state")
        with self.assertRaises(LifeEngineError):
            validate_social_response_state(social)

    def test_03_wrong_hash_revision_reject(self) -> None:
        social = build_social_response_state(character_id=CHAR, as_of=AS_OF).as_dict()
        social["revision"] = "a" * 64
        with self.assertRaises(LifeEngineError):
            validate_social_response_state(social)
        social = build_social_response_state(character_id=CHAR, as_of=AS_OF).as_dict()
        social["state_hash"] = "b" * 64
        with self.assertRaises(LifeEngineError):
            validate_social_response_state(social)

    def test_04_character_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            social = build_social_response_state(
                character_id="other-character", as_of=AS_OF
            )
            root = build_5c1_persistent_root(tmp, social=social, bind_social=True)
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("character_id", ctx.exception.detail)

    def test_05_as_of_future_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            social = build_social_response_state(
                character_id=CHAR, as_of="2026-04-01T13:00:00+09:00"
            )
            root = build_5c1_persistent_root(
                tmp, social=social, processed_through=AS_OF, bind_social=True
            )
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("as_of", ctx.exception.detail)

    def test_06_current_ref_null_reject_in_persistent_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, bind_social=False)
            current = read_json(root / "state/current.json")
            self.assertIsNone(current["domain_refs"]["social_response_revision"])
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("non-null", ctx.exception.detail)

    def test_07_current_ref_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)

            def _bad(current: dict) -> dict:
                current = dict(current)
                current["domain_refs"] = dict(current["domain_refs"])
                current["domain_refs"]["social_response_revision"] = "c" * 64
                return current

            root = build_5c1_persistent_root(Path(tmp) / "mismatch", mutate_current=_bad)
            # mutate runs before social write rebind — force mismatch after bind
            current = read_json(root / "state/current.json")
            current["domain_refs"]["social_response_revision"] = "c" * 64
            write_runtime_json(root / "state/current.json", current)
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("social_response_revision", ctx.exception.detail)

    def test_08_social_graph_revision_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            social = social_with_responses("DECLINE")
            root = build_5c1_persistent_root(tmp, social=social)
            current = read_json(root / "state/current.json")
            self.assertNotEqual(
                current["domain_refs"]["social_graph_revision"],
                social.revision,
            )
            self.assertEqual(
                current["domain_refs"]["social_response_revision"],
                social.revision,
            )
            # Explicitly prove they are independent fields.
            self.assertIn("social_graph_revision", current["domain_refs"])
            self.assertIn("social_response_revision", current["domain_refs"])

    def test_09_semantic_state_round_trip_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            social = social_with_responses(
                build_response_row(response="DECLINE", opportunity_id="opp:a"),
                build_response_row(response="DEFER", opportunity_id="opp:b"),
            )
            root = build_5c1_persistent_root(tmp, social=social)
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.social_response_state.as_dict(), social.as_dict())
            reloaded = validate_social_response_state(
                read_json(root / SOCIAL_RESPONSE_RELPATH)
            )
            self.assertEqual(reloaded.as_dict(), social.as_dict())

    def test_10_decline_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            social = social_with_responses("DECLINE")
            root = build_5c1_persistent_root(tmp, social=social)
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(len(snap.social_response_state.responses), 1)
            self.assertEqual(snap.social_response_state.responses[0].response, "DECLINE")

    def test_11_defer_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            social = social_with_responses("DEFER")
            root = build_5c1_persistent_root(tmp, social=social)
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.social_response_state.responses[0].response, "DEFER")

    def test_12_terminal_accept_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            social = social_with_responses("ACCEPT")
            root = build_5c1_persistent_root(tmp, social=social)
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.social_response_state.responses[0].response, "ACCEPT")
            # Round-trip bytes via write/load remain identical.
            again = copy.deepcopy(snap.social_response_state.as_dict())
            self.assertEqual(again, social.as_dict())
