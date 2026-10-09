"""Pre-v1 canonical Foundation CLI observable behavior; not a stable wire API.

The provider-backed full RuntimeBundle candidate is intentionally *not* a CLI
command. These tests pin current exit/output behavior for design review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from engine.life import ErrorCode
from engine.life.canonical import canonical_hash, persisted_hash
from snowfall_life.cli import main
from engine.life.cli import build_parser

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[3]
W1_CURRENT = ROOT / "tests/fixtures/golden/persistence-v0.1.0/runtime/state/current.json"
FOUNDATION_POLICY = ROOT / "examples/foundation_sandbox/policy.json"
EPOCH = "2026-01-01T00:00:00+09:00"


def test_preview_cli_contains_exact_seven_foundation_commands() -> None:
    parser = build_parser(prog="snowfall-life")
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert len(actions) == 1
    assert set(actions[0].choices) == {
        "validate",
        "canonical-hash",
        "bootstrap-propose",
        "advance",
        "verify-workspace",
        "self-check",
        "init-sandbox",
    }
    assert "provider" not in actions[0].choices


def test_no_command_is_argparse_exit_2_not_provider_action(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2
    streams = capsys.readouterr()
    assert streams.out == ""
    assert "usage: snowfall-life" in streams.err
    assert "required" in streams.err


def test_self_check_is_text_and_keeps_foundation_epoch(capsys) -> None:
    assert main(["self-check"]) == 0
    streams = capsys.readouterr()
    assert "OK self-check engine=0.1.0-foundation" in streams.out
    assert streams.err == ""


def test_validate_success_readonly_against_pinned_w1_golden(capsys) -> None:
    before = W1_CURRENT.read_bytes()
    assert main(["validate", str(W1_CURRENT), "--schema", "current_state"]) == 0
    streams = capsys.readouterr()
    assert streams.out.strip() == "OK schema=current_state"
    assert streams.err == ""
    assert W1_CURRENT.read_bytes() == before


def test_validate_invalid_schema_reports_error_code_with_exit_2(tmp_path, capsys) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}\n", encoding="utf-8")
    before = invalid.read_bytes()
    assert main(["validate", str(invalid), "--schema", "current_state"]) == 2
    streams = capsys.readouterr()
    assert streams.out == ""
    assert streams.err.startswith("ERROR " + ErrorCode.SCHEMA_INVALID.value + ": ")
    assert invalid.read_bytes() == before


def test_canonical_hash_command_text_raw_vs_normalized_and_no_rewrite(tmp_path, capsys) -> None:
    fixture = {"name": "fixture", "counter": 1}
    path = tmp_path / "data.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    before = path.read_bytes()
    assert main(["canonical-hash", str(path), "--raw"]) == 0
    raw = capsys.readouterr()
    assert raw.out.strip() == canonical_hash(fixture)
    assert raw.err == ""
    assert main(["canonical-hash", str(path)]) == 0
    persisted = capsys.readouterr()
    assert persisted.out.strip() == persisted_hash(fixture)
    assert persisted.err == ""
    assert path.read_bytes() == before


def test_init_sandbox_is_explicit_synthetic_and_verify_workspace(tmp_path, capsys) -> None:
    sandbox = tmp_path / "sandbox"
    assert main(["init-sandbox", "--workspace", str(sandbox)]) == 0
    text = capsys.readouterr()
    assert "wrote " in text.out
    assert text.err == ""
    checkpoint = sandbox / "current_state.json"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["engine_version"] == "0.1.0-foundation"
    assert saved["character_id"] == "fixture-character"
    assert checkpoint.is_file()
    before = checkpoint.read_bytes()
    assert main(["verify-workspace", "--workspace", str(sandbox)]) == 0
    checked = capsys.readouterr()
    assert checked.out.strip() == "OK workspace history verified"
    assert checked.err == ""
    assert checkpoint.read_bytes() == before


def test_bootstrap_propose_writes_only_explicit_synthetic_output(tmp_path, capsys) -> None:
    output = tmp_path / "synthetic-proposal.json"
    assert main(["bootstrap-propose", "--output", str(output)]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    assert len(streams.out.strip()) == 64
    value = json.loads(output.read_text(encoding="utf-8"))
    assert value["status"] == "PROPOSED"
    assert value["notes"] == "SYNTHETIC_FIXTURE_ONLY"
    assert value["engine_version"] == "0.1.0-foundation"
    assert value["character_id"] == "fixture-character"
    assert set(p.name for p in tmp_path.iterdir()) == {"synthetic-proposal.json"}


def test_foundation_advance_is_json_and_same_target_is_noop(tmp_path, capsys) -> None:
    sandbox = tmp_path / "sandbox"
    assert main(["init-sandbox", "--workspace", str(sandbox)]) == 0
    capsys.readouterr()
    target = "2026-01-01T00:05:00+09:00"
    args = [
        "advance", "--workspace", str(sandbox),
        "--target", target, "--policy", str(FOUNDATION_POLICY),
    ]
    assert main(args) == 0
    first = capsys.readouterr()
    assert first.err == ""
    data = json.loads(first.out)
    assert data["status"] == "ADVANCED"
    assert data["processed_through"] == target
    checkpoint = (sandbox / "current_state.json").read_bytes()
    assert main(args) == 0
    second = capsys.readouterr()
    assert json.loads(second.out)["status"] == "NOOP"
    assert second.err == ""
    assert (sandbox / "current_state.json").read_bytes() == checkpoint
