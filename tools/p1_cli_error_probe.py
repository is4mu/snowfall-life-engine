"""Inspect installed canonical diagnostic CLI error behavior under isolated -I.

This executable is test-only; the installed distribution is loaded from
site-packages. It never mutates the historical golden or publishes Git refs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", "-m", "snowfall_life", *args],
        text=True, capture_output=True, check=False,
    )


def installed_cli_probe(golden: Path) -> dict:
    import engine.life as old
    import snowfall_life as new
    from engine.life.cli import main as legacy_main

    assert importlib.metadata.version("snowfall-life-engine") == "1.0.0rc1"
    assert sys.version_info[:2] == (3, 12)
    assert old.ENGINE_VERSION == "0.1.0-foundation"
    expected_old = {
        "ENGINE_VERSION", "SUPPORTED_ENGINE_VERSIONS",
        "SUPPORTED_SCHEMA_VERSION", "ErrorCode", "LifeEngineError",
        "canonical_bytes", "canonical_hash", "canonical_json",
    }
    assert set(old.__all__) == expected_old
    assert old.SUPPORTED_ENGINE_VERSIONS == frozenset({old.ENGINE_VERSION})
    assert old.SUPPORTED_SCHEMA_VERSION == 1
    for name in new.__all__:
        assert getattr(new, name) is getattr(old, name)

    golden_original = golden.read_bytes()
    assert b'"engine_version"' in golden_original
    cases: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="snowfall-cli-input-review-") as d:
        root = Path(d)
        missing = root / "not-here.json"
        broken = root / "broken.json"
        broken.write_text('{"incomplete":', encoding="utf-8")
        bad_unicode = root / "invalid-utf8.json"
        bad_unicode.write_bytes(b"\xff")
        valid_copy = root / "current.json"
        valid_copy.write_bytes(golden_original)
        directory = root / "directory-as-input"
        directory.mkdir()
        file_bytes = {
            path: path.read_bytes()
            for path in (broken, bad_unicode, valid_copy)
        }

        successful = _cli("validate", str(valid_copy), "--schema", "current_state")
        assert successful.returncode == 0
        assert successful.stdout.strip() == "OK schema=current_state"
        assert successful.stderr == ""
        cases["validate_valid"] = "0"

        digest = _cli("canonical-hash", str(valid_copy), "--raw")
        assert digest.returncode == 0
        assert len(digest.stdout.strip()) == 64
        int(digest.stdout.strip(), 16)
        assert digest.stderr == ""
        cases["canonical_hash_valid"] = "0"

        problem_cases = {
            "validate_malformed_json": (
                ("validate", str(broken), "--schema", "current_state"),
                "SCHEMA_INVALID", "invalid JSON or UTF-8 input",
            ),
            "validate_invalid_utf8": (
                ("validate", str(bad_unicode), "--schema", "current_state"),
                "SCHEMA_INVALID", "invalid JSON or UTF-8 input",
            ),
            "hash_malformed_json": (
                ("canonical-hash", str(broken)),
                "INVALID_STATE", "invalid JSON or UTF-8 input",
            ),
            "hash_invalid_utf8": (
                ("canonical-hash", str(bad_unicode)),
                "INVALID_STATE", "invalid JSON or UTF-8 input",
            ),
            "validate_missing_file": (
                ("validate", str(missing), "--schema", "current_state"),
                "INVALID_STATE", "unable to read JSON input",
            ),
            "hash_missing_file": (
                ("canonical-hash", str(missing)),
                "INVALID_STATE", "unable to read JSON input",
            ),
            "validate_directory": (
                ("validate", str(directory), "--schema", "current_state"),
                "INVALID_STATE", "unable to read JSON input",
            ),
        }
        for name, (args, code, detail) in problem_cases.items():
            result = _cli(*args)
            assert result.returncode == 2, (name, result)
            assert result.stdout == "", (name, result.stdout)
            assert result.stderr == f"ERROR {code}: {detail}\n", (name, result.stderr)
            cases[name] = code

        parsed = _cli("validate")
        assert parsed.returncode == 2
        assert "usage:" in parsed.stderr
        assert parsed.stdout == ""
        cases["argparse_missing_flags"] = "2"

        # Existing legacy CLI must keep its old failure path (untouched).
        try:
            legacy_main(["validate", str(broken), "--schema", "current_state"])
        except json.JSONDecodeError:
            pass
        else:
            raise AssertionError("legacy Python CLI error handling changed")
        assert all(path.read_bytes() == before for path, before in file_bytes.items())
        assert not missing.exists()
    assert golden.read_bytes() == golden_original

    return {
        "status": "PASS",
        "scope": "private-1.0-rc-basic-cli-errors-and-legacy-root",
        "stable_cli_approved": False,
        "python_version": "3.12",
        "engine_version": old.ENGINE_VERSION,
        "legacy_root_exports": sorted(expected_old),
        "typed_error_cases": cases,
        "source_unchanged": True,
        "legacy_cli_unchanged": True,
        "sha256_legacy_source": hashlib.sha256(golden_original).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden-current", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(installed_cli_probe(args.golden_current), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
