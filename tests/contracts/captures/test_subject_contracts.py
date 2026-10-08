"""Capture kind and subject reference public compatibility contracts."""

from __future__ import annotations

import copy
import unittest

import pytest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.camera_roll import (
    merge_camera_roll_records,
    merge_camera_roll_shard,
    project_camera_roll_records,
)
from engine.life.canonical import (
    _UNORDERED_LIST_KEYS,
    canonical_hash,
    persisted_hash,
)
from engine.life.captures import (
    CAPTURE_KINDS,
    attach_capture_to_active_activity,
    build_capture_fact,
    compute_capture_context_hash,
    derive_capture_id,
    validate_capture_fact,
)
from tests.support.builders.captures import (
    PUBLIC_NO_CAPTURE_EVENT_HASH,
    PUBLIC_NO_CAPTURE_HISTORY_HASH,
    build_test_capture_fact,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.events import (
    build_actual_event_from_activity,
    normalize_actual_event_for_persistence,
)
from engine.life.history import next_history_hash
from engine.life.schema import load_schema_document


CHAR = "fixture-character"


def _visual_context(**overrides) -> dict:
    base = {
        "character_id": CHAR,
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
        "activity_instance_id": "activity:capture-subject-fixture-1",
        "activity_type": "LEISURE",
        "actual_start": "2026-04-01T10:00:00+09:00",
        "location_id": "fixture-home",
        "summary": "slice4a1 fixture",
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
    activity_instance_id: str = "activity:capture-subject-fixture-1",
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


def _event_with_captures(*captures: dict) -> dict:
    act = _activity()
    for cap in captures:
        act = attach_capture_to_active_activity(act, cap)
    return build_actual_event_from_activity(
        act,
        actual_end="2026-04-01T12:00:00+09:00",
        finalized_at="2026-04-01T12:00:00+09:00",
    )

pytestmark = pytest.mark.contract


class CaptureSubjectSchemaTests(unittest.TestCase):
    def test_modified_schemas_draft_2020_12_meta_valid(self) -> None:
        for name in ("capture", "camera_roll_record"):
            schema = load_schema_document(name)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:  # pragma: no cover
                self.fail(f"{name} schema meta-invalid: {exc}")

class CaptureSubjectValidationTests(unittest.TestCase):
    def test_01_every_allowed_capture_kind_validates(self) -> None:
        self.assertEqual(
            CAPTURE_KINDS,
            frozenset({"SELFIE", "PEOPLE", "FOOD", "SCENERY", "OBJECT", "DAILY_LIFE"}),
        )
        for kind in sorted(CAPTURE_KINDS):
            if kind == "SELFIE":
                vc = _visual_context(photographer_ref=CHAR)
                refs = [CHAR]
            elif kind == "PEOPLE":
                vc = _visual_context()
                refs = ["friend-a"]
            else:
                vc = _visual_context()
                refs = []
            cap = build_test_capture_fact(
                activity_instance_id="activity:capture-subject-fixture-1",
                occurrence_key=f"occ-{kind}",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind=kind,
                subject_refs=refs,
                visual_context=vc,
            )
            self.assertEqual(cap["capture_kind"], kind)
            self.assertEqual(cap["schema_version"], 1)

    def test_02_unknown_capture_kind_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_test_capture_fact(
                activity_instance_id="activity:capture-subject-fixture-1",
                occurrence_key="occ-bad-kind",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="SELFIE_PLUS",
                subject_refs=[],
                visual_context=_visual_context(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_03_missing_capture_kind_rejected(self) -> None:
        cap = _capture()
        bad = dict(cap)
        del bad["capture_kind"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_capture_fact(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_04_missing_subject_refs_rejected(self) -> None:
        cap = _capture()
        bad = dict(cap)
        del bad["subject_refs"]
        with self.assertRaises(LifeEngineError) as ctx:
            validate_capture_fact(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_05_empty_subject_refs_valid_for_non_people_kinds(self) -> None:
        for kind in ("FOOD", "SCENERY", "OBJECT", "DAILY_LIFE"):
            cap = _capture(capture_kind=kind, subject_refs=[], occurrence_key=f"occ-{kind}")
            self.assertEqual(cap["subject_refs"], [])

    def test_06_non_string_subject_ref_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_test_capture_fact(
                activity_instance_id="activity:capture-subject-fixture-1",
                occurrence_key="occ-int-subj",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="FOOD",
                subject_refs=[123],  # type: ignore[list-item]
                visual_context=_visual_context(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_07_empty_string_subject_ref_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_test_capture_fact(
                activity_instance_id="activity:capture-subject-fixture-1",
                occurrence_key="occ-empty-subj",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="FOOD",
                subject_refs=[""],
                visual_context=_visual_context(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_08_duplicate_subject_ref_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            build_test_capture_fact(
                activity_instance_id="activity:capture-subject-fixture-1",
                occurrence_key="occ-dup-subj",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="PEOPLE",
                subject_refs=["friend-a", "friend-a"],
                visual_context=_visual_context(),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_09_subject_order_canonical(self) -> None:
        cap = _capture(
            capture_kind="PEOPLE",
            subject_refs=["z-friend", "a-friend"],
        )
        self.assertEqual(cap["subject_refs"], ["a-friend", "z-friend"])

    def test_10_subject_reorder_same_capture_semantics(self) -> None:
        a = _capture(capture_kind="PEOPLE", subject_refs=["b", "a"], occurrence_key="occ-ord")
        b = _capture(capture_kind="PEOPLE", subject_refs=["a", "b"], occurrence_key="occ-ord")
        self.assertEqual(canonical_hash(a), canonical_hash(b))
        self.assertEqual(a, b)

    def test_11_no_str_coercion(self) -> None:
        for bad_item in (1, True, None, {"id": "x"}, ["nested"]):
            with self.assertRaises(LifeEngineError) as ctx:
                build_test_capture_fact(
                    activity_instance_id="activity:capture-subject-fixture-1",
                    occurrence_key=f"occ-coerce-{type(bad_item).__name__}",
                    captured_at="2026-04-01T10:30:00+09:00",
                    capture_kind="FOOD",
                    subject_refs=[bad_item],  # type: ignore[list-item]
                    visual_context=_visual_context(),
                )
            self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

class CapturePeopleSemanticsTests(unittest.TestCase):
    def test_12_selfie_photographer_character_in_subjects_valid(self) -> None:
        cap = _capture(
            capture_kind="SELFIE",
            subject_refs=[CHAR],
            visual_context=_visual_context(photographer_ref=CHAR),
        )
        self.assertEqual(cap["capture_kind"], "SELFIE")
        self.assertIn(CHAR, cap["subject_refs"])

    def test_13_selfie_null_photographer_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            _capture(
                capture_kind="SELFIE",
                subject_refs=[CHAR],
                visual_context=_visual_context(photographer_ref=None),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_14_selfie_other_photographer_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            _capture(
                capture_kind="SELFIE",
                subject_refs=[CHAR],
                visual_context=_visual_context(photographer_ref="friend-a"),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_15_selfie_missing_character_from_subjects_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            _capture(
                capture_kind="SELFIE",
                subject_refs=["friend-a"],
                visual_context=_visual_context(photographer_ref=CHAR),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_16_selfie_group_subject_list_valid(self) -> None:
        cap = _capture(
            capture_kind="SELFIE",
            subject_refs=["friend-z", CHAR, "friend-a"],
            visual_context=_visual_context(photographer_ref=CHAR),
        )
        self.assertEqual(cap["subject_refs"], ["fixture-character", "friend-a", "friend-z"])

    def test_17_people_with_subjects_valid(self) -> None:
        cap = _capture(capture_kind="PEOPLE", subject_refs=["friend-a"])
        self.assertEqual(cap["capture_kind"], "PEOPLE")

    def test_18_people_empty_subjects_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            _capture(capture_kind="PEOPLE", subject_refs=[])
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

class CaptureOtherKindSemanticsTests(unittest.TestCase):
    def test_19_22_empty_subjects_valid_for_other_kinds(self) -> None:
        for kind in ("FOOD", "SCENERY", "OBJECT", "DAILY_LIFE"):
            cap = _capture(capture_kind=kind, subject_refs=[], occurrence_key=f"occ-{kind}-empty")
            self.assertEqual(cap["subject_refs"], [])

    def test_23_explicit_stable_refs_allowed_for_non_people_kinds(self) -> None:
        for kind in ("FOOD", "SCENERY", "OBJECT", "DAILY_LIFE"):
            cap = _capture(
                capture_kind=kind,
                subject_refs=["object-plate-1"],
                occurrence_key=f"occ-{kind}-ref",
            )
            self.assertEqual(cap["subject_refs"], ["object-plate-1"])

class CaptureSubjectIdentityTests(unittest.TestCase):
    def test_24_capture_id_unchanged_by_new_fields(self) -> None:
        # capture_id still derives only from activity_instance_id + occurrence_key
        # (kind/subjects/evidence do not enter the id formula).
        cap = _capture()
        expected = derive_capture_id(cap["activity_instance_id"], cap["occurrence_key"])
        self.assertEqual(cap["capture_id"], expected)
        selfie = _capture(
            capture_kind="SELFIE",
            subject_refs=[CHAR],
            visual_context=_visual_context(photographer_ref=CHAR),
        )
        self.assertEqual(selfie["capture_id"], expected)
        self.assertEqual(selfie["occurrence_key"], cap["occurrence_key"])

    def test_25_context_hash_ignores_capture_kind_change(self) -> None:
        vc = _visual_context()
        a = _capture(capture_kind="FOOD", subject_refs=[], visual_context=vc, occurrence_key="k")
        b = _capture(capture_kind="SCENERY", subject_refs=[], visual_context=vc, occurrence_key="k")
        self.assertEqual(a["capture_context_hash"], b["capture_context_hash"])
        self.assertEqual(a["capture_context_hash"], compute_capture_context_hash(vc))

    def test_26_context_hash_ignores_subject_refs_change(self) -> None:
        vc = _visual_context()
        a = _capture(capture_kind="FOOD", subject_refs=[], visual_context=vc, occurrence_key="k2")
        b = _capture(
            capture_kind="FOOD",
            subject_refs=["object-1"],
            visual_context=vc,
            occurrence_key="k2",
        )
        self.assertEqual(a["capture_context_hash"], b["capture_context_hash"])

    def test_27_full_capture_hash_changes_with_kind(self) -> None:
        vc = _visual_context()
        a = _capture(capture_kind="FOOD", subject_refs=[], visual_context=vc, occurrence_key="k3")
        b = _capture(capture_kind="OBJECT", subject_refs=[], visual_context=vc, occurrence_key="k3")
        self.assertNotEqual(canonical_hash(a), canonical_hash(b))

    def test_28_full_capture_hash_changes_with_subjects(self) -> None:
        vc = _visual_context()
        a = _capture(capture_kind="FOOD", subject_refs=[], visual_context=vc, occurrence_key="k4")
        b = _capture(
            capture_kind="FOOD",
            subject_refs=["object-1"],
            visual_context=vc,
            occurrence_key="k4",
        )
        self.assertNotEqual(canonical_hash(a), canonical_hash(b))

    def test_29_duplicate_same_id_different_kind_rejected(self) -> None:
        act = attach_capture_to_active_activity(_activity(), _capture(capture_kind="FOOD"))
        conflict = _capture(capture_kind="SCENERY")
        with self.assertRaises(LifeEngineError) as ctx:
            attach_capture_to_active_activity(act, conflict)
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_30_duplicate_same_id_different_subjects_rejected(self) -> None:
        act = attach_capture_to_active_activity(
            _activity(),
            _capture(capture_kind="FOOD", subject_refs=[]),
        )
        conflict = _capture(capture_kind="FOOD", subject_refs=["object-1"])
        with self.assertRaises(LifeEngineError) as ctx:
            attach_capture_to_active_activity(act, conflict)
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

class CaptureSubjectProjectionTests(unittest.TestCase):
    def test_31_32_pending_and_finalized_carry_new_fields(self) -> None:
        cap = _capture(
            capture_kind="SELFIE",
            subject_refs=[CHAR, "friend-a"],
            visual_context=_visual_context(photographer_ref=CHAR),
        )
        act = attach_capture_to_active_activity(_activity(), cap)
        pending = act["pending_captures"][0]
        self.assertEqual(pending["capture_kind"], "SELFIE")
        self.assertEqual(pending["subject_refs"], [CHAR, "friend-a"])
        event = build_actual_event_from_activity(
            act,
            actual_end="2026-04-01T12:00:00+09:00",
            finalized_at="2026-04-01T12:00:00+09:00",
        )
        self.assertEqual(event["schema_version"], 1)
        fin = event["captures"][0]
        self.assertEqual(fin["capture_kind"], "SELFIE")
        self.assertEqual(fin["subject_refs"], [CHAR, "friend-a"])

    def test_33_34_35_record_copies_kind_subjects_no_inference(self) -> None:
        cap = _capture(
            capture_kind="PEOPLE",
            subject_refs=["z-person", "a-person"],
        )
        event = _event_with_captures(cap)
        recs = project_camera_roll_records(event)
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["capture_kind"], "PEOPLE")
        self.assertEqual(rec["subject_refs"], ["a-person", "z-person"])
        self.assertEqual(rec["schema_version"], 1)
        # Projection must not invent kind/subjects from location or companions.
        self.assertNotEqual(rec["subject_refs"], event["captures"][0]["visual_context"]["companion_refs"])

    def test_36_record_normalization_rejects_invalid_kind_subjects(self) -> None:
        event = _event_with_captures(_capture())
        rec = project_camera_roll_records(event)[0]
        bad_kind = dict(rec)
        bad_kind["capture_kind"] = "UNKNOWN_KIND"
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_records([], [bad_kind])
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

        bad_people = dict(rec)
        bad_people["capture_kind"] = "PEOPLE"
        bad_people["subject_refs"] = []
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_records([], [bad_people])
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_37_38_record_merge_conflict_on_kind_or_subjects(self) -> None:
        event = _event_with_captures(_capture(capture_kind="FOOD", subject_refs=[]))
        rec = project_camera_roll_records(event)[0]
        other_kind = dict(rec)
        other_kind["capture_kind"] = "SCENERY"
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_records([rec], [other_kind])
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

        other_subj = dict(rec)
        other_subj["subject_refs"] = ["object-1"]
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_records([rec], [other_subj])
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_39_closed_shard_cannot_mutate_kind_subjects(self) -> None:
        event = _event_with_captures(_capture(capture_kind="FOOD"))
        rec = project_camera_roll_records(event)[0]
        month = rec["captured_at"][:7]
        shard = {
            "schema_version": 1,
            "month": month,
            "closed_at": "2026-05-01T00:00:00+09:00",
            "records": [rec],
        }
        mutated = dict(rec)
        mutated["capture_kind"] = "OBJECT"
        with self.assertRaises(LifeEngineError) as ctx:
            merge_camera_roll_shard(shard, [mutated])
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

class CaptureSubjectCompatibilityTests(unittest.TestCase):
    def test_40_41_no_capture_hashes_unchanged(self) -> None:
        from engine.life.history import EMPTY_HISTORY_HASH

        event = build_actual_event_from_activity(
            _baseline_activity(),
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        self.assertEqual(event["captures"], [])
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(persisted_hash(event), PUBLIC_NO_CAPTURE_EVENT_HASH)
        self.assertEqual(
            next_history_hash(EMPTY_HISTORY_HASH, event),
            PUBLIC_NO_CAPTURE_HISTORY_HASH,
        )

    def test_42_43_schema_versions_remain_1(self) -> None:
        cap = _capture()
        self.assertEqual(cap["schema_version"], 1)
        event = _event_with_captures(cap)
        self.assertEqual(event["schema_version"], 1)
        rec = project_camera_roll_records(event)[0]
        self.assertEqual(rec["schema_version"], 1)
        capture_schema = load_schema_document("capture")
        record_schema = load_schema_document("camera_roll_record")
        self.assertEqual(capture_schema["properties"]["schema_version"]["const"], 1)
        self.assertEqual(record_schema["properties"]["schema_version"]["const"], 1)

    def test_44_no_global_subject_refs_canonicalization(self) -> None:
        self.assertNotIn("subject_refs", _UNORDERED_LIST_KEYS)
        self.assertNotIn("captures", _UNORDERED_LIST_KEYS)

    def test_build_does_not_default_missing_fields(self) -> None:
        with self.assertRaises(TypeError):
            build_capture_fact(  # type: ignore[call-arg]
                activity_instance_id="activity:capture-subject-fixture-1",
                occurrence_key="occ-missing",
                captured_at="2026-04-01T10:30:00+09:00",
                visual_context=_visual_context(),
            )

