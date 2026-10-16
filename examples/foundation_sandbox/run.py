"""Fully synthetic *foundation sandbox* consumer of the installed 0.x CLI.

This intentionally does not exercise the full provider-backed RuntimeBundle
or imply that the foundation CLI can activate a private production character.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


EPOCH = "2026-01-01T00:00:00+09:00"
TARGET = "2026-01-01T00:05:00+09:00"
POLICY = Path(__file__).with_name("policy.json")


def _invoke(cwd: Path, *args: str) -> str:
    finished = subprocess.run(
        [sys.executable, "-m", "snowfall_life", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if finished.returncode != 0:
        raise RuntimeError(
            f"snowfall-life {' '.join(args)} returned {finished.returncode}: "
            f"{finished.stderr.strip()}"
        )
    return finished.stdout.strip()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="snowfall-life-fixture-") as root:
        base = Path(root)
        ws = base / "sandbox"
        _invoke(
            base,
            "init-sandbox",
            "--workspace", str(ws),
            "--character-id", "fixture-character",
            "--life-epoch", EPOCH,
            "--policy-version", "fixture-policy-1",
            "--world-seed", "fixture-world-seed",
        )
        advanced = json.loads(
            _invoke(
                base, "advance", "--workspace", str(ws),
                "--target", TARGET, "--policy", str(POLICY),
            )
        )
        if advanced.get("status") != "ADVANCED":
            raise AssertionError(f"first advance must advance: {advanced}")
        if advanced["processed_through"] != TARGET:
            raise AssertionError(f"unexpected target: {advanced}")

        state_path = ws / "current_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["engine_version"] != "0.1.0-foundation":
            raise AssertionError("unreviewed stored engine version changed")
        if state["character_id"] != "fixture-character":
            raise AssertionError("synthetic character identity changed")

        if not _invoke(base, "verify-workspace", "--workspace", str(ws)).startswith("OK"):
            raise AssertionError("workspace verification failed")
        if not _invoke(base, "validate", str(state_path), "--schema", "current_state").startswith("OK"):
            raise AssertionError("schema validation failed")

        digest = _invoke(base, "canonical-hash", str(state_path))
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise AssertionError("invalid canonical digest")

        snapshot_bytes = state_path.read_bytes()
        again = json.loads(
            _invoke(
                base, "advance", "--workspace", str(ws),
                "--target", TARGET, "--policy", str(POLICY),
            )
        )
        if again.get("status") != "NOOP":
            raise AssertionError(f"same-target advance must be NOOP: {again}")
        if state_path.read_bytes() != snapshot_bytes:
            raise AssertionError("same-target NOOP rewrote the persisted checkpoint")

        print(json.dumps(
            {
                "status": "PASS",
                "scope": "foundation-sandbox-only",
                "processed_through": TARGET,
                "engine_version": state["engine_version"],
                "checkpoint_digest": digest,
                "repeat_advance": again["status"],
            },
            sort_keys=True,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
