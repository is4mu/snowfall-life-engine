"""Persistent snapshot loading and cross-ledger integrity contracts."""

from __future__ import annotations

import tempfile
import unittest

import pytest
from pathlib import Path

from engine.life.camera_roll import project_camera_roll_records
from engine.life.errors import LifeEngineError
from engine.life.runtime_persistence import (
    LAST_RUN_RELPATH,
    SOCIAL_RESPONSE_RELPATH,
    compute_camera_month_closed_at,
    empty_camera_roll_shard,
    load_runtime_persistent_snapshot,
    write_runtime_json,
)
from engine.life.runtime_bundle import FORBIDDEN_LEGACY_RUNTIME_RELPATHS
from engine.life.timeline import close_day, empty_day
from tests.support.builders.persistence import (
    AS_OF,
    CHAR,
    _refs,
    build_5c1_persistent_root,
    build_actual_event,
    build_capture_event,
    build_last_run_for_root,
    day_with_events,
    read_json,
    shard_with_records,
)


pytestmark = pytest.mark.contract

class PersistenceBaselineVerifyTests(unittest.TestCase):
    def test_13_all_required_files_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.bundle.current_state["character_id"], CHAR)
            self.assertTrue((root / SOCIAL_RESPONSE_RELPATH).is_file())
            self.assertTrue((root / "state/current.json").is_file())
            self.assertTrue((root / "schedule/state.json").is_file())
            self.assertIsNone(snap.last_run)

    def test_14_missing_social_state_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            (root / SOCIAL_RESPONSE_RELPATH).unlink()
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("social-responses", ctx.exception.detail)

    def test_15_required_5a_file_missing_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            (root / "finance/state.json").unlink()
            with self.assertRaises(LifeEngineError):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_16_legacy_runtime_files_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            legacy = root.joinpath(*Path(FORBIDDEN_LEGACY_RUNTIME_RELPATHS[0]).parts)
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text('{"legacy": true}\n', encoding="utf-8")
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.bundle.current_state["character_id"], CHAR)

    def test_17_timeline_absent_empty_history_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, include_timeline_dir=False)
            self.assertFalse((root / "timeline").exists())
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.bundle.current_state["history"]["event_count"], 0)
            self.assertEqual(snap.timeline_days, {})

    def test_18_timeline_absent_nonempty_history_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_actual_event()

            def mutate(current: dict) -> dict:
                current["history"] = {
                    "head_event_id": event["event_id"],
                    "history_hash": "d" * 64,
                    "event_count": 1,
                }
                return current

            root = build_5c1_persistent_root(
                tmp, mutate_current=mutate, include_timeline_dir=False, auto_history=False
            )
            self.assertFalse((root / "timeline").exists())
            with self.assertRaises(LifeEngineError):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_19_camera_absent_zero_captures_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_actual_event()
            day = day_with_events(event)
            root = build_5c1_persistent_root(tmp, timeline_days=[day])
            self.assertFalse((root / "camera-roll").exists())
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.camera_roll_shards, {})
            self.assertEqual(len(snap.timeline_days), 1)

    def test_20_camera_missing_expected_finalized_capture_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event()
            day = day_with_events(event)
            root = build_5c1_persistent_root(tmp, timeline_days=[day])
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("missing CameraRollRecord", ctx.exception.detail)

    def test_21_orphan_camera_record_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event()
            recs = project_camera_roll_records(event)
            shard = shard_with_records("2026-04", recs)
            root = build_5c1_persistent_root(
                tmp, timeline_days=[], camera_shards=[shard], auto_history=True
            )
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("orphan", ctx.exception.detail)

    def test_22_record_semantic_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event()
            day = day_with_events(event)
            recs = project_camera_roll_records(event)
            bad = dict(recs[0])
            bad["capture_kind"] = (
                "FOOD" if bad["capture_kind"] != "FOOD" else "SELFIE"
            )
            # Bypass merge (which would reject) — write mismatched body directly.
            shard = {
                "schema_version": 1,
                "month": "2026-04",
                "closed_at": None,
                "records": [bad],
            }
            root = build_5c1_persistent_root(
                tmp, timeline_days=[day], camera_shards=[shard]
            )
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("semantic mismatch", ctx.exception.detail)

    def test_23_wrong_shard_month_path_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event(captured_at="2026-04-01T10:30:00+09:00")
            day = day_with_events(event)
            recs = project_camera_roll_records(event)
            # Put April record into a March-named file body claiming March.
            wrong = empty_camera_roll_shard("2026-03")
            wrong["records"] = recs
            root = build_5c1_persistent_root(tmp, timeline_days=[day])
            write_runtime_json(root / "camera-roll/records/2026-03.json", wrong)
            with self.assertRaises(LifeEngineError):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_24_duplicate_record_capture_global_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event()
            day = day_with_events(event)
            recs = project_camera_roll_records(event)
            # Duplicate capture_id across two shard files (global uniqueness).
            shard_a = shard_with_records("2026-04", recs)
            # Second copy under same month body list.
            dup_body = {
                "schema_version": 1,
                "month": "2026-04",
                "closed_at": None,
                "records": [recs[0], dict(recs[0])],
            }
            root = build_5c1_persistent_root(
                tmp, timeline_days=[day], camera_shards=[dup_body]
            )
            with self.assertRaises(LifeEngineError):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_25_stale_closable_timeline_day_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Event on 2026-03-31; processed far later with no active => day should close.
            event = build_actual_event(
                actual_start="2026-03-31T10:00:00+09:00",
                actual_end="2026-03-31T11:00:00+09:00",
            )
            day = day_with_events(event, date="2026-03-31", status="OPEN")
            root = build_5c1_persistent_root(
                tmp,
                timeline_days=[day],
                processed_through="2026-04-02T12:00:00+09:00",
                as_of="2026-04-02T12:00:00+09:00",
            )
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            detail = ctx.exception.detail
            self.assertTrue(
                "stale closable OPEN timeline day" in detail
                or "elapsed OPEN day must be CLOSED" in detail,
                detail,
            )

    def test_26_stale_closable_camera_shard_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event(
                actual_start="2026-03-15T10:00:00+09:00",
                actual_end="2026-03-15T11:00:00+09:00",
                captured_at="2026-03-15T10:30:00+09:00",
            )
            day = day_with_events(event, date="2026-03-15", status="CLOSED")
            recs = project_camera_roll_records(event)
            shard = shard_with_records("2026-03", recs, closed_at=None)
            root = build_5c1_persistent_root(
                tmp,
                timeline_days=[day],
                camera_shards=[shard],
                processed_through="2026-04-15T12:00:00+09:00",
                as_of="2026-04-15T12:00:00+09:00",
            )
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("stale safely-closable camera shard", ctx.exception.detail)

    def test_27_last_run_absent_revision0_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, state_revision=0)
            self.assertFalse((root / LAST_RUN_RELPATH).exists())
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertEqual(snap.bundle.current_state["state_revision"], 0)
            self.assertIsNone(snap.last_run)

    def test_28_last_run_absent_revision_gt0_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, state_revision=3)
            self.assertFalse((root / LAST_RUN_RELPATH).exists())
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("last-run.json absent", ctx.exception.detail)

    def test_29_last_run_current_binding_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, state_revision=1)
            last = build_last_run_for_root(root)
            last["processed_through"] = "2026-04-01T00:00:00+09:00"
            write_runtime_json(root / LAST_RUN_RELPATH, last)
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("processed_through", ctx.exception.detail)

    def test_30_last_run_state_after_hash_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, state_revision=1)
            last = build_last_run_for_root(root)
            last["state_after_hash"] = "e" * 64
            write_runtime_json(root / LAST_RUN_RELPATH, last)
            with self.assertRaises(LifeEngineError) as ctx:
                load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("state_after_hash", ctx.exception.detail)
