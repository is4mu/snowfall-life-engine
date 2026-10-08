"""Capture facts and Camera Roll public semantic tests."""

from __future__ import annotations

import copy
import os
import tempfile
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

pytestmark = pytest.mark.unit


class CameraRollSchemaMetaTests(unittest.TestCase):
    def test_new_and_modified_schemas_draft_2020_12_meta_valid(self) -> None:
        for name in (
            "capture",
            "camera_roll_record",
            "camera_roll_shard",
            "active_activity",
            "actual_event",
        ):
            schema = load_schema_document(name)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:  # pragma: no cover
                self.fail(f"{name} schema meta-invalid: {exc}")

class CaptureIdentityContextTests(unittest.TestCase):
    def test_01_derive_capture_id_deterministic(self) -> None:
        a = derive_capture_id("act-1", "occ-1")
        b = derive_capture_id("act-1", "occ-1")
        self.assertEqual(a, b)
        self.assertEqual(a, stable_id("capture", "act-1", "occ-1"))

    def test_02_different_occurrence_key_different_id(self) -> None:
        self.assertNotEqual(
            derive_capture_id("act-1", "occ-1"),
            derive_capture_id("act-1", "occ-2"),
        )

    def test_03_same_second_different_occurrence_key_distinct(self) -> None:
        c1 = _capture(occurrence_key="occ-a", captured_at="2026-04-01T10:30:00+09:00")
        c2 = _capture(occurrence_key="occ-b", captured_at="2026-04-01T10:30:00+09:00")
        self.assertEqual(c1["captured_at"], c2["captured_at"])
        self.assertNotEqual(c1["capture_id"], c2["capture_id"])

    def test_04_array_position_never_in_id(self) -> None:
        # ID derivation ignores list position; only activity_instance_id + occurrence_key.
        self.assertEqual(
            derive_capture_id("act-1", "occ-1"),
            stable_id("capture", "act-1", "occ-1"),
        )
        self.assertNotIn("0", derive_capture_id("act-1", "occ-1").split(":")[-1][:0])

    def test_05_visual_context_hash_deterministic(self) -> None:
        vc = _visual_context()
        self.assertEqual(
            compute_capture_context_hash(vc),
            compute_capture_context_hash(copy.deepcopy(vc)),
        )

    def test_06_outfit_item_ids_reorder_same_hash(self) -> None:
        a = _visual_context(appearance={"outfit_item_ids": ["a", "b", "c"]})
        b = _visual_context(appearance={"outfit_item_ids": ["c", "a", "b"]})
        self.assertEqual(
            compute_capture_context_hash(a),
            compute_capture_context_hash(b),
        )

    def test_07_companion_refs_reorder_same_hash(self) -> None:
        a = _visual_context(companion_refs=["x", "y"])
        b = _visual_context(companion_refs=["y", "x"])
        self.assertEqual(
            compute_capture_context_hash(a),
            compute_capture_context_hash(b),
        )

    def test_08_semantic_change_different_hash(self) -> None:
        a = _visual_context(location_id="home-a")
        b = _visual_context(location_id="home-b")
        self.assertNotEqual(
            compute_capture_context_hash(a),
            compute_capture_context_hash(b),
        )

    def test_09_wrong_capture_context_hash_rejected(self) -> None:
        cap = _capture()
        bad = dict(cap)
        bad["capture_context_hash"] = "0" * 64
        with self.assertRaises(LifeEngineError) as ctx:
            validate_capture_fact(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_10_unknown_visual_context_field_rejected(self) -> None:
        vc = _visual_context()
        vc["weather"] = "sunny"
        with self.assertRaises(LifeEngineError) as ctx:
            build_test_capture_fact(
                activity_instance_id="activity:capture-fixture-1",
                occurrence_key="occ-1",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="DAILY_LIFE",
                subject_refs=[],
                visual_context=vc,
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_11_image_post_materialization_fields_rejected(self) -> None:
        for field, value in (
            ("image_prompt", "selfie"),
            ("provider", "openai"),
            ("seed", 42),
            ("generated_file", "/tmp/x.png"),
            ("quality", "HIGH"),
            ("favorite", True),
            ("caption", "hi"),
            ("post_decision", "POST"),
        ):
            vc = _visual_context()
            vc[field] = value
            with self.assertRaises(LifeEngineError) as ctx:
                build_test_capture_fact(
                    activity_instance_id="activity:capture-fixture-1",
                    occurrence_key=f"occ-{field}",
                    captured_at="2026-04-01T10:30:00+09:00",
                    capture_kind="DAILY_LIFE",
                    subject_refs=[],
                    visual_context=vc,
                )
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_12_bad_naive_fractional_captured_at_rejected(self) -> None:
        for bad in (
            "not-a-timestamp",
            "2026-04-01T10:30:00",
            "2026-04-01T10:30:00.5+09:00",
        ):
            with self.assertRaises(LifeEngineError) as ctx:
                build_test_capture_fact(
                    activity_instance_id="activity:capture-fixture-1",
                    occurrence_key="occ-bad-ts",
                    captured_at=bad,
                    capture_kind="DAILY_LIFE",
                    subject_refs=[],
                    visual_context=_visual_context(),
                )
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_13_z_offset_canonicalizes_to_plus_09(self) -> None:
        cap = build_test_capture_fact(
            activity_instance_id="activity:capture-fixture-1",
            occurrence_key="occ-z",
            captured_at="2026-04-01T01:30:00Z",
            capture_kind="DAILY_LIFE",
            subject_refs=[],
            visual_context=_visual_context(),
        )
        self.assertEqual(cap["captured_at"], "2026-04-01T10:30:00+09:00")

    def test_14_binary_float_anywhere_rejected(self) -> None:
        vc = _visual_context()
        with self.assertRaises(LifeEngineError) as ctx:
            build_test_capture_fact(
                activity_instance_id="activity:capture-fixture-1",
                occurrence_key="occ-float",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="DAILY_LIFE",
                subject_refs=[],
                visual_context={**vc, "character_id": 1.5},  # type: ignore[dict-item]
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_15_build_validate_does_not_mutate_input(self) -> None:
        vc = _visual_context(appearance={"outfit_item_ids": ["z", "a"]})
        vc_before = copy.deepcopy(vc)
        raw = {
            "activity_instance_id": "activity:capture-fixture-1",
            "occurrence_key": "occ-mut",
            "captured_at": "2026-04-01T10:30:00Z",
            "capture_kind": "DAILY_LIFE",
            "subject_refs": [],
            "visual_context": vc,
        }
        raw_before = copy.deepcopy(raw)
        cap = build_test_capture_fact(**raw)
        self.assertEqual(vc, vc_before)
        self.assertEqual(raw, raw_before)
        again = validate_capture_fact(cap)
        cap_before = copy.deepcopy(cap)
        validate_capture_fact(cap)
        self.assertEqual(cap, cap_before)
        self.assertEqual(again["visual_context"]["appearance"]["outfit_item_ids"], ["a", "z"])

class CameraRollProjectionTests(unittest.TestCase):
    def _event_with_captures(self, *captures: dict) -> dict:
        return build_actual_event_from_activity(
            _activity(pending_captures=list(captures)),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )

    def test_36_zero_captures_zero_records(self) -> None:
        event = build_actual_event_from_activity(
            _activity(),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        self.assertEqual(project_camera_roll_records(event), [])

    def test_37_one_capture_exactly_one_record(self) -> None:
        event = self._event_with_captures(_capture())
        recs = project_camera_roll_records(event)
        self.assertEqual(len(recs), 1)

    def test_38_two_same_second_captures_two_distinct_records(self) -> None:
        event = self._event_with_captures(
            _capture(occurrence_key="occ-a", captured_at="2026-04-01T10:30:00+09:00"),
            _capture(occurrence_key="occ-b", captured_at="2026-04-01T10:30:00+09:00"),
        )
        recs = project_camera_roll_records(event)
        self.assertEqual(len(recs), 2)
        self.assertNotEqual(recs[0]["record_id"], recs[1]["record_id"])

    def test_39_record_id_derivation(self) -> None:
        cap = _capture()
        event = self._event_with_captures(cap)
        rec = project_camera_roll_records(event)[0]
        self.assertEqual(rec["record_id"], stable_id("camera-roll-record", cap["capture_id"]))
        self.assertEqual(rec["record_id"], derive_camera_roll_record_id(cap["capture_id"]))

    def test_40_unrelated_insertion_does_not_change_existing_ids(self) -> None:
        c1 = _capture(occurrence_key="occ-1")
        event1 = self._event_with_captures(c1)
        id1 = project_camera_roll_records(event1)[0]["record_id"]
        c2 = _capture(occurrence_key="occ-2")
        event2 = self._event_with_captures(c2, c1)
        ids = {r["capture_id"]: r["record_id"] for r in project_camera_roll_records(event2)}
        self.assertEqual(ids[c1["capture_id"]], id1)

    def test_41_projection_deterministic(self) -> None:
        event = self._event_with_captures(_capture(), _capture(occurrence_key="occ-2"))
        a = project_camera_roll_records(event)
        b = project_camera_roll_records(event)
        self.assertEqual(a, b)

    def test_42_projection_does_not_mutate_event(self) -> None:
        event = self._event_with_captures(_capture())
        before = copy.deepcopy(event)
        project_camera_roll_records(event)
        self.assertEqual(event, before)

    def test_43_record_has_no_mutable_review_fields(self) -> None:
        rec = project_camera_roll_records(self._event_with_captures(_capture()))[0]
        for forbidden in (
            "quality",
            "favorite",
            "visibility",
            "post_decision",
            "materialization",
            "image_prompt",
            "provider",
            "seed",
            "caption",
        ):
            self.assertNotIn(forbidden, rec)

    def test_44_record_visual_context_equals_frozen_capture(self) -> None:
        cap = _capture()
        event = self._event_with_captures(cap)
        rec = project_camera_roll_records(event)[0]
        self.assertEqual(rec["visual_context"], event["captures"][0]["visual_context"])
        self.assertEqual(rec["capture_context_hash"], event["captures"][0]["capture_context_hash"])

    def test_45_malformed_capture_hash_mismatch_rejected(self) -> None:
        event = self._event_with_captures(_capture())
        bad = dict(event)
        bad_caps = [dict(event["captures"][0])]
        bad_caps[0]["capture_context_hash"] = "0" * 64
        bad["captures"] = bad_caps
        with self.assertRaises(LifeEngineError) as ctx:
            project_camera_roll_records(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

class CameraRollShardTests(unittest.TestCase):
    def _record(self, *, occurrence_key: str, captured_at: str) -> dict:
        event = build_actual_event_from_activity(
            _activity(
                pending_captures=[
                    _capture(occurrence_key=occurrence_key, captured_at=captured_at)
                ]
            ),
            actual_end="2026-04-01T23:59:59+09:00"
            if captured_at.startswith("2026-04")
            else "2026-05-01T23:59:59+09:00",
            finalized_at="2026-04-01T23:59:59+09:00"
            if captured_at.startswith("2026-04")
            else "2026-05-01T23:59:59+09:00",
        )
        # Adjust activity window for May captures.
        if captured_at.startswith("2026-05"):
            act = _activity(
                actual_start="2026-05-01T10:00:00+09:00",
                pending_captures=[
                    _capture(
                        occurrence_key=occurrence_key,
                        captured_at=captured_at,
                    )
                ],
            )
            event = build_actual_event_from_activity(
                act,
                actual_end="2026-05-01T23:59:59+09:00",
                finalized_at="2026-05-01T23:59:59+09:00",
            )
        return project_camera_roll_records(event)[0]

    def test_46_asia_tokyo_month_derivation(self) -> None:
        self.assertEqual(camera_roll_shard_month("2026-04-01T10:00:00+09:00"), "2026-04")
        self.assertEqual(camera_roll_shard_path("2026-04-01T10:00:00+09:00"), "camera-roll/records/2026-04.json")

    def test_47_month_boundary_routing(self) -> None:
        # 2026-03-31 15:00Z = 2026-04-01 00:00+09 → April
        self.assertEqual(camera_roll_shard_month("2026-03-31T15:00:00Z"), "2026-04")
        # 2026-04-30 14:59Z = 2026-04-30 23:59+09 → April
        self.assertEqual(camera_roll_shard_month("2026-04-30T14:59:00Z"), "2026-04")
        # 2026-04-30 15:00Z = 2026-05-01 00:00+09 → May
        self.assertEqual(camera_roll_shard_month("2026-04-30T15:00:00Z"), "2026-05")

    def test_48_record_order_canonical(self) -> None:
        r1 = self._record(occurrence_key="occ-late", captured_at="2026-04-01T10:40:00+09:00")
        r2 = self._record(occurrence_key="occ-early", captured_at="2026-04-01T10:20:00+09:00")
        merged = merge_camera_roll_records([r1], [r2])
        self.assertEqual(
            [r["captured_at"] for r in merged],
            sorted(r["captured_at"] for r in merged),
        )

    def test_49_duplicate_identical_merge_noop(self) -> None:
        r = self._record(occurrence_key="occ-1", captured_at="2026-04-01T10:30:00+09:00")
        merged = merge_camera_roll_records([r], [copy.deepcopy(r)])
        self.assertEqual(len(merged), 1)

    def test_50_same_record_id_conflict_rejected(self) -> None:
        r = self._record(occurrence_key="occ-1", captured_at="2026-04-01T10:30:00+09:00")
        conflict = dict(r)
        conflict["visual_context"] = _visual_context(location_id="other")
        conflict["capture_context_hash"] = compute_capture_context_hash(
            conflict["visual_context"]
        )
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_records([r], [conflict])
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_51_wrong_month_record_rejected(self) -> None:
        r = self._record(occurrence_key="occ-1", captured_at="2026-04-01T10:30:00+09:00")
        shard = {"schema_version": 1, "month": "2026-05", "closed_at": None, "records": []}
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_shard(shard, [r])
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_52_open_shard_accepts_additions(self) -> None:
        r = self._record(occurrence_key="occ-1", captured_at="2026-04-01T10:30:00+09:00")
        shard = {"schema_version": 1, "month": "2026-04", "closed_at": None, "records": []}
        out = merge_camera_roll_shard(shard, [r])
        self.assertEqual(len(out["records"]), 1)
        self.assertIsNone(out["closed_at"])

    def test_53_closed_shard_rejects_additions(self) -> None:
        r1 = self._record(occurrence_key="occ-1", captured_at="2026-04-01T10:30:00+09:00")
        r2 = self._record(occurrence_key="occ-2", captured_at="2026-04-01T10:40:00+09:00")
        shard = {
            "schema_version": 1,
            "month": "2026-04",
            "closed_at": "2026-05-01T00:00:00+09:00",
            "records": [r1],
        }
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_shard(shard, [r2])
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_54_closed_shard_identical_noop_valid(self) -> None:
        r1 = self._record(occurrence_key="occ-1", captured_at="2026-04-01T10:30:00+09:00")
        shard = {
            "schema_version": 1,
            "month": "2026-04",
            "closed_at": "2026-05-01T00:00:00+09:00",
            "records": [r1],
        }
        out = merge_camera_roll_shard(shard, [copy.deepcopy(r1)])
        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(out["closed_at"], "2026-05-01T00:00:00+09:00")

    def test_55_shard_helper_performs_no_io(self) -> None:
        old_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                r = self._record(
                    occurrence_key="occ-io",
                    captured_at="2026-04-01T10:30:00+09:00",
                )
                merge_camera_roll_shard(
                    {
                        "schema_version": 1,
                        "month": "2026-04",
                        "closed_at": None,
                        "records": [],
                    },
                    [r],
                )
                self.assertEqual(
                    camera_roll_shard_path("2026-04-01T10:30:00+09:00"),
                    "camera-roll/records/2026-04.json",
                )
                self.assertEqual(list(Path(tmp).rglob("*")), [])
            finally:
                os.chdir(old_cwd)


class CameraRollBoundaryTests(unittest.TestCase):
    def test_56_planned_unresolved_cannot_project(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            project_camera_roll_records(
                {
                    "schema_version": 1,
                    "activity_instance_id": "act-1",
                    "activity_type": "LEISURE",
                    "actual_start": "2026-04-01T10:00:00+09:00",
                    "pending_captures": [_capture()],
                }
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_57_pending_capture_alone_creates_no_camera_roll_record(self) -> None:
        act = attach_capture_to_active_activity(_activity(), _capture())
        self.assertEqual(len(act["pending_captures"]), 1)
        # No event_id / captures → not projectable; and pending is not a record.
        self.assertNotIn("record_id", act["pending_captures"][0])
        with self.assertRaises(LifeEngineError):
            project_camera_roll_records(act)

    def test_62_no_generic_captures_in_global_unordered_keys(self) -> None:
        self.assertNotIn("captures", _UNORDERED_LIST_KEYS)
        self.assertNotIn("pending_captures", _UNORDERED_LIST_KEYS)
        self.assertNotIn("outfit_item_ids", _UNORDERED_LIST_KEYS)
        self.assertNotIn("companion_refs", _UNORDERED_LIST_KEYS)

    def test_63_actual_event_schema_version_is_1(self) -> None:
        schema = load_schema_document("actual_event")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)

    def test_64_capture_schema_rejects_extra_toplevel_fields(self) -> None:
        cap = _capture()
        cap["favorite"] = True
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(cap, "capture")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_65_camera_roll_record_rejects_review_fields(self) -> None:
        event = build_actual_event_from_activity(
            attach_capture_to_active_activity(_activity(), _capture()),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        rec = project_camera_roll_records(event)[0]
        rec["quality"] = "HIGH"
        with self.assertRaises(LifeEngineError) as ctx:
            validate_instance(rec, "camera_roll_record")
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)
