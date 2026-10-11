"""Owner-approved v1 essentials scope is narrower than pre-v1 imports.

This is a candidate API list and a guard against silently widening it.
No stable ABI declaration or release authority is implied.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from engine.life import ENGINE_VERSION
from engine.life.schema import SCHEMA_NAMES
from snowfall_life import ErrorCode, LifeEngineError

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
SCOPE = ROOT / "oss/p1-v1-basic-api-scope.json"
DECISION = ROOT / "oss/p1-w1-initial-writer-decision.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_owner_approved_minimal_scope_is_not_a_shipped_api_or_abi() -> None:
    record = _read(SCOPE)
    assert record["record_version"] == 1
    assert record["status"] == "owner-approved-scope; exact-API-ABI-and-release-unapproved"
    assert record["principle"] == "stable-1.0-essentials-only; defer-complex-upgrade-and-recovery"
    for gate in (
        "exact_public_signatures_approved",
        "stable_cli_error_contract_approved",
        "release_candidate_passed",
        "stable_v1_published",
        "merge_main_authorized",
        "release_tag_authorized",
        "private_life_ref_publication_authorized",
    ):
        assert record[gate] is False


def test_candidate_core_public_whitelist_matches_current_preview_modules() -> None:
    candidates = _read(SCOPE)["candidate_public_python_modules"]
    assert set(candidates) == {
        "snowfall_life", "snowfall_life.schema",
        "snowfall_life.runtime", "snowfall_life.persistence", "snowfall_life.spatial",
    }
    for path, names in candidates.items():
        module = importlib.import_module(path)
        assert len(names) == len(set(names))
        assert set(names) == set(module.__all__), path
        for name in names:
            assert getattr(module, name) is not None, (path, name)
    assert ErrorCode.UNSUPPORTED_VERSION.value == "UNSUPPORTED_VERSION"
    assert issubclass(LifeEngineError, Exception)


def test_preview_upgrade_is_importable_but_excluded_from_core_stability() -> None:
    record = _read(SCOPE)
    assert "snowfall_life.upgrade" not in record["candidate_public_python_modules"]
    assert "snowfall_life.upgrade" in record["excluded_from_initial_stability"]
    from snowfall_life.upgrade import UpgradeEnvironmentAdapter
    assert hasattr(UpgradeEnvironmentAdapter, "probe_target_compatibility")
    assert "direct-Git-CAS-publication" in record["excluded_from_initial_stability"]
    assert "automatic-W2-migration" in record["excluded_from_initial_stability"]


def test_w1_persisted_epoch_families_and_parser_scope_unchanged() -> None:
    record, writer = _read(SCOPE), _read(DECISION)
    assert record["intended_first_v1_writer"] == writer["first_stable_writer"] == "W1"
    assert record["persisted_engine_epoch"] == ENGINE_VERSION == "0.1.0-foundation"
    assert record["stored_family_count"] == len(writer["family_schema_versions"]) == 16
    assert set(writer["family_schema_versions"].values()) == {record["stored_family_schema_version"]} == {1}
    assert len(SCHEMA_NAMES) == 37


def test_only_three_cli_diagnostics_are_basic_scope_candidates() -> None:
    record = _read(SCOPE)
    assert set(record["candidate_basic_cli"]) == {
        "self-check", "validate", "canonical-hash",
    }
    assert set(record["preview_or_sandbox_only_cli"]) == {
        "init-sandbox", "bootstrap-propose", "advance", "verify-workspace",
    }
    assert not (set(record["candidate_basic_cli"]) & set(record["preview_or_sandbox_only_cli"]))
    assert record["python_release_candidate_interpreter"] == "3.12"
