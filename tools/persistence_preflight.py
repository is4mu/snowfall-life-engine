"""Experimental read-only persistence inspection; no migration or commit facade.

Only explicit current->current inspection is implemented. This repository tool
is excluded from wheels/sdists and declares no v1 compatibility policy.
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.life import ENGINE_VERSION
from engine.life.canonical import canonical_hash
from engine.life.correction import apply_correction_overlays
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.history import collect_ledger_events
from engine.life.operator_action import load_operator_ledger
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.runtime_persistence import (
    is_allowed_runtime_relpath,
    load_runtime_persistent_snapshot,
    snapshot_tree_bytes,
)
from engine.life.versioning import ensure_supported_engine


def _load_snapshot(root, reference_sets):
    try:
        return load_runtime_persistent_snapshot(root, reference_sets=reference_sets)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unreadable persistence source: {exc}") from exc


def inspect_persistence(root: Path | str, *, target_engine: str,
                        reference_sets: RuntimeReferenceSets) -> dict:
    """Validate source and return detached evidence, without staging/writing.

    The caller must provide an explicit target and approved reference sets.
    A successful inspection is not a migration plan or permission to commit.
    The caller must hold the source quiescent; fingerprints detect ordinary
    changes during inspection, but are not a lock or atomic snapshot.
    """
    ensure_supported_engine(target_engine)
    if target_engine != ENGINE_VERSION:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_VERSION, "no approved migration target")
    root = Path(root)
    # Validate path safety before any fingerprint reads (including symlinks).
    snapshot = _load_snapshot(root, reference_sets)
    before = snapshot_tree_bytes(root)
    unexpected = [rel for rel in before if not is_allowed_runtime_relpath(rel)]
    if unexpected:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unexpected preflight paths: {unexpected}")
    # Reload between byte fingerprints so the evidence describes validated bytes.
    snapshot = _load_snapshot(root, reference_sets)
    source_engine = snapshot.bundle.current_state["engine_version"]
    if source_engine != target_engine:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_VERSION, "no approved migration path")
    events = collect_ledger_events(snapshot.timeline_days.values())
    ledger = load_operator_ledger(root)
    effective = apply_correction_overlays(events, ledger)
    if snapshot_tree_bytes(root) != before:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "source changed during preflight")
    return {
        "status": "VERIFIED_CURRENT_FORMAT",
        "source_engine": source_engine,
        "target_engine": target_engine,
        "migration_available": False,
        "file_sha256": before,
        "semantic_tree_hash": snapshot.semantic_tree_hash,
        "history": dict(snapshot.bundle.current_state["history"]),
        "operator_history": ledger.as_history(),
        "event_ids": [event["event_id"] for event in events],
        "effective_events_hash": canonical_hash(list(effective)),
        "capture_ids": sorted(record["capture_id"] for shard in snapshot.camera_roll_shards.values()
                              for record in shard["records"]),
        "action_ids": [action["action_id"] for action in ledger.actions],
        "correction_lineage": [
            {"action_id": action["action_id"],
             "target_event_id": action["correction"]["target_event_id"],
             "supersedes_action_id": action["correction"]["supersedes_action_id"]}
            for action in ledger.actions if action["action_type"] == "CORRECTION"
        ],
    }


def main() -> int:
    import argparse
    import json
    from engine.life.canonical import canonical_json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--target-engine', required=True)
    parser.add_argument('--references', type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = json.loads(args.references.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or any(
            not isinstance(value, list) or any(not isinstance(item, str) for item in value)
            for value in raw.values()
        ):
            raise ValueError('references must be an object of string arrays')
        refs = RuntimeReferenceSets(**{key: set(value) for key, value in raw.items()})
        report = inspect_persistence(args.root, target_engine=args.target_engine, reference_sets=refs)
    except LifeEngineError as exc:
        print(canonical_json({'status': 'REJECTED', 'code': exc.code.value, 'detail': exc.detail}))
        return 1
    except (OSError, ValueError, TypeError) as exc:
        print(canonical_json({'status': 'REJECTED', 'code': 'INVALID_INPUT', 'detail': str(exc)}))
        return 1
    print(canonical_json(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
