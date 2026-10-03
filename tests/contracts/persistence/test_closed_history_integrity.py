"""Closed timeline/camera immutability and reload integrity contracts."""

from __future__ import annotations

import tempfile
import unittest

import pytest
from pathlib import Path
from unittest import mock

from engine.life.camera_roll import project_camera_roll_records
from engine.life.canonical import canonical_hash, normalize_persisted_object
from engine.life.runtime_persistence import (
    compute_camera_month_closed_at,
    load_runtime_persistent_snapshot,
    verify_timeline_camera_integrity,
)
from tests.support.builders.persistence import (
    _refs,
    build_5c1_persistent_root,
    build_capture_event,
    day_with_events,
    make_c3_exact_end_root,
    run_candidate,
    shard_with_records,
)


pytestmark = pytest.mark.contract

class PersistenceClosedIntegrityTests(unittest.TestCase):
    def test_96_closed_timeline_baseline_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from tests.support.builders.persistence import build_actual_event

            prior = build_actual_event(
                activity_instance_id="activity:5c1-closed",
                actual_start="2026-03-31T10:00:00+09:00",
                actual_end="2026-03-31T11:00:00+09:00",
            )
            closed_day = day_with_events(prior, date="2026-03-31", status="CLOSED")
            root, social, current, inputs, *_ = make_c3_exact_end_root(Path(tmp) / "run")
            # Attach closed prior day to C3 root.
            from engine.life.history import fold_history
            from engine.life.runtime_persistence import write_runtime_json
            from tests.support.builders.persistence import read_json

            write_runtime_json(root / "timeline/2026-03-31.json", closed_day)
            cur = read_json(root / "state/current.json")
            # History starts empty in C3 fixture; keep closed day out of history count
            # only if verify requires match — include prior in history.
            cur["history"] = {
                "head_event_id": prior["event_id"],
                "history_hash": fold_history([prior]),
                "event_count": 1,
            }
            write_runtime_json(root / "state/current.json", cur)
            before_bytes = (root / "timeline/2026-03-31.json").read_bytes()
            result = run_candidate(root, target_inputs=inputs)
            after_bytes = (
                Path(result.candidate_snapshot.root) / "timeline/2026-03-31.json"
            ).read_bytes()
            self.assertEqual(before_bytes, after_bytes)

    def test_97_closed_camera_shard_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event(
                actual_start="2026-03-15T10:00:00+09:00",
                actual_end="2026-03-15T11:00:00+09:00",
                captured_at="2026-03-15T10:30:00+09:00",
            )
            day = day_with_events(event, date="2026-03-15", status="CLOSED")
            recs = project_camera_roll_records(event)
            shard = shard_with_records(
                "2026-03", recs, closed_at=compute_camera_month_closed_at("2026-03")
            )
            root = build_5c1_persistent_root(
                tmp,
                timeline_days=[day],
                camera_shards=[shard],
                processed_through="2026-04-15T12:00:00+09:00",
                as_of="2026-04-15T12:00:00+09:00",
            )
            before = (root / "camera-roll/records/2026-03.json").read_bytes()
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            # Reload path must preserve closed shard bytes when rewritten equivalently.
            from engine.life.runtime_persistence import write_runtime_json

            write_runtime_json(
                root / "camera-roll/records/2026-03.json",
                snap.camera_roll_shards["2026-03"],
            )
            # Semantic identity (normalize) holds even if writer re-indents.
            self.assertEqual(
                canonical_hash(
                    normalize_persisted_object(snap.camera_roll_shards["2026-03"])
                ),
                canonical_hash(normalize_persisted_object(shard)),
            )
            # For exact-end CHANGE that doesn't touch March, bytes stay identical:
            root2, social, current, inputs, *_ = make_c3_exact_end_root(Path(tmp) / "c3")
            from engine.life.runtime_persistence import write_runtime_json as wr
            from tests.support.builders.persistence import read_json
            from engine.life.history import fold_history

            wr(root2 / "camera-roll/records/2026-03.json", shard)
            wr(root2 / "timeline/2026-03-15.json", day)
            cur = read_json(root2 / "state/current.json")
            cur["history"] = {
                "head_event_id": event["event_id"],
                "history_hash": fold_history([event]),
                "event_count": 1,
            }
            wr(root2 / "state/current.json", cur)
            before2 = (root2 / "camera-roll/records/2026-03.json").read_bytes()
            result = run_candidate(root2, target_inputs=inputs)
            after2 = (
                Path(result.candidate_snapshot.root) / "camera-roll/records/2026-03.json"
            ).read_bytes()
            self.assertEqual(before2, after2)

    def test_98_existing_actual_event_byte_semantic_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from tests.support.builders.persistence import build_actual_event, read_json
            from engine.life.history import fold_history
            from engine.life.runtime_persistence import write_runtime_json

            prior = build_actual_event(
                activity_instance_id="activity:5c1-prior",
                actual_start="2026-04-01T08:00:00+09:00",
                actual_end="2026-04-01T09:00:00+09:00",
            )
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            day = day_with_events(prior)
            write_runtime_json(root / "timeline/2026-04-01.json", day)
            cur = read_json(root / "state/current.json")
            cur["history"] = {
                "head_event_id": prior["event_id"],
                "history_hash": fold_history([prior]),
                "event_count": 1,
            }
            write_runtime_json(root / "state/current.json", cur)
            result = run_candidate(root, target_inputs=inputs)
            got = result.candidate_snapshot.timeline_days["2026-04-01"]["actual_events"][0]
            self.assertEqual(
                canonical_hash(normalize_persisted_object(got)),
                canonical_hash(normalize_persisted_object(prior)),
            )
            self.assertEqual(got, prior)

    def test_99_candidate_full_load_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            from tests.support.builders.persistence import _advance_refs

            reloaded = load_runtime_persistent_snapshot(
                result.candidate_snapshot.root, reference_sets=_advance_refs()
            )
            self.assertEqual(
                reloaded.semantic_tree_hash, result.candidate_snapshot.semantic_tree_hash
            )
            self.assertEqual(
                reloaded.bundle.current_state["state_revision"],
                result.candidate_snapshot.bundle.current_state["state_revision"],
            )

    def test_100_timeline_camera_set_equality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event()
            day = day_with_events(event)
            recs = project_camera_roll_records(event)
            root = build_5c1_persistent_root(
                tmp,
                timeline_days=[day],
                camera_shards=[shard_with_records("2026-04", recs)],
            )
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            verify_timeline_camera_integrity(
                days=snap.timeline_days, shards=snap.camera_roll_shards
            )
