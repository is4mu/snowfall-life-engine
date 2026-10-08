"""Slice 5E3 — reviewed operator request contract (Correction/Recovery/Upgrades)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .canonical import canonical_hash, normalize_persisted_object
from .errors import ErrorCode, LifeEngineError
from .events import normalize_actual_event_for_persistence
from .schema import reject_binary_floats, validate_instance
from .timeutil import require_canonical_timestamp

IMPLEMENTED_REQUEST_TYPES = frozenset(
    {
        "CORRECTION",
        "RECOVERY_AUTHORIZATION",
        "ENGINE_UPGRADE",
        "POLICY_UPGRADE",
    }
)
UPGRADE_REQUEST_TYPES = frozenset({"ENGINE_UPGRADE", "POLICY_UPGRADE"})

_HEX40 = frozenset("0123456789abcdef")
_HEX64 = _HEX40


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def _require_hex40(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 40:
        _fail(f"{field} must be 40 lower-hex")
    lowered = value.lower()
    if any(c not in _HEX40 for c in lowered):
        _fail(f"{field} must be 40 lower-hex")
    if value != lowered:
        _fail(f"{field} must be 40 lower-hex")
    return lowered


def _require_hex64(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        _fail(f"{field} must be 64 lower-hex")
    lowered = value.lower()
    if any(c not in _HEX64 for c in lowered):
        _fail(f"{field} must be 64 lower-hex")
    if value != lowered:
        _fail(f"{field} must be 64 lower-hex")
    return lowered


def normalize_correction_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize/validate Correction v1 payload (shared by request and action)."""
    if not isinstance(raw, Mapping):
        _fail("correction payload must be an object")
    data = normalize_persisted_object(dict(raw))
    reject_binary_floats(data)

    required = {
        "target_event_id",
        "target_event_hash",
        "supersedes_action_id",
        "corrected_event",
    }
    missing = required - set(data)
    if missing:
        _fail(f"correction missing fields: {sorted(missing)}")
    extra = set(data) - required - {"compensation_effects"}
    if extra:
        _fail(f"correction unknown fields: {sorted(extra)}", code=ErrorCode.SCHEMA_INVALID)

    target_event_id = data["target_event_id"]
    if not isinstance(target_event_id, str) or not target_event_id:
        _fail("target_event_id must be non-empty string")

    target_event_hash = data["target_event_hash"]
    if not isinstance(target_event_hash, str) or len(target_event_hash) != 64:
        _fail("target_event_hash must be 64 lower-hex")
    if any(c not in "0123456789abcdef" for c in target_event_hash):
        _fail("target_event_hash must be 64 lower-hex")

    supersedes = data["supersedes_action_id"]
    if supersedes is not None:
        if not isinstance(supersedes, str) or len(supersedes) != 64:
            _fail("supersedes_action_id must be null or 64 lower-hex")
        if any(c not in "0123456789abcdef" for c in supersedes):
            _fail("supersedes_action_id must be null or 64 lower-hex")

    corrected = normalize_actual_event_for_persistence(data["corrected_event"])
    if corrected["event_id"] != target_event_id:
        _fail("corrected_event.event_id must equal target_event_id")

    effects_raw = data.get("compensation_effects")
    compensation: list[dict[str, Any]]
    if effects_raw is None:
        compensation = []
    else:
        if not isinstance(effects_raw, list):
            _fail("compensation_effects must be an array")
        from .effects import normalize_effect

        compensation = [normalize_effect(e) for e in effects_raw]

    out: dict[str, Any] = {
        "target_event_id": target_event_id,
        "target_event_hash": target_event_hash,
        "supersedes_action_id": supersedes,
        "corrected_event": corrected,
    }
    if compensation:
        out["compensation_effects"] = compensation
    return out


def normalize_recovery_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize/validate one-window RECOVERY_AUTHORIZATION payload."""
    if not isinstance(raw, Mapping):
        _fail("recovery payload must be an object")
    data = normalize_persisted_object(dict(raw))
    reject_binary_floats(data)
    required = {"from_processed_through", "recover_through"}
    missing = required - set(data)
    if missing:
        _fail(f"recovery missing fields: {sorted(missing)}")
    extra = set(data) - required
    if extra:
        _fail(f"recovery unknown fields: {sorted(extra)}", code=ErrorCode.SCHEMA_INVALID)
    from_ts = require_canonical_timestamp(
        data["from_processed_through"], field="from_processed_through"
    )
    through_ts = require_canonical_timestamp(
        data["recover_through"], field="recover_through"
    )
    return {
        "from_processed_through": from_ts,
        "recover_through": through_ts,
    }


def normalize_upgrade_payload(
    raw: Mapping[str, Any],
    *,
    request_type: str,
) -> dict[str, Any]:
    """Normalize/validate ENGINE_UPGRADE / POLICY_UPGRADE payload."""
    if request_type not in UPGRADE_REQUEST_TYPES:
        _fail(f"normalize_upgrade_payload unsupported type: {request_type!r}")
    if not isinstance(raw, Mapping):
        _fail("upgrade payload must be an object")
    data = normalize_persisted_object(dict(raw))
    reject_binary_floats(data)
    required = {
        "current_engine_commit_sha",
        "target_engine_commit_sha",
        "current_behavior_policy_version",
        "target_behavior_policy_version",
        "current_policy_hash",
        "target_policy_hash",
    }
    missing = required - set(data)
    if missing:
        _fail(f"upgrade missing fields: {sorted(missing)}")
    extra = set(data) - required
    if extra:
        _fail(f"upgrade unknown fields: {sorted(extra)}", code=ErrorCode.SCHEMA_INVALID)

    current_engine = _require_hex40(
        data["current_engine_commit_sha"], field="current_engine_commit_sha"
    )
    target_engine = _require_hex40(
        data["target_engine_commit_sha"], field="target_engine_commit_sha"
    )
    current_version = data["current_behavior_policy_version"]
    target_version = data["target_behavior_policy_version"]
    if not isinstance(current_version, str) or not current_version:
        _fail("current_behavior_policy_version must be non-empty string")
    if not isinstance(target_version, str) or not target_version:
        _fail("target_behavior_policy_version must be non-empty string")
    current_hash = _require_hex64(data["current_policy_hash"], field="current_policy_hash")
    target_hash = _require_hex64(data["target_policy_hash"], field="target_policy_hash")

    # Structural type-specific identity rules (application-time also re-checks state).
    if target_engine == current_engine:
        _fail("upgrade target_engine_commit_sha must differ from current")
    if request_type == "ENGINE_UPGRADE":
        if target_version != current_version:
            _fail("ENGINE_UPGRADE must keep behavior_policy_version unchanged")
        if target_hash != current_hash:
            _fail("ENGINE_UPGRADE must keep policy_hash unchanged")
    else:
        if target_version == current_version:
            _fail("POLICY_UPGRADE must change behavior_policy_version")
        if target_hash == current_hash:
            _fail("POLICY_UPGRADE must change policy_hash")

    return {
        "current_engine_commit_sha": current_engine,
        "target_engine_commit_sha": target_engine,
        "current_behavior_policy_version": current_version,
        "target_behavior_policy_version": target_version,
        "current_policy_hash": current_hash,
        "target_policy_hash": target_hash,
    }


def normalize_operator_request(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Strict pure request normalizer. Rejects unimplemented action types."""
    reject_binary_floats(raw)
    data = normalize_persisted_object(dict(raw))
    if "schema_version" not in data:
        data["schema_version"] = 1

    request_type = data.get("request_type")
    if request_type not in IMPLEMENTED_REQUEST_TYPES:
        _fail(
            f"unsupported operator request_type: {request_type!r}",
            code=ErrorCode.UNSUPPORTED_VERSION,
        )

    # Structural schema (additionalProperties: false via oneOf variants).
    validate_instance(data, "operator_request")

    data["expected_life_head"] = str(data["expected_life_head"]).lower()
    approval_ref = data["approval_ref"]
    if not isinstance(approval_ref, str) or not approval_ref.strip():
        _fail("approval_ref must be non-empty string")
    data["approval_ref"] = approval_ref

    if request_type == "CORRECTION":
        data["effective_at"] = require_canonical_timestamp(
            data["effective_at"], field="effective_at"
        )
        data["correction"] = normalize_correction_payload(data["correction"])
    elif request_type == "RECOVERY_AUTHORIZATION":
        data["recovery"] = normalize_recovery_payload(data["recovery"])
    else:
        data["upgrade"] = normalize_upgrade_payload(
            data["upgrade"], request_type=request_type
        )

    # Re-validate after semantic normalization for closed contract.
    validate_instance(data, "operator_request")
    return data


def request_semantics_for_identity(request: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical request body used for deterministic request/action identity."""
    normalized = normalize_operator_request(request)
    request_type = normalized["request_type"]
    if request_type == "CORRECTION":
        return {
            "schema_version": normalized["schema_version"],
            "request_type": normalized["request_type"],
            "expected_life_head": normalized["expected_life_head"],
            "effective_at": normalized["effective_at"],
            "approval_ref": normalized["approval_ref"],
            "correction": deepcopy(normalized["correction"]),
        }
    if request_type == "RECOVERY_AUTHORIZATION":
        return {
            "schema_version": normalized["schema_version"],
            "request_type": normalized["request_type"],
            "expected_life_head": normalized["expected_life_head"],
            "approval_ref": normalized["approval_ref"],
            "recovery": deepcopy(normalized["recovery"]),
        }
    return {
        "schema_version": normalized["schema_version"],
        "request_type": normalized["request_type"],
        "expected_life_head": normalized["expected_life_head"],
        "approval_ref": normalized["approval_ref"],
        "upgrade": deepcopy(normalized["upgrade"]),
    }


def derive_request_hash(request: Mapping[str, Any]) -> str:
    return canonical_hash(
        {
            "kind": "operator-request-v1",
            "request": request_semantics_for_identity(request),
        }
    )


def derive_request_id(request: Mapping[str, Any]) -> str:
    """Deterministic request ID (identical to request hash for 5E1/5E2/5E3)."""
    return derive_request_hash(request)
