"""Execute the standalone synthetic provider consumer without test builders."""

from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys

import pytest

from engine.life.runtime_fact_provider import RuntimeFactProvider
from examples.provider_runtime.run import SyntheticFactProvider

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
