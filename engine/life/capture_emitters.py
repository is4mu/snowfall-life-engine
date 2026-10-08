"""Slice 4B2 — production semantic capture emitters (pure; no TAKE/SKIP).

Emitter question only:
  Does a concrete, semantic photographable moment exist?

Does **not**:
- resolve TAKE/SKIP (4B1)
- attach Capture / write Camera Roll
- invent moments from prose/summary/location names
- apply rate/tick/multiplier/daily quota/Camera Roll fullness feedback
- wire clock / wakeups / runtime / life start
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .canonical import canonical_hash
from .capture_decisions import build_capture_opportunity
from .captures import (
    normalize_subject_refs,
    normalize_visual_context,
    validate_capture_kind_subject_semantics,
)
from .derived import classify_stress_band
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .ids import stable_id
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339

# ---------------------------------------------------------------------------
# Closed emitter / context matrices
# ---------------------------------------------------------------------------

EMITTER_RULE_TO_CAPTURE_KIND: Mapping[str, str] = {
    "slice4b2.selfie.appearance": "SELFIE",
    "slice4b2.selfie.contextual": "SELFIE",
    "slice4b2.social.group_selfie": "SELFIE",
    "slice4b2.social.people": "PEOPLE",
    "slice4b2.meal.contextual": "FOOD",
    "slice4b2.scenery.view": "SCENERY",
    "slice4b2.object.detail": "OBJECT",
    "slice4b2.daily.detail": "DAILY_LIFE",
}

EMITTER_CONTEXT_COMPAT: Mapping[str, frozenset[str]] = {
    "slice4b2.selfie.appearance": frozenset(
        {
            "PRE_OUTING_SELF_CHECK",
            "OUTFIT_COMPARISON",
            "APPEARANCE_SATISFACTION",
        }
    ),
    "slice4b2.selfie.contextual": frozenset(
        {
            "TRAVEL_SELFIE",
            "OUTING_SELFIE",
        }
    ),
    "slice4b2.social.group_selfie": frozenset({"IN_PERSON_SOCIAL"}),
    "slice4b2.social.people": frozenset({"IN_PERSON_SOCIAL"}),
    "slice4b2.meal.contextual": frozenset(
        {
            "SOCIAL_MEAL",
            "TRAVEL_MEAL",
            "SPECIAL_OCCASION",
            "FIRST_AT_PLACE",
            "VISUAL_DISTINCT",
        }
    ),
    "slice4b2.scenery.view": frozenset(
        {
            "TRAVEL_VIEW",
            "OUTING_VIEW",
        }
    ),
    "slice4b2.object.detail": frozenset(
        {
            "OUTFIT_DETAIL",
            "SHOPPING_ITEM",
            "OWNED_ITEM_DETAIL",
            "TRAVEL_DETAIL",
        }
    ),
    "slice4b2.daily.detail": frozenset(
        {
            "TRANSIT_DETAIL",
            "LODGING_DETAIL",
            "SEASONAL_DETAIL",
            "HOME_DETAIL",
            "CAMPUS_DETAIL",
            "OUTING_DETAIL",
        }
    ),
}

# Optional self-initiated emitters suppressed at VERY_HIGH stress.
# social.people remains eligible (explicit in-person only).
VERY_HIGH_SUPPRESSED_EMITTERS: frozenset[str] = frozenset(
    {
        "slice4b2.selfie.appearance",
        "slice4b2.selfie.contextual",
        "slice4b2.social.group_selfie",
        "slice4b2.meal.contextual",
        "slice4b2.scenery.view",
        "slice4b2.object.detail",
        "slice4b2.daily.detail",
    }
)

STRESS_GUARD_RULE_ID = "slice4b2.stress.not_very_high"
SLEEP_SUPPRESS_CODE = "SLEEP_ACTIVE"
STRESS_SUPPRESS_CODE = "VERY_HIGH_STRESS"
EMITTER_PEOPLE = "slice4b2.social.people"

_SOURCE_MOMENT_KEYS = frozenset(
    {
        "schema_version",
        "emitter_rule_id",
        "source_context_kind",
        "activity_instance_id",
        "semantic_source_ref",
        "moment_ref",
        "moment_at",
        "subject_refs",
        "visual_context",
        "input_state_refs",
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


def derive_capture_source_key(
    emitter_rule_id: str,
    semantic_source_ref: str,
    moment_ref: str,
) -> str:
    """Stable production source identity. No index/UUID/timestamp-only inputs."""
    emitter = _require_nonempty_str(emitter_rule_id, label="emitter_rule_id")
    semantic = _require_nonempty_str(semantic_source_ref, label="semantic_source_ref")
    moment = _require_nonempty_str(moment_ref, label="moment_ref")
    return stable_id("capture-source", emitter, semantic, moment)


def capture_kind_for_emitter_rule(emitter_rule_id: str) -> str:
    emitter = _require_nonempty_str(emitter_rule_id, label="emitter_rule_id")
    kind = EMITTER_RULE_TO_CAPTURE_KIND.get(emitter)
    if kind is None:
        _fail(f"unknown emitter_rule_id: {emitter!r}", code=ErrorCode.SCHEMA_INVALID)
    return kind


def context_rule_id_for(source_context_kind: str) -> str:
    kind = _require_nonempty_str(source_context_kind, label="source_context_kind")
    return f"slice4b2.context.{kind}"


def _assert_emitter_context_pair(emitter_rule_id: str, source_context_kind: str) -> None:
    allowed = EMITTER_CONTEXT_COMPAT.get(emitter_rule_id)
    if allowed is None:
        _fail(f"unknown emitter_rule_id: {emitter_rule_id!r}", code=ErrorCode.SCHEMA_INVALID)
    if source_context_kind not in allowed:
        _fail(
            f"incompatible source_context_kind {source_context_kind!r} "
            f"for emitter_rule_id {emitter_rule_id!r}",
            code=ErrorCode.SCHEMA_INVALID,
        )


def _build_validated_source_moment_dict(
    *,
    emitter_rule_id: str,
    source_context_kind: str,
    activity_instance_id: str,
    semantic_source_ref: str,
    moment_ref: str,
    moment_at: str,
    subject_refs: Any,
    visual_context: Any,
    input_state_refs: Any,
) -> dict[str, Any]:
    # Strict JSON-array contract (4A.1): actual list only — no tuple/set/Sequence coercion.
    subject_refs_list = _require_json_array_list(subject_refs, field_name="subject_refs")
    input_refs_list = _require_json_array_list(
        input_state_refs, field_name="input_state_refs"
    )
    reject_binary_floats(
        {
            "emitter_rule_id": emitter_rule_id,
            "source_context_kind": source_context_kind,
            "activity_instance_id": activity_instance_id,
            "semantic_source_ref": semantic_source_ref,
            "moment_ref": moment_ref,
            "moment_at": moment_at,
            "subject_refs": subject_refs_list,
            "visual_context": visual_context,
            "input_state_refs": input_refs_list,
        },
        path="capture_source_moment",
    )

    emitter = _require_nonempty_str(emitter_rule_id, label="emitter_rule_id")
    context = _require_nonempty_str(source_context_kind, label="source_context_kind")
    if emitter not in EMITTER_RULE_TO_CAPTURE_KIND:
        _fail(f"unknown emitter_rule_id: {emitter!r}", code=ErrorCode.SCHEMA_INVALID)
    _assert_emitter_context_pair(emitter, context)

    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    semantic = _require_nonempty_str(semantic_source_ref, label="semantic_source_ref")
    moment = _require_nonempty_str(moment_ref, label="moment_ref")
    if not isinstance(moment_at, str):
        _fail("moment_at must be a string")
    moment_at_c = canonicalize_timestamp(moment_at, field="moment_at")

    kind = capture_kind_for_emitter_rule(emitter)
    refs = normalize_subject_refs(subject_refs_list)
    vc = normalize_visual_context(visual_context)
    validate_capture_kind_subject_semantics(
        capture_kind=kind,
        subject_refs=refs,
        visual_context=vc,
    )
    inputs = _canonical_unique_sorted_strs(input_refs_list, field_name="input_state_refs")
    moment_obj = {
        "schema_version": 1,
        "emitter_rule_id": emitter,
        "source_context_kind": context,
        "activity_instance_id": activity,
        "semantic_source_ref": semantic,
        "moment_ref": moment,
        "moment_at": moment_at_c,
        "subject_refs": refs,
        "visual_context": vc,
        "input_state_refs": inputs,
    }
    validate_instance(moment_obj, "capture_source_moment")
    return moment_obj


def build_capture_source_moment(
    *,
    emitter_rule_id: str,
    source_context_kind: str,
    activity_instance_id: str,
    semantic_source_ref: str,
    moment_ref: str,
    moment_at: str,
    subject_refs: object,
    visual_context: Mapping[str, Any],
    input_state_refs: object,
) -> dict[str, Any]:
    """Build a validated CaptureSourceMoment v1. Pure; no input mutation.

    ``subject_refs`` / ``input_state_refs`` must already be JSON arrays (lists).
    Tuple/set/string/bytes and other non-list containers are rejected (4A.1).
    """
    # Shallow-copy only already-valid lists so caller inputs stay untouched.
    subject_snapshot = list(subject_refs) if isinstance(subject_refs, list) else subject_refs
    input_snapshot = (
        list(input_state_refs) if isinstance(input_state_refs, list) else input_state_refs
    )
    vc_snapshot = (
        deepcopy(dict(visual_context)) if isinstance(visual_context, Mapping) else visual_context
    )
    return _build_validated_source_moment_dict(
        emitter_rule_id=emitter_rule_id,
        source_context_kind=source_context_kind,
        activity_instance_id=activity_instance_id,
        semantic_source_ref=semantic_source_ref,
        moment_ref=moment_ref,
        moment_at=moment_at,
        subject_refs=subject_snapshot,
        visual_context=vc_snapshot,
        input_state_refs=input_snapshot,
    )


def validate_capture_source_moment(source_moment: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a canonical CaptureSourceMoment copy. No input mutation."""
    reject_binary_floats(source_moment, path="capture_source_moment")
    if not isinstance(source_moment, Mapping):
        _fail("capture_source_moment must be object")
    unknown = set(source_moment.keys()) - _SOURCE_MOMENT_KEYS
    if unknown:
        _fail(
            f"unknown capture_source_moment field(s): {sorted(unknown)}",
            code=ErrorCode.SCHEMA_INVALID,
        )
    data = dict(source_moment)
    if data.get("schema_version") != 1:
        _fail("capture_source_moment.schema_version must be 1", code=ErrorCode.UNSUPPORTED_VERSION)
    for required in (
        "emitter_rule_id",
        "source_context_kind",
        "activity_instance_id",
        "semantic_source_ref",
        "moment_ref",
        "moment_at",
        "subject_refs",
        "visual_context",
        "input_state_refs",
    ):
        if required not in data:
            _fail(f"capture_source_moment.{required} required")
    return _build_validated_source_moment_dict(
        emitter_rule_id=data["emitter_rule_id"],
        source_context_kind=data["source_context_kind"],
        activity_instance_id=data["activity_instance_id"],
        semantic_source_ref=data["semantic_source_ref"],
        moment_ref=data["moment_ref"],
        moment_at=data["moment_at"],
        subject_refs=data["subject_refs"],
        visual_context=data["visual_context"],
        input_state_refs=data["input_state_refs"],
    )


@dataclass(frozen=True)
class CaptureSuppression:
    code: str
    emitter_rule_id: str
    source_key: str
    source_context_kind: str
    stress_band: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "emitter_rule_id": self.emitter_rule_id,
            "source_key": self.source_key,
            "source_context_kind": self.source_context_kind,
        }
        if self.stress_band is not None:
            out["stress_band"] = self.stress_band
        return out


@dataclass(frozen=True)
class CaptureEmissionResult:
    opportunities: tuple[dict[str, Any], ...]
    suppressed: tuple[CaptureSuppression, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunities": [deepcopy(o) for o in self.opportunities],
            "suppressed": [s.as_dict() for s in self.suppressed],
        }


def _source_sort_key(moment: Mapping[str, Any]) -> tuple[str, str]:
    source_key = derive_capture_source_key(
        moment["emitter_rule_id"],
        moment["semantic_source_ref"],
        moment["moment_ref"],
    )
    return (moment["moment_at"], source_key)


def _assert_source_matches_active_context(
    moment: Mapping[str, Any],
    *,
    validated_activity: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
    """Fail closed when a source moment does not match the active production context."""
    if moment["activity_instance_id"] != validated_activity["activity_instance_id"]:
        _fail(
            "source moment activity_instance_id must equal "
            "active_activity.activity_instance_id"
        )

    start = parse_rfc3339(validated_activity["actual_start"], field="actual_start")
    moment_at = parse_rfc3339(moment["moment_at"], field="moment_at")
    if moment_at < start:
        _fail("source moment_at must be >= active_activity.actual_start")

    vc = moment["visual_context"]
    if vc.get("character_id") != policy["character_id"]:
        _fail("source visual_context.character_id must equal policy.character_id")
    if vc.get("behavior_policy_version") != policy["behavior_policy_version"]:
        _fail(
            "source visual_context.behavior_policy_version must equal "
            "policy.behavior_policy_version"
        )


def _canonicalize_source_moments(
    moments_in: Sequence[Any],
) -> dict[str, dict[str, Any]]:
    """Validate, order, and dedupe source moments by source_key.

    Identical semantics for the same source_key collapse to one canonical row.
    Conflicting semantics for the same source_key fail closed.
    """
    validated_moments: list[dict[str, Any]] = []
    for raw in moments_in:
        validated_moments.append(validate_capture_source_moment(raw))

    validated_moments.sort(key=_source_sort_key)

    by_source: dict[str, dict[str, Any]] = {}
    for moment in validated_moments:
        source_key = derive_capture_source_key(
            moment["emitter_rule_id"],
            moment["semantic_source_ref"],
            moment["moment_ref"],
        )
        prior = by_source.get(source_key)
        if prior is None:
            by_source[source_key] = moment
            continue
        if canonical_hash(prior) == canonical_hash(moment):
            continue
        _fail(
            f"conflicting capture source moments for source_key={source_key!r}",
            code=ErrorCode.DUPLICATE_ID,
        )
    return by_source


def emit_capture_opportunities(
    *,
    policy: Mapping[str, Any],
    active_activity: Mapping[str, Any],
    stress: object,
    stress_state_ref: object,
    source_moments: Sequence[Mapping[str, Any]],
) -> CaptureEmissionResult:
    """Pure production emitter: source moments → at most one CaptureOpportunity each.

    No TAKE/SKIP. No PRNG. No input mutation. No I/O.

    Authoritative source validation / dedupe / active-context checks always run
    before SLEEP or VERY_HIGH suppression decisions. Malformed or conflicting
    sources fail closed even when the active activity is SLEEP.
    """
    policy_in = deepcopy(dict(policy)) if isinstance(policy, Mapping) else policy
    activity_in = (
        deepcopy(dict(active_activity))
        if isinstance(active_activity, Mapping)
        else active_activity
    )
    moments_in: list[Any] = []
    if not isinstance(source_moments, Sequence) or isinstance(source_moments, (str, bytes)):
        _fail("source_moments must be a sequence of objects")
    for item in source_moments:
        moments_in.append(deepcopy(dict(item)) if isinstance(item, Mapping) else item)

    pol = require_v2_production_format(policy_in)  # type: ignore[arg-type]
    if pol.get("timezone") != "Asia/Tokyo":
        _fail("policy.timezone must be Asia/Tokyo")

    validated_activity = validate_active_activity(activity_in)  # type: ignore[arg-type]
    if validated_activity is None:
        _fail("active_activity required")

    stress_i = _require_strict_int(stress, label="stress")
    if stress_i < 0 or stress_i > 1000:
        _fail("stress must be in 0..1000")
    stress_ref = _require_nonempty_str(stress_state_ref, label="stress_state_ref")
    stress_band = classify_stress_band(stress_i, pol)

    # Validate + dedupe first (including for SLEEP): malformed/conflicting inputs
    # must fail closed, never become suppression diagnostics.
    by_source = _canonicalize_source_moments(moments_in)
    ordered_keys = sorted(
        by_source.keys(),
        key=lambda sk: (by_source[sk]["moment_at"], sk),
    )

    # Active-context checks for every accepted source identity.
    for source_key in ordered_keys:
        _assert_source_matches_active_context(
            by_source[source_key],
            validated_activity=validated_activity,
            policy=pol,
        )

    # SLEEP: never emit opportunities; only valid sources become suppression diagnostics.
    if validated_activity.get("activity_type") == "SLEEP":
        suppressed: list[CaptureSuppression] = []
        for source_key in ordered_keys:
            moment = by_source[source_key]
            suppressed.append(
                CaptureSuppression(
                    code=SLEEP_SUPPRESS_CODE,
                    emitter_rule_id=moment["emitter_rule_id"],
                    source_key=source_key,
                    source_context_kind=moment["source_context_kind"],
                    stress_band=stress_band,
                )
            )
        return CaptureEmissionResult(opportunities=(), suppressed=tuple(suppressed))

    opportunities: list[dict[str, Any]] = []
    suppressed_out: list[CaptureSuppression] = []

    for source_key in ordered_keys:
        moment = by_source[source_key]
        emitter = moment["emitter_rule_id"]

        if stress_band == "VERY_HIGH" and emitter in VERY_HIGH_SUPPRESSED_EMITTERS:
            suppressed_out.append(
                CaptureSuppression(
                    code=STRESS_SUPPRESS_CODE,
                    emitter_rule_id=emitter,
                    source_key=source_key,
                    source_context_kind=moment["source_context_kind"],
                    stress_band=stress_band,
                )
            )
            continue

        kind = capture_kind_for_emitter_rule(emitter)
        rule_ids = [
            emitter,
            context_rule_id_for(moment["source_context_kind"]),
        ]
        # Stress guard only when an emitted path was stress-eligible (not VERY_HIGH
        # optional suppression). For social.people at VERY_HIGH, still record that
        # stress was considered without suppressing.
        if stress_band != "VERY_HIGH" or emitter == EMITTER_PEOPLE:
            rule_ids.append(STRESS_GUARD_RULE_ID)

        input_refs = list(moment["input_state_refs"]) + [stress_ref]

        opportunity = build_capture_opportunity(
            activity_instance_id=moment["activity_instance_id"],
            source_key=source_key,
            opportunity_at=moment["moment_at"],
            capture_kind=kind,
            subject_refs=list(moment["subject_refs"]),
            visual_context=deepcopy(moment["visual_context"]),
            rule_ids=rule_ids,
            input_state_refs=input_refs,
        )
        opportunities.append(opportunity)

    return CaptureEmissionResult(
        opportunities=tuple(opportunities),
        suppressed=tuple(suppressed_out),
    )
