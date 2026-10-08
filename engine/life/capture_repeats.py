"""Slice 4B3 — chainable repeated-frame capture mechanics (pure; one link at a time).

Approved sequence (Issue #61 / #45 comment 5828503589):

  eligible pending Capture
   -> one next-frame CaptureOpportunity
   -> existing 4B1 TAKE/SKIP
   -> TAKE => child pending Capture
   -> child may become the next parent
   -> ...
   -> first SKIP ends the sequence

Does **not**:
- invent primary opportunities (4B2)
- add repeat probability / multi-shot count fields / daily quota
- eagerly recurse until SKIP
- impose a production semantic max frame count
- mutate parent Capture facts
- project Camera Roll / write runtime / wire clock
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .canonical import canonical_hash
from .capture_decisions import (
    RESULT_TAKE,
    CaptureDecisionResolution,
    build_capture_opportunity,
    resolve_capture_opportunity,
    validate_capture_opportunity,
)
from .capture_pipeline import apply_capture_take
from .captures import validate_capture_fact
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .ids import stable_id
from .rng import SeedLike

REPEAT_RULE_ID = "slice4b3.alternate_frame"
REPEATABLE_CAPTURE_KINDS = frozenset({"SELFIE", "PEOPLE"})
PARENT_REF_PREFIX = "capture-parent:"


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def _require_nonempty_str(value: object, *, label: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool) or value == "":
        _fail(f"{label} must be a non-empty string (coercion forbidden)")
    return value


def derive_repeat_frame_source_key(parent_opportunity_key: str) -> str:
    """Stable next-frame source identity from the parent's opportunity_key."""
    parent_opp = _require_nonempty_str(parent_opportunity_key, label="parent_opportunity_key")
    return stable_id("capture-source-alternate-frame", parent_opp)


def parent_capture_input_ref(parent_capture_id: str) -> str:
    """Machine-readable immediate parent provenance ref."""
    parent_id = _require_nonempty_str(parent_capture_id, label="parent_capture_id")
    return f"{PARENT_REF_PREFIX}{parent_id}"


def _resolution_semantic_dict(resolution: CaptureDecisionResolution | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(resolution, CaptureDecisionResolution):
        return resolution.as_dict()
    if isinstance(resolution, Mapping):
        return deepcopy(dict(resolution))
    _fail("resolution must be CaptureDecisionResolution or mapping")
    raise AssertionError("unreachable")  # pragma: no cover


def _locate_pending_parent(
    active_activity: Mapping[str, Any],
    parent_capture_id: object,
) -> dict[str, Any]:
    """Locate + validate parent strictly inside current pending_captures. Fail closed."""
    parent_id = _require_nonempty_str(parent_capture_id, label="parent_capture_id")
    validated_activity = validate_active_activity(
        deepcopy(dict(active_activity)) if isinstance(active_activity, Mapping) else active_activity
    )
    if validated_activity is None:
        _fail("active_activity required")

    pending = list(validated_activity.get("pending_captures") or [])
    matches = [cap for cap in pending if cap["capture_id"] == parent_id]
    if not matches:
        _fail(
            "parent Capture not found in current ActiveActivity.pending_captures "
            "(historical/finalized-only or missing parent rejected)"
        )
    if len(matches) != 1:
        _fail(f"conflicting parent Capture identity: {parent_id!r}")

    parent = validate_capture_fact(matches[0])
    if parent["activity_instance_id"] != validated_activity["activity_instance_id"]:
        _fail("parent Capture owner mismatch vs active_activity.activity_instance_id")
    if parent["capture_decision_evidence"]["selected_result"] != RESULT_TAKE:
        _fail("parent Capture must carry TAKE_CAPTURE evidence")
    return parent


def build_next_repeat_frame_opportunity(
    *,
    active_activity: Mapping[str, Any],
    parent_capture_id: object,
) -> dict[str, Any] | None:
    """Build the unique successor CaptureOpportunity for one pending parent. Pure.

    Returns None for ineligible capture kinds (FOOD/SCENERY/OBJECT/DAILY_LIFE).
    Fail-closed for missing/malformed/non-pending/owner-mismatch parents.
    Repeat children remain eligible (chainable). No RNG / I/O / mutation.
    """
    parent = _locate_pending_parent(active_activity, parent_capture_id)
    kind = parent["capture_kind"]
    if kind not in REPEATABLE_CAPTURE_KINDS:
        return None

    evidence = parent["capture_decision_evidence"]
    source_key = derive_repeat_frame_source_key(evidence["opportunity_key"])
    return build_capture_opportunity(
        activity_instance_id=parent["activity_instance_id"],
        source_key=source_key,
        opportunity_at=parent["captured_at"],
        capture_kind=kind,
        subject_refs=list(parent["subject_refs"]),
        visual_context=deepcopy(parent["visual_context"]),
        rule_ids=[REPEAT_RULE_ID],
        input_state_refs=[parent_capture_input_ref(parent["capture_id"])],
    )


def apply_repeat_frame_take(
    *,
    world_seed: SeedLike,
    policy: Mapping[str, Any],
    active_activity: Mapping[str, Any],
    parent_capture_id: object,
    opportunity: Mapping[str, Any],
    resolution: CaptureDecisionResolution | Mapping[str, Any],
) -> dict[str, Any]:
    """Verify one repeat TAKE and attach exactly one child pending Capture. Pure.

    Reconstructs the expected successor opportunity from the current pending parent,
    recomputes 4B1 resolution, and delegates to the existing 4B2 TAKE bridge.
    SKIP cannot enter this bridge. Parent Capture facts are never mutated.
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
    supplied_resolution = _resolution_semantic_dict(resolution)

    pol = require_v2_production_format(policy_in)  # type: ignore[arg-type]
    parent_id = _require_nonempty_str(parent_capture_id, label="parent_capture_id")

    # Snapshot parent facts before attachment for immutability proof.
    parent_before = _locate_pending_parent(activity_in, parent_id)
    parent_before_hash = canonical_hash(parent_before)

    expected_opp = build_next_repeat_frame_opportunity(
        active_activity=activity_in,
        parent_capture_id=parent_id,
    )
    if expected_opp is None:
        _fail(
            f"parent capture_kind {parent_before['capture_kind']!r} is not "
            "eligible for repeat-frame TAKE"
        )

    supplied_opp = validate_capture_opportunity(opportunity_in)  # type: ignore[arg-type]
    if canonical_hash(supplied_opp) != canonical_hash(expected_opp):
        _fail(
            "supplied repeat opportunity is not semantically equivalent to "
            "reconstructed successor opportunity"
        )

    expected = resolve_capture_opportunity(
        world_seed=world_seed,
        policy=pol,
        active_activity=activity_in,
        opportunity=expected_opp,
    )
    expected_dict = _resolution_semantic_dict(expected)
    if canonical_hash(supplied_resolution) != canonical_hash(expected_dict):
        _fail(
            "supplied capture resolution is not semantically equivalent to "
            "recomputed 4B1 resolution"
        )
    if expected.result_kind != RESULT_TAKE:
        _fail("SKIP_CAPTURE cannot enter apply_repeat_frame_take bridge")

    updated = apply_capture_take(
        world_seed=world_seed,
        policy=pol,
        active_activity=activity_in,
        opportunity=expected_opp,
        resolution=expected,
    )

    # Parent must remain byte-identical; exactly one child for this parent link.
    pending = list(updated.get("pending_captures") or [])
    parent_after = next((c for c in pending if c["capture_id"] == parent_id), None)
    if parent_after is None:
        _fail("parent Capture missing after repeat TAKE")
    if canonical_hash(parent_after) != parent_before_hash:
        _fail("parent Capture mutated by repeat TAKE")

    child_id = None
    if expected.occurrence_key is not None:
        from .captures import derive_capture_id

        child_id = derive_capture_id(
            expected_opp["activity_instance_id"], expected.occurrence_key
        )
    children = [
        c
        for c in pending
        if parent_capture_input_ref(parent_id)
        in c["capture_decision_evidence"]["input_state_refs"]
    ]
    if child_id is not None:
        children = [c for c in children if c["capture_id"] == child_id] or children
    if len(children) != 1:
        _fail(
            f"repeat TAKE must attach exactly one child for parent {parent_id!r}; "
            f"found {len(children)}"
        )
    child = children[0]
    if child["capture_id"] == parent_id:
        _fail("repeat child capture_id must differ from parent")
    if REPEAT_RULE_ID not in child["capture_decision_evidence"]["rule_ids"]:
        _fail("repeat child evidence missing slice4b3.alternate_frame")
    if parent_capture_input_ref(parent_id) not in child["capture_decision_evidence"][
        "input_state_refs"
    ]:
        _fail("repeat child evidence missing immediate capture-parent ref")

    return updated
