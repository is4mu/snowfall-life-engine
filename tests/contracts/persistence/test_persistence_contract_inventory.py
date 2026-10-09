"""Pre-v1 persisted artifact inventory checks against the released implementation.

This module guards *baseline evidence*; it does not promise any v0.x->1.x
reader or migration. P1 gate is tracked in public issue #9.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from engine.life import ENGINE_VERSION
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.operator_history import empty_operator_history, normalize_operator_history
from engine.life.runtime_bundle import REQUIRED_BUNDLE_RELPATHS
from engine.life.runtime_persistence import (
    SEMANTIC_FIXED_RELPATHS,
    is_allowed_runtime_relpath,
)
from engine.life.schema import SCHEMA_NAMES, load_schema_document
from engine.life.versioning import ensure_supported_engine

pytestmark = pytest.mark.contract

INVENTORY = Path(__file__).resolve().parents[3] / "oss" / "persistence-contract-inventory.json"


def _load() -> dict:
    return json.loads(INVENTORY.read_text(encoding="utf-8"))


def test_inventory_is_not_an_undocumented_v1_compatibility_promise() -> None:
    data = _load()
    assert data["status"] == "pre-v1-baseline-inventory-only"
    assert data["published_release"] == "v0.1.0"
    assert data["persisted_engine_version"] == ENGINE_VERSION
    assert data["v1_compatibility_guarantee"] is False
    families = [row["family"] for row in data["artifacts"]]
    assert len(families) == 16
    assert len(set(families)) == 16
    for row in data["artifacts"]:
        assert row["baseline_schema_version"] == 1
        assert row["v1_oldest_readable_schema_version"] is None
        assert row["v1_newest_writable_schema_version"] is None
        assert row["migration_entrypoint"] is None
        assert row["migration_status"] == "not_implemented"


@pytest.mark.parametrize("variant", ["correction_action", "recovery_action", "engine_upgrade_action", "policy_upgrade_action"])
def test_operator_action_union_variants_pin_schema_version_one(variant: str) -> None:
    schema = load_schema_document("operator_action")
    assert len(schema["oneOf"]) == 4
    definition = schema["$defs"][variant]
    assert "schema_version" in definition["required"]
    field = definition["properties"]["schema_version"]
    assert field["type"] == "integer"
    assert field["const"] == 1
    errors = list(Draft202012Validator(field).iter_errors(99))
    assert any(error.validator == "const" for error in errors)


def test_registered_persisted_schema_ids_and_version_rejections() -> None:
    data = _load()
    declared = set()
    for row in data["artifacts"]:
        schema_name = row["schema_name"]
        declared.add(schema_name)
        assert schema_name in SCHEMA_NAMES
        schema = load_schema_document(schema_name)
        assert schema["$id"] == (
            data["schema_id_prefix"] + SCHEMA_NAMES[schema_name]
        )
        if schema_name == "operator_action":
            continue  # its four versioned oneOf variants are tested separately
        assert "schema_version" in schema["required"]
        field = schema["properties"]["schema_version"]
        assert field == {"type": "integer", "const": 1}
        errors = list(Draft202012Validator(field).iter_errors(99))
        assert any(error.validator == "const" for error in errors)
    assert len(declared) == len(data["artifacts"])


def test_inventory_matches_mandatory_bundle_and_semantic_tree_paths() -> None:
    rows = _load()["artifacts"]
    assert {
        row["example_relpath"] for row in rows if row["required_for_bundle"]
    } == set(REQUIRED_BUNDLE_RELPATHS)
    assert {
        row["example_relpath"] for row in rows if row["fixed_semantic_path"]
    } == set(SEMANTIC_FIXED_RELPATHS)
    assert sum(row["required_for_full_snapshot"] for row in rows) == 8
    for row in rows:
        assert is_allowed_runtime_relpath(row["example_relpath"]), row
        if row["json_pointer"] is not None:
            assert row["family"] in {"actual_event", "camera_roll_record"}
            assert row["included_in_semantic_tree"]
    excluded = {
        row["example_relpath"]
        for row in rows
        if not row["included_in_semantic_tree"]
    }
    assert excluded == {"state/last-run.json", "state/last-operator-action.json"}


def test_operator_history_legacy_missing_version_default_is_explicit() -> None:
    # A historical special case exists in the code: a missing version for
    # this family is normalized as v1. Do not invent a uniform rejection
    # policy that would silently break this reader behavior.
    valid = empty_operator_history()
    no_version = dict(valid)
    no_version.pop("schema_version")
    assert normalize_operator_history(no_version) == valid


def test_future_engine_version_stays_unsupported_without_a_migration() -> None:
    with pytest.raises(LifeEngineError) as error:
        ensure_supported_engine("99.0.0-unrecognized")
    assert error.value.code == ErrorCode.UNSUPPORTED_VERSION
