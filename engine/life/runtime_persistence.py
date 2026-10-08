"""Slice 5C1 — persistent runtime snapshot and staged candidate tree.

Owns SocialResponseState persistence, CurrentState cross-file binding,
timeline append / deterministic day close, Camera Roll shard merge /
deterministic safe month close, exactly-one state_revision bump on CHANGE,
SUCCESS-only state/last-run.json, and full candidate-tree verification.

Writes only under a caller-supplied isolated candidate_root. Never mutates
the authoritative baseline root. No Git commit/ref/push. No network.
No wall-clock input. Production application state is not activated by this module.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .camera_roll import (
    camera_roll_shard_month,
    camera_roll_shard_path,
    merge_camera_roll_shard,
    project_camera_roll_records,
    validate_camera_roll_shard_month,
)
from .canonical import canonical_hash, normalize_persisted_object, persisted_hash
from .checkpoint import bump_revision, validate_checkpoint
from .errors import ErrorCode, LifeEngineError
from .history import collect_ledger_events, verify_history
from .ids import stable_id
from .runtime_bundle import (
    REQUIRED_BUNDLE_RELPATHS,
    RuntimeBundle,
    RuntimeReferenceSets,
    load_runtime_bundle,
    validate_runtime_bundle,
)
from .runtime_orchestrator import (
    RuntimeFactProvider,
    RuntimeTargetAdvanceResult,
    RuntimeTargetInputs,
    RuntimeTargetRequest,
    advance_runtime_to_target,
    advance_runtime_to_target_with_provider,
    parse_runtime_target_inputs,
)
from .runtime_fact_provider import parse_runtime_target_request
from .schema import reject_binary_floats, validate_instance
from .social_runtime import SocialResponseState, validate_social_response_state
from .timeline import (
    append_actual_event,
    belonging_date,
    close_day,
    empty_day,
    should_close_day,
)
from .timeutil import (
    TOKYO,
    format_rfc3339,
    parse_rfc3339,
    require_canonical_timestamp,
    tokyo_date,
)

SOCIAL_RESPONSE_RELPATH = "state/social-responses.json"
LAST_RUN_RELPATH = "state/last-run.json"
OPERATOR_HISTORY_RELPATH = "state/operator-history.json"
LAST_OPERATOR_ACTION_RELPATH = "state/last-operator-action.json"

_HEX64_RE = re.compile(r"^[a-f0-9]{64}$")
_HEX40_RE = re.compile(r"^[a-f0-9]{40}$")
_DAY_FILE_RE = re.compile(r"^timeline/([0-9]{4}-[0-9]{2}-[0-9]{2})\.json$")
_SHARD_FILE_RE = re.compile(r"^camera-roll/records/([0-9]{4}-(?:0[1-9]|1[0-2]))\.json$")
_OPERATOR_ACTION_FILE_RE = re.compile(r"^operator/actions/([0-9a-f]{64})\.json$")

# Authoritative runtime JSON paths included in the semantic tree hash.
# Explicitly excludes state/last-run.json, state/last-operator-action.json,
# and all operational/Git/temp data.
SEMANTIC_FIXED_RELPATHS: tuple[str, ...] = (
    "state/current.json",
    SOCIAL_RESPONSE_RELPATH,
    OPERATOR_HISTORY_RELPATH,
    "schedule/state.json",
    "relations/state.json",
    "home/state.json",
    "consumables/state.json",
    "wardrobe/state.json",
    "finance/state.json",
)

ALLOWED_CANDIDATE_RELPATH_PREFIXES: tuple[str, ...] = (
    "state/current.json",
    SOCIAL_RESPONSE_RELPATH,
    OPERATOR_HISTORY_RELPATH,
    LAST_RUN_RELPATH,
    LAST_OPERATOR_ACTION_RELPATH,
    "schedule/state.json",
    "relations/state.json",
    "home/state.json",
    "consumables/state.json",
    "wardrobe/state.json",
    "finance/state.json",
    "timeline/",
    "camera-roll/records/",
    "operator/actions/",
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


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail(f"required file missing: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON at {path}: {exc}")
    if not isinstance(raw, dict):
        _fail(f"JSON root must be object: {path}")
    reject_binary_floats(raw, path=str(path))
    return raw


def write_runtime_json(path: Path, obj: Mapping[str, Any]) -> None:
    """Deterministic Foundation JSON writer for candidate trees."""
    normalized = normalize_persisted_object(dict(obj))
    reject_binary_floats(normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def empty_camera_roll_shard(month: str) -> dict[str, Any]:
    month_c = validate_camera_roll_shard_month(month)
    shard = {
        "schema_version": 1,
        "month": month_c,
        "closed_at": None,
        "records": [],
    }
    validate_instance(shard, "camera_roll_shard")
    return shard


def next_month_midnight(month: str) -> datetime:
    """First instant of the following Tokyo month at 00:00:00+09:00."""
    month_c = validate_camera_roll_shard_month(month)
    year = int(month_c[:4])
    mon = int(month_c[5:7])
    if mon == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, mon + 1, 1)
    return parse_rfc3339(f"{nxt.isoformat()}T00:00:00+09:00")


def tokyo_month_of(ts: str) -> str:
    dt = parse_rfc3339(require_canonical_timestamp(ts, field="timestamp"), field="timestamp")
    return validate_camera_roll_shard_month(f"{dt.year:04d}-{dt.month:02d}")


def _resolve_root(root: Path | str, *, label: str) -> Path:
    path = Path(root)
    if path.exists() and path.is_symlink():
        _fail(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        _fail(f"{label} is not a directory: {path}")
    # Reject path escape via linked parents when resolving.
    resolved = path.resolve(strict=False)
    if path.exists():
        for child in path.rglob("*"):
            if child.is_symlink():
                _fail(f"{label} contains symlink (path escape reject): {child}")
    return resolved


def is_allowed_runtime_relpath(rel: str) -> bool:
    """Return True iff ``rel`` is an exact 5C runtime persistent path.

    Public behavior-preserving export of the candidate/base path contract used by
    5C1 and 5C2. Rejects code/canon/debug/operational paths.
    """
    if rel in ALLOWED_CANDIDATE_RELPATH_PREFIXES:
        return True
    if rel.startswith("timeline/") and _DAY_FILE_RE.fullmatch(rel):
        return True
    if rel.startswith("camera-roll/records/") and _SHARD_FILE_RE.fullmatch(rel):
        return True
    if rel.startswith("operator/actions/") and _OPERATOR_ACTION_FILE_RE.fullmatch(rel):
        return True
    return False


def _is_allowed_candidate_relpath(rel: str) -> bool:
    return is_allowed_runtime_relpath(rel)


def _iter_json_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out.append(path)
    return out


def _relpath(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace(os.sep, "/")


def list_runtime_files(root: Path | str) -> dict[str, bytes]:
    """Byte map of all files under root (sorted keys)."""
    root_path = Path(root)
    out: dict[str, bytes] = {}
    for path in _iter_json_files(root_path):
        rel = _relpath(root_path, path)
        out[rel] = path.read_bytes()
    return out


def snapshot_tree_bytes(root: Path | str) -> dict[str, str]:
    """SHA-256 fingerprint map for baseline isolation checks."""
    import hashlib

    return {
        rel: hashlib.sha256(data).hexdigest()
        for rel, data in list_runtime_files(root).items()
    }


def _load_timeline_days(root: Path) -> dict[str, dict[str, Any]]:
    timeline_dir = root / "timeline"
    days: dict[str, dict[str, Any]] = {}
    if not timeline_dir.exists():
        return days
    if not timeline_dir.is_dir():
        _fail("timeline/ exists but is not a directory")
    for path in sorted(timeline_dir.glob("*.json")):
        if path.is_symlink():
            _fail(f"timeline day symlink rejected: {path}")
        rel = _relpath(root, path)
        match = _DAY_FILE_RE.fullmatch(rel)
        if match is None:
            _fail(f"unexpected timeline path: {rel}")
        day = _read_json_object(path)
        validate_instance(day, "timeline_day")
        date_str = match.group(1)
        if day.get("date") != date_str:
            _fail(f"timeline day date mismatch: file={date_str} body={day.get('date')!r}")
        days[date_str] = day
    return days


def _load_camera_shards(root: Path) -> dict[str, dict[str, Any]]:
    shard_dir = root / "camera-roll" / "records"
    shards: dict[str, dict[str, Any]] = {}
    if not shard_dir.exists():
        return shards
    if not shard_dir.is_dir():
        _fail("camera-roll/records/ exists but is not a directory")
    for path in sorted(shard_dir.glob("*.json")):
        if path.is_symlink():
            _fail(f"camera shard symlink rejected: {path}")
        rel = _relpath(root, path)
        match = _SHARD_FILE_RE.fullmatch(rel)
        if match is None:
            _fail(f"unexpected camera-roll path: {rel}")
        shard = _read_json_object(path)
        validate_instance(shard, "camera_roll_shard")
        month = match.group(1)
        if shard.get("month") != month:
            _fail(f"camera shard month mismatch: file={month} body={shard.get('month')!r}")
        shards[month] = shard
    return shards


def _project_expected_camera_records(
    days: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """capture_id -> projected CameraRollRecord across all finalized events."""
    expected: dict[str, dict[str, Any]] = {}
    for day in sorted(days.values(), key=lambda d: d["date"]):
        for event in day.get("actual_events") or []:
            for record in project_camera_roll_records(event):
                cid = record["capture_id"]
                rid = record["record_id"]
                if cid in expected:
                    _fail(f"duplicate capture_id across timeline ledger: {cid}")
                if any(r["record_id"] == rid for r in expected.values()):
                    _fail(f"duplicate record_id across timeline ledger: {rid}")
                expected[cid] = record
    return expected


def _collect_stored_camera_records(
    shards: Mapping[str, Mapping[str, Any]],
) -> dict[str, tuple[str, dict[str, Any]]]:
    """capture_id -> (month, record)."""
    stored: dict[str, tuple[str, dict[str, Any]]] = {}
    seen_record_ids: set[str] = set()
    for month, shard in sorted(shards.items()):
        for raw in shard.get("records") or []:
            if not isinstance(raw, Mapping):
                _fail("camera roll record must be object")
            rec = dict(raw)
            cid = rec.get("capture_id")
            rid = rec.get("record_id")
            if not isinstance(cid, str) or not cid:
                _fail("camera roll record missing capture_id")
            if not isinstance(rid, str) or not rid:
                _fail("camera roll record missing record_id")
            if cid in stored:
                _fail(f"duplicate capture_id across camera shards: {cid}")
            if rid in seen_record_ids:
                _fail(f"duplicate record_id across camera shards: {rid}")
            seen_record_ids.add(rid)
            expected_month = camera_roll_shard_month(str(rec.get("captured_at") or ""))
            if expected_month != month:
                _fail(
                    f"record month ownership mismatch: capture_id={cid} "
                    f"expected_shard={expected_month} stored_shard={month}"
                )
            stored[cid] = (month, rec)
    return stored


def verify_timeline_camera_integrity(
    *,
    days: Mapping[str, Mapping[str, Any]],
    shards: Mapping[str, Mapping[str, Any]],
) -> None:
    """Exact set equality: every finalized Capture ↔ one pure-projected record."""
    expected = _project_expected_camera_records(days)
    stored = _collect_stored_camera_records(shards)
    expected_ids = set(expected)
    stored_ids = set(stored)
    missing = sorted(expected_ids - stored_ids)
    orphans = sorted(stored_ids - expected_ids)
    if missing:
        _fail(f"finalized Capture missing CameraRollRecord: {missing}")
    if orphans:
        _fail(f"orphan CameraRollRecord (no finalized Capture): {orphans}")
    for cid in sorted(expected_ids):
        exp = expected[cid]
        _month, got = stored[cid]
        if canonical_hash(normalize_persisted_object(exp)) != canonical_hash(
            normalize_persisted_object(got)
        ):
            _fail(f"CameraRollRecord semantic mismatch for capture_id={cid}")
        expected_path = camera_roll_shard_path(exp["captured_at"])
        actual_path = f"camera-roll/records/{_month}.json"
        if expected_path != actual_path:
            _fail(
                f"wrong shard path for capture_id={cid}: "
                f"expected={expected_path} actual={actual_path}"
            )


def active_has_pending_capture_in_month(
    current_state: Mapping[str, Any],
    month: str,
) -> bool:
    """True when ActiveActivity has a pending_capture belonging to month M."""
    active = current_state.get("context", {}).get("active_activity")
    if active is None:
        return False
    pending = active.get("pending_captures") or []
    if not isinstance(pending, list):
        _fail("active_activity.pending_captures must be array")
    month_c = validate_camera_roll_shard_month(month)
    for cap in pending:
        if not isinstance(cap, Mapping):
            _fail("pending_capture must be object")
        captured_at = cap.get("captured_at")
        if not isinstance(captured_at, str):
            _fail("pending_capture.captured_at required")
        if camera_roll_shard_month(captured_at) == month_c:
            return True
    return False


def is_camera_month_safely_closable(
    *,
    month: str,
    processed_through: str,
    current_state: Mapping[str, Any],
    pending_additions_for_month: Sequence[Mapping[str, Any]] = (),
) -> bool:
    """Safe close predicate (§10).

    Month M may close only when:
    1. M is strictly before the Tokyo month of final processed_through
    2. all pending additions for M are already merged (caller responsibility)
    3. ActiveActivity has no pending_capture whose captured_at belongs to M
    """
    month_c = validate_camera_roll_shard_month(month)
    processed_month = tokyo_month_of(processed_through)
    if month_c >= processed_month:
        return False
    if pending_additions_for_month:
        # Caller must merge first; presence of unmerged additions blocks close.
        return False
    if active_has_pending_capture_in_month(current_state, month_c):
        return False
    return True


def compute_camera_month_closed_at(month: str) -> str:
    return format_rfc3339(next_month_midnight(month))


def _verify_no_stale_closable_days(
    *,
    current_state: Mapping[str, Any],
    days: Mapping[str, Mapping[str, Any]],
) -> None:
    as_of = parse_rfc3339(
        require_canonical_timestamp(
            current_state["processed_through"], field="processed_through"
        ),
        field="processed_through",
    )
    for day in days.values():
        if should_close_day(dict(current_state), dict(day), as_of=as_of):
            _fail(
                f"stale closable OPEN timeline day at baseline processed_through: "
                f"{day.get('date')}"
            )


def _verify_no_stale_closable_shards(
    *,
    current_state: Mapping[str, Any],
    shards: Mapping[str, Mapping[str, Any]],
) -> None:
    """Validate OPEN and CLOSED Camera shard semantics (review correction §A)."""
    processed = require_canonical_timestamp(
        current_state["processed_through"], field="processed_through"
    )
    processed_month = tokyo_month_of(processed)
    for month, shard in shards.items():
        # Semantic path: normalize via merge with empty additions; require equality.
        normalized = merge_camera_roll_shard(shard, [])
        if canonical_hash(normalize_persisted_object(dict(shard))) != canonical_hash(
            normalize_persisted_object(dict(normalized))
        ):
            _fail(
                f"camera shard fails semantic canonical equality "
                f"(merge_camera_roll_shard empty): {month}"
            )

        closed_at = shard.get("closed_at")
        if closed_at is None:
            if is_camera_month_safely_closable(
                month=month,
                processed_through=processed,
                current_state=current_state,
                pending_additions_for_month=(),
            ):
                _fail(f"stale safely-closable camera shard still open: {month}")
            continue

        # CLOSED shard invariants.
        expected_closed = compute_camera_month_closed_at(month)
        if closed_at != expected_closed:
            _fail(
                f"CLOSED camera shard closed_at mismatch: month={month} "
                f"stored={closed_at!r} expected={expected_closed!r}"
            )
        if month >= processed_month:
            _fail(
                f"CLOSED camera shard month must be strictly before processed month: "
                f"month={month} processed_month={processed_month}"
            )
        if active_has_pending_capture_in_month(current_state, month):
            _fail(
                f"CLOSED camera shard incompatible with active pending_capture "
                f"in same month: {month}"
            )


AUTHORITATIVE_NAMESPACE_PREFIXES: tuple[str, ...] = (
    "state/",
    "schedule/",
    "relations/",
    "home/",
    "consumables/",
    "wardrobe/",
    "finance/",
    "timeline/",
    "camera-roll/records/",
    "operator/",
)

# Explicitly reviewed non-authoritative legacy paths that may exist in fixtures
# and are ignored (not consulted) by 5C1 loaders.
LEGACY_IGNORED_PREFIXES: tuple[str, ...] = (
    "application-runtime/",
)


def _under_authoritative_namespace(rel: str) -> bool:
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in AUTHORITATIVE_NAMESPACE_PREFIXES)


def _verify_authoritative_runtime_paths(root: Path) -> None:
    """Fail closed on unexpected files under authoritative runtime namespaces."""
    for path in _iter_json_files(root):
        rel = _relpath(root, path)
        if any(rel.startswith(p) for p in LEGACY_IGNORED_PREFIXES):
            continue
        if not _under_authoritative_namespace(rel):
            # Non-authoritative paths outside reviewed legacy roots are ignored
            # for snapshot load (not consulted); they do not enter the semantic hash.
            continue
        if not _is_allowed_candidate_relpath(rel):
            _fail(f"unexpected file under authoritative runtime namespace: {rel}")
        name = path.name
        if name.endswith("~") or name.endswith(".tmp") or name.endswith(".bak"):
            _fail(f"temp/backup file under authoritative runtime namespace: {rel}")


def _semantic_mapping_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return canonical_hash(normalize_persisted_object(dict(left))) == canonical_hash(
        normalize_persisted_object(dict(right))
    )


def _write_runtime_json_if_semantic_changed(path: Path, obj: Mapping[str, Any]) -> bool:
    """Write only when missing or semantic content differs. Preserves baseline bytes."""
    if path.is_file():
        existing = _read_json_object(path)
        if _semantic_mapping_equal(existing, obj):
            return False
    write_runtime_json(path, obj)
    return True


def validate_runtime_last_run(raw: Mapping[str, Any]) -> dict[str, Any]:
    reject_binary_floats(raw)
    data = normalize_persisted_object(dict(raw))
    validate_instance(data, "runtime_last_run")
    if data.get("result") != "SUCCESS":
        _fail("runtime last-run result must be SUCCESS")
    _require_hex40(data["engine_commit_sha"], field="engine_commit_sha")
    _require_hex40(data["life_base_sha"], field="life_base_sha")
    _require_hex64(data["behavior_policy_hash"], field="behavior_policy_hash")
    _require_hex64(data["state_before_hash"], field="state_before_hash")
    _require_hex64(data["state_after_hash"], field="state_after_hash")
    require_canonical_timestamp(data["target_time"], field="target_time")
    require_canonical_timestamp(
        data["previous_processed_through"], field="previous_processed_through"
    )
    require_canonical_timestamp(data["processed_through"], field="processed_through")
    files = data.get("files_changed")
    if not isinstance(files, list):
        _fail("files_changed must be array")
    if files != sorted(files):
        _fail("files_changed must be sorted")
    if len(files) != len(set(files)):
        _fail("files_changed must not contain duplicates")
    # Forbidden observational payloads.
    forbidden_keys = {
        "commit_sha",
        "resulting_commit_sha",
        "world_seed",
        "decision_frames",
        "fact_tape",
        "state_dump",
        "prose_rationale",
        "rationale",
    }
    overlap = forbidden_keys.intersection(data)
    if overlap:
        _fail(f"last-run contains forbidden fields: {sorted(overlap)}")
    return data


def compute_behavior_policy_hash(behavior_policy: Mapping[str, Any]) -> str:
    """Deterministic canonical hash of the normalized policy used by C3."""
    reject_binary_floats(behavior_policy)
    return persisted_hash(dict(behavior_policy))


def _semantic_relpaths(root: Path) -> list[str]:
    paths: list[str] = []
    for rel in SEMANTIC_FIXED_RELPATHS:
        path = root / rel
        if path.is_file():
            paths.append(rel)
    timeline = root / "timeline"
    if timeline.is_dir():
        for path in sorted(timeline.glob("*.json")):
            rel = _relpath(root, path)
            if _DAY_FILE_RE.fullmatch(rel):
                paths.append(rel)
    shard_dir = root / "camera-roll" / "records"
    if shard_dir.is_dir():
        for path in sorted(shard_dir.glob("*.json")):
            rel = _relpath(root, path)
            if _SHARD_FILE_RE.fullmatch(rel):
                paths.append(rel)
    actions_dir = root / "operator" / "actions"
    if actions_dir.is_dir():
        for path in sorted(actions_dir.glob("*.json")):
            rel = _relpath(root, path)
            if _OPERATOR_ACTION_FILE_RE.fullmatch(rel):
                paths.append(rel)
    return paths


def compute_runtime_semantic_tree_hash(root_or_snapshot: Path | str | RuntimePersistentSnapshot) -> str:
    """Deterministic hash over authoritative runtime JSON (excludes last-run)."""
    if isinstance(root_or_snapshot, RuntimePersistentSnapshot):
        root = Path(root_or_snapshot.root)
    else:
        root = Path(root_or_snapshot)
    pairs: list[list[str]] = []
    for rel in _semantic_relpaths(root):
        obj = _read_json_object(root / rel)
        digest = persisted_hash(obj)
        pairs.append([rel, digest])
    pairs.sort(key=lambda p: p[0])
    return canonical_hash(pairs)


@dataclass(frozen=True)
class RuntimePersistentSnapshot:
    root: Path
    bundle: RuntimeBundle
    social_response_state: SocialResponseState
    timeline_days: Mapping[str, Mapping[str, Any]]
    camera_roll_shards: Mapping[str, Mapping[str, Any]]
    last_run: Mapping[str, Any] | None
    semantic_tree_hash: str
    operator_history: Mapping[str, Any] | None = None
    operator_actions: tuple[Mapping[str, Any], ...] = ()
    last_operator_action: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RuntimePersistenceResult:
    status: str  # NOOP | CHANGE
    baseline_snapshot: RuntimePersistentSnapshot
    candidate_snapshot: RuntimePersistentSnapshot | None
    runtime_result: RuntimeTargetAdvanceResult
    life_base_sha: str
    state_before_hash: str
    state_after_hash: str
    files_changed: tuple[str, ...]
    last_run: Mapping[str, Any] | None


def _bind_social_response(
    *,
    current_state: Mapping[str, Any],
    social: SocialResponseState,
) -> None:
    ref = current_state.get("domain_refs", {}).get("social_response_revision")
    if ref is None:
        _fail(
            "persistent snapshot requires non-null "
            "CurrentState.domain_refs.social_response_revision"
        )
    _require_hex64(ref, field="domain_refs.social_response_revision")
    if ref != social.revision:
        _fail(
            "domain_refs.social_response_revision must equal "
            f"SocialResponseState.revision (ref={ref} social={social.revision})"
        )
    if social.character_id != current_state["character_id"]:
        _fail("SocialResponseState.character_id must equal CurrentState.character_id")
    social_as_of = parse_rfc3339(social.as_of, field="as_of")
    processed = parse_rfc3339(
        require_canonical_timestamp(
            current_state["processed_through"], field="processed_through"
        ),
        field="processed_through",
    )
    if social_as_of > processed:
        _fail("SocialResponseState.as_of must be <= CurrentState.processed_through")


def _verify_last_operator_action_binding(
    *,
    last_operator_action: Mapping[str, Any] | None,
    current_state: Mapping[str, Any],
    semantic_tree_hash: str,
    operator_history_hash: str,
) -> None:
    if last_operator_action is None:
        return
    from .last_operator_action import validate_last_operator_action

    data = validate_last_operator_action(last_operator_action)
    if data["engine_commit_sha"] != current_state["engine_commit_sha"]:
        _fail("last-operator-action.engine_commit_sha mismatch")
    if data["behavior_policy_version"] != current_state["behavior_policy_version"]:
        _fail("last-operator-action.behavior_policy_version mismatch")
    rev_after = int(data["state_revision_after"])
    revision = int(current_state["state_revision"])
    if rev_after > revision:
        _fail("last-operator-action.state_revision_after is ahead of CurrentState")
    if rev_after == revision:
        if data["state_after_hash"] != semantic_tree_hash:
            _fail(
                "last-operator-action.state_after_hash mismatch vs "
                "recomputed semantic tree hash"
            )
        if data["operator_history_hash_after"] != operator_history_hash:
            _fail("last-operator-action.operator_history_hash_after mismatch")
        if data["processed_through_after"] != current_state["processed_through"]:
            _fail("last-operator-action.processed_through_after mismatch")


def _verify_last_run_binding(
    *,
    last_run: Mapping[str, Any] | None,
    current_state: Mapping[str, Any],
    semantic_tree_hash: str,
    operator_action_count: int = 0,
) -> None:
    revision = int(current_state["state_revision"])
    if type(operator_action_count) is not int or operator_action_count < 0:
        _fail("operator_action_count must be a non-negative integer")

    if last_run is None:
        if revision == 0:
            return
        # Operator-only actions (Correction) may bump revision without producing
        # state/last-run.json. Bound the allowed revision by verified operator
        # action count; arbitrary jumps still fail.
        if operator_action_count <= 0 or revision != operator_action_count:
            _fail(
                "state/last-run.json absent requires state_revision == "
                "operator_action_count for operator-only history"
            )
        return

    data = validate_runtime_last_run(last_run)
    if data["character_id"] != current_state["character_id"]:
        _fail("last-run.character_id mismatch")
    if data["engine_commit_sha"] != current_state["engine_commit_sha"]:
        _fail("last-run.engine_commit_sha mismatch")
    if data["behavior_policy_version"] != current_state["behavior_policy_version"]:
        _fail("last-run.behavior_policy_version mismatch")
    if data["processed_through"] != current_state["processed_through"]:
        _fail("last-run.processed_through mismatch")
    if data["history_head_after"] != current_state["history"]["head_event_id"]:
        _fail("last-run.history_head_after mismatch")

    last_revision = int(data["state_revision_after"])
    if last_revision > revision:
        _fail("last-run.state_revision_after is ahead of CurrentState")
    if last_revision == revision:
        if data["state_after_hash"] != semantic_tree_hash:
            _fail("last-run.state_after_hash mismatch vs recomputed semantic tree hash")
        return

    # A Correction is semantic state but state/last-run.json intentionally remains
    # the last *simulation* success record. Therefore it may become stale after
    # one or more verified operator actions. Bound the allowed revision lag by
    # the total verified operator action count; arbitrary revision jumps still fail.
    revision_lag = revision - last_revision
    if operator_action_count <= 0 or revision_lag > operator_action_count:
        _fail(
            "stale last-run revision is not explainable by verified operator actions"
        )


def load_runtime_persistent_snapshot(
    root: Path | str,
    *,
    reference_sets: RuntimeReferenceSets,
) -> RuntimePersistentSnapshot:
    """Load and fully verify a 5C persistent runtime snapshot (read-only)."""
    root_path = _resolve_root(root, label="runtime root")
    if not root_path.is_dir():
        _fail(f"runtime root is not a directory: {root_path}")

    # Required 5A files via existing loader.
    bundle = load_runtime_bundle(root_path, reference_sets=reference_sets)

    social_path = root_path / SOCIAL_RESPONSE_RELPATH
    if not social_path.is_file():
        _fail(f"missing SocialResponseState file: {SOCIAL_RESPONSE_RELPATH}")
    social_raw = _read_json_object(social_path)
    validate_instance(social_raw, "social_response_state")
    social = validate_social_response_state(social_raw)
    _bind_social_response(current_state=bundle.current_state, social=social)

    days = _load_timeline_days(root_path)
    # History already verified by load_runtime_bundle; re-assert collect consistency.
    events = collect_ledger_events(list(days.values()))
    verify_history(
        events,
        head_event_id=bundle.current_state["history"]["head_event_id"],
        history_hash=bundle.current_state["history"]["history_hash"],
        event_count=bundle.current_state["history"]["event_count"],
    )

    shards = _load_camera_shards(root_path)
    verify_timeline_camera_integrity(days=days, shards=shards)
    _verify_no_stale_closable_days(current_state=bundle.current_state, days=days)
    _verify_no_stale_closable_shards(current_state=bundle.current_state, shards=shards)

    last_run_path = root_path / LAST_RUN_RELPATH
    last_run: dict[str, Any] | None
    if last_run_path.is_file():
        last_run = _read_json_object(last_run_path)
    else:
        last_run = None

    last_op_path = root_path / LAST_OPERATOR_ACTION_RELPATH
    last_operator_action: dict[str, Any] | None
    if last_op_path.is_file():
        last_operator_action = _read_json_object(last_op_path)
    else:
        last_operator_action = None

    # Slice 5E1: verify operator ledger when present; missing ≡ empty.
    from .operator_action import load_operator_ledger

    operator_ledger = load_operator_ledger(root_path)

    semantic = compute_runtime_semantic_tree_hash(root_path)
    _verify_last_run_binding(
        last_run=last_run,
        current_state=bundle.current_state,
        semantic_tree_hash=semantic,
        operator_action_count=operator_ledger.action_count,
    )
    _verify_last_operator_action_binding(
        last_operator_action=last_operator_action,
        current_state=bundle.current_state,
        semantic_tree_hash=semantic,
        operator_history_hash=operator_ledger.operator_history_hash,
    )

    # Reject unexpected files under authoritative runtime namespaces.
    # Legacy application-runtime/** fixtures remain ignored/not consulted.
    _verify_authoritative_runtime_paths(root_path)

    return RuntimePersistentSnapshot(
        root=root_path,
        bundle=bundle,
        social_response_state=social,
        timeline_days={k: dict(v) for k, v in days.items()},
        camera_roll_shards={k: dict(v) for k, v in shards.items()},
        last_run=None if last_run is None else dict(last_run),
        semantic_tree_hash=semantic,
        operator_history=operator_ledger.as_history(),
        operator_actions=tuple(dict(a) for a in operator_ledger.actions),
        last_operator_action=(
            None if last_operator_action is None else dict(last_operator_action)
        ),
    )


def _copy_baseline_to_candidate(baseline: Path, candidate: Path) -> None:
    if candidate.exists():
        if any(candidate.iterdir()):
            _fail(f"candidate_root must be empty or nonexistent: {candidate}")
    else:
        candidate.mkdir(parents=True, exist_ok=True)
    for path in _iter_json_files(baseline):
        rel = _relpath(baseline, path)
        dest = candidate / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)


def _diff_files(baseline: Path, candidate: Path) -> list[str]:
    before = list_runtime_files(baseline)
    after = list_runtime_files(candidate)
    changed: list[str] = []
    for rel in sorted(set(before) | set(after)):
        if before.get(rel) != after.get(rel):
            changed.append(rel)
    return changed


def _materialize_bundle_files(candidate: Path, bundle: RuntimeBundle) -> None:
    """Write bundle JSON only when semantic content differs from copied baseline."""
    mapping = {
        "state/current.json": bundle.current_state,
        "schedule/state.json": bundle.schedule_state,
        "relations/state.json": bundle.relation_state,
        "home/state.json": bundle.home_state,
        "consumables/state.json": bundle.consumables_state,
        "wardrobe/state.json": bundle.wardrobe_state,
        "finance/state.json": bundle.finance_state,
    }
    for rel, obj in mapping.items():
        _write_runtime_json_if_semantic_changed(candidate / rel, obj)


def _apply_timeline_events(
    *,
    baseline_days: Mapping[str, Mapping[str, Any]],
    actual_events: Sequence[Mapping[str, Any]],
    final_current: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    days: dict[str, dict[str, Any]] = {
        date_str: deepcopy(dict(day)) for date_str, day in baseline_days.items()
    }
    baseline_event_ids = {
        e["event_id"]
        for day in baseline_days.values()
        for e in (day.get("actual_events") or [])
    }
    for raw_event in actual_events:
        event = dict(raw_event)
        eid = event.get("event_id")
        if not isinstance(eid, str) or not eid:
            _fail("C3 ActualEvent missing event_id")
        if eid in baseline_event_ids:
            _fail(f"duplicate baseline event_id reject: {eid}")
        date_str = belonging_date(str(event["actual_start"]))
        day = days.get(date_str)
        if day is None:
            day = empty_day(date_str)
        if day.get("status") == "CLOSED":
            _fail(f"closed day new event reject: {date_str}", code=ErrorCode.CLOSED_DAY_MUTATION)
        day = append_actual_event(day, event)
        days[date_str] = day
        baseline_event_ids.add(eid)

    as_of = parse_rfc3339(
        require_canonical_timestamp(
            final_current["processed_through"], field="processed_through"
        ),
        field="processed_through",
    )
    for date_str, day in list(days.items()):
        if should_close_day(dict(final_current), day, as_of=as_of):
            days[date_str] = close_day(day)

    # Candidate history must match C3 final CurrentState.history.
    events = collect_ledger_events(list(days.values()))
    verify_history(
        events,
        head_event_id=final_current["history"]["head_event_id"],
        history_hash=final_current["history"]["history_hash"],
        event_count=final_current["history"]["event_count"],
    )
    return days


def _independently_project_c3_records(
    actual_events: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    records: list[dict[str, Any]] = []
    by_path: dict[str, list[dict[str, Any]]] = {}
    for event in actual_events:
        for rec in project_camera_roll_records(event):
            records.append(rec)
            path = camera_roll_shard_path(rec["captured_at"])
            by_path.setdefault(path, []).append(rec)
    records.sort(key=lambda r: (r["captured_at"], r["record_id"]))
    for path in by_path:
        by_path[path].sort(key=lambda r: (r["captured_at"], r["record_id"]))
    return records, by_path


def _records_equal(
    left: Sequence[Mapping[str, Any]],
    right: Sequence[Mapping[str, Any]],
) -> bool:
    if len(left) != len(right):
        return False
    for a, b in zip(left, right, strict=True):
        if canonical_hash(normalize_persisted_object(dict(a))) != canonical_hash(
            normalize_persisted_object(dict(b))
        ):
            return False
    return True


def _apply_camera_shards(
    *,
    baseline_shards: Mapping[str, Mapping[str, Any]],
    records_by_path: Mapping[str, Sequence[Mapping[str, Any]]],
    final_current: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], int]:
    shards: dict[str, dict[str, Any]] = {
        month: deepcopy(dict(shard)) for month, shard in baseline_shards.items()
    }
    baseline_record_ids: set[str] = set()
    for shard in baseline_shards.values():
        for rec in shard.get("records") or []:
            baseline_record_ids.add(str(rec["record_id"]))

    added_count = 0
    # Merge all C3 additions first.
    for path, additions in sorted(records_by_path.items()):
        match = _SHARD_FILE_RE.fullmatch(path)
        if match is None:
            _fail(f"invalid camera shard path from C3: {path}")
        month = match.group(1)
        shard = shards.get(month)
        if shard is None:
            shard = empty_camera_roll_shard(month)
        before_ids = {str(r["record_id"]) for r in (shard.get("records") or [])}
        merged = merge_camera_roll_shard(shard, additions)
        after_ids = {str(r["record_id"]) for r in (merged.get("records") or [])}
        for rid in after_ids - before_ids:
            if rid not in baseline_record_ids:
                added_count += 1
        shards[month] = merged

    # Deterministic safe month close after merges.
    processed = require_canonical_timestamp(
        final_current["processed_through"], field="processed_through"
    )
    for month, shard in list(shards.items()):
        if shard.get("closed_at") is not None:
            continue
        if is_camera_month_safely_closable(
            month=month,
            processed_through=processed,
            current_state=final_current,
            pending_additions_for_month=(),
        ):
            closed = deepcopy(dict(shard))
            closed["closed_at"] = compute_camera_month_closed_at(month)
            validate_instance(closed, "camera_roll_shard")
            shards[month] = closed

    return shards, added_count


def _write_days(candidate: Path, days: Mapping[str, Mapping[str, Any]]) -> None:
    """Write timeline days only when semantic content differs; never delete-all."""
    for date_str, day in sorted(days.items()):
        path = candidate / "timeline" / f"{date_str}.json"
        _write_runtime_json_if_semantic_changed(path, day)


def _write_shards(candidate: Path, shards: Mapping[str, Mapping[str, Any]]) -> None:
    """Write Camera shards only when semantic content differs; never delete-all."""
    for month, shard in sorted(shards.items()):
        path = candidate / "camera-roll" / "records" / f"{month}.json"
        _write_runtime_json_if_semantic_changed(path, shard)


def _stage_pre_revision_candidate(
    *,
    candidate: Path,
    baseline_snapshot: RuntimePersistentSnapshot,
    runtime_result: RuntimeTargetAdvanceResult,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], int, RuntimeBundle]:
    """Apply C3 results into candidate with baseline state_revision still intact."""
    baseline_revision = int(baseline_snapshot.bundle.current_state["state_revision"])
    c3_revision = int(runtime_result.bundle.current_state["state_revision"])
    if c3_revision != baseline_revision:
        _fail(
            "C3 must leave CurrentState.state_revision unchanged "
            f"(baseline={baseline_revision} c3={c3_revision})"
        )

    final_social = runtime_result.social_response_state
    final_bundle_parts = {
        "current_state": dict(runtime_result.bundle.current_state),
        "schedule_state": dict(runtime_result.bundle.schedule_state),
        "relation_state": dict(runtime_result.bundle.relation_state),
        "home_state": dict(runtime_result.bundle.home_state),
        "consumables_state": dict(runtime_result.bundle.consumables_state),
        "wardrobe_state": dict(runtime_result.bundle.wardrobe_state),
        "finance_state": dict(runtime_result.bundle.finance_state),
    }
    # Bind social_response_revision; keep verified baseline state_revision for NOOP.
    current = deepcopy(final_bundle_parts["current_state"])
    current["state_revision"] = baseline_revision
    current["domain_refs"] = dict(current["domain_refs"])
    current["domain_refs"]["social_response_revision"] = final_social.revision
    final_bundle_parts["current_state"] = current

    projected, by_path = _independently_project_c3_records(runtime_result.actual_events)
    c3_records = [dict(r) for r in runtime_result.camera_roll_records]
    c3_by_path = {
        path: [dict(r) for r in rows]
        for path, rows in runtime_result.camera_roll_records_by_shard_path.items()
    }
    if not _records_equal(projected, c3_records):
        _fail("C3 camera_roll_records mismatch vs independent projection")
    if set(by_path) != set(c3_by_path):
        _fail("C3 camera_roll_records_by_shard_path keys mismatch")
    for path in sorted(by_path):
        if not _records_equal(by_path[path], c3_by_path[path]):
            _fail(f"C3 camera grouping mismatch at {path}")

    days = _apply_timeline_events(
        baseline_days=baseline_snapshot.timeline_days,
        actual_events=runtime_result.actual_events,
        final_current=current,
    )
    shards, added_count = _apply_camera_shards(
        baseline_shards=baseline_snapshot.camera_roll_shards,
        records_by_path=by_path,
        final_current=current,
    )
    verify_timeline_camera_integrity(days=days, shards=shards)

    # Byte-preserving writes: only rewrite when semantic content changes.
    _write_runtime_json_if_semantic_changed(
        candidate / SOCIAL_RESPONSE_RELPATH, final_social.as_dict()
    )
    staged_bundle = RuntimeBundle(
        current_state=current,
        schedule_state=final_bundle_parts["schedule_state"],
        relation_state=final_bundle_parts["relation_state"],
        home_state=final_bundle_parts["home_state"],
        consumables_state=final_bundle_parts["consumables_state"],
        wardrobe_state=final_bundle_parts["wardrobe_state"],
        finance_state=final_bundle_parts["finance_state"],
    )
    _materialize_bundle_files(candidate, staged_bundle)
    _write_days(candidate, days)
    _write_shards(candidate, shards)

    return days, shards, added_count, staged_bundle


def _build_last_run(
    *,
    baseline_snapshot: RuntimePersistentSnapshot,
    runtime_result: RuntimeTargetAdvanceResult,
    target_time: str,
    behavior_policy: Mapping[str, Any],
    life_base_sha: str,
    state_before_hash: str,
    state_after_hash: str,
    state_revision_before: int,
    state_revision_after: int,
    capture_records_added_count: int,
    files_changed: Sequence[str],
) -> dict[str, Any]:
    current = runtime_result.bundle.current_state
    # After bump, caller passes the bumped current via state_revision_after;
    # history heads come from C3 final bundle (unchanged by revision bump).
    final_current = dict(current)
    final_current["state_revision"] = state_revision_after
    final_current["domain_refs"] = dict(final_current["domain_refs"])
    final_current["domain_refs"]["social_response_revision"] = (
        runtime_result.social_response_state.revision
    )

    run_id = stable_id(
        "runtime-run",
        baseline_snapshot.bundle.current_state["character_id"],
        life_base_sha,
        baseline_snapshot.bundle.current_state["processed_through"],
        target_time,
        state_before_hash,
        state_after_hash,
    )
    last_run = {
        "schema_version": 1,
        "result": "SUCCESS",
        "run_id": run_id,
        "character_id": baseline_snapshot.bundle.current_state["character_id"],
        "engine_commit_sha": baseline_snapshot.bundle.current_state["engine_commit_sha"],
        "behavior_policy_version": baseline_snapshot.bundle.current_state[
            "behavior_policy_version"
        ],
        "behavior_policy_hash": compute_behavior_policy_hash(behavior_policy),
        "life_base_sha": life_base_sha,
        "target_time": target_time,
        "previous_processed_through": baseline_snapshot.bundle.current_state[
            "processed_through"
        ],
        "processed_through": final_current["processed_through"],
        "state_revision_before": state_revision_before,
        "state_revision_after": state_revision_after,
        "state_before_hash": state_before_hash,
        "state_after_hash": state_after_hash,
        "history_head_before": baseline_snapshot.bundle.current_state["history"][
            "head_event_id"
        ],
        "history_head_after": final_current["history"]["head_event_id"],
        "scheduler_events_processed_count": len(runtime_result.consumed_trigger_ids),
        "finalized_actual_events_count": len(runtime_result.actual_events),
        "capture_records_added_count": capture_records_added_count,
        "microsteps_used": runtime_result.microsteps_used,
        "files_changed": list(files_changed),
    }
    return validate_runtime_last_run(last_run)


def _verify_closed_history_immutability(
    *,
    baseline: RuntimePersistentSnapshot,
    candidate_days: Mapping[str, Mapping[str, Any]],
    candidate_shards: Mapping[str, Mapping[str, Any]],
    baseline_root: Path,
    candidate_root: Path,
) -> None:
    for date_str, base_day in baseline.timeline_days.items():
        if base_day.get("status") != "CLOSED":
            continue
        cand = candidate_days.get(date_str)
        if cand is None:
            _fail(f"closed timeline day missing in candidate: {date_str}")
        if not _semantic_mapping_equal(dict(base_day), dict(cand)):
            _fail(f"closed timeline day mutated: {date_str}")
        base_path = baseline_root / "timeline" / f"{date_str}.json"
        cand_path = candidate_root / "timeline" / f"{date_str}.json"
        if base_path.read_bytes() != cand_path.read_bytes():
            _fail(f"closed timeline day bytes mutated: {date_str}")

    for month, base_shard in baseline.camera_roll_shards.items():
        if base_shard.get("closed_at") is None:
            continue
        cand = candidate_shards.get(month)
        if cand is None:
            _fail(f"closed camera shard missing in candidate: {month}")
        if not _semantic_mapping_equal(dict(base_shard), dict(cand)):
            _fail(f"closed camera shard mutated: {month}")
        base_path = baseline_root / "camera-roll" / "records" / f"{month}.json"
        cand_path = candidate_root / "camera-roll" / "records" / f"{month}.json"
        if base_path.read_bytes() != cand_path.read_bytes():
            _fail(f"closed camera shard bytes mutated: {month}")

    # Prior ActualEvent immutability (by event_id).
    base_events = {
        e["event_id"]: e
        for day in baseline.timeline_days.values()
        for e in (day.get("actual_events") or [])
    }
    cand_events = {
        e["event_id"]: e
        for day in candidate_days.values()
        for e in (day.get("actual_events") or [])
    }
    for eid, base_event in base_events.items():
        cand_event = cand_events.get(eid)
        if cand_event is None:
            _fail(f"prior ActualEvent missing in candidate: {eid}")
        if not _semantic_mapping_equal(dict(base_event), dict(cand_event)):
            _fail(f"prior ActualEvent mutated: {eid}")


def _verify_candidate_paths(candidate: Path) -> None:
    for path in _iter_json_files(candidate):
        rel = _relpath(candidate, path)
        if not _is_allowed_candidate_relpath(rel):
            _fail(f"forbidden path in candidate tree: {rel}")
        # No temp/backup suffixes.
        name = path.name
        if name.endswith("~") or name.endswith(".tmp") or name.endswith(".bak"):
            _fail(f"temp/backup file in candidate tree: {rel}")


def build_runtime_candidate_tree(
    *,
    baseline_root: Path | str,
    candidate_root: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    target_inputs: RuntimeTargetInputs | Mapping[str, Any],
    executing_engine_commit_sha: str,
    life_base_sha: str,
) -> RuntimePersistenceResult:
    """Build a candidate tree using the existing precomputed C3 input tape."""

    def _run_c3(
        snapshot: RuntimePersistentSnapshot,
        policy: Mapping[str, Any],
    ) -> tuple[str, RuntimeTargetAdvanceResult]:
        parsed_inputs = parse_runtime_target_inputs(target_inputs)
        result = advance_runtime_to_target(
            bundle=snapshot.bundle,
            reference_sets=reference_sets,
            behavior_policy=policy,
            social_response_state=snapshot.social_response_state,
            target_inputs=parsed_inputs,
        )
        return parsed_inputs.target_time, result

    return _build_runtime_candidate_tree_core(
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        executing_engine_commit_sha=executing_engine_commit_sha,
        life_base_sha=life_base_sha,
        runtime_advance=_run_c3,
    )


def build_runtime_candidate_tree_with_provider(
    *,
    baseline_root: Path | str,
    candidate_root: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    target_request: RuntimeTargetRequest | Mapping[str, Any],
    fact_provider: RuntimeFactProvider,
    executing_engine_commit_sha: str,
    life_base_sha: str,
) -> RuntimePersistenceResult:
    """Build a candidate tree using on-demand provider-driven C3 facts."""

    def _run_c3(
        snapshot: RuntimePersistentSnapshot,
        policy: Mapping[str, Any],
    ) -> tuple[str, RuntimeTargetAdvanceResult]:
        request = parse_runtime_target_request(target_request)
        result = advance_runtime_to_target_with_provider(
            bundle=snapshot.bundle,
            reference_sets=reference_sets,
            behavior_policy=policy,
            social_response_state=snapshot.social_response_state,
            target_request=request,
            fact_provider=fact_provider,
        )
        return request.target_time, result

    return _build_runtime_candidate_tree_core(
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        executing_engine_commit_sha=executing_engine_commit_sha,
        life_base_sha=life_base_sha,
        runtime_advance=_run_c3,
    )


def build_runtime_candidate_tree_with_provider_factory(
    *,
    baseline_root: Path | str,
    candidate_root: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    target_request: RuntimeTargetRequest | Mapping[str, Any],
    fact_provider_factory: Callable[[RuntimePersistentSnapshot], RuntimeFactProvider],
    executing_engine_commit_sha: str,
    life_base_sha: str,
) -> RuntimePersistenceResult:
    """Build a provider-driven candidate after the exact baseline snapshot exists.

    The factory is invoked exactly once, after the frozen baseline has been
    fully loaded/verified with the supplied reference sets and before C3 runs.
    Existing static and already-built-provider entry points remain unchanged.
    """

    if not callable(fact_provider_factory):
        _fail("fact_provider_factory must be callable")

    calls = 0

    def _run_c3(
        snapshot: RuntimePersistentSnapshot,
        policy: Mapping[str, Any],
    ) -> tuple[str, RuntimeTargetAdvanceResult]:
        nonlocal calls
        if calls != 0:
            _fail("fact_provider_factory must be invoked exactly once")
        provider = fact_provider_factory(snapshot)
        calls += 1
        request = parse_runtime_target_request(target_request)
        result = advance_runtime_to_target_with_provider(
            bundle=snapshot.bundle,
            reference_sets=reference_sets,
            behavior_policy=policy,
            social_response_state=snapshot.social_response_state,
            target_request=request,
            fact_provider=provider,
        )
        return request.target_time, result

    return _build_runtime_candidate_tree_core(
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        executing_engine_commit_sha=executing_engine_commit_sha,
        life_base_sha=life_base_sha,
        runtime_advance=_run_c3,
    )


def _build_runtime_candidate_tree_core(
    *,
    baseline_root: Path | str,
    candidate_root: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    executing_engine_commit_sha: str,
    life_base_sha: str,
    runtime_advance: Callable[
        [RuntimePersistentSnapshot, Mapping[str, Any]],
        tuple[str, RuntimeTargetAdvanceResult],
    ],
    require_change: bool = False,
    pre_revision_semantic_augment: (
        Callable[
            [Path, RuntimePersistentSnapshot, RuntimeBundle, RuntimeTargetAdvanceResult],
            None,
        ]
        | None
    ) = None,
    post_hash_observability_augment: (
        Callable[
            [
                Path,
                RuntimePersistentSnapshot,
                RuntimeTargetAdvanceResult,
                str,
                str,
                int,
                int,
                list[str],
            ],
            list[str],
        ]
        | None
    ) = None,
) -> RuntimePersistenceResult:
    """Shared 5C1 persistence semantics for static and provider-driven C3.

    Optional 5E2 recovery hooks:
    - ``pre_revision_semantic_augment`` stages semantic operator ledger files
      after ordinary C3 pre-revision materialization and before the NOOP check /
      single revision bump.
    - ``post_hash_observability_augment`` may append non-semantic observability
      paths (e.g. last-operator-action) into ``files_changed`` before last-run
      is sealed; it must write those files itself when needed.
    - ``require_change`` rejects ordinary semantic NOOP (recovery fail-closed).
    """

    baseline_path = _resolve_root(baseline_root, label="baseline_root")
    candidate_path = Path(candidate_root)
    if candidate_path.exists() and candidate_path.is_symlink():
        _fail(f"candidate_root must not be a symlink: {candidate_path}")
    candidate_resolved = candidate_path.resolve(strict=False)
    baseline_resolved = baseline_path.resolve(strict=False)
    if candidate_resolved == baseline_resolved:
        _fail("candidate_root must be distinct from baseline_root")
    # Prevent candidate nested inside baseline or vice versa.
    try:
        candidate_resolved.relative_to(baseline_resolved)
        _fail("candidate_root must not be inside baseline_root")
    except ValueError:
        pass
    try:
        baseline_resolved.relative_to(candidate_resolved)
        _fail("baseline_root must not be inside candidate_root")
    except ValueError:
        pass

    life_base = _require_hex40(life_base_sha, field="life_base_sha")
    exec_sha = _require_hex40(executing_engine_commit_sha, field="executing_engine_commit_sha")

    baseline_bytes_before = snapshot_tree_bytes(baseline_path)
    try:
        baseline_snapshot = load_runtime_persistent_snapshot(
            baseline_path, reference_sets=reference_sets
        )
        if exec_sha != baseline_snapshot.bundle.current_state["engine_commit_sha"]:
            _fail(
                "executing_engine_commit_sha must equal "
                "CurrentState.engine_commit_sha"
            )

        policy = dict(behavior_policy)
        reject_binary_floats(policy)

        # C3 is the sole behavior transition authority. The adapter returns the
        # validated target time together with exactly one C3 result.
        target_time, runtime_result = runtime_advance(baseline_snapshot, policy)
        target_time = require_canonical_timestamp(target_time, field="target_time")

        _copy_baseline_to_candidate(baseline_path, candidate_path)
        days, shards, added_count, staged_bundle = _stage_pre_revision_candidate(
            candidate=candidate_path,
            baseline_snapshot=baseline_snapshot,
            runtime_result=runtime_result,
        )

        if pre_revision_semantic_augment is not None:
            pre_revision_semantic_augment(
                candidate_path,
                baseline_snapshot,
                staged_bundle,
                runtime_result,
            )

        state_before_hash = baseline_snapshot.semantic_tree_hash
        staged_hash = compute_runtime_semantic_tree_hash(candidate_path)

        # Semantic NOOP: compare with state_revision still at baseline.
        if staged_hash == state_before_hash:
            if require_change:
                _fail(
                    "required CHANGE path produced semantic NOOP "
                    "(recovery/operator advance failed closed)"
                )
            # Restore baseline last-run bytes (NOOP must not rewrite last-run).
            # Candidate may exist for inspection but is not publishable.
            # Ensure candidate last-run matches baseline (copied already).
            if snapshot_tree_bytes(baseline_path) != baseline_bytes_before:
                _fail("baseline root mutated during NOOP path")
            return RuntimePersistenceResult(
                status="NOOP",
                baseline_snapshot=baseline_snapshot,
                candidate_snapshot=None,
                runtime_result=runtime_result,
                life_base_sha=life_base,
                state_before_hash=state_before_hash,
                state_after_hash=state_before_hash,
                files_changed=(),
                last_run=None,
            )

        # CHANGE: bump state_revision exactly once.
        rev_before = int(baseline_snapshot.bundle.current_state["state_revision"])
        bumped_current = bump_revision(dict(staged_bundle.current_state))
        if int(bumped_current["state_revision"]) != rev_before + 1:
            _fail("state_revision must bump exactly +1 on CHANGE")
        bumped_current["domain_refs"] = dict(bumped_current["domain_refs"])
        bumped_current["domain_refs"]["social_response_revision"] = (
            runtime_result.social_response_state.revision
        )
        bumped_current = validate_checkpoint(bumped_current)

        final_bundle = validate_runtime_bundle(
            RuntimeBundle(
                current_state=bumped_current,
                schedule_state=staged_bundle.schedule_state,
                relation_state=staged_bundle.relation_state,
                home_state=staged_bundle.home_state,
                consumables_state=staged_bundle.consumables_state,
                wardrobe_state=staged_bundle.wardrobe_state,
                finance_state=staged_bundle.finance_state,
            ),
            reference_sets=reference_sets,
        )
        write_runtime_json(candidate_path / "state/current.json", final_bundle.current_state)

        state_after_hash = compute_runtime_semantic_tree_hash(candidate_path)

        # Provisional files_changed without last-run, then include last-run itself.
        provisional = _diff_files(baseline_path, candidate_path)
        if LAST_RUN_RELPATH not in provisional:
            provisional = sorted([*provisional, LAST_RUN_RELPATH])
        else:
            provisional = sorted(provisional)

        if post_hash_observability_augment is not None:
            provisional = list(
                post_hash_observability_augment(
                    candidate_path,
                    baseline_snapshot,
                    runtime_result,
                    state_before_hash,
                    state_after_hash,
                    rev_before,
                    rev_before + 1,
                    provisional,
                )
            )
            provisional = sorted(provisional)

        last_run = _build_last_run(
            baseline_snapshot=baseline_snapshot,
            runtime_result=runtime_result,
            target_time=target_time,
            behavior_policy=policy,
            life_base_sha=life_base,
            state_before_hash=state_before_hash,
            state_after_hash=state_after_hash,
            state_revision_before=rev_before,
            state_revision_after=rev_before + 1,
            capture_records_added_count=added_count,
            files_changed=provisional,
        )
        write_runtime_json(candidate_path / LAST_RUN_RELPATH, last_run)

        files_changed = tuple(_diff_files(baseline_path, candidate_path))
        if list(files_changed) != list(provisional):
            # Rebuild last-run with exact final files_changed (must be stable).
            last_run = _build_last_run(
                baseline_snapshot=baseline_snapshot,
                runtime_result=runtime_result,
                target_time=target_time,
                behavior_policy=policy,
                life_base_sha=life_base,
                state_before_hash=state_before_hash,
                state_after_hash=state_after_hash,
                state_revision_before=rev_before,
                state_revision_after=rev_before + 1,
                capture_records_added_count=added_count,
                files_changed=files_changed,
            )
            write_runtime_json(candidate_path / LAST_RUN_RELPATH, last_run)
            # Observability augment may need to rewrite last-operator-action with
            # the stabilized files_changed list.
            if post_hash_observability_augment is not None:
                files_changed = tuple(
                    post_hash_observability_augment(
                        candidate_path,
                        baseline_snapshot,
                        runtime_result,
                        state_before_hash,
                        state_after_hash,
                        rev_before,
                        rev_before + 1,
                        list(files_changed),
                    )
                )
                last_run = _build_last_run(
                    baseline_snapshot=baseline_snapshot,
                    runtime_result=runtime_result,
                    target_time=target_time,
                    behavior_policy=policy,
                    life_base_sha=life_base,
                    state_before_hash=state_before_hash,
                    state_after_hash=state_after_hash,
                    state_revision_before=rev_before,
                    state_revision_after=rev_before + 1,
                    capture_records_added_count=added_count,
                    files_changed=files_changed,
                )
                write_runtime_json(candidate_path / LAST_RUN_RELPATH, last_run)
            files_changed = tuple(_diff_files(baseline_path, candidate_path))
            if sorted(files_changed) != sorted(last_run["files_changed"]):
                _fail("files_changed did not stabilize after last-run write")

        _verify_candidate_paths(candidate_path)
        _verify_closed_history_immutability(
            baseline=baseline_snapshot,
            candidate_days=days,
            candidate_shards=shards,
            baseline_root=baseline_path,
            candidate_root=candidate_path,
        )

        # Full candidate reload / verify.
        candidate_snapshot = load_runtime_persistent_snapshot(
            candidate_path, reference_sets=reference_sets
        )
        if int(candidate_snapshot.bundle.current_state["state_revision"]) != rev_before + 1:
            _fail("candidate state_revision must be baseline+1")
        if candidate_snapshot.semantic_tree_hash != state_after_hash:
            _fail("candidate semantic hash mismatch after reload")
        if candidate_snapshot.last_run is None:
            _fail("CHANGE candidate missing last-run")
        if candidate_snapshot.last_run["state_after_hash"] != state_after_hash:
            _fail("last-run.state_after_hash mismatch after reload")
        if tuple(candidate_snapshot.last_run["files_changed"]) != files_changed:
            _fail("last-run.files_changed mismatch after reload")
        if (
            candidate_snapshot.social_response_state.revision
            != runtime_result.social_response_state.revision
        ):
            _fail("candidate social_response_revision bind mismatch")

        if snapshot_tree_bytes(baseline_path) != baseline_bytes_before:
            _fail("baseline root mutated during CHANGE path")

        return RuntimePersistenceResult(
            status="CHANGE",
            baseline_snapshot=baseline_snapshot,
            candidate_snapshot=candidate_snapshot,
            runtime_result=runtime_result,
            life_base_sha=life_base,
            state_before_hash=state_before_hash,
            state_after_hash=state_after_hash,
            files_changed=files_changed,
            last_run=dict(last_run),
        )
    except Exception:
        # Baseline must remain byte-identical on any failure.
        if snapshot_tree_bytes(baseline_path) != baseline_bytes_before:
            # Surface baseline mutation as a hard failure preference.
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "baseline root mutated during failed persistence attempt",
            )
        raise
