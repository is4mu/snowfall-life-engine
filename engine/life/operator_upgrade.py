"""Slice 5E3 — ENGINE_UPGRADE / POLICY_UPGRADE candidate gates.

The currently executing (old) pin owns validation and candidate staging.
Target code is used only for a read-only compatibility probe; no simulation
or effect application runs under the target engine during the upgrade.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .checkpoint import bump_revision, validate_checkpoint
from .errors import ErrorCode, LifeEngineError
from .operator_action import (
    FrozenOperatorLedger,
    build_operator_action_from_request,
    load_operator_ledger,
    normalize_operator_action,
    operator_action_relpath,
)
from .operator_history import (
    OPERATOR_HISTORY_RELPATH,
    advance_operator_history,
    empty_operator_history,
)
from .operator_request import (
    UPGRADE_REQUEST_TYPES,
    normalize_operator_request,
)
from .policy import hash_policy
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, load_runtime_bundle, validate_runtime_bundle
from .runtime_persistence import (
    list_runtime_files,
    load_runtime_persistent_snapshot,
    snapshot_tree_bytes,
    write_runtime_json,
)
from .timeutil import require_canonical_timestamp


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


@dataclass(frozen=True)
class CheckoutPolicyMaterial:
    """Host-loaded policy/approval material from one exact engine checkout."""

    character_id: str
    behavior_policy: Mapping[str, Any]
    policy_approval: Mapping[str, Any]
    declared_behavior_policy_version: str


@dataclass(frozen=True)
class CheckoutPolicyIdentity:
    """Host-supplied approved policy identity for one exact engine checkout."""

    character_id: str
    behavior_policy_version: str
    policy_hash: str
    approval_ref: str


@dataclass(frozen=True)
class CompatibilityProbeResult:
    target_engine_commit_sha: str
    character_id: str
    behavior_policy_version: str
    policy_hash: str
    state_revision: int
    processed_through: str
    engine_commit_sha: str
    semantic_tree_hash: str


class UpgradeEnvironmentAdapter(Protocol):
    """Environment boundary required by the public upgrade gate.

    Implementations own host-specific policy approval discovery, exact checkout
    materialization, cleanup, and execution of the target-engine read-only
    compatibility probe. The Life Engine independently verifies exact Git pins,
    clean checkouts, immutable Life bytes, and returned compatibility evidence.
    """

    def load_checkout_policy_material(
        self, checkout: Path
    ) -> CheckoutPolicyMaterial: ...

    def materialize_pinned_engine_checkout(
        self,
        *,
        engine_source_repo: Path,
        engine_commit_sha: str,
        work_root: Path,
    ) -> Path: ...

    def cleanup_materialized_checkout(
        self,
        *,
        engine_source_repo: Path,
        checkout: Path,
    ) -> None: ...

    def probe_target_compatibility(
        self,
        *,
        target_checkout: Path,
        target_engine_commit_sha: str,
        life_files: Mapping[str, bytes],
    ) -> CompatibilityProbeResult: ...


@dataclass(frozen=True)
class OperatorUpgradeCandidateResult:
    status: str  # CHANGE only (no NOOP path in 5E3 upgrade gate)
    baseline_root: Path
    candidate_root: Path
    life_base_sha: str
    action: Mapping[str, Any]
    operator_history: Mapping[str, Any]
    state_revision_before: int
    state_revision_after: int
    files_changed: tuple[str, ...]
    baseline_fingerprint: Mapping[str, bytes]
    current_policy_hash: str
    target_policy_hash: str
    engine_commit_sha_before: str
    engine_commit_sha_after: str
    behavior_policy_version_before: str
    behavior_policy_version_after: str
    compatibility: CompatibilityProbeResult


def _resolve_distinct_roots(baseline: Path, candidate: Path) -> tuple[Path, Path]:
    base = baseline.resolve(strict=False)
    cand = candidate.resolve(strict=False)
    if base == cand:
        _fail("candidate_root must differ from baseline_root")
    if cand.exists() and any(cand.iterdir()):
        _fail(f"candidate_root must be empty or nonexistent: {cand}")
    return base, cand


def _copy_tree(baseline: Path, candidate: Path) -> None:
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=True)
    for path in sorted(baseline.rglob("*")):
        if path.is_symlink():
            _fail(f"baseline contains symlink: {path}")
        if not path.is_file():
            continue
        rel = path.relative_to(baseline).as_posix()
        dest = candidate / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def _diff_relpaths(baseline: Path, candidate: Path) -> tuple[str, ...]:
    before = snapshot_tree_bytes(baseline)
    after = snapshot_tree_bytes(candidate)
    changed: list[str] = []
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) != after.get(rel):
            changed.append(rel)
    return tuple(changed)


def _run_git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        _fail(f"git {' '.join(args)} failed: {detail or completed.returncode}")
    return completed


def verify_checkout_head_clean(
    checkout: Path | str,
    *,
    expected_commit_sha: str,
    label: str,
) -> Path:
    """Prove checkout HEAD equals expected pin and working tree is clean."""
    root = Path(checkout).resolve()
    if not root.is_dir():
        _fail(f"{label} checkout is not a directory: {root}")
    pin = expected_commit_sha.lower()
    if len(pin) != 40 or any(c not in "0123456789abcdef" for c in pin):
        _fail(f"{label} expected commit must be 40 lower-hex")
    head = _run_git(root, ["rev-parse", "--verify", "HEAD^{commit}"])
    actual = head.stdout.decode("utf-8").strip().lower()
    if actual != pin:
        _fail(f"{label} checkout HEAD mismatch: expected {pin}, got {actual}")
    status = _run_git(root, ["status", "--porcelain=v1", "-uall"])
    if status.stdout.strip():
        _fail(f"{label} checkout is dirty; refusing upgrade gate")
    return root


def _require_upgrade_adapter(adapter: object) -> None:
    for name in (
        "load_checkout_policy_material",
        "materialize_pinned_engine_checkout",
        "cleanup_materialized_checkout",
        "probe_target_compatibility",
    ):
        if not callable(getattr(adapter, name, None)):
            _fail(f"upgrade_adapter.{name} must be callable")


def _validate_checkout_policy_material(
    material: object,
) -> CheckoutPolicyMaterial:
    if not isinstance(material, CheckoutPolicyMaterial):
        _fail("upgrade adapter must return CheckoutPolicyMaterial")
    if not isinstance(material.character_id, str) or not material.character_id:
        _fail("checkout policy material character_id must be non-empty")
    if not isinstance(material.behavior_policy, Mapping):
        _fail("checkout policy material behavior_policy must be a mapping")
    if not isinstance(material.policy_approval, Mapping):
        _fail("checkout policy material policy_approval must be a mapping")
    if (
        not isinstance(material.declared_behavior_policy_version, str)
        or not material.declared_behavior_policy_version
    ):
        _fail("checkout declared behavior policy version must be non-empty")
    return material


def load_checkout_policy_identity(
    checkout: Path | str,
    *,
    upgrade_adapter: "UpgradeEnvironmentAdapter",
) -> CheckoutPolicyIdentity:
    """Load and independently validate approved policy identity via the adapter."""
    _require_upgrade_adapter(upgrade_adapter)
    root = Path(checkout).resolve()
    material = _validate_checkout_policy_material(
        upgrade_adapter.load_checkout_policy_material(root)
    )
    policy = material.behavior_policy
    if int(policy.get("schema_version", -1)) != 2:
        _fail("checkout policy must be schema_version=2")
    if policy.get("policy_mode") != "PRODUCTION":
        _fail("checkout policy must be policy_mode=PRODUCTION")
    version = policy.get("behavior_policy_version")
    if not isinstance(version, str) or not version:
        _fail("checkout policy behavior_policy_version must be non-empty")
    policy_hash = hash_policy(policy)
    approval = material.policy_approval
    if approval.get("policy_hash") != policy_hash:
        _fail("checkout policy approval hash mismatch")
    approval_ref = approval.get("review_ref")
    if not isinstance(approval_ref, str) or not approval_ref:
        _fail("checkout policy approval review_ref must be non-empty")
    if material.declared_behavior_policy_version != version:
        _fail("checkout declared behavior_policy_version mismatch vs policy")
    return CheckoutPolicyIdentity(
        character_id=material.character_id,
        behavior_policy_version=version,
        policy_hash=policy_hash,
        approval_ref=approval_ref,
    )


def validate_upgrade_request_against_state(
    request: Mapping[str, Any],
    *,
    current_state: Mapping[str, Any],
    life_base_sha: str,
) -> dict[str, Any]:
    """Apply-time identity checks against frozen Life base + CurrentState."""
    normalized = normalize_operator_request(request)
    request_type = normalized["request_type"]
    if request_type not in UPGRADE_REQUEST_TYPES:
        _fail(f"not an upgrade request: {request_type!r}")
    if normalized["expected_life_head"] != life_base_sha:
        _fail("request.expected_life_head must equal frozen Life base (stale Life head)")
    upgrade = normalized["upgrade"]
    if upgrade["current_engine_commit_sha"] != current_state["engine_commit_sha"]:
        _fail("stale current_engine_commit_sha vs CurrentState.engine_commit_sha")
    if (
        upgrade["current_behavior_policy_version"]
        != current_state["behavior_policy_version"]
    ):
        _fail(
            "stale current_behavior_policy_version vs CurrentState.behavior_policy_version"
        )
    return normalized


def validate_current_checkout_for_upgrade(
    *,
    current_checkout: Path | str,
    current_state: Mapping[str, Any],
    request: Mapping[str, Any],
    upgrade_adapter: UpgradeEnvironmentAdapter,
) -> CheckoutPolicyIdentity:
    """Prove the executing old pin matches CurrentState + request current identity."""
    normalized = normalize_operator_request(request)
    upgrade = normalized["upgrade"]
    root = verify_checkout_head_clean(
        current_checkout,
        expected_commit_sha=current_state["engine_commit_sha"],
        label="current",
    )
    if current_state["engine_commit_sha"] != upgrade["current_engine_commit_sha"]:
        _fail("current checkout pin mismatch vs request current_engine_commit_sha")
    identity = load_checkout_policy_identity(root, upgrade_adapter=upgrade_adapter)
    if identity.character_id != current_state["character_id"]:
        _fail("current checkout identity character_id mismatch vs CurrentState")
    if identity.behavior_policy_version != upgrade["current_behavior_policy_version"]:
        _fail("current approved policy version mismatch vs request")
    if identity.policy_hash != upgrade["current_policy_hash"]:
        _fail("current approved policy hash mismatch vs request")
    return identity


def validate_target_checkout_for_upgrade(
    *,
    target_checkout: Path | str,
    request: Mapping[str, Any],
    current_state: Mapping[str, Any],
    current_identity: CheckoutPolicyIdentity,
    upgrade_adapter: UpgradeEnvironmentAdapter,
) -> CheckoutPolicyIdentity:
    """Prove exact local clean target pin + approved PRODUCTION policy identity."""
    normalized = normalize_operator_request(request)
    request_type = normalized["request_type"]
    upgrade = normalized["upgrade"]
    root = verify_checkout_head_clean(
        target_checkout,
        expected_commit_sha=upgrade["target_engine_commit_sha"],
        label="target",
    )
    identity = load_checkout_policy_identity(root, upgrade_adapter=upgrade_adapter)
    if identity.character_id != current_state["character_id"]:
        _fail("target character_id mismatch vs CurrentState")
    if identity.character_id != current_identity.character_id:
        _fail("target character_id mismatch vs current checkout identity")
    if identity.behavior_policy_version != upgrade["target_behavior_policy_version"]:
        _fail("target approved policy version mismatch vs request")
    if identity.policy_hash != upgrade["target_policy_hash"]:
        _fail("target approved policy hash mismatch vs request")
    if request_type == "ENGINE_UPGRADE":
        if identity.behavior_policy_version != current_identity.behavior_policy_version:
            _fail("ENGINE_UPGRADE target policy version must equal current")
        if identity.policy_hash != current_identity.policy_hash:
            _fail("ENGINE_UPGRADE target policy hash must equal current")
    else:
        if identity.approval_ref != normalized["approval_ref"]:
            _fail(
                "POLICY_UPGRADE approval review_ref must equal request.approval_ref"
            )
        if identity.behavior_policy_version == current_identity.behavior_policy_version:
            _fail("POLICY_UPGRADE target policy version must differ from current")
        if identity.policy_hash == current_identity.policy_hash:
            _fail("POLICY_UPGRADE target policy hash must differ from current")
    return identity


def _source_git_authority(repo: Path) -> tuple[bytes, bytes]:
    refs = _run_git(repo, ["for-each-ref", "--format=%(refname) %(objectname)"])
    head = _run_git(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
    return refs.stdout, head.stdout


def _materialize_exact_target_checkout(
    *,
    upgrade_adapter: UpgradeEnvironmentAdapter,
    engine_source_repo: Path | str,
    target_engine_commit_sha: str,
    work_root: Path,
    label: str,
) -> Path:
    _require_upgrade_adapter(upgrade_adapter)
    source = Path(engine_source_repo).resolve()
    if not source.is_dir():
        _fail("engine_source_repo is not a directory")
    work = work_root.resolve()
    work.mkdir(parents=True, exist_ok=True)
    before = _source_git_authority(source)
    checkout = Path(
        upgrade_adapter.materialize_pinned_engine_checkout(
            engine_source_repo=source,
            engine_commit_sha=target_engine_commit_sha,
            work_root=work,
        )
    ).resolve()
    try:
        checkout.relative_to(work)
    except ValueError:
        _fail("upgrade adapter returned checkout outside owned work_root")
    verify_checkout_head_clean(
        checkout, expected_commit_sha=target_engine_commit_sha, label=label
    )
    if _source_git_authority(source) != before:
        _fail("upgrade checkout materialization mutated engine-source refs/HEAD")
    return checkout


def _cleanup_adapter_checkout(
    *,
    upgrade_adapter: UpgradeEnvironmentAdapter,
    engine_source_repo: Path | str,
    checkout: Path | None,
) -> None:
    if checkout is None or not checkout.exists():
        return
    upgrade_adapter.cleanup_materialized_checkout(
        engine_source_repo=Path(engine_source_repo).resolve(),
        checkout=checkout.resolve(),
    )


def run_target_compatibility_probe(
    *,
    engine_source_repo: Path | str,
    target_engine_commit_sha: str,
    life_files: Mapping[str, bytes],
    upgrade_adapter: UpgradeEnvironmentAdapter,
    work_root: Path | str | None = None,
) -> CompatibilityProbeResult:
    """Run a host-provided read-only probe under public fail-closed guards.

    The adapter owns target-engine execution details. This function owns the
    portable safety contract: exact SHA pin, clean checkout, no source Git
    ref/HEAD mutation, immutable Life input bytes, and typed compatibility
    evidence bound to the requested target pin.
    """
    _require_upgrade_adapter(upgrade_adapter)
    pin = target_engine_commit_sha.lower()
    if len(pin) != 40 or any(c not in "0123456789abcdef" for c in pin):
        _fail("target_engine_commit_sha must be 40 lower-hex")

    life_before = dict(life_files)
    for rel, data in life_before.items():
        if not isinstance(rel, str) or not rel:
            _fail("life_files paths must be non-empty strings")
        if not isinstance(data, bytes):
            _fail(f"life_files[{rel}] must be raw bytes")

    source = Path(engine_source_repo).resolve()
    if not source.is_dir():
        _fail("engine_source_repo is not a directory")
    source_before = _source_git_authority(source)
    owned_tmp: Path | None = None
    target_checkout: Path | None = None
    try:
        if work_root is None:
            owned_tmp = Path(
                tempfile.mkdtemp(prefix="life-engine-5e3-probe-")
            ).resolve()
            root = owned_tmp
        else:
            root = Path(work_root).resolve()
            root.mkdir(parents=True, exist_ok=True)

        target_checkout = _materialize_exact_target_checkout(
            upgrade_adapter=upgrade_adapter,
            engine_source_repo=source,
            target_engine_commit_sha=pin,
            work_root=root / "target-checkout",
            label="target-probe",
        )
        target_head_before = _run_git(
            target_checkout, ["rev-parse", "--verify", "HEAD^{commit}"]
        ).stdout

        frozen_life = MappingProxyType(dict(life_before))
        result = upgrade_adapter.probe_target_compatibility(
            target_checkout=target_checkout,
            target_engine_commit_sha=pin,
            life_files=frozen_life,
        )
        if not isinstance(result, CompatibilityProbeResult):
            _fail("upgrade adapter probe must return CompatibilityProbeResult")
        if result.target_engine_commit_sha != pin:
            _fail("compatibility probe evidence target pin mismatch")
        if (
            not isinstance(result.semantic_tree_hash, str)
            or len(result.semantic_tree_hash) != 64
            or any(c not in "0123456789abcdef" for c in result.semantic_tree_hash)
        ):
            _fail("compatibility probe semantic_tree_hash must be 64 lower-hex")
        require_canonical_timestamp(
            result.processed_through, field="compatibility.processed_through"
        )
        if dict(frozen_life) != life_before:
            _fail("compatibility probe mutated Life bytes")
        if _source_git_authority(source) != source_before:
            _fail("compatibility probe mutated engine-source Git refs/HEAD")
        target_head_after = _run_git(
            target_checkout, ["rev-parse", "--verify", "HEAD^{commit}"]
        ).stdout
        if target_head_after != target_head_before:
            _fail("compatibility probe mutated target checkout HEAD")
        status = _run_git(target_checkout, ["status", "--porcelain=v1", "-uall"])
        if status.stdout.strip():
            _fail("compatibility probe left target checkout dirty")
        return result
    finally:
        if target_checkout is not None:
            try:
                _cleanup_adapter_checkout(
                    upgrade_adapter=upgrade_adapter,
                    engine_source_repo=source,
                    checkout=target_checkout,
                )
            except Exception:
                pass
        if owned_tmp is not None and owned_tmp.exists():
            shutil.rmtree(owned_tmp, ignore_errors=True)


def build_operator_upgrade_candidate(
    *,
    baseline_root: Path | str,
    candidate_root: Path | str,
    request: Mapping[str, Any],
    life_base_sha: str,
    reference_sets: RuntimeReferenceSets,
    current_checkout: Path | str,
    engine_source_repo: Path | str,
    upgrade_adapter: UpgradeEnvironmentAdapter,
    target_checkout: Path | str | None = None,
) -> OperatorUpgradeCandidateResult:
    """Stage one operator-only upgrade candidate under the old pin.

    Changes only CurrentState engine/policy identity fields + operator ledger.
    Does not simulate, rewrite last-run, mutate history/timeline/camera/domain.
    """
    _require_upgrade_adapter(upgrade_adapter)
    base, cand = _resolve_distinct_roots(Path(baseline_root), Path(candidate_root))
    baseline_fp = snapshot_tree_bytes(base)

    baseline_snapshot = load_runtime_persistent_snapshot(
        base, reference_sets=reference_sets
    )
    ledger = load_operator_ledger(base)
    current = baseline_snapshot.bundle.current_state
    rev_before = int(current["state_revision"])
    processed = require_canonical_timestamp(
        current["processed_through"], field="processed_through"
    )

    normalized = validate_upgrade_request_against_state(
        request, current_state=current, life_base_sha=life_base_sha
    )
    request_type = normalized["request_type"]
    upgrade = normalized["upgrade"]

    current_identity = validate_current_checkout_for_upgrade(
        current_checkout=current_checkout,
        current_state=current,
        request=normalized,
        upgrade_adapter=upgrade_adapter,
    )

    owned_target: Path | None = None
    probe_tmp: Path | None = None
    try:
        if target_checkout is None:
            probe_tmp = Path(tempfile.mkdtemp(prefix="life-engine-5e3-target-")).resolve()
            owned_target = _materialize_exact_target_checkout(
                upgrade_adapter=upgrade_adapter,
                engine_source_repo=engine_source_repo,
                target_engine_commit_sha=upgrade["target_engine_commit_sha"],
                work_root=probe_tmp / "checkout",
                label="target",
            )
            target_root = owned_target
        else:
            target_root = Path(target_checkout)

        target_identity = validate_target_checkout_for_upgrade(
            target_checkout=target_root,
            request=normalized,
            current_state=current,
            current_identity=current_identity,
            upgrade_adapter=upgrade_adapter,
        )

        compatibility = run_target_compatibility_probe(
            engine_source_repo=engine_source_repo,
            target_engine_commit_sha=upgrade["target_engine_commit_sha"],
            life_files=list_runtime_files(base),
            upgrade_adapter=upgrade_adapter,
        )
        if compatibility.engine_commit_sha != current["engine_commit_sha"]:
            _fail("compatibility probe saw unexpected CurrentState.engine_commit_sha")
        if compatibility.character_id != current["character_id"]:
            _fail("compatibility probe character_id mismatch")
        if compatibility.behavior_policy_version != target_identity.behavior_policy_version:
            _fail("compatibility probe policy version mismatch vs target identity")
        if compatibility.policy_hash != target_identity.policy_hash:
            _fail("compatibility probe policy hash mismatch vs target identity")
        if int(compatibility.state_revision) != rev_before:
            _fail("compatibility probe must not mutate/observe advanced revision")
        if compatibility.processed_through != processed:
            _fail("compatibility probe processed_through mismatch")
        if compatibility.semantic_tree_hash != baseline_snapshot.semantic_tree_hash:
            _fail("compatibility probe semantic_tree_hash mismatch")

        # Idempotence: identical request already applied on this Life base → reject
        # as stale (upgrade gates are not NOOP-retryable once identity advanced).
        from .operator_request import derive_request_id

        request_id = derive_request_id(normalized)
        for prior in ledger.actions:
            if (
                prior["request_id"] == request_id
                and prior["life_base_sha"] == life_base_sha
            ):
                _fail("upgrade request already applied on this Life base")

        action = build_operator_action_from_request(
            normalized,
            life_base_sha=life_base_sha,
            previous_action_id=ledger.head_action_id,
        )
        existing_by_id = {a["action_id"]: a for a in ledger.actions}
        if action["action_id"] in existing_by_id:
            _fail("upgrade action_id collision")

        history_before = (
            ledger.as_history() if ledger.action_count else empty_operator_history()
        )
        history_after = advance_operator_history(history_before, action)
        FrozenOperatorLedger(
            head_action_id=history_after["head_action_id"],
            operator_history_hash=history_after["operator_history_hash"],
            action_count=history_after["action_count"],
            actions=ledger.actions + (action,),
        )

        updated_current = dict(current)
        updated_current["engine_commit_sha"] = upgrade["target_engine_commit_sha"]
        if request_type == "POLICY_UPGRADE":
            updated_current["behavior_policy_version"] = upgrade[
                "target_behavior_policy_version"
            ]
        else:
            if (
                updated_current["behavior_policy_version"]
                != upgrade["current_behavior_policy_version"]
            ):
                _fail("ENGINE_UPGRADE must not change behavior_policy_version")

        if updated_current["processed_through"] != processed:
            _fail("upgrade must not change processed_through")
        if updated_current["history"] != current["history"]:
            _fail("upgrade must not mutate CurrentState.history")
        if updated_current["pending_queue"] != current["pending_queue"]:
            _fail("upgrade must not mutate pending_queue")
        if updated_current["domain_refs"] != current["domain_refs"]:
            _fail("upgrade must not mutate domain_refs")
        if updated_current["character_id"] != current["character_id"]:
            _fail("upgrade must not change character_id")

        bumped_current = bump_revision(updated_current)
        if int(bumped_current["state_revision"]) != rev_before + 1:
            _fail("state_revision must bump exactly once on upgrade CHANGE")
        bumped_current = validate_checkpoint(bumped_current)
        final_bundle = validate_runtime_bundle(
            RuntimeBundle(
                current_state=bumped_current,
                schedule_state=baseline_snapshot.bundle.schedule_state,
                relation_state=baseline_snapshot.bundle.relation_state,
                home_state=baseline_snapshot.bundle.home_state,
                consumables_state=baseline_snapshot.bundle.consumables_state,
                wardrobe_state=baseline_snapshot.bundle.wardrobe_state,
                finance_state=baseline_snapshot.bundle.finance_state,
            ),
            reference_sets=reference_sets,
        )

        _copy_tree(base, cand)
        write_runtime_json(cand / operator_action_relpath(action["action_id"]), action)
        write_runtime_json(cand / OPERATOR_HISTORY_RELPATH, history_after)
        write_runtime_json(cand / "state/current.json", final_bundle.current_state)

        # Raw timeline / camera / domain / last-run bytes must remain identical.
        after_fp = snapshot_tree_bytes(cand)
        for rel, raw in baseline_fp.items():
            if rel in {
                "state/current.json",
                OPERATOR_HISTORY_RELPATH,
            } or rel.startswith("operator/actions/"):
                continue
            if after_fp.get(rel) != raw:
                _fail(f"upgrade mutated non-allowed path bytes: {rel}")
        for rel in after_fp:
            if rel not in baseline_fp and not (
                rel.startswith("operator/actions/")
                or rel == OPERATOR_HISTORY_RELPATH
                or rel == "state/current.json"
            ):
                _fail(f"upgrade introduced unexpected path: {rel}")

        candidate_ledger = load_operator_ledger(cand)
        if candidate_ledger.action_count != history_after["action_count"]:
            _fail("candidate operator action_count mismatch after staging")
        if candidate_ledger.action_count != ledger.action_count + 1:
            _fail("operator action_count must advance exactly +1")
        candidate_bundle = load_runtime_bundle(cand, reference_sets=reference_sets)
        if int(candidate_bundle.current_state["state_revision"]) != rev_before + 1:
            _fail("candidate state_revision mismatch after staging")
        if (
            candidate_bundle.current_state["engine_commit_sha"]
            != upgrade["target_engine_commit_sha"]
        ):
            _fail("candidate engine_commit_sha mismatch")
        if request_type == "ENGINE_UPGRADE":
            if (
                candidate_bundle.current_state["behavior_policy_version"]
                != current["behavior_policy_version"]
            ):
                _fail("ENGINE_UPGRADE changed behavior_policy_version")
        else:
            if (
                candidate_bundle.current_state["behavior_policy_version"]
                != upgrade["target_behavior_policy_version"]
            ):
                _fail("POLICY_UPGRADE candidate policy version mismatch")

        after_baseline_fp = snapshot_tree_bytes(base)
        if after_baseline_fp != baseline_fp:
            _fail("baseline mutated during upgrade candidate staging")

        files_changed = _diff_relpaths(base, cand)
        allowed = {
            "state/current.json",
            OPERATOR_HISTORY_RELPATH,
            operator_action_relpath(action["action_id"]),
        }
        if set(files_changed) != allowed:
            _fail(
                "upgrade candidate changed unexpected paths: "
                f"{sorted(set(files_changed) - allowed)} missing={sorted(allowed - set(files_changed))}"
            )

        return OperatorUpgradeCandidateResult(
            status="CHANGE",
            baseline_root=base,
            candidate_root=cand,
            life_base_sha=life_base_sha,
            action=normalize_operator_action(action),
            operator_history=history_after,
            state_revision_before=rev_before,
            state_revision_after=rev_before + 1,
            files_changed=files_changed,
            baseline_fingerprint=baseline_fp,
            current_policy_hash=current_identity.policy_hash,
            target_policy_hash=target_identity.policy_hash,
            engine_commit_sha_before=current["engine_commit_sha"],
            engine_commit_sha_after=upgrade["target_engine_commit_sha"],
            behavior_policy_version_before=current["behavior_policy_version"],
            behavior_policy_version_after=candidate_bundle.current_state[
                "behavior_policy_version"
            ],
            compatibility=compatibility,
        )
    finally:
        if owned_target is not None:
            try:
                _cleanup_adapter_checkout(
                    upgrade_adapter=upgrade_adapter,
                    engine_source_repo=engine_source_repo,
                    checkout=owned_target,
                )
            except Exception:
                pass
        if probe_tmp is not None and probe_tmp.exists():
            shutil.rmtree(probe_tmp, ignore_errors=True)
