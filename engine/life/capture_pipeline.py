"""Slice 4B2 — pure TAKE -> pending Capture bridge.

Only approved 4B2 path from TAKE_CAPTURE to ActiveActivity.pending_captures.

Does **not**:
- invent opportunities
- accept SKIP
- project Camera Roll
- write runtime / life branch / files
- wire clock / wakeups
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .canonical import canonical_hash
from .capture_decisions import (
    RESULT_SKIP,
    RESULT_TAKE,
    CaptureDecisionResolution,
    resolve_capture_opportunity,
    validate_capture_opportunity,
)
from .captures import (
    attach_capture_to_active_activity,
    build_capture_decision_evidence,
    build_capture_fact,
)
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .rng import SeedLike


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def _resolution_semantic_dict(resolution: CaptureDecisionResolution) -> dict[str, Any]:
    """Stable comparable view of a resolution (fresh lists; no shared mutables)."""
    return resolution.as_dict()


def apply_capture_take(
    *,
    world_seed: SeedLike,
    policy: Mapping[str, Any],
    active_activity: Mapping[str, Any],
    opportunity: Mapping[str, Any],
    resolution: CaptureDecisionResolution | Mapping[str, Any],
) -> dict[str, Any]:
    """Verify TAKE resolution and attach exactly one pending Capture. Pure.

    1. Deep-copy / no input mutation
    2. Recompute expected 4B1 resolution
    3. Supplied resolution must be semantically equivalent
    4. Expected result must be TAKE_CAPTURE (SKIP rejected)
    5. Build Capture + capture_decision_evidence from opportunity + verified resolution
    6. Attach via existing idempotent helper
    """
    if world_seed is None or world_seed == "":
        _fail("world_seed required")

    policy_in = deepcopy(dict(policy)) if isinstance(policy, Mapping) else policy
    activity_in = (
        deepcopy(dict(active_activity))
        if isinstance(active_activity, Mapping)
        else active_activity
    )
    opportunity_in = (
        deepcopy(dict(opportunity)) if isinstance(opportunity, Mapping) else opportunity
    )

    # Snapshot supplied resolution without mutating caller object.
    if isinstance(resolution, CaptureDecisionResolution):
        supplied_dict = _resolution_semantic_dict(resolution)
    elif isinstance(resolution, Mapping):
        supplied_dict = deepcopy(dict(resolution))
    else:
        _fail("resolution must be CaptureDecisionResolution or mapping")

    pol = require_v2_production_format(policy_in)  # type: ignore[arg-type]
    validated_activity = validate_active_activity(activity_in)  # type: ignore[arg-type]
    if validated_activity is None:
        _fail("active_activity required")
    opp = validate_capture_opportunity(opportunity_in)  # type: ignore[arg-type]

    expected = resolve_capture_opportunity(
        world_seed=world_seed,
        policy=pol,
        active_activity=validated_activity,
        opportunity=opp,
    )
    expected_dict = _resolution_semantic_dict(expected)

    if canonical_hash(supplied_dict) != canonical_hash(expected_dict):
        _fail(
            "supplied capture resolution is not semantically equivalent to "
            "recomputed 4B1 resolution"
        )

    if expected.result_kind != RESULT_TAKE:
        if expected.result_kind == RESULT_SKIP:
            _fail("SKIP_CAPTURE cannot enter apply_capture_take bridge")
        _fail(f"unexpected capture resolution result: {expected.result_kind!r}")

    if expected.occurrence_key is None or expected.random_key is None:
        _fail("TAKE_CAPTURE resolution missing occurrence_key/random_key")

    evidence = build_capture_decision_evidence(
        activity_instance_id=opp["activity_instance_id"],
        source_key=opp["source_key"],
        policy_version=expected.policy_version,
        activation_permille=expected.activation_permille,
        rule_ids=list(expected.rule_ids),
        input_state_refs=list(expected.input_state_refs),
        random_key=expected.random_key,
        opportunity_key=opp["opportunity_key"],
        selected_result=RESULT_TAKE,
    )

    capture = build_capture_fact(
        activity_instance_id=opp["activity_instance_id"],
        occurrence_key=expected.occurrence_key,
        captured_at=opp["opportunity_at"],
        capture_kind=opp["capture_kind"],
        subject_refs=list(opp["subject_refs"]),
        visual_context=deepcopy(opp["visual_context"]),
        capture_decision_evidence=evidence,
    )

    return attach_capture_to_active_activity(validated_activity, capture)
