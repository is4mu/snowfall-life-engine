"""Slice 5E3 — SUCCESS-only bounded last-operator-action observability."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .canonical import normalize_persisted_object
from .errors import ErrorCode, LifeEngineError
from .operator_request import UPGRADE_REQUEST_TYPES
from .schema import reject_binary_floats, validate_instance
from .timeutil import require_canonical_timestamp

LAST_OPERATOR_ACTION_RELPATH = "state/last-operator-action.json"

_HEX64_RE = re.compile(r"^[a-f0-9]{64}$")
_HEX40_RE = re.compile(r"^[a-f0-9]{40}$")

_FORBIDDEN_KEYS = frozenset(
    {
        "commit_sha",
        "resulting_commit_sha",
        "world_seed",
        "decision_frames",
        "fact_tape",
        "state_dump",
        "full_state",
        "request",
        "request_dump",
        "token",
        "bearer_token",
        "remote_bearer_token",
        "prose_rationale",
        "rationale",
        "free_form_rationale",
    }
)

_IMPLEMENTED_ACTION_TYPES = frozenset(
    {
        "CORRECTION",
        "RECOVERY_AUTHORIZATION",
        "ENGINE_UPGRADE",
        "POLICY_UPGRADE",
    }
)


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def _require_hex64(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
        _fail(f"{field} must be a 64-hex digest")
    return value


def _require_hex40(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _HEX40_RE.fullmatch(value):
        _fail(f"{field} must be a 40-hex commit SHA")
    return value


def validate_last_operator_action(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Strict SUCCESS-only last-operator-action normalizer."""
    reject_binary_floats(raw)
    data = normalize_persisted_object(dict(raw))
    if "schema_version" not in data:
        data["schema_version"] = 1

    overlap = _FORBIDDEN_KEYS.intersection(data)
    if overlap:
        _fail(f"last-operator-action contains forbidden fields: {sorted(overlap)}")

    validate_instance(data, "last_operator_action")

    if data.get("result") != "SUCCESS":
        _fail("last-operator-action result must be SUCCESS")

    action_type = data["action_type"]
    if action_type not in _IMPLEMENTED_ACTION_TYPES:
        _fail(
            f"unsupported last-operator-action action_type: {action_type!r}",
            code=ErrorCode.UNSUPPORTED_VERSION,
        )

    _require_hex64(data["action_id"], field="action_id")
    _require_hex64(data["request_id"], field="request_id")
    _require_hex40(data["life_base_sha"], field="life_base_sha")
    _require_hex40(data["engine_commit_sha"], field="engine_commit_sha")
    _require_hex64(data["state_before_hash"], field="state_before_hash")
    _require_hex64(data["state_after_hash"], field="state_after_hash")
    _require_hex64(
        data["operator_history_hash_before"], field="operator_history_hash_before"
    )
    _require_hex64(
        data["operator_history_hash_after"], field="operator_history_hash_after"
    )
    require_canonical_timestamp(
        data["processed_through_before"], field="processed_through_before"
    )
    require_canonical_timestamp(
        data["processed_through_after"], field="processed_through_after"
    )

    files = data.get("files_changed")
    if not isinstance(files, list):
        _fail("files_changed must be array")
    if files != sorted(files):
        _fail("files_changed must be sorted")
    if len(files) != len(set(files)):
        _fail("files_changed must not contain duplicates")
    if not files:
        _fail("SUCCESS last-operator-action requires non-empty files_changed")

    rev_before = int(data["state_revision_before"])
    rev_after = int(data["state_revision_after"])
    if rev_after != rev_before + 1:
        _fail("last-operator-action state_revision must advance exactly +1")
    if data["state_before_hash"] == data["state_after_hash"]:
        _fail("last-operator-action state hashes must differ on SUCCESS")
    if data["operator_history_hash_before"] == data["operator_history_hash_after"]:
        _fail("last-operator-action operator history hashes must differ on SUCCESS")

    if action_type == "RECOVERY_AUTHORIZATION":
        require_canonical_timestamp(data["recovery_from"], field="recovery_from")
        require_canonical_timestamp(data["recovery_through"], field="recovery_through")
        if data["recovery_from"] != data["processed_through_before"]:
            _fail("recovery_from must equal processed_through_before")
        if data["recovery_through"] != data["processed_through_after"]:
            _fail("recovery_through must equal processed_through_after")
        if data["recovery_through"] <= data["recovery_from"]:
            _fail("recovery_through must be strictly greater than recovery_from")
        for field in (
            "engine_commit_sha_before",
            "behavior_policy_version_before",
            "policy_hash_before",
            "policy_hash_after",
        ):
            if field in data:
                _fail(f"RECOVERY_AUTHORIZATION must not include {field}")
    elif action_type in UPGRADE_REQUEST_TYPES:
        if "recovery_from" in data or "recovery_through" in data:
            _fail(f"{action_type} last-operator-action must not include recovery fields")
        if data["processed_through_before"] != data["processed_through_after"]:
            _fail(f"{action_type} must not advance processed_through")
        _require_hex40(data["engine_commit_sha_before"], field="engine_commit_sha_before")
        before_version = data["behavior_policy_version_before"]
        if not isinstance(before_version, str) or not before_version:
            _fail("behavior_policy_version_before must be non-empty string")
        _require_hex64(data["policy_hash_before"], field="policy_hash_before")
        _require_hex64(data["policy_hash_after"], field="policy_hash_after")
        if data["engine_commit_sha_before"] == data["engine_commit_sha"]:
            _fail("upgrade engine_commit_sha_before must differ from post-action engine")
        if action_type == "ENGINE_UPGRADE":
            if before_version != data["behavior_policy_version"]:
                _fail("ENGINE_UPGRADE must keep behavior_policy_version unchanged")
            if data["policy_hash_before"] != data["policy_hash_after"]:
                _fail("ENGINE_UPGRADE must keep policy_hash unchanged")
        else:
            if before_version == data["behavior_policy_version"]:
                _fail("POLICY_UPGRADE must change behavior_policy_version")
            if data["policy_hash_before"] == data["policy_hash_after"]:
                _fail("POLICY_UPGRADE must change policy_hash")
    else:
        if "recovery_from" in data or "recovery_through" in data:
            _fail("CORRECTION last-operator-action must not include recovery fields")
        for field in (
            "engine_commit_sha_before",
            "behavior_policy_version_before",
            "policy_hash_before",
            "policy_hash_after",
        ):
            if field in data:
                _fail(f"CORRECTION must not include {field}")
        if data["processed_through_before"] != data["processed_through_after"]:
            _fail("CORRECTION must not advance processed_through")

    validate_instance(data, "last_operator_action")
    return data


def build_last_operator_action(
    *,
    action: Mapping[str, Any],
    life_base_sha: str,
    engine_commit_sha: str,
    behavior_policy_version: str,
    state_revision_before: int,
    state_revision_after: int,
    state_before_hash: str,
    state_after_hash: str,
    operator_history_hash_before: str,
    operator_history_hash_after: str,
    processed_through_before: str,
    processed_through_after: str,
    files_changed: Sequence[str],
    engine_commit_sha_before: str | None = None,
    behavior_policy_version_before: str | None = None,
    policy_hash_before: str | None = None,
    policy_hash_after: str | None = None,
) -> dict[str, Any]:
    """Build the SUCCESS-only bounded operator observability record."""
    action_type = action["action_type"]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "result": "SUCCESS",
        "action_id": action["action_id"],
        "request_id": action["request_id"],
        "action_type": action_type,
        "life_base_sha": life_base_sha,
        "engine_commit_sha": engine_commit_sha,
        "behavior_policy_version": behavior_policy_version,
        "state_revision_before": state_revision_before,
        "state_revision_after": state_revision_after,
        "state_before_hash": state_before_hash,
        "state_after_hash": state_after_hash,
        "operator_history_hash_before": operator_history_hash_before,
        "operator_history_hash_after": operator_history_hash_after,
        "processed_through_before": processed_through_before,
        "processed_through_after": processed_through_after,
        "files_changed": list(files_changed),
    }
    if action_type == "RECOVERY_AUTHORIZATION":
        recovery = action["recovery"]
        payload["recovery_from"] = recovery["from_processed_through"]
        payload["recovery_through"] = recovery["recover_through"]
    elif action_type in UPGRADE_REQUEST_TYPES:
        if (
            engine_commit_sha_before is None
            or behavior_policy_version_before is None
            or policy_hash_before is None
            or policy_hash_after is None
        ):
            _fail(f"{action_type} requires before/after engine/policy identity fields")
        payload["engine_commit_sha_before"] = engine_commit_sha_before
        payload["behavior_policy_version_before"] = behavior_policy_version_before
        payload["policy_hash_before"] = policy_hash_before
        payload["policy_hash_after"] = policy_hash_after
    return validate_last_operator_action(payload)
