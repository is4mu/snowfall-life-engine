"""Slice 4B1 — capture policy contract + pure deterministic capture decisions.

Implements:
- CaptureOpportunity v1 build/validate
- opportunity / occurrence identity helpers
- keyed yes/no capture activation against Behavior Policy capture_policy
- ActualEvent-compatible decision evidence (returned, not persisted)

Does **not**:
- invent production opportunities
- attach Capture / write Camera Roll / touch runtime
- wire clock / wakeups / life start
- approve a production Behavior Policy
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from .captures import (
    normalize_capture_kind,
    normalize_subject_refs,
    normalize_visual_context,
    validate_capture_kind_subject_semantics,
)
from .decisions import build_decision_evidence
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .ids import stable_id
from .rng import SeedLike, keyed_digest, u01
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339

RNG_NAMESPACE = "capture/opportunity"
DECISION_TYPE = "CAPTURE_OPPORTUNITY"
RESOLVER_RULE_ID = "slice4b1.capture_activation"
SLEEP_GUARD_RULE_ID = "slice4b1.sleep_hard_guard"

RESULT_TAKE = "TAKE_CAPTURE"
RESULT_SKIP = "SKIP_CAPTURE"

_OPPORTUNITY_KEYS = frozenset(
    {
        "schema_version",
        "opportunity_key",
        "source_key",
        "activity_instance_id",
        "opportunity_at",
        "capture_kind",
        "subject_refs",
        "visual_context",
        "rule_ids",
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
    """Strict JSON-array contract: must already be a list. No list()/coercion."""
    if not isinstance(value, list):
        _fail(
            f"{field_name} must be array (string/bytes/tuple/set/generator/null/object rejected)",
            code=ErrorCode.SCHEMA_INVALID,
        )
    return value


def _canonical_unique_sorted_strs(values: Any, *, field_name: str) -> list[str]:
    """Every item already non-empty str; unique; locally sorted. No str() coercion.

    Container must already be a list (JSON array). Callers must not pre-coerce
    scalar strings via ``list(...)``.
    """
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


def _immutable_decision_evidence(evidence: Mapping[str, Any]) -> Mapping[str, Any]:
    """Store decision evidence as a read-only mapping with immutable sequences."""
    if not isinstance(evidence, Mapping):
        _fail("decision_evidence must be a mapping")
    frozen = {
        "decision_type": evidence["decision_type"],
        "policy_version": evidence["policy_version"],
        "rule_ids": tuple(evidence["rule_ids"]),
        "input_state_refs": tuple(evidence["input_state_refs"]),
        "random_key": evidence["random_key"],
        "selected_result": evidence["selected_result"],
    }
    return MappingProxyType(frozen)


def _decision_evidence_json_dict(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Fresh JSON-shaped decision_evidence dict (lists, not shared mutables)."""
    return {
        "decision_type": evidence["decision_type"],
        "policy_version": evidence["policy_version"],
        "rule_ids": list(evidence["rule_ids"]),
        "input_state_refs": list(evidence["input_state_refs"]),
        "random_key": evidence["random_key"],
        "selected_result": evidence["selected_result"],
    }


def derive_capture_opportunity_key(activity_instance_id: str, source_key: str) -> str:
    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    source = _require_nonempty_str(source_key, label="source_key")
    return stable_id("capture-opportunity", activity, source)


def derive_capture_occurrence_key(activity_instance_id: str, opportunity_key: str) -> str:
    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    opportunity = _require_nonempty_str(opportunity_key, label="opportunity_key")
    return stable_id("capture-occurrence", activity, opportunity)


def _build_validated_opportunity_dict(
    *,
    activity_instance_id: str,
    source_key: str,
    opportunity_at: str,
    capture_kind: str,
    subject_refs: Sequence[str] | list[str],
    visual_context: Mapping[str, Any],
    rule_ids: object,
    input_state_refs: object,
    opportunity_key: str | None = None,
) -> dict[str, Any]:
    # Strict JSON-array first: never list()-coerce scalar string/bytes/etc.
    rule_ids_list = _require_json_array_list(rule_ids, field_name="rule_ids")
    input_state_refs_list = _require_json_array_list(
        input_state_refs, field_name="input_state_refs"
    )
    # Shallow copies of already-validated lists only (caller inputs untouched).
    rule_ids_for_scan = list(rule_ids_list)
    input_refs_for_scan = list(input_state_refs_list)

    subject_refs_for_scan = (
        list(subject_refs)
        if isinstance(subject_refs, Sequence) and not isinstance(subject_refs, (str, bytes))
        else subject_refs
    )
    reject_binary_floats(
        {
            "activity_instance_id": activity_instance_id,
            "source_key": source_key,
            "opportunity_at": opportunity_at,
            "capture_kind": capture_kind,
            "subject_refs": subject_refs_for_scan,
            "visual_context": visual_context,
            "rule_ids": rule_ids_for_scan,
            "input_state_refs": input_refs_for_scan,
            "opportunity_key": opportunity_key,
        },
        path="capture_opportunity",
    )

    activity = _require_nonempty_str(activity_instance_id, label="activity_instance_id")
    source = _require_nonempty_str(source_key, label="source_key")
    expected_key = derive_capture_opportunity_key(activity, source)
    if opportunity_key is not None:
        stored = _require_nonempty_str(opportunity_key, label="opportunity_key")
        if stored != expected_key:
            _fail(
                f"opportunity_key mismatch: stored={stored!r} expected={expected_key!r}"
            )

    if not isinstance(opportunity_at, str):
        _fail("opportunity_at must be a string")
    opportunity_at_c = canonicalize_timestamp(opportunity_at, field="opportunity_at")

    kind = normalize_capture_kind(capture_kind)
    refs = normalize_subject_refs(subject_refs)
    vc = normalize_visual_context(visual_context)
    validate_capture_kind_subject_semantics(
        capture_kind=kind,
        subject_refs=refs,
        visual_context=vc,
    )
    rules = _canonical_unique_sorted_strs(rule_ids_for_scan, field_name="rule_ids")
    inputs = _canonical_unique_sorted_strs(
        input_refs_for_scan, field_name="input_state_refs"
    )

    opportunity = {
        "schema_version": 1,
        "opportunity_key": expected_key,
        "source_key": source,
        "activity_instance_id": activity,
        "opportunity_at": opportunity_at_c,
        "capture_kind": kind,
        "subject_refs": refs,
        "visual_context": vc,
        "rule_ids": rules,
        "input_state_refs": inputs,
    }
    validate_instance(opportunity, "capture_opportunity")
    return opportunity


def build_capture_opportunity(
    *,
    activity_instance_id: str,
    source_key: str,
    opportunity_at: str,
    capture_kind: str,
    subject_refs: Sequence[str],
    visual_context: Mapping[str, Any],
    rule_ids: object,
    input_state_refs: object,
) -> dict[str, Any]:
    """Build a validated CaptureOpportunity v1. Pure; no input mutation."""
    # Snapshot list inputs only; non-lists pass through for strict rejection.
    subject_snapshot = list(subject_refs) if isinstance(subject_refs, list) else subject_refs
    rule_snapshot = list(rule_ids) if isinstance(rule_ids, list) else rule_ids
    input_snapshot = (
        list(input_state_refs) if isinstance(input_state_refs, list) else input_state_refs
    )
    vc_snapshot = (
        deepcopy(dict(visual_context)) if isinstance(visual_context, Mapping) else visual_context
    )
    return _build_validated_opportunity_dict(
        activity_instance_id=activity_instance_id,
        source_key=source_key,
        opportunity_at=opportunity_at,
        capture_kind=capture_kind,
        subject_refs=subject_snapshot,  # type: ignore[arg-type]
        visual_context=vc_snapshot,  # type: ignore[arg-type]
        rule_ids=rule_snapshot,
        input_state_refs=input_snapshot,
        opportunity_key=None,
    )


def validate_capture_opportunity(opportunity: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a canonical CaptureOpportunity copy. No input mutation."""
    reject_binary_floats(opportunity, path="capture_opportunity")
    if not isinstance(opportunity, Mapping):
        _fail("capture_opportunity must be object")
    unknown = set(opportunity.keys()) - _OPPORTUNITY_KEYS
    if unknown:
        _fail(
            f"unknown capture_opportunity field(s): {sorted(unknown)}",
            code=ErrorCode.SCHEMA_INVALID,
        )
    data = dict(opportunity)
    if data.get("schema_version") != 1:
        _fail("capture_opportunity.schema_version must be 1", code=ErrorCode.UNSUPPORTED_VERSION)
    for required in (
        "opportunity_key",
        "source_key",
        "activity_instance_id",
        "opportunity_at",
        "capture_kind",
        "subject_refs",
        "visual_context",
        "rule_ids",
        "input_state_refs",
    ):
        if required not in data:
            _fail(f"capture_opportunity.{required} required")
    return _build_validated_opportunity_dict(
        activity_instance_id=data["activity_instance_id"],
        source_key=data["source_key"],
        opportunity_at=data["opportunity_at"],
        capture_kind=data["capture_kind"],
        subject_refs=data["subject_refs"],
        visual_context=data["visual_context"],
        rule_ids=data["rule_ids"],
        input_state_refs=data["input_state_refs"],
        opportunity_key=data["opportunity_key"],
    )


@dataclass(frozen=True)
class CaptureDecisionResolution:
    """Pure immutable/transient capture activation result. No runtime persistence.

    ``decision_evidence`` is exposed as a fresh JSON-shaped dict on each access /
    ``as_dict()`` call. Internal storage is a MappingProxyType over immutable
    sequences so callers cannot mutate the resolution through the public object.
    """

    result_kind: str
    opportunity_key: str
    activity_instance_id: str
    capture_kind: str
    policy_version: str
    activation_permille: int
    random_key: str | None
    occurrence_key: str | None
    rule_ids: tuple[str, ...]
    input_state_refs: tuple[str, ...]
    _frozen_decision_evidence: Mapping[str, Any] = field(repr=False)

    @classmethod
    def create(
        cls,
        *,
        result_kind: str,
        opportunity_key: str,
        activity_instance_id: str,
        capture_kind: str,
        policy_version: str,
        activation_permille: int,
        random_key: str | None,
        occurrence_key: str | None,
        rule_ids: tuple[str, ...],
        input_state_refs: tuple[str, ...],
        decision_evidence: Mapping[str, Any],
    ) -> CaptureDecisionResolution:
        return cls(
            result_kind=result_kind,
            opportunity_key=opportunity_key,
            activity_instance_id=activity_instance_id,
            capture_kind=capture_kind,
            policy_version=policy_version,
            activation_permille=activation_permille,
            random_key=random_key,
            occurrence_key=occurrence_key,
            rule_ids=rule_ids,
            input_state_refs=input_state_refs,
            _frozen_decision_evidence=_immutable_decision_evidence(decision_evidence),
        )

    @property
    def decision_evidence(self) -> dict[str, Any]:
        """Fresh JSON-shaped evidence dict; mutations do not affect this resolution."""
        return _decision_evidence_json_dict(self._frozen_decision_evidence)

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_kind": self.result_kind,
            "opportunity_key": self.opportunity_key,
            "activity_instance_id": self.activity_instance_id,
            "capture_kind": self.capture_kind,
            "policy_version": self.policy_version,
            "activation_permille": self.activation_permille,
            "random_key": self.random_key,
            "occurrence_key": self.occurrence_key,
            "rule_ids": list(self.rule_ids),
            "input_state_refs": list(self.input_state_refs),
            "decision_evidence": self.decision_evidence,
        }


def _activation_permille_for_kind(policy: Mapping[str, Any], capture_kind: str) -> int:
    capture_policy = policy.get("capture_policy")
    if not isinstance(capture_policy, Mapping):
        _fail("policy.capture_policy required")
    by_kind = capture_policy.get("activation_permille_by_kind")
    if not isinstance(by_kind, Mapping):
        _fail("policy.capture_policy.activation_permille_by_kind required")
    if capture_kind not in by_kind:
        _fail(f"missing activation_permille for capture_kind={capture_kind!r}")
    return _require_strict_int(
        by_kind[capture_kind],
        label=f"activation_permille_by_kind.{capture_kind}",
    )


def resolve_capture_opportunity(
    *,
    world_seed: SeedLike,
    policy: Mapping[str, Any],
    active_activity: Mapping[str, Any],
    opportunity: Mapping[str, Any],
) -> CaptureDecisionResolution:
    """Pure keyed capture activation. Does not attach Capture or write Camera Roll."""
    if world_seed is None or world_seed == "":
        _fail("world_seed required")

    # Snapshot inputs so callers can verify no mutation even if they pass mutable dicts.
    policy_in = deepcopy(dict(policy)) if isinstance(policy, Mapping) else policy
    activity_in = (
        deepcopy(dict(active_activity))
        if isinstance(active_activity, Mapping)
        else active_activity
    )
    opportunity_in = (
        deepcopy(dict(opportunity)) if isinstance(opportunity, Mapping) else opportunity
    )

    pol = require_v2_production_format(policy_in)  # type: ignore[arg-type]
    if pol.get("timezone") != "Asia/Tokyo":
        _fail("policy.timezone must be Asia/Tokyo")

    validated_activity = validate_active_activity(activity_in)  # type: ignore[arg-type]
    if validated_activity is None:
        _fail("active_activity required")

    opp = validate_capture_opportunity(opportunity_in)  # type: ignore[arg-type]

    if opp["activity_instance_id"] != validated_activity["activity_instance_id"]:
        _fail("opportunity.activity_instance_id must equal active_activity.activity_instance_id")

    start = parse_rfc3339(validated_activity["actual_start"], field="actual_start")
    opp_at = parse_rfc3339(opp["opportunity_at"], field="opportunity_at")
    if opp_at < start:
        _fail("opportunity_at must be >= active_activity.actual_start")

    vc = opp["visual_context"]
    if vc.get("character_id") != pol["character_id"]:
        _fail("opportunity.visual_context.character_id must equal policy.character_id")
    if vc.get("behavior_policy_version") != pol["behavior_policy_version"]:
        _fail(
            "opportunity.visual_context.behavior_policy_version must equal "
            "policy.behavior_policy_version"
        )

    activation = _activation_permille_for_kind(pol, opp["capture_kind"])
    policy_version = _require_nonempty_str(
        pol["behavior_policy_version"], label="behavior_policy_version"
    )
    input_refs = tuple(opp["input_state_refs"])

    # SLEEP hard guard: never TAKE; no occurrence; no RNG evaluation.
    if validated_activity.get("activity_type") == "SLEEP":
        rule_ids = tuple(
            sorted(
                set(opp["rule_ids"]) | {RESOLVER_RULE_ID, SLEEP_GUARD_RULE_ID}
            )
        )
        evidence = build_decision_evidence(
            decision_type=DECISION_TYPE,
            policy_version=policy_version,
            rule_ids=rule_ids,
            input_state_refs=input_refs,
            random_key=None,
            selected_result=RESULT_SKIP,
        )
        return CaptureDecisionResolution.create(
            result_kind=RESULT_SKIP,
            opportunity_key=opp["opportunity_key"],
            activity_instance_id=opp["activity_instance_id"],
            capture_kind=opp["capture_kind"],
            policy_version=policy_version,
            activation_permille=activation,
            random_key=None,
            occurrence_key=None,
            rule_ids=rule_ids,
            input_state_refs=input_refs,
            decision_evidence=evidence,
        )

    digest = keyed_digest(
        world_seed,
        RNG_NAMESPACE,
        pol["character_id"],
        DECISION_TYPE,
        opp["opportunity_key"],
    )
    draw_billionths = u01(
        world_seed,
        RNG_NAMESPACE,
        pol["character_id"],
        DECISION_TYPE,
        opp["opportunity_key"],
    )
    threshold = activation * 1_000_000
    take = draw_billionths < threshold
    random_key = digest.hex()
    rule_ids = tuple(sorted(set(opp["rule_ids"]) | {RESOLVER_RULE_ID}))

    if take:
        result_kind = RESULT_TAKE
        occurrence_key = derive_capture_occurrence_key(
            opp["activity_instance_id"],
            opp["opportunity_key"],
        )
    else:
        result_kind = RESULT_SKIP
        occurrence_key = None

    evidence = build_decision_evidence(
        decision_type=DECISION_TYPE,
        policy_version=policy_version,
        rule_ids=rule_ids,
        input_state_refs=input_refs,
        random_key=random_key,
        selected_result=result_kind,
    )
    return CaptureDecisionResolution.create(
        result_kind=result_kind,
        opportunity_key=opp["opportunity_key"],
        activity_instance_id=opp["activity_instance_id"],
        capture_kind=opp["capture_kind"],
        policy_version=policy_version,
        activation_permille=activation,
        random_key=random_key,
        occurrence_key=occurrence_key,
        rule_ids=rule_ids,
        input_state_refs=input_refs,
        decision_evidence=evidence,
    )
