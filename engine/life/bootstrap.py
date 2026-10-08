"""Bootstrap proposal / approval gate; does not activate production application state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .canonical import canonical_hash
from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .spatial_context import validate_spatial_context
from .timeutil import canonicalize_timestamps


def normalize_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    data = canonicalize_timestamps(dict(proposal))
    validate_instance(data, "bootstrap_proposal")
    reject_binary_floats(data)
    return data


def hash_proposal(proposal: dict[str, Any]) -> str:
    return canonical_hash(normalize_proposal(proposal))


def write_proposal(proposal: dict[str, Any], output_path: Path | str) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = normalize_proposal(proposal)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return canonical_hash(data)


def load_approval(path: Path | str) -> dict[str, Any]:
    data = canonicalize_timestamps(json.loads(Path(path).read_text(encoding="utf-8")))
    validate_instance(data, "bootstrap_approval")
    return data


def assert_approved(proposal: dict[str, Any], approval: dict[str, Any] | None) -> None:
    if approval is None:
        raise LifeEngineError(ErrorCode.BOOTSTRAP_NOT_APPROVED, "missing approval")
    normalized_approval = canonicalize_timestamps(dict(approval))
    validate_instance(normalized_approval, "bootstrap_approval")
    expected = hash_proposal(proposal)
    if normalized_approval["proposal_hash"] != expected:
        raise LifeEngineError(ErrorCode.BOOTSTRAP_HASH_MISMATCH, "proposal_hash mismatch")
    prop = normalize_proposal(proposal)
    if normalized_approval["life_epoch"] != prop["life_epoch"]:
        raise LifeEngineError(ErrorCode.BOOTSTRAP_HASH_MISMATCH, "life_epoch mismatch")


def assert_spatial_context_bound(
    proposal: dict[str, Any],
    spatial_context: dict[str, Any],
) -> None:
    """Require proposal.spatial_context_ref to exactly bind a validated SpatialContext.

    Pure / fail-closed: does not mutate inputs, repair, infer, substitute, or
    auto-select locations/routes. Does not require HOME or any route.
    """
    prop = normalize_proposal(proposal)
    ctx = validate_spatial_context(spatial_context)

    ref = prop.get("spatial_context_ref")
    if ref is None:
        raise LifeEngineError(
            ErrorCode.BOOTSTRAP_NOT_APPROVED,
            "spatial_context_ref required for spatial binding",
        )
    if not isinstance(ref, dict):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "spatial_context_ref must be an object",
        )

    if prop["character_id"] != ctx["character_id"]:
        raise LifeEngineError(
            ErrorCode.BOOTSTRAP_HASH_MISMATCH,
            "proposal.character_id does not match spatial_context.character_id",
        )
    if ref.get("spatial_context_version") != ctx["spatial_context_version"]:
        raise LifeEngineError(
            ErrorCode.BOOTSTRAP_HASH_MISMATCH,
            "spatial_context_ref.spatial_context_version mismatch",
        )
    if ref.get("context_hash") != ctx["context_hash"]:
        raise LifeEngineError(
            ErrorCode.BOOTSTRAP_HASH_MISMATCH,
            "spatial_context_ref.context_hash mismatch",
        )

    location_ids = {loc["location_id"] for loc in ctx["locations"]}
    if prop["initial_location_id"] not in location_ids:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "initial_location_id not declared in SpatialContext.locations",
        )


def require_production_bootstrap(
    *,
    policy: dict[str, Any] | None,
    proposal: dict[str, Any] | None,
    approval: dict[str, Any] | None,
    spatial_context: dict[str, Any] | None,
) -> None:
    """Production init must fail without PRODUCTION policy + approved spatial-bound bootstrap."""
    if policy is None or policy.get("policy_mode") != "PRODUCTION":
        raise LifeEngineError(ErrorCode.PRODUCTION_POLICY_MISSING, "no PRODUCTION policy")
    if proposal is None:
        raise LifeEngineError(ErrorCode.BOOTSTRAP_NOT_APPROVED, "no proposal")
    assert_approved(proposal, approval)
    if spatial_context is None:
        raise LifeEngineError(ErrorCode.BOOTSTRAP_NOT_APPROVED, "no SpatialContext")
    assert_spatial_context_bound(proposal, spatial_context)
