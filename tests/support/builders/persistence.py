"""Shared synthetic persistent snapshot and candidate-tree builders."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from engine.life.camera_roll import merge_camera_roll_shard, project_camera_roll_records
from engine.life.events import build_actual_event_from_activity
from engine.life.history import EMPTY_HISTORY_HASH, fold_history, next_history_hash
from engine.life.ids import stable_id
from engine.life.runtime_orchestrator import RuntimeTargetAdvanceResult
from engine.life.runtime_persistence import (
    LAST_RUN_RELPATH,
    SOCIAL_RESPONSE_RELPATH,
    build_runtime_candidate_tree,
    compute_behavior_policy_hash,
    compute_runtime_semantic_tree_hash,
    empty_camera_roll_shard,
    snapshot_tree_bytes,
    write_runtime_json,
)
from engine.life.social_runtime import SocialResponseState, build_social_response_state
from engine.life.timeline import append_actual_event, close_day, empty_day
from tests.support.builders.captures import build_test_capture_fact
from tests.support.builders.runtime_bundle import (
    AS_OF,
    CHAR,
    ENGINE_SHA,
    build_synthetic_bundle_root,
    reference_sets as _refs,
    snapshot_tree as _snapshot_tree,
    write_json as _write_json,
)
from tests.support.builders.runtime_orchestrator import (
    POLICY,
    POLICY_VERSION,
    _add_min,
    _advance_refs,
    _continue_setup,
    _exact_end_with_post_finalize_start_inputs,
    _load_c3_bundle,
    _no_active_start_setup,
    _social_state,
    _stable_active_bundle,
    _target_inputs,
)

LIFE_BASE_SHA = "b" * 40
fingerprint = snapshot_tree_bytes



def _visual_context(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "character_id": CHAR,
        "character_source_sha": "public-persistence-character-sha",
        "engine_commit_sha": ENGINE_SHA,
        "behavior_policy_version": POLICY_VERSION,
        "location_id": "fixture-home",
        "appearance": {
            "outfit_state_id": None,
            "outfit_item_ids": [],
            "makeup_level": None,
            "hair_state_id": None,
        },
        "home_revision": None,
        "wardrobe_revision": None,
        "photographer_ref": None,
        "companion_refs": [],
        "lighting_context": "NATURAL",
    }
    appearance = overrides.pop("appearance", None)
    base.update(overrides)
    if appearance is not None:
        merged = dict(base["appearance"])
        merged.update(appearance)
        base["appearance"] = merged
    return base


def _activity(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "activity_instance_id": "activity:public-persistence-1",
        "activity_type": "LEISURE",
        "actual_start": "2026-04-01T10:00:00+09:00",
        "location_id": "fixture-home",
        "summary": "public persistence fixture",
    }
    base.update(overrides)
    return base


def _capture(
    *,
    activity_instance_id: str = "activity:public-persistence-1",
    occurrence_key: str = "decision:photo:public-1",
    captured_at: str = "2026-04-01T10:30:00+09:00",
    capture_kind: str = "DAILY_LIFE",
    subject_refs: Sequence[str] | None = None,
    visual_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_test_capture_fact(
        activity_instance_id=activity_instance_id,
        occurrence_key=occurrence_key,
        captured_at=captured_at,
        capture_kind=capture_kind,
        subject_refs=[] if subject_refs is None else list(subject_refs),
        visual_context=dict(visual_context) if visual_context is not None else _visual_context(),
    )

__all__ = [
    "AS_OF",
    "CHAR",
    "ENGINE_SHA",
    "LIFE_BASE_SHA",
    "POLICY",
    "POLICY_VERSION",
    "_add_min",
    "_advance_refs",
    "_refs",
    "_snapshot_tree",
    "_social_state",
    "_target_inputs",
    "_write_json",
    "bind_social_on_root",
    "build_5c1_persistent_root",
    "build_actual_event",
    "build_capture_event",
    "build_last_run_for_root",
    "build_response_row",
    "clone_advance_result",
    "day_with_events",
    "fingerprint",
    "finalize_disk_root_as_5c1",
    "make_c3_continue_root",
    "make_c3_exact_end_root",
    "make_c3_noop_root",
    "make_c3_start_root",
    "read_json",
    "real_advance",
    "run_candidate",
    "shard_with_records",
    "social_with_responses",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_response_row(
    *,
    response: str,
    opportunity_id: str = "opp:5c1-test-1",
    decided_at: str = AS_OF,
    reason_code: str | None = None,
) -> dict[str, Any]:
    if reason_code is None:
        reason_code = {
            "ACCEPT": "INVITE_FEASIBLE",
            "DECLINE": "LOW_SOCIAL_BATTERY",
            "DEFER": "INSUFFICIENT_WINDOW",
        }[response]
    return {
        "response_id": stable_id("social-response", opportunity_id, response),
        "opportunity_id": opportunity_id,
        "response": response,
        "decided_at": decided_at,
        "reason_code": reason_code,
    }


def social_with_responses(
    *responses: Mapping[str, Any] | str,
    character_id: str = CHAR,
    as_of: str = AS_OF,
) -> SocialResponseState:
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(responses):
        if isinstance(item, str):
            rows.append(
                build_response_row(
                    response=item,
                    opportunity_id=f"opp:5c1-{item.lower()}-{i}",
                    decided_at=as_of,
                )
            )
        else:
            rows.append(dict(item))
    return build_social_response_state(
        character_id=character_id,
        as_of=as_of,
        responses=rows,
    )


def bind_social_on_root(
    root: Path,
    social: SocialResponseState | Mapping[str, Any],
    *,
    bind_social: bool = True,
    state_revision: int | None = None,
) -> dict[str, Any]:
    social_obj = (
        social
        if isinstance(social, SocialResponseState)
        else build_social_response_state(
            character_id=str(social["character_id"]),
            as_of=str(social["as_of"]),
            responses=list(social.get("responses") or []),
        )
    )
    write_runtime_json(root / SOCIAL_RESPONSE_RELPATH, social_obj.as_dict())
    current = read_json(root / "state/current.json")
    current["domain_refs"] = dict(current["domain_refs"])
    if bind_social:
        current["domain_refs"]["social_response_revision"] = social_obj.revision
    if state_revision is not None:
        current["state_revision"] = int(state_revision)
    write_runtime_json(root / "state/current.json", current)
    return current


def build_actual_event(
    *,
    activity_instance_id: str = "activity:5c1-fixture-1",
    activity_type: str = "LEISURE",
    actual_start: str = "2026-04-01T10:00:00+09:00",
    actual_end: str = "2026-04-01T11:00:00+09:00",
    pending_captures: Sequence[Mapping[str, Any]] | None = None,
    **activity_overrides: Any,
) -> dict[str, Any]:
    activity = _activity(
        activity_instance_id=activity_instance_id,
        activity_type=activity_type,
        actual_start=actual_start,
        pending_captures=list(pending_captures or []),
        **activity_overrides,
    )
    return build_actual_event_from_activity(
        activity,
        actual_end=actual_end,
        finalized_at=actual_end,
    )


def build_capture_event(
    *,
    captured_at: str = "2026-04-01T10:30:00+09:00",
    occurrence_key: str = "decision:photo:5c1-1",
    activity_instance_id: str = "activity:5c1-fixture-1",
    **kwargs: Any,
) -> dict[str, Any]:
    return build_actual_event(
        activity_instance_id=activity_instance_id,
        pending_captures=[
            _capture(
                activity_instance_id=activity_instance_id,
                captured_at=captured_at,
                occurrence_key=occurrence_key,
            )
        ],
        **kwargs,
    )


def _history_for_events(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not events:
        return {
            "head_event_id": None,
            "history_hash": EMPTY_HISTORY_HASH,
            "event_count": 0,
        }
    return {
        "head_event_id": events[-1]["event_id"],
        "history_hash": fold_history(list(events)),
        "event_count": len(events),
    }


def _write_timeline_days(
    root: Path,
    days: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if isinstance(days, Mapping):
        items = list(days.values())
    else:
        items = list(days)
    for raw in items:
        day = dict(raw)
        date_str = str(day["date"])
        write_runtime_json(root / "timeline" / f"{date_str}.json", day)
        out[date_str] = day
    return out


def _write_camera_shards(
    root: Path,
    shards: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if isinstance(shards, Mapping):
        items = list(shards.values())
    else:
        items = list(shards)
    for raw in items:
        shard = dict(raw)
        month = str(shard["month"])
        write_runtime_json(root / "camera-roll" / "records" / f"{month}.json", shard)
        out[month] = shard
    return out


def build_last_run_for_root(
    root: Path,
    *,
    life_base_sha: str = LIFE_BASE_SHA,
    behavior_policy: Mapping[str, Any] | None = None,
    target_time: str | None = None,
    state_before_hash: str | None = None,
    files_changed: Sequence[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    current = read_json(root / "state/current.json")
    policy = dict(behavior_policy) if behavior_policy is not None else dict(POLICY)
    after_hash = compute_runtime_semantic_tree_hash(root)
    before_hash = state_before_hash or after_hash
    processed = current["processed_through"]
    target = target_time or processed
    files = list(files_changed) if files_changed is not None else [LAST_RUN_RELPATH]
    files = sorted(set(files))
    run_id = stable_id(
        "runtime-run",
        current["character_id"],
        life_base_sha,
        processed,
        target,
        before_hash,
        after_hash,
    )
    last_run: dict[str, Any] = {
        "schema_version": 1,
        "result": "SUCCESS",
        "run_id": run_id,
        "character_id": current["character_id"],
        "engine_commit_sha": current["engine_commit_sha"],
        "behavior_policy_version": current["behavior_policy_version"],
        "behavior_policy_hash": compute_behavior_policy_hash(policy),
        "life_base_sha": life_base_sha,
        "target_time": target,
        "previous_processed_through": processed,
        "processed_through": processed,
        "state_revision_before": max(0, int(current["state_revision"]) - 1),
        "state_revision_after": int(current["state_revision"]),
        "state_before_hash": before_hash,
        "state_after_hash": after_hash,
        "history_head_before": current["history"]["head_event_id"],
        "history_head_after": current["history"]["head_event_id"],
        "scheduler_events_processed_count": 0,
        "finalized_actual_events_count": 0,
        "capture_records_added_count": 0,
        "microsteps_used": 0,
        "files_changed": files,
    }
    last_run.update(overrides)
    return last_run


def build_5c1_persistent_root(
    tmp: Path | str,
    *,
    social: SocialResponseState | Mapping[str, Any] | None = None,
    bind_social: bool = True,
    state_revision: int = 0,
    include_last_run: bool = False,
    last_run: Mapping[str, Any] | None = None,
    timeline_days: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None = None,
    camera_shards: Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]] | None = None,
    mutate_current: Callable[[dict], dict] | None = None,
    character_id: str = CHAR,
    as_of: str = AS_OF,
    processed_through: str | None = None,
    engine_commit_sha: str = ENGINE_SHA,
    behavior_policy_version: str = "fixture-policy-1",
    use_c3_defaults: bool = False,
    active: Any = ...,
    queue_events: list | None = None,
    world_overrides: dict[str, dict] | None = None,
    include_timeline_dir: bool = False,
    auto_history: bool = True,
    auto_camera_from_timeline: bool = False,
    **bundle_kwargs: Any,
) -> Path:
    """Build a 5C1-ready persistent runtime root under ``tmp/bundle``."""
    tmp_path = Path(tmp)
    as_of_eff = processed_through or as_of
    if social is None:
        social_obj = build_social_response_state(
            character_id=character_id, as_of=as_of_eff
        )
    elif isinstance(social, SocialResponseState):
        social_obj = social
    else:
        social_obj = build_social_response_state(
            character_id=str(social.get("character_id", character_id)),
            as_of=str(social.get("as_of", as_of_eff)),
            responses=list(social.get("responses") or []),
        )

    events_for_history: list[dict[str, Any]] = []
    if timeline_days is not None:
        if isinstance(timeline_days, Mapping):
            day_list = [dict(v) for v in timeline_days.values()]
        else:
            day_list = [dict(v) for v in timeline_days]
        for day in sorted(day_list, key=lambda d: d["date"]):
            events_for_history.extend(dict(e) for e in (day.get("actual_events") or []))

    def _mut(current: dict) -> dict:
        out = dict(current)
        out["domain_refs"] = dict(out["domain_refs"])
        # Keep social_graph_revision distinct — never reuse social response digest.
        if "social_graph_revision" not in out["domain_refs"]:
            out["domain_refs"]["social_graph_revision"] = None
        if bind_social:
            out["domain_refs"]["social_response_revision"] = social_obj.revision
        else:
            out["domain_refs"]["social_response_revision"] = out["domain_refs"].get(
                "social_response_revision"
            )
        out["state_revision"] = int(state_revision)
        out["behavior_policy_version"] = behavior_policy_version
        if auto_history and events_for_history:
            out["history"] = _history_for_events(events_for_history)
        if use_c3_defaults:
            out["behavior_policy_version"] = POLICY_VERSION
        if mutate_current is not None:
            out = mutate_current(out)
        return out

    if use_c3_defaults:
        sentinel = ...
        act = None if active is None else (active if active is not sentinel else ...)
        _load_c3_bundle(
            str(tmp_path),
            active=act,
            queue_events=queue_events,
            processed_through=processed_through or as_of,
            mutate_current=_mut,
            **{k: v for k, v in bundle_kwargs.items() if k in {
                "commitments", "tasks", "world_seed", "human_state",
                "location_id", "use_social_relation",
            }},
        )
        root = tmp_path / "bundle"
    else:
        root = build_synthetic_bundle_root(
            tmp_path,
            character_id=character_id,
            as_of=as_of,
            processed_through=processed_through,
            engine_commit_sha=engine_commit_sha,
            include_timeline=include_timeline_dir or timeline_days is not None,
            mutate_current=_mut,
            world_overrides=world_overrides,
            **{k: v for k, v in bundle_kwargs.items() if k in {"mutate_schedule"}},
        )

    write_runtime_json(root / SOCIAL_RESPONSE_RELPATH, social_obj.as_dict())
    # Re-bind after write in case mutate_current overwrote the revision.
    if bind_social:
        current = read_json(root / "state/current.json")
        current["domain_refs"] = dict(current["domain_refs"])
        current["domain_refs"]["social_response_revision"] = social_obj.revision
        if auto_history and events_for_history:
            current["history"] = _history_for_events(events_for_history)
        if mutate_current is None:
            current["state_revision"] = int(state_revision)
        write_runtime_json(root / "state/current.json", current)

    written_days: dict[str, dict[str, Any]] = {}
    if timeline_days is not None:
        written_days = _write_timeline_days(root, timeline_days)

    if camera_shards is not None:
        _write_camera_shards(root, camera_shards)
    elif auto_camera_from_timeline and written_days:
        by_month: dict[str, list[dict[str, Any]]] = {}
        for day in written_days.values():
            for event in day.get("actual_events") or []:
                for rec in project_camera_roll_records(event):
                    month = rec["captured_at"][:7]
                    by_month.setdefault(month, []).append(rec)
        shards = {}
        for month, recs in by_month.items():
            shards[month] = merge_camera_roll_shard(empty_camera_roll_shard(month), recs)
        _write_camera_shards(root, shards)

    if include_last_run or last_run is not None:
        payload = dict(last_run) if last_run is not None else build_last_run_for_root(root)
        write_runtime_json(root / LAST_RUN_RELPATH, payload)

    return root


def finalize_disk_root_as_5c1(
    root: Path,
    social: SocialResponseState | None = None,
    *,
    state_revision: int = 0,
    bind_social: bool = True,
) -> tuple[Path, SocialResponseState, dict[str, Any]]:
    """Attach SocialResponseState + revision binding to an existing C3 disk root."""
    social_obj = social if social is not None else _social_state(
        as_of=read_json(root / "state/current.json")["processed_through"]
    )
    current = bind_social_on_root(
        root,
        social_obj,
        bind_social=bind_social,
        state_revision=state_revision,
    )
    return root, social_obj, current


def make_c3_noop_root(tmp: Path | str, *, state_revision: int = 0):
    """Stable active + no due work at target => semantic NOOP."""
    tmp_path = Path(tmp)
    _stable_active_bundle(str(tmp_path))
    root = tmp_path / "bundle"
    social = _social_state(as_of=AS_OF)
    current = bind_social_on_root(root, social, state_revision=state_revision)
    inputs = _target_inputs(event_budget=0)
    return root, social, current, inputs


def make_c3_start_root(tmp: Path | str, *, state_revision: int = 0):
    tmp_path = Path(tmp)
    bundle, wakeup, social, inputs, cand, instance_id = _no_active_start_setup(
        str(tmp_path)
    )
    root = tmp_path / "bundle"
    current = bind_social_on_root(root, social, state_revision=state_revision)
    return root, social, current, inputs, wakeup, cand, instance_id, bundle


def make_c3_continue_root(tmp: Path | str, *, state_revision: int = 0):
    tmp_path = Path(tmp)
    bundle, active, wakeup, social, inputs = _continue_setup(str(tmp_path))
    root = tmp_path / "bundle"
    current = bind_social_on_root(root, social, state_revision=state_revision)
    return root, social, current, inputs, active, wakeup, bundle


def make_c3_exact_end_root(tmp: Path | str, *, state_revision: int = 0):
    tmp_path = Path(tmp)
    (
        bundle,
        active,
        end_event,
        end,
        pf,
        social,
        inputs,
        cand,
        instance_id,
    ) = _exact_end_with_post_finalize_start_inputs(str(tmp_path))
    root = tmp_path / "bundle"
    current = bind_social_on_root(root, social, state_revision=state_revision)
    return (
        root,
        social,
        current,
        inputs,
        active,
        end_event,
        end,
        pf,
        cand,
        instance_id,
        bundle,
    )


def run_candidate(
    baseline_root: Path,
    *,
    candidate_root: Path | None = None,
    reference_sets=None,
    behavior_policy: Mapping[str, Any] | None = None,
    target_inputs=None,
    executing_engine_commit_sha: str | None = None,
    life_base_sha: str = LIFE_BASE_SHA,
):
    """Wrap ``build_runtime_candidate_tree`` with a sibling candidate directory."""
    baseline = Path(baseline_root)
    cand = (
        Path(candidate_root)
        if candidate_root is not None
        else baseline.parent / "candidate"
    )
    current = read_json(baseline / "state/current.json")
    refs = reference_sets if reference_sets is not None else _advance_refs()
    policy = behavior_policy if behavior_policy is not None else POLICY
    inputs = target_inputs if target_inputs is not None else _target_inputs(event_budget=0)
    exec_sha = (
        executing_engine_commit_sha
        if executing_engine_commit_sha is not None
        else current["engine_commit_sha"]
    )
    return build_runtime_candidate_tree(
        baseline_root=baseline,
        candidate_root=cand,
        reference_sets=refs,
        behavior_policy=policy,
        target_inputs=inputs,
        executing_engine_commit_sha=exec_sha,
        life_base_sha=life_base_sha,
    )


def clone_advance_result(
    real: Any,
    *,
    bundle: Any = None,
    social_response_state: Any = None,
    actual_events: Sequence[Mapping[str, Any]] | None = None,
    camera_roll_records: Sequence[Mapping[str, Any]] | None = None,
    camera_roll_records_by_shard_path: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    consumed_source_keys: Sequence[str] | None = None,
    consumed_trigger_ids: Sequence[str] | None = None,
    decision_frames: Any = None,
    microsteps_used: int | None = None,
) -> RuntimeTargetAdvanceResult:
    """Build a RuntimeTargetAdvanceResult from a real C3 result with overrides."""
    from engine.life.runtime_orchestrator import RuntimeTargetAdvanceResult

    events = (
        tuple(dict(e) for e in actual_events)
        if actual_events is not None
        else tuple(dict(e) for e in real.actual_events)
    )
    records = (
        tuple(dict(r) for r in camera_roll_records)
        if camera_roll_records is not None
        else tuple(dict(r) for r in real.camera_roll_records)
    )
    by_path = (
        {
            path: tuple(dict(r) for r in rows)
            for path, rows in camera_roll_records_by_shard_path.items()
        }
        if camera_roll_records_by_shard_path is not None
        else {
            path: tuple(dict(r) for r in rows)
            for path, rows in real.camera_roll_records_by_shard_path.items()
        }
    )
    return RuntimeTargetAdvanceResult(
        bundle=bundle if bundle is not None else real.bundle,
        social_response_state=(
            social_response_state
            if social_response_state is not None
            else real.social_response_state
        ),
        actual_events=events,
        camera_roll_records=records,
        camera_roll_records_by_shard_path=by_path,
        consumed_source_keys=tuple(
            consumed_source_keys
            if consumed_source_keys is not None
            else real.consumed_source_keys
        ),
        consumed_trigger_ids=tuple(
            consumed_trigger_ids
            if consumed_trigger_ids is not None
            else real.consumed_trigger_ids
        ),
        decision_frames=(
            decision_frames if decision_frames is not None else real.decision_frames
        ),
        microsteps_used=(
            microsteps_used if microsteps_used is not None else real.microsteps_used
        ),
    )


def real_advance(**kwargs: Any):
    from engine.life.runtime_orchestrator import advance_runtime_to_target

    return advance_runtime_to_target(**kwargs)


def day_with_events(
    *events: Mapping[str, Any],
    date: str | None = None,
    status: str = "OPEN",
    closed_at: str | None = None,
) -> dict[str, Any]:
    if not events:
        raise ValueError("day_with_events requires at least one event")
    date_str = date or str(events[0]["actual_start"])[:10]
    day = empty_day(date_str)
    for event in events:
        day = append_actual_event(day, dict(event))
    if status == "CLOSED":
        day = close_day(day)
        if closed_at is not None:
            day["closed_at"] = closed_at
    return day


def shard_with_records(
    month: str,
    records: Sequence[Mapping[str, Any]],
    *,
    closed_at: str | None = None,
) -> dict[str, Any]:
    shard = merge_camera_roll_shard(empty_camera_roll_shard(month), list(records))
    if closed_at is not None:
        shard = dict(shard)
        shard["closed_at"] = closed_at
    return shard
