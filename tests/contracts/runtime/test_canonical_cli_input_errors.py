"""Narrow canonical JSON diagnostic error behavior is a v1 *candidate* only.

Legacy engine.life.cli is unchanged, and Foundation sandbox commands retain
their original exception behavior. None of this approves a stable CLI ABI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.life import ErrorCode, LifeEngineError
from engine.life.cli import main as legacy_main
from snowfall_life.cli import main as canonical_main

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
DECISION = ROOT / "oss/p1-v1-cli-error-review.json"
GOLDEN = ROOT / "tests/fixtures/golden/persistence-v0.1.0/runtime/state/current.json"


def test_cli_error_review_record_has_no_stability_or_release_grants() -> None:
    record = json.loads(DECISION.read_text(encoding="utf-8"))
    assert record["record_version"] == 1
    assert record["status"] == "implementation-review-candidate-NOT-stable"
    assert set(record["normalized_only"]) == {"validate", "canonical-hash"}
    assert record["initial_python_floor_candidate"] == "3.12.x"
    assert len(record["old_root_exports"]) == 8
    assert record["no_legacy_cli_change"]
    for key in (
        "exact_cli_contract_approved", "python_3_12_support_approved",
        "old_import_1x_support_approved", "final_v1_rc_approved",
        "merge_main_authorized", "release_authorized",
    ):
        assert record[key] is False


@pytest.mark.parametrize(
    ("command", "content", "expected_code"),
    [
        ("validate", '{"bad":', ErrorCode.SCHEMA_INVALID),
        ("canonical-hash", '{"bad":', ErrorCode.INVALID_STATE),
        ("validate", b"\xff", ErrorCode.SCHEMA_INVALID),
        ("canonical-hash", b"\xff", ErrorCode.INVALID_STATE),
    ],
)
def test_canonical_invalid_json_and_utf8_fail_without_traceback_or_write(
    tmp_path: Path, capsys, command, content, expected_code
) -> None:
    path = tmp_path / "input.json"
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    old = path.read_bytes()
    args = (
        ["validate", str(path), "--schema", "current_state"]
        if command == "validate" else ["canonical-hash", str(path)]
    )
    assert canonical_main(args) == 2
    result = capsys.readouterr()
    assert result.stdout == ""
    assert result.stderr == f"ERROR {expected_code.value}: invalid JSON or UTF-8 input\n"
    assert path.read_bytes() == old


@pytest.mark.parametrize("command", ["validate", "canonical-hash"])
def test_canonical_missing_file_reports_typed_error_without_creating_path(
    tmp_path: Path, capsys, command: str
) -> None:
    absent = tmp_path / "missing.json"
    args = (
        ["validate", str(absent), "--schema", "current_state"]
        if command == "validate" else ["canonical-hash", str(absent)]
    )
    assert canonical_main(args) == 2
    text = capsys.readouterr()
    assert text.stdout == ""
    assert text.stderr == "ERROR INVALID_STATE: unable to read JSON input\n"
    assert not absent.exists()


def test_canonical_directory_input_is_not_a_valid_file(tmp_path: Path, capsys) -> None:
    assert canonical_main(["validate", str(tmp_path), "--schema", "current_state"]) == 2
    out = capsys.readouterr()
    assert out.stdout == ""
    assert out.stderr == "ERROR INVALID_STATE: unable to read JSON input\n"


def test_old_legacy_cli_does_not_gain_canonical_error_translation(tmp_path: Path) -> None:
    broken = tmp_path / "bad.json"
    broken.write_text('{"bad":', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        legacy_main(["validate", str(broken), "--schema", "current_state"])
    with pytest.raises(FileNotFoundError):
        legacy_main(["canonical-hash", str(tmp_path / "absent.json")])


def test_old_engine_life_root_exports_alias_current_canonical_basics() -> None:
    import engine.life as old
    import snowfall_life as new
    expected = {
        "ENGINE_VERSION", "SUPPORTED_ENGINE_VERSIONS", "SUPPORTED_SCHEMA_VERSION",
        "ErrorCode", "LifeEngineError", "canonical_bytes",
        "canonical_hash", "canonical_json",
    }
    assert set(old.__all__) == expected
    assert old.ENGINE_VERSION == "0.1.0-foundation"
    assert old.SUPPORTED_ENGINE_VERSIONS == frozenset({old.ENGINE_VERSION})
    assert old.SUPPORTED_SCHEMA_VERSION == 1
    for symbol in new.__all__:
        assert getattr(new, symbol) is getattr(old, symbol)
    assert old.LifeEngineError is LifeEngineError


def test_canonical_w1_schema_validation_success_is_read_only(capsys) -> None:
    before = GOLDEN.read_bytes()
    assert canonical_main(["validate", str(GOLDEN), "--schema", "current_state"]) == 0
    out = capsys.readouterr()
    assert out.stdout.strip() == "OK schema=current_state"
    assert out.stderr == ""
    assert GOLDEN.read_bytes() == before
