"""Slice 4A — Camera Roll v2 pure projection and monthly shard helpers.

No I/O, no clock, no PRNG, no materialization, no post selection.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_hash
from .captures import (
    compute_capture_context_hash,
    normalize_capture_kind,
    normalize_subject_refs,
    normalize_visual_context,
    validate_capture_kind_subject_semantics,
)
from .errors import ErrorCode, LifeEngineError
from .ids import stable_id
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339

_SHARD_MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


def derive_camera_roll_record_id(capture_id: str) -> str:
    if not isinstance(capture_id, str) or not capture_id:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture_id required")
    return stable_id("camera-roll-record", capture_id)


def validate_camera_roll_shard_month(month: object) -> str:
    """Reject impossible YYYY-MM values (00/13/99). Real month 01..12 only."""
    if not isinstance(month, str) or not month:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "shard.month required")
    if not _SHARD_MONTH_RE.fullmatch(month):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"invalid shard.month (must be real YYYY-MM 01..12): {month!r}",
        )
    return month


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return (str(record.get("captured_at") or ""), str(record.get("record_id") or ""))


def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    reject_binary_floats(record, path="camera_roll_record")
    data = dict(record)
    if data.get("schema_version") != 1:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_VERSION, "camera_roll_record.schema_version must be 1")
    capture_id = data.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "record.capture_id required")
    expected_id = derive_camera_roll_record_id(capture_id)
    if data.get("record_id") != expected_id:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"record_id mismatch: stored={data.get('record_id')!r} expected={expected_id!r}",
        )
    if not isinstance(data.get("captured_at"), str):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "record.captured_at required")
    captured = canonicalize_timestamp(data["captured_at"], field="captured_at")
    if "capture_kind" not in data:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "record.capture_kind required")
    if "subject_refs" not in data:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "record.subject_refs required")
    kind = normalize_capture_kind(data["capture_kind"])
    refs = normalize_subject_refs(data["subject_refs"])
    vc_raw = data.get("visual_context")
    if not isinstance(vc_raw, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "record.visual_context required")
    vc = normalize_visual_context(vc_raw)
    validate_capture_kind_subject_semantics(
        capture_kind=kind,
        subject_refs=refs,
        visual_context=vc,
    )
    expected_hash = compute_capture_context_hash(vc)
    if data.get("capture_context_hash") != expected_hash:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"record capture_context_hash mismatch: stored={data.get('capture_context_hash')!r}",
        )
    out = {
        "schema_version": 1,
        "record_id": expected_id,
        "capture_id": capture_id,
        "event_id": data.get("event_id"),
        "activity_instance_id": data.get("activity_instance_id"),
        "captured_at": captured,
        "capture_kind": kind,
        "subject_refs": refs,
        "visual_context": vc,
        "capture_context_hash": expected_hash,
    }
    validate_instance(out, "camera_roll_record")
    return out


def project_camera_roll_records(actual_event: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project finalized ActualEvent captures → immutable CameraRollRecord list.

    Uses the authoritative ActualEvent capture semantic validation boundary
    (``normalize_actual_event_for_persistence``) before projection.

    - zero captures => []
    - one capture => exactly one record
    - copies capture_kind / subject_refs exactly (no inference)
    - no input mutation
    - order by (captured_at, record_id)
    """
    from .events import normalize_actual_event_for_persistence

    reject_binary_floats(actual_event, path="actual_event")
    if not isinstance(actual_event, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "actual_event must be object")

    # Reject planned / unresolved / ActiveActivity-shaped objects.
    if "pending_captures" in actual_event and "event_id" not in actual_event:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "ActiveActivity pending captures cannot be projected as Camera Roll",
        )
    if "event_id" not in actual_event or "captures" not in actual_event:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "only finalized ActualEvent can be projected to Camera Roll",
        )

    # Authoritative semantic validation (owner/time/hash/uniqueness/order).
    event = normalize_actual_event_for_persistence(dict(actual_event))

    records: list[dict[str, Any]] = []
    seen_record_ids: set[str] = set()
    for cap in event.get("captures") or []:
        # Captures already validated; rebuild record from frozen fact.
        record_id = derive_camera_roll_record_id(cap["capture_id"])
        if record_id in seen_record_ids:
            raise LifeEngineError(
                ErrorCode.DUPLICATE_ID,
                f"duplicate camera roll record_id from captures: {record_id}",
            )
        seen_record_ids.add(record_id)
        record = {
            "schema_version": 1,
            "record_id": record_id,
            "capture_id": cap["capture_id"],
            "event_id": event["event_id"],
            "activity_instance_id": cap["activity_instance_id"],
            "captured_at": cap["captured_at"],
            "capture_kind": cap["capture_kind"],
            "subject_refs": list(cap["subject_refs"]),
            "visual_context": deepcopy(cap["visual_context"]),
            "capture_context_hash": cap["capture_context_hash"],
        }
        validate_instance(record, "camera_roll_record")
        records.append(record)

    records.sort(key=_record_sort_key)
    return records


def camera_roll_shard_month(captured_at: str) -> str:
    """Asia/Tokyo calendar month YYYY-MM from canonical captured_at."""
    dt = parse_rfc3339(
        canonicalize_timestamp(captured_at, field="captured_at"),
        field="captured_at",
    )
    return validate_camera_roll_shard_month(f"{dt.year:04d}-{dt.month:02d}")


def camera_roll_shard_path(captured_at: str) -> str:
    month = camera_roll_shard_month(captured_at)
    return f"camera-roll/records/{month}.json"


def merge_camera_roll_records(
    existing: Sequence[Mapping[str, Any]],
    additions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Merge record lists: identical record_id no-op; conflicting semantics fail closed."""
    by_id: dict[str, dict[str, Any]] = {}
    for raw in list(existing) + list(additions):
        rec = _normalize_record(raw)
        rid = rec["record_id"]
        prior = by_id.get(rid)
        if prior is None:
            by_id[rid] = rec
            continue
        if canonical_hash(prior) == canonical_hash(rec):
            continue
        raise LifeEngineError(
            ErrorCode.DUPLICATE_ID,
            f"camera roll record_id collision with different semantics: {rid}",
        )
    out = list(by_id.values())
    out.sort(key=_record_sort_key)
    return out


def merge_camera_roll_shard(
    shard: Mapping[str, Any],
    additions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Merge additions into a shard. Closed shards reject changes; identical no-op OK."""
    reject_binary_floats(shard, path="camera_roll_shard")
    data = dict(shard)
    if data.get("schema_version") != 1:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_VERSION, "camera_roll_shard.schema_version must be 1")
    # Enforce real month even for empty shards (no record required).
    month = validate_camera_roll_shard_month(data.get("month"))

    closed_at = data.get("closed_at", None)
    if closed_at is not None:
        closed_at = canonicalize_timestamp(closed_at, field="closed_at")

    existing_raw = list(data.get("records") or [])
    existing = [_normalize_record(r) for r in existing_raw]
    addition_list = [_normalize_record(r) for r in additions]

    for rec in existing + addition_list:
        rec_month = camera_roll_shard_month(rec["captured_at"])
        if rec_month != month:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"record month {rec_month} != shard.month {month}",
            )

    if closed_at is not None:
        # Closed: only identical re-validation / no-op additions allowed.
        existing_by_id = {r["record_id"]: r for r in existing}
        for rec in addition_list:
            prior = existing_by_id.get(rec["record_id"])
            if prior is None:
                raise LifeEngineError(
                    ErrorCode.INVALID_STATE,
                    f"closed shard rejects new record: {rec['record_id']}",
                )
            if canonical_hash(prior) != canonical_hash(rec):
                raise LifeEngineError(
                    ErrorCode.INVALID_STATE,
                    f"closed shard rejects changed record: {rec['record_id']}",
                )
        # No structural change — return canonicalized copy of existing shard.
        out = {
            "schema_version": 1,
            "month": month,
            "closed_at": closed_at,
            "records": sorted(existing, key=_record_sort_key),
        }
        validate_instance(out, "camera_roll_shard")
        return out

    merged = merge_camera_roll_records(existing, addition_list)
    out = {
        "schema_version": 1,
        "month": month,
        "closed_at": None,
        "records": merged,
    }
    validate_instance(out, "camera_roll_shard")
    return out
