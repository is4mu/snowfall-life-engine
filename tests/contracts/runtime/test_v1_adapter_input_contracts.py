"""Existing fact-request and SpatialContext contracts: fail closed, stay pure.

No stable 1.0 facade is declared by exercising these implementation paths.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from engine.life.canonical import canonical_hash
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_fact_provider import (
    RuntimeFactRequestContext,
    parse_runtime_target_request,
)
from engine.life.spatial_context import build_spatial_context, validate_spatial_context

pytestmark = pytest.mark.contract


def _valid_request() -> dict:
    return {
        "target_time": "2026-01-01T00:00:00+09:00",
        "event_budget": 0,
        "character_source_sha": "fixture-character-source",
    }


def test_fact_target_request_explicit_and_non_mutating() -> None:
    original = _valid_request()
    before = deepcopy(original)
    parsed = parse_runtime_target_request(original)

    assert parsed.target_time == before["target_time"]
    assert parsed.event_budget == 0
    assert parsed.character_source_sha == before["character_source_sha"]
    assert original == before


@pytest.mark.parametrize(
    "changes",
    [
        {"event_budget": True},
        {"event_budget": -1},
        {"event_budget": 1.5},
        {"character_source_sha": ""},
        {"unexpected": "not allowed"},
    ],
)
def test_fact_target_request_rejects_invalid_or_unknown_fields(changes: dict) -> None:
    invalid = _valid_request()
    invalid.update(changes)
    with pytest.raises(LifeEngineError) as err:
        parse_runtime_target_request(invalid)
    assert err.value.code == ErrorCode.INVALID_STATE


def test_fact_target_request_rejects_missing_required_field() -> None:
    invalid = _valid_request()
    invalid.pop("character_source_sha")
    with pytest.raises(LifeEngineError) as err:
        parse_runtime_target_request(invalid)
    assert err.value.code == ErrorCode.INVALID_STATE


def test_call_local_context_is_hash_stable_and_returns_detached_views() -> None:
    context = RuntimeFactRequestContext()
    expected = canonical_hash({"finalized_events_since_base": []})
    assert context.history_context_hash == expected
    assert context.finalized_events_since_base == ()

    external = context.as_dict()
    external["finalized_events_since_base"].append({"fabricated": True})
    external["history_context_hash"] = "changed"

    assert context.history_context_hash == expected
    assert context.finalized_events_since_base == ()
    assert context.as_dict()["finalized_events_since_base"] == []


def test_call_local_context_requires_a_sequence_of_events() -> None:
    with pytest.raises(LifeEngineError) as err:
        RuntimeFactRequestContext({"unexpected": "mapping"})
    assert err.value.code == ErrorCode.INVALID_STATE


def test_empty_spatial_context_is_sealed_deterministically() -> None:
    first = build_spatial_context(
        character_id="fixture-character",
        spatial_context_version="fixture-spatial-v1",
    )
    second = build_spatial_context(
        character_id="fixture-character",
        spatial_context_version="fixture-spatial-v1",
        locations=[],
        route_profiles=[],
    )
    assert first == second
    assert validate_spatial_context(first) == first
    assert first["locations"] == []
    assert first["route_profiles"] == []


def test_spatial_validation_rejects_bad_hash_without_mutating_input() -> None:
    original = build_spatial_context(
        character_id="fixture-character",
        spatial_context_version="fixture-spatial-v1",
    )
    tampered = deepcopy(original)
    tampered["character_id"] = "another-character"
    before = deepcopy(tampered)

    with pytest.raises(LifeEngineError) as err:
        validate_spatial_context(tampered)
    assert err.value.code == ErrorCode.INVALID_STATE
    assert tampered == before


def test_spatial_validation_rejects_bool_schema_version() -> None:
    context = build_spatial_context(
        character_id="fixture-character",
        spatial_context_version="fixture-spatial-v1",
    )
    context["schema_version"] = True
    with pytest.raises(LifeEngineError) as err:
        validate_spatial_context(context)
    assert err.value.code == ErrorCode.INVALID_STATE
