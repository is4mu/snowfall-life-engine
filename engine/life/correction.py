"""Slice 5E1 — Correction v1 validation, supersession, and effective overlay."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_hash, canonical_json
from .errors import ErrorCode, LifeEngineError
from .events import normalize_actual_event_for_persistence
from .operator_action import FrozenOperatorLedger, normalize_operator_action
from .operator_request import normalize_correction_payload, normalize_operator_request
from .runtime_effects import apply_finalized_event_effects
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets
from .timeutil import require_canonical_timestamp

# Correction v1 may change only these factual projection fields.
ALLOWED_CORRECTION_FIELDS = frozenset(
    {
        "location_id",
        "summary",
        "effects",
        "captures",
        "participants",
    }
)


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def raw_event_canonical_hash(event: Mapping[str, Any]) -> str:
    """Canonical hash of an immutable raw ActualEvent (target binding)."""
    return canonical_hash(normalize_actual_event_for_persistence(event))


def _field_equal(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def validate_corrected_event_against_raw(
    *,
    raw_event: Mapping[str, Any],
    corrected_event: Mapping[str, Any],
) -> dict[str, Any]:
    """Accept only allowed factual field diffs; preserve structural/decision identity."""
    raw = normalize_actual_event_for_persistence(raw_event)
    corrected = normalize_actual_event_for_persistence(corrected_event)

    all_keys = set(raw) | set(corrected)
    for key in sorted(all_keys):
        if key in ALLOWED_CORRECTION_FIELDS:
            continue
        if key not in raw:
            _fail(f"corrected_event introduces forbidden field: {key}")
        if key not in corrected:
            _fail(f"corrected_event removes preserved field: {key}")
        if not _field_equal(raw[key], corrected[key]):
            _fail(f"corrected_event changes preserved field: {key}")

    return corrected


def effective_correction_by_target(
    ledger: FrozenOperatorLedger,
) -> dict[str, dict[str, Any]]:
    """Map target_event_id -> currently effective CORRECTION action (linear supersession)."""
    effective: dict[str, dict[str, Any]] = {}
    for action in ledger.actions:
        if action["action_type"] != "CORRECTION":
            continue
        corr = action["correction"]
        target = corr["target_event_id"]
        supersedes = corr["supersedes_action_id"]
        current = effective.get(target)
        if current is None:
            if supersedes is not None:
                _fail(
                    f"first correction for {target} requires supersedes_action_id=null"
                )
        else:
            if supersedes != current["action_id"]:
                _fail(
                    f"stale/forked supersession for {target}: "
                    f"got {supersedes!r} expected {current['action_id']!r}"
                )
        effective[target] = action
    return effective


def validate_correction_supersession(
    *,
    ledger: FrozenOperatorLedger,
    correction: Mapping[str, Any],
) -> None:
    """Fail-closed linear supersession check for a pending correction."""
    corr = normalize_correction_payload(correction)
    effective = effective_correction_by_target(ledger)
    target = corr["target_event_id"]
    current = effective.get(target)
    supersedes = corr["supersedes_action_id"]
    if current is None:
        if supersedes is not None:
            _fail(
                f"first correction for {target} requires supersedes_action_id=null"
            )
    else:
        if supersedes != current["action_id"]:
            _fail(
                f"stale/forked supersession for {target}: "
                f"got {supersedes!r} expected {current['action_id']!r}"
            )


def validate_correction_against_raw_history(
    *,
    raw_events: Sequence[Mapping[str, Any]],
    correction: Mapping[str, Any],
    ledger: FrozenOperatorLedger,
) -> dict[str, Any]:
    """Validate Correction v1 target binding + preserved fields + supersession."""
    corr = normalize_correction_payload(correction)
    by_id = {
        normalize_actual_event_for_persistence(e)["event_id"]: normalize_actual_event_for_persistence(e)
        for e in raw_events
    }
    target_id = corr["target_event_id"]
    if target_id not in by_id:
        _fail(f"correction target event missing: {target_id}")
    raw = by_id[target_id]
    expected_hash = raw_event_canonical_hash(raw)
    if corr["target_event_hash"] != expected_hash:
        _fail("correction target_event_hash mismatch vs raw ActualEvent")
    validate_corrected_event_against_raw(
        raw_event=raw,
        corrected_event=corr["corrected_event"],
    )
    validate_correction_supersession(ledger=ledger, correction=corr)
    return corr


def apply_correction_overlays(
    raw_events: Sequence[Mapping[str, Any]],
    ledger: FrozenOperatorLedger | None,
) -> tuple[dict[str, Any], ...]:
    """Derive effective finalized history from raw events + operator corrections.

    Event count/order/IDs/type/times remain identical to raw history for Correction v1.
    Missing/empty ledger returns a deep copy of raw events (behavior-identical).
    """
    base = [normalize_actual_event_for_persistence(e) for e in raw_events]
    if ledger is None or ledger.action_count == 0:
        return tuple(deepcopy(e) for e in base)

    # Re-validate every stored correction against raw immutable history.
    # This makes persistent-tree verification fail closed even if a repository
    # was manually/corruptly rewritten outside the approved candidate builder.
    # Non-CORRECTION operator actions (e.g. RECOVERY_AUTHORIZATION) are skipped.
    by_id = {event["event_id"]: event for event in base}
    for action in ledger.actions:
        if action["action_type"] != "CORRECTION":
            continue
        corr = action["correction"]
        target_id = corr["target_event_id"]
        raw = by_id.get(target_id)
        if raw is None:
            _fail(f"stored correction target event missing: {target_id}")
        if corr["target_event_hash"] != raw_event_canonical_hash(raw):
            _fail(f"stored correction target_event_hash mismatch for {target_id}")
        validate_corrected_event_against_raw(
            raw_event=raw,
            corrected_event=corr["corrected_event"],
        )

    # Re-validate linear supersession while selecting the currently effective view.
    effective = effective_correction_by_target(ledger)
    out: list[dict[str, Any]] = []
    for event in base:
        action = effective.get(event["event_id"])
        if action is None:
            out.append(deepcopy(event))
            continue
        corrected = validate_corrected_event_against_raw(
            raw_event=event,
            corrected_event=action["correction"]["corrected_event"],
        )
        out.append(deepcopy(corrected))
    return tuple(out)


def validate_compensation_effects(
    *,
    effects: Sequence[Mapping[str, Any]],
    effective_at: str,
) -> list[dict[str, Any]]:
    """Require timestamp-bearing compensation effects to use exact effective_at."""
    from .effects import normalize_effect

    effective_at_c = require_canonical_timestamp(effective_at, field="effective_at")
    timestamp_fields = {
        "RELATION_TOUCH": "contact_at",
        "HOME_TRANSITION": "transition_at",
        "WARDROBE_TRANSITION": "transition_at",
        "FINANCE_TRANSACTION": "transaction_at",
        "TASK_PROGRESS": "progress_at",
        "COMMITMENT_COMPLETE": "completed_at",
    }
    out: list[dict[str, Any]] = []
    for raw in effects:
        effect = normalize_effect(raw)
        effect_type = effect["effect_type"]
        # Only known TypedEffect types are accepted (schema already closed).
        # Unsupported ad-hoc types fail at normalize_effect / schema.
        ts_field = timestamp_fields.get(effect_type)
        if ts_field is not None:
            ts = effect["payload"].get(ts_field)
            if ts != effective_at_c:
                _fail(
                    f"compensation {effect_type}.{ts_field} must equal effective_at"
                )
        out.append(effect)
    return out


def apply_compensation_effects(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    compensation_effects: Sequence[Mapping[str, Any]],
    effective_at: str,
    action_id: str,
    reference_sets: RuntimeReferenceSets,
) -> RuntimeBundle:
    """Apply compensation once via existing TypedEffect reducer dispatcher.

    Builds a synthetic point-in-time ActualEvent so ``apply_finalized_event_effects``
    owns reducer routing. Does not append history or bump state_revision.
    """
    effects = validate_compensation_effects(
        effects=compensation_effects,
        effective_at=effective_at,
    )
    if not effects:
        if isinstance(bundle, RuntimeBundle):
            return bundle
        from .runtime_bundle import validate_runtime_bundle

        return validate_runtime_bundle(bundle, reference_sets=reference_sets)

    effective_at_c = require_canonical_timestamp(effective_at, field="effective_at")
    # Synthetic carrier: interval collapses to the correction effective instant.
    # event_id is namespaced to the operator action and must not enter history.
    synthetic = normalize_actual_event_for_persistence(
        {
            "schema_version": 1,
            "event_id": f"operator-compensation:{action_id}",
            "activity_instance_id": f"operator-compensation-activity:{action_id}",
            "event_type": "OPERATOR_COMPENSATION",
            "actual_start": effective_at_c,
            "actual_end": effective_at_c,
            "finalized_at": effective_at_c,
            "summary": "",
            "effects": list(effects),
            "captures": [],
            "provenance": {"origin": "OPERATOR_CORRECTION"},
        }
    )
    return apply_finalized_event_effects(
        bundle=bundle,
        actual_event=synthetic,
        reference_sets=reference_sets,
    )


def build_corrected_event_view(
    *,
    raw_event: Mapping[str, Any],
    location_id: str | None = None,
    summary: str | None = None,
    effects: Sequence[Mapping[str, Any]] | None = None,
    captures: Sequence[Mapping[str, Any]] | None = None,
    participants: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Helper for tests/fixtures: clone raw event with allowed factual overrides."""
    out = deepcopy(normalize_actual_event_for_persistence(raw_event))
    if location_id is not None:
        out["location_id"] = location_id
    if summary is not None:
        out["summary"] = summary
    if effects is not None:
        out["effects"] = list(effects)
    if captures is not None:
        out["captures"] = list(captures)
    if participants is not None:
        out["participants"] = list(participants)
    return normalize_actual_event_for_persistence(out)
