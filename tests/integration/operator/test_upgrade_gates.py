"""Synthetic public upgrade-adapter and upgrade transaction tests."""

from __future__ import annotations

import json
import subprocess
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Mapping

import pytest

from engine.life.errors import LifeEngineError
from engine.life.operator_git_transaction import build_operator_upgrade_git_transaction
from engine.life.operator_upgrade import (
    CheckoutPolicyMaterial,
    CompatibilityProbeResult,
    load_checkout_policy_identity,
    run_target_compatibility_probe,
    verify_checkout_head_clean,
)
from engine.life.policy import hash_policy
from engine.life.runtime_persistence import compute_runtime_semantic_tree_hash
from tests.support.builders.persistence import (
    CHAR,
    POLICY,
    _advance_refs,
    build_5c1_persistent_root,
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


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def _engine_source(tmp_path: Path) -> tuple[Path, str, str, Path]:
    source = tmp_path / "engine-source"
    subprocess.run(["git", "init", "-b", "main", str(source)], check=True, capture_output=True)
    _git(source, "config", "user.name", "fixture")
    _git(source, "config", "user.email", "fixture@test.invalid")
    (source / "engine.txt").write_text("current\n", encoding="utf-8")
    _git(source, "add", "engine.txt")
    _git(source, "commit", "-m", "current")
    current_sha = _git(source, "rev-parse", "HEAD")
    (source / "engine.txt").write_text("target\n", encoding="utf-8")
    _git(source, "commit", "-am", "target")
    target_sha = _git(source, "rev-parse", "HEAD")
    current_checkout = tmp_path / "current-checkout"
    subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "--detach", str(current_checkout), current_sha],
        check=True,
        capture_output=True,
    )
    return source, current_sha, target_sha, current_checkout


class SyntheticUpgradeAdapter:
    def __init__(self, policy: Mapping, *, approval_ref: str = "review:engine-upgrade") -> None:
        self.policy = deepcopy(dict(policy))
        self.approval_ref = approval_ref
        self.return_checkout_outside_root = False
        self.dirty_probe_checkout = False
        self.wrong_probe_pin = False
        self.wrong_probe_policy_hash = False
        self.wrong_probe_semantic_hash = False

    def load_checkout_policy_material(self, checkout: Path) -> CheckoutPolicyMaterial:
        return CheckoutPolicyMaterial(
            character_id=CHAR,
            behavior_policy=self.policy,
            policy_approval={
                "policy_hash": hash_policy(self.policy),
                "review_ref": self.approval_ref,
            },
            declared_behavior_policy_version=self.policy["behavior_policy_version"],
        )

    def materialize_pinned_engine_checkout(
        self,
        *,
        engine_source_repo: Path,
        engine_commit_sha: str,
        work_root: Path,
    ) -> Path:
        dest = work_root
        if self.return_checkout_outside_root:
            dest = work_root.parent / "outside-checkout"
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(engine_source_repo),
                "worktree",
                "add",
                "--detach",
                str(dest),
                engine_commit_sha,
            ],
            check=True,
            capture_output=True,
        )
        return dest

    def cleanup_materialized_checkout(
        self,
        *,
        engine_source_repo: Path,
        checkout: Path,
    ) -> None:
        subprocess.run(
            ["git", "-C", str(engine_source_repo), "worktree", "remove", "--force", str(checkout)],
            check=False,
            capture_output=True,
        )

    def probe_target_compatibility(
        self,
        *,
        target_checkout: Path,
        target_engine_commit_sha: str,
        life_files: Mapping[str, bytes],
    ) -> CompatibilityProbeResult:
        if self.dirty_probe_checkout:
            (target_checkout / "probe-dirt.txt").write_text("dirty\n", encoding="utf-8")
        current = json.loads(life_files["state/current.json"].decode("utf-8"))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, raw in life_files.items():
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
            semantic_hash = compute_runtime_semantic_tree_hash(root)
        return CompatibilityProbeResult(
            target_engine_commit_sha=("f" * 40 if self.wrong_probe_pin else target_engine_commit_sha),
            character_id=current["character_id"],
            behavior_policy_version=self.policy["behavior_policy_version"],
            policy_hash=("f" * 64 if self.wrong_probe_policy_hash else hash_policy(self.policy)),
            state_revision=int(current["state_revision"]),
            processed_through=current["processed_through"],
            engine_commit_sha=current["engine_commit_sha"],
            semantic_tree_hash=("f" * 64 if self.wrong_probe_semantic_hash else semantic_hash),
        )


def _upgrade_request(*, life_head: str, current_sha: str, target_sha: str) -> dict:
    policy_hash = hash_policy(POLICY)
    return {
        "schema_version": 1,
        "request_type": "ENGINE_UPGRADE",
        "expected_life_head": life_head,
        "approval_ref": "review:engine-upgrade",
        "upgrade": {
            "current_engine_commit_sha": current_sha,
            "target_engine_commit_sha": target_sha,
            "current_behavior_policy_version": POLICY["behavior_policy_version"],
            "target_behavior_policy_version": POLICY["behavior_policy_version"],
            "current_policy_hash": policy_hash,
            "target_policy_hash": policy_hash,
        },
    }


def _authority_snapshot(repo: Path):
    return (
        snapshot_refs_head(repo),
        snapshot_index(repo),
        snapshot_worktree(repo),
    )


def test_checkout_pin_and_cleanliness_are_core_verified(tmp_path: Path) -> None:
    source, current_sha, _target_sha, current_checkout = _engine_source(tmp_path)
    assert verify_checkout_head_clean(
        current_checkout,
        expected_commit_sha=current_sha,
        label="current",
    ) == current_checkout.resolve()
    (current_checkout / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(LifeEngineError, match="dirty"):
        verify_checkout_head_clean(
            current_checkout,
            expected_commit_sha=current_sha,
            label="current",
        )


def test_core_revalidates_adapter_policy_material(tmp_path: Path) -> None:
    source, current_sha, _target_sha, current_checkout = _engine_source(tmp_path)
    adapter = SyntheticUpgradeAdapter(POLICY)
    identity = load_checkout_policy_identity(current_checkout, upgrade_adapter=adapter)
    assert identity.character_id == CHAR
    assert identity.policy_hash == hash_policy(POLICY)

    bad_policy = deepcopy(POLICY)
    bad_policy["policy_mode"] = "TEST"

    class BadPolicyAdapter(SyntheticUpgradeAdapter):
        def load_checkout_policy_material(self, checkout: Path) -> CheckoutPolicyMaterial:
            return CheckoutPolicyMaterial(
                character_id=CHAR,
                behavior_policy=bad_policy,
                policy_approval={
                    "policy_hash": "0" * 64,
                    "review_ref": "review:engine-upgrade",
                },
                declared_behavior_policy_version=bad_policy["behavior_policy_version"],
            )

    with pytest.raises(LifeEngineError, match="policy_mode=PRODUCTION"):
        load_checkout_policy_identity(
            current_checkout, upgrade_adapter=BadPolicyAdapter(POLICY)
        )


def test_probe_rejects_wrong_pin_outside_checkout_and_dirty_checkout(tmp_path: Path) -> None:
    source, _current_sha, target_sha, _current_checkout = _engine_source(tmp_path)
    life_root = build_5c1_persistent_root(tmp_path / "runtime")
    life_files = {
        path.relative_to(life_root).as_posix(): path.read_bytes()
        for path in life_root.rglob("*")
        if path.is_file()
    }

    wrong_pin = SyntheticUpgradeAdapter(POLICY)
    wrong_pin.wrong_probe_pin = True
    with pytest.raises(LifeEngineError, match="target pin mismatch"):
        run_target_compatibility_probe(
            engine_source_repo=source,
            target_engine_commit_sha=target_sha,
            life_files=life_files,
            upgrade_adapter=wrong_pin,
        )

    outside = SyntheticUpgradeAdapter(POLICY)
    outside.return_checkout_outside_root = True
    with pytest.raises(LifeEngineError, match="outside owned work_root"):
        run_target_compatibility_probe(
            engine_source_repo=source,
            target_engine_commit_sha=target_sha,
            life_files=life_files,
            upgrade_adapter=outside,
        )

    dirty = SyntheticUpgradeAdapter(POLICY)
    dirty.dirty_probe_checkout = True
    with pytest.raises(LifeEngineError, match="left target checkout dirty"):
        run_target_compatibility_probe(
            engine_source_repo=source,
            target_engine_commit_sha=target_sha,
            life_files=life_files,
            upgrade_adapter=dirty,
        )


def test_upgrade_candidate_rejects_probe_identity_or_semantic_mismatch(tmp_path: Path) -> None:
    source, current_sha, target_sha, current_checkout = _engine_source(tmp_path)
    root = build_5c1_persistent_root(
        tmp_path / "runtime",
        engine_commit_sha=current_sha,
        behavior_policy_version=POLICY["behavior_policy_version"],
    )
    repo = build_runtime_git_repo(tmp_path / "repo", root)
    head = git_out(repo, "rev-parse", "HEAD")
    request = _upgrade_request(life_head=head, current_sha=current_sha, target_sha=target_sha)

    wrong_policy = SyntheticUpgradeAdapter(POLICY)
    wrong_policy.wrong_probe_policy_hash = True
    with pytest.raises(LifeEngineError, match="policy hash mismatch"):
        build_operator_upgrade_git_transaction(
            runtime_repo=repo,
            request=request,
            reference_sets=_advance_refs(),
            current_checkout=current_checkout,
            engine_source_repo=source,
            upgrade_adapter=wrong_policy,
        )

    wrong_semantic = SyntheticUpgradeAdapter(POLICY)
    wrong_semantic.wrong_probe_semantic_hash = True
    with pytest.raises(LifeEngineError, match="semantic_tree_hash mismatch"):
        build_operator_upgrade_git_transaction(
            runtime_repo=repo,
            request=request,
            reference_sets=_advance_refs(),
            current_checkout=current_checkout,
            engine_source_repo=source,
            upgrade_adapter=wrong_semantic,
        )


def test_engine_upgrade_git_transaction_is_local_and_deterministic(tmp_path: Path) -> None:
    source, current_sha, target_sha, current_checkout = _engine_source(tmp_path)
    root = build_5c1_persistent_root(
        tmp_path / "runtime",
        engine_commit_sha=current_sha,
        behavior_policy_version=POLICY["behavior_policy_version"],
    )
    repo = build_runtime_git_repo(tmp_path / "repo", root)
    head = git_out(repo, "rev-parse", "HEAD")
    request = _upgrade_request(life_head=head, current_sha=current_sha, target_sha=target_sha)
    adapter = SyntheticUpgradeAdapter(POLICY)
    before = _authority_snapshot(repo)

    first = build_operator_upgrade_git_transaction(
        runtime_repo=repo,
        request=request,
        reference_sets=_advance_refs(),
        current_checkout=current_checkout,
        engine_source_repo=source,
        upgrade_adapter=adapter,
    )
    second = build_operator_upgrade_git_transaction(
        runtime_repo=repo,
        request=request,
        reference_sets=_advance_refs(),
        current_checkout=current_checkout,
        engine_source_repo=source,
        upgrade_adapter=adapter,
    )

    assert first.status == "CHANGE"
    assert first.action_type == "ENGINE_UPGRADE"
    assert first.base_commit_sha == head
    assert first.candidate_commit_sha == second.candidate_commit_sha
    assert set(first.files_changed) == {
        f"operator/actions/{first.action_id}.json",
        "state/current.json",
        "state/last-operator-action.json",
        "state/operator-history.json",
    }
    assert _authority_snapshot(repo) == before
    parents = git_out(repo, "rev-list", "--parents", "-n", "1", first.candidate_commit_sha).split()
    assert parents == [first.candidate_commit_sha, head]
    assert git_out(repo, "show", "-s", "--format=%B", first.candidate_commit_sha).startswith(
        "Life Engine operator engine-upgrade "
    )


def test_policy_upgrade_updates_policy_identity_without_mutating_runtime_authority(tmp_path: Path) -> None:
    source, current_sha, target_sha, current_checkout = _engine_source(tmp_path)
    target_policy = deepcopy(POLICY)
    target_policy["behavior_policy_version"] = "public-fixture-v2-candidate-2"
    current_hash = hash_policy(POLICY)
    target_hash = hash_policy(target_policy)

    class PolicyUpgradeAdapter(SyntheticUpgradeAdapter):
        def load_checkout_policy_material(self, checkout: Path) -> CheckoutPolicyMaterial:
            head = _git(checkout, "rev-parse", "HEAD")
            policy = POLICY if head == current_sha else target_policy
            review_ref = "review:policy-upgrade" if head == target_sha else "review:current"
            return CheckoutPolicyMaterial(
                character_id=CHAR,
                behavior_policy=policy,
                policy_approval={
                    "policy_hash": hash_policy(policy),
                    "review_ref": review_ref,
                },
                declared_behavior_policy_version=policy["behavior_policy_version"],
            )

        def probe_target_compatibility(
            self,
            *,
            target_checkout: Path,
            target_engine_commit_sha: str,
            life_files: Mapping[str, bytes],
        ) -> CompatibilityProbeResult:
            current = json.loads(life_files["state/current.json"].decode("utf-8"))
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for rel, raw in life_files.items():
                    dest = root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(raw)
                semantic_hash = compute_runtime_semantic_tree_hash(root)
            return CompatibilityProbeResult(
                target_engine_commit_sha=target_engine_commit_sha,
                character_id=current["character_id"],
                behavior_policy_version=target_policy["behavior_policy_version"],
                policy_hash=target_hash,
                state_revision=int(current["state_revision"]),
                processed_through=current["processed_through"],
                engine_commit_sha=current["engine_commit_sha"],
                semantic_tree_hash=semantic_hash,
            )

    root = build_5c1_persistent_root(
        tmp_path / "runtime-policy",
        engine_commit_sha=current_sha,
        behavior_policy_version=POLICY["behavior_policy_version"],
    )
    repo = build_runtime_git_repo(tmp_path / "repo-policy", root)
    head = git_out(repo, "rev-parse", "HEAD")
    request = {
        "schema_version": 1,
        "request_type": "POLICY_UPGRADE",
        "expected_life_head": head,
        "approval_ref": "review:policy-upgrade",
        "upgrade": {
            "current_engine_commit_sha": current_sha,
            "target_engine_commit_sha": target_sha,
            "current_behavior_policy_version": POLICY["behavior_policy_version"],
            "target_behavior_policy_version": target_policy["behavior_policy_version"],
            "current_policy_hash": current_hash,
            "target_policy_hash": target_hash,
        },
    }
    before = _authority_snapshot(repo)
    result = build_operator_upgrade_git_transaction(
        runtime_repo=repo,
        request=request,
        reference_sets=_advance_refs(),
        current_checkout=current_checkout,
        engine_source_repo=source,
        upgrade_adapter=PolicyUpgradeAdapter(POLICY),
    )

    assert result.status == "CHANGE"
    assert result.action_type == "POLICY_UPGRADE"
    assert result.last_operator_action is not None
    assert result.last_operator_action["behavior_policy_version"] == target_policy[
        "behavior_policy_version"
    ]
    assert result.last_operator_action["policy_hash_before"] == current_hash
    assert result.last_operator_action["policy_hash_after"] == target_hash
    assert _authority_snapshot(repo) == before
