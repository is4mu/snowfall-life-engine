"""Candidate-tree isolation, path safety, and deterministic-byte integration tests."""

from __future__ import annotations

import tempfile
import unittest

import pytest
from pathlib import Path

from engine.life.errors import LifeEngineError
from engine.life.runtime_persistence import (
    ALLOWED_CANDIDATE_RELPATH_PREFIXES,
    LAST_RUN_RELPATH,
    build_runtime_candidate_tree,
    compute_runtime_semantic_tree_hash,
    list_runtime_files,
)
from tests.support.builders.persistence import (
    LIFE_BASE_SHA,
    POLICY,
    _advance_refs,
    fingerprint,
    make_c3_noop_root,
    make_c3_start_root,
    run_candidate,
)


pytestmark = pytest.mark.integration

class PersistenceIsolationTests(unittest.TestCase):
    def test_86_baseline_bytes_unchanged_on_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            before = fingerprint(root)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "CHANGE")
            self.assertEqual(fingerprint(root), before)

    def test_87_baseline_bytes_unchanged_on_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            before = fingerprint(root)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "NOOP")
            self.assertEqual(fingerprint(root), before)

    def test_88_baseline_bytes_unchanged_on_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            before = fingerprint(root)
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

    def test_89_candidate_root_distinct_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            with self.assertRaises(LifeEngineError) as ctx:
                build_runtime_candidate_tree(
                    baseline_root=root,
                    candidate_root=root,
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    target_inputs=inputs,
                    executing_engine_commit_sha=current["engine_commit_sha"],
                    life_base_sha=LIFE_BASE_SHA,
                )
            self.assertIn("distinct", ctx.exception.detail)

    def test_90_symlink_path_escape_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            link = Path(tmp) / "link-baseline"
            link.symlink_to(root)
            with self.assertRaises(LifeEngineError) as ctx:
                build_runtime_candidate_tree(
                    baseline_root=link,
                    candidate_root=Path(tmp) / "candidate",
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    target_inputs=inputs,
                    executing_engine_commit_sha=current["engine_commit_sha"],
                    life_base_sha=LIFE_BASE_SHA,
                )
            self.assertIn("symlink", ctx.exception.detail.lower())

    def test_91_no_temp_files_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            cand = Path(result.candidate_snapshot.root)
            for path in cand.rglob("*"):
                if path.is_file():
                    self.assertFalse(path.name.endswith("~"))
                    self.assertFalse(path.name.endswith(".tmp"))
                    self.assertFalse(path.name.endswith(".bak"))

    def test_92_only_allowed_runtime_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            cand = Path(result.candidate_snapshot.root)
            for rel in list_runtime_files(cand):
                allowed = rel in ALLOWED_CANDIDATE_RELPATH_PREFIXES or any(
                    rel.startswith(p)
                    for p in ("timeline/", "camera-roll/records/", "state/", "schedule/",
                              "relations/", "home/", "consumables/", "wardrobe/", "finance/")
                )
                self.assertTrue(allowed, rel)

    def test_93_deterministic_json_bytes_for_same_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1, social, current, inputs, *_ = make_c3_start_root(Path(tmp) / "a")
            result1 = run_candidate(root1, target_inputs=inputs)
            files1 = list_runtime_files(result1.candidate_snapshot.root)

            root2, social2, current2, inputs2, *_ = make_c3_start_root(Path(tmp) / "b")
            result2 = run_candidate(root2, target_inputs=inputs2)
            files2 = list_runtime_files(result2.candidate_snapshot.root)
            self.assertEqual(files1.keys(), files2.keys())
            for rel in files1:
                self.assertEqual(files1[rel], files2[rel], rel)

    def test_94_deterministic_candidate_semantic_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1, social, current, inputs, *_ = make_c3_start_root(Path(tmp) / "a")
            result1 = run_candidate(root1, target_inputs=inputs)
            root2, social2, current2, inputs2, *_ = make_c3_start_root(Path(tmp) / "b")
            result2 = run_candidate(root2, target_inputs=inputs2)
            self.assertEqual(result1.state_after_hash, result2.state_after_hash)
            self.assertEqual(
                compute_runtime_semantic_tree_hash(result1.candidate_snapshot.root),
                result1.state_after_hash,
            )

    def test_95_two_identical_runs_exact_candidate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root1, social, current, inputs, *_ = make_c3_start_root(Path(tmp) / "a")
            result1 = run_candidate(root1, target_inputs=inputs)
            root2, social2, current2, inputs2, *_ = make_c3_start_root(Path(tmp) / "b")
            result2 = run_candidate(root2, target_inputs=inputs2)
            self.assertEqual(
                fingerprint(result1.candidate_snapshot.root),
                fingerprint(result2.candidate_snapshot.root),
            )
