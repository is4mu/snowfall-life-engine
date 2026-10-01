"""Slice 5E2 — one-window RECOVERY_AUTHORIZATION atomic candidate."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .clock import CATCHUP_LIMIT_SECONDS, check_catchup_bounds
from .errors import ErrorCode, LifeEngineError
from .last_operator_action import (
    LAST_OPERATOR_ACTION_RELPATH,
    build_last_operator_action,
)
from .operator_action import (
    build_operator_action_from_request,
    load_operator_ledger,
    operator_action_relpath,
)
from .operator_history import (
    OPERATOR_HISTORY_RELPATH,
    advance_operator_history,
    empty_operator_history,
)
from .operator_request import normalize_operator_request
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets
from .runtime_fact_provider import parse_runtime_target_request
from .runtime_orchestrator import (
    RuntimeFactProvider,
    RuntimeTargetAdvanceResult,
    RuntimeTargetInputs,
    RuntimeTargetRequest,
    advance_runtime_to_target,
    advance_runtime_to_target_with_provider,
    parse_runtime_target_inputs,
)
from .runtime_persistence import (
    LAST_RUN_RELPATH,
    RuntimePersistenceResult,
    RuntimePersistentSnapshot,
    _build_runtime_candidate_tree_core,
    write_runtime_json,
)
from .timeutil import parse_rfc3339, require_canonical_timestamp


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


@dataclass(frozen=True)
class OperatorRecoveryCandidateResult:
    status: str  # CHANGE only on success
    persistence: RuntimePersistenceResult
    action: Mapping[str, Any]
    operator_history: Mapping[str, Any]
    last_operator_action: Mapping[str, Any]
    recover_through: str
    from_processed_through: str


def validate_recovery_window_against_state(
    *,
    request: Mapping[str, Any],
    current_state: Mapping[str, Any],
    life_base_sha: str,
) -> dict[str, Any]:
    """Fail-closed one-window recovery validation (does not weaken catch-up)."""
    normalized = normalize_operator_request(request)
    if normalized["request_type"] != "RECOVERY_AUTHORIZATION":
        _fail("request_type must be RECOVERY_AUTHORIZATION")
    if normalized["expected_life_head"] != life_base_sha:
        _fail("request.expected_life_head must equal life_base_sha (stale Life base)")

    recovery = normalized["recovery"]
    from_ts = recovery["from_processed_through"]
    through_ts = recovery["recover_through"]
    processed = require_canonical_timestamp(
        current_state["processed_through"], field="processed_through"
    )
    if from_ts != processed:
        _fail(
            "recovery.from_processed_through must equal CurrentState.processed_through"
        )
    if through_ts == from_ts:
        _fail("recover_through must be strictly greater than from_processed_through")
    if through_ts < from_ts:
        _fail("recover_through time reversal rejected", code=ErrorCode.TIME_REVERSAL)

    from_dt = parse_rfc3339(from_ts, field="from_processed_through")
    through_dt = parse_rfc3339(through_ts, field="recover_through")
    epoch = parse_rfc3339(
        require_canonical_timestamp(
            current_state["life_epoch"], field="life_epoch"
        ),
        field="life_epoch",
    )
    # Reuse the exact existing clock catch-up guard; never bypass or widen it.
    noop = check_catchup_bounds(from_dt, through_dt, epoch)
    if noop == "NOOP":
        _fail("recover_through must advance processed_through")
    gap = int((through_dt - from_dt).total_seconds())
    if gap > CATCHUP_LIMIT_SECONDS:
        # Defensive: check_catchup_bounds already raises; keep explicit contract.
        raise LifeEngineError(
            ErrorCode.CATCHUP_LIMIT_EXCEEDED, f"gap_seconds={gap}"
        )
    return normalized


def build_operator_recovery_candidate_tree(
    *,
    baseline_root: Path | str,
    candidate_root: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    request: Mapping[str, Any],
    fact_provider: RuntimeFactProvider | None = None,
    fact_provider_factory: (
        Callable[[RuntimePersistentSnapshot], RuntimeFactProvider] | None
    ) = None,
    target_request: RuntimeTargetRequest | Mapping[str, Any] | None = None,
    target_inputs: RuntimeTargetInputs | Mapping[str, Any] | None = None,
    executing_engine_commit_sha: str,
    life_base_sha: str,
) -> OperatorRecoveryCandidateResult:
    """Build one atomic recovery+runtime CHANGE candidate.

    Stages the RECOVERY_AUTHORIZATION action before last-run / last-operator-action
    observability is sealed so the final semantic hash binds ordinary runtime
    changes + operator ledger. ``state_revision`` advances exactly once.

    Exactly one C3 input mode is required: provider, provider factory, or the
    ordinary precomputed ``target_inputs`` tape (same engine/policy path).
    """
    modes = sum(
        1
        for x in (fact_provider, fact_provider_factory, target_inputs)
        if x is not None
    )
    if modes != 1:
        _fail(
            "exactly one of fact_provider, fact_provider_factory, "
            "or target_inputs is required"
        )

    # Pre-validate against a provisional load so stale from/head fail before C3.
    # The core reloads/verifies the same baseline again under its own isolation.
    provisional = Path(baseline_root)
    # Window validation needs current_state; load via persistence snapshot API.
    from .runtime_persistence import load_runtime_persistent_snapshot

    baseline_probe = load_runtime_persistent_snapshot(
        provisional, reference_sets=reference_sets
    )
    normalized = validate_recovery_window_against_state(
        request=request,
        current_state=baseline_probe.bundle.current_state,
        life_base_sha=life_base_sha,
    )
    recover_through = normalized["recovery"]["recover_through"]
    from_processed = normalized["recovery"]["from_processed_through"]

    if target_request is not None:
        parsed = parse_runtime_target_request(target_request)
        if parsed.target_time != recover_through:
            _fail("recovery target_request.target_time must equal recover_through")
    if target_inputs is not None:
        parsed_inputs = parse_runtime_target_inputs(target_inputs)
        if parsed_inputs.target_time != recover_through:
            _fail("recovery target_inputs.target_time must equal recover_through")

    ledger_before = load_operator_ledger(provisional)
    history_before = (
        ledger_before.as_history()
        if ledger_before.action_count
        else empty_operator_history()
    )
    action_holder: dict[str, Any] = {}
    history_after_holder: dict[str, Any] = {}
    last_op_holder: dict[str, Any] = {}

    def _run_c3(
        snapshot: RuntimePersistentSnapshot,
        policy: Mapping[str, Any],
    ) -> tuple[str, RuntimeTargetAdvanceResult]:
        # Re-validate against the frozen baseline snapshot authority.
        validate_recovery_window_against_state(
            request=normalized,
            current_state=snapshot.bundle.current_state,
            life_base_sha=life_base_sha,
        )
        if target_inputs is not None:
            parsed_inputs = parse_runtime_target_inputs(target_inputs)
            if parsed_inputs.target_time != recover_through:
                _fail("recovery runtime target_time must equal recover_through")
            result = advance_runtime_to_target(
                bundle=snapshot.bundle,
                reference_sets=reference_sets,
                behavior_policy=policy,
                social_response_state=snapshot.social_response_state,
                target_inputs=parsed_inputs,
            )
            return recover_through, result

        if fact_provider_factory is not None:
            provider = fact_provider_factory(snapshot)
        else:
            assert fact_provider is not None
            provider = fact_provider
        if target_request is None:
            _fail("provider recovery requires target_request")
        req = parse_runtime_target_request(target_request)
        if req.target_time != recover_through:
            _fail("recovery runtime target_time must equal recover_through")
        result = advance_runtime_to_target_with_provider(
            bundle=snapshot.bundle,
            reference_sets=reference_sets,
            behavior_policy=policy,
            social_response_state=snapshot.social_response_state,
            target_request=req,
            fact_provider=provider,
        )
        return recover_through, result

    def _stage_operator(
        candidate_path: Path,
        baseline_snapshot: RuntimePersistentSnapshot,
        staged_bundle: RuntimeBundle,
        runtime_result: RuntimeTargetAdvanceResult,
    ) -> None:
        processed_after = require_canonical_timestamp(
            runtime_result.bundle.current_state["processed_through"],
            field="processed_through",
        )
        if processed_after != recover_through:
            _fail(
                "recovery runtime advance must reach exactly recover_through "
                f"(got {processed_after})"
            )
        # Engine / policy identity must remain unchanged across recovery.
        if (
            runtime_result.bundle.current_state["engine_commit_sha"]
            != baseline_snapshot.bundle.current_state["engine_commit_sha"]
        ):
            _fail("recovery must not change engine_commit_sha")
        if (
            runtime_result.bundle.current_state["behavior_policy_version"]
            != baseline_snapshot.bundle.current_state["behavior_policy_version"]
        ):
            _fail("recovery must not change behavior_policy_version")

        ledger = load_operator_ledger(baseline_snapshot.root)
        action = build_operator_action_from_request(
            normalized,
            life_base_sha=life_base_sha,
            previous_action_id=ledger.head_action_id,
        )
        # Exact same request already applied against this Life base ⇒ stale/replay.
        for prior in ledger.actions:
            if (
                prior["request_id"] == action["request_id"]
                and prior["life_base_sha"] == life_base_sha
            ):
                _fail(
                    "recovery request already applied on this Life base "
                    "(no silent replay)"
                )
        if action["action_id"] in {a["action_id"] for a in ledger.actions}:
            _fail("recovery action_id already present (no silent replay)")

        hist_before = (
            ledger.as_history() if ledger.action_count else empty_operator_history()
        )
        hist_after = advance_operator_history(hist_before, action)
        write_runtime_json(
            candidate_path / operator_action_relpath(action["action_id"]), action
        )
        write_runtime_json(candidate_path / OPERATOR_HISTORY_RELPATH, hist_after)
        action_holder.clear()
        action_holder.update(action)
        history_after_holder.clear()
        history_after_holder.update(hist_after)

    def _write_last_operator_action(
        candidate_path: Path,
        baseline_snapshot: RuntimePersistentSnapshot,
        runtime_result: RuntimeTargetAdvanceResult,
        state_before_hash: str,
        state_after_hash: str,
        rev_before: int,
        rev_after: int,
        provisional: list[str],
    ) -> list[str]:
        if not action_holder:
            _fail("recovery operator action missing before observability seal")
        files = list(provisional)
        if LAST_OPERATOR_ACTION_RELPATH not in files:
            files.append(LAST_OPERATOR_ACTION_RELPATH)
        files = sorted(set(files))
        hist_before = (
            history_before
            if history_before["action_count"]
            else empty_operator_history()
        )
        # Prefer the frozen baseline ledger hash from the outer probe when empty.
        op_before = hist_before["operator_history_hash"]
        if baseline_snapshot.operator_history is not None:
            op_before = baseline_snapshot.operator_history["operator_history_hash"]
        last_op = build_last_operator_action(
            action=action_holder,
            life_base_sha=life_base_sha,
            engine_commit_sha=baseline_snapshot.bundle.current_state[
                "engine_commit_sha"
            ],
            behavior_policy_version=baseline_snapshot.bundle.current_state[
                "behavior_policy_version"
            ],
            state_revision_before=rev_before,
            state_revision_after=rev_after,
            state_before_hash=state_before_hash,
            state_after_hash=state_after_hash,
            operator_history_hash_before=op_before,
            operator_history_hash_after=history_after_holder["operator_history_hash"],
            processed_through_before=from_processed,
            processed_through_after=recover_through,
            files_changed=files,
        )
        write_runtime_json(candidate_path / LAST_OPERATOR_ACTION_RELPATH, last_op)
        last_op_holder.clear()
        last_op_holder.update(last_op)
        return files

    persistence = _build_runtime_candidate_tree_core(
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        executing_engine_commit_sha=executing_engine_commit_sha,
        life_base_sha=life_base_sha,
        runtime_advance=_run_c3,
        require_change=True,
        pre_revision_semantic_augment=_stage_operator,
        post_hash_observability_augment=_write_last_operator_action,
    )
    if persistence.status != "CHANGE":
        _fail("recovery must produce CHANGE")
    if not action_holder or not last_op_holder:
        _fail("recovery missing action or last-operator-action after CHANGE")
    if persistence.last_run is None:
        _fail("recovery CHANGE requires last-run")
    if LAST_OPERATOR_ACTION_RELPATH not in persistence.files_changed:
        _fail("recovery files_changed must include last-operator-action")
    if LAST_RUN_RELPATH not in persistence.files_changed:
        _fail("recovery files_changed must include last-run")
    action_rel = operator_action_relpath(action_holder["action_id"])
    if action_rel not in persistence.files_changed:
        _fail("recovery files_changed must include operator action file")
    if OPERATOR_HISTORY_RELPATH not in persistence.files_changed:
        _fail("recovery files_changed must include operator-history")

    return OperatorRecoveryCandidateResult(
        status="CHANGE",
        persistence=persistence,
        action=dict(action_holder),
        operator_history=dict(history_after_holder),
        last_operator_action=dict(last_op_holder),
        recover_through=recover_through,
        from_processed_through=from_processed,
    )
