"""Camera Roll shard merge, closure, and persistence integrity tests."""

from __future__ import annotations

import tempfile
import unittest

import pytest
from pathlib import Path
from unittest import mock

from engine.life.camera_roll import merge_camera_roll_shard, project_camera_roll_records
from engine.life.checkpoint import validate_checkpoint
from engine.life.errors import LifeEngineError
from engine.life.history import fold_history
from engine.life.runtime_bundle import RuntimeBundle
from engine.life.runtime_persistence import (
    compute_camera_month_closed_at,
    empty_camera_roll_shard,
    is_camera_month_safely_closable,
    load_runtime_persistent_snapshot,
    write_runtime_json,
)
from tests.support.builders.persistence import (
    POLICY_VERSION,
    _refs,
    build_5c1_persistent_root,
    build_actual_event,
    build_capture_event,
    clone_advance_result,
    day_with_events,
    fingerprint,
    make_c3_noop_root,
    read_json,
    real_advance,
    run_candidate,
    shard_with_records,
)


def _force_change_with_events(root: Path, events, records_by_path=None):
    """Patch C3 to emit the given finalized events (and projected records)."""

    all_recs = []
    by_path: dict = {}
    for event in events:
        for rec in project_camera_roll_records(event):
            all_recs.append(rec)
            path = f"camera-roll/records/{rec['captured_at'][:7]}.json"
            by_path.setdefault(path, []).append(rec)
    if records_by_path is not None:
        by_path = {k: list(v) for k, v in records_by_path.items()}
        all_recs = [r for rows in by_path.values() for r in rows]

    def _handoff(**kwargs):
        from engine.life.runtime_orchestrator import RuntimeTargetAdvanceResult

        bundle = kwargs["bundle"]
        social = kwargs["social_response_state"]
        cur = dict(bundle.current_state)
        baseline_events = []
        timeline = root / "timeline"
        if timeline.exists():
            for path in sorted(timeline.glob("*.json")):
                day = read_json(path)
                baseline_events.extend(day.get("actual_events") or [])
        full = baseline_events + list(events)
        cur["history"] = {
            "head_event_id": full[-1]["event_id"] if full else None,
            "history_hash": fold_history(full) if full else cur["history"]["history_hash"],
            "event_count": len(full),
        }
        cur = validate_checkpoint(cur)
        new_bundle = RuntimeBundle(
            current_state=cur,
            schedule_state=bundle.schedule_state,
            relation_state=bundle.relation_state,
            home_state=bundle.home_state,
            consumables_state=bundle.consumables_state,
            wardrobe_state=bundle.wardrobe_state,
            finance_state=bundle.finance_state,
        )
        return RuntimeTargetAdvanceResult(
            bundle=new_bundle,
            social_response_state=social,
            actual_events=tuple(dict(e) for e in events),
            camera_roll_records=tuple(dict(r) for r in all_recs),
            camera_roll_records_by_shard_path={
                path: tuple(dict(r) for r in rows) for path, rows in by_path.items()
            },
            consumed_source_keys=(),
            consumed_trigger_ids=(),
            decision_frames=(),
            microsteps_used=1,
        )

    return _handoff


pytestmark = pytest.mark.integration

class PersistenceCameraTests(unittest.TestCase):
    def test_47_missing_shard_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            event = build_capture_event()
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_force_change_with_events(root, [event]),
            ):
                result = run_candidate(root, target_inputs=inputs)
            self.assertIn("2026-04", result.candidate_snapshot.camera_roll_shards)
            self.assertEqual(
                len(result.candidate_snapshot.camera_roll_shards["2026-04"]["records"]),
                1,
            )

    def test_48_same_month_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e1 = build_capture_event(occurrence_key="decision:photo:a")
            day = day_with_events(e1)
            recs = project_camera_roll_records(e1)
            root = build_5c1_persistent_root(
                tmp,
                timeline_days=[day],
                camera_shards=[shard_with_records("2026-04", recs)],
                behavior_policy_version=POLICY_VERSION,
            )
            e2 = build_capture_event(
                activity_instance_id="activity:5c1-fixture-2",
                occurrence_key="decision:photo:b",
                actual_start="2026-04-01T12:00:00+09:00",
                actual_end="2026-04-01T13:00:00+09:00",
                captured_at="2026-04-01T12:30:00+09:00",
            )
            before = fingerprint(root)
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_force_change_with_events(root, [e2]),
            ):
                result = run_candidate(root, reference_sets=_refs())
            self.assertEqual(fingerprint(root), before)
            self.assertEqual(
                len(result.candidate_snapshot.camera_roll_shards["2026-04"]["records"]),
                2,
            )

    def test_49_cross_month_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            e_apr = build_capture_event(
                captured_at="2026-04-01T10:30:00+09:00",
                occurrence_key="decision:photo:apr",
            )
            e_may = build_capture_event(
                activity_instance_id="activity:5c1-fixture-2",
                actual_start="2026-05-01T10:00:00+09:00",
                actual_end="2026-05-01T11:00:00+09:00",
                captured_at="2026-05-01T10:30:00+09:00",
                occurrence_key="decision:photo:may",
            )
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_force_change_with_events(root, [e_apr, e_may]),
            ):
                result = run_candidate(root, target_inputs=inputs)
            self.assertIn("2026-04", result.candidate_snapshot.camera_roll_shards)
            self.assertIn("2026-05", result.candidate_snapshot.camera_roll_shards)

    def test_50_closed_shard_new_record_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e1 = build_capture_event(
                actual_start="2026-03-15T10:00:00+09:00",
                actual_end="2026-03-15T11:00:00+09:00",
                captured_at="2026-03-15T10:30:00+09:00",
            )
            day = day_with_events(e1, date="2026-03-15", status="CLOSED")
            recs = project_camera_roll_records(e1)
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
            e2 = build_capture_event(
                activity_instance_id="activity:5c1-fixture-2",
                actual_start="2026-03-20T10:00:00+09:00",
                actual_end="2026-03-20T11:00:00+09:00",
                captured_at="2026-03-20T10:30:00+09:00",
                occurrence_key="decision:photo:new",
            )
            before = fingerprint(root)
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_force_change_with_events(root, [e2]),
            ):
                with self.assertRaises(LifeEngineError):
                    run_candidate(root, reference_sets=_refs())
            self.assertEqual(fingerprint(root), before)

    def test_51_closed_shard_exact_replay_noop(self) -> None:
        e1 = build_capture_event(
            actual_start="2026-03-15T10:00:00+09:00",
            actual_end="2026-03-15T11:00:00+09:00",
            captured_at="2026-03-15T10:30:00+09:00",
        )
        recs = project_camera_roll_records(e1)
        shard = shard_with_records(
            "2026-03", recs, closed_at=compute_camera_month_closed_at("2026-03")
        )
        merged = merge_camera_roll_shard(shard, recs)
        self.assertEqual(merged["closed_at"], shard["closed_at"])
        self.assertEqual(len(merged["records"]), 1)

    def test_52_no_orphan_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            event = build_capture_event()
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_force_change_with_events(root, [event]),
            ):
                result = run_candidate(root, target_inputs=inputs)
            from tests.support.builders.persistence import _advance_refs

            snap = load_runtime_persistent_snapshot(
                result.candidate_snapshot.root, reference_sets=_advance_refs()
            )
            self.assertTrue(snap.camera_roll_shards)

    def test_53_every_finalized_capture_has_exactly_one_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            event = build_capture_event()
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_force_change_with_events(root, [event]),
            ):
                result = run_candidate(root, target_inputs=inputs)
            caps = 0
            for day in result.candidate_snapshot.timeline_days.values():
                for ev in day["actual_events"]:
                    caps += len(ev.get("captures") or [])
            recs = sum(
                len(s["records"])
                for s in result.candidate_snapshot.camera_roll_shards.values()
            )
            self.assertEqual(caps, recs)
            self.assertEqual(caps, 1)

    def test_54_prior_month_pending_active_capture_blocks_close(self) -> None:
        state = {
            "processed_through": "2026-04-15T12:00:00+09:00",
            "context": {
                "active_activity": {
                    "pending_captures": [
                        {"captured_at": "2026-03-20T10:00:00+09:00"}
                    ]
                }
            },
        }
        self.assertFalse(
            is_camera_month_safely_closable(
                month="2026-03",
                processed_through=state["processed_through"],
                current_state=state,
            )
        )

    def test_55_prior_month_closes_after_pending_capture_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Baseline: March open shard empty; processed in April; no pending.
            # After merge of March capture + no pending => safe close.
            root, social, current, inputs = make_c3_noop_root(tmp)
            # Advance processed into May so April (and March) can close.
            event = build_capture_event(
                actual_start="2026-03-15T10:00:00+09:00",
                actual_end="2026-03-15T11:00:00+09:00",
                captured_at="2026-03-15T10:30:00+09:00",
            )

            def _handoff(**kwargs):
                result = _force_change_with_events(root, [event])(**kwargs)
                cur = dict(result.bundle.current_state)
                cur["processed_through"] = "2026-05-01T12:00:00+09:00"
                cur["context"] = dict(cur["context"])
                cur["context"]["active_activity"] = None
                cur["pending_queue"] = {"cursor": 0, "events": []}
                cur = validate_checkpoint(cur)
                bundle = RuntimeBundle(
                    current_state=cur,
                    schedule_state=result.bundle.schedule_state,
                    relation_state=result.bundle.relation_state,
                    home_state=result.bundle.home_state,
                    consumables_state=result.bundle.consumables_state,
                    wardrobe_state=result.bundle.wardrobe_state,
                    finance_state=result.bundle.finance_state,
                )
                return clone_advance_result(result, bundle=bundle)

            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_handoff,
            ):
                result = run_candidate(root, target_inputs=inputs)
            shard = result.candidate_snapshot.camera_roll_shards["2026-03"]
            self.assertIsNotNone(shard["closed_at"])
            self.assertEqual(
                shard["closed_at"], compute_camera_month_closed_at("2026-03")
            )

    def test_56_deterministic_month_closed_at(self) -> None:
        self.assertEqual(
            compute_camera_month_closed_at("2026-03"),
            "2026-04-01T00:00:00+09:00",
        )
        self.assertEqual(
            compute_camera_month_closed_at("2026-12"),
            "2027-01-01T00:00:00+09:00",
        )

    def test_57_no_reopen(self) -> None:
        shard = empty_camera_roll_shard("2026-03")
        shard = dict(shard)
        shard["closed_at"] = compute_camera_month_closed_at("2026-03")
        # merge identical is fine; there is no reopen API — closed_at stays set.
        merged = merge_camera_roll_shard(shard, [])
        self.assertEqual(merged["closed_at"], shard["closed_at"])
