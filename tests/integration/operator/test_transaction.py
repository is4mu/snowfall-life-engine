"""Local operator Git transaction integration tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest

from engine.life.correction import raw_event_canonical_hash
from engine.life.errors import LifeEngineError
from engine.life.operator_git_transaction import (
    OperatorGitTransactionResult,
    build_operator_correction_git_transaction,
    build_operator_recovery_git_transaction,
)
from engine.life.runtime_git_transaction import RuntimeGitTransactionResult
from engine.life.timeutil import format_rfc3339, parse_rfc3339
from tests.support.builders.persistence import (
    AS_OF,
    POLICY,
    _advance_refs,
    _target_inputs,
    build_5c1_persistent_root,
    build_actual_event,
    day_with_events,
    make_c3_noop_root,
)
from tests.support.git.local_repo import (
    build_runtime_git_repo,
    git,
    git_out,
    snapshot_index,
    snapshot_refs_head,
    snapshot_worktree,
)

pytestmark = pytest.mark.integration


def _correction_request(event: dict, *, life_head: str) -> dict:
    corrected = deepcopy(event)
    corrected["summary"] = "corrected public fixture summary"
    return {
        "schema_version": 1,
        "request_type": "CORRECTION",
        "expected_life_head": life_head,
        "effective_at": AS_OF,
        "approval_ref": "review:public-correction",
        "correction": {
            "target_event_id": event["event_id"],
            "target_event_hash": raw_event_canonical_hash(event),
            "supersedes_action_id": None,
            "corrected_event": corrected,
        },
    }


def _authority_snapshot(repo: Path):
    return (
        snapshot_refs_head(repo),
        snapshot_index(repo),
        snapshot_worktree(repo),
    )


def test_correction_transaction_is_deterministic_and_does_not_move_refs(tmp_path: Path) -> None:
    event = build_actual_event()
    root = build_5c1_persistent_root(
        tmp_path / "runtime",
        timeline_days=[day_with_events(event)],
    )
    repo = build_runtime_git_repo(tmp_path / "repo", root)
    head = git_out(repo, "rev-parse", "HEAD")
    request = _correction_request(event, life_head=head)
    before = _authority_snapshot(repo)

    first = build_operator_correction_git_transaction(
        runtime_repo=repo,
        request=request,
        reference_sets=_advance_refs(),
    )
    second = build_operator_correction_git_transaction(
        runtime_repo=repo,
        request=request,
        reference_sets=_advance_refs(),
    )

    assert isinstance(first, OperatorGitTransactionResult)
    assert first.status == "CHANGE"
    assert first.base_commit_sha == head
    assert first.candidate_commit_sha == second.candidate_commit_sha
    assert first.candidate_tree_sha == second.candidate_tree_sha
    assert first.last_operator_action is not None
    assert first.last_operator_action["result"] == "SUCCESS"
    assert "state/last-operator-action.json" in first.files_changed
    assert _authority_snapshot(repo) == before

    parents = git_out(repo, "rev-list", "--parents", "-n", "1", first.candidate_commit_sha).split()
    assert parents == [first.candidate_commit_sha, head]
    assert git_out(repo, "show", "-s", "--format=%B", first.candidate_commit_sha).startswith(
        "Life Engine operator correction "
    )
    contains = git(repo, "for-each-ref", "--contains", first.candidate_commit_sha, "--format=%(refname)")
    assert contains.stdout.strip() == b""


def test_correction_transaction_rejects_stale_life_binding(tmp_path: Path) -> None:
    event = build_actual_event()
    root = build_5c1_persistent_root(
        tmp_path / "runtime",
        timeline_days=[day_with_events(event)],
    )
    repo = build_runtime_git_repo(tmp_path / "repo", root)
    request = _correction_request(event, life_head="0" * 40)

    with pytest.raises(LifeEngineError):
        build_operator_correction_git_transaction(
            runtime_repo=repo,
            request=request,
            reference_sets=_advance_refs(),
        )


def test_recovery_reuses_runtime_transaction_shape_without_ref_mutation(tmp_path: Path) -> None:
    root, _social, current, _inputs = make_c3_noop_root(tmp_path / "runtime")
    repo = build_runtime_git_repo(tmp_path / "repo", root)
    head = git_out(repo, "rev-parse", "HEAD")
    through = format_rfc3339(parse_rfc3339(AS_OF) + timedelta(minutes=10))
    request = {
        "schema_version": 1,
        "request_type": "RECOVERY_AUTHORIZATION",
        "expected_life_head": head,
        "approval_ref": "review:public-recovery",
        "recovery": {
            "from_processed_through": AS_OF,
            "recover_through": through,
        },
    }
    before = _authority_snapshot(repo)

    result = build_operator_recovery_git_transaction(
        runtime_repo=repo,
        request=request,
        reference_sets=_advance_refs(),
        behavior_policy=POLICY,
        executing_engine_commit_sha=current["engine_commit_sha"],
        target_inputs=_target_inputs(target_time=through, event_budget=0),
    )

    assert isinstance(result, RuntimeGitTransactionResult)
    assert result.status == "CHANGE"
    assert result.base_commit_sha == head
    assert result.last_run is not None
    assert "state/last-operator-action.json" in result.files_changed
    assert _authority_snapshot(repo) == before
