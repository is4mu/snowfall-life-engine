"""Slice 5E3 — immutable operator action records and ledger verification."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .canonical import canonical_hash, canonical_json, normalize_persisted_object
from .errors import ErrorCode, LifeEngineError
from .operator_history import (
    EMPTY_OPERATOR_HISTORY_HASH,
    OPERATOR_HISTORY_RELPATH,
    empty_operator_history,
    fold_operator_history,
    normalize_operator_history,
    verify_operator_history,
)
from .operator_request import (
    IMPLEMENTED_REQUEST_TYPES,
    derive_request_hash,
    derive_request_id,
    normalize_correction_payload,
    normalize_operator_request,
    normalize_recovery_payload,
    normalize_upgrade_payload,
    request_semantics_for_identity,
)
from .schema import reject_binary_floats, validate_instance
from .timeutil import require_canonical_timestamp

OPERATOR_ACTIONS_DIR = "operator/actions"
_ACTION_FILE_RE = re.compile(r"^operator/actions/([0-9a-f]{64})\.json$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def is_operator_action_relpath(rel: str) -> bool:
    return _ACTION_FILE_RE.fullmatch(rel) is not None


def operator_action_relpath(action_id: str) -> str:
    if not isinstance(action_id, str) or _HEX64_RE.fullmatch(action_id) is None:
        _fail("action_id must be 64 lower-hex")
    return f"{OPERATOR_ACTIONS_DIR}/{action_id}.json"


def derive_action_id(
    *,
    request: Mapping[str, Any],
    life_base_sha: str,
    previous_action_id: str | None,
) -> str:
    """Deterministic cross-platform-safe 64 lower-hex action identity."""
    if not isinstance(life_base_sha, str) or _HEX40_RE.fullmatch(life_base_sha) is None:
        _fail("life_base_sha must be 40 lower-hex")
    if previous_action_id is not None:
        if (
            not isinstance(previous_action_id, str)
            or _HEX64_RE.fullmatch(previous_action_id) is None
        ):
            _fail("previous_action_id must be null or 64 lower-hex")
    return canonical_hash(
        {
            "kind": "operator-action-v1",
            "request": request_semantics_for_identity(request),
            "life_base_sha": life_base_sha,
            "previous_action_id": previous_action_id,
        }
    )


def deepcopy_correction(correction: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json(dict(correction)))


def deepcopy_recovery(recovery: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json(dict(recovery)))


def deepcopy_upgrade(upgrade: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json(dict(upgrade)))


def build_operator_action_from_request(
    request: Mapping[str, Any],
    *,
    life_base_sha: str,
    previous_action_id: str | None,
) -> dict[str, Any]:
    """Build an immutable action bound to request + Life base + prior head."""
    normalized_request = normalize_operator_request(request)
    if normalized_request["expected_life_head"] != life_base_sha:
        _fail("request.expected_life_head must equal life_base_sha")
    request_id = derive_request_id(normalized_request)
    request_hash = derive_request_hash(normalized_request)
    if request_id != request_hash:
        _fail("request_id must equal request_hash")
    action_id = derive_action_id(
        request=normalized_request,
        life_base_sha=life_base_sha,
        previous_action_id=previous_action_id,
    )
    request_type = normalized_request["request_type"]
    action: dict[str, Any] = {
        "schema_version": 1,
        "action_id": action_id,
        "action_type": request_type,
        "previous_action_id": previous_action_id,
        "life_base_sha": life_base_sha,
        "request_id": request_id,
        "request_hash": request_hash,
        "approval_ref": normalized_request["approval_ref"],
    }
    if request_type == "CORRECTION":
        action["effective_at"] = normalized_request["effective_at"]
        action["correction"] = deepcopy_correction(normalized_request["correction"])
    elif request_type == "RECOVERY_AUTHORIZATION":
        action["recovery"] = deepcopy_recovery(normalized_request["recovery"])
    else:
        action["upgrade"] = deepcopy_upgrade(normalized_request["upgrade"])
    return normalize_operator_action(action)


def _reconstruct_request_from_action(data: Mapping[str, Any]) -> dict[str, Any]:
    action_type = data["action_type"]
    if action_type == "CORRECTION":
        return {
            "schema_version": 1,
            "request_type": "CORRECTION",
            "expected_life_head": data["life_base_sha"],
            "effective_at": data["effective_at"],
            "approval_ref": data["approval_ref"],
            "correction": data["correction"],
        }
    if action_type == "RECOVERY_AUTHORIZATION":
        return {
            "schema_version": 1,
            "request_type": "RECOVERY_AUTHORIZATION",
            "expected_life_head": data["life_base_sha"],
            "approval_ref": data["approval_ref"],
            "recovery": data["recovery"],
        }
    return {
        "schema_version": 1,
        "request_type": action_type,
        "expected_life_head": data["life_base_sha"],
        "approval_ref": data["approval_ref"],
        "upgrade": data["upgrade"],
    }


def normalize_operator_action(raw: Mapping[str, Any]) -> dict[str, Any]:
    reject_binary_floats(raw)
    data = normalize_persisted_object(dict(raw))
    if "schema_version" not in data:
        data["schema_version"] = 1

    action_type = data.get("action_type")
    if action_type not in IMPLEMENTED_REQUEST_TYPES:
        _fail(
            f"unsupported operator action_type: {action_type!r}",
            code=ErrorCode.UNSUPPORTED_VERSION,
        )

    validate_instance(data, "operator_action")

    data["life_base_sha"] = str(data["life_base_sha"]).lower()
    if action_type == "CORRECTION":
        data["effective_at"] = require_canonical_timestamp(
            data["effective_at"], field="effective_at"
        )
        data["correction"] = normalize_correction_payload(data["correction"])
    elif action_type == "RECOVERY_AUTHORIZATION":
        data["recovery"] = normalize_recovery_payload(data["recovery"])
    else:
        data["upgrade"] = normalize_upgrade_payload(
            data["upgrade"], request_type=action_type
        )

    reconstructed_request = _reconstruct_request_from_action(data)
    expected_request_id = derive_request_id(reconstructed_request)
    expected_request_hash = derive_request_hash(reconstructed_request)
    if data["request_id"] != expected_request_id:
        _fail("action.request_id mismatch vs normalized request identity")
    if data["request_hash"] != expected_request_hash:
        _fail("action.request_hash mismatch vs normalized request identity")
    expected_action_id = derive_action_id(
        request=reconstructed_request,
        life_base_sha=data["life_base_sha"],
        previous_action_id=data["previous_action_id"],
    )
    if data["action_id"] != expected_action_id:
        _fail("action.action_id mismatch vs deterministic identity")

    validate_instance(data, "operator_action")
    return data


@dataclass(frozen=True, init=False)
class FrozenOperatorLedger:
    """Verified append-only operator ledger with copy-isolated action payloads."""

    head_action_id: str | None
    operator_history_hash: str
    action_count: int
    _actions_json: str = field(repr=False)

    def __init__(
        self,
        *,
        head_action_id: str | None,
        operator_history_hash: str,
        action_count: int,
        actions: Sequence[Mapping[str, Any]],
    ) -> None:
        history = normalize_operator_history(
            {
                "schema_version": 1,
                "head_action_id": head_action_id,
                "operator_history_hash": operator_history_hash,
                "action_count": action_count,
            }
        )
        chain = verify_operator_action_chain(actions)
        verify_operator_history(
            chain,
            head_action_id=history["head_action_id"],
            operator_history_hash=history["operator_history_hash"],
            action_count=history["action_count"],
        )
        object.__setattr__(self, "head_action_id", history["head_action_id"])
        object.__setattr__(
            self, "operator_history_hash", history["operator_history_hash"]
        )
        object.__setattr__(self, "action_count", history["action_count"])
        object.__setattr__(self, "_actions_json", canonical_json(chain))

    @property
    def actions(self) -> tuple[dict[str, Any], ...]:
        # Return fresh objects so callers cannot mutate verified ledger state.
        return tuple(json.loads(self._actions_json))

    def as_history(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "head_action_id": self.head_action_id,
            "operator_history_hash": self.operator_history_hash,
            "action_count": self.action_count,
        }


def empty_operator_ledger() -> FrozenOperatorLedger:
    empty = empty_operator_history()
    return FrozenOperatorLedger(
        head_action_id=empty["head_action_id"],
        operator_history_hash=empty["operator_history_hash"],
        action_count=empty["action_count"],
        actions=(),
    )


def verify_operator_action_chain(actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Verify linear previous-head bindings; reject forks/duplicates/reorder."""
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    expected_prev: str | None = None
    for i, raw in enumerate(actions):
        action = normalize_operator_action(raw)
        action_id = action["action_id"]
        if action_id in seen_ids:
            _fail(f"duplicate operator action_id: {action_id}", code=ErrorCode.DUPLICATE_ID)
        seen_ids.add(action_id)
        if action["previous_action_id"] != expected_prev:
            _fail(
                f"operator action chain fork/reorder at index {i}: "
                f"previous_action_id={action['previous_action_id']!r} "
                f"expected={expected_prev!r}"
            )
        expected_prev = action_id
        normalized.append(action)
    return normalized


def verify_operator_ledger(
    *,
    history: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
) -> FrozenOperatorLedger:
    """Fail-closed verification of operator-history identity + action files."""
    hist = normalize_operator_history(history)
    chain = verify_operator_action_chain(actions)
    verify_operator_history(
        chain,
        head_action_id=hist["head_action_id"],
        operator_history_hash=hist["operator_history_hash"],
        action_count=hist["action_count"],
    )
    return FrozenOperatorLedger(
        head_action_id=hist["head_action_id"],
        operator_history_hash=hist["operator_history_hash"],
        action_count=hist["action_count"],
        actions=tuple(chain),
    )


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"invalid operator JSON at {path}: {exc}")
    if not isinstance(raw, dict):
        _fail(f"operator JSON root must be object: {path}")
    reject_binary_floats(raw, path=str(path))
    return raw


def load_operator_ledger(root: Path | str) -> FrozenOperatorLedger:
    """Load and verify operator ledger from a runtime root.

    Missing ``state/operator-history.json`` with no action files yields the
    deterministic empty ledger (behavior-identical to pre-5E1 trees).
    """
    root_path = Path(root)
    history_path = root_path / OPERATOR_HISTORY_RELPATH
    actions_dir = root_path / OPERATOR_ACTIONS_DIR

    action_files: dict[str, Path] = {}
    if actions_dir.is_dir():
        for path in sorted(actions_dir.glob("*.json")):
            rel = f"{OPERATOR_ACTIONS_DIR}/{path.name}"
            match = _ACTION_FILE_RE.fullmatch(rel)
            if match is None:
                _fail(f"unexpected operator action filename: {rel}")
            action_id = match.group(1)
            if action_id in action_files:
                _fail(f"duplicate operator action file for {action_id}")
            action_files[action_id] = path

    if not history_path.is_file():
        if action_files:
            _fail("operator action files present but operator-history.json missing")
        return empty_operator_ledger()

    history = normalize_operator_history(_read_json_object(history_path))
    if history["action_count"] == 0:
        if action_files:
            _fail("empty operator history forbids action files")
        if history["operator_history_hash"] != EMPTY_OPERATOR_HISTORY_HASH:
            _fail("empty operator history hash mismatch")
        return empty_operator_ledger()

    # Walk chain from head via previous links collected from files.
    by_id: dict[str, dict[str, Any]] = {}
    for action_id, path in action_files.items():
        action = normalize_operator_action(_read_json_object(path))
        if action["action_id"] != action_id:
            _fail(f"operator action file name mismatch: {path.name}")
        by_id[action_id] = action

    # Reconstruct chain order from genesis using previous_action_id links.
    chain: list[dict[str, Any]] = []
    expected_prev: str | None = None
    remaining = dict(by_id)
    while len(chain) < history["action_count"]:
        nxt = None
        for action_id, action in list(remaining.items()):
            if action["previous_action_id"] == expected_prev:
                if nxt is not None:
                    _fail("forked operator action previous-head binding")
                nxt = action
        if nxt is None:
            _fail("missing linked operator action file in chain")
        chain.append(nxt)
        del remaining[nxt["action_id"]]
        expected_prev = nxt["action_id"]

    if remaining:
        _fail(
            f"orphan operator action file(s): {sorted(remaining.keys())}",
        )

    return verify_operator_ledger(history=history, actions=chain)


def list_operator_action_relpaths(root: Path | str) -> list[str]:
    root_path = Path(root)
    actions_dir = root_path / OPERATOR_ACTIONS_DIR
    if not actions_dir.is_dir():
        return []
    out: list[str] = []
    for path in sorted(actions_dir.glob("*.json")):
        rel = f"{OPERATOR_ACTIONS_DIR}/{path.name}"
        if is_operator_action_relpath(rel):
            out.append(rel)
    return out
