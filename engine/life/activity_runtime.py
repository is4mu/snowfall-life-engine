"""Slice 5B1 — activity provenance bridge (start / continue / ActiveDecisionContext).

Pure in-memory helpers. Does not select actions, invent durations, map ActionKind
to ActivityStartSpec, enqueue ACTIVITY_END, mutate ScheduleState / world domains,
write disk/Git, or activate production application state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .canonical import sort_unordered_collections
from .checkpoint import validate_checkpoint
from .decisions import (
    ACTION_KINDS,
    ActiveDecisionContext,
    ActionCandidate,
    DecisionResolution,
    INTERRUPTIBILITY,
    RESULT_KINDS,
    SOURCE_KINDS,
    _assert_tier_source_action,
    build_decision_evidence,
    candidate_id_for,
)
from .effects import normalize_effect
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .ids import stable_id
from .schema import reject_binary_floats
from .timeutil import canonicalize_timestamp, parse_rfc3339

ACTIVITY_START_RULE_ID = "slice5b1.activity-start"

# Action kinds allowed in persisted runtime_context (CONTINUE_CURRENT excluded).
RUNTIME_ACTION_KINDS = frozenset(ACTION_KINDS - {"CONTINUE_CURRENT"})

# Source kinds allowed on selected (non-continue) candidates for runtime start.
RUNTIME_SOURCE_KINDS = frozenset(SOURCE_KINDS - {"CURRENT_ACTIVITY"})

_SPECIAL_DETAIL_TYPES = frozenset({"SLEEP", "MEAL", "SOCIAL_CONTACT"})


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label}: expected non-empty string")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{label}: expected bool")
    return value


def _require_true_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label}: expected true int (no bool/float/string)")
    return value


def _canonical_unique_sorted_strs(values: Sequence[str], *, label: str) -> list[str]:
    """Sort unique non-empty strings. Silent dedupe for builder-owned merges only."""
    out: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(values):
        s = _require_str(raw, f"{label}[{i}]")
        if s not in seen:
            seen.add(s)
            out.append(s)
    out.sort()
    return out


def _require_unique_sorted_str_sequence(
    values: object,
    *,
    label: str,
) -> list[str]:
    """Require list/tuple of unique non-empty strings; reject bare str and duplicates."""
    if isinstance(values, (str, bytes)):
        _fail(f"{label}: expected list/tuple of strings, not bare string")
    if not isinstance(values, (list, tuple)):
        _fail(f"{label}: expected list/tuple of non-empty strings")
    out: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(values):
        s = _require_str(raw, f"{label}[{i}]")
        if s in seen:
            _fail(f"{label}: duplicate value {s!r}")
        seen.add(s)
        out.append(s)
    out.sort()
    return out


def derive_activity_instance_id(
    *,
    character_id: str,
    decision_key: str,
    selected_candidate_id: str,
) -> str:
    """Deterministic activity identity. No timestamp / index / UUID."""
    return stable_id(
        "activity-instance",
        _require_str(character_id, "character_id"),
        _require_str(decision_key, "decision_key"),
        _require_str(selected_candidate_id, "selected_candidate_id"),
    )


@dataclass(frozen=True)
class ActivityStartSpec:
    """Transient start facts. Not persisted as a separate object.

    5B1 does not map ActionKind → this spec (5B2 owns that).
    """

    activity_type: str
    actual_start: str
    interruptibility: str
    planned_end: str | None
    expected_end: str | None
    expected_end_window: Mapping[str, Any] | None
    location_id: str | None
    companions: tuple[Mapping[str, Any], ...]
    commitment_id: str | None
    details: Any
    effects_on_end: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "activity_type": self.activity_type,
            "actual_start": self.actual_start,
            "interruptibility": self.interruptibility,
            "planned_end": self.planned_end,
            "expected_end": self.expected_end,
            "expected_end_window": (
                None
                if self.expected_end_window is None
                else dict(self.expected_end_window)
            ),
            "location_id": self.location_id,
            "companions": [dict(c) for c in self.companions],
            "commitment_id": self.commitment_id,
            "details": deepcopy(self.details),
            "effects_on_end": [dict(e) for e in self.effects_on_end],
        }


def parse_activity_start_spec(raw: Mapping[str, Any] | ActivityStartSpec) -> ActivityStartSpec:
    """Strict parse of ActivityStartSpec. No invent / no wall-clock default."""
    if isinstance(raw, ActivityStartSpec):
        return raw
    if not isinstance(raw, Mapping):
        _fail("ActivityStartSpec: expected object")
    reject_binary_floats(dict(raw), path="$.ActivityStartSpec")
    allowed = {
        "activity_type",
        "actual_start",
        "interruptibility",
        "planned_end",
        "expected_end",
        "expected_end_window",
        "location_id",
        "companions",
        "commitment_id",
        "details",
        "effects_on_end",
    }
    extra = set(raw.keys()) - allowed
    if extra:
        _fail(f"ActivityStartSpec: unexpected fields {sorted(extra)}")
    missing = allowed - set(raw.keys())
    if missing:
        _fail(f"ActivityStartSpec: missing fields {sorted(missing)}")

    activity_type = _require_str(raw["activity_type"], "activity_type")
    actual_start = canonicalize_timestamp(
        _require_str(raw["actual_start"], "actual_start"), field="actual_start"
    )
    interruptibility = _require_str(raw["interruptibility"], "interruptibility")
    if interruptibility not in INTERRUPTIBILITY:
        _fail(f"interruptibility: unsupported {interruptibility!r}")

    planned_end = raw["planned_end"]
    if planned_end is not None:
        planned_end = canonicalize_timestamp(
            _require_str(planned_end, "planned_end"), field="planned_end"
        )
    expected_end = raw["expected_end"]
    if expected_end is not None:
        expected_end = canonicalize_timestamp(
            _require_str(expected_end, "expected_end"), field="expected_end"
        )

    window = raw["expected_end_window"]
    if window is None:
        expected_end_window: dict[str, Any] | None = None
    elif isinstance(window, Mapping):
        if set(window.keys()) - {"earliest", "latest"}:
            _fail("expected_end_window: only earliest/latest allowed")
        earliest = window.get("earliest")
        latest = window.get("latest")
        expected_end_window = {
            "earliest": (
                None
                if earliest is None
                else canonicalize_timestamp(
                    _require_str(earliest, "expected_end_window.earliest"),
                    field="expected_end_window.earliest",
                )
            ),
            "latest": (
                None
                if latest is None
                else canonicalize_timestamp(
                    _require_str(latest, "expected_end_window.latest"),
                    field="expected_end_window.latest",
                )
            ),
        }
    else:
        _fail("expected_end_window: expected object or null")

    location_id = raw["location_id"]
    if location_id is not None:
        location_id = _require_str(location_id, "location_id")

    companions_raw = raw["companions"]
    if not isinstance(companions_raw, (list, tuple)):
        _fail("companions: expected list")
    companions: list[dict[str, Any]] = []
    for i, row in enumerate(companions_raw):
        if not isinstance(row, Mapping):
            _fail(f"companions[{i}]: expected object")
        entity_id = _require_str(row.get("entity_id"), f"companions[{i}].entity_id")
        companion: dict[str, Any] = {"entity_id": entity_id}
        if "role" in row and row["role"] is not None:
            companion["role"] = _require_str(row["role"], f"companions[{i}].role")
        extra_c = set(row.keys()) - {"entity_id", "role"}
        if extra_c:
            _fail(f"companions[{i}]: unexpected fields {sorted(extra_c)}")
        companions.append(companion)

    commitment_id = raw["commitment_id"]
    if commitment_id is not None:
        commitment_id = _require_str(commitment_id, "commitment_id")

    details = deepcopy(raw["details"])

    effects_raw = raw["effects_on_end"]
    if not isinstance(effects_raw, (list, tuple)):
        _fail("effects_on_end: expected list")
    effects: list[dict[str, Any]] = []
    for i, effect in enumerate(effects_raw):
        if not isinstance(effect, Mapping):
            _fail(f"effects_on_end[{i}]: expected object")
        effects.append(normalize_effect(effect))

    return ActivityStartSpec(
        activity_type=activity_type,
        actual_start=actual_start,
        interruptibility=interruptibility,
        planned_end=planned_end,
        expected_end=expected_end,
        expected_end_window=expected_end_window,
        location_id=location_id,
        companions=tuple(companions),
        commitment_id=commitment_id,
        details=details,
        effects_on_end=tuple(effects),
    )


def _parse_candidate(raw: Mapping[str, Any] | ActionCandidate) -> ActionCandidate:
    """Strict candidate parse. Dataclass and mapping share the same validation path."""
    if isinstance(raw, ActionCandidate):
        raw = raw.as_dict()
    if not isinstance(raw, Mapping):
        _fail("selected_candidate: expected object")
    reject_binary_floats(dict(raw), path="$.selected_candidate")
    rule_ids_raw = raw.get("rule_ids")
    if not isinstance(rule_ids_raw, (list, tuple)) or isinstance(
        rule_ids_raw, (str, bytes)
    ):
        _fail("selected_candidate.rule_ids: expected list/tuple")
    # Candidate rule_ids: unique required (reject duplicates; no silent collapse).
    rule_ids = tuple(
        _require_unique_sorted_str_sequence(rule_ids_raw, label="rule_ids")
    )
    pref = raw.get("local_preference_permille")
    if pref is not None:
        pref = _require_true_int(pref, "local_preference_permille")
        if pref < 0 or pref > 1000:
            _fail("local_preference_permille: expected 0..1000 or null")
    activity_instance_id = raw.get("activity_instance_id")
    if activity_instance_id is not None:
        activity_instance_id = _require_str(
            activity_instance_id, "activity_instance_id"
        )
    return ActionCandidate(
        candidate_key=_require_str(raw.get("candidate_key"), "candidate_key"),
        candidate_id=_require_str(raw.get("candidate_id"), "candidate_id"),
        action_kind=_require_str(raw.get("action_kind"), "action_kind"),
        priority_tier=_require_str(raw.get("priority_tier"), "priority_tier"),
        source_kind=_require_str(raw.get("source_kind"), "source_kind"),
        source_ref=_require_str(raw.get("source_ref"), "source_ref"),
        soft_candidate=_require_bool(raw.get("soft_candidate"), "soft_candidate"),
        time_feasible=_require_bool(raw.get("time_feasible"), "time_feasible"),
        location_feasible=_require_bool(
            raw.get("location_feasible"), "location_feasible"
        ),
        physical_feasible=_require_bool(
            raw.get("physical_feasible"), "physical_feasible"
        ),
        domain_guard_satisfied=_require_bool(
            raw.get("domain_guard_satisfied"), "domain_guard_satisfied"
        ),
        local_preference_permille=pref,
        activity_instance_id=activity_instance_id,
        rule_ids=rule_ids,
    )


def _parse_resolution(raw: Mapping[str, Any] | DecisionResolution) -> DecisionResolution:
    """Strict resolution parse. Dataclass and mapping share the same validation path."""
    if isinstance(raw, DecisionResolution):
        raw = raw.as_dict()
    if not isinstance(raw, Mapping):
        _fail("resolution: expected object")
    reject_binary_floats(dict(raw), path="$.resolution")
    result_kind = _require_str(raw.get("result_kind"), "result_kind")
    if result_kind not in RESULT_KINDS:
        _fail(f"result_kind: unsupported {result_kind!r}")
    selected_candidate_id = raw.get("selected_candidate_id")
    if selected_candidate_id is not None:
        selected_candidate_id = _require_str(
            selected_candidate_id, "selected_candidate_id"
        )
    selected_candidate_key = raw.get("selected_candidate_key")
    if selected_candidate_key is not None:
        selected_candidate_key = _require_str(
            selected_candidate_key, "selected_candidate_key"
        )
    selected_action_kind = raw.get("selected_action_kind")
    if selected_action_kind is not None:
        selected_action_kind = _require_str(
            selected_action_kind, "selected_action_kind"
        )
    selected_priority_tier = raw.get("selected_priority_tier")
    if selected_priority_tier is not None:
        selected_priority_tier = _require_str(
            selected_priority_tier, "selected_priority_tier"
        )
    activity_instance_id = raw.get("activity_instance_id")
    if activity_instance_id is not None:
        activity_instance_id = _require_str(
            activity_instance_id, "activity_instance_id"
        )
    considered = raw.get("considered_candidate_ids")
    if considered is None:
        considered = ()
    if isinstance(considered, (str, bytes)):
        _fail("considered_candidate_ids: expected list/tuple, not bare string")
    if not isinstance(considered, (list, tuple)):
        _fail("considered_candidate_ids: expected list/tuple")
    rule_ids_raw = raw.get("rule_ids")
    if rule_ids_raw is None:
        rule_ids_raw = ()
    if isinstance(rule_ids_raw, (str, bytes)):
        _fail("resolution.rule_ids: expected list/tuple, not bare string")
    if not isinstance(rule_ids_raw, (list, tuple)):
        _fail("resolution.rule_ids: expected list/tuple")
    rule_ids = tuple(
        _require_unique_sorted_str_sequence(
            rule_ids_raw, label="resolution.rule_ids"
        )
    )
    random_key = raw.get("random_key")
    if random_key is not None:
        random_key = _require_str(random_key, "random_key")
    return DecisionResolution(
        result_kind=result_kind,
        decision_key=_require_str(raw.get("decision_key"), "decision_key"),
        selected_candidate_id=selected_candidate_id,
        selected_candidate_key=selected_candidate_key,
        selected_action_kind=selected_action_kind,
        selected_priority_tier=selected_priority_tier,
        activity_instance_id=activity_instance_id,
        considered_candidate_ids=tuple(
            _require_str(x, "considered_candidate_id") for x in considered
        ),
        rejected=(),
        rule_ids=rule_ids,
        random_key=random_key,
        min_dwell_forced=_require_bool(
            raw.get("min_dwell_forced", False), "min_dwell_forced"
        ),
        selection_mode=raw.get("selection_mode"),
    )


def _build_causes(
    *,
    resolution: DecisionResolution,
    candidate: ActionCandidate,
    commitment_id: str | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"cause_type": "DECISION", "ref": resolution.decision_key},
        {"cause_type": candidate.source_kind, "ref": candidate.source_ref},
    ]
    if commitment_id is not None:
        already = any(
            r["cause_type"] == "COMMITMENT" and r.get("ref") == commitment_id
            for r in rows
        )
        if not already:
            rows.append({"cause_type": "COMMITMENT", "ref": commitment_id})
    # Exact duplicate removal, then canonical (cause_type, ref) order.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["cause_type"], row.get("ref") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    sorted_rows = sort_unordered_collections({"causes": deduped})["causes"]
    return list(sorted_rows)


def _validate_details_for_activity_type(activity_type: str, details: Any) -> Any:
    """Enforce ActualEvent-equivalent details conditioning for runtime-started activities."""
    if activity_type == "SLEEP":
        if not isinstance(details, Mapping):
            _fail("SLEEP activity requires sleep details object")
        if set(details.keys()) - {"sleep_kind"}:
            _fail("SLEEP details: only sleep_kind allowed")
        kind = details.get("sleep_kind")
        if kind not in {"MAIN", "NAP"}:
            _fail("SLEEP details.sleep_kind must be MAIN or NAP")
        return {"sleep_kind": kind}
    if activity_type == "MEAL":
        if not isinstance(details, Mapping):
            _fail("MEAL activity requires meal details object")
        if set(details.keys()) - {"meal_source", "meal_extent"}:
            _fail("MEAL details: only meal_source/meal_extent allowed")
        source = details.get("meal_source")
        extent = details.get("meal_extent")
        if source not in {
            "HOME_COOKED",
            "OUTSIDE",
            "CONVENIENCE",
            "DELIVERY",
            "SOCIAL_MEAL",
        }:
            _fail("MEAL details.meal_source invalid")
        if extent not in {"LIGHT", "STANDARD"}:
            _fail("MEAL details.meal_extent invalid")
        return {"meal_source": source, "meal_extent": extent}
    if activity_type == "SOCIAL_CONTACT":
        if not isinstance(details, Mapping):
            _fail("SOCIAL_CONTACT activity requires contact details object")
        if set(details.keys()) - {"contact_mode"}:
            _fail("SOCIAL_CONTACT details: only contact_mode allowed")
        mode = details.get("contact_mode")
        if mode not in {
            "LIGHTWEIGHT",
            "CALL",
            "LONG_CATCHUP_CALL",
            "LOCAL_OUTING",
            "POST_WORK",
        }:
            _fail("SOCIAL_CONTACT details.contact_mode invalid")
        return {"contact_mode": mode}
    # Non-special types: details must be null (ActualEvent conditioning).
    if details is not None:
        _fail(f"{activity_type}: details must be null")
    return None


def _validate_select_candidate_for_start(
    *,
    character_id: str,
    resolution: DecisionResolution,
    candidate: ActionCandidate,
) -> None:
    """Semantic verification of already-resolved SELECT_CANDIDATE output.

    Does not re-run RNG or choose a winner. Fail-closed against forged pairs.
    """
    if resolution.result_kind != "SELECT_CANDIDATE":
        _fail(f"result_kind must be SELECT_CANDIDATE, got {resolution.result_kind!r}")

    # SELECT_CANDIDATE coherence: selected fields non-null; no pre-existing activity id.
    if resolution.selected_candidate_id is None:
        _fail("SELECT_CANDIDATE requires non-null selected_candidate_id")
    if resolution.selected_candidate_key is None:
        _fail("SELECT_CANDIDATE requires non-null selected_candidate_key")
    if resolution.selected_action_kind is None:
        _fail("SELECT_CANDIDATE requires non-null selected_action_kind")
    if resolution.selected_priority_tier is None:
        _fail("SELECT_CANDIDATE requires non-null selected_priority_tier")
    if resolution.activity_instance_id is not None:
        _fail("SELECT_CANDIDATE must not carry a pre-existing activity_instance_id")

    # Exact candidate ↔ resolution equality (kept from initial 5B1 bridge).
    if candidate.candidate_id != resolution.selected_candidate_id:
        _fail("selected_candidate_id mismatch vs resolution")
    if candidate.candidate_key != resolution.selected_candidate_key:
        _fail("selected_candidate_key mismatch vs resolution")
    if candidate.action_kind != resolution.selected_action_kind:
        _fail("selected action_kind mismatch vs resolution")
    if candidate.priority_tier != resolution.selected_priority_tier:
        _fail("selected priority_tier mismatch vs resolution")

    if candidate.action_kind == "CONTINUE_CURRENT":
        _fail("CONTINUE_CURRENT cannot start a new activity")
    if candidate.action_kind not in RUNTIME_ACTION_KINDS:
        _fail(f"action_kind not allowed for runtime start: {candidate.action_kind!r}")
    if candidate.source_kind not in RUNTIME_SOURCE_KINDS:
        _fail(
            f"source_kind not allowed for runtime start: {candidate.source_kind!r}"
        )

    # Stable resolver-owned identity — reject forged candidate_id.
    expected_id = candidate_id_for(
        character_id=character_id,
        decision_key=resolution.decision_key,
        candidate_key=candidate.candidate_key,
    )
    if candidate.candidate_id != expected_id:
        _fail(
            "selected_candidate_id must equal candidate_id_for("
            "character_id, decision_key, candidate_key)"
        )

    # Existing Slice-2D causal compatibility (reuse matrix; do not copy).
    # decisions.py remains byte-frozen by Slice 3 guards; call the shared helper.
    _assert_tier_source_action(
        action_kind=candidate.action_kind,
        priority_tier=candidate.priority_tier,
        source_kind=candidate.source_kind,
    )

    if candidate.activity_instance_id is not None:
        _fail("new selected candidate activity_instance_id must be null")
    if not candidate.time_feasible:
        _fail("selected candidate time_feasible must be true")
    if not candidate.location_feasible:
        _fail("selected candidate location_feasible must be true")
    if not candidate.physical_feasible:
        _fail("selected candidate physical_feasible must be true")
    if not candidate.domain_guard_satisfied:
        _fail("selected candidate domain_guard_satisfied must be true")

    if candidate.candidate_id not in resolution.considered_candidate_ids:
        _fail("selected_candidate_id must appear in considered_candidate_ids")


def start_activity_from_selection(
    *,
    current_state: Mapping[str, Any],
    policy_version: str,
    resolution: Mapping[str, Any] | DecisionResolution,
    selected_candidate: Mapping[str, Any] | ActionCandidate,
    start_spec: Mapping[str, Any] | ActivityStartSpec,
    input_state_refs: Sequence[str],
) -> dict[str, Any]:
    """Bridge SELECT_CANDIDATE → ActiveActivity with provenance. Pure / fail-closed."""
    state = validate_checkpoint(deepcopy(dict(current_state)))
    if state["context"].get("active_activity") is not None:
        _fail("active_activity already exists; cannot start a new activity")

    res = _parse_resolution(resolution)
    cand = _parse_candidate(selected_candidate)
    spec = parse_activity_start_spec(start_spec)
    policy = _require_str(policy_version, "policy_version")

    _validate_select_candidate_for_start(
        character_id=state["character_id"],
        resolution=res,
        candidate=cand,
    )

    processed = parse_rfc3339(state["processed_through"], field="processed_through")
    start_instant = parse_rfc3339(spec.actual_start, field="actual_start")
    if start_instant < processed:
        _fail("actual_start before CurrentState.processed_through")

    details = _validate_details_for_activity_type(spec.activity_type, spec.details)

    # Builder-owned merge: candidate rules + start rule; unique/sorted for persistence.
    rule_ids = _canonical_unique_sorted_strs(
        list(cand.rule_ids) + [ACTIVITY_START_RULE_ID],
        label="runtime_context.rule_ids",
    )
    runtime_context = {
        "schema_version": 1,
        "decision_key": res.decision_key,
        "selected_candidate_id": cand.candidate_id,
        "selected_candidate_key": cand.candidate_key,
        "action_kind": cand.action_kind,
        "priority_tier": cand.priority_tier,
        "source_kind": cand.source_kind,
        "source_ref": cand.source_ref,
        "interruptibility": spec.interruptibility,
        "local_preference_permille": cand.local_preference_permille,
        "rule_ids": rule_ids,
    }

    # Stronger runtime-start contract than ActualEvent schema alone:
    # list/tuple only, unique non-empty refs, canonical sort, then evidence builder.
    input_refs = _require_unique_sorted_str_sequence(
        input_state_refs, label="input_state_refs"
    )
    evidence = build_decision_evidence(
        decision_type="ACTION_SELECTION",
        policy_version=policy,
        rule_ids=list(res.rule_ids),
        input_state_refs=input_refs,
        random_key=res.random_key,
        selected_result=_require_str(res.selected_candidate_id, "selected_candidate_id"),
    )
    causes = _build_causes(
        resolution=res, candidate=cand, commitment_id=spec.commitment_id
    )

    activity_instance_id = derive_activity_instance_id(
        character_id=state["character_id"],
        decision_key=res.decision_key,
        selected_candidate_id=cand.candidate_id,
    )

    activity: dict[str, Any] = {
        "schema_version": 1,
        "activity_instance_id": activity_instance_id,
        "activity_type": spec.activity_type,
        "actual_start": spec.actual_start,
        "planned_end": spec.planned_end,
        "expected_end": spec.expected_end,
        "expected_end_window": (
            None
            if spec.expected_end_window is None
            else dict(spec.expected_end_window)
        ),
        "commitment_id": spec.commitment_id,
        "companions": [dict(c) for c in spec.companions],
        "effects_on_end": [dict(e) for e in spec.effects_on_end],
        "pending_captures": [],
        "runtime_context": runtime_context,
        "causes": causes,
        "details": details,
        "decision_evidence": evidence,
    }
    if spec.location_id is not None:
        activity["location_id"] = spec.location_id

    validated = validate_active_activity(activity)
    assert validated is not None

    out = deepcopy(state)
    out["context"]["active_activity"] = validated
    # processed_through must not advance.
    return validate_checkpoint(out)


def build_active_decision_context(
    *,
    active_activity: Mapping[str, Any],
    continuation_feasible: bool,
) -> ActiveDecisionContext:
    """Rebuild Slice 2D ActiveDecisionContext from persisted runtime_context."""
    activity = validate_active_activity(dict(active_activity))
    assert activity is not None
    ctx = activity.get("runtime_context")
    if not isinstance(ctx, Mapping):
        _fail("legacy/Foundation activity without runtime_context cannot rebuild context")
    _require_bool(continuation_feasible, "continuation_feasible")
    rule_ids_raw = ctx.get("rule_ids")
    if not isinstance(rule_ids_raw, (list, tuple)):
        _fail("runtime_context.rule_ids: expected list")
    return ActiveDecisionContext(
        activity_instance_id=activity["activity_instance_id"],
        action_kind=_require_str(ctx.get("action_kind"), "action_kind"),
        actual_start=activity["actual_start"],
        interruptibility=_require_str(ctx.get("interruptibility"), "interruptibility"),
        current_priority_tier=_require_str(
            ctx.get("priority_tier"), "priority_tier"
        ),
        continuation_feasible=continuation_feasible,
        local_preference_permille=ctx.get("local_preference_permille"),
        source_kind=_require_str(ctx.get("source_kind"), "source_kind"),
        source_ref=_require_str(ctx.get("source_ref"), "source_ref"),
        rule_ids=tuple(_require_str(x, "rule_id") for x in rule_ids_raw),
    )


def apply_continue_current(
    *,
    current_state: Mapping[str, Any],
    resolution: Mapping[str, Any] | DecisionResolution,
) -> dict[str, Any]:
    """CONTINUE_CURRENT bridge: no new activity, no evidence/start mutation."""
    state = validate_checkpoint(deepcopy(dict(current_state)))
    active = state["context"].get("active_activity")
    if active is None:
        _fail("CONTINUE_CURRENT requires an active_activity")
    res = _parse_resolution(resolution)
    if res.result_kind != "CONTINUE_CURRENT":
        _fail(f"result_kind must be CONTINUE_CURRENT, got {res.result_kind!r}")
    if res.activity_instance_id != active["activity_instance_id"]:
        _fail("CONTINUE_CURRENT activity_instance_id mismatch vs active_activity")
    # Semantic-equivalent copy; no activity-start fact mutation.
    return validate_checkpoint(deepcopy(state))
