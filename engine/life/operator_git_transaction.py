"""Slice 5E3 — local operator Git transactions (Correction / Recovery / Upgrade).

Correction/Upgrade return a typed operator transaction (not fabricated
simulation metadata). Recovery reuses the ordinary 5C2
``RuntimeGitTransactionResult`` path with an atomic recovery+operator candidate.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCode, LifeEngineError
from .last_operator_action import (
    LAST_OPERATOR_ACTION_RELPATH,
    build_last_operator_action,
    validate_last_operator_action,
)
from .operator_candidate import build_operator_correction_candidate
from .operator_history import empty_operator_history
from .operator_recovery import build_operator_recovery_candidate_tree
from .operator_request import UPGRADE_REQUEST_TYPES
from .operator_upgrade import UpgradeEnvironmentAdapter, build_operator_upgrade_candidate
from .runtime_bundle import RuntimeReferenceSets
from .runtime_git_transaction import (
    RuntimeGitTransactionResult,
    RuntimeProviderTransactionPlan,
    _assert_authority_unchanged,
    _assert_base_objects_locally_complete,
    _assert_linear_ancestry,
    _assert_object_in_intended_repo,
    _byte_diff_paths,
    _build_runtime_git_transaction_core,
    _cleanup_owned_temp_root,
    _commit_is_unreferenced,
    _commit_tree,
    _freeze_authority,
    _git_date_from_target_time,
    _ls_tree_blobs,
    _materialize_base_tree,
    _precompute_blob_ids,
    _require_hex40,
    _verify_commit_object,
    _write_candidate_tree,
)
from .runtime_orchestrator import RuntimeFactProvider, RuntimeTargetRequest
from .runtime_persistence import (
    RuntimePersistentSnapshot,
    compute_runtime_semantic_tree_hash,
    is_allowed_runtime_relpath,
    list_runtime_files,
    load_runtime_persistent_snapshot,
    snapshot_tree_bytes,
    write_runtime_json,
)
from .timeutil import require_canonical_timestamp


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


@dataclass(frozen=True)
class OperatorGitTransactionResult:
    """Typed local Correction transaction (not a simulation RuntimeGitTransactionResult)."""

    status: str  # NOOP | CHANGE
    base_commit_sha: str
    candidate_tree_sha: str | None
    candidate_commit_sha: str | None
    action_id: str | None
    request_id: str | None
    action_type: str
    state_before_hash: str
    state_after_hash: str
    files_changed: tuple[str, ...]
    last_operator_action: Mapping[str, Any] | None


def _operator_commit_kind(action_type: str) -> str:
    if action_type == "CORRECTION":
        return "correction"
    if action_type == "ENGINE_UPGRADE":
        return "engine-upgrade"
    if action_type == "POLICY_UPGRADE":
        return "policy-upgrade"
    _fail(f"unsupported operator commit action_type: {action_type!r}")


def operator_commit_message(last_operator_action: Mapping[str, Any]) -> str:
    """Deterministic operator-only commit message (Correction + Upgrades)."""
    action_id = last_operator_action["action_id"]
    kind = _operator_commit_kind(str(last_operator_action["action_type"]))
    msg = (
        f"Life Engine operator {kind} {action_id}\n"
        f"\n"
        f"action_type: {last_operator_action['action_type']}\n"
        f"state_revision: "
        f"{last_operator_action['state_revision_before']} -> "
        f"{last_operator_action['state_revision_after']}\n"
    )
    if msg[-1] != "\n" or msg[-2] == "\n":
        _fail("operator commit message must end with exactly one trailing newline")
    return msg


def _operator_correction_commit_message(last_operator_action: Mapping[str, Any]) -> str:
    return operator_commit_message(last_operator_action)


def build_operator_correction_git_transaction(
    *,
    runtime_repo: Path | str,
    request: Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
) -> OperatorGitTransactionResult:
    """Local-only Correction Git transaction: freeze base, stage candidate, no refs."""
    repo_path = Path(runtime_repo)
    temp_root: Path | None = None
    result: OperatorGitTransactionResult | None = None
    try:
        authority = _freeze_authority(repo_path)
        _ls_tree_blobs(authority.git_dir, authority.head_sha)
        _assert_linear_ancestry(authority.git_dir, authority.head_sha)
        _assert_base_objects_locally_complete(authority.git_dir, authority.head_sha)

        temp_root = Path(tempfile.mkdtemp(prefix="life-engine-5e2-corr-", dir=None)).resolve()
        try:
            temp_root.relative_to(authority.git_dir)
            _fail("temp workspace resolved inside git dir")
        except ValueError:
            pass
        if authority.work_tree is not None:
            try:
                temp_root.relative_to(authority.work_tree)
                _fail("temp workspace resolved inside worktree")
            except ValueError:
                pass

        baseline_root = temp_root / "baseline"
        candidate_root = temp_root / "candidate"
        index_file = temp_root / "isolated-index"
        baseline_root.mkdir(parents=True, exist_ok=True)
        candidate_root.mkdir(parents=True, exist_ok=True)

        base_files = _materialize_base_tree(
            authority.git_dir, authority.head_sha, baseline_root
        )
        _assert_authority_unchanged(authority, stage="pre-correction-candidate")

        candidate = build_operator_correction_candidate(
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            request=request,
            life_base_sha=authority.head_sha,
            reference_sets=reference_sets,
        )
        if candidate.life_base_sha != authority.head_sha:
            _fail("correction life_base_sha != frozen HEAD")

        baseline_disk = list_runtime_files(baseline_root)
        if set(baseline_disk) != set(base_files):
            _fail("correction baseline file set != frozen Git base tree")
        for rel, data in base_files.items():
            if baseline_disk.get(rel) != data:
                _fail(f"baseline byte mismatch vs frozen Git blob: {rel}")

        state_before = compute_runtime_semantic_tree_hash(baseline_root)
        action = candidate.action
        action_type = "CORRECTION"
        request_id = None if action is None else str(action["request_id"])
        action_id = None if action is None else str(action["action_id"])

        if candidate.status == "NOOP":
            if candidate.files_changed != ():
                _fail("NOOP correction must have empty files_changed")
            if (candidate_root / LAST_OPERATOR_ACTION_RELPATH).exists():
                _fail("NOOP correction must not write last-operator-action")
            _assert_authority_unchanged(authority, stage="NOOP correction exit")
            result = OperatorGitTransactionResult(
                status="NOOP",
                base_commit_sha=authority.head_sha,
                candidate_tree_sha=None,
                candidate_commit_sha=None,
                action_id=action_id,
                request_id=request_id,
                action_type=action_type,
                state_before_hash=state_before,
                state_after_hash=state_before,
                files_changed=(),
                last_operator_action=None,
            )
        elif candidate.status != "CHANGE":
            _fail(f"unexpected correction status: {candidate.status}")
        else:
            if action is None:
                _fail("CHANGE correction requires action")
            # Seal last-operator-action after correction semantic staging.
            baseline_snap = load_runtime_persistent_snapshot(
                baseline_root, reference_sets=reference_sets
            )
            state_after_pre = compute_runtime_semantic_tree_hash(candidate_root)
            hist_before = (
                baseline_snap.operator_history
                if baseline_snap.operator_history is not None
                else empty_operator_history()
            )
            provisional = list(candidate.files_changed)
            if LAST_OPERATOR_ACTION_RELPATH not in provisional:
                provisional = sorted([*provisional, LAST_OPERATOR_ACTION_RELPATH])
            last_op = build_last_operator_action(
                action=action,
                life_base_sha=authority.head_sha,
                engine_commit_sha=baseline_snap.bundle.current_state[
                    "engine_commit_sha"
                ],
                behavior_policy_version=baseline_snap.bundle.current_state[
                    "behavior_policy_version"
                ],
                state_revision_before=candidate.state_revision_before,
                state_revision_after=candidate.state_revision_after,
                state_before_hash=state_before,
                state_after_hash=state_after_pre,
                operator_history_hash_before=hist_before["operator_history_hash"],
                operator_history_hash_after=candidate.operator_history[
                    "operator_history_hash"
                ],
                processed_through_before=baseline_snap.bundle.current_state[
                    "processed_through"
                ],
                processed_through_after=baseline_snap.bundle.current_state[
                    "processed_through"
                ],
                files_changed=provisional,
            )
            write_runtime_json(candidate_root / LAST_OPERATOR_ACTION_RELPATH, last_op)

            # Reload/verify candidate including last-operator-action binding.
            candidate_snap = load_runtime_persistent_snapshot(
                candidate_root, reference_sets=reference_sets
            )
            if candidate_snap.semantic_tree_hash != state_after_pre:
                _fail("last-operator-action must not alter semantic tree hash")
            if candidate_snap.last_operator_action is None:
                _fail("CHANGE correction missing last-operator-action after reload")
            validate_last_operator_action(candidate_snap.last_operator_action)

            candidate_files = list_runtime_files(candidate_root)
            for rel in candidate_files:
                if not is_allowed_runtime_relpath(rel):
                    _fail(f"forbidden path in operator candidate tree: {rel}")
            files_changed = tuple(_byte_diff_paths(base_files, candidate_files))
            if sorted(files_changed) != sorted(provisional):
                # Stabilize last-operator-action files_changed.
                last_op = build_last_operator_action(
                    action=action,
                    life_base_sha=authority.head_sha,
                    engine_commit_sha=baseline_snap.bundle.current_state[
                        "engine_commit_sha"
                    ],
                    behavior_policy_version=baseline_snap.bundle.current_state[
                        "behavior_policy_version"
                    ],
                    state_revision_before=candidate.state_revision_before,
                    state_revision_after=candidate.state_revision_after,
                    state_before_hash=state_before,
                    state_after_hash=state_after_pre,
                    operator_history_hash_before=hist_before["operator_history_hash"],
                    operator_history_hash_after=candidate.operator_history[
                        "operator_history_hash"
                    ],
                    processed_through_before=baseline_snap.bundle.current_state[
                        "processed_through"
                    ],
                    processed_through_after=baseline_snap.bundle.current_state[
                        "processed_through"
                    ],
                    files_changed=files_changed,
                )
                write_runtime_json(
                    candidate_root / LAST_OPERATOR_ACTION_RELPATH, last_op
                )
                candidate_files = list_runtime_files(candidate_root)
                files_changed = tuple(_byte_diff_paths(base_files, candidate_files))
                if sorted(files_changed) != sorted(last_op["files_changed"]):
                    _fail("operator files_changed did not stabilize")

            # Freeze bytes before commit to reject post-staging tamper.
            frozen_candidate_bytes = dict(candidate_files)
            expected_blobs = _precompute_blob_ids(authority.git_dir, frozen_candidate_bytes)
            _assert_authority_unchanged(authority, stage="pre-operator-object-write")

            # Re-check freeze against tamper.
            if list_runtime_files(candidate_root) != frozen_candidate_bytes:
                _fail("operator candidate tampered after staging")

            tree_sha = _write_candidate_tree(
                authority.git_dir,
                index_file=index_file,
                candidate_files=frozen_candidate_bytes,
                expected_blobs=expected_blobs,
            )
            # Use processed_through as deterministic commit date (correction does
            # not advance time).
            git_date = _git_date_from_target_time(
                baseline_snap.bundle.current_state["processed_through"]
            )
            message = _operator_correction_commit_message(last_op)
            commit_sha = _commit_tree(
                authority.git_dir,
                tree_sha=tree_sha,
                parent_sha=authority.head_sha,
                message=message,
                git_date=git_date,
            )
            try:
                _verify_commit_object(
                    authority.git_dir,
                    commit_sha=commit_sha,
                    tree_sha=tree_sha,
                    parent_sha=authority.head_sha,
                    message=message,
                    git_date=git_date,
                    files_changed=list(files_changed),
                )
                _assert_object_in_intended_repo(authority.git_dir, commit_sha)
                _assert_authority_unchanged(authority, stage="post-operator-build")
                _commit_is_unreferenced(authority.git_dir, commit_sha)
            except Exception:
                raise

            result = OperatorGitTransactionResult(
                status="CHANGE",
                base_commit_sha=authority.head_sha,
                candidate_tree_sha=tree_sha,
                candidate_commit_sha=commit_sha,
                action_id=str(action["action_id"]),
                request_id=str(action["request_id"]),
                action_type=action_type,
                state_before_hash=state_before,
                state_after_hash=state_after_pre,
                files_changed=files_changed,
                last_operator_action=dict(last_op),
            )
    except BaseException as primary:
        try:
            _cleanup_owned_temp_root(temp_root)
        except Exception as cleanup_exc:
            raise cleanup_exc from primary
        raise

    _cleanup_owned_temp_root(temp_root)
    if result is None:
        _fail("internal error: operator transaction produced no result")
    return result


def build_operator_recovery_git_transaction(
    *,
    runtime_repo: Path | str,
    request: Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    executing_engine_commit_sha: str,
    fact_provider: RuntimeFactProvider | None = None,
    fact_provider_factory: (
        Callable[[RuntimePersistentSnapshot], RuntimeFactProvider] | None
    ) = None,
    target_request: RuntimeTargetRequest | Mapping[str, Any] | None = None,
    target_inputs: Mapping[str, Any] | None = None,
) -> RuntimeGitTransactionResult:
    """Local recovery Git transaction returning ordinary 5C2 result shape."""

    def _build_persistence(
        baseline_root: Path,
        candidate_root: Path,
        life_base_sha: str,
    ):
        recovery = build_operator_recovery_candidate_tree(
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            reference_sets=reference_sets,
            behavior_policy=behavior_policy,
            request=request,
            fact_provider=fact_provider,
            fact_provider_factory=fact_provider_factory,
            target_request=target_request,
            target_inputs=target_inputs,
            executing_engine_commit_sha=executing_engine_commit_sha,
            life_base_sha=life_base_sha,
        )
        return recovery.persistence

    def _requested_target_time() -> str:
        from .operator_request import normalize_operator_request

        normalized = normalize_operator_request(request)
        return require_canonical_timestamp(
            normalized["recovery"]["recover_through"], field="recover_through"
        )

    return _build_runtime_git_transaction_core(
        runtime_repo=runtime_repo,
        persistence_builder=_build_persistence,
        requested_target_time=_requested_target_time,
    )


def build_operator_recovery_git_transaction_with_plan_factory(
    *,
    runtime_repo: Path | str,
    request: Mapping[str, Any],
    plan_factory: Callable[[Path, str], RuntimeProviderTransactionPlan],
) -> RuntimeGitTransactionResult:
    """Recovery transaction whose provider plan is created from the frozen base."""

    if not callable(plan_factory):
        _fail("plan_factory must be callable")
    plan_calls = 0
    target_holder: list[str] = []

    def _build_persistence(
        baseline_root: Path,
        candidate_root: Path,
        life_base_sha: str,
    ):
        nonlocal plan_calls
        if plan_calls != 0:
            _fail("recovery plan_factory must run exactly once")
        plan = plan_factory(baseline_root, life_base_sha)
        plan_calls += 1
        if not isinstance(plan, RuntimeProviderTransactionPlan):
            _fail("plan_factory must return RuntimeProviderTransactionPlan")
        from .runtime_fact_provider import parse_runtime_target_request

        parsed = parse_runtime_target_request(plan.target_request)
        target_holder.append(parsed.target_time)
        recovery = build_operator_recovery_candidate_tree(
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            reference_sets=plan.reference_sets,
            behavior_policy=plan.behavior_policy,
            request=request,
            fact_provider_factory=plan.fact_provider_factory,
            target_request=parsed,
            executing_engine_commit_sha=plan.executing_engine_commit_sha,
            life_base_sha=life_base_sha,
        )
        return recovery.persistence

    def _requested_target_time() -> str:
        if len(target_holder) == 1:
            return target_holder[0]
        from .operator_request import normalize_operator_request

        normalized = normalize_operator_request(request)
        return require_canonical_timestamp(
            normalized["recovery"]["recover_through"], field="recover_through"
        )

    return _build_runtime_git_transaction_core(
        runtime_repo=runtime_repo,
        persistence_builder=_build_persistence,
        requested_target_time=_requested_target_time,
    )


def build_operator_upgrade_git_transaction(
    *,
    runtime_repo: Path | str,
    request: Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
    current_checkout: Path | str,
    engine_source_repo: Path | str,
    upgrade_adapter: UpgradeEnvironmentAdapter,
    target_checkout: Path | str | None = None,
) -> OperatorGitTransactionResult:
    """Local-only ENGINE/POLICY upgrade Git transaction: freeze base, stage, no refs."""
    from .operator_request import normalize_operator_request

    repo_path = Path(runtime_repo)
    temp_root: Path | None = None
    result: OperatorGitTransactionResult | None = None
    try:
        normalized = normalize_operator_request(request)
        action_type = normalized["request_type"]
        if action_type not in UPGRADE_REQUEST_TYPES:
            _fail(f"upgrade git transaction requires upgrade request, got {action_type!r}")

        authority = _freeze_authority(repo_path)
        _ls_tree_blobs(authority.git_dir, authority.head_sha)
        _assert_linear_ancestry(authority.git_dir, authority.head_sha)
        _assert_base_objects_locally_complete(authority.git_dir, authority.head_sha)

        temp_root = Path(tempfile.mkdtemp(prefix="life-engine-5e3-upg-", dir=None)).resolve()
        try:
            temp_root.relative_to(authority.git_dir)
            _fail("temp workspace resolved inside git dir")
        except ValueError:
            pass
        if authority.work_tree is not None:
            try:
                temp_root.relative_to(authority.work_tree)
                _fail("temp workspace resolved inside worktree")
            except ValueError:
                pass

        baseline_root = temp_root / "baseline"
        candidate_root = temp_root / "candidate"
        index_file = temp_root / "isolated-index"
        baseline_root.mkdir(parents=True, exist_ok=True)
        candidate_root.mkdir(parents=True, exist_ok=True)

        base_files = _materialize_base_tree(
            authority.git_dir, authority.head_sha, baseline_root
        )
        _assert_authority_unchanged(authority, stage="pre-upgrade-candidate")

        candidate = build_operator_upgrade_candidate(
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            request=normalized,
            life_base_sha=authority.head_sha,
            reference_sets=reference_sets,
            current_checkout=current_checkout,
            engine_source_repo=engine_source_repo,
            upgrade_adapter=upgrade_adapter,
            target_checkout=target_checkout,
        )
        if candidate.life_base_sha != authority.head_sha:
            _fail("upgrade life_base_sha != frozen HEAD")
        if candidate.status != "CHANGE":
            _fail(f"unexpected upgrade status: {candidate.status}")

        baseline_disk = list_runtime_files(baseline_root)
        if set(baseline_disk) != set(base_files):
            _fail("upgrade baseline file set != frozen Git base tree")
        for rel, data in base_files.items():
            if baseline_disk.get(rel) != data:
                _fail(f"baseline byte mismatch vs frozen Git blob: {rel}")

        state_before = compute_runtime_semantic_tree_hash(baseline_root)
        action = candidate.action
        request_id = str(action["request_id"])
        action_id = str(action["action_id"])

        baseline_snap = load_runtime_persistent_snapshot(
            baseline_root, reference_sets=reference_sets
        )
        state_after_pre = compute_runtime_semantic_tree_hash(candidate_root)
        hist_before = (
            baseline_snap.operator_history
            if baseline_snap.operator_history is not None
            else empty_operator_history()
        )
        provisional = list(candidate.files_changed)
        if LAST_OPERATOR_ACTION_RELPATH not in provisional:
            provisional = sorted([*provisional, LAST_OPERATOR_ACTION_RELPATH])

        last_op = build_last_operator_action(
            action=action,
            life_base_sha=authority.head_sha,
            engine_commit_sha=candidate.engine_commit_sha_after,
            behavior_policy_version=candidate.behavior_policy_version_after,
            state_revision_before=candidate.state_revision_before,
            state_revision_after=candidate.state_revision_after,
            state_before_hash=state_before,
            state_after_hash=state_after_pre,
            operator_history_hash_before=hist_before["operator_history_hash"],
            operator_history_hash_after=candidate.operator_history[
                "operator_history_hash"
            ],
            processed_through_before=baseline_snap.bundle.current_state[
                "processed_through"
            ],
            processed_through_after=baseline_snap.bundle.current_state[
                "processed_through"
            ],
            files_changed=provisional,
            engine_commit_sha_before=candidate.engine_commit_sha_before,
            behavior_policy_version_before=candidate.behavior_policy_version_before,
            policy_hash_before=candidate.current_policy_hash,
            policy_hash_after=candidate.target_policy_hash,
        )
        write_runtime_json(candidate_root / LAST_OPERATOR_ACTION_RELPATH, last_op)

        candidate_snap = load_runtime_persistent_snapshot(
            candidate_root, reference_sets=reference_sets
        )
        if candidate_snap.semantic_tree_hash != state_after_pre:
            _fail("last-operator-action must not alter semantic tree hash")
        if candidate_snap.last_operator_action is None:
            _fail("CHANGE upgrade missing last-operator-action after reload")
        validate_last_operator_action(candidate_snap.last_operator_action)
        if (
            candidate_snap.bundle.current_state["engine_commit_sha"]
            != last_op["engine_commit_sha"]
        ):
            _fail("upgrade last-operator-action engine pin != candidate CurrentState")
        if (
            candidate_snap.bundle.current_state["behavior_policy_version"]
            != last_op["behavior_policy_version"]
        ):
            _fail("upgrade last-operator-action policy version != candidate CurrentState")

        candidate_files = list_runtime_files(candidate_root)
        for rel in candidate_files:
            if not is_allowed_runtime_relpath(rel):
                _fail(f"forbidden path in operator candidate tree: {rel}")
        files_changed = tuple(_byte_diff_paths(base_files, candidate_files))
        if sorted(files_changed) != sorted(provisional):
            last_op = build_last_operator_action(
                action=action,
                life_base_sha=authority.head_sha,
                engine_commit_sha=candidate.engine_commit_sha_after,
                behavior_policy_version=candidate.behavior_policy_version_after,
                state_revision_before=candidate.state_revision_before,
                state_revision_after=candidate.state_revision_after,
                state_before_hash=state_before,
                state_after_hash=state_after_pre,
                operator_history_hash_before=hist_before["operator_history_hash"],
                operator_history_hash_after=candidate.operator_history[
                    "operator_history_hash"
                ],
                processed_through_before=baseline_snap.bundle.current_state[
                    "processed_through"
                ],
                processed_through_after=baseline_snap.bundle.current_state[
                    "processed_through"
                ],
                files_changed=files_changed,
                engine_commit_sha_before=candidate.engine_commit_sha_before,
                behavior_policy_version_before=candidate.behavior_policy_version_before,
                policy_hash_before=candidate.current_policy_hash,
                policy_hash_after=candidate.target_policy_hash,
            )
            write_runtime_json(candidate_root / LAST_OPERATOR_ACTION_RELPATH, last_op)
            candidate_files = list_runtime_files(candidate_root)
            files_changed = tuple(_byte_diff_paths(base_files, candidate_files))
            if sorted(files_changed) != sorted(last_op["files_changed"]):
                _fail("operator files_changed did not stabilize")

        expected_paths = {
            "state/current.json",
            "state/operator-history.json",
            f"operator/actions/{action_id}.json",
            LAST_OPERATOR_ACTION_RELPATH,
        }
        if set(files_changed) != expected_paths:
            _fail(
                "upgrade CHANGE must alter exactly the four operator paths: "
                f"got={sorted(files_changed)}"
            )

        frozen_candidate_bytes = dict(candidate_files)
        expected_blobs = _precompute_blob_ids(authority.git_dir, frozen_candidate_bytes)
        _assert_authority_unchanged(authority, stage="pre-upgrade-object-write")
        if list_runtime_files(candidate_root) != frozen_candidate_bytes:
            _fail("operator candidate tampered after staging")

        tree_sha = _write_candidate_tree(
            authority.git_dir,
            index_file=index_file,
            candidate_files=frozen_candidate_bytes,
            expected_blobs=expected_blobs,
        )
        git_date = _git_date_from_target_time(
            baseline_snap.bundle.current_state["processed_through"]
        )
        message = operator_commit_message(last_op)
        commit_sha = _commit_tree(
            authority.git_dir,
            tree_sha=tree_sha,
            parent_sha=authority.head_sha,
            message=message,
            git_date=git_date,
        )
        _verify_commit_object(
            authority.git_dir,
            commit_sha=commit_sha,
            tree_sha=tree_sha,
            parent_sha=authority.head_sha,
            message=message,
            git_date=git_date,
            files_changed=list(files_changed),
        )
        _assert_object_in_intended_repo(authority.git_dir, commit_sha)
        _assert_authority_unchanged(authority, stage="post-upgrade-build")
        _commit_is_unreferenced(authority.git_dir, commit_sha)

        result = OperatorGitTransactionResult(
            status="CHANGE",
            base_commit_sha=authority.head_sha,
            candidate_tree_sha=tree_sha,
            candidate_commit_sha=commit_sha,
            action_id=action_id,
            request_id=request_id,
            action_type=action_type,
            state_before_hash=state_before,
            state_after_hash=state_after_pre,
            files_changed=files_changed,
            last_operator_action=dict(last_op),
        )
    except BaseException as primary:
        try:
            _cleanup_owned_temp_root(temp_root)
        except Exception as cleanup_exc:
            raise cleanup_exc from primary
        raise

    _cleanup_owned_temp_root(temp_root)
    if result is None:
        _fail("internal error: upgrade transaction produced no result")
    return result
