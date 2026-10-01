"""RuntimeBundle current-state and publication-boundary contracts."""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.checkpoint import empty_checkpoint
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_bundle import (
    RuntimeReferenceSets,
    load_runtime_bundle,
    validate_runtime_bundle,
)
from engine.life.schema import load_schema_document
from tests.support.builders.runtime_bundle import (
    CHAR,
    ENGINE_SHA,
    build_synthetic_bundle_root,
    provenance,
    reference_sets,
)
from tests.support.constants import POLICY_PATH

pytestmark = pytest.mark.contract


class CurrentStateRuntimeBundleContractTests(unittest.TestCase):
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

class RuntimeBundlePublicationBoundaryTests(unittest.TestCase):
    def test_38_no_production_state_current_json(self) -> None:
        repo_root = Path(inspect.getfile(load_runtime_bundle)).resolve().parents[2]
        self.assertFalse((repo_root / "state" / "current.json").exists())

    def test_39_no_production_schedule_state_json(self) -> None:
        repo_root = Path(inspect.getfile(load_runtime_bundle)).resolve().parents[2]
        self.assertFalse((repo_root / "schedule" / "state.json").exists())

    def test_40_no_life_branch_workflow_code(self) -> None:
        repo_root = Path(inspect.getfile(load_runtime_bundle)).resolve().parents[2]
        workflows = repo_root / ".github" / "workflows"
        if workflows.is_dir():
            for path in workflows.rglob("*"):
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    self.assertNotIn("life-heartbeat", text.lower())
        # Public runtime modules must not create life branch APIs.
        import engine.life.runtime_bundle as rb
        import engine.life.schedule_state as ss

        for mod in (rb, ss):
            src = Path(inspect.getfile(mod)).read_text(encoding="utf-8")
            self.assertNotIn("git.Repo", src)
            self.assertNotIn("subprocess", src)
            self.assertNotIn("write_checkpoint", src)

    def test_41_no_behavior_policy_change(self) -> None:
        # Public runtime loading must not depend on private policy approval state.
        # Assert the synthetic public policy fixture remains available.
        self.assertTrue(POLICY_PATH.is_file())

    def test_43_no_new_dependency(self) -> None:
        import engine.life.runtime_bundle as rb
        req = Path(inspect.getfile(rb)).with_name("requirements.txt").read_text(encoding="utf-8")
        self.assertIn("jsonschema", req)
        # Still only the documented Foundation dependency line family.
        lines = [ln.strip() for ln in req.splitlines() if ln.strip() and not ln.startswith("#")]
        self.assertLessEqual(len(lines), 3)

    def test_44_no_dashboard_implementation(self) -> None:
        import engine.life.runtime_bundle as rb

        src = Path(inspect.getfile(rb)).read_text(encoding="utf-8")
        self.assertNotIn("dashboard", src.lower())
        self.assertNotIn("flask", src.lower())
        self.assertNotIn("fastapi", src.lower())

    def test_reference_sets_reject_duplicates_and_bad_types(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            RuntimeReferenceSets(
                known_person_ids=["a", "a"],  # type: ignore[arg-type]
                known_home_entity_ids=frozenset(),
                approved_wardrobe_item_ids=frozenset(),
                approved_consumable_ids=frozenset(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertIn("set/frozenset", ctx.exception.detail)

        with self.assertRaises(LifeEngineError) as ctx:
            RuntimeReferenceSets(
                known_person_ids=("a",),  # type: ignore[arg-type]
                known_home_entity_ids=frozenset(),
                approved_wardrobe_item_ids=frozenset(),
                approved_consumable_ids=frozenset(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

        with self.assertRaises(LifeEngineError) as ctx:
            RuntimeReferenceSets(
                known_person_ids={""},
                known_home_entity_ids=set(),
                approved_wardrobe_item_ids=set(),
                approved_consumable_ids=set(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

        with self.assertRaises(LifeEngineError) as ctx:
            RuntimeReferenceSets(
                known_person_ids=123,  # type: ignore[arg-type]
                known_home_entity_ids=set(),
                approved_wardrobe_item_ids=set(),
                approved_consumable_ids=set(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_reference_sets_malformed_element_is_life_engine_error(self) -> None:
        """Non-string elements must fail via LifeEngineError, not raw TypeError."""
        with self.assertRaises(LifeEngineError) as ctx:
            RuntimeReferenceSets(
                known_person_ids={42},  # type: ignore[arg-type]
                known_home_entity_ids=frozenset(),
                approved_wardrobe_item_ids=frozenset(),
                approved_consumable_ids=frozenset(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

        with self.assertRaises(LifeEngineError) as ctx:
            RuntimeReferenceSets(
                known_person_ids={None},  # type: ignore[arg-type]
                known_home_entity_ids=frozenset(),
                approved_wardrobe_item_ids=frozenset(),
                approved_consumable_ids=frozenset(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_validate_runtime_bundle_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_synthetic_bundle_root(Path(tmp))
            mapping = {
                "current_state": json.loads(
                    (root / "state/current.json").read_text(encoding="utf-8")
                ),
                "schedule_state": json.loads(
                    (root / "schedule/state.json").read_text(encoding="utf-8")
                ),
                "relation_state": json.loads(
                    (root / "relations/state.json").read_text(encoding="utf-8")
                ),
                "home_state": json.loads((root / "home/state.json").read_text(encoding="utf-8")),
                "consumables_state": json.loads(
                    (root / "consumables/state.json").read_text(encoding="utf-8")
                ),
                "wardrobe_state": json.loads(
                    (root / "wardrobe/state.json").read_text(encoding="utf-8")
                ),
                "finance_state": json.loads(
                    (root / "finance/state.json").read_text(encoding="utf-8")
                ),
            }
            bundle = validate_runtime_bundle(mapping, reference_sets=reference_sets())
            self.assertEqual(bundle.finance_state["domain"], "finance")
