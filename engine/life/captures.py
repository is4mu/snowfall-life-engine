"""Slice 4A / 4A.1 / 4B2 — strict Capture facts (pure; no I/O / clock / policy).

Capture identity:
  capture_id = stable_id("capture", activity_instance_id, occurrence_key)

occurrence_key must equal:
  stable_id("capture-occurrence", activity_instance_id, opportunity_key)

Slice 4A.1 adds immutable factual fields capture_kind + subject_refs[].
Slice 4B2 adds required per-capture capture_decision_evidence (TAKE only).
capture_context_hash remains visual_context-only.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_hash
from .errors import ErrorCode, LifeEngineError
from .ids import stable_id
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339

CAPTURE_KINDS = frozenset(
    {
        "SELFIE",
        "PEOPLE",
        "FOOD",
        "SCENERY",
        "OBJECT",
        "DAILY_LIFE",
    }
)

DECISION_TYPE_CAPTURE = "CAPTURE_OPPORTUNITY"
RESULT_TAKE_CAPTURE = "TAKE_CAPTURE"
_RANDOM_KEY_HEX_LEN = 64

_CAPTURE_KEYS = frozenset(
    {
        "schema_version",
        "capture_id",
        "occurrence_key",
        "activity_instance_id",
        "captured_at",
        "capture_kind",
        "subject_refs",
        "visual_context",
        "capture_context_hash",
        "capture_decision_evidence",
    }
)

_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "decision_type",
        "policy_version",
        "source_key",
        "opportunity_key",
        "activation_permille",
        "rule_ids",
        "input_state_refs",
        "random_key",
        "selected_result",
    }
)

_VISUAL_CONTEXT_KEYS = frozenset(
    {
        "character_id",
        "character_source_sha",
        "engine_commit_sha",
        "behavior_policy_version",
        "location_id",
        "appearance",
        "home_revision",
        "wardrobe_revision",
        "photographer_ref",
        "companion_refs",
        "lighting_context",
    }
)
_APPEARANCE_KEYS = frozenset(
    {
        "outfit_state_id",
        "outfit_item_ids",
        "makeup_level",
        "hair_state_id",
    }
)


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def _require_nonempty_str(value: object, *, label: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool) or value == "":
        _fail(f"{label} must be a non-empty string (coercion forbidden)")
    return value


def _require_strict_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be strict int (no bool/float/string)")
    return value


def _require_json_array_list(value: object, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(
            f"{field_name} must be array (string/bytes/tuple/set/generator/null/object rejected)",
            code=ErrorCode.SCHEMA_INVALID,
        )
    return value


def _canonical_unique_sorted_strs(values: Any, *, field_name: str) -> list[str]:
    values_list = _require_json_array_list(values, field_name=field_name)
    out: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(values_list):
        if isinstance(item, bool) or not isinstance(item, str) or item == "":
            _fail(
                f"{field_name}[{index}] must be a non-empty string (coercion forbidden)",
                code=ErrorCode.SCHEMA_INVALID,
            )
        if item in seen:
            _fail(
                f"{field_name} contains duplicate id: {item!r}",
                code=ErrorCode.SCHEMA_INVALID,
            )
        seen.add(item)
        out.append(item)
    return sorted(out)


def _reject_unknown_visual_context_fields(visual_context: Mapping[str, Any]) -> None:
    """Fail closed on unknown visual_context / appearance keys before normalize strips them."""
    if not isinstance(visual_context, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "visual_context must be object")
    unknown = set(visual_context.keys()) - _VISUAL_CONTEXT_KEYS
    if unknown:
        raise LifeEngineError(
            ErrorCode.SCHEMA_INVALID,
            f"unknown visual_context field(s): {sorted(unknown)}",
        )
    appearance = visual_context.get("appearance")
    if isinstance(appearance, Mapping):
        unknown_app = set(appearance.keys()) - _APPEARANCE_KEYS
        if unknown_app:
            raise LifeEngineError(
                ErrorCode.SCHEMA_INVALID,
                f"unknown appearance field(s): {sorted(unknown_app)}",
            )


def derive_capture_id(activity_instance_id: str, occurrence_key: str) -> str:
    if not isinstance(activity_instance_id, str) or not activity_instance_id:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "activity_instance_id required")
    if not isinstance(occurrence_key, str) or not occurrence_key:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "occurrence_key required")
    return stable_id("capture", activity_instance_id, occurrence_key)


def derive_capture_opportunity_key_from_source(
    activity_instance_id: str, source_key: str
) -> str:
    """Local re-export shape for evidence validation (matches 4B1 derivation)."""
    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    source = _require_nonempty_str(source_key, label="source_key")
    return stable_id("capture-opportunity", activity, source)


def derive_capture_occurrence_key_from_opportunity(
    activity_instance_id: str, opportunity_key: str
) -> str:
    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    opportunity = _require_nonempty_str(opportunity_key, label="opportunity_key")
    return stable_id("capture-occurrence", activity, opportunity)


def _require_nonempty_string_id_list(items: Any, *, field: str) -> list[str]:
    """Fail closed: every entry must already be a non-empty str. No str() coercion."""
    if not isinstance(items, list):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{field} must be array")
    out: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        # bool is not str; int/null/object/array must not become identity strings.
        if isinstance(item, bool) or not isinstance(item, str) or item == "":
            raise LifeEngineError(
                ErrorCode.SCHEMA_INVALID,
                f"{field}[{index}] must be a non-empty string (coercion forbidden)",
            )
        if item in seen:
            raise LifeEngineError(
                ErrorCode.SCHEMA_INVALID,
                f"{field} contains duplicate id: {item!r}",
            )
        seen.add(item)
        out.append(item)
    return sorted(out)


def normalize_visual_context(visual_context: Mapping[str, Any]) -> dict[str, Any]:
    """Capture-local visual_context normalization (not global key-name rules).

    outfit_item_ids / companion_refs are semantically unordered and sorted
    by string value so content hash is order-independent. Items must already
    be non-empty strings — never coerce via ``str()``.
    """
    reject_binary_floats(visual_context, path="visual_context")
    _reject_unknown_visual_context_fields(visual_context)
    if not isinstance(visual_context, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "visual_context must be object")
    raw = dict(visual_context)
    appearance_in = raw.get("appearance")
    if not isinstance(appearance_in, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "visual_context.appearance required")
    appearance = dict(appearance_in)
    outfit_ids = _require_nonempty_string_id_list(
        appearance.get("outfit_item_ids"),
        field="appearance.outfit_item_ids",
    )
    companion_refs = _require_nonempty_string_id_list(
        raw.get("companion_refs"),
        field="companion_refs",
    )
    out = {
        "character_id": raw.get("character_id"),
        "character_source_sha": raw.get("character_source_sha"),
        "engine_commit_sha": raw.get("engine_commit_sha"),
        "behavior_policy_version": raw.get("behavior_policy_version"),
        "location_id": raw.get("location_id"),
        "appearance": {
            "outfit_state_id": appearance.get("outfit_state_id"),
            "outfit_item_ids": outfit_ids,
            "makeup_level": appearance.get("makeup_level"),
            "hair_state_id": appearance.get("hair_state_id"),
        },
        "home_revision": raw.get("home_revision"),
        "wardrobe_revision": raw.get("wardrobe_revision"),
        "photographer_ref": raw.get("photographer_ref"),
        "companion_refs": companion_refs,
        "lighting_context": raw.get("lighting_context"),
    }
    return out


def compute_capture_context_hash(visual_context: Mapping[str, Any]) -> str:
    """64 lower-case hex; content-derived from visual_context only; no self-reference.

    Does **not** include capture_kind, subject_refs, or capture_decision_evidence.
    """
    normalized = normalize_visual_context(visual_context)
    digest = canonical_hash(normalized)
    if len(digest) != 64 or digest != digest.lower() or any(c not in "0123456789abcdef" for c in digest):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture_context_hash must be 64 lower-case hex")
    return digest


def normalize_subject_refs(subject_refs: Any) -> list[str]:
    """Capture/CameraRollRecord-local subject_refs normalization.

    Every item must already be a non-empty str (no ``str()`` coercion).
    Unique; semantically unordered; sorted by string value for hash/persist.
    Empty list is valid at this layer; cross-field rules may reject it.
    """
    return _require_nonempty_string_id_list(subject_refs, field="subject_refs")


def normalize_capture_kind(capture_kind: Any) -> str:
    """Strict closed enum. Missing/unknown/non-string fail closed. No defaults."""
    if not isinstance(capture_kind, str) or isinstance(capture_kind, bool):
        raise LifeEngineError(
            ErrorCode.SCHEMA_INVALID,
            "capture_kind must be a string enum member (coercion forbidden)",
        )
    if capture_kind not in CAPTURE_KINDS:
        raise LifeEngineError(
            ErrorCode.SCHEMA_INVALID,
            f"unknown capture_kind: {capture_kind!r}",
        )
    return capture_kind


def validate_capture_kind_subject_semantics(
    *,
    capture_kind: str,
    subject_refs: Sequence[str],
    visual_context: Mapping[str, Any],
) -> None:
    """Authoritative cross-field rules for capture_kind + subject_refs.

    SELFIE:
      - photographer_ref is non-null
      - photographer_ref == character_id
      - subject_refs contains character_id (additional subjects allowed)
    PEOPLE:
      - subject_refs non-empty
    FOOD / SCENERY / OBJECT / DAILY_LIFE:
      - empty subject_refs valid; explicit refs allowed when already known
    """
    kind = normalize_capture_kind(capture_kind)
    refs = list(subject_refs)
    if not isinstance(visual_context, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "visual_context must be object")
    character_id = visual_context.get("character_id")
    photographer_ref = visual_context.get("photographer_ref")

    if kind == "SELFIE":
        if photographer_ref is None:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "SELFIE requires non-null visual_context.photographer_ref",
            )
        if not isinstance(character_id, str) or not character_id:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "SELFIE requires non-empty visual_context.character_id",
            )
        if photographer_ref != character_id:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "SELFIE requires photographer_ref == character_id",
            )
        if character_id not in refs:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "SELFIE subject_refs must contain visual_context.character_id",
            )
        return

    if kind == "PEOPLE":
        if len(refs) == 0:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "PEOPLE requires non-empty subject_refs",
            )
        return

    # FOOD / SCENERY / OBJECT / DAILY_LIFE: empty or explicit refs OK.
    return


def _validate_random_key(value: object) -> str:
    key = _require_nonempty_str(value, label="random_key")
    if len(key) != _RANDOM_KEY_HEX_LEN or key != key.lower():
        _fail("random_key must be 64 lower-case hex", code=ErrorCode.SCHEMA_INVALID)
    if any(c not in "0123456789abcdef" for c in key):
        _fail("random_key must be 64 lower-case hex", code=ErrorCode.SCHEMA_INVALID)
    return key


def build_capture_decision_evidence(
    *,
    activity_instance_id: str,
    source_key: str,
    policy_version: str,
    activation_permille: object,
    rule_ids: object,
    input_state_refs: object,
    random_key: object,
    opportunity_key: str | None = None,
    selected_result: str = RESULT_TAKE_CAPTURE,
) -> dict[str, Any]:
    """Build validated CaptureDecisionEvidence v1 (TAKE only). Pure; no mutation."""
    rule_snapshot = list(rule_ids) if isinstance(rule_ids, list) else rule_ids
    input_snapshot = (
        list(input_state_refs) if isinstance(input_state_refs, list) else input_state_refs
    )
    reject_binary_floats(
        {
            "activity_instance_id": activity_instance_id,
            "source_key": source_key,
            "policy_version": policy_version,
            "activation_permille": activation_permille,
            "rule_ids": rule_snapshot,
            "input_state_refs": input_snapshot,
            "random_key": random_key,
            "opportunity_key": opportunity_key,
            "selected_result": selected_result,
        },
        path="capture_decision_evidence",
    )
    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    source = _require_nonempty_str(source_key, label="source_key")
    policy_v = _require_nonempty_str(policy_version, label="policy_version")
    expected_opp = derive_capture_opportunity_key_from_source(activity, source)
    if opportunity_key is not None:
        stored_opp = _require_nonempty_str(opportunity_key, label="opportunity_key")
        if stored_opp != expected_opp:
            _fail(
                f"opportunity_key mismatch: stored={stored_opp!r} expected={expected_opp!r}"
            )
    if selected_result != RESULT_TAKE_CAPTURE:
        _fail(
            "capture_decision_evidence.selected_result must be TAKE_CAPTURE "
            "(SKIP cannot appear in Capture)",
            code=ErrorCode.SCHEMA_INVALID,
        )
    activation = _require_strict_int(activation_permille, label="activation_permille")
    if activation < 0 or activation > 1000:
        _fail("activation_permille must be in 0..1000", code=ErrorCode.SCHEMA_INVALID)
    rules = _canonical_unique_sorted_strs(rule_snapshot, field_name="rule_ids")
    inputs = _canonical_unique_sorted_strs(input_snapshot, field_name="input_state_refs")
    rk = _validate_random_key(random_key)
    evidence = {
        "schema_version": 1,
        "decision_type": DECISION_TYPE_CAPTURE,
        "policy_version": policy_v,
        "source_key": source,
        "opportunity_key": expected_opp,
        "activation_permille": activation,
        "rule_ids": rules,
        "input_state_refs": inputs,
        "random_key": rk,
        "selected_result": RESULT_TAKE_CAPTURE,
    }
    validate_instance(evidence, "capture_decision_evidence")
    return evidence


def validate_capture_decision_evidence(
    evidence: Mapping[str, Any],
    *,
    activity_instance_id: str,
    occurrence_key: str,
    policy_version: str,
) -> dict[str, Any]:
    """Validate evidence against owning Capture identity/policy. No input mutation."""
    reject_binary_floats(evidence, path="capture_decision_evidence")
    if not isinstance(evidence, Mapping):
        _fail("capture_decision_evidence must be object")
    unknown = set(evidence.keys()) - _EVIDENCE_KEYS
    if unknown:
        _fail(
            f"unknown capture_decision_evidence field(s): {sorted(unknown)}",
            code=ErrorCode.SCHEMA_INVALID,
        )
    data = dict(evidence)
    if data.get("schema_version") != 1:
        _fail(
            "capture_decision_evidence.schema_version must be 1",
            code=ErrorCode.UNSUPPORTED_VERSION,
        )
    for required in (
        "decision_type",
        "policy_version",
        "source_key",
        "opportunity_key",
        "activation_permille",
        "rule_ids",
        "input_state_refs",
        "random_key",
        "selected_result",
    ):
        if required not in data:
            _fail(f"capture_decision_evidence.{required} required")

    if data.get("decision_type") != DECISION_TYPE_CAPTURE:
        _fail(
            "capture_decision_evidence.decision_type must be CAPTURE_OPPORTUNITY",
            code=ErrorCode.SCHEMA_INVALID,
        )
    validated = build_capture_decision_evidence(
        activity_instance_id=activity_instance_id,
        source_key=data["source_key"],
        policy_version=data["policy_version"],
        activation_permille=data["activation_permille"],
        rule_ids=data["rule_ids"],
        input_state_refs=data["input_state_refs"],
        random_key=data["random_key"],
        opportunity_key=data["opportunity_key"],
        selected_result=data["selected_result"],
    )
    expected_policy = _require_nonempty_str(policy_version, label="policy_version")
    if validated["policy_version"] != expected_policy:
        _fail(
            "capture_decision_evidence.policy_version must equal "
            "visual_context.behavior_policy_version"
        )
    expected_occ = derive_capture_occurrence_key_from_opportunity(
        activity_instance_id, validated["opportunity_key"]
    )
    stored_occ = _require_nonempty_str(occurrence_key, label="occurrence_key")
    if stored_occ != expected_occ:
        _fail(
            f"occurrence_key mismatch vs evidence.opportunity_key: "
            f"stored={stored_occ!r} expected={expected_occ!r}"
        )
    return validated


def _capture_sort_key(capture: Mapping[str, Any]) -> tuple[str, str]:
    return (str(capture.get("captured_at") or ""), str(capture.get("capture_id") or ""))


def normalize_capture_list_for_persistence(
    captures: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Sort captures by (captured_at, capture_id); canonicalize each capture locally."""
    out: list[dict[str, Any]] = []
    for item in captures:
        if not isinstance(item, Mapping):
            raise LifeEngineError(ErrorCode.INVALID_STATE, "capture items must be objects")
        out.append(validate_capture_fact(item))
    out.sort(key=_capture_sort_key)
    return out


def build_capture_fact(
    *,
    activity_instance_id: str,
    occurrence_key: str,
    captured_at: str,
    capture_kind: str,
    subject_refs: Sequence[str],
    visual_context: Mapping[str, Any],
    capture_decision_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a validated Capture v1. Does not mutate inputs. No field defaults."""
    # Materialize subject_refs for float scan without mutating caller input.
    subject_refs_for_scan = (
        list(subject_refs)
        if isinstance(subject_refs, Sequence) and not isinstance(subject_refs, (str, bytes))
        else subject_refs
    )
    evidence_snapshot = (
        deepcopy(dict(capture_decision_evidence))
        if isinstance(capture_decision_evidence, Mapping)
        else capture_decision_evidence
    )
    reject_binary_floats(
        {
            "activity_instance_id": activity_instance_id,
            "occurrence_key": occurrence_key,
            "captured_at": captured_at,
            "capture_kind": capture_kind,
            "subject_refs": subject_refs_for_scan,
            "visual_context": visual_context,
            "capture_decision_evidence": evidence_snapshot,
        },
        path="build_capture_fact",
    )
    vc_normalized = normalize_visual_context(visual_context)
    kind = normalize_capture_kind(capture_kind)
    refs = normalize_subject_refs(subject_refs)
    validate_capture_kind_subject_semantics(
        capture_kind=kind,
        subject_refs=refs,
        visual_context=vc_normalized,
    )
    captured = canonicalize_timestamp(captured_at, field="captured_at")
    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    occurrence = _require_nonempty_str(occurrence_key, label="occurrence_key")
    evidence = validate_capture_decision_evidence(
        evidence_snapshot,  # type: ignore[arg-type]
        activity_instance_id=activity,
        occurrence_key=occurrence,
        policy_version=vc_normalized["behavior_policy_version"],
    )
    capture_id = derive_capture_id(activity, occurrence)
    context_hash = compute_capture_context_hash(vc_normalized)
    capture = {
        "schema_version": 1,
        "capture_id": capture_id,
        "occurrence_key": occurrence,
        "activity_instance_id": activity,
        "captured_at": captured,
        "capture_kind": kind,
        "subject_refs": refs,
        "visual_context": vc_normalized,
        "capture_context_hash": context_hash,
        "capture_decision_evidence": evidence,
    }
    validate_instance(capture, "capture")
    return capture


def validate_capture_fact(capture: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a canonical Capture copy. Does not mutate input."""
    reject_binary_floats(capture, path="capture")
    if not isinstance(capture, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture must be object")
    unknown = set(capture.keys()) - _CAPTURE_KEYS
    if unknown:
        raise LifeEngineError(
            ErrorCode.SCHEMA_INVALID,
            f"unknown capture field(s): {sorted(unknown)}",
        )
    data = dict(capture)
    if data.get("schema_version") != 1:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_VERSION, "capture.schema_version must be 1")

    activity_instance_id = data.get("activity_instance_id")
    occurrence_key = data.get("occurrence_key")
    if not isinstance(activity_instance_id, str) or not activity_instance_id:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture.activity_instance_id required")
    if not isinstance(occurrence_key, str) or not occurrence_key:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture.occurrence_key required")

    expected_id = derive_capture_id(activity_instance_id, occurrence_key)
    stored_id = data.get("capture_id")
    if stored_id != expected_id:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"capture_id mismatch: stored={stored_id!r} expected={expected_id!r}",
        )

    if "captured_at" not in data or not isinstance(data["captured_at"], str):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture.captured_at required")
    captured = canonicalize_timestamp(data["captured_at"], field="captured_at")

    if "capture_kind" not in data:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture.capture_kind required")
    if "subject_refs" not in data:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture.subject_refs required")
    if "capture_decision_evidence" not in data:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE, "capture.capture_decision_evidence required"
        )
    kind = normalize_capture_kind(data["capture_kind"])
    refs = normalize_subject_refs(data["subject_refs"])

    vc_raw = data.get("visual_context")
    if not isinstance(vc_raw, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "capture.visual_context required")
    vc_normalized = normalize_visual_context(vc_raw)
    validate_capture_kind_subject_semantics(
        capture_kind=kind,
        subject_refs=refs,
        visual_context=vc_normalized,
    )
    expected_hash = compute_capture_context_hash(vc_normalized)
    stored_hash = data.get("capture_context_hash")
    if stored_hash != expected_hash:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"capture_context_hash mismatch: stored={stored_hash!r} expected={expected_hash!r}",
        )

    evidence = validate_capture_decision_evidence(
        data["capture_decision_evidence"],
        activity_instance_id=activity_instance_id,
        occurrence_key=occurrence_key,
        policy_version=vc_normalized["behavior_policy_version"],
    )

    out = {
        "schema_version": 1,
        "capture_id": expected_id,
        "occurrence_key": occurrence_key,
        "activity_instance_id": activity_instance_id,
        "captured_at": captured,
        "capture_kind": kind,
        "subject_refs": refs,
        "visual_context": vc_normalized,
        "capture_context_hash": expected_hash,
        "capture_decision_evidence": evidence,
    }
    validate_instance(out, "capture")
    return out


def attach_capture_to_active_activity(
    activity: Mapping[str, Any],
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach a validated capture to ActiveActivity.pending_captures (pure).

    - No input mutation
    - capture.activity_instance_id must equal activity.activity_instance_id
    - captured_at >= activity.actual_start
    - same capture_id + identical semantics => idempotent
    - same capture_id + different semantics => fail closed
    - pending_captures canonicalized by (captured_at, capture_id)
    """
    from .events import validate_active_activity

    activity_in = deepcopy(dict(activity))
    capture_in = deepcopy(dict(capture))

    validated_activity = validate_active_activity(activity_in)
    assert validated_activity is not None
    validated_capture = validate_capture_fact(capture_in)

    if validated_capture["activity_instance_id"] != validated_activity["activity_instance_id"]:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "capture.activity_instance_id must equal active_activity.activity_instance_id",
        )

    start = parse_rfc3339(validated_activity["actual_start"], field="actual_start")
    captured = parse_rfc3339(validated_capture["captured_at"], field="captured_at")
    if captured < start:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "captured_at must be >= active_activity.actual_start",
        )

    pending = [
        validate_capture_fact(c)
        for c in validated_activity.get("pending_captures", []) or []
    ]
    new_hash = canonical_hash(validated_capture)
    for existing in pending:
        if existing["capture_id"] == validated_capture["capture_id"]:
            if canonical_hash(existing) == new_hash:
                # Idempotent: return activity with already-canonical pending list.
                out = dict(validated_activity)
                out["pending_captures"] = normalize_capture_list_for_persistence(pending)
                return out
            raise LifeEngineError(
                ErrorCode.DUPLICATE_ID,
                f"pending capture id collision with different semantics: {validated_capture['capture_id']}",
            )

    pending.append(validated_capture)
    out = dict(validated_activity)
    out["pending_captures"] = normalize_capture_list_for_persistence(pending)
    validate_instance(out, "active_activity")
    return out


def validate_captures_for_actual_event(
    event: Mapping[str, Any],
    captures: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validate captures against owning ActualEvent time/identity bounds.

    Persisted/finalized capture arrays are strict: any second row with the same
    ``capture_id`` is rejected (identical or conflicting). No repair/collapse.
    """
    if not isinstance(captures, list) and not isinstance(captures, tuple):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "captures must be array")
    instance_id = event["activity_instance_id"]
    start = parse_rfc3339(event["actual_start"], field="actual_start")
    end = parse_rfc3339(event["actual_end"], field="actual_end")
    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in captures:
        cap = validate_capture_fact(raw)
        if cap["activity_instance_id"] != instance_id:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "capture.activity_instance_id must equal ActualEvent.activity_instance_id",
            )
        captured = parse_rfc3339(cap["captured_at"], field="captured_at")
        if captured < start:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "captured_at must be >= actual_start",
            )
        if captured > end:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "captured_at must be <= actual_end",
            )
        if cap["capture_id"] in seen_ids:
            raise LifeEngineError(
                ErrorCode.DUPLICATE_ID,
                f"duplicate capture_id in ActualEvent.captures: {cap['capture_id']}",
            )
        seen_ids.add(cap["capture_id"])
        validated.append(cap)
    return normalize_capture_list_for_persistence(validated)
