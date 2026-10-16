"""Pre-1.0 package facade and CLI smoke contracts.

These tests guard deliberate export scope without declaring 1.x stability.
"""

from __future__ import annotations

import pytest

import snowfall_life
from snowfall_life.cli import main as canonical_cli_main
from snowfall_life.schema import load_schema_document, validate_instance

from engine.life import ErrorCode, LifeEngineError, canonical_hash
from engine.life.cli import build_parser as legacy_build_parser
from engine.life.schema import SCHEMA_NAMES

pytestmark = pytest.mark.contract


def test_canonical_root_reexports_only_minimal_symbols() -> None:
    expected = {
        "ErrorCode",
        "LifeEngineError",
        "canonical_bytes",
        "canonical_hash",
        "canonical_json",
    }
    assert set(snowfall_life.__all__) == expected
    assert snowfall_life.ErrorCode is ErrorCode
    assert snowfall_life.LifeEngineError is LifeEngineError
    assert snowfall_life.canonical_hash is canonical_hash


def test_schema_facade_has_exactly_the_existing_public_schema_inventory() -> None:
    assert len(SCHEMA_NAMES) == 37
    for name in SCHEMA_NAMES:
        schema = load_schema_document(name)
        assert schema["$id"].startswith("https://snowfall-life-engine.invalid/schemas/life/")
    assert "current_state" in SCHEMA_NAMES


def test_canonical_cli_help_uses_new_prog_without_changing_legacy(capsys) -> None:
    assert legacy_build_parser().prog == "python3 -m engine.life.cli"
    with pytest.raises(SystemExit) as err:
        canonical_cli_main(["--help"])
    assert err.value.code == 0
    output = capsys.readouterr().out
    assert "usage: snowfall-life" in output
    assert "init-sandbox" in output


def test_canonical_self_check_keeps_current_engine_version(capsys) -> None:
    assert canonical_cli_main(["self-check"]) == 0
    assert "engine=0.1.0-foundation" in capsys.readouterr().out
