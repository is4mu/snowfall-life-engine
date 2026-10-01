"""Slice 5E1 — local staged Correction candidate (no remote push/workflow)."""

from __future__ import annotations

import shutil
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .canonical import normalize_persisted_object
from .checkpoint import bump_revision, validate_checkpoint
from .correction import (
    apply_compensation_effects,
    apply_correction_overlays,
    validate_correction_against_raw_history,
)
from .errors import ErrorCode, LifeEngineError
from .history import collect_ledger_events, verify_history
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
from .operator_request import normalize_operator_request
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, load_runtime_bundle, validate_runtime_bundle
from .runtime_persistence import (
    load_runtime_persistent_snapshot,
    snapshot_tree_bytes,
    write_runtime_json,
)
from .timeutil import require_canonical_timestamp


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


@dataclass(frozen=True)
class OperatorCorrectionCandidateResult:
    status: str  # NOOP | CHANGE
    baseline_root: Path
    candidate_root: Path
    life_base_sha: str
    action: Mapping[str, Any] | None
    operator_history: Mapping[str, Any]
    state_revision_before: int
    state_revision_after: int
    effective_events: tuple[dict[str, Any], ...]
    files_changed: tuple[str, ...]
    baseline_fingerprint: Mapping[str, bytes]


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


def build_operator_correction_candidate(
    *,
    baseline_root: Path | str,
    candidate_root: Path | str,
    request: Mapping[str, Any],
    life_base_sha: str,
    reference_sets: RuntimeReferenceSets,
) -> OperatorCorrectionCandidateResult:
    """Stage one local Correction candidate: action + compensation + history + revision.

    Pure/local only. Does not push, create Life refs, or write baseline.
    Raw ActualEvent/timeline bytes and CurrentState.history identity stay unchanged.
    Exact retry against the same base is idempotent (NOOP when already applied).
    """
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

    normalized_request = normalize_operator_request(request)
    if normalized_request["expected_life_head"] != life_base_sha:
        _fail("request.expected_life_head must equal life_base_sha (stale Life base)")
    if normalized_request["effective_at"] != processed:
        _fail("correction effective_at must equal CurrentState.processed_through")

    raw_events = collect_ledger_events(baseline_snapshot.timeline_days.values())
    verify_history(
        raw_events,
        head_event_id=current["history"]["head_event_id"],
        history_hash=current["history"]["history_hash"],
        event_count=current["history"]["event_count"],
    )

    from .operator_request import derive_request_id

    request_id = derive_request_id(normalized_request)
    # Idempotence before supersession: exact request already on this Life base.
    for prior in ledger.actions:
        if (
            prior["request_id"] == request_id
            and prior["life_base_sha"] == life_base_sha
        ):
            prior_n = normalize_operator_action(prior)
            _copy_tree(base, cand)
            after_fp = snapshot_tree_bytes(base)
            if after_fp != baseline_fp:
                _fail("baseline mutated during idempotent correction NOOP")
            return OperatorCorrectionCandidateResult(
                status="NOOP",
                baseline_root=base,
                candidate_root=cand,
                life_base_sha=life_base_sha,
                action=prior_n,
                operator_history=ledger.as_history(),
                state_revision_before=rev_before,
                state_revision_after=rev_before,
                effective_events=apply_correction_overlays(raw_events, ledger),
                files_changed=(),
                baseline_fingerprint=baseline_fp,
            )

    # Validate correction payload against raw history + supersession.
    validate_correction_against_raw_history(
        raw_events=raw_events,
        correction=normalized_request["correction"],
        ledger=ledger,
    )

    action = build_operator_action_from_request(
        normalized_request,
        life_base_sha=life_base_sha,
        previous_action_id=ledger.head_action_id,
    )

    # Exact action_id already present (same previous-head binding).
    existing_by_id = {a["action_id"]: a for a in ledger.actions}
    if action["action_id"] in existing_by_id:
        prior = normalize_operator_action(existing_by_id[action["action_id"]])
        if prior != action:
            _fail("action_id collision with different action payload")
        _copy_tree(base, cand)
        after_fp = snapshot_tree_bytes(base)
        if after_fp != baseline_fp:
            _fail("baseline mutated during idempotent correction NOOP")
        return OperatorCorrectionCandidateResult(
            status="NOOP",
            baseline_root=base,
            candidate_root=cand,
            life_base_sha=life_base_sha,
            action=action,
            operator_history=ledger.as_history(),
            state_revision_before=rev_before,
            state_revision_after=rev_before,
            effective_events=apply_correction_overlays(raw_events, ledger),
            files_changed=(),
            baseline_fingerprint=baseline_fp,
        )

    # Different Life base / previous head ⇒ different action_id (no silent retarget).
    history_before = (
        ledger.as_history()
        if ledger.action_count
        else empty_operator_history()
    )
    history_after = advance_operator_history(history_before, action)
    next_ledger = FrozenOperatorLedger(
        head_action_id=history_after["head_action_id"],
        operator_history_hash=history_after["operator_history_hash"],
        action_count=history_after["action_count"],
        actions=ledger.actions + (action,),
    )

    compensation = list(
        normalized_request["correction"].get("compensation_effects") or []
    )
    updated_bundle = apply_compensation_effects(
        bundle=baseline_snapshot.bundle,
        compensation_effects=compensation,
        effective_at=normalized_request["effective_at"],
        action_id=action["action_id"],
        reference_sets=reference_sets,
    )

    # Exactly one state_revision bump on CHANGE.
    bumped_current = bump_revision(dict(updated_bundle.current_state))
    if int(bumped_current["state_revision"]) != rev_before + 1:
        _fail("state_revision must bump exactly once on correction CHANGE")
    # Raw history identity must remain byte-identical.
    if bumped_current["history"] != current["history"]:
        _fail("correction must not mutate CurrentState.history")
    bumped_current = validate_checkpoint(bumped_current)
    final_bundle = validate_runtime_bundle(
        RuntimeBundle(
            current_state=bumped_current,
            schedule_state=updated_bundle.schedule_state,
            relation_state=updated_bundle.relation_state,
            home_state=updated_bundle.home_state,
            consumables_state=updated_bundle.consumables_state,
            wardrobe_state=updated_bundle.wardrobe_state,
            finance_state=updated_bundle.finance_state,
        ),
        reference_sets=reference_sets,
    )

    _copy_tree(base, cand)

    # Preserve raw timeline bytes exactly (copy already did); refuse rewrite.
    for date_str, day in baseline_snapshot.timeline_days.items():
        dest = cand / "timeline" / f"{date_str}.json"
        if not dest.is_file():
            _fail(f"candidate missing timeline day: {date_str}")
        # Compare semantic content equality with baseline bytes via fingerprint later.

    write_runtime_json(cand / operator_action_relpath(action["action_id"]), action)
    write_runtime_json(cand / OPERATOR_HISTORY_RELPATH, history_after)

    write_runtime_json(cand / "state/current.json", final_bundle.current_state)
    write_runtime_json(cand / "schedule/state.json", final_bundle.schedule_state)
    write_runtime_json(cand / "relations/state.json", final_bundle.relation_state)
    write_runtime_json(cand / "home/state.json", final_bundle.home_state)
    write_runtime_json(cand / "consumables/state.json", final_bundle.consumables_state)
    write_runtime_json(cand / "wardrobe/state.json", final_bundle.wardrobe_state)
    write_runtime_json(cand / "finance/state.json", final_bundle.finance_state)

    # Verify candidate without requiring simulation last-run (operator manifest is 5E2).
    candidate_ledger = load_operator_ledger(cand)
    if candidate_ledger.operator_history_hash != history_after["operator_history_hash"]:
        _fail("candidate operator history hash mismatch after staging")
    if candidate_ledger.action_count != history_after["action_count"]:
        _fail("candidate operator action_count mismatch after staging")
    candidate_bundle = load_runtime_bundle(cand, reference_sets=reference_sets)
    if int(candidate_bundle.current_state["state_revision"]) != rev_before + 1:
        _fail("candidate state_revision mismatch after staging")
    if (
        candidate_bundle.current_state["history"]
        != baseline_snapshot.bundle.current_state["history"]
    ):
        _fail("candidate raw history identity changed")

    # Raw timeline file bytes must match baseline exactly.
    for rel, raw in baseline_fp.items():
        if rel.startswith("timeline/"):
            if candidate_fp_get := snapshot_tree_bytes(cand).get(rel):
                if candidate_fp_get != raw:
                    _fail(f"raw timeline bytes mutated: {rel}")
            else:
                _fail(f"raw timeline file missing in candidate: {rel}")

    after_baseline_fp = snapshot_tree_bytes(base)
    if after_baseline_fp != baseline_fp:
        _fail("baseline mutated during correction candidate staging")

    files_changed = _diff_relpaths(base, cand)
    effective = apply_correction_overlays(raw_events, next_ledger)

    return OperatorCorrectionCandidateResult(
        status="CHANGE",
        baseline_root=base,
        candidate_root=cand,
        life_base_sha=life_base_sha,
        action=action,
        operator_history=history_after,
        state_revision_before=rev_before,
        state_revision_after=rev_before + 1,
        effective_events=effective,
        files_changed=files_changed,
        baseline_fingerprint=baseline_fp,
    )
