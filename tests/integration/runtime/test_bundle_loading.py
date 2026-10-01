"""RuntimeBundle loading, closure, timeline, and non-mutation integration tests."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.history import EMPTY_HISTORY_HASH
from engine.life.runtime_bundle import (
    FORBIDDEN_LEGACY_RUNTIME_RELPATHS,
    REQUIRED_BUNDLE_RELPATHS,
    RuntimeReferenceSets,
    load_runtime_bundle,
    verify_runtime_bundle,
)
from tests.support.builders.runtime_bundle import (
    CHAR,
    build_synthetic_bundle_root,
    provenance,
    reference_sets,
    snapshot_tree,
    write_json,
)

pytestmark = pytest.mark.integration


class RuntimeBundleLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_19_valid_synthetic_complete_bundle(self) -> None:
        root = build_synthetic_bundle_root(self.tmp)
        result = verify_runtime_bundle(root, reference_sets=reference_sets())
        self.assertEqual(result.bundle.current_state["character_id"], CHAR)
        self.assertEqual(
            result.bundle.current_state["domain_refs"]["consumables_revision"],
            result.bundle.consumables_state["revision"],
        )

    def test_20_each_required_file_missing_fails(self) -> None:
        for rel in REQUIRED_BUNDLE_RELPATHS:
            with self.subTest(missing=rel):
                root = build_synthetic_bundle_root(self.tmp / rel.replace("/", "_"))
                (root / rel).unlink()
                with self.assertRaises(LifeEngineError) as ctx:
                    load_runtime_bundle(root, reference_sets=reference_sets())
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
                self.assertIn("missing required files", ctx.exception.detail)

    def test_21_character_mismatch_reject(self) -> None:
        def mutate(current: dict) -> dict:
            current["character_id"] = "other-character"
            return current

        root = build_synthetic_bundle_root(self.tmp, mutate_current=mutate)
        with self.assertRaises(LifeEngineError) as ctx:
            load_runtime_bundle(root, reference_sets=reference_sets())
        self.assertIn("character_id mismatch", ctx.exception.detail)

    def _mismatch_ref(self, ref_key: str) -> None:
        def mutate(current: dict) -> dict:
            current["domain_refs"][ref_key] = "0" * 64
            return current

        root = build_synthetic_bundle_root(self.tmp, mutate_current=mutate)
        with self.assertRaises(LifeEngineError) as ctx:
            load_runtime_bundle(root, reference_sets=reference_sets())
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_22_relation_revision_mismatch(self) -> None:
        self._mismatch_ref("relation_state_revision")

    def test_23_home_revision_mismatch(self) -> None:
        self._mismatch_ref("home_revision")

    def test_24_consumables_revision_mismatch(self) -> None:
        self._mismatch_ref("consumables_revision")

    def test_25_wardrobe_revision_mismatch(self) -> None:
        self._mismatch_ref("wardrobe_revision")

    def test_26_finance_revision_mismatch(self) -> None:
        self._mismatch_ref("finance_revision")

    def test_27_schedule_revision_mismatch(self) -> None:
        self._mismatch_ref("schedule_revision")

    def test_28_schedule_hash_mismatch(self) -> None:
        self._mismatch_ref("schedule_hash")

    def test_29_null_required_runtime_revision_reject(self) -> None:
        def mutate(current: dict) -> dict:
            current["domain_refs"]["consumables_revision"] = None
            current["domain_refs"]["schedule_revision"] = None
            current["domain_refs"]["schedule_hash"] = None
            return current

        root = build_synthetic_bundle_root(self.tmp, mutate_current=mutate)
        with self.assertRaises(LifeEngineError) as ctx:
            load_runtime_bundle(root, reference_sets=reference_sets())
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_30_domain_as_of_after_processed_through_reject(self) -> None:
        root = build_synthetic_bundle_root(
            self.tmp,
            as_of="2026-04-01T18:00:00+09:00",
            processed_through="2026-04-01T12:00:00+09:00",
        )
        with self.assertRaises(LifeEngineError) as ctx:
            load_runtime_bundle(root, reference_sets=reference_sets())
        self.assertIn("after CurrentState.processed_through", ctx.exception.detail)

    def test_31_unknown_ids_reject(self) -> None:
        root = build_synthetic_bundle_root(self.tmp)
        # Empty approved sets → unknown IDs in loaded states.
        with self.assertRaises(LifeEngineError) as ctx:
            load_runtime_bundle(
                root,
                reference_sets=RuntimeReferenceSets(
                    known_person_ids=frozenset(),
                    known_home_entity_ids=frozenset(),
                    approved_wardrobe_item_ids=frozenset(),
                    approved_consumable_ids=frozenset(),
                ),
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_32_history_mismatch_reject(self) -> None:
        def mutate(current: dict) -> dict:
            current["history"] = {
                "head_event_id": "evt-missing",
                "history_hash": "b" * 64,
                "event_count": 1,
            }
            return current

        root = build_synthetic_bundle_root(self.tmp, mutate_current=mutate)
        with self.assertRaises(LifeEngineError):
            load_runtime_bundle(root, reference_sets=reference_sets())

    def test_33_valid_empty_history_no_timeline(self) -> None:
        root = build_synthetic_bundle_root(self.tmp, include_timeline=False)
        self.assertFalse((root / "timeline").exists())
        bundle = load_runtime_bundle(root, reference_sets=reference_sets())
        self.assertEqual(bundle.current_state["history"]["event_count"], 0)
        self.assertEqual(
            bundle.current_state["history"]["history_hash"], EMPTY_HISTORY_HASH
        )

    def test_34_active_already_finalized_reject(self) -> None:
        from engine.life.history import next_history_hash
        from engine.life.timeline import empty_day

        activity_id = "act-already-done"
        event = {
            "schema_version": 1,
            "event_id": "evt-1",
            "event_type": "LEISURE",
            "activity_instance_id": activity_id,
            "actual_start": "2026-04-01T10:00:00+09:00",
            "actual_end": "2026-04-01T11:00:00+09:00",
            "finalized_at": "2026-04-01T11:00:00+09:00",
            "location_id": "fixture-home",
            "participants": [],
            "summary": "done",
            "causes": [],
            "effects": [],
            "captures": [],
            "provenance": provenance(origin="ACTUAL_EVENT"),
            "decision_evidence": None,
        }

        def mutate(current: dict) -> dict:
            current["context"]["active_activity"] = {
                "schema_version": 1,
                "activity_instance_id": activity_id,
                "activity_type": "LEISURE",
                "actual_start": "2026-04-01T10:00:00+09:00",
                "expected_end": None,
                "companions": [],
                "commitment_id": None,
            }
            h = next_history_hash(EMPTY_HISTORY_HASH, event)
            current["history"] = {
                "head_event_id": "evt-1",
                "history_hash": h,
                "event_count": 1,
            }
            return current

        root = build_synthetic_bundle_root(self.tmp, mutate_current=mutate)
        day = empty_day("2026-04-01")
        day["actual_events"] = [event]
        write_json(root / "timeline/2026-04-01.json", day)
        with self.assertRaises(LifeEngineError) as ctx:
            load_runtime_bundle(root, reference_sets=reference_sets())
        self.assertIn("already finalized", ctx.exception.detail)

    def test_35_bad_engine_commit_sha_reject(self) -> None:
        root = build_synthetic_bundle_root(
            self.tmp, engine_commit_sha="not-a-valid-git-sha"
        )
        with self.assertRaises(LifeEngineError) as ctx:
            load_runtime_bundle(root, reference_sets=reference_sets())
        self.assertIn("engine_commit_sha", ctx.exception.detail)

    def test_36_loader_verifier_does_not_mutate_disk(self) -> None:
        root = build_synthetic_bundle_root(self.tmp)
        before = snapshot_tree(root)
        load_runtime_bundle(root, reference_sets=reference_sets())
        verify_runtime_bundle(root, reference_sets=reference_sets())
        after = snapshot_tree(root)
        self.assertEqual(before, after)

    def test_37_loader_never_reads_legacy_data_runtime_files(self) -> None:
        root = build_synthetic_bundle_root(self.tmp)
        legacy_suffixes = tuple(
            "/" + rel.replace("\\", "/").lstrip("/")
            for rel in FORBIDDEN_LEGACY_RUNTIME_RELPATHS
        )
        opened: list[Path] = []
        real_open = open

        def tracking_open(file, *args, **kwargs):
            path = Path(file).resolve() if not hasattr(file, "read") else None
            if path is not None:
                opened.append(path)
            return real_open(file, *args, **kwargs)

        with mock.patch("builtins.open", tracking_open):
            load_runtime_bundle(root, reference_sets=reference_sets())
        for path in opened:
            posix = path.as_posix()
            self.assertFalse(
                any(posix.endswith(suffix) for suffix in legacy_suffixes),
                posix,
            )

        # Source-level guarantee: module does not reference legacy paths as inputs.
        import engine.life.runtime_bundle as rb

        src = Path(inspect.getfile(rb)).read_text(encoding="utf-8")
        self.assertIn("FORBIDDEN_LEGACY_RUNTIME_RELPATHS", src)
        self.assertNotRegex(src, r"read_text\(.*character\.json")
        self.assertNotRegex(src, r"data/runtime/character\.json\"\)")
