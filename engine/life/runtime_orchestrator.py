"""Life Engine v2 Slice 5B2C3 — pure target-time orchestration loop.

Composes reviewed microsteps into one chronological in-memory state machine.
Does **not** own disk/file writes, timeline/shard merge, Camera Roll write,
state_revision/CAS, Git/life branch, heartbeat/workflow, or production
activation. Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, Mapping, Sequence

from .activity_finalization import (
    finalize_runtime_activity_from_decision_switch,
    finalize_runtime_activity_from_exact_end,
)
from .activity_lifecycle import (
    RuntimeWakeupProjectionFacts,
    apply_active_window_continuation_guard,
    integrate_active_interval,
    parse_runtime_wakeup_projection_facts,
    reconcile_active_runtime_queue,
    reconcile_activity_end_event,
)
from .activity_materialization import (
    RuntimeActivityMaterializationFacts,
    build_activity_end_event,
    build_runtime_materialization_context,
    parse_runtime_activity_materialization_facts,
    start_runtime_activity_from_selection,
)
from .activity_runtime import apply_continue_current
from .canonical import canonical_hash, canonical_json
from .capture_emitters import (
    derive_capture_source_key,
    validate_capture_source_moment,
)
from .checkpoint import validate_checkpoint
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .history import next_history_hash
from .ids import stable_id
from .invariants import assert_queue_canonically_sorted, queue_event_sort_key
from .policy import assert_policy_matches_checkpoint, normalize_policy
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .runtime_capture import (
    RuntimeCaptureContextFacts,
    process_runtime_capture_moments,
    project_runtime_camera_roll_handoff,
    resolve_runtime_repeat_step,
)
from .runtime_fact_provider import (
    RuntimeDecisionProviderResult,
    RuntimeFactProvider,
    RuntimeFactRequestContext,
    RuntimeTargetRequest,
    parse_runtime_target_request,
)
from .runtime_decision import (
    RuntimeDecisionFacts,
    RuntimeDecisionFrame,
    RuntimeDecisionTrigger,
    derive_managed_v2_decision_wakeup_event_id,
    parse_runtime_decision_facts,
    runtime_trigger_from_v2_wakeup,
)
from .schema import reject_binary_floats, validate_instance
from .social_runtime import (
    PendingImmediateAccept,
    RuntimeSocialFacts,
    SocialResponseState,
    build_runtime_social_decision,
    parse_runtime_social_facts,
    validate_social_response_state,
)
from .timeutil import parse_rfc3339, require_canonical_timestamp
from .wakeups import (
    FutureCandidate,
    V2_DECISION_WAKEUP_PRIORITY,
    build_v2_decision_wakeup_event,
    is_managed_v2_decision_wakeup,
)

_TARGET_INPUT_FIELDS = frozenset(
    {
        "target_time",
        "event_budget",
        "character_source_sha",
        "capture_source_moments",
        "decision_steps",
        "wakeup_steps",
    }
)

_DECISION_STEP_FIELDS = frozenset(
    {
        "trigger_id",
        "decision_facts",
        "social_facts",
        "materialization_facts",
    }
)

_WAKEUP_STEP_FIELDS = frozenset(
    {
        "activity_instance_id",
        "as_of",
        "projection_facts",
    }
)

_C3_OWNED_TRIGGERS = frozenset({"POST_FINALIZE", "IMMEDIATE_REASSESSMENT"})


def _fail(
    detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE
) -> None:
    raise LifeEngineError(code, detail)


def _require_nonempty_str(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _require_true_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be int (no bool/float)")
    return value


def _require_non_neg_int(value: object, *, label: str) -> int:
    n = _require_true_int(value, label=label)
    if n < 0:
        _fail(f"{label} must be >= 0")
    return n


def _reject_unknown(
    data: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = set(data.keys()) - allowed
    if unknown:
        _fail(f"{label}: unknown fields {sorted(unknown)}")


def _clone_bundle(bundle: RuntimeBundle) -> RuntimeBundle:
    return RuntimeBundle(
        current_state=validate_checkpoint(deepcopy(dict(bundle.current_state))),
        schedule_state=deepcopy(dict(bundle.schedule_state)),
        relation_state=deepcopy(dict(bundle.relation_state)),
        home_state=deepcopy(dict(bundle.home_state)),
        consumables_state=deepcopy(dict(bundle.consumables_state)),
        wardrobe_state=deepcopy(dict(bundle.wardrobe_state)),
        finance_state=deepcopy(dict(bundle.finance_state)),
    )


def _replace_current(
    bundle: RuntimeBundle, current: Mapping[str, Any]
) -> RuntimeBundle:
    return RuntimeBundle(
        current_state=validate_checkpoint(deepcopy(dict(current))),
        schedule_state=deepcopy(dict(bundle.schedule_state)),
        relation_state=deepcopy(dict(bundle.relation_state)),
        home_state=deepcopy(dict(bundle.home_state)),
        consumables_state=deepcopy(dict(bundle.consumables_state)),
        wardrobe_state=deepcopy(dict(bundle.wardrobe_state)),
        finance_state=deepcopy(dict(bundle.finance_state)),
    )


@dataclass(frozen=True)
class RuntimeDecisionStepInput:
    trigger_id: str
    decision_facts: Mapping[str, Any] | RuntimeDecisionFacts
    social_facts: Mapping[str, Any]
    materialization_facts: Mapping[str, Any] | RuntimeActivityMaterializationFacts | None

    def as_dict(self) -> dict[str, Any]:
        facts: Any = self.decision_facts
        if not isinstance(facts, RuntimeDecisionFacts):
            facts = dict(facts)
        mat: Any = self.materialization_facts
        if mat is not None and not isinstance(
            mat, RuntimeActivityMaterializationFacts
        ):
            mat = dict(mat)
        return {
            "trigger_id": self.trigger_id,
            "decision_facts": facts,
            "social_facts": dict(self.social_facts),
            "materialization_facts": mat,
        }


@dataclass(frozen=True)
class RuntimeWakeupStepInput:
    activity_instance_id: str
    as_of: str
    projection_facts: Mapping[str, Any] | RuntimeWakeupProjectionFacts

    def as_dict(self) -> dict[str, Any]:
        facts = self.projection_facts
        if isinstance(facts, RuntimeWakeupProjectionFacts):
            facts_out: Any = facts
        else:
            facts_out = dict(facts)
        return {
            "activity_instance_id": self.activity_instance_id,
            "as_of": self.as_of,
            "projection_facts": facts_out,
        }


@dataclass(frozen=True)
class RuntimeTargetInputs:
    target_time: str
    event_budget: int
    character_source_sha: str
    capture_source_moments: tuple[Mapping[str, Any], ...]
    decision_steps: tuple[RuntimeDecisionStepInput, ...]
    wakeup_steps: tuple[RuntimeWakeupStepInput, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_time": self.target_time,
            "event_budget": self.event_budget,
            "character_source_sha": self.character_source_sha,
            "capture_source_moments": [dict(m) for m in self.capture_source_moments],
            "decision_steps": [s.as_dict() for s in self.decision_steps],
            "wakeup_steps": [s.as_dict() for s in self.wakeup_steps],
        }


def _observability_plain(value: Any) -> Any:
    """Recursively convert observability values to canonical-JSON-safe mappings.

    Structured decision frames only — no dataclass/object leftovers.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        _fail("observability mapping forbids float values")
    if isinstance(value, Mapping):
        return {str(k): _observability_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) and not hasattr(value, "_fields"):
        return [_observability_plain(v) for v in value]
    if hasattr(value, "as_dict") and callable(getattr(value, "as_dict")):
        return _observability_plain(value.as_dict())
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return {
            name: _observability_plain(getattr(value, name))
            for name in value._fields
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _observability_plain(getattr(value, f.name))
            for f in fields(value)
        }
    _fail(f"non-serializable observability value: {type(value).__name__}")


@dataclass(frozen=True)
class RuntimeTargetAdvanceResult:
    bundle: RuntimeBundle
    social_response_state: SocialResponseState
    actual_events: tuple[Mapping[str, Any], ...]
    camera_roll_records: tuple[Mapping[str, Any], ...]
    camera_roll_records_by_shard_path: Mapping[str, tuple[Mapping[str, Any], ...]]
    consumed_source_keys: tuple[str, ...]
    consumed_trigger_ids: tuple[str, ...]
    decision_frames: tuple[RuntimeDecisionFrame, ...]
    microsteps_used: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle": {
                "current_state": dict(self.bundle.current_state),
                "schedule_state": dict(self.bundle.schedule_state),
                "relation_state": dict(self.bundle.relation_state),
                "home_state": dict(self.bundle.home_state),
                "consumables_state": dict(self.bundle.consumables_state),
                "wardrobe_state": dict(self.bundle.wardrobe_state),
                "finance_state": dict(self.bundle.finance_state),
            },
            "social_response_state": self.social_response_state.as_dict(),
            "actual_events": [dict(e) for e in self.actual_events],
            "camera_roll_records": [dict(r) for r in self.camera_roll_records],
            "camera_roll_records_by_shard_path": {
                path: [dict(r) for r in rows]
                for path, rows in self.camera_roll_records_by_shard_path.items()
            },
            "consumed_source_keys": list(self.consumed_source_keys),
            "consumed_trigger_ids": list(self.consumed_trigger_ids),
            "decision_frames": [
                _observability_plain(frame) for frame in self.decision_frames
            ],
            "microsteps_used": self.microsteps_used,
        }


def parse_runtime_decision_step_input(
    raw: RuntimeDecisionStepInput | Mapping[str, Any],
) -> RuntimeDecisionStepInput:
    if isinstance(raw, RuntimeDecisionStepInput):
        data = {
            "trigger_id": raw.trigger_id,
            "decision_facts": raw.decision_facts,
            "social_facts": raw.social_facts,
            "materialization_facts": raw.materialization_facts,
        }
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeDecisionStepInput must be mapping or dataclass")
    reject_binary_floats(
        {
            k: v
            for k, v in data.items()
            if k != "decision_facts"
            and k != "social_facts"
            and k != "materialization_facts"
        }
    )
    _reject_unknown(data, _DECISION_STEP_FIELDS, "RuntimeDecisionStepInput")
    missing = _DECISION_STEP_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeDecisionStepInput missing fields: {sorted(missing)}")
    trigger_id = _require_nonempty_str(data["trigger_id"], label="trigger_id")
    decision_facts = data["decision_facts"]
    if not isinstance(decision_facts, (Mapping, RuntimeDecisionFacts)):
        _fail("decision_facts must be mapping or RuntimeDecisionFacts")
    social_facts = data["social_facts"]
    if not isinstance(social_facts, Mapping):
        _fail("social_facts must be a mapping")
    mat = data["materialization_facts"]
    if mat is not None and not isinstance(
        mat, (Mapping, RuntimeActivityMaterializationFacts)
    ):
        _fail(
            "materialization_facts must be null, mapping, or "
            "RuntimeActivityMaterializationFacts"
        )
    return RuntimeDecisionStepInput(
        trigger_id=trigger_id,
        decision_facts=decision_facts
        if isinstance(decision_facts, RuntimeDecisionFacts)
        else dict(decision_facts),
        social_facts=dict(social_facts),
        materialization_facts=(
            None
            if mat is None
            else (
                mat
                if isinstance(mat, RuntimeActivityMaterializationFacts)
                else dict(mat)
            )
        ),
    )


def parse_runtime_wakeup_step_input(
    raw: RuntimeWakeupStepInput | Mapping[str, Any],
) -> RuntimeWakeupStepInput:
    if isinstance(raw, RuntimeWakeupStepInput):
        data = {
            "activity_instance_id": raw.activity_instance_id,
            "as_of": raw.as_of,
            "projection_facts": raw.projection_facts,
        }
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeWakeupStepInput must be mapping or dataclass")
    _reject_unknown(data, _WAKEUP_STEP_FIELDS, "RuntimeWakeupStepInput")
    missing = _WAKEUP_STEP_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeWakeupStepInput missing fields: {sorted(missing)}")
    activity_instance_id = _require_nonempty_str(
        data["activity_instance_id"], label="activity_instance_id"
    )
    as_of = require_canonical_timestamp(data["as_of"], field="as_of")
    projection_facts = data["projection_facts"]
    if not isinstance(projection_facts, (Mapping, RuntimeWakeupProjectionFacts)):
        _fail("projection_facts must be mapping or RuntimeWakeupProjectionFacts")
    # Validate structure early (no hidden defaults).
    parse_runtime_wakeup_projection_facts(projection_facts)
    return RuntimeWakeupStepInput(
        activity_instance_id=activity_instance_id,
        as_of=as_of,
        projection_facts=(
            projection_facts
            if isinstance(projection_facts, RuntimeWakeupProjectionFacts)
            else dict(projection_facts)
        ),
    )


def parse_runtime_target_inputs(
    raw: RuntimeTargetInputs | Mapping[str, Any],
) -> RuntimeTargetInputs:
    if isinstance(raw, RuntimeTargetInputs):
        data = {
            "target_time": raw.target_time,
            "event_budget": raw.event_budget,
            "character_source_sha": raw.character_source_sha,
            "capture_source_moments": raw.capture_source_moments,
            "decision_steps": raw.decision_steps,
            "wakeup_steps": raw.wakeup_steps,
        }
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeTargetInputs must be mapping or dataclass")
    reject_binary_floats(
        {
            "target_time": data.get("target_time"),
            "event_budget": data.get("event_budget"),
            "character_source_sha": data.get("character_source_sha"),
        }
    )
    _reject_unknown(data, _TARGET_INPUT_FIELDS, "RuntimeTargetInputs")
    missing = _TARGET_INPUT_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeTargetInputs missing fields: {sorted(missing)}")

    target_time = require_canonical_timestamp(
        data["target_time"], field="target_time"
    )
    event_budget = _require_non_neg_int(data["event_budget"], label="event_budget")
    character_source_sha = _require_nonempty_str(
        data["character_source_sha"], label="character_source_sha"
    )

    moments_raw = data["capture_source_moments"]
    if not isinstance(moments_raw, (list, tuple)) or isinstance(
        moments_raw, (str, bytes)
    ):
        _fail(
            "capture_source_moments must be a list/tuple "
            "(scalar/string/bytes/set/generator coercion forbidden)"
        )
    moments: list[Mapping[str, Any]] = []
    for item in moments_raw:
        if not isinstance(item, Mapping):
            _fail("capture_source_moments entries must be mappings")
        moments.append(validate_capture_source_moment(dict(item)))

    # Same source identity: exact replay/dedup allowed; conflicting payload fails.
    by_key: dict[str, Mapping[str, Any]] = {}
    ordered_keys: list[str] = []
    for moment in moments:
        key = derive_capture_source_key(
            emitter_rule_id=moment["emitter_rule_id"],
            semantic_source_ref=moment["semantic_source_ref"],
            moment_ref=moment["moment_ref"],
        )
        prior = by_key.get(key)
        if prior is None:
            by_key[key] = moment
            ordered_keys.append(key)
        elif canonical_json(prior) != canonical_json(moment):
            _fail(f"conflicting capture source moments for source_key={key!r}")

    deduped = tuple(by_key[k] for k in ordered_keys)

    steps_raw = data["decision_steps"]
    if not isinstance(steps_raw, (list, tuple)) or isinstance(steps_raw, (str, bytes)):
        _fail("decision_steps must be a list/tuple")
    decision_steps: list[RuntimeDecisionStepInput] = []
    seen_triggers: set[str] = set()
    for item in steps_raw:
        step = parse_runtime_decision_step_input(item)
        if step.trigger_id in seen_triggers:
            _fail(f"duplicate decision_steps trigger_id: {step.trigger_id!r}")
        seen_triggers.add(step.trigger_id)
        decision_steps.append(step)

    wakeup_raw = data["wakeup_steps"]
    if not isinstance(wakeup_raw, (list, tuple)) or isinstance(
        wakeup_raw, (str, bytes)
    ):
        _fail("wakeup_steps must be a list/tuple")
    wakeup_steps: list[RuntimeWakeupStepInput] = []
    seen_wake: set[tuple[str, str]] = set()
    for item in wakeup_raw:
        step = parse_runtime_wakeup_step_input(item)
        key = (step.activity_instance_id, step.as_of)
        if key in seen_wake:
            _fail(
                "duplicate wakeup_steps key "
                f"(activity_instance_id={step.activity_instance_id!r}, "
                f"as_of={step.as_of!r})"
            )
        seen_wake.add(key)
        wakeup_steps.append(step)

    return RuntimeTargetInputs(
        target_time=target_time,
        event_budget=event_budget,
        character_source_sha=character_source_sha,
        capture_source_moments=deduped,
        decision_steps=tuple(decision_steps),
        wakeup_steps=tuple(wakeup_steps),
    )


def derive_post_finalize_decision_key(
    *,
    character_id: str,
    actual_event_id: str,
    actual_end: str,
) -> str:
    return stable_id(
        "post-finalize-decision",
        character_id,
        actual_event_id,
        actual_end,
    )


def build_post_finalize_decision_wakeup(
    *,
    character_id: str,
    actual_event: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministic due-now v2 DECISION_WAKEUP after C2B finalization."""
    if not isinstance(actual_event, Mapping):
        _fail("actual_event must be a mapping")
    event_id = _require_nonempty_str(actual_event.get("event_id"), label="event_id")
    actual_end = require_canonical_timestamp(
        actual_event.get("actual_end"), field="actual_end"
    )
    decision_key = derive_post_finalize_decision_key(
        character_id=character_id,
        actual_event_id=event_id,
        actual_end=actual_end,
    )
    return build_v2_decision_wakeup_event(
        character_id=character_id,
        candidate=FutureCandidate(
            due_at=actual_end,
            trigger_kind="POST_FINALIZE",
            decision_key=decision_key,
            reason_code="POST_FINALIZE_FRESH_DECISION",
            metric=None,
            target_band=None,
            direction=None,
            source_kind="ACTUAL_EVENT",
            source_ref=event_id,
        ),
    )


def derive_immediate_reassessment_decision_key(
    *,
    character_id: str,
    activity_instance_id: str,
    processed_through: str,
    immediate_reassessment_reasons: Sequence[str],
) -> str:
    reasons = sorted(
        _require_nonempty_str(r, label="immediate_reassessment_reason")
        for r in immediate_reassessment_reasons
    )
    return stable_id(
        "immediate-reassessment",
        character_id,
        activity_instance_id,
        processed_through,
        canonical_hash(reasons),
    )


def build_immediate_reassessment_wakeup(
    *,
    character_id: str,
    activity_instance_id: str,
    processed_through: str,
    immediate_reassessment_reasons: Sequence[str],
) -> dict[str, Any]:
    reasons = sorted(
        _require_nonempty_str(r, label="immediate_reassessment_reason")
        for r in immediate_reassessment_reasons
    )
    reason_hash = canonical_hash(reasons)
    decision_key = derive_immediate_reassessment_decision_key(
        character_id=character_id,
        activity_instance_id=activity_instance_id,
        processed_through=processed_through,
        immediate_reassessment_reasons=reasons,
    )
    return build_v2_decision_wakeup_event(
        character_id=character_id,
        candidate=FutureCandidate(
            due_at=processed_through,
            trigger_kind="IMMEDIATE_REASSESSMENT",
            decision_key=decision_key,
            reason_code=f"IMMEDIATE_REASSESSMENT:{reason_hash}",
            metric=None,
            target_band=None,
            direction=None,
            source_kind="CURRENT_ACTIVITY",
            source_ref=activity_instance_id,
        ),
    )


def _enqueue_queue_event(
    current: Mapping[str, Any], event: Mapping[str, Any]
) -> dict[str, Any]:
    """Insert a validated queued event without importing clock.py."""
    data = dict(event)
    validate_instance(data, "queued_internal_event")
    out = deepcopy(dict(current))
    events = list(out["pending_queue"]["events"] or [])
    existing_ids = {e["event_id"] for e in events}
    if data["event_id"] in existing_ids:
        # Exact replay: identical row is idempotent; conflicting id fails.
        prior = next(e for e in events if e["event_id"] == data["event_id"])
        if canonical_json(prior) == canonical_json(data):
            return out
        raise LifeEngineError(ErrorCode.DUPLICATE_ID, data["event_id"])
    # Managed v2 DECISION_WAKEUP ownership: at most one row.
    if is_managed_v2_decision_wakeup(data):
        other_managed = [e for e in events if is_managed_v2_decision_wakeup(e)]
        if other_managed:
            _fail(
                "conflicting managed v2 DECISION_WAKEUP at insertion "
                f"(existing={other_managed[0]['event_id']!r} "
                f"new={data['event_id']!r})"
            )
    events.append(data)
    events.sort(key=queue_event_sort_key)
    assert_queue_canonically_sorted(events)
    out["pending_queue"] = {"cursor": 0, "events": events}
    return validate_checkpoint(out)


def _exact_pop_event(
    current: Mapping[str, Any], *, event_id: str
) -> dict[str, Any]:
    out = deepcopy(dict(current))
    events = list(out["pending_queue"]["events"] or [])
    matches = [e for e in events if e["event_id"] == event_id]
    if len(matches) != 1:
        _fail(
            f"exact-pop requires exactly one queued event_id={event_id!r} "
            f"(matches={len(matches)})"
        )
    kept = [e for e in events if e["event_id"] != event_id]
    assert_queue_canonically_sorted(kept)
    out["pending_queue"] = {"cursor": 0, "events": kept}
    return validate_checkpoint(out)


def _managed_due_now(
    events: Sequence[Mapping[str, Any]], *, now: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in events:
        if not is_managed_v2_decision_wakeup(raw):
            continue
        due = require_canonical_timestamp(raw.get("due_at"), field="due_at")
        if due == now:
            out.append(dict(raw))
    return out


def _remove_managed_v2_wakeups(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    kept = [dict(e) for e in events if not is_managed_v2_decision_wakeup(e)]
    kept.sort(key=queue_event_sort_key)
    assert_queue_canonically_sorted(kept)
    return kept


def _apply_immediate_reassessment_bridge(
    *,
    bundle: RuntimeBundle,
    character_id: str,
    processed_through: str,
    immediate_reasons: Sequence[str],
    consumed_immediate_keys: set[str],
    generated_c3_trigger_ids: set[str],
) -> RuntimeBundle:
    """Enqueue due-now IMMEDIATE_REASSESSMENT when needed (call-local one-shot)."""
    if not immediate_reasons:
        return bundle
    active = bundle.current_state["context"].get("active_activity")
    if active is None:
        return bundle
    instance_id = _require_nonempty_str(
        active.get("activity_instance_id"), label="activity_instance_id"
    )
    decision_key = derive_immediate_reassessment_decision_key(
        character_id=character_id,
        activity_instance_id=instance_id,
        processed_through=processed_through,
        immediate_reassessment_reasons=immediate_reasons,
    )
    if decision_key in consumed_immediate_keys:
        return bundle

    events = list(bundle.current_state["pending_queue"]["events"] or [])
    if _managed_due_now(events, now=processed_through):
        # Existing due-now managed wakeup covers immediate reasons.
        consumed_immediate_keys.add(decision_key)
        return bundle

    wakeup = build_immediate_reassessment_wakeup(
        character_id=character_id,
        activity_instance_id=instance_id,
        processed_through=processed_through,
        immediate_reassessment_reasons=immediate_reasons,
    )
    # Replace future managed wakeup with the due-now immediate bridge.
    current = deepcopy(dict(bundle.current_state))
    current["pending_queue"] = {
        "cursor": 0,
        "events": _remove_managed_v2_wakeups(events),
    }
    generated_c3_trigger_ids.add(wakeup["event_id"])
    current = _enqueue_queue_event(current, wakeup)
    consumed_immediate_keys.add(decision_key)
    return _replace_current(bundle, current)


def _lookup_decision_step(
    steps_by_id: Mapping[str, RuntimeDecisionStepInput], *, trigger_id: str
) -> RuntimeDecisionStepInput:
    step = steps_by_id.get(trigger_id)
    if step is None:
        _fail(f"missing RuntimeDecisionStepInput for trigger_id={trigger_id!r}")
    return step


def _lookup_wakeup_step(
    steps: Mapping[tuple[str, str], RuntimeWakeupStepInput],
    *,
    activity_instance_id: str,
    as_of: str,
    target_time: str,
) -> RuntimeWakeupStepInput:
    step = steps.get((activity_instance_id, as_of))
    if step is None:
        _fail(
            "missing RuntimeWakeupStepInput for "
            f"activity_instance_id={activity_instance_id!r} as_of={as_of!r}"
        )
    facts = parse_runtime_wakeup_projection_facts(step.projection_facts)
    if parse_rfc3339(facts.projection_horizon_end, field="projection_horizon_end") < (
        parse_rfc3339(target_time, field="target_time")
    ):
        _fail("projection_horizon_end must be >= target_time")
    return step



@dataclass(frozen=True)
class _RuntimeDecisionSourceStep:
    decision_facts: RuntimeDecisionFacts | Mapping[str, Any]
    social_facts: RuntimeSocialFacts | Mapping[str, Any]
    materialization_facts: RuntimeActivityMaterializationFacts | Mapping[str, Any] | None


class _StaticRuntimeInputSource:
    """Adapter preserving the existing precomputed RuntimeTargetInputs contract."""

    def __init__(self, inputs: RuntimeTargetInputs) -> None:
        self.target_time = inputs.target_time
        self.event_budget = inputs.event_budget
        self.character_source_sha = inputs.character_source_sha
        self.initial_capture_moments = tuple(
            deepcopy(dict(moment)) for moment in inputs.capture_source_moments
        )
        self._decision_by_id = {step.trigger_id: step for step in inputs.decision_steps}
        self._wakeup_by_key = {
            (step.activity_instance_id, step.as_of): step
            for step in inputs.wakeup_steps
        }

    def decision_step(
        self,
        *,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        trigger: RuntimeDecisionTrigger,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        finalized_events_since_base: Sequence[Mapping[str, Any]],
    ) -> _RuntimeDecisionSourceStep:
        del (
            bundle,
            social_response_state,
            behavior_policy,
            reference_sets,
            finalized_events_since_base,
        )
        step = _lookup_decision_step(
            self._decision_by_id, trigger_id=trigger.trigger_id
        )
        return _RuntimeDecisionSourceStep(
            decision_facts=step.decision_facts,
            social_facts=step.social_facts,
            materialization_facts=step.materialization_facts,
        )

    def materialization_facts(
        self,
        *,
        step: _RuntimeDecisionSourceStep,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        frame: RuntimeDecisionFrame,
        pending_immediate_accept: PendingImmediateAccept | None,
        decision_facts: RuntimeDecisionFacts | Mapping[str, Any],
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        trigger: RuntimeDecisionTrigger,
        finalized_events_since_base: Sequence[Mapping[str, Any]],
    ) -> RuntimeActivityMaterializationFacts | Mapping[str, Any] | None:
        del (
            bundle,
            social_response_state,
            frame,
            pending_immediate_accept,
            decision_facts,
            behavior_policy,
            reference_sets,
            trigger,
            finalized_events_since_base,
        )
        return step.materialization_facts

    def wakeup_facts(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        as_of: str,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        finalized_events_since_base: Sequence[Mapping[str, Any]],
    ) -> RuntimeWakeupProjectionFacts:
        del bundle, behavior_policy, reference_sets, finalized_events_since_base
        step = _lookup_wakeup_step(
            self._wakeup_by_key,
            activity_instance_id=_require_nonempty_str(
                active_activity.get("activity_instance_id"),
                label="activity_instance_id",
            ),
            as_of=as_of,
            target_time=self.target_time,
        )
        return parse_runtime_wakeup_projection_facts(step.projection_facts)

    def capture_source_moments(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        from_time: str,
    ) -> tuple[Mapping[str, Any], ...]:
        del bundle, active_activity, from_time
        # Static tape mode supplied the complete source set at entry.
        return ()


class _ProviderRuntimeInputSource:
    """Memoized adapter for on-demand provider-driven runtime facts."""

    def __init__(
        self,
        request: RuntimeTargetRequest,
        provider: RuntimeFactProvider,
    ) -> None:
        self.request = request
        self.provider = provider
        self.target_time = request.target_time
        self.event_budget = request.event_budget
        self.character_source_sha = request.character_source_sha
        self.initial_capture_moments: tuple[Mapping[str, Any], ...] = ()
        self._decision_cache: dict[str, _RuntimeDecisionSourceStep] = {}
        self._decision_context_hash: dict[str, str] = {}
        self._materialization_cache: dict[
            tuple[str, str], RuntimeActivityMaterializationFacts
        ] = {}
        self._materialization_context_hash: dict[tuple[str, str], str] = {}
        self._wakeup_cache: dict[
            tuple[str, str], RuntimeWakeupProjectionFacts
        ] = {}
        self._wakeup_context_hash: dict[tuple[str, str], str] = {}
        self._capture_cache: dict[
            tuple[str, str, str], tuple[Mapping[str, Any], ...]
        ] = {}

    def decision_step(
        self,
        *,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        trigger: RuntimeDecisionTrigger,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        finalized_events_since_base: Sequence[Mapping[str, Any]],
    ) -> _RuntimeDecisionSourceStep:
        request_context = RuntimeFactRequestContext(finalized_events_since_base)
        key = trigger.trigger_id
        cached = self._decision_cache.get(key)
        if cached is not None:
            if self._decision_context_hash[key] != request_context.history_context_hash:
                _fail("memoized decision request history context changed")
            return deepcopy(cached)

        result = self.provider.decision_inputs(
            bundle=_clone_bundle(bundle),
            social_response_state=deepcopy(social_response_state),
            trigger=deepcopy(trigger),
            behavior_policy=deepcopy(dict(behavior_policy)),
            reference_sets=reference_sets,
            target_request=self.request,
            request_context=request_context,
        )
        if not isinstance(result, RuntimeDecisionProviderResult):
            _fail("RuntimeFactProvider.decision_inputs must return RuntimeDecisionProviderResult")
        decision_facts = parse_runtime_decision_facts(
            deepcopy(result.decision_facts), now=trigger.occurred_at
        )
        social_facts = parse_runtime_social_facts(deepcopy(result.social_facts))
        step = _RuntimeDecisionSourceStep(
            decision_facts=decision_facts,
            social_facts=social_facts,
            materialization_facts=None,
        )
        self._decision_cache[key] = deepcopy(step)
        self._decision_context_hash[key] = request_context.history_context_hash
        return deepcopy(step)

    def materialization_facts(
        self,
        *,
        step: _RuntimeDecisionSourceStep,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        frame: RuntimeDecisionFrame,
        pending_immediate_accept: PendingImmediateAccept | None,
        decision_facts: RuntimeDecisionFacts | Mapping[str, Any],
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        trigger: RuntimeDecisionTrigger,
        finalized_events_since_base: Sequence[Mapping[str, Any]],
    ) -> RuntimeActivityMaterializationFacts:
        del step
        request_context = RuntimeFactRequestContext(finalized_events_since_base)
        selected = frame.selected_candidate
        if selected is None:
            _fail("provider materialization request requires selected candidate")
        key = (trigger.trigger_id, selected.candidate_id)
        cached = self._materialization_cache.get(key)
        if cached is not None:
            if (
                self._materialization_context_hash[key]
                != request_context.history_context_hash
            ):
                _fail("memoized materialization request history context changed")
            return deepcopy(cached)

        context = build_runtime_materialization_context(
            bundle=bundle, frame=frame, pending_immediate_accept=pending_immediate_accept
        )
        raw = self.provider.materialization_facts(
            bundle=_clone_bundle(bundle),
            social_response_state=deepcopy(social_response_state),
            frame=deepcopy(frame),
            context=context,
            decision_facts=deepcopy(decision_facts),
            behavior_policy=deepcopy(dict(behavior_policy)),
            reference_sets=reference_sets,
            target_request=self.request,
            request_context=request_context,
        )
        parsed = parse_runtime_activity_materialization_facts(deepcopy(raw))
        if parsed.selected_candidate_id != selected.candidate_id:
            _fail("provider materialization selected_candidate_id mismatch")
        if context.pending_immediate_accept is not None:
            if parsed.social_contact_mode != context.pending_immediate_accept.contact_mode:
                _fail("provider social_contact_mode differs from exact pending handoff")
            # C1's static contract reserves this field for scheduled social only.
            # Validate the provider's explicit answer, then keep the existing C1
            # pending object as the sole immediate contact-mode authority.
            parsed = replace(parsed, social_contact_mode=None)
        self._materialization_cache[key] = deepcopy(parsed)
        self._materialization_context_hash[key] = request_context.history_context_hash
        return deepcopy(parsed)

    def wakeup_facts(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        as_of: str,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        finalized_events_since_base: Sequence[Mapping[str, Any]],
    ) -> RuntimeWakeupProjectionFacts:
        request_context = RuntimeFactRequestContext(finalized_events_since_base)
        instance_id = _require_nonempty_str(
            active_activity.get("activity_instance_id"),
            label="activity_instance_id",
        )
        key = (instance_id, as_of)
        cached = self._wakeup_cache.get(key)
        if cached is not None:
            if self._wakeup_context_hash[key] != request_context.history_context_hash:
                _fail("memoized wakeup request history context changed")
            return deepcopy(cached)

        raw = self.provider.wakeup_facts(
            bundle=_clone_bundle(bundle),
            active_activity=deepcopy(dict(active_activity)),
            as_of=as_of,
            behavior_policy=deepcopy(dict(behavior_policy)),
            reference_sets=reference_sets,
            target_request=self.request,
            request_context=request_context,
        )
        parsed = parse_runtime_wakeup_projection_facts(deepcopy(raw))
        if parse_rfc3339(
            parsed.projection_horizon_end, field="projection_horizon_end"
        ) < parse_rfc3339(self.target_time, field="target_time"):
            _fail("projection_horizon_end must be >= target_time")
        self._wakeup_cache[key] = deepcopy(parsed)
        self._wakeup_context_hash[key] = request_context.history_context_hash
        return deepcopy(parsed)

    def capture_source_moments(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        from_time: str,
    ) -> tuple[Mapping[str, Any], ...]:
        instance_id = _require_nonempty_str(
            active_activity.get("activity_instance_id"),
            label="activity_instance_id",
        )
        key = (instance_id, from_time, self.target_time)
        cached = self._capture_cache.get(key)
        if cached is not None:
            return deepcopy(cached)

        raw = self.provider.capture_source_moments(
            bundle=_clone_bundle(bundle),
            active_activity=deepcopy(dict(active_activity)),
            from_time=from_time,
            target_time=self.target_time,
            character_source_sha=self.character_source_sha,
            target_request=self.request,
        )
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            _fail("RuntimeFactProvider.capture_source_moments must return a sequence")
        rows: list[Mapping[str, Any]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                _fail("provider capture source moment must be a mapping")
            rows.append(deepcopy(dict(item)))
        result = tuple(rows)
        self._capture_cache[key] = deepcopy(result)
        return deepcopy(result)


def _guard_decision_facts(
    *,
    facts: RuntimeDecisionFacts | Mapping[str, Any],
    active: Mapping[str, Any] | None,
    now: str,
) -> RuntimeDecisionFacts | Mapping[str, Any]:
    if active is None:
        return facts
    parsed = (
        facts
        if isinstance(facts, RuntimeDecisionFacts)
        else parse_runtime_decision_facts(facts, now=now)
    )
    if parsed.continuation_feasible is None:
        _fail("continuation_feasible must be a bool when active_activity exists")
    guarded = apply_active_window_continuation_guard(
        active,
        now=now,
        source_continuation_feasible=parsed.continuation_feasible,
    )
    if isinstance(facts, RuntimeDecisionFacts):
        return replace(facts, continuation_feasible=guarded)
    out = dict(facts)
    out["continuation_feasible"] = guarded
    return out


def _assert_input_active_queue_invariants(
    current: Mapping[str, Any],
) -> None:
    """Input active queue is authoritative; validate without reconciling/repair."""
    queue = current.get("pending_queue")
    if not isinstance(queue, Mapping):
        _fail("pending_queue must be an object")
    if queue.get("cursor") != 0:
        _fail("pending_queue.cursor must be 0 at C3 entry")
    events = list(queue.get("events") or [])
    assert_queue_canonically_sorted(events)
    processed = require_canonical_timestamp(
        current["processed_through"], field="processed_through"
    )
    processed_dt = parse_rfc3339(processed, field="processed_through")
    character_id = _require_nonempty_str(
        current.get("character_id"), label="character_id"
    )
    managed_rows: list[dict[str, Any]] = []
    for event in events:
        due = require_canonical_timestamp(event.get("due_at"), field="due_at")
        if parse_rfc3339(due, field="due_at") < processed_dt:
            _fail("input queue has event due before processed_through")
        if not is_managed_v2_decision_wakeup(event):
            continue
        row = dict(event)
        managed_rows.append(row)
        payload = row.get("payload") or {}
        if not isinstance(payload, Mapping):
            _fail("managed v2 DECISION_WAKEUP payload must be an object")
        trigger_kind = payload.get("trigger_kind")
        # C3-owned bridges are call-local; never accept as input authority.
        if trigger_kind in _C3_OWNED_TRIGGERS:
            _fail(
                "pre-existing C3-owned trigger forbidden in input queue: "
                f"{trigger_kind!r}"
            )
        if row.get("priority") != V2_DECISION_WAKEUP_PRIORITY:
            _fail(
                "managed v2 DECISION_WAKEUP priority must equal "
                f"{V2_DECISION_WAKEUP_PRIORITY}"
            )
        decision_key = _require_nonempty_str(
            payload.get("decision_key"), label="payload.decision_key"
        )
        expected_id = derive_managed_v2_decision_wakeup_event_id(
            character_id=character_id,
            decision_key=decision_key,
            due_at=due,
        )
        if row.get("event_id") != expected_id:
            _fail(
                "managed v2 DECISION_WAKEUP event_id must equal deterministic "
                "managed-v2 identity"
            )

    active = current["context"].get("active_activity")
    if active is None:
        return

    # Reuse C1 reconcile as a pure validator: canonical output must equal input
    # (no repair of missing/wrong ACTIVITY_END rows).
    reconciled = reconcile_activity_end_event(events, active_activity=active)
    if canonical_json(reconciled) != canonical_json(
        [dict(e) for e in events]
    ):
        _fail(
            "input ACTIVITY_END must already equal C1 canonical schedule "
            "(no repair)"
        )

    planned_end = active.get("planned_end")
    if planned_end is None:
        if len(managed_rows) != 1:
            _fail(
                "open/window active requires exactly one managed v2 "
                "DECISION_WAKEUP in input queue"
            )
        managed_due = require_canonical_timestamp(
            managed_rows[0].get("due_at"), field="due_at"
        )
        window = active.get("expected_end_window")
        if isinstance(window, Mapping):
            latest = window.get("latest")
            earliest = window.get("earliest")
            if latest is not None:
                latest_ts = require_canonical_timestamp(latest, field="latest")
                if parse_rfc3339(managed_due, field="due_at") > parse_rfc3339(
                    latest_ts, field="latest"
                ):
                    _fail(
                        "open/window managed v2 due_at must be <= "
                        "expected_end_window.latest"
                    )
            if earliest is not None and processed_dt < parse_rfc3339(
                require_canonical_timestamp(earliest, field="earliest"),
                field="earliest",
            ):
                earliest_ts = require_canonical_timestamp(
                    earliest, field="earliest"
                )
                if parse_rfc3339(managed_due, field="due_at") > parse_rfc3339(
                    earliest_ts, field="earliest"
                ):
                    _fail(
                        "open/window managed v2 due_at must be <= "
                        "expected_end_window.earliest while processed_through "
                        "< earliest"
                    )


def _queue_head(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not events:
        return None
    return dict(sorted((dict(e) for e in events), key=queue_event_sort_key)[0])


def _is_supported_due_event(event: Mapping[str, Any]) -> bool:
    kind = event.get("event_kind")
    schema = event.get("schema_version")
    if kind == "ACTIVITY_END" and schema == 1:
        return True
    if kind == "DECISION_WAKEUP" and schema == 2:
        return True
    return False


def _update_history_for_event(
    current: Mapping[str, Any], actual_event: Mapping[str, Any]
) -> dict[str, Any]:
    out = deepcopy(dict(current))
    history = dict(out.get("history") or {})
    prev_hash = history.get("history_hash")
    if not isinstance(prev_hash, str) or not prev_hash:
        _fail("CurrentState.history.history_hash must be a non-empty string")
    event_count = history.get("event_count")
    if isinstance(event_count, bool) or not isinstance(event_count, int):
        _fail("CurrentState.history.event_count must be int")
    history["history_hash"] = next_history_hash(prev_hash, actual_event)
    history["head_event_id"] = actual_event["event_id"]
    history["event_count"] = event_count + 1
    out["history"] = history
    return validate_checkpoint(out)


def _aggregate_camera_roll(
    *,
    records: list[Mapping[str, Any]],
    by_shard: dict[str, list[Mapping[str, Any]]],
    handoff_records: Sequence[Mapping[str, Any]],
    handoff_by_shard: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    for row in handoff_records:
        records.append(deepcopy(dict(row)))
    for path in sorted(handoff_by_shard.keys()):
        bucket = by_shard.setdefault(path, [])
        for row in handoff_by_shard[path]:
            bucket.append(deepcopy(dict(row)))


def advance_runtime_to_target(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    social_response_state: SocialResponseState | Mapping[str, Any],
    target_inputs: RuntimeTargetInputs | Mapping[str, Any],
) -> RuntimeTargetAdvanceResult:
    """Advance using the existing precomputed deterministic input tape."""
    inputs = parse_runtime_target_inputs(target_inputs)
    return _advance_runtime_to_target_core(
        bundle=bundle,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        social_response_state=social_response_state,
        input_source=_StaticRuntimeInputSource(inputs),
    )


def advance_runtime_to_target_with_provider(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    social_response_state: SocialResponseState | Mapping[str, Any],
    target_request: RuntimeTargetRequest | Mapping[str, Any],
    fact_provider: RuntimeFactProvider,
) -> RuntimeTargetAdvanceResult:
    """Advance using facts requested on demand at exact runtime boundaries."""
    request = parse_runtime_target_request(target_request)
    return _advance_runtime_to_target_core(
        bundle=bundle,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        social_response_state=social_response_state,
        input_source=_ProviderRuntimeInputSource(request, fact_provider),
    )


def _advance_runtime_to_target_core(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    social_response_state: SocialResponseState | Mapping[str, Any],
    input_source: _StaticRuntimeInputSource | _ProviderRuntimeInputSource,
) -> RuntimeTargetAdvanceResult:
    """Shared pure C3 chronology for static-tape and provider-driven inputs."""
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    policy = require_v2_production_format(normalize_policy(dict(behavior_policy)))
    assert_policy_matches_checkpoint(dict(validated_in.current_state), policy)

    social_state = validate_social_response_state(social_response_state)
    character_id = validated_in.current_state["character_id"]
    if social_state.character_id != character_id:
        _fail(
            "SocialResponseState.character_id mismatch: "
            f"{social_state.character_id!r} vs {character_id!r}"
        )
    processed0 = require_canonical_timestamp(
        validated_in.current_state["processed_through"], field="processed_through"
    )
    if parse_rfc3339(social_state.as_of, field="SocialResponseState.as_of") > (
        parse_rfc3339(processed0, field="processed_through")
    ):
        _fail("SocialResponseState.as_of must be <= CurrentState.processed_through")
    if parse_rfc3339(input_source.target_time, field="target_time") < parse_rfc3339(
        processed0, field="processed_through"
    ):
        _fail("target_time must be >= processed_through", code=ErrorCode.TIME_REVERSAL)

    _assert_input_active_queue_invariants(validated_in.current_state)

    working = _clone_bundle(validated_in)
    social_working = social_state
    revision_before = working.current_state["state_revision"]

    # Unconsumed call-local source moments keyed by deterministic source key.
    unconsumed_moments: dict[str, Mapping[str, Any]] = {}
    consumed_source_keys: list[str] = []

    def _merge_capture_source_moments(
        raw_moments: Sequence[Mapping[str, Any]],
        *,
        from_time: str,
        expected_activity_instance_id: str | None,
    ) -> None:
        from_c = require_canonical_timestamp(from_time, field="capture_from_time")
        for raw in raw_moments:
            if not isinstance(raw, Mapping):
                _fail("capture source moment must be a mapping")
            moment = validate_capture_source_moment(deepcopy(dict(raw)))
            if (
                expected_activity_instance_id is not None
                and moment["activity_instance_id"] != expected_activity_instance_id
            ):
                _fail(
                    "provider capture source moment activity_instance_id mismatch"
                )
            moment_at = require_canonical_timestamp(
                moment["moment_at"], field="moment_at"
            )
            if parse_rfc3339(moment_at, field="moment_at") < parse_rfc3339(
                from_c, field="capture_from_time"
            ):
                _fail("capture source moment_at must be >= requested from_time")
            if parse_rfc3339(moment_at, field="moment_at") > parse_rfc3339(
                input_source.target_time, field="target_time"
            ):
                _fail("capture source moment_at must be <= target_time")
            key = derive_capture_source_key(
                emitter_rule_id=moment["emitter_rule_id"],
                semantic_source_ref=moment["semantic_source_ref"],
                moment_ref=moment["moment_ref"],
            )
            prior = unconsumed_moments.get(key)
            if prior is not None:
                if canonical_json(prior) != canonical_json(moment):
                    _fail(
                        f"conflicting capture source moments for source_key={key!r}"
                    )
                continue
            if key in consumed_source_keys:
                _fail(
                    f"provider reintroduced already-consumed source_key={key!r}"
                )
            unconsumed_moments[key] = deepcopy(moment)

    _merge_capture_source_moments(
        input_source.initial_capture_moments,
        from_time=processed0,
        expected_activity_instance_id=None,
    )

    entry_active = working.current_state["context"].get("active_activity")
    if entry_active is not None:
        entry_id = _require_nonempty_str(
            entry_active.get("activity_instance_id"),
            label="activity_instance_id",
        )
        _merge_capture_source_moments(
            input_source.capture_source_moments(
                bundle=working,
                active_activity=entry_active,
                from_time=processed0,
            ),
            from_time=processed0,
            expected_activity_instance_id=entry_id,
        )

    consumed_trigger_ids: list[str] = []
    decision_frames: list[RuntimeDecisionFrame] = []
    actual_events: list[Mapping[str, Any]] = []
    camera_records: list[Mapping[str, Any]] = []
    camera_by_shard: dict[str, list[Mapping[str, Any]]] = {}
    microsteps_used = 0
    consumed_immediate_keys: set[str] = set()
    generated_c3_trigger_ids: set[str] = set()

    def _budget_consume(n: int = 1) -> None:
        nonlocal microsteps_used
        if microsteps_used + n > input_source.event_budget:
            _fail(
                "event budget exhausted before required discrete step",
                code=ErrorCode.EVENT_BUDGET_EXCEEDED,
            )
        microsteps_used += n

    def _bookkeep_finalized(finish_bundle: RuntimeBundle, actual_event: Mapping[str, Any]) -> RuntimeBundle:
        # 1–2 history
        current = _update_history_for_event(finish_bundle.current_state, actual_event)
        # 3–5 camera roll
        handoff = project_runtime_camera_roll_handoff(actual_event)
        _aggregate_camera_roll(
            records=camera_records,
            by_shard=camera_by_shard,
            handoff_records=handoff.records,
            handoff_by_shard=handoff.records_by_shard_path,
        )
        actual_events.append(deepcopy(dict(actual_event)))
        # 6 POST_FINALIZE (C3-owned; register provenance before enqueue)
        post_finalize = build_post_finalize_decision_wakeup(
            character_id=character_id,
            actual_event=actual_event,
        )
        generated_c3_trigger_ids.add(post_finalize["event_id"])
        current = _enqueue_queue_event(current, post_finalize)
        out = _replace_current(finish_bundle, current)
        # 7 validate
        return validate_runtime_bundle(out, reference_sets=reference_sets)

    # Main loop.
    while True:
        current = working.current_state
        t = require_canonical_timestamp(
            current["processed_through"], field="processed_through"
        )
        active = current["context"].get("active_activity")
        events = list(current["pending_queue"]["events"] or [])
        assert_queue_canonically_sorted(events)

        progressed = False

        # --- A. current-active primary captures at t ---
        if active is not None:
            instance_id = active["activity_instance_id"]
            due_moments = [
                m
                for m in unconsumed_moments.values()
                if m["moment_at"] == t and m["activity_instance_id"] == instance_id
            ]
            if due_moments:
                _budget_consume(len({
                    derive_capture_source_key(
                        emitter_rule_id=m["emitter_rule_id"],
                        semantic_source_ref=m["semantic_source_ref"],
                        moment_ref=m["moment_ref"],
                    )
                    for m in due_moments
                }))
                capture_result = process_runtime_capture_moments(
                    bundle=working,
                    reference_sets=reference_sets,
                    behavior_policy=policy,
                    capture_context_facts=RuntimeCaptureContextFacts(
                        character_source_sha=input_source.character_source_sha,
                        source_moments=tuple(due_moments),
                    ),
                )
                working = capture_result.bundle
                for key in capture_result.consumed_source_keys:
                    unconsumed_moments.pop(key, None)
                    if key not in consumed_source_keys:
                        consumed_source_keys.append(key)
                # Drain repeat chains depth-first, canonical parent_capture_id order.
                for parent_id in sorted(capture_result.repeat_parent_capture_ids):
                    next_parent: str | None = parent_id
                    while next_parent is not None:
                        _budget_consume(1)
                        step = resolve_runtime_repeat_step(
                            bundle=working,
                            reference_sets=reference_sets,
                            behavior_policy=policy,
                            character_source_sha=input_source.character_source_sha,
                            parent_capture_id=next_parent,
                        )
                        working = step.bundle
                        if step.chain_ended or step.ineligible:
                            next_parent = None
                        else:
                            next_parent = step.next_parent_capture_id
                progressed = True
                continue  # restart fixed point after captures/repeats

        # --- B. scheduler queue head at t ---
        head = _queue_head(events)
        if head is not None:
            due = require_canonical_timestamp(head.get("due_at"), field="due_at")
            if due == t:
                if not _is_supported_due_event(head):
                    _fail(
                        "unsupported due-now queue event: "
                        f"schema_version={head.get('schema_version')!r} "
                        f"event_kind={head.get('event_kind')!r}"
                    )
                _budget_consume(1)
                if head["event_kind"] == "ACTIVITY_END":
                    if active is None:
                        _fail("ACTIVITY_END due with no active activity")
                    finish = finalize_runtime_activity_from_exact_end(
                        bundle=working,
                        reference_sets=reference_sets,
                        behavior_policy=policy,
                        activity_end_event=head,
                    )
                    working = _bookkeep_finalized(finish.bundle, finish.actual_event)
                    progressed = True
                    continue

                # schema-v2 DECISION_WAKEUP
                trigger = runtime_trigger_from_v2_wakeup(working.current_state, head)
                if trigger.trigger_kind in _C3_OWNED_TRIGGERS:
                    if trigger.trigger_id not in generated_c3_trigger_ids:
                        _fail(
                            "C3-owned trigger must be generated by this "
                            f"invocation: {trigger.trigger_kind!r}"
                        )
                step_in = input_source.decision_step(
                    bundle=working,
                    social_response_state=social_working,
                    trigger=trigger,
                    behavior_policy=policy,
                    reference_sets=reference_sets,
                    finalized_events_since_base=actual_events,
                )
                guarded_facts = _guard_decision_facts(
                    facts=step_in.decision_facts,
                    active=active,
                    now=t,
                )
                social_result = build_runtime_social_decision(
                    bundle=working,
                    reference_sets=reference_sets,
                    behavior_policy=policy,
                    trigger=trigger,
                    facts=guarded_facts,
                    social_response_state=social_working,
                    social_facts=step_in.social_facts,
                )
                working = social_result.working_bundle
                social_working = social_result.social_response_state
                frame = social_result.frame
                decision_frames.append(frame)
                consumed_trigger_ids.append(trigger.trigger_id)
                resolution = frame.resolution

                if resolution.result_kind == "NO_ELIGIBLE_ACTION":
                    _fail("NO_ELIGIBLE_ACTION fails closed in C3")

                if resolution.result_kind == "CONTINUE_CURRENT":
                    if active is None:
                        _fail("CONTINUE_CURRENT requires active activity")
                    if social_result.pending_immediate_accept is not None:
                        _fail(
                            "CONTINUE_CURRENT forbids pending_immediate_accept"
                        )
                    if step_in.materialization_facts is not None:
                        _fail(
                            "CONTINUE_CURRENT requires materialization_facts "
                            "== null"
                        )
                    # Apply continue (semantic-equivalent checkpoint).
                    continued = apply_continue_current(
                        current_state=working.current_state,
                        resolution=resolution,
                    )
                    working = _replace_current(working, continued)
                    # Exact-pop consumed decision.
                    working = _replace_current(
                        working,
                        _exact_pop_event(
                            working.current_state, event_id=trigger.trigger_id
                        ),
                    )
                    # True C2A ACTIVE_REASSESSMENT only: kind + CURRENT_ACTIVITY
                    # source pointing at the current active instance.
                    consumed_boundary: str | None = None
                    if (
                        trigger.trigger_kind == "ACTIVE_REASSESSMENT"
                        and trigger.source_kind == "CURRENT_ACTIVITY"
                        and trigger.source_ref == active["activity_instance_id"]
                    ):
                        consumed_boundary = trigger.semantic_decision_key
                    # IMMEDIATE / POST_FINALIZE never passed as C2A boundary.
                    if trigger.trigger_kind in _C3_OWNED_TRIGGERS:
                        consumed_boundary = None
                    wakeup_facts = input_source.wakeup_facts(
                        bundle=working,
                        active_activity=active,
                        as_of=t,
                        behavior_policy=policy,
                        reference_sets=reference_sets,
                        finalized_events_since_base=actual_events,
                    )
                    reconciled = reconcile_active_runtime_queue(
                        bundle=working,
                        reference_sets=reference_sets,
                        behavior_policy=policy,
                        wakeup_facts=wakeup_facts,
                        consumed_active_boundary_key=consumed_boundary,
                    )
                    working = reconciled.bundle
                    working = _apply_immediate_reassessment_bridge(
                        bundle=working,
                        character_id=character_id,
                        processed_through=t,
                        immediate_reasons=reconciled.projection.immediate_reassessment_reasons,
                        consumed_immediate_keys=consumed_immediate_keys,
                        generated_c3_trigger_ids=generated_c3_trigger_ids,
                    )
                    progressed = True
                    continue

                if resolution.result_kind == "SELECT_CANDIDATE":
                    if active is not None:
                        # Active SELECT: discard-only; never C1-start.
                        if step_in.materialization_facts is not None:
                            _fail(
                                "active SELECT_CANDIDATE requires "
                                "materialization_facts == null"
                            )
                        finish = finalize_runtime_activity_from_decision_switch(
                            bundle=working,
                            reference_sets=reference_sets,
                            behavior_policy=policy,
                            frame=frame,
                            decision_facts=guarded_facts,
                        )
                        # Provisional immediate ACCEPT discarded (not terminalized).
                        working = _bookkeep_finalized(
                            finish.bundle, finish.actual_event
                        )
                        progressed = True
                        continue

                    # No-active SELECT/start.
                    materialization_raw = input_source.materialization_facts(
                        pending_immediate_accept=social_result.pending_immediate_accept,
                        step=step_in,
                        bundle=working,
                        social_response_state=social_working,
                        frame=frame,
                        decision_facts=guarded_facts,
                        behavior_policy=policy,
                        reference_sets=reference_sets,
                        trigger=trigger,
                        finalized_events_since_base=actual_events,
                    )
                    if materialization_raw is None:
                        _fail(
                            "no-active SELECT_CANDIDATE requires "
                            "materialization_facts"
                        )
                    mat_facts = parse_runtime_activity_materialization_facts(
                        materialization_raw
                    )
                    start = start_runtime_activity_from_selection(
                        bundle=working,
                        reference_sets=reference_sets,
                        frame=frame,
                        decision_facts=guarded_facts,
                        materialization_facts=mat_facts,
                        behavior_policy=policy,
                        social_responses=tuple(social_result.social_response.responses),
                        social_response_state=social_working,
                        pending_immediate_accept=social_result.pending_immediate_accept,
                    )
                    working = start.bundle
                    if start.social_response_state is not None:
                        social_working = start.social_response_state
                    # Exact-pop after successful start; C2A reconcile owns ACTIVITY_END.
                    working = _replace_current(
                        working,
                        _exact_pop_event(
                            working.current_state, event_id=trigger.trigger_id
                        ),
                    )
                    new_active = working.current_state["context"]["active_activity"]
                    assert new_active is not None
                    new_active_id = _require_nonempty_str(
                        new_active.get("activity_instance_id"),
                        label="activity_instance_id",
                    )
                    _merge_capture_source_moments(
                        input_source.capture_source_moments(
                            bundle=working,
                            active_activity=new_active,
                            from_time=t,
                        ),
                        from_time=t,
                        expected_activity_instance_id=new_active_id,
                    )
                    wakeup_facts = input_source.wakeup_facts(
                        bundle=working,
                        active_activity=new_active,
                        as_of=t,
                        behavior_policy=policy,
                        reference_sets=reference_sets,
                        finalized_events_since_base=actual_events,
                    )
                    reconciled = reconcile_active_runtime_queue(
                        bundle=working,
                        reference_sets=reference_sets,
                        behavior_policy=policy,
                        wakeup_facts=wakeup_facts,
                        consumed_active_boundary_key=None,
                    )
                    working = reconciled.bundle
                    working = _apply_immediate_reassessment_bridge(
                        bundle=working,
                        character_id=character_id,
                        processed_through=t,
                        immediate_reasons=reconciled.projection.immediate_reassessment_reasons,
                        consumed_immediate_keys=consumed_immediate_keys,
                        generated_c3_trigger_ids=generated_c3_trigger_ids,
                    )
                    progressed = True
                    continue

                _fail(
                    f"unsupported resolution.result_kind={resolution.result_kind!r}"
                )

        # --- unmatched other-owner due moments at t ---
        other_due = [
            m
            for m in unconsumed_moments.values()
            if m["moment_at"] == t
            and (
                active is None
                or m["activity_instance_id"] != active["activity_instance_id"]
            )
        ]
        if other_due:
            _fail(
                "unmatched due-now capture source moment with no same-time "
                "transition path to its owner activity"
            )

        # --- C. future chronological boundary / stable return ---
        if t == input_source.target_time:
            break

        # No due-now work and t < target: require active and integrate.
        if active is None:
            _fail(
                "cannot advance time with no active activity "
                "(no REST/idle default; due-now v2 decision required to start)"
            )

        next_candidates: list[str] = [input_source.target_time]
        for event in events:
            due = require_canonical_timestamp(event.get("due_at"), field="due_at")
            if parse_rfc3339(due, field="due_at") > parse_rfc3339(t, field="t"):
                next_candidates.append(due)
        for moment in unconsumed_moments.values():
            moment_at = require_canonical_timestamp(
                moment["moment_at"], field="moment_at"
            )
            if parse_rfc3339(moment_at, field="moment_at") > parse_rfc3339(
                t, field="t"
            ):
                next_candidates.append(moment_at)

        boundary = min(
            next_candidates,
            key=lambda ts: parse_rfc3339(ts, field="boundary"),
        )
        if parse_rfc3339(boundary, field="boundary") <= parse_rfc3339(t, field="t"):
            _fail("integration boundary must strictly advance processed_through")

        working = integrate_active_interval(
            bundle=working,
            reference_sets=reference_sets,
            behavior_policy=policy,
            until=boundary,
        )
        # Integration does not consume event budget; restart fixed point.
        if not progressed and working.current_state["processed_through"] == t:
            _fail("integration failed to advance processed_through")
        continue

    # --- Stable-return invariants ---
    final = validate_runtime_bundle(working, reference_sets=reference_sets)
    current = final.current_state
    t = require_canonical_timestamp(
        current["processed_through"], field="processed_through"
    )
    if t != input_source.target_time:
        _fail("stable return requires processed_through == target_time")
    active = current["context"].get("active_activity")
    if active is None:
        _fail("stable return requires non-null active_activity")
    events = list(current["pending_queue"]["events"] or [])
    if current["pending_queue"].get("cursor") != 0:
        _fail("stable return requires pending_queue.cursor == 0")
    assert_queue_canonically_sorted(events)
    for event in events:
        due = require_canonical_timestamp(event.get("due_at"), field="due_at")
        if parse_rfc3339(due, field="due_at") <= parse_rfc3339(
            input_source.target_time, field="target_time"
        ):
            _fail("stable return forbids queue event due_at <= target_time")
    leftover = [
        m
        for m in unconsumed_moments.values()
        if parse_rfc3339(m["moment_at"], field="moment_at")
        <= parse_rfc3339(input_source.target_time, field="target_time")
    ]
    if leftover:
        _fail("stable return requires all supplied <=target sources consumed")
    if current["state_revision"] != revision_before:
        _fail("state_revision must remain unchanged")

    # Open/window active retains future managed v2 wakeup.
    planned_end = active.get("planned_end")
    if planned_end is None:
        managed_future = [
            e
            for e in events
            if is_managed_v2_decision_wakeup(e)
            and parse_rfc3339(e["due_at"], field="due_at")
            > parse_rfc3339(input_source.target_time, field="target_time")
        ]
        if not managed_future:
            _fail(
                "open/window active stable return requires future managed "
                "v2 DECISION_WAKEUP"
            )
    else:
        expected_end = build_activity_end_event(
            activity_instance_id=active["activity_instance_id"],
            planned_end=planned_end,
        )
        assert expected_end is not None
        if not any(e["event_id"] == expected_end["event_id"] for e in events):
            # Only required if planned_end still in the future (not yet consumed).
            if parse_rfc3339(planned_end, field="planned_end") > parse_rfc3339(
                input_source.target_time, field="target_time"
            ):
                _fail(
                    "exact planned_end active must retain ACTIVITY_END until consumed"
                )

    if microsteps_used > input_source.event_budget:
        _fail(
            "microsteps_used exceeds event_budget",
            code=ErrorCode.EVENT_BUDGET_EXCEEDED,
        )

    by_shard_out = {
        path: tuple(camera_by_shard[path]) for path in sorted(camera_by_shard.keys())
    }
    return RuntimeTargetAdvanceResult(
        bundle=final,
        social_response_state=social_working,
        actual_events=tuple(actual_events),
        camera_roll_records=tuple(camera_records),
        camera_roll_records_by_shard_path=by_shard_out,
        consumed_source_keys=tuple(sorted(set(consumed_source_keys))),
        consumed_trigger_ids=tuple(consumed_trigger_ids),
        decision_frames=tuple(decision_frames),
        microsteps_used=microsteps_used,
    )


__all__ = [
    "RuntimeDecisionStepInput",
    "RuntimeWakeupStepInput",
    "RuntimeTargetInputs",
    "RuntimeTargetRequest",
    "RuntimeFactProvider",
    "RuntimeDecisionProviderResult",
    "RuntimeTargetAdvanceResult",
    "parse_runtime_decision_step_input",
    "parse_runtime_wakeup_step_input",
    "parse_runtime_target_inputs",
    "derive_post_finalize_decision_key",
    "build_post_finalize_decision_wakeup",
    "derive_immediate_reassessment_decision_key",
    "build_immediate_reassessment_wakeup",
    "advance_runtime_to_target",
    "advance_runtime_to_target_with_provider",
    "V2_DECISION_WAKEUP_PRIORITY",
]
