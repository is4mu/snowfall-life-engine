"""Capture facts and Camera Roll public semantic tests."""

from __future__ import annotations

import copy
import unittest

import pytest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.camera_roll import (
    camera_roll_shard_month,
    camera_roll_shard_path,
    derive_camera_roll_record_id,
    merge_camera_roll_records,
    merge_camera_roll_shard,
    project_camera_roll_records,
    validate_camera_roll_shard_month,
)
from engine.life.canonical import (
    _UNORDERED_LIST_KEYS,
    canonical_hash,
    normalize_persisted_object,
    persisted_hash,
)
from engine.life.captures import (
    attach_capture_to_active_activity,
    build_capture_fact,
    compute_capture_context_hash,
    derive_capture_id,
    normalize_visual_context,
    validate_capture_fact,
)
from tests.support.builders.captures import (
    PUBLIC_NO_CAPTURE_EVENT_HASH,
    PUBLIC_NO_CAPTURE_HISTORY_HASH,
    build_test_capture_fact,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.events import (
    assert_finalized_immutable,
    build_actual_event_from_activity,
    normalize_actual_event_for_persistence,
    validate_active_activity,
)
from engine.life.history import EMPTY_HISTORY_HASH, next_history_hash
from engine.life.ids import stable_id
from engine.life.schema import load_schema_document, validate_instance
from engine.life.timeline import append_actual_event, empty_day



def _visual_context(**overrides) -> dict:
    base = {
        "character_id": "fixture-character",
        "character_source_sha": "char-sha-1",
        "engine_commit_sha": "engine-sha-1",
        "behavior_policy_version": "policy-v1",
        "location_id": "fixture-home",
        "appearance": {
            "outfit_state_id": "outfit-1",
            "outfit_item_ids": ["item-b", "item-a"],
            "makeup_level": "NORMAL",
            "hair_state_id": "hair-1",
        },
        "home_revision": "home-rev-1",
        "wardrobe_revision": "wardrobe-rev-1",
        "photographer_ref": None,
        "companion_refs": ["friend-b", "friend-a"],
        "lighting_context": "NATURAL",
    }
    appearance_over = overrides.pop("appearance", None)
    base.update(overrides)
    if appearance_over is not None:
        app = dict(base["appearance"])
        app.update(appearance_over)
        base["appearance"] = app
    return base


def _activity(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "activity_instance_id": "activity:capture-fixture-1",
        "activity_type": "LEISURE",
        "actual_start": "2026-04-01T10:00:00+09:00",
        "location_id": "fixture-home",
        "summary": "capture fixture",
    }
    base.update(overrides)
    return base


def _baseline_activity() -> dict:
    return {
        "schema_version": 1,
        "activity_instance_id": "activity:baseline-fixture-1",
        "activity_type": "LEISURE",
        "actual_start": "2026-04-01T10:00:00+09:00",
        "location_id": "home",
        "summary": "baseline no-capture",
    }


def _capture(
    *,
    activity_instance_id: str = "activity:capture-fixture-1",
    occurrence_key: str = "decision:photo:1",
    captured_at: str = "2026-04-01T10:30:00+09:00",
    capture_kind: str = "DAILY_LIFE",
    subject_refs: list | None = None,
    visual_context: dict | None = None,
) -> dict:
    return build_test_capture_fact(
        activity_instance_id=activity_instance_id,
        occurrence_key=occurrence_key,
        captured_at=captured_at,
        capture_kind=capture_kind,
        subject_refs=[] if subject_refs is None else subject_refs,
        visual_context=visual_context or _visual_context(),
    )

pytestmark = pytest.mark.integration


class PendingCaptureIntegrationTests(unittest.TestCase):
    def test_16_missing_pending_captures_valid(self) -> None:
        out = validate_active_activity(_activity())
        assert out is not None
        self.assertNotIn("pending_captures", out)

    def test_17_empty_pending_captures_valid(self) -> None:
        out = validate_active_activity(_activity(pending_captures=[]))
        assert out is not None
        self.assertEqual(out["pending_captures"], [])

    def test_18_attach_valid_capture_returns_new_activity(self) -> None:
        act = _activity()
        cap = _capture()
        out = attach_capture_to_active_activity(act, cap)
        self.assertIsNot(out, act)
        self.assertEqual(len(out["pending_captures"]), 1)

    def test_19_original_activity_capture_not_mutated(self) -> None:
        act = _activity()
        cap = _capture()
        act_before = copy.deepcopy(act)
        cap_before = copy.deepcopy(cap)
        attach_capture_to_active_activity(act, cap)
        self.assertEqual(act, act_before)
        self.assertEqual(cap, cap_before)

    def test_20_activity_instance_id_mismatch_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            attach_capture_to_active_activity(
                _activity(),
                _capture(activity_instance_id="activity:other"),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_21_captured_at_before_actual_start_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            attach_capture_to_active_activity(
                _activity(),
                _capture(captured_at="2026-04-01T09:59:59+09:00"),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_22_identical_duplicate_attach_idempotent(self) -> None:
        act = _activity()
        cap = _capture()
        once = attach_capture_to_active_activity(act, cap)
        twice = attach_capture_to_active_activity(once, cap)
        self.assertEqual(len(twice["pending_captures"]), 1)
        self.assertEqual(
            canonical_hash(once["pending_captures"][0]),
            canonical_hash(twice["pending_captures"][0]),
        )

    def test_23_same_capture_id_different_semantics_rejected(self) -> None:
        act = attach_capture_to_active_activity(_activity(), _capture())
        conflict = _capture()
        conflict = dict(conflict)
        conflict["visual_context"] = _visual_context(location_id="elsewhere")
        conflict["capture_context_hash"] = compute_capture_context_hash(
            conflict["visual_context"]
        )
        # Same capture_id (same occurrence_key) but different visual semantics.
        with self.assertRaises(LifeEngineError) as ctx:
            attach_capture_to_active_activity(act, conflict)
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_24_pending_capture_ordering_canonical(self) -> None:
        act = _activity()
        c_late = _capture(
            occurrence_key="occ-late",
            captured_at="2026-04-01T10:40:00+09:00",
        )
        c_early = _capture(
            occurrence_key="occ-early",
            captured_at="2026-04-01T10:20:00+09:00",
        )
        out = attach_capture_to_active_activity(act, c_late)
        out = attach_capture_to_active_activity(out, c_early)
        times = [c["captured_at"] for c in out["pending_captures"]]
        self.assertEqual(times, sorted(times))

class CaptureFinalizationIntegrationTests(unittest.TestCase):
    def test_25_no_pending_captures_means_empty_captures(self) -> None:
        event = build_actual_event_from_activity(
            _activity(),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        self.assertEqual(event["captures"], [])

    def test_26_no_capture_event_stable_without_capture(self) -> None:
        event = build_actual_event_from_activity(
            _baseline_activity(),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        self.assertEqual(persisted_hash(event), PUBLIC_NO_CAPTURE_EVENT_HASH)
        self.assertEqual(
            canonical_hash(normalize_actual_event_for_persistence(event)),
            PUBLIC_NO_CAPTURE_EVENT_HASH,
        )
        self.assertEqual(
            normalize_actual_event_for_persistence(event),
            normalize_persisted_object(event),
        )

    def test_27_pending_captures_copied_at_finalize(self) -> None:
        act = attach_capture_to_active_activity(_activity(), _capture())
        event = build_actual_event_from_activity(
            act,
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        self.assertEqual(len(event["captures"]), 1)
        self.assertEqual(event["captures"][0]["capture_id"], act["pending_captures"][0]["capture_id"])

    def test_28_capture_after_actual_end_rejected(self) -> None:
        act = attach_capture_to_active_activity(
            _activity(),
            _capture(captured_at="2026-04-01T11:00:01+09:00"),
        )
        with self.assertRaises(LifeEngineError) as ctx:
            build_actual_event_from_activity(
                act,
                actual_end="2026-04-01T11:00:00+09:00",
                finalized_at="2026-04-01T11:00:00+09:00",
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_29_capture_before_actual_start_rejected(self) -> None:
        # Bypass attach (which also rejects); inject pending directly.
        cap = _capture(captured_at="2026-04-01T10:30:00+09:00")
        act = _activity(pending_captures=[cap])
        act["actual_start"] = "2026-04-01T10:45:00+09:00"
        with self.assertRaises(LifeEngineError) as ctx:
            build_actual_event_from_activity(
                act,
                actual_end="2026-04-01T11:00:00+09:00",
                finalized_at="2026-04-01T11:00:00+09:00",
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_30_duplicate_id_rejected(self) -> None:
        cap = _capture()
        twin = dict(cap)
        twin["visual_context"] = _visual_context(location_id="other-loc")
        twin["capture_context_hash"] = compute_capture_context_hash(twin["visual_context"])
        act = _activity(pending_captures=[cap, twin])
        with self.assertRaises(LifeEngineError) as ctx:
            build_actual_event_from_activity(
                act,
                actual_end="2026-04-01T11:00:00+09:00",
                finalized_at="2026-04-01T11:00:00+09:00",
            )
        self.assertIn(ctx.exception.code, (ErrorCode.DUPLICATE_ID, ErrorCode.INVALID_STATE))

    def test_31_finalized_actual_event_mutation_rejected(self) -> None:
        event = build_actual_event_from_activity(
            attach_capture_to_active_activity(_activity(), _capture()),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        mutated = dict(event)
        mutated["summary"] = "changed"
        with self.assertRaises(LifeEngineError) as ctx:
            assert_finalized_immutable(event, mutated)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_32_capture_ordering_canonical(self) -> None:
        act = _activity()
        act = attach_capture_to_active_activity(
            act,
            _capture(occurrence_key="occ-2", captured_at="2026-04-01T10:40:00+09:00"),
        )
        act = attach_capture_to_active_activity(
            act,
            _capture(occurrence_key="occ-1", captured_at="2026-04-01T10:20:00+09:00"),
        )
        event = build_actual_event_from_activity(
            act,
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        times = [c["captured_at"] for c in event["captures"]]
        self.assertEqual(times, sorted(times))

    def test_33_reordered_captures_same_event_semantic_hash(self) -> None:
        c1 = _capture(occurrence_key="occ-1", captured_at="2026-04-01T10:20:00+09:00")
        c2 = _capture(occurrence_key="occ-2", captured_at="2026-04-01T10:40:00+09:00")
        event_a = build_actual_event_from_activity(
            _activity(pending_captures=[c1, c2]),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        event_b = dict(event_a)
        event_b["captures"] = list(reversed(event_a["captures"]))
        self.assertEqual(
            canonical_hash(normalize_actual_event_for_persistence(event_a)),
            canonical_hash(normalize_actual_event_for_persistence(event_b)),
        )

    def test_34_reordered_captures_same_next_history_hash(self) -> None:
        c1 = _capture(occurrence_key="occ-1", captured_at="2026-04-01T10:20:00+09:00")
        c2 = _capture(occurrence_key="occ-2", captured_at="2026-04-01T10:40:00+09:00")
        event_a = build_actual_event_from_activity(
            _activity(pending_captures=[c1, c2]),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        event_b = dict(event_a)
        event_b["captures"] = list(reversed(event_a["captures"]))
        self.assertEqual(
            next_history_hash(EMPTY_HISTORY_HASH, event_a),
            next_history_hash(EMPTY_HISTORY_HASH, event_b),
        )

    def test_35_stable_no_capture_event_history_hash_unchanged(self) -> None:
        event = build_actual_event_from_activity(
            _baseline_activity(),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        self.assertEqual(persisted_hash(event), PUBLIC_NO_CAPTURE_EVENT_HASH)
        self.assertEqual(
            next_history_hash(EMPTY_HISTORY_HASH, event),
            PUBLIC_NO_CAPTURE_HISTORY_HASH,
        )

class CaptureRegressionTests(unittest.TestCase):
    """Capture validation and timeline regressions."""

    def _valid_event_with_capture(self, cap: dict | None = None) -> dict:
        capture = cap or _capture()
        return build_actual_event_from_activity(
            attach_capture_to_active_activity(_activity(), capture),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )

    def test_blocker1_normalize_rejects_int_outfit_item_id(self) -> None:
        vc = _visual_context(appearance={"outfit_item_ids": [123]})  # type: ignore[list-item]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_visual_context(vc)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_blocker1_normalize_rejects_bool_companion_ref(self) -> None:
        vc = _visual_context(companion_refs=[True])  # type: ignore[list-item]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_visual_context(vc)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_blocker1_normalize_rejects_null_outfit_item_id(self) -> None:
        vc = _visual_context(appearance={"outfit_item_ids": [None]})  # type: ignore[list-item]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_visual_context(vc)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_blocker1_build_capture_rejects_non_string_ids(self) -> None:
        vc = _visual_context(appearance={"outfit_item_ids": [{"id": "x"}]})  # type: ignore[list-item]
        with self.assertRaises(LifeEngineError) as ctx:
            build_test_capture_fact(
                activity_instance_id="activity:capture-fixture-1",
                occurrence_key="occ-coerce",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="DAILY_LIFE",
                subject_refs=[],
                visual_context=vc,
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_blocker1_validate_capture_rejects_coerced_style_int_ids(self) -> None:
        cap = _capture()
        bad = dict(cap)
        bad["visual_context"] = _visual_context(appearance={"outfit_item_ids": [1, 2]})  # type: ignore[list-item]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_capture_fact(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_blocker1_camera_roll_record_normalize_rejects_non_string_ids(self) -> None:
        event = self._valid_event_with_capture()
        rec = project_camera_roll_records(event)[0]
        bad = dict(rec)
        bad_vc = dict(bad["visual_context"])
        bad_app = dict(bad_vc["appearance"])
        bad_app["outfit_item_ids"] = [99]
        bad_vc["appearance"] = bad_app
        bad["visual_context"] = bad_vc
        # Recompute would still fail before hash because normalize rejects.
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_records([], [bad])
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_blocker2_duplicate_identical_pending_rows_rejected(self) -> None:
        cap = _capture()
        with self.assertRaises(LifeEngineError) as ctx:
            validate_active_activity(_activity(pending_captures=[cap, copy.deepcopy(cap)]))
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_blocker2_attach_exact_same_remains_idempotent(self) -> None:
        act = attach_capture_to_active_activity(_activity(), _capture())
        again = attach_capture_to_active_activity(act, act["pending_captures"][0])
        self.assertEqual(len(again["pending_captures"]), 1)

    def test_blocker2_duplicate_identical_finalized_captures_rejected(self) -> None:
        event = self._valid_event_with_capture()
        bad = dict(event)
        bad["captures"] = [event["captures"][0], copy.deepcopy(event["captures"][0])]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_actual_event_for_persistence(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_blocker2_duplicate_conflicting_finalized_captures_rejected(self) -> None:
        event = self._valid_event_with_capture()
        twin = dict(event["captures"][0])
        twin["visual_context"] = _visual_context(location_id="elsewhere")
        twin["capture_context_hash"] = compute_capture_context_hash(twin["visual_context"])
        bad = dict(event)
        bad["captures"] = [event["captures"][0], twin]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_actual_event_for_persistence(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_blocker2_projection_duplicate_id_rejected(self) -> None:
        event = self._valid_event_with_capture()
        bad = dict(event)
        bad["captures"] = [event["captures"][0], copy.deepcopy(event["captures"][0])]
        with self.assertRaises(LifeEngineError) as ctx:
            project_camera_roll_records(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_blocker2_timeline_append_wrong_owner_rejected(self) -> None:
        event = self._valid_event_with_capture()
        bad = dict(event)
        bad_cap = dict(event["captures"][0])
        bad_cap["activity_instance_id"] = "activity:other-owner"
        # Keep capture_id derived for other owner so validate_capture_fact passes ID match.
        bad_cap["capture_id"] = derive_capture_id(
            "activity:other-owner", bad_cap["occurrence_key"]
        )
        bad["captures"] = [bad_cap]
        day = empty_day("2026-04-01")
        with self.assertRaises(LifeEngineError) as ctx:
            append_actual_event(day, bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_blocker2_timeline_append_capture_before_start_rejected(self) -> None:
        event = self._valid_event_with_capture(
            _capture(captured_at="2026-04-01T10:00:00+09:00")
        )
        bad = dict(event)
        bad_cap = dict(event["captures"][0])
        bad_cap["captured_at"] = "2026-04-01T09:59:59+09:00"
        bad["captures"] = [bad_cap]
        with self.assertRaises(LifeEngineError) as ctx:
            append_actual_event(empty_day("2026-04-01"), bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_blocker2_timeline_append_capture_after_end_rejected(self) -> None:
        event = self._valid_event_with_capture()
        bad = dict(event)
        bad_cap = dict(event["captures"][0])
        bad_cap["captured_at"] = "2026-04-01T11:00:01+09:00"
        bad["captures"] = [bad_cap]
        with self.assertRaises(LifeEngineError) as ctx:
            append_actual_event(empty_day("2026-04-01"), bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_blocker2_timeline_append_bad_context_hash_rejected(self) -> None:
        event = self._valid_event_with_capture()
        bad = dict(event)
        bad_cap = dict(event["captures"][0])
        bad_cap["capture_context_hash"] = "0" * 64
        bad["captures"] = [bad_cap]
        with self.assertRaises(LifeEngineError) as ctx:
            append_actual_event(empty_day("2026-04-01"), bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_blocker2_projection_capture_outside_bounds_rejected(self) -> None:
        event = self._valid_event_with_capture()
        bad = dict(event)
        bad_cap = dict(event["captures"][0])
        bad_cap["captured_at"] = "2026-04-01T11:30:00+09:00"
        bad["captures"] = [bad_cap]
        with self.assertRaises(LifeEngineError) as ctx:
            project_camera_roll_records(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_blocker2_history_path_rejects_out_of_bounds_capture(self) -> None:
        event = self._valid_event_with_capture()
        bad = dict(event)
        bad_cap = dict(event["captures"][0])
        bad_cap["captured_at"] = "2026-04-01T08:00:00+09:00"
        bad["captures"] = [bad_cap]
        with self.assertRaises(LifeEngineError) as ctx:
            next_history_hash(EMPTY_HISTORY_HASH, bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_major_impossible_shard_months_rejected_empty_shard(self) -> None:
        for bad_month in ("2026-00", "2026-13", "2026-99", "2026-1", "26-04"):
            with self.assertRaises(LifeEngineError) as ctx:
                validate_camera_roll_shard_month(bad_month)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
            with self.assertRaises(LifeEngineError) as ctx2:
                merge_camera_roll_shard(
                    {
                        "schema_version": 1,
                        "month": bad_month,
                        "closed_at": None,
                        "records": [],
                    },
                    [],
                )
            self.assertIn(
                ctx2.exception.code,
                (ErrorCode.INVALID_STATE, ErrorCode.SCHEMA_INVALID),
            )

    def test_major_schema_rejects_impossible_month(self) -> None:
        for bad_month in ("2026-00", "2026-13", "2026-99"):
            with self.assertRaises(LifeEngineError) as ctx:
                validate_instance(
                    {
                        "schema_version": 1,
                        "month": bad_month,
                        "closed_at": None,
                        "records": [],
                    },
                    "camera_roll_shard",
                )
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_major_valid_empty_shard_month_accepted(self) -> None:
        out = merge_camera_roll_shard(
            {"schema_version": 1, "month": "2026-04", "closed_at": None, "records": []},
            [],
        )
        self.assertEqual(out["month"], "2026-04")
        self.assertEqual(out["records"], [])
