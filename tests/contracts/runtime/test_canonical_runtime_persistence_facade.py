"""Contract tests for the *preview* canonical full RuntimeBundle facade.

Identity aliases deliberately retain exact implementation signatures and
failure semantics. None of these tests calls the facade a stable v1 API.
"""

from __future__ import annotations

import inspect
import json
import shutil
from pathlib import Path

import pytest

from engine.life import ErrorCode, LifeEngineError
from engine.life.activity_lifecycle import RuntimeWakeupProjectionFacts as ImplWakeupFacts
from engine.life.activity_materialization import (
    RuntimeActivityMaterializationFacts as ImplMaterializationFacts,
    RuntimeMaterializationContext as ImplMaterializationContext,
)
from engine.life.runtime_decision import (
    RuntimeDecisionFacts as ImplDecisionFacts,
    RuntimeDecisionFrame as ImplDecisionFrame,
    RuntimeDecisionTrigger as ImplDecisionTrigger,
)
from engine.life.social_runtime import SocialResponseState as ImplSocial
from engine.life.runtime_bundle import RuntimeBundle as ImplBundle, RuntimeReferenceSets as ImplReferences
from engine.life.runtime_fact_provider import (
    RuntimeDecisionProviderResult as ImplProviderResult,
    RuntimeFactProvider as ImplProvider,
    RuntimeFactRequestContext as ImplContext,
    RuntimeTargetRequest as ImplRequest,
    parse_runtime_target_request as impl_parse,
)
from engine.life.runtime_orchestrator import (
    RuntimeTargetAdvanceResult as ImplAdvanceResult,
    advance_runtime_to_target_with_provider as impl_advance,
)
from engine.life.runtime_persistence import (
    RuntimePersistentSnapshot as ImplSnapshot,
    RuntimePersistenceResult as ImplPersistenceResult,
    build_runtime_candidate_tree_with_provider_factory as impl_candidate,
    load_runtime_persistent_snapshot as impl_reader,
    snapshot_tree_bytes,
)
from engine.life.policy import load_policy
import snowfall_life.runtime as runtime
import snowfall_life.persistence as persistence
from examples.provider_runtime.run import (
    AS_OF,
    ENGINE_SHA,
    LIFE_BASE_SHA,
    POLICY_FILE,
    SyntheticFactProvider,
    _reference_sets,
    _write_baseline,
    demo,
)

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "tests/fixtures/golden/persistence-v0.1.0"
EXPECTED = json.loads((GOLDEN / "expected.json").read_text(encoding="utf-8"))


def test_exact_runtime_facade_export_and_signature_identity() -> None:
    expected = {
        "RuntimeBundle": ImplBundle,
        "RuntimeReferenceSets": ImplReferences,
        "RuntimeTargetRequest": ImplRequest,
        "RuntimeFactRequestContext": ImplContext,
        "RuntimeFactProvider": ImplProvider,
        "RuntimeDecisionProviderResult": ImplProviderResult,
        "RuntimeTargetAdvanceResult": ImplAdvanceResult,
        "RuntimeDecisionFacts": ImplDecisionFacts,
        "RuntimeDecisionFrame": ImplDecisionFrame,
        "RuntimeDecisionTrigger": ImplDecisionTrigger,
        "RuntimeMaterializationContext": ImplMaterializationContext,
        "RuntimeActivityMaterializationFacts": ImplMaterializationFacts,
        "RuntimeWakeupProjectionFacts": ImplWakeupFacts,
        "SocialResponseState": ImplSocial,
        "parse_runtime_target_request": impl_parse,
        "advance_runtime_to_target_with_provider": impl_advance,
    }
    assert set(runtime.__all__) == set(expected)
    for symbol, implementation in expected.items():
        exported = getattr(runtime, symbol)
        assert exported is implementation
        assert inspect.signature(exported) == inspect.signature(implementation)


def test_exact_persistence_facade_export_and_signature_identity() -> None:
    expected = {
        "RuntimePersistentSnapshot": ImplSnapshot,
        "RuntimePersistenceResult": ImplPersistenceResult,
        "load_runtime_persistent_snapshot": impl_reader,
        "build_runtime_candidate_tree_with_provider_factory": impl_candidate,
    }
    assert set(persistence.__all__) == set(expected)
    for symbol, implementation in expected.items():
        exported = getattr(persistence, symbol)
        assert exported is implementation
        assert inspect.signature(exported) == inspect.signature(implementation)


def test_provider_callback_contract_is_structural_and_keyword_only() -> None:
    for name in ("decision_inputs", "materialization_facts", "wakeup_facts", "capture_source_moments"):
        public = inspect.signature(getattr(runtime.RuntimeFactProvider, name))
        consumer = inspect.signature(getattr(SyntheticFactProvider, name))
        assert list(public.parameters) == list(consumer.parameters)
        assert list(public.parameters)[0] == "self"
        for value in list(public.parameters.values())[1:]:
            assert value.kind == inspect.Parameter.KEYWORD_ONLY
        assert all(consumer.parameters[name].kind == item.kind for name, item in public.parameters.items())


def test_runtime_request_parser_via_preview_remains_fail_closed_and_nonmutating() -> None:
    source = {
        "target_time": "2026-04-01T12:00:00+09:00",
        "event_budget": 0,
        "character_source_sha": "fixture-character-source",
    }
    before = dict(source)
    parsed = runtime.parse_runtime_target_request(source)
    assert parsed == runtime.RuntimeTargetRequest(**source)
    assert source == before
    invalid = [
        {**source, "event_budget": True},
        {**source, "event_budget": -1},
        {**source, "unexpected": "extra"},
        {key: value for key, value in source.items() if key != "character_source_sha"},
    ]
    for bad in invalid:
        with pytest.raises(LifeEngineError) as caught:
            runtime.parse_runtime_target_request(bad)
        assert caught.value.code is ErrorCode.INVALID_STATE
    assert source == before


def test_reference_sets_reject_coercion_and_call_context_is_detached() -> None:
    with pytest.raises(LifeEngineError) as caught:
        runtime.RuntimeReferenceSets(
            known_person_ids=["person-a"],
            known_home_entity_ids=set(),
            approved_wardrobe_item_ids=set(),
            approved_consumable_ids=set(),
        )
    assert caught.value.code is ErrorCode.INVALID_STATE
    context = runtime.RuntimeFactRequestContext()
    digest = context.history_context_hash
    detached = context.as_dict()
    detached["history_context_hash"] = "bad"
    detached["finalized_events_since_base"].append({"forged": True})
    assert context.history_context_hash == digest
    assert context.finalized_events_since_base == ()


def test_canonical_reader_validates_legacy_golden_without_rewriting(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    shutil.copytree(GOLDEN / "runtime", root)
    original = snapshot_tree_bytes(root)
    snapshot = persistence.load_runtime_persistent_snapshot(root, reference_sets=runtime.RuntimeReferenceSets(
        **{k: set(v) for k, v in json.loads((GOLDEN / "references.json").read_text()).items()}
    ))
    assert original == EXPECTED["file_sha256"]
    assert snapshot.semantic_tree_hash == EXPECTED["semantic_tree_hash"]
    assert snapshot.bundle.current_state["history"] == EXPECTED["history"]
    assert snapshot.operator_history == EXPECTED["operator_history"]
    assert snapshot_tree_bytes(root) == original


def test_canonical_reader_rejects_future_engine_without_repair(tmp_path: Path) -> None:
    root = tmp_path / "future"
    shutil.copytree(GOLDEN / "runtime", root)
    state_path = root / "state/current.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    data["engine_version"] = "99.0.0-future"
    state_path.write_text(json.dumps(data), encoding="utf-8")
    before = snapshot_tree_bytes(root)
    with pytest.raises(LifeEngineError) as caught:
        persistence.load_runtime_persistent_snapshot(root, reference_sets=runtime.RuntimeReferenceSets(
            **{k: set(v) for k, v in json.loads((GOLDEN / "references.json").read_text()).items()}
        ))
    assert caught.value.code is ErrorCode.UNSUPPORTED_VERSION
    assert snapshot_tree_bytes(root) == before


def test_canonical_candidate_rejects_nested_root_before_host_callback(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline"
    policy = load_policy(POLICY_FILE)
    _write_baseline(baseline, policy["behavior_policy_version"])
    before = snapshot_tree_bytes(baseline)
    calls = []
    def factory(_):
        calls.append("invoked")
        return SyntheticFactProvider()
    with pytest.raises(LifeEngineError) as caught:
        persistence.build_runtime_candidate_tree_with_provider_factory(
            baseline_root=baseline, candidate_root=baseline / "nested",
            reference_sets=_reference_sets(), behavior_policy=policy,
            target_request=runtime.RuntimeTargetRequest(
                target_time=AS_OF, event_budget=20,
                character_source_sha="fixture-source",
            ),
            fact_provider_factory=factory,
            executing_engine_commit_sha=ENGINE_SHA, life_base_sha=LIFE_BASE_SHA,
        )
    assert caught.value.code is ErrorCode.INVALID_STATE
    assert calls == []
    assert snapshot_tree_bytes(baseline) == before


def test_canonical_full_provider_consumer_replays_without_baseline_writes() -> None:
    # The real synthetic consumer now imports the candidate functions through
    # snowfall_life.runtime/persistence, not implementation-only entrypoints.
    result = demo()
    assert result["status"] == "PASS"
    assert result["result"] == "CHANGE"
    assert result["engine_version"] == "0.1.0-foundation"
    assert result["state_revision_after"] == 1
    assert result["baseline_unchanged"] and result["byte_identical_replay"]
    assert len(result["semantic_tree_hash"]) == 64
    assert set(result["callback_sequence"]) == {
        "decision_inputs",
        "materialization_facts",
        "wakeup_facts",
        "capture_source_moments",
    }
