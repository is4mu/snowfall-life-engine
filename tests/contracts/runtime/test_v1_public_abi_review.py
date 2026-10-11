"""Tests for the proposed exact v1 ABI without declaring stable release.

The review-only manifest pins function parameter names/kinds/required-optional
structure, Provider callbacks, and key frozen result shapes. Private RC CI
repeats the same checks under installed wheel and sdist and compares digests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.p1_api_abi_probe import inspect_proposal

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[3]
SPEC = ROOT / "oss/p1-v1-abi-review.json"
SCOPE = ROOT / "oss/p1-v1-basic-api-scope.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_candidate_exact_abi_manifest_matches_experimental_core_runtime() -> None:
    evidence = inspect_proposal(_load(SPEC), _load(SCOPE))
    assert evidence["status"] == "PASS"
    assert evidence["scope"] == "candidate-v1-abi-review-only"
    assert evidence["module_count"] == 5
    assert evidence["functions_verified"] == 13
    assert evidence["provider_callbacks_verified"] == 4
    assert evidence["frozen_shapes_verified"] == 7
    assert len(evidence["candidate_signature_sha256"]) == 64
    assert evidence["w1_engine_epoch"] == "0.1.0-foundation"
    assert evidence["experimental_upgrade_excluded"]
    assert evidence["api_approved"] is False


def test_proposed_abi_preserves_nonstable_and_published_boundaries() -> None:
    review = _load(SPEC)
    scope = _load(SCOPE)
    assert review["status"] == "review-proposal-exact-ABI-NOT-APPROVED"
    assert review["legacy_engine_life_root_candidate"].startswith("keep-v0.1.0-root-imports")
    assert review["supported_python_candidate"] == "3.12.x"
    assert review["storage_writer"] == scope["intended_first_v1_writer"] == "W1"
    assert review["schema_families"] == 16
    assert set(review["cli_stability_candidates"]) == set(scope["candidate_basic_cli"])
    assert len(review["excluded_stable_imports"]) >= 4
    for gate in (
        "exact_signatures_approved", "cli_error_contract_approved",
        "python_support_window_approved", "legacy_import_support_window_approved",
        "final_rc_approved", "main_merge_authorized", "release_authorized",
    ):
        assert review[gate] is False


def test_broken_abi_spec_does_not_silently_pass() -> None:
    review = _load(SPEC)
    scope = _load(SCOPE)
    name = "snowfall_life.persistence.load_runtime_persistent_snapshot"
    review["function_parameter_candidates"][name][1]["kind"] = "POSITIONAL_OR_KEYWORD"
    with pytest.raises(AssertionError, match="proposed ABI drift"):
        inspect_proposal(review, scope)


def test_missing_provider_callback_is_rejected() -> None:
    review = _load(SPEC)
    scope = _load(SCOPE)
    review["provider_protocol_keyword_only_candidates"].pop("wakeup_facts")
    with pytest.raises(AssertionError, match="Provider callback set"):
        inspect_proposal(review, scope)


def test_a_new_extra_dataclass_field_needs_review() -> None:
    review = _load(SPEC)
    scope = _load(SCOPE)
    review["frozen_dataclass_field_candidates"]["snowfall_life.runtime.RuntimeTargetRequest"].append("unstable_extra")
    with pytest.raises(AssertionError, match="data shape drift"):
        inspect_proposal(review, scope)
