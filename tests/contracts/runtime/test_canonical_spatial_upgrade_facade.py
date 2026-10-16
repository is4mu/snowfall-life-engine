"""Proof of narrow pre-v1 canonical spatial and upgrade host boundaries."""

from __future__ import annotations

from copy import deepcopy
import inspect
from pathlib import Path

import pytest

from engine.life import ErrorCode, LifeEngineError
from engine.life.spatial_context import (
    build_spatial_context as impl_build,
    validate_spatial_context as impl_validate,
)
from engine.life.runtime_spatial import (
    project_spatial_runtime_decision_facts as impl_decision,
    project_spatial_runtime_target_inputs as impl_target,
)
from engine.life.operator_upgrade import (
    CheckoutPolicyMaterial as ImplMaterial,
    CompatibilityProbeResult as ImplProbeResult,
    UpgradeEnvironmentAdapter as ImplAdapter,
    run_target_compatibility_probe as impl_probe,
)
from examples.provider_runtime.run import AS_OF, _decision_facts
import snowfall_life.spatial as spatial
import snowfall_life.upgrade as upgrade

pytestmark = pytest.mark.contract


def test_spatial_export_whitelist_preserves_implementation_signatures() -> None:
    aliases = {
        "build_spatial_context": impl_build,
        "validate_spatial_context": impl_validate,
        "project_spatial_runtime_decision_facts": impl_decision,
        "project_spatial_runtime_target_inputs": impl_target,
    }
    assert set(spatial.__all__) == set(aliases)
    for name, function in aliases.items():
        assert getattr(spatial, name) is function
        assert inspect.signature(getattr(spatial, name)) == inspect.signature(function)


def test_upgrade_export_whitelist_preserves_existing_probe_and_protocol() -> None:
    aliases = {
        "UpgradeEnvironmentAdapter": ImplAdapter,
        "CheckoutPolicyMaterial": ImplMaterial,
        "CompatibilityProbeResult": ImplProbeResult,
        "run_target_compatibility_probe": impl_probe,
    }
    assert set(upgrade.__all__) == set(aliases)
    for name, symbol in aliases.items():
        assert getattr(upgrade, name) is symbol
        assert inspect.signature(getattr(upgrade, name)) == inspect.signature(symbol)
    required = (
        "load_checkout_policy_material",
        "materialize_pinned_engine_checkout",
        "cleanup_materialized_checkout",
        "probe_target_compatibility",
    )
    for name in required:
        assert callable(getattr(upgrade.UpgradeEnvironmentAdapter, name))


def test_empty_sealed_spatial_data_and_facts_projection_preserve_input() -> None:
    context = spatial.build_spatial_context(
        character_id="fixture-character", spatial_context_version="fixture-v1"
    )
    original_context = deepcopy(context)
    assert spatial.validate_spatial_context(context) == context
    facts = _decision_facts()
    facts.pop("route_profiles")
    original_facts = deepcopy(facts)
    projected = spatial.project_spatial_runtime_decision_facts(
        spatial_context=context,
        character_id="fixture-character",
        facts_without_routes=facts,
        now=AS_OF,
    )
    assert projected.route_profiles == ()
    assert context == original_context
    assert facts == original_facts


def test_spatial_override_is_rejected_even_when_empty_and_source_not_mutated() -> None:
    context = spatial.build_spatial_context(
        character_id="fixture-character", spatial_context_version="fixture-v1"
    )
    facts = _decision_facts()  # has a caller-supplied (empty) route_profiles
    original = deepcopy(facts)
    with pytest.raises(LifeEngineError) as caught:
        spatial.project_spatial_runtime_decision_facts(
            spatial_context=context, character_id="fixture-character",
            facts_without_routes=facts, now=AS_OF,
        )
    assert caught.value.code is ErrorCode.INVALID_STATE
    assert facts == original


def test_spatial_context_hash_and_character_identity_fail_closed_without_reseal() -> None:
    context = spatial.build_spatial_context(
        character_id="fixture-character", spatial_context_version="fixture-v1"
    )
    wrong = deepcopy(context)
    wrong["context_hash"] = "0" * 64
    frozen = deepcopy(wrong)
    with pytest.raises(LifeEngineError) as caught:
        spatial.validate_spatial_context(wrong)
    assert caught.value.code is ErrorCode.INVALID_STATE
    assert wrong == frozen
    with pytest.raises(LifeEngineError) as err:
        spatial.project_spatial_runtime_decision_facts(
            spatial_context=context, character_id="other-character",
            facts_without_routes={}, now=AS_OF,
        )
    assert err.value.code is ErrorCode.INVALID_STATE


def test_upgrade_probe_rejects_incomplete_host_before_any_io(tmp_path: Path) -> None:
    before = list(tmp_path.iterdir())
    with pytest.raises(LifeEngineError) as caught:
        upgrade.run_target_compatibility_probe(
            engine_source_repo=tmp_path / "does-not-exist",
            target_engine_commit_sha="a" * 40,
            life_files={},
            upgrade_adapter=object(),
        )
    assert caught.value.code is ErrorCode.INVALID_STATE
    assert "upgrade_adapter." in caught.value.detail
    assert list(tmp_path.iterdir()) == before


def test_upgrade_probe_rejects_invalid_commit_pin_before_host_callback(tmp_path: Path) -> None:
    calls: list[str] = []

    class NoopHost:
        def load_checkout_policy_material(self, checkout):
            calls.append("material")
        def materialize_pinned_engine_checkout(self, **kwargs):
            calls.append("checkout")
        def cleanup_materialized_checkout(self, **kwargs):
            calls.append("cleanup")
        def probe_target_compatibility(self, **kwargs):
            calls.append("probe")

    with pytest.raises(LifeEngineError) as caught:
        upgrade.run_target_compatibility_probe(
            engine_source_repo=tmp_path / "does-not-exist",
            target_engine_commit_sha="invalid",
            life_files={"state/current.json": b"{}"},
            upgrade_adapter=NoopHost(),
        )
    assert caught.value.code is ErrorCode.INVALID_STATE
    assert calls == []
    assert list(tmp_path.iterdir()) == []
