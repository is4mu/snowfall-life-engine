"""Review-only integrated v1 candidate explicitly forbids auto release."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[3]


def test_integration_draft_is_cumulative_review_only() -> None:
    data = json.loads((ROOT / "oss/p1-v1-integration-review.json").read_text())
    assert data["record_version"] == 1
    assert data["status"] == "draft-stacked-integration-candidate-NOT-stable-or-releasable"
    assert len(data["reviewed_main_baseline_sha"]) == 40
    assert len(data["accumulated_source_pr_head"]) == 40
    assert data["source_pr_numbers"][-3:] == [25, 26, 27]
    assert data["source_pr_numbers"][0] == 7
    assert data["owner_approved_direction"]["persistence_writer"] == "W1"
    assert data["owner_approved_direction"]["persisted_engine_epoch"] == "0.1.0-foundation"
    assert data["owner_approved_direction"]["persisted_families"] == 16
    assert data["owner_approved_direction"]["schema_version"] == 1
    assert data["packaged_release_version_not_changed"] == "0.2.0.dev0"
    assert data["unpublished_private_rc_metadata"] == "1.0.0rc1"
    assert len(data["verified_candidate_modules"]) == 5
    assert "snowfall_life.upgrade" not in data["verified_candidate_modules"]


def test_no_stabilization_merge_release_or_live_publish_allowed_by_record() -> None:
    data = json.loads((ROOT / "oss/p1-v1-integration-review.json").read_text())
    for key in (
        "api_abi_signatures_approved",
        "cli_error_contract_approved",
        "python_support_promise_approved",
        "legacy_root_1x_support_window_approved",
        "reconciled_integration_head_approved",
        "final_exact_head_rc_validated",
        "main_merge_authorized",
        "package_release_authorized",
        "production_life_ref_publish_authorized",
    ):
        assert data[key] is False


def test_integration_head_keeps_old_package_version_and_schemas() -> None:
    project = (ROOT / "pyproject.toml").read_text()
    assert 'version = "0.2.0.dev0"' in project
    assert 'version = "1.0.0rc1"' not in project
    from engine.life import ENGINE_VERSION, SUPPORTED_SCHEMA_VERSION
    from engine.life.schema import SCHEMA_NAMES
    assert ENGINE_VERSION == "0.1.0-foundation"
    assert SUPPORTED_SCHEMA_VERSION == 1
    assert len(SCHEMA_NAMES) == 37
