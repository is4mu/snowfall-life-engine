"""Behavior policy load, version dispatch, and approval gate."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .canonical import canonical_hash
from .errors import ErrorCode, LifeEngineError
from .policy_invariants import validate_behavior_policy_v2_invariants
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamps


def normalize_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Validate + float-reject a Behavior Policy document (v1 or v2)."""
    data = dict(policy)
    reject_binary_floats(data)
    schema_version = data.get("schema_version")
    if schema_version not in {1, 2}:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"unsupported behavior_policy schema_version={schema_version}",
        )
    validate_instance(data, "behavior_policy")
    if schema_version == 2:
        validate_behavior_policy_v2_invariants(data)
    return data


def hash_policy(policy: dict[str, Any]) -> str:
    """SHA-256 of canonical JSON for an already-normalized or raw policy."""
    return canonical_hash(normalize_policy(dict(policy)))


def load_policy(path: Path | str) -> dict[str, Any]:
    """Load + validate a Behavior Policy file.

    Loading a v2 PRODUCTION candidate does **not** approve it.
    Call ``assert_policy_approved`` explicitly when an approval gate is required.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_policy(data)


def assert_policy_allowed(policy: dict[str, Any], *, production_mode: bool) -> None:
    mode = policy.get("policy_mode")
    if production_mode and mode == "TEST_FIXTURE":
        raise LifeEngineError(ErrorCode.FIXTURE_POLICY_FORBIDDEN, "TEST_FIXTURE in production")
    if production_mode and mode != "PRODUCTION":
        raise LifeEngineError(ErrorCode.PRODUCTION_POLICY_MISSING, f"mode={mode}")
    if not production_mode and mode not in {"TEST_FIXTURE", "PRODUCTION"}:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown policy_mode={mode}")


def assert_policy_matches_checkpoint(state: dict[str, Any], policy: dict[str, Any]) -> None:
    """Foundation: checkpoint policy/character/timezone must match loaded policy."""
    if state["behavior_policy_version"] != policy["behavior_policy_version"]:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"behavior_policy_version checkpoint={state['behavior_policy_version']} "
            f"policy={policy['behavior_policy_version']}",
        )
    if state["character_id"] != policy["character_id"]:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"character_id checkpoint={state['character_id']} policy={policy['character_id']}",
        )
    if state["timezone"] != policy["timezone"]:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"timezone checkpoint={state['timezone']} policy={policy['timezone']}",
        )


def normalize_policy_approval(approval: dict[str, Any]) -> dict[str, Any]:
    data = canonicalize_timestamps(dict(approval))
    reject_binary_floats(data)
    validate_instance(data, "behavior_policy_approval")
    return data


def load_policy_approval(path: Path | str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_policy_approval(data)


def assert_policy_approved(
    policy: dict[str, Any],
    approval: dict[str, Any] | None,
) -> None:
    """Explicit approval gate: separate artifact must match policy version + hash.

    Loading a policy never implies approval.
    Only schema_version=2 PRODUCTION policies may be approved.
    """
    if approval is None:
        raise LifeEngineError(ErrorCode.POLICY_NOT_APPROVED, "missing policy approval")
    normalized_approval = normalize_policy_approval(approval)
    normalized_policy = normalize_policy(dict(policy))
    if int(normalized_policy.get("schema_version", -1)) != 2:
        raise LifeEngineError(
            ErrorCode.POLICY_NOT_APPROVED,
            "only schema_version=2 policies may be approved",
        )
    if normalized_policy.get("policy_mode") != "PRODUCTION":
        raise LifeEngineError(
            ErrorCode.POLICY_NOT_APPROVED,
            "only policy_mode=PRODUCTION policies may be approved",
        )
    if normalized_approval["behavior_policy_version"] != normalized_policy["behavior_policy_version"]:
        raise LifeEngineError(
            ErrorCode.POLICY_HASH_MISMATCH,
            "behavior_policy_version mismatch",
        )
    expected = canonical_hash(normalized_policy)
    if normalized_approval["policy_hash"] != expected:
        raise LifeEngineError(ErrorCode.POLICY_HASH_MISMATCH, "policy_hash mismatch")
