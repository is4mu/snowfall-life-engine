"""CurrentState and RuntimeBundle public representation contracts."""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.checkpoint import empty_checkpoint
from engine.life.schema import load_schema_document
from tests.support.builders.runtime_bundle import CHARACTER_ID, ENGINE_SHA

pytestmark = pytest.mark.contract


class CurrentStateRuntimeContractTests(unittest.TestCase):
    def test_15_consumables_revision_schema_required(self) -> None:
        schema = load_schema_document("current_state")
        required = schema["properties"]["domain_refs"]["required"]
        self.assertIn("consumables_revision", required)
        props = schema["properties"]["domain_refs"]["properties"]
        self.assertIn("consumables_revision", props)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:  # pragma: no cover
            self.fail(f"current_state meta-invalid: {exc}")

    def test_16_empty_checkpoint_initializes_null(self) -> None:
        state = empty_checkpoint(
            character_id=CHAR,
            life_epoch="2026-04-01T00:00:00+09:00",
            world_seed="seed",
            behavior_policy_version="fixture-policy-1",
            engine_commit_sha=ENGINE_SHA,
            location_id="home",
            human_state={
                "sleep_debt_min": 0,
                "hunger": 0,
                "physical_fatigue": 0,
                "affect_valence": 0,
                "stress": 0,
                "social_battery": 0,
            },
        )
        self.assertIsNone(state["domain_refs"]["consumables_revision"])

    def test_17_exactly_six_human_state_fields(self) -> None:
        schema = load_schema_document("current_state")
        hs = schema["properties"]["human_state"]
        self.assertEqual(len(hs["required"]), 6)
        self.assertEqual(len(hs["properties"]), 6)

    def test_18_no_legacy_simulation_epoch_import(self) -> None:
        import engine.life.runtime_bundle as rb
        from engine.life import checkpoint as cp

        src = Path(inspect.getfile(rb)).read_text(encoding="utf-8")
        self.assertNotIn("simulation_epoch", src)
        # Forbidden paths are listed only as denylist constants / docs — never opened.
        self.assertIn("FORBIDDEN_LEGACY_RUNTIME_RELPATHS", src)
        self.assertNotRegex(src, r"Path\([^\)]*character\.json")
        self.assertNotRegex(src, r"open\([^\)]*character\.json")
        self.assertNotRegex(src, r"read_text\([^\)]*character\.json")
        cp_src = Path(inspect.getfile(cp)).read_text(encoding="utf-8")
        self.assertNotIn("data/runtime", cp_src)
        self.assertNotIn("simulation_epoch", cp_src)

