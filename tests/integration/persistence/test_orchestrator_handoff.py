"""Runtime orchestrator handoff contracts for persistence."""

from __future__ import annotations

import copy
import inspect
import tempfile
import unittest

import pytest
from pathlib import Path
from unittest import mock

from engine.life.camera_roll import project_camera_roll_records
from engine.life.errors import LifeEngineError
from engine.life.runtime_persistence import build_runtime_candidate_tree
from tests.support.builders.persistence import (
    LIFE_BASE_SHA,
    POLICY,
    _advance_refs,
    clone_advance_result,
    fingerprint,
    make_c3_exact_end_root,
    make_c3_noop_root,
    make_c3_start_root,
    real_advance,
    run_candidate,
)


pytestmark = pytest.mark.integration

class PersistenceC3HandoffTests(unittest.TestCase):
    def test_31_exact_baseline_social_supplied_to_c3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            seen: dict = {}

            def _wrap(**kwargs):
                seen["social"] = kwargs.get("social_response_state")
                return real_advance(**kwargs)

            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_wrap,
            ):
                result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(seen["social"].as_dict(), social.as_dict())
            self.assertEqual(result.status, "NOOP")

    def test_32_executing_engine_sha_mismatch_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            before = fingerprint(root)
            with self.assertRaises(LifeEngineError) as ctx:
                build_runtime_candidate_tree(
                    baseline_root=root,
                    candidate_root=Path(tmp) / "candidate",
                    reference_sets=_advance_refs(),
                    behavior_policy=POLICY,
                    target_inputs=inputs,
                    executing_engine_commit_sha="f" * 40,
                    life_base_sha=LIFE_BASE_SHA,
                )
            self.assertIn("executing_engine_commit_sha", ctx.exception.detail)
            self.assertEqual(fingerprint(root), before)

    def test_33_policy_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            policy = copy.deepcopy(POLICY)
            before_policy = copy.deepcopy(policy)
            run_candidate(root, target_inputs=inputs, behavior_policy=policy)
            self.assertEqual(policy, before_policy)

    def test_34_no_second_resolver_reducer(self) -> None:
        import engine.life.runtime_persistence as mod

        src = inspect.getsource(mod)
        self.assertIn("advance_runtime_to_target", src)
        self.assertNotIn("build_runtime_social_decision", src)
        self.assertNotIn("generate_social_opportunities", src)
        self.assertNotIn("adapt_social_responses", src)
        self.assertEqual(src.count("advance_runtime_to_target("), 1)

    def test_35_ordered_actual_events_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "CHANGE")
            self.assertGreaterEqual(len(result.runtime_result.actual_events), 1)
            day = result.candidate_snapshot.timeline_days["2026-04-01"]
            ids = [e["event_id"] for e in day["actual_events"]]
            self.assertEqual(
                ids,
                [e["event_id"] for e in result.runtime_result.actual_events],
            )

    def test_36_c3_camera_grouping_independently_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            projected = []
            for event in result.runtime_result.actual_events:
                projected.extend(project_camera_roll_records(event))
            self.assertEqual(
                len(projected), len(result.runtime_result.camera_roll_records)
            )

    def test_37_malformed_c3_handoff_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            before = fingerprint(root)

            def _bad_handoff(**kwargs):
                real = real_advance(**kwargs)
                return clone_advance_result(
                    real,
                    camera_roll_records=[{"schema_version": 1, "record_id": "bogus"}],
                    camera_roll_records_by_shard_path={
                        "camera-roll/records/2099-01.json": []
                    },
                )

            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_bad_handoff,
            ):
                with self.assertRaises(LifeEngineError):
                    run_candidate(root, target_inputs=inputs)
            self.assertEqual(fingerprint(root), before)
