"""Registered schema document isolation and public failure semantics."""

from __future__ import annotations

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.schema import load_schema_document

pytestmark = pytest.mark.contract


def test_schema_document_lookup_returns_an_independent_view() -> None:
    first = load_schema_document("current_state")
    original_id = first["$id"]
    assert first["properties"]["schema_version"]["const"] == 1

    first["$id"] = "altered by caller"
    first["properties"]["schema_version"]["const"] = 999

    second = load_schema_document("current_state")
    assert second["$id"] == original_id
    assert second["properties"]["schema_version"]["const"] == 1


def test_unknown_schema_name_fails_closed() -> None:
    with pytest.raises(LifeEngineError) as err:
        load_schema_document("not_a_public_schema")
    assert err.value.code == ErrorCode.SCHEMA_INVALID
