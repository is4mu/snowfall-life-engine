"""Persistence NOOP versus CHANGE and state-revision ownership integration tests."""

from __future__ import annotations

import copy
import tempfile
import unittest

import pytest
from pathlib import Path
from unittest import mock

from engine.life.checkpoint import validate_checkpoint
from engine.life.runtime_bundle import RuntimeBundle
from engine.life.runtime_persistence import LAST_RUN_RELPATH
from tests.support.builders.persistence import (
    AS_OF,
    _add_min,
    _target_inputs,
    clone_advance_result,
    fingerprint,
    make_c3_continue_root,
    make_c3_exact_end_root,
    make_c3_noop_root,
    make_c3_start_root,
    read_json,
    real_advance,
    run_candidate,
    social_with_responses,
)


pytestmark = pytest.mark.integration

class PersistenceNoopRevisionTests(unittest.TestCase):
    def test_58_stable_target_eq_processed_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "NOOP")
            self.assertIsNone(result.candidate_snapshot)
            self.assertIsNone(result.last_run)
            self.assertEqual(result.files_changed, ())

    def test_59_noop_leaves_state_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp, state_revision=0)
            before = current["state_revision"]
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "NOOP")
            after = read_json(root / "state/current.json")["state_revision"]
            self.assertEqual(after, before)

    def test_60_noop_leaves_last_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            self.assertFalse((root / LAST_RUN_RELPATH).exists())
            run_candidate(root, target_inputs=inputs)
            self.assertFalse((root / LAST_RUN_RELPATH).exists())

    def test_61_noop_baseline_bytes_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            before = fingerprint(root)
            run_candidate(root, target_inputs=inputs)
            self.assertEqual(fingerprint(root), before)

    def test_62_target_same_but_due_now_work_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "CHANGE")

    def test_63_time_integration_only_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            target = _add_min(AS_OF, 10)
            result = run_candidate(
                root, target_inputs=_target_inputs(target_time=target, event_budget=0)
            )
            self.assertEqual(result.status, "CHANGE")
            self.assertEqual(
                result.runtime_result.bundle.current_state["processed_through"], target
            )

    def test_64_social_only_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            new_social = social_with_responses("DECLINE")

            def _handoff(**kwargs):
                real = real_advance(**kwargs)
                cur = dict(real.bundle.current_state)
                cur["domain_refs"] = dict(cur["domain_refs"])
                cur["domain_refs"]["social_response_revision"] = new_social.revision
                cur = validate_checkpoint(cur)
                bundle = RuntimeBundle(
                    current_state=cur,
                    schedule_state=real.bundle.schedule_state,
                    relation_state=real.bundle.relation_state,
                    home_state=real.bundle.home_state,
                    consumables_state=real.bundle.consumables_state,
                    wardrobe_state=real.bundle.wardrobe_state,
                    finance_state=real.bundle.finance_state,
                )
                return clone_advance_result(
                    real,
                    bundle=bundle,
                    social_response_state=new_social,
                    microsteps_used=1,
                )

            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_handoff,
            ):
                result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "CHANGE")
            self.assertEqual(
                result.candidate_snapshot.social_response_state.revision,
                new_social.revision,
            )
            self.assertIn("state/social-responses.json", result.files_changed)

    def test_65_one_actual_event_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "CHANGE")
            self.assertEqual(len(result.runtime_result.actual_events), 1)

    def test_66_n_events_still_one_state_revision_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            before = current["state_revision"]
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                result.candidate_snapshot.bundle.current_state["state_revision"],
                before + 1,
            )

    def test_67_only_one_plus_one_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_start_root(tmp)
            before = int(current["state_revision"])
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(
                int(result.candidate_snapshot.bundle.current_state["state_revision"]),
                before + 1,
            )
            self.assertEqual(
                result.last_run["state_revision_after"],
                result.last_run["state_revision_before"] + 1,
            )

    def test_68_no_domain_fake_revision_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_continue_root(tmp)
            before_home = current["domain_refs"]["home_revision"]
            before_fin = current["domain_refs"]["finance_revision"]
            result = run_candidate(root, target_inputs=inputs)
            after = result.candidate_snapshot.bundle.current_state["domain_refs"]
            # Domain revisions only change when C3 domain state changes; 5C1 must not
            # invent fake bumps. Continue typically leaves home/finance revisions equal.
            self.assertEqual(after["home_revision"], before_home)
            self.assertEqual(after["finance_revision"], before_fin)
            # state_revision is the only mandatory +1 on CHANGE.
            self.assertEqual(
                result.candidate_snapshot.bundle.current_state["state_revision"],
                current["state_revision"] + 1,
            )
