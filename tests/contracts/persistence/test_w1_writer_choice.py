"""Audit the owner-approved W1 first-v1 writer design without making it stable.

The decision JSON records a design choice; the other inventory files record
capabilities actually implemented/shipped. Do not conflate those semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.life import ENGINE_VERSION
from engine.life.schema import load_schema_document
from tools.persistence_capabilities import capability_table

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
DECISION = ROOT / "oss/p1-w1-initial-writer-decision.json"
INVENTORY = ROOT / "oss/persistence-contract-inventory.json"
GOLDEN = ROOT / "tests/fixtures/golden/w1-write-reload.experimental.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_owner_w1_design_is_exactly_current_sixteen_family_format() -> None:
    approved = _json(DECISION)
    inventory = _json(INVENTORY)
    assert approved["decision_record_version"] == 1
    assert approved["status"] == "owner-selected-design-not-yet-shipped"
    assert approved["first_stable_writer"] == "W1"
    assert approved["persisted_engine_epoch"] == ENGINE_VERSION == "0.1.0-foundation"
    assert {row["family"]: row["baseline_schema_version"] for row in inventory["artifacts"]} == approved["family_schema_versions"]
    assert len(approved["family_schema_versions"]) == 16
    assert set(approved["parent_issues"]) == {9, 16}
    for row in inventory["artifacts"]:
        assert approved["family_schema_versions"][row["family"]] == 1
        schema = load_schema_document(row["schema_name"])
        assert schema["$id"] == inventory["schema_id_prefix"] + row["schema_name"] + ".schema.json"


def test_selected_w1_does_not_register_migrations_or_pretend_to_ship_v1() -> None:
    approved = _json(DECISION)
    capabilities = capability_table()
    # Existing experimental table tracks *implemented v1 capability*, not the
    # owner's format-selection decision. A genuine RC requires a separate gate.
    assert capabilities["first_v1_writer"] is None
    assert capabilities["stable_v1_guarantee"] is False
    assert capabilities["migration_edges"] == []
    for row in capabilities["families"]:
        assert row["v1_writer_epoch"] is None
        assert row["v1_reader_epoch"] is None
    assert approved["future_w2_migration_registered"] is False
    assert approved["canonical_python_cli_contract_approved"] is False
    assert approved["stable_v1_reader_writer_implemented"] is False
    assert approved["release_candidate_validated"] is False
    assert approved["main_merge_authorized"] is False
    assert approved["release_authorized"] is False
    assert approved["automatic_load_migration"] is False
    assert approved["write_existing_save_in_place"] is False
    assert approved["next_writer_format"] == "not-selected-reconsider-only-if-concrete-incompatibility"


def test_w1_writer_choice_is_grounded_in_unchanged_synthetic_write_golden() -> None:
    report = _json(GOLDEN)
    approved = _json(DECISION)
    assert report["status"] == "PASS"
    assert report["scope"] == "pre-v1-W1-write-reload"
    assert report["state_revision"] == 3
    assert report["unreferenced_candidate"]
    assert report["restart_identical"]
    assert report["source_unchanged"]
    assert report["authority_unchanged"]
    assert report["stable_v1_guarantee"] is False
    assert len(report["action_ids"]) == 3
    assert len(report["file_sha256"]) == 16
    assert len(report["semantic_tree_hash"]) == 64
    assert approved["evidence"]["w1_replay"] == "tests/fixtures/golden/w1-write-reload.experimental.json"
