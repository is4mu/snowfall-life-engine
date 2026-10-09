"""Execute the standalone synthetic provider consumer without test builders."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.policy import load_policy
from engine.life.runtime_fact_provider import RuntimeFactProvider, RuntimeTargetRequest
from engine.life.runtime_persistence import (
    build_runtime_candidate_tree_with_provider_factory,
    snapshot_tree_bytes,
)
from examples.provider_runtime.run import (
    AS_OF,
    ENGINE_SHA,
    LIFE_BASE_SHA,
    POLICY_FILE,
    SyntheticFactProvider,
    _reference_sets,
    _write_baseline,
)

pytestmark = pytest.mark.integration


def _execute(*, hashseed: str, tz: str) -> dict:
    env = dict(os.environ, PYTHONHASHSEED=hashseed, TZ=tz)
    result = subprocess.run(
        [sys.executable, "-m", "examples.provider_runtime.run"],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(result.stdout)


def test_provider_runtime_consumer_is_deterministic_across_process_environments() -> None:
    utc = _execute(hashseed="0", tz="UTC")
    tokyo = _execute(hashseed="8675309", tz="Asia/Tokyo")
    assert utc == tokyo
    assert utc["status"] == "PASS"
    assert utc["result"] == "CHANGE"
    assert utc["state_revision_after"] == 1
    assert utc["baseline_unchanged"]
    assert utc["byte_identical_replay"]
    assert len(utc["semantic_tree_hash"]) == 64
    assert set(utc["callback_sequence"]) == {
        "decision_inputs",
        "materialization_facts",
        "wakeup_facts",
        "capture_source_moments",
    }


def test_consumer_provider_matches_the_existing_keyword_only_protocol() -> None:
    """Detect protocol drift before any facade symbol is declared stable."""
    methods = (
        "decision_inputs",
        "materialization_facts",
        "wakeup_facts",
        "capture_source_moments",
    )
    for name in methods:
        proposed = inspect.signature(getattr(SyntheticFactProvider, name))
        protocol = inspect.signature(getattr(RuntimeFactProvider, name))
        assert list(proposed.parameters) == list(protocol.parameters), name
        assert all(
            parameter.kind == list(protocol.parameters.values())[i].kind
            for i, parameter in enumerate(proposed.parameters.values())
        ), name


def test_unexpected_provider_return_fails_closed_without_baseline_writes(tmp_path) -> None:
    """Malformed host output is rejected before any source-tree commit."""
    policy = load_policy(POLICY_FILE)
    baseline = tmp_path / "baseline"
    _write_baseline(baseline, policy["behavior_policy_version"])
    before = snapshot_tree_bytes(baseline)
    requested: list[object] = []

    class InvalidProvider(SyntheticFactProvider):
        def decision_inputs(self, **kwargs):
            requested.append(kwargs["request_context"].history_context_hash)
            return {"decision_facts": {}, "social_facts": {}}  # not the required result type

    def factory(snapshot):
        assert snapshot.bundle.current_state["character_id"] == "fixture-character"
        return InvalidProvider()

    with pytest.raises(LifeEngineError) as caught:
        build_runtime_candidate_tree_with_provider_factory(
            baseline_root=baseline,
            candidate_root=tmp_path / "rejected-candidate",
            reference_sets=_reference_sets(),
            behavior_policy=policy,
            target_request=RuntimeTargetRequest(
                target_time=AS_OF,
                event_budget=20,
                character_source_sha="synthetic-character-source-id",
            ),
            fact_provider_factory=factory,
            executing_engine_commit_sha=ENGINE_SHA,
            life_base_sha=LIFE_BASE_SHA,
        )
    assert caught.value.code is ErrorCode.INVALID_STATE
    assert len(requested) == 1
    assert snapshot_tree_bytes(baseline) == before
