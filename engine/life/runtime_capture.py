"""Life Engine v2 Slice 5B2C2C — runtime capture moments + Camera Roll handoff.

Pure/in-memory microsteps that wire already-approved Slice 4 capture contracts
into the Slice 5 runtime lifecycle while an activity is active.

Owns:
- RuntimeCaptureContextFacts (transient; no CurrentState schema expansion)
- exact-now primary CaptureSourceMoment processing
- runtime visual-context / stress bind onto CurrentState + domain revisions
- existing 4B2 emit → 4B1 resolve → 4B2 TAKE bridge
- one-link-at-a-time 4B3 repeat resolution
- call-local consumed_source_keys / repeat_parent diagnostics
- finalized ActualEvent → logical CameraRollRecord handoff (no shard write)

Does **not**:
- invent source moments from prose/activity/location
- process primary moments after their timestamp
- persist capture/repeat queue, consumed-source ledger, or SKIP history
- eagerly recurse repeats / impose max frames / add repeat probability
- implement the C3 target-time / event-budget outer loop
- finalize activities (C2B remains the only finalizer)
- write Camera Roll shards / images / review / post
- wire clock.py / mutate processed_through / state_revision / history / domains
- change Behavior Policy values or Capture schemas

Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .camera_roll import camera_roll_shard_path, project_camera_roll_records
from .canonical import canonical_hash
from .capture_decisions import RESULT_SKIP, RESULT_TAKE, resolve_capture_opportunity
from .capture_emitters import (
    EMITTER_RULE_TO_CAPTURE_KIND,
    derive_capture_source_key,
    emit_capture_opportunities,
    validate_capture_source_moment,
)
from .capture_pipeline import apply_capture_take
from .capture_repeats import apply_repeat_frame_take, build_next_repeat_frame_opportunity
from .captures import derive_capture_id
from .checkpoint import validate_checkpoint
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .policy import assert_policy_matches_checkpoint, normalize_policy
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .schema import reject_binary_floats
from .timeutil import parse_rfc3339, require_canonical_timestamp

_RUNTIME_ACTIVE_REQUIRED = frozenset(
    {
        "runtime_context",
        "causes",
        "decision_evidence",
        "dynamics_context",
        "runtime_finish_context",
    }
)

_CAPTURE_CONTEXT_FACTS_KEYS = frozenset({"character_source_sha", "source_moments"})

_LIGHTING_ENUM = frozenset({"UNKNOWN", "NATURAL", "ARTIFICIAL", "MIXED"})

_REPEATABLE_KINDS = frozenset({"SELFIE", "PEOPLE"})


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def _require_nonempty_str(value: object, *, label: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool) or value == "":
        _fail(f"{label} must be a non-empty string (coercion forbidden)")
    return value


def _require_true_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be strict int (no bool/float/string)")
    return value


def _require_v2_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        _fail("behavior_policy must be a mapping")
    normalized = normalize_policy(dict(policy))
    if normalized.get("schema_version") != 2 or normalized.get("policy_mode") != "PRODUCTION":
        _fail(
            "5B2C2C capture requires schema_version=2 policy_mode=PRODUCTION "
            f"(got schema_version={normalized.get('schema_version')!r} "
            f"policy_mode={normalized.get('policy_mode')!r})"
        )
    return normalized


def _require_runtime_active_activity(activity: Mapping[str, Any] | None) -> dict[str, Any]:
    if activity is None:
        _fail("runtime active_activity required")
    if not isinstance(activity, Mapping):
        _fail("active_activity must be a mapping")
    missing = _RUNTIME_ACTIVE_REQUIRED - set(activity.keys())
    if missing:
        _fail(
            "runtime ActiveActivity missing required provenance fields: "
            f"{sorted(missing)}"
        )
    validated = validate_active_activity(dict(activity))
    if validated is None:
        _fail("active_activity required")
    return validated


def _assert_active_time_bounds(
    active: Mapping[str, Any],
    *,
    processed_through: str,
) -> None:
    processed = parse_rfc3339(processed_through, field="processed_through")
    start = parse_rfc3339(active["actual_start"], field="actual_start")
    if processed < start:
        _fail("processed_through must be >= active_activity.actual_start")

    planned_end = active.get("planned_end")
    if planned_end is not None:
        end = parse_rfc3339(planned_end, field="planned_end")
        if processed > end:
            _fail("processed_through must be <= active_activity.planned_end")

    window = active.get("expected_end_window")
    if isinstance(window, Mapping):
        latest = window.get("latest")
        if latest is not None:
            latest_dt = parse_rfc3339(latest, field="expected_end_window.latest")
            if processed > latest_dt:
                _fail(
                    "processed_through must be <= "
                    "active_activity.expected_end_window.latest"
                )


def _external_active_companion_ids(
    active: Mapping[str, Any],
    *,
    character_id: str,
) -> frozenset[str]:
    companions = active.get("companions") or []
    if not isinstance(companions, list):
        _fail("active_activity.companions must be array")
    out: set[str] = set()
    for index, row in enumerate(companions):
        if not isinstance(row, Mapping):
            _fail(f"companions[{index}] must be object")
        entity_id = row.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            _fail(f"companions[{index}].entity_id must be non-empty string")
        if entity_id != character_id:
            out.add(entity_id)
    return frozenset(out)


def _wardrobe_item_ids(wardrobe_state: Mapping[str, Any]) -> frozenset[str]:
    items = wardrobe_state.get("items")
    if not isinstance(items, list):
        _fail("wardrobe_state.items must be array")
    out: set[str] = set()
    for index, row in enumerate(items):
        if not isinstance(row, Mapping):
            _fail(f"wardrobe_state.items[{index}] must be object")
        item_id = row.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            _fail(f"wardrobe_state.items[{index}].item_id must be non-empty string")
        out.add(item_id)
    return frozenset(out)


def _appearance_field(current: Mapping[str, Any], field: str) -> Any:
    appearance = current.get("appearance")
    if not isinstance(appearance, Mapping):
        _fail("CurrentState.appearance required")
    return appearance.get(field)


@dataclass(frozen=True)
class RuntimeCaptureContextFacts:
    character_source_sha: str
    source_moments: tuple[Mapping[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "character_source_sha": self.character_source_sha,
            "source_moments": [dict(m) for m in self.source_moments],
        }


def parse_runtime_capture_context_facts(
    raw: RuntimeCaptureContextFacts | Mapping[str, Any],
) -> RuntimeCaptureContextFacts:
    """Strict transient input. No hidden defaults / unknown fields / binary floats."""
    # Inspect fields without list()/iteration coercion first. Generators, strings,
    # bytes, sets, and None must fail with LifeEngineError — never raw TypeError.
    if isinstance(raw, RuntimeCaptureContextFacts):
        character_source_sha_raw = raw.character_source_sha
        moments_raw = raw.source_moments
    elif isinstance(raw, Mapping):
        unknown = set(raw.keys()) - _CAPTURE_CONTEXT_FACTS_KEYS
        if unknown:
            _fail(
                f"RuntimeCaptureContextFacts unknown field(s): {sorted(unknown)}",
                code=ErrorCode.SCHEMA_INVALID,
            )
        missing = _CAPTURE_CONTEXT_FACTS_KEYS - set(raw.keys())
        if missing:
            _fail(f"RuntimeCaptureContextFacts missing field(s): {sorted(missing)}")
        character_source_sha_raw = raw["character_source_sha"]
        moments_raw = raw["source_moments"]
    else:
        _fail("RuntimeCaptureContextFacts must be mapping or dataclass")

    character_source_sha = _require_nonempty_str(
        character_source_sha_raw, label="character_source_sha"
    )
    if not isinstance(moments_raw, (list, tuple)):
        _fail(
            "source_moments must be a list/tuple of mappings "
            "(scalar/string/bytes/set/generator/null coercion forbidden)",
            code=ErrorCode.SCHEMA_INVALID,
        )

    # Snapshot only after approved-container validation.
    moments: list[Mapping[str, Any]] = []
    for index, item in enumerate(moments_raw):
        if not isinstance(item, Mapping):
            _fail(
                f"source_moments[{index}] must be a mapping",
                code=ErrorCode.SCHEMA_INVALID,
            )
        moments.append(item)

    out = RuntimeCaptureContextFacts(
        character_source_sha=character_source_sha,
        source_moments=tuple(moments),
    )
    reject_binary_floats(out.as_dict(), path="RuntimeCaptureContextFacts")
    return out


def derive_runtime_capture_stress_state_ref(current_state: Mapping[str, Any]) -> str:
    """Deterministic stress evidence ref bound to character/time/exact stress."""
    if not isinstance(current_state, Mapping):
        _fail("current_state must be a mapping")
    character_id = _require_nonempty_str(
        current_state.get("character_id"), label="character_id"
    )
    processed = require_canonical_timestamp(
        current_state.get("processed_through"), field="processed_through"
    )
    human = current_state.get("human_state")
    if not isinstance(human, Mapping):
        _fail("CurrentState.human_state required")
    stress = _require_true_int(human.get("stress"), label="human_state.stress")
    digest = canonical_hash(
        {
            "character_id": character_id,
            "processed_through": processed,
            "stress": stress,
        }
    )
    return f"runtime-stress:{digest}"


def _bind_runtime_visual_context(
    visual_context: Mapping[str, Any],
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    character_source_sha: str,
    policy_version: str,
    capture_kind: str | None,
    subject_refs: Sequence[str] | None,
) -> None:
    """Bind frozen visual_context (+ optional person subjects) to current RuntimeBundle."""
    current = bundle.current_state
    active = _require_runtime_active_activity(current["context"].get("active_activity"))
    character_id = _require_nonempty_str(current.get("character_id"), label="character_id")
    vc = visual_context

    if vc.get("character_id") != character_id:
        _fail("visual_context.character_id must equal CurrentState.character_id")
    if vc.get("character_source_sha") != character_source_sha:
        _fail(
            "visual_context.character_source_sha must equal trusted "
            "runtime character_source_sha"
        )
    if vc.get("engine_commit_sha") != current.get("engine_commit_sha"):
        _fail("visual_context.engine_commit_sha must equal CurrentState.engine_commit_sha")
    if vc.get("behavior_policy_version") != current.get("behavior_policy_version"):
        _fail(
            "visual_context.behavior_policy_version must equal "
            "CurrentState.behavior_policy_version"
        )
    if vc.get("behavior_policy_version") != policy_version:
        _fail(
            "visual_context.behavior_policy_version must equal supplied policy version"
        )
    if vc.get("location_id") != current["context"].get("location_id"):
        _fail("visual_context.location_id must equal CurrentState.context.location_id")

    appearance = vc.get("appearance")
    if not isinstance(appearance, Mapping):
        _fail("visual_context.appearance required")
    if appearance.get("outfit_state_id") != _appearance_field(current, "outfit_state_id"):
        _fail(
            "visual_context.appearance.outfit_state_id must equal "
            "CurrentState.appearance.outfit_state_id"
        )
    if appearance.get("makeup_level") != _appearance_field(current, "makeup_level"):
        _fail(
            "visual_context.appearance.makeup_level must equal "
            "CurrentState.appearance.makeup_level"
        )
    if appearance.get("hair_state_id") != _appearance_field(current, "hair_state_id"):
        _fail(
            "visual_context.appearance.hair_state_id must equal "
            "CurrentState.appearance.hair_state_id"
        )

    outfit_item_ids = appearance.get("outfit_item_ids")
    if not isinstance(outfit_item_ids, list):
        _fail("appearance.outfit_item_ids must be array")
    wardrobe_ids = _wardrobe_item_ids(bundle.wardrobe_state)
    for item_id in outfit_item_ids:
        if item_id not in reference_sets.approved_wardrobe_item_ids:
            _fail(
                f"outfit_item_id not in approved_wardrobe_item_ids: {item_id!r}",
                code=ErrorCode.INVALID_STATE,
            )
        if item_id not in wardrobe_ids:
            _fail(
                f"outfit_item_id missing from current WardrobeState.items: {item_id!r}",
                code=ErrorCode.INVALID_STATE,
            )

    if vc.get("home_revision") != bundle.home_state.get("revision"):
        _fail("visual_context.home_revision must equal current HomeState.revision")
    if vc.get("wardrobe_revision") != bundle.wardrobe_state.get("revision"):
        _fail(
            "visual_context.wardrobe_revision must equal current WardrobeState.revision"
        )

    external_companions = _external_active_companion_ids(
        active, character_id=character_id
    )
    companion_refs = vc.get("companion_refs")
    if not isinstance(companion_refs, list):
        _fail("visual_context.companion_refs must be array")
    for ref in companion_refs:
        if ref not in external_companions:
            _fail(
                f"visual_context.companion_ref is not an external active companion: "
                f"{ref!r}"
            )
        if ref not in reference_sets.known_person_ids:
            _fail(
                f"visual_context.companion_ref must be in "
                f"reference_sets.known_person_ids: {ref!r}"
            )

    photographer = vc.get("photographer_ref")
    if photographer is not None:
        if photographer == character_id:
            pass
        elif photographer not in external_companions:
            _fail(
                "visual_context.photographer_ref must be null, CurrentState.character_id, "
                "or an external active companion"
            )
        elif photographer not in reference_sets.known_person_ids:
            _fail(
                "external photographer_ref must be in "
                f"reference_sets.known_person_ids: {photographer!r}"
            )

    lighting = vc.get("lighting_context")
    if lighting not in _LIGHTING_ENUM:
        _fail(
            f"lighting_context must be an explicit enum member "
            f"(no location/time/weather inference): {lighting!r}",
            code=ErrorCode.SCHEMA_INVALID,
        )

    if capture_kind is None or subject_refs is None:
        return

    companion_ref_set = frozenset(companion_refs)
    if capture_kind == "PEOPLE":
        for subject in subject_refs:
            if subject == character_id:
                continue
            if subject not in external_companions:
                _fail(
                    f"PEOPLE external person subject must be a known active companion: "
                    f"{subject!r}"
                )
            if subject not in companion_ref_set:
                _fail(
                    "PEOPLE external person subject must appear in "
                    f"visual_context.companion_refs: {subject!r}"
                )
            if subject not in reference_sets.known_person_ids:
                _fail(
                    "PEOPLE external person subject must be in "
                    f"reference_sets.known_person_ids: {subject!r}"
                )
        return

    if capture_kind == "SELFIE":
        # Slice 4A.1: character + additional explicit subject refs allowed.
        # Only known-person / active-companion refs are treated as persons.
        for subject in subject_refs:
            if subject == character_id:
                continue
            person_like = (
                subject in reference_sets.known_person_ids
                or subject in external_companions
            )
            if not person_like:
                continue
            if subject not in external_companions:
                _fail(
                    f"SELFIE person subject must be a known active companion: "
                    f"{subject!r}"
                )
            if subject not in companion_ref_set:
                _fail(
                    "SELFIE person subject must appear in "
                    f"visual_context.companion_refs: {subject!r}"
                )
            if subject not in reference_sets.known_person_ids:
                _fail(
                    "SELFIE person subject must be in "
                    f"reference_sets.known_person_ids: {subject!r}"
                )


def _bind_runtime_source_moment(
    moment: Mapping[str, Any],
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    character_source_sha: str,
    policy_version: str,
) -> dict[str, Any]:
    """Validate CaptureSourceMoment then bind to exact CurrentState / domains."""
    validated = validate_capture_source_moment(moment)
    current = bundle.current_state
    active = _require_runtime_active_activity(current["context"].get("active_activity"))
    processed = require_canonical_timestamp(
        current["processed_through"], field="processed_through"
    )

    if validated["activity_instance_id"] != active["activity_instance_id"]:
        _fail(
            "source moment.activity_instance_id must equal current "
            "active_activity.activity_instance_id"
        )
    if validated["moment_at"] != processed:
        _fail(
            "source moment.moment_at must equal CurrentState.processed_through "
            f"(got moment_at={validated['moment_at']!r} processed_through={processed!r})"
        )

    kind = EMITTER_RULE_TO_CAPTURE_KIND[validated["emitter_rule_id"]]
    _bind_runtime_visual_context(
        validated["visual_context"],
        bundle=bundle,
        reference_sets=reference_sets,
        character_source_sha=character_source_sha,
        policy_version=policy_version,
        capture_kind=kind,
        subject_refs=list(validated["subject_refs"]),
    )
    return validated


def _bind_runtime_repeat_parent(
    parent: Mapping[str, Any],
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    character_source_sha: str,
    policy_version: str,
) -> None:
    """Runtime-bind a pending parent Capture visual_context before repeat child creation."""
    _bind_runtime_visual_context(
        parent["visual_context"],
        bundle=bundle,
        reference_sets=reference_sets,
        character_source_sha=character_source_sha,
        policy_version=policy_version,
        capture_kind=parent.get("capture_kind"),
        subject_refs=list(parent.get("subject_refs") or []),
    )


def _replace_active_pending_captures(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    updated_activity: Mapping[str, Any],
) -> RuntimeBundle:
    """Install validated active with only pending_captures possibly changed."""
    prior = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current = deepcopy(dict(prior.current_state))
    validated_active = validate_active_activity(dict(updated_activity))
    if validated_active is None:
        _fail("updated active_activity required")

    prior_active = _require_runtime_active_activity(
        prior.current_state["context"].get("active_activity")
    )
    # Ensure only pending_captures differs vs prior active (semantic boundary).
    prior_without = deepcopy(prior_active)
    updated_without = deepcopy(validated_active)
    prior_without.pop("pending_captures", None)
    updated_without.pop("pending_captures", None)
    if canonical_hash(prior_without) != canonical_hash(updated_without):
        _fail(
            "runtime capture must mutate only ActiveActivity.pending_captures "
            "(other active fields unchanged)"
        )

    current["context"] = dict(current["context"])
    current["context"]["active_activity"] = validated_active
    current = validate_checkpoint(current)

    if current["processed_through"] != prior.current_state["processed_through"]:
        _fail("processed_through must remain unchanged")
    if current["state_revision"] != prior.current_state["state_revision"]:
        _fail("state_revision must remain unchanged")
    if current.get("history") != prior.current_state.get("history"):
        _fail("history must remain unchanged")
    if current.get("pending_queue") != prior.current_state.get("pending_queue"):
        _fail("pending_queue must remain unchanged")
    if current.get("human_state") != prior.current_state.get("human_state"):
        _fail("human_state must remain unchanged")
    if current.get("sleep_context") != prior.current_state.get("sleep_context"):
        _fail("sleep_context must remain unchanged")
    if current.get("appearance") != prior.current_state.get("appearance"):
        _fail("appearance must remain unchanged")
    if current["context"].get("location_id") != prior.current_state["context"].get(
        "location_id"
    ):
        _fail("location_id must remain unchanged")

    out = RuntimeBundle(
        current_state=current,
        schedule_state=deepcopy(dict(prior.schedule_state)),
        relation_state=deepcopy(dict(prior.relation_state)),
        home_state=deepcopy(dict(prior.home_state)),
        consumables_state=deepcopy(dict(prior.consumables_state)),
        wardrobe_state=deepcopy(dict(prior.wardrobe_state)),
        finance_state=deepcopy(dict(prior.finance_state)),
    )
    return validate_runtime_bundle(out, reference_sets=reference_sets)


@dataclass(frozen=True)
class RuntimeCaptureBatchResult:
    bundle: RuntimeBundle
    opportunities: tuple[Mapping[str, Any], ...]
    resolutions: tuple[Mapping[str, Any], ...]
    suppressed: tuple[Mapping[str, Any], ...]
    consumed_source_keys: tuple[str, ...]
    repeat_parent_capture_ids: tuple[str, ...]

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
            "opportunities": [dict(o) for o in self.opportunities],
            "resolutions": [dict(r) for r in self.resolutions],
            "suppressed": [dict(s) for s in self.suppressed],
            "consumed_source_keys": list(self.consumed_source_keys),
            "repeat_parent_capture_ids": list(self.repeat_parent_capture_ids),
        }


def process_runtime_capture_moments(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    capture_context_facts: RuntimeCaptureContextFacts | Mapping[str, Any],
) -> RuntimeCaptureBatchResult:
    """Process exact-now primary CaptureSourceMoments. Pure; no input mutation."""
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state
    policy = _require_v2_policy(behavior_policy)
    # Identity bind: version + character_id + timezone (no hash/approval gate).
    assert_policy_matches_checkpoint(dict(current_in), policy)

    active = _require_runtime_active_activity(
        current_in["context"].get("active_activity")
    )
    processed = require_canonical_timestamp(
        current_in["processed_through"], field="processed_through"
    )
    _assert_active_time_bounds(active, processed_through=processed)

    facts = parse_runtime_capture_context_facts(capture_context_facts)

    bound_moments: list[dict[str, Any]] = []
    for moment in facts.source_moments:
        bound_moments.append(
            _bind_runtime_source_moment(
                moment,
                bundle=validated_in,
                reference_sets=reference_sets,
                character_source_sha=facts.character_source_sha,
                policy_version=policy["behavior_policy_version"],
            )
        )

    # Deterministic consumed keys for every valid supplied source (unique, sorted).
    consumed_keys = tuple(
        sorted(
            {
                derive_capture_source_key(
                    m["emitter_rule_id"],
                    m["semantic_source_ref"],
                    m["moment_ref"],
                )
                for m in bound_moments
            }
        )
    )

    if not bound_moments:
        return RuntimeCaptureBatchResult(
            bundle=validated_in,
            opportunities=(),
            resolutions=(),
            suppressed=(),
            consumed_source_keys=consumed_keys,
            repeat_parent_capture_ids=(),
        )

    stress = _require_true_int(
        current_in["human_state"].get("stress"), label="human_state.stress"
    )
    stress_ref = derive_runtime_capture_stress_state_ref(current_in)

    emission = emit_capture_opportunities(
        policy=policy,
        active_activity=active,
        stress=stress,
        stress_state_ref=stress_ref,
        source_moments=bound_moments,
    )

    world_seed = current_in["world_seed"]
    working_activity = deepcopy(active)
    resolutions: list[dict[str, Any]] = []
    repeat_parents: list[str] = []

    for opportunity in emission.opportunities:
        resolution = resolve_capture_opportunity(
            world_seed=world_seed,
            policy=policy,
            active_activity=working_activity,
            opportunity=opportunity,
        )
        resolutions.append(resolution.as_dict())
        if resolution.result_kind == RESULT_TAKE:
            working_activity = apply_capture_take(
                world_seed=world_seed,
                policy=policy,
                active_activity=working_activity,
                opportunity=opportunity,
                resolution=resolution,
            )
            if opportunity["capture_kind"] in _REPEATABLE_KINDS:
                if resolution.occurrence_key is None:
                    _fail("TAKE_CAPTURE missing occurrence_key")
                capture_id = derive_capture_id(
                    opportunity["activity_instance_id"], resolution.occurrence_key
                )
                repeat_parents.append(capture_id)
        elif resolution.result_kind != RESULT_SKIP:
            _fail(f"unexpected capture resolution: {resolution.result_kind!r}")

    suppressed = tuple(s.as_dict() for s in emission.suppressed)
    opportunities = tuple(deepcopy(dict(o)) for o in emission.opportunities)

    if canonical_hash(working_activity.get("pending_captures") or []) == canonical_hash(
        active.get("pending_captures") or []
    ):
        out_bundle = validated_in
    else:
        out_bundle = _replace_active_pending_captures(
            bundle=validated_in,
            reference_sets=reference_sets,
            updated_activity=working_activity,
        )

    return RuntimeCaptureBatchResult(
        bundle=out_bundle,
        opportunities=opportunities,
        resolutions=tuple(resolutions),
        suppressed=suppressed,
        consumed_source_keys=consumed_keys,
        repeat_parent_capture_ids=tuple(repeat_parents),
    )


@dataclass(frozen=True)
class RuntimeRepeatStepResult:
    bundle: RuntimeBundle
    parent_capture_id: str
    opportunity: Mapping[str, Any] | None
    resolution: Mapping[str, Any] | None
    child_capture_id: str | None
    next_parent_capture_id: str | None
    chain_ended: bool
    ineligible: bool

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
            "parent_capture_id": self.parent_capture_id,
            "opportunity": None if self.opportunity is None else dict(self.opportunity),
            "resolution": None if self.resolution is None else dict(self.resolution),
            "child_capture_id": self.child_capture_id,
            "next_parent_capture_id": self.next_parent_capture_id,
            "chain_ended": self.chain_ended,
            "ineligible": self.ineligible,
        }


def resolve_runtime_repeat_step(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    character_source_sha: object,
    parent_capture_id: object,
) -> RuntimeRepeatStepResult:
    """Advance one repeat-frame link. Pure; no eager recursion / caller authority."""
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state
    policy = _require_v2_policy(behavior_policy)
    # Identity bind: version + character_id + timezone (no hash/approval gate).
    assert_policy_matches_checkpoint(dict(current_in), policy)

    active = _require_runtime_active_activity(
        current_in["context"].get("active_activity")
    )
    processed = require_canonical_timestamp(
        current_in["processed_through"], field="processed_through"
    )
    _assert_active_time_bounds(active, processed_through=processed)

    trusted_source_sha = _require_nonempty_str(
        character_source_sha, label="character_source_sha"
    )
    parent_id = _require_nonempty_str(parent_capture_id, label="parent_capture_id")
    pending = list(active.get("pending_captures") or [])
    matches = [cap for cap in pending if cap["capture_id"] == parent_id]
    if not matches:
        _fail(
            "parent Capture not found in current ActiveActivity.pending_captures "
            "(historical/finalized-only or missing parent rejected)"
        )
    if len(matches) != 1:
        _fail(f"conflicting parent Capture identity: {parent_id!r}")
    parent = matches[0]
    if parent["activity_instance_id"] != active["activity_instance_id"]:
        _fail("parent Capture owner mismatch vs active_activity.activity_instance_id")
    if parent["captured_at"] != processed:
        _fail(
            "parent Capture.captured_at must equal CurrentState.processed_through "
            "(old pending parent after time advance rejected)"
        )

    # Bind parent visual_context to current RuntimeBundle before 4B3 inheritance.
    _bind_runtime_repeat_parent(
        parent,
        bundle=validated_in,
        reference_sets=reference_sets,
        character_source_sha=trusted_source_sha,
        policy_version=policy["behavior_policy_version"],
    )

    opportunity = build_next_repeat_frame_opportunity(
        active_activity=active,
        parent_capture_id=parent_id,
    )
    if opportunity is None:
        return RuntimeRepeatStepResult(
            bundle=validated_in,
            parent_capture_id=parent_id,
            opportunity=None,
            resolution=None,
            child_capture_id=None,
            next_parent_capture_id=None,
            chain_ended=True,
            ineligible=True,
        )

    world_seed = current_in["world_seed"]
    resolution = resolve_capture_opportunity(
        world_seed=world_seed,
        policy=policy,
        active_activity=active,
        opportunity=opportunity,
    )

    if resolution.result_kind == RESULT_SKIP:
        return RuntimeRepeatStepResult(
            bundle=validated_in,
            parent_capture_id=parent_id,
            opportunity=deepcopy(dict(opportunity)),
            resolution=resolution.as_dict(),
            child_capture_id=None,
            next_parent_capture_id=None,
            chain_ended=True,
            ineligible=False,
        )

    if resolution.result_kind != RESULT_TAKE:
        _fail(f"unexpected repeat resolution: {resolution.result_kind!r}")

    parent_before = deepcopy(parent)
    updated = apply_repeat_frame_take(
        world_seed=world_seed,
        policy=policy,
        active_activity=active,
        parent_capture_id=parent_id,
        opportunity=opportunity,
        resolution=resolution,
    )
    # Parent immutability is enforced inside apply_repeat_frame_take; re-check owner.
    parent_after = next(
        (c for c in (updated.get("pending_captures") or []) if c["capture_id"] == parent_id),
        None,
    )
    if parent_after is None or canonical_hash(parent_after) != canonical_hash(parent_before):
        _fail("parent Capture must remain unchanged after repeat TAKE")

    if resolution.occurrence_key is None:
        _fail("repeat TAKE missing occurrence_key")
    child_id = derive_capture_id(opportunity["activity_instance_id"], resolution.occurrence_key)

    out_bundle = _replace_active_pending_captures(
        bundle=validated_in,
        reference_sets=reference_sets,
        updated_activity=updated,
    )
    return RuntimeRepeatStepResult(
        bundle=out_bundle,
        parent_capture_id=parent_id,
        opportunity=deepcopy(dict(opportunity)),
        resolution=resolution.as_dict(),
        child_capture_id=child_id,
        next_parent_capture_id=child_id,
        chain_ended=False,
        ineligible=False,
    )


@dataclass(frozen=True)
class RuntimeCameraRollHandoff:
    records: tuple[Mapping[str, Any], ...]
    records_by_shard_path: Mapping[str, tuple[Mapping[str, Any], ...]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": [dict(r) for r in self.records],
            "records_by_shard_path": {
                path: [dict(r) for r in rows]
                for path, rows in self.records_by_shard_path.items()
            },
        }


def project_runtime_camera_roll_handoff(
    actual_event: Mapping[str, Any],
) -> RuntimeCameraRollHandoff:
    """Logical Camera Roll handoff from a finalized ActualEvent. No I/O / merge / write."""
    if not isinstance(actual_event, Mapping):
        _fail("actual_event must be a mapping")
    # Reject unresolved ActiveActivity-shaped objects before projection.
    if "pending_captures" in actual_event and "event_id" not in actual_event:
        _fail("ActiveActivity pending captures cannot be projected as Camera Roll")

    records = project_camera_roll_records(actual_event)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        path = camera_roll_shard_path(record["captured_at"])
        grouped.setdefault(path, []).append(deepcopy(dict(record)))

    # Deterministic grouping: shard paths sorted; each group preserves record order.
    by_path = {
        path: tuple(grouped[path]) for path in sorted(grouped.keys())
    }
    return RuntimeCameraRollHandoff(
        records=tuple(deepcopy(dict(r)) for r in records),
        records_by_shard_path=by_path,
    )
