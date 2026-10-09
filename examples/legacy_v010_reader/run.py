"""Read v0.1.0 Life saves with a newly installed pre-v1 engine build.

This is a *candidate reader compatibility test*, not a stable 1.x API.
It imports only the installed engine implementation, reads immutable public
synthetic goldens and never writes to the original source tree.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from engine.life import ENGINE_VERSION
from engine.life.canonical import canonical_hash, normalize_persisted_object
from engine.life.correction import apply_correction_overlays
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.history import collect_ledger_events
from engine.life.operator_action import load_operator_ledger
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.runtime_persistence import (
    load_runtime_persistent_snapshot,
    snapshot_tree_bytes,
)
from engine.life.schema import validate_instance

GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "golden"
    / "persistence-v0.1.0"
)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _references() -> RuntimeReferenceSets:
    raw = _read_json(GOLDEN / "references.json")
    return RuntimeReferenceSets(**{name: set(values) for name, values in raw.items()})


def _validate_pinned_families(root: Path, artifact_rows: list[dict]) -> None:
    if len(artifact_rows) != 16:
        raise AssertionError("v0.1.0 frozen family count changed")
    names: set[str] = set()
    for row in artifact_rows:
        name = row["family"]
        if name in names:
            raise AssertionError("duplicate persistence family")
        names.add(name)
        document = _read_json(root / row["path"])
        for key in (row["pointer"] or "").strip("/").split("/"):
            if not key:
                continue
            document = document[int(key)] if isinstance(document, list) else document[key]
        validate_instance(document, row["schema_name"])
        if document["schema_version"] != 1:
            raise AssertionError(f"legacy {name} version was silently changed")
        digest = canonical_hash(normalize_persisted_object(document))
        if digest != row["canonical_hash"]:
            raise AssertionError(f"legacy {name} canonical digest changed")


def verify_installed_reader() -> dict:
    """Prove today's packaged candidate reads the v0.1.0 frozen bytes."""
    expected = _read_json(GOLDEN / "expected.json")
    families = _read_json(GOLDEN / "artifacts.json")["artifacts"]
    old_tree = GOLDEN / "runtime"
    original_bytes = snapshot_tree_bytes(old_tree)
    if original_bytes != expected["file_sha256"]:
        raise AssertionError("frozen v0.1.0 input bytes have drifted")
    if ENGINE_VERSION != expected["source_engine"]:
        # The future v1 reader will need an explicit *legacy* compatibility
        # path here. Do not auto-rename the old engine version to pass a test.
        raise AssertionError("current reader engine identity changed without a legacy decoder")

    with tempfile.TemporaryDirectory(prefix="snowfall-v010-legacy-read-") as tmp:
        copy = Path(tmp) / "copied-legacy"
        shutil.copytree(old_tree, copy)
        if snapshot_tree_bytes(copy) != original_bytes:
            raise AssertionError("copy changed frozen file bytes")
        snapshot = load_runtime_persistent_snapshot(copy, reference_sets=_references())
        _validate_pinned_families(copy, families)

        if snapshot.semantic_tree_hash != expected["semantic_tree_hash"]:
            raise AssertionError("v0.1.0 semantic history digest changed")
        if snapshot.bundle.current_state["history"] != expected["history"]:
            raise AssertionError("v0.1.0 history head changed")
        if snapshot.operator_history != expected["operator_history"]:
            raise AssertionError("v0.1.0 operator history changed")
        events = collect_ledger_events(list(snapshot.timeline_days.values()))
        if [event["event_id"] for event in events] != expected["event_ids"]:
            raise AssertionError("v0.1.0 finalized events changed")
        ledger = load_operator_ledger(copy)
        effective = apply_correction_overlays(events, ledger)
        if canonical_hash(list(effective)) != expected["effective_events_hash"]:
            raise AssertionError("v0.1.0 correction projection changed")
        capture_ids = sorted(
            record["capture_id"]
            for shard in snapshot.camera_roll_shards.values()
            for record in shard["records"]
        )
        if capture_ids != expected["capture_ids"]:
            raise AssertionError("v0.1.0 capture links changed")
        if [action["action_id"] for action in ledger.actions] != expected["action_ids"]:
            raise AssertionError("v0.1.0 operator action chain changed")

        after = snapshot_tree_bytes(copy)
        if after != original_bytes or snapshot_tree_bytes(old_tree) != original_bytes:
            raise AssertionError("legacy reader wrote or modified source files")

        # Future versions must fail closed, even when the test's historical
        # source is otherwise valid. Modify ONLY a temporary disposable copy.
        invalid = Path(tmp) / "bad-future-version"
        shutil.copytree(old_tree, invalid)
        state_path = invalid / "state" / "current.json"
        broken = _read_json(state_path)
        broken["engine_version"] = "99.0.0-future"
        state_path.write_text(json.dumps(broken, sort_keys=True) + "\n", encoding="utf-8")
        bad_before = snapshot_tree_bytes(invalid)
        try:
            load_runtime_persistent_snapshot(invalid, reference_sets=_references())
        except LifeEngineError as exc:
            if exc.code is not ErrorCode.UNSUPPORTED_VERSION:
                raise AssertionError(f"wrong unknown-version error: {exc.code}") from exc
        else:
            raise AssertionError("future persisted engine version was accepted")
        if snapshot_tree_bytes(invalid) != bad_before:
            raise AssertionError("unknown-format failure rewrote its input")
        if snapshot_tree_bytes(old_tree) != original_bytes:
            raise AssertionError("legacy frozen fixtures were modified")

    return {
        "status": "PASS",
        "scope": "pre-v1-installed-reader-v0.1.0",
        "old_engine_version": expected["source_engine"],
        "families": len(families),
        "files": len(original_bytes),
        "semantic_tree_hash": expected["semantic_tree_hash"],
        "effective_events_hash": expected["effective_events_hash"],
        "source_unchanged": True,
        "rejects_future_engine_version": True,
    }


def main() -> int:
    print(json.dumps(verify_installed_reader(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
