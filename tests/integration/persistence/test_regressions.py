"""Persistence regressions for closure, byte preservation, revision guards, and forbidden paths."""

from __future__ import annotations

import json
import tempfile
import unittest

import pytest
from pathlib import Path
from unittest import mock

from engine.life.camera_roll import project_camera_roll_records
from engine.life.checkpoint import validate_checkpoint
from engine.life.errors import LifeEngineError
from engine.life.events import validate_active_activity
from engine.life.history import fold_history
from engine.life.runtime_bundle import RuntimeBundle
from engine.life.runtime_orchestrator import RuntimeTargetAdvanceResult
from engine.life.runtime_persistence import (
    compute_camera_month_closed_at,
    load_runtime_persistent_snapshot,
    write_runtime_json,
)
from engine.life.social_runtime import validate_social_response_state
from tests.support.builders.persistence import _activity, _capture
from tests.support.builders.persistence import (
    AS_OF,
    _refs,
    build_5c1_persistent_root,
    build_capture_event,
    fingerprint,
    make_c3_noop_root,
    read_json,
    run_candidate,
)


def _alt_format_json(obj: dict) -> str:
    """Semantically valid JSON with alternate key order / spacing."""
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": ")) + "\n"


def _processed_bump_advance(**kwargs):
    """C3 handoff that only advances processed_through (semantic CHANGE)."""
    bundle = kwargs["bundle"]
    social = kwargs["social_response_state"]
    if not hasattr(social, "as_dict"):
        social = validate_social_response_state(social)
    cur = dict(bundle.current_state)
    cur["processed_through"] = "2026-04-01T13:00:00+09:00"
    cur = validate_checkpoint(cur)
    return RuntimeTargetAdvanceResult(
        bundle=RuntimeBundle(
            current_state=cur,
            schedule_state=bundle.schedule_state,
            relation_state=bundle.relation_state,
            home_state=bundle.home_state,
            consumables_state=bundle.consumables_state,
            wardrobe_state=bundle.wardrobe_state,
            finance_state=bundle.finance_state,
        ),
        social_response_state=social,
        actual_events=(),
        camera_roll_records=(),
        camera_roll_records_by_shard_path={},
        consumed_source_keys=(),
        consumed_trigger_ids=(),
        decision_frames=(),
        microsteps_used=0,
    )


pytestmark = pytest.mark.integration

class PersistenceReviewClosedShardSemanticsTests(unittest.TestCase):
    """Major 1 — CLOSED CameraRollShard baseline semantics."""

    def test_closed_current_month_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, processed_through=AS_OF)
            month = AS_OF[:7]  # current month
            shard = {
                "schema_version": 1,
                "month": month,
                "closed_at": compute_camera_month_closed_at(month),
                "records": [],
            }
            write_runtime_json(root / "camera-roll" / "records" / f"{month}.json", shard)
            with self.assertRaisesRegex(LifeEngineError, "strictly before processed"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_closed_future_month_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, processed_through=AS_OF)
            month = "2026-05"
            shard = {
                "schema_version": 1,
                "month": month,
                "closed_at": compute_camera_month_closed_at(month),
                "records": [],
            }
            write_runtime_json(root / "camera-roll" / "records" / f"{month}.json", shard)
            with self.assertRaisesRegex(LifeEngineError, "strictly before processed"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_closed_prior_with_pending_capture_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prior_month = "2026-03"
            capture_at = "2026-03-15T12:00:00+09:00"
            aid = "activity:pending-review"
            root = build_5c1_persistent_root(
                tmp,
                processed_through=AS_OF,
                camera_shards={
                    prior_month: {
                        "schema_version": 1,
                        "month": prior_month,
                        "closed_at": compute_camera_month_closed_at(prior_month),
                        "records": [],
                    }
                },
            )
            current = read_json(root / "state/current.json")
            cap = _capture(
                activity_instance_id=aid,
                captured_at=capture_at,
                occurrence_key="occ-pending-prior",
            )
            act = validate_active_activity(
                _activity(
                    activity_instance_id=aid,
                    activity_type="REST",
                    actual_start="2026-03-10T10:00:00+09:00",
                    pending_captures=[cap],
                )
            )
            current["context"] = dict(current["context"])
            current["context"]["active_activity"] = act
            write_runtime_json(root / "state/current.json", validate_checkpoint(current))
            with self.assertRaisesRegex(LifeEngineError, "pending_capture"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_closed_wrong_closed_at_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp, processed_through=AS_OF)
            month = "2026-03"
            shard = {
                "schema_version": 1,
                "month": month,
                "closed_at": "2026-03-31T23:59:59+09:00",  # not next-month midnight
                "records": [],
            }
            write_runtime_json(root / "camera-roll" / "records" / f"{month}.json", shard)
            with self.assertRaisesRegex(LifeEngineError, "closed_at mismatch"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_noncanonical_record_order_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event(
                actual_start="2026-03-10T09:00:00+09:00",
                actual_end="2026-03-10T11:00:00+09:00",
                captured_at="2026-03-10T10:00:00+09:00",
                occurrence_key="decision:photo:order-a",
                activity_instance_id="activity:order-a",
            )
            event2 = build_capture_event(
                actual_start="2026-03-11T09:00:00+09:00",
                actual_end="2026-03-11T11:00:00+09:00",
                captured_at="2026-03-11T10:00:00+09:00",
                occurrence_key="decision:photo:order-b",
                activity_instance_id="activity:order-b",
            )
            recs = project_camera_roll_records(event) + project_camera_roll_records(event2)
            reverse = list(reversed(recs))
            root = build_5c1_persistent_root(
                tmp,
                processed_through=AS_OF,
                timeline_days={
                    "2026-03-10": {
                        "schema_version": 1,
                        "date": "2026-03-10",
                        "timezone": "Asia/Tokyo",
                        "status": "CLOSED",
                        "closed_at": "2026-03-11T00:00:00+09:00",
                        "actual_events": [event],
                    },
                    "2026-03-11": {
                        "schema_version": 1,
                        "date": "2026-03-11",
                        "timezone": "Asia/Tokyo",
                        "status": "CLOSED",
                        "closed_at": "2026-03-12T00:00:00+09:00",
                        "actual_events": [event2],
                    },
                },
                camera_shards={
                    "2026-03": {
                        "schema_version": 1,
                        "month": "2026-03",
                        "closed_at": compute_camera_month_closed_at("2026-03"),
                        "records": reverse,
                    }
                },
                mutate_current=lambda c: {
                    **c,
                    "history": {
                        "head_event_id": event2["event_id"],
                        "history_hash": fold_history([event, event2]),
                        "event_count": 2,
                    },
                },
            )
            with self.assertRaisesRegex(LifeEngineError, "canonical equality"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_valid_prior_closed_and_current_open_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(
                tmp,
                processed_through=AS_OF,
                camera_shards={
                    "2026-03": {
                        "schema_version": 1,
                        "month": "2026-03",
                        "closed_at": compute_camera_month_closed_at("2026-03"),
                        "records": [],
                    },
                    "2026-04": {
                        "schema_version": 1,
                        "month": "2026-04",
                        "closed_at": None,
                        "records": [],
                    },
                },
            )
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIn("2026-03", snap.camera_roll_shards)
            self.assertIsNone(snap.camera_roll_shards["2026-04"]["closed_at"])


class PersistenceReviewBytePreservationTests(unittest.TestCase):
    """Major 2 — preserve bytes for semantically unchanged / CLOSED files."""

    def test_closed_timeline_alt_format_bytes_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = build_capture_event(
                actual_start="2026-03-10T09:00:00+09:00",
                actual_end="2026-03-10T11:00:00+09:00",
                captured_at="2026-03-10T10:00:00+09:00",
                occurrence_key="decision:photo:byte-day",
                activity_instance_id="activity:byte-day",
            )
            day = {
                "schema_version": 1,
                "date": "2026-03-10",
                "timezone": "Asia/Tokyo",
                "status": "CLOSED",
                "closed_at": "2026-03-11T00:00:00+09:00",
                "actual_events": [event],
            }
            root = build_5c1_persistent_root(
                tmp,
                processed_through=AS_OF,
                timeline_days={"2026-03-10": day},
                camera_shards={
                    "2026-03": {
                        "schema_version": 1,
                        "month": "2026-03",
                        "closed_at": compute_camera_month_closed_at("2026-03"),
                        "records": project_camera_roll_records(event),
                    }
                },
                mutate_current=lambda c: {
                    **c,
                    "history": {
                        "head_event_id": event["event_id"],
                        "history_hash": fold_history([event]),
                        "event_count": 1,
                    },
                },
            )
            day_path = root / "timeline" / "2026-03-10.json"
            alt = _alt_format_json(read_json(day_path))
            day_path.write_text(alt, encoding="utf-8")
            before_bytes = day_path.read_bytes()

            cand = Path(tmp) / "cand"
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_processed_bump_advance,
            ):
                result = run_candidate(root, candidate_root=cand)
            self.assertEqual(result.status, "CHANGE")
            after_bytes = (cand / "timeline" / "2026-03-10.json").read_bytes()
            self.assertEqual(before_bytes, after_bytes)
            self.assertNotIn("timeline/2026-03-10.json", result.files_changed)

    def test_unchanged_domain_alt_format_not_in_files_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            schedule_path = root / "schedule" / "state.json"
            alt = _alt_format_json(read_json(schedule_path))
            schedule_path.write_text(alt, encoding="utf-8")
            before = schedule_path.read_bytes()

            cand = Path(tmp) / "cand"
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_processed_bump_advance,
            ):
                result = run_candidate(root, candidate_root=cand)
            self.assertEqual(result.status, "CHANGE")
            self.assertEqual((cand / "schedule" / "state.json").read_bytes(), before)
            self.assertNotIn("schedule/state.json", result.files_changed)


class PersistenceReviewC3RevisionGuardTests(unittest.TestCase):
    """Minor 1 — do not silently normalize unauthorized C3 state_revision mutation."""

    def test_c3_state_revision_mutation_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, _bundle, _social, inputs = make_c3_noop_root(tmp)
            before = fingerprint(root)

            def _advance(**kwargs):
                bundle = kwargs["bundle"]
                social = kwargs["social_response_state"]
                cur = dict(bundle.current_state)
                cur["state_revision"] = int(cur["state_revision"]) + 7
                cur = validate_checkpoint(cur)
                return RuntimeTargetAdvanceResult(
                    bundle=RuntimeBundle(
                        current_state=cur,
                        schedule_state=bundle.schedule_state,
                        relation_state=bundle.relation_state,
                        home_state=bundle.home_state,
                        consumables_state=bundle.consumables_state,
                        wardrobe_state=bundle.wardrobe_state,
                        finance_state=bundle.finance_state,
                    ),
                    social_response_state=social,
                    actual_events=(),
                    camera_roll_records=(),
                    camera_roll_records_by_shard_path={},
                    consumed_source_keys=(),
                    consumed_trigger_ids=(),
                    decision_frames=(),
                    microsteps_used=0,
                )

            cand = Path(tmp) / "cand"
            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_advance,
            ):
                with self.assertRaisesRegex(
                    LifeEngineError, "state_revision unchanged"
                ):
                    run_candidate(root, candidate_root=cand, target_inputs=inputs)
            self.assertEqual(fingerprint(root), before)


class PersistenceReviewForbiddenPathTests(unittest.TestCase):
    """Minor 2 — reject unexpected files under authoritative namespaces."""

    def test_state_debug_json_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            (root / "state" / "debug.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(LifeEngineError, "authoritative runtime"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_timeline_tmp_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            (root / "timeline").mkdir(exist_ok=True)
            (root / "timeline" / "foo.tmp").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(LifeEngineError, "authoritative runtime"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_camera_debug_txt_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            shard_dir = root / "camera-roll" / "records"
            shard_dir.mkdir(parents=True, exist_ok=True)
            (shard_dir / "debug.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(LifeEngineError, "authoritative runtime"):
                load_runtime_persistent_snapshot(root, reference_sets=_refs())

    def test_legacy_data_runtime_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = build_5c1_persistent_root(tmp)
            legacy = root / "data" / "runtime"
            legacy.mkdir(parents=True)
            (legacy / "character.json").write_text("{}\n", encoding="utf-8")
            snap = load_runtime_persistent_snapshot(root, reference_sets=_refs())
            self.assertIsNotNone(snap)
