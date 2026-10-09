"""Pre-1.0 regression guards for persisted engine/schema version gates.

These tests verify the published baseline and do not promise a v0.x->1.x
migration before its implementation and compatibility review.
"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from engine.life import ENGINE_VERSION
from engine.life.checkpoint import empty_checkpoint, load_checkpoint, write_checkpoint
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.versioning import ensure_supported_engine, migrate_state

pytestmark = pytest.mark.contract


def _fixture_checkpoint() -> dict:
    return empty_checkpoint(
        character_id="fixture-version-gate",
        life_epoch="2026-01-01T00:00:00+09:00",
        world_seed="fixture-world-seed",
        behavior_policy_version="fixture-policy-1",
        engine_commit_sha="0" * 40,
        location_id="fixture-home",
        human_state={
            "sleep_debt_min": 0,
            "hunger": 0,
            "physical_fatigue": 0,
            "affect_valence": 0,
            "stress": 0,
            "social_battery": 0,
        },
    )


def test_baseline_checkpoint_version_is_explicit_and_readable(tmp_path) -> None:
    original = _fixture_checkpoint()
    assert original["engine_version"] == ENGINE_VERSION
    ensure_supported_engine(original["engine_version"])

    file_path = tmp_path / "checkpoint.json"
    write_checkpoint(file_path, original)
    source_bytes = file_path.read_bytes()
    loaded = load_checkpoint(file_path)

    assert loaded["engine_version"] == ENGINE_VERSION
    assert loaded["character_id"] == original["character_id"]
    assert file_path.read_bytes() == source_bytes


@pytest.mark.parametrize("unknown", ["99.0.0-future", "0.0.0-unknown"])
def test_unsupported_engine_version_fails_closed_without_rewriting_file(
    tmp_path, unknown: str
) -> None:
    state = _fixture_checkpoint()
    state["engine_version"] = unknown
    file_path = tmp_path / "checkpoint.json"
    file_path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    source_bytes = file_path.read_bytes()

    with pytest.raises(LifeEngineError) as err:
        load_checkpoint(file_path)
    assert err.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert file_path.read_bytes() == source_bytes


def test_future_schema_version_rejected_without_rewriting_file(tmp_path) -> None:
    state = _fixture_checkpoint()
    state["schema_version"] = 99
    file_path = tmp_path / "checkpoint.json"
    file_path.write_text(json.dumps(state, ensure_ascii=False) + "\n", encoding="utf-8")
    source_bytes = file_path.read_bytes()

    with pytest.raises(LifeEngineError) as err:
        load_checkpoint(file_path)
    assert err.value.code == ErrorCode.SCHEMA_INVALID
    assert file_path.read_bytes() == source_bytes


def test_missing_engine_version_cannot_migrate_implicitly() -> None:
    state = _fixture_checkpoint()
    state.pop("engine_version")
    untouched = deepcopy(state)

    with pytest.raises(LifeEngineError) as err:
        migrate_state(state)
    assert err.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert state == untouched


def test_unknown_engine_migration_does_not_modify_input_state() -> None:
    state = _fixture_checkpoint()
    state["engine_version"] = "99.0.0-future"
    untouched = deepcopy(state)

    with pytest.raises(LifeEngineError) as err:
        migrate_state(state)
    assert err.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert state == untouched
