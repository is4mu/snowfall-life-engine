"""SUCCESS-only last-run metadata persistence contracts."""

from __future__ import annotations

import copy
import tempfile
import unittest

import pytest
from pathlib import Path

from engine.life.errors import LifeEngineError
from engine.life.ids import stable_id
from engine.life.runtime_persistence import (
    LAST_RUN_RELPATH,
    compute_behavior_policy_hash,
    validate_runtime_last_run,
)
from engine.life.schema import validate_instance
from tests.support.builders.persistence import (
    LIFE_BASE_SHA,
    POLICY,
    build_last_run_for_root,
    fingerprint,
    make_c3_exact_end_root,
    make_c3_noop_root,
    make_c3_start_root,
    read_json,
    run_candidate,
)


pytestmark = pytest.mark.contract

class PersistenceLastRunTests(unittest.TestCase):
    def test_69_strict_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            validate_instance(result.last_run, "runtime_last_run")
            validate_runtime_last_run(result.last_run)

    def test_70_deterministic_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            expected = stable_id(
                "runtime-run",
                current["character_id"],
                LIFE_BASE_SHA,
                current["processed_through"],
                inputs.target_time,
                result.state_before_hash,
                result.state_after_hash,
            )
            self.assertEqual(result.last_run["run_id"], expected)

    def test_71_engine_policy_bind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                result.last_run["engine_commit_sha"], current["engine_commit_sha"]
            )
            self.assertEqual(
                result.last_run["behavior_policy_version"],
                current["behavior_policy_version"],
            )

    def test_72_policy_hash_deterministic(self) -> None:
        h1 = compute_behavior_policy_hash(POLICY)
        h2 = compute_behavior_policy_hash(copy.deepcopy(POLICY))
        self.assertEqual(h1, h2)
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.last_run["behavior_policy_hash"], h1)

    def test_73_before_after_hashes_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.last_run["state_before_hash"], result.state_before_hash)
            self.assertEqual(result.last_run["state_after_hash"], result.state_after_hash)
            self.assertNotEqual(result.state_before_hash, result.state_after_hash)

    def test_74_history_heads_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                result.last_run["history_head_before"],
                current["history"]["head_event_id"],
            )
            self.assertEqual(
                result.last_run["history_head_after"],
                result.candidate_snapshot.bundle.current_state["history"]["head_event_id"],
            )

    def test_75_scheduler_count_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                result.last_run["scheduler_events_processed_count"],
                len(result.runtime_result.consumed_trigger_ids),
            )

    def test_76_finalized_count_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                result.last_run["finalized_actual_events_count"],
                len(result.runtime_result.actual_events),
            )

    def test_77_capture_added_count_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                result.last_run["capture_records_added_count"],
                len(result.runtime_result.camera_roll_records),
            )

    def test_78_microsteps_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                result.last_run["microsteps_used"],
                result.runtime_result.microsteps_used,
            )

    def test_79_files_changed_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            files = list(result.last_run["files_changed"])
            self.assertEqual(files, sorted(files))
            self.assertEqual(tuple(files), result.files_changed)

    def test_80_includes_last_run_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertIn(LAST_RUN_RELPATH, result.last_run["files_changed"])

    def test_81_no_commit_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertNotIn("commit_sha", result.last_run)
            self.assertNotIn("resulting_commit_sha", result.last_run)
            # life_base_sha is the frozen base, not a resulting commit.
            self.assertEqual(result.last_run["life_base_sha"], LIFE_BASE_SHA)

    def test_82_no_world_seed_state_dump_fact_tape_decision_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            for forbidden in (
                "world_seed",
                "state_dump",
                "fact_tape",
                "decision_frames",
                "prose_rationale",
                "rationale",
            ):
                self.assertNotIn(forbidden, result.last_run)

    def test_83_success_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.last_run["result"], "SUCCESS")
            bad = dict(result.last_run)
            bad["result"] = "FAILED"
            with self.assertRaises(LifeEngineError):
                validate_runtime_last_run(bad)

    def test_84_failure_writes_no_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            before = fingerprint(root)
            from engine.life.runtime_persistence import build_runtime_candidate_tree
            from tests.support.builders.persistence import _advance_refs, POLICY

            with self.assertRaises(LifeEngineError):
                build_runtime_candidate_tree(
                    baseline_root=root,
                    candidate_root=Path(tmp) / "candidate",
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    target_inputs=inputs,
                    executing_engine_commit_sha="0" * 40,
                    life_base_sha=LIFE_BASE_SHA,
                )
            self.assertEqual(fingerprint(root), before)
            self.assertFalse((root / LAST_RUN_RELPATH).exists())

    def test_85_noop_writes_no_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "NOOP")
            self.assertIsNone(result.last_run)
            self.assertFalse((root / LAST_RUN_RELPATH).exists())
