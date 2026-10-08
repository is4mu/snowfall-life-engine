"""Discrete event clock, decision wakeups, catch-up, advance.

Advance is transactional: timeline/checkpoint mutations are staged in memory and
published to the workspace only after the full advance validates successfully.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .checkpoint import bump_revision, load_checkpoint, validate_checkpoint, write_checkpoint
from .effects import apply_effects
from .errors import ErrorCode, LifeEngineError
from .events import finalize_activity
from .fixed_point import integrate_rates, validate_human_state
from .history import collect_ledger_events, next_history_hash, verify_history
from .ids import IdRegistry, stable_id
from .invariants import (
    assert_active_not_already_finalized,
    assert_monotonic_timeline_events,
    assert_unique_event_ids,
    collect_finalized_ids,
    collect_finalized_instance_ids,
    queue_event_sort_key,
    validate_actual_event_times,
)
from .policy import assert_policy_allowed, assert_policy_matches_checkpoint, load_policy
from .schema import validate_instance
from .timeutil import canonicalize_timestamp, format_rfc3339, parse_rfc3339
from .timeline import (
    append_actual_event,
    belonging_date,
    close_day,
    empty_day,
    logical_closed_at,
    should_close_day,
)
from .workspace import assert_safe_workspace

CATCHUP_LIMIT_DAYS = 7
CATCHUP_LIMIT_SECONDS = CATCHUP_LIMIT_DAYS * 86400
DEFAULT_MAX_EVENTS_PER_ADVANCE = 10000

# Fields excluded from semantic workspace equivalence (operational/diagnostic).
SEMANTIC_EXCLUSIONS = frozenset({"state_revision"})


class WorkspaceStage:
    """In-memory staging for timeline days; publish only on success."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self._days: dict[str, dict[str, Any]] = {}
        self._loaded_from_disk: set[str] = set()

    def load_day(self, date_str: str) -> dict[str, Any]:
        if date_str in self._days:
            return deepcopy(self._days[date_str])
        path = self.workspace / "timeline" / f"{date_str}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            validate_instance(data, "timeline_day")
            self._loaded_from_disk.add(date_str)
        else:
            data = empty_day(date_str)
        self._days[date_str] = deepcopy(data)
        return deepcopy(data)

    def save_day(self, day: dict[str, Any]) -> None:
        from .canonical import normalize_persisted_object

        day = normalize_persisted_object(day)
        validate_instance(day, "timeline_day")
        self._days[day["date"]] = deepcopy(day)

    def all_days(self) -> list[dict[str, Any]]:
        tdir = self.workspace / "timeline"
        dates: set[str] = set(self._days.keys())
        if tdir.exists():
            for path in tdir.glob("*.json"):
                dates.add(path.stem)
        out: list[dict[str, Any]] = []
        for date_str in sorted(dates):
            out.append(self.load_day(date_str))
        return out

    def publish(self) -> None:
        """Atomically write staged days via temp files + replace."""
        tdir = self.workspace / "timeline"
        tdir.mkdir(parents=True, exist_ok=True)
        for date_str, day in self._days.items():
            from .canonical import normalize_persisted_object

            day = normalize_persisted_object(day)
            validate_instance(day, "timeline_day")
            final = tdir / f"{date_str}.json"
            fd, tmp_name = tempfile.mkstemp(prefix=f".{date_str}.", suffix=".tmp", dir=tdir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(day, fh, ensure_ascii=False, indent=2)
                    fh.write("\n")
                os.replace(tmp_name, final)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise


def _sort_key(event: dict[str, Any]) -> tuple[datetime, int, str]:
    return queue_event_sort_key(event)


def enqueue_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    data = dict(event)
    if "schema_version" not in data:
        data["schema_version"] = 1
    data["due_at"] = canonicalize_timestamp(data["due_at"], field="due_at")
    validate_instance(data, "queued_internal_event")
    out = deepcopy(state)
    ids = [e["event_id"] for e in out["pending_queue"]["events"]]
    if data["event_id"] in ids:
        raise LifeEngineError(ErrorCode.DUPLICATE_ID, data["event_id"])
    out["pending_queue"]["events"].append(data)
    out["pending_queue"]["events"].sort(key=_sort_key)
    return out


def _queue_ids(state: dict[str, Any]) -> set[str]:
    return {e["event_id"] for e in state["pending_queue"]["events"]}


def schedule_idle_wakeup(state: dict[str, Any], policy: dict[str, Any], due_at: datetime) -> dict[str, Any]:
    interval = int(policy["idle_reassessment"]["interval_min"])
    due_str = format_rfc3339(due_at)
    event_id = stable_id("idle", state["character_id"], due_str, interval)
    if event_id in _queue_ids(state):
        return state
    event = {
        "schema_version": 1,
        "event_id": event_id,
        "event_kind": "IDLE_REASSESSMENT",
        "due_at": due_str,
        "priority": 100,
        "payload": {"interval_min": interval},
    }
    return enqueue_event(state, event)


def idle_slot_times(
    epoch: datetime,
    interval_min: int,
    *,
    after: datetime,
    until: datetime,
) -> list[datetime]:
    """Absolute idle reassessment slots in (after, until] from life_epoch grid."""
    if interval_min < 1:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "interval_min must be >= 1")
    step = timedelta(minutes=interval_min)
    t = epoch + step
    guard = 0
    while t <= after:
        t += step
        guard += 1
        if guard > 1_000_000:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "idle_slot_times overflow")
    out: list[datetime] = []
    while t <= until:
        out.append(t)
        t += step
        guard += 1
        if guard > 1_000_000:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "idle_slot_times overflow")
    return out


def ensure_idle_slots(
    state: dict[str, Any],
    policy: dict[str, Any],
    *,
    processed: datetime,
    target: datetime,
) -> dict[str, Any]:
    """Enqueue absolute idle slots due in (processed, target] (heartbeat-invariant)."""
    interval = int(policy["idle_reassessment"]["interval_min"])
    epoch = parse_rfc3339(state["life_epoch"], field="life_epoch")
    out = state
    for due in idle_slot_times(epoch, interval, after=processed, until=target):
        out = schedule_idle_wakeup(out, policy, due)
    return out


def _threshold_eta_seconds(
    current: int,
    rate: int,
    threshold: int,
    remainder: int,
) -> int | None:
    if rate <= 0 or current >= threshold:
        return None
    need = threshold - current
    target_accum = need * 3600 - remainder
    if target_accum <= 0:
        return 0
    return (target_accum + rate - 1) // rate


def reconcile_threshold_wakeups(
    state: dict[str, Any],
    policy: dict[str, Any],
    *,
    from_time: datetime,
) -> dict[str, Any]:
    """Remove/replace threshold DECISION_WAKEUPs from current human_state trajectory.

    After effects change human_state, previously queued crossing times may be stale.
    """
    out = deepcopy(state)
    # Drop all pending threshold wakeups; rebuild from current state.
    out["pending_queue"]["events"] = [
        e
        for e in out["pending_queue"]["events"]
        if not (
            e["event_kind"] == "DECISION_WAKEUP"
            and str(e.get("payload", {}).get("reason", "")).startswith("threshold:")
        )
    ]

    rates = policy["rates"]
    thresholds = policy["thresholds"]
    hs = validate_human_state(out["human_state"])
    remainders = out["integration"]["rate_remainders"]

    for field, rate_key, thr_key in (
        ("hunger", "hunger_per_hour", "hunger_wakeup"),
        ("physical_fatigue", "physical_fatigue_per_hour", "fatigue_wakeup"),
        ("stress", "stress_per_hour", "stress_wakeup"),
    ):
        secs = _threshold_eta_seconds(
            hs[field],
            rates[rate_key],
            thresholds[thr_key],
            int(remainders[field]),
        )
        if secs is None:
            # Already at/above threshold: leave no future wakeup for this field.
            continue
        due = from_time + timedelta(seconds=secs)
        due_str = format_rfc3339(due)
        reason = f"threshold:{field}"
        event_id = stable_id("wakeup", out["character_id"], reason, due_str)
        if event_id in _queue_ids(out):
            continue
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "event_kind": "DECISION_WAKEUP",
            "due_at": due_str,
            "priority": 50,
            "payload": {
                "reason": reason,
                "decision_key": reason,
                "field": field,
            },
        }
        out = enqueue_event(out, event)
    return out


# Back-compat alias used by tests/helpers
schedule_threshold_wakeups = reconcile_threshold_wakeups


def _disk_days(workspace: Path) -> list[dict[str, Any]]:
    tdir = workspace / "timeline"
    if not tdir.exists():
        return []
    days = []
    for path in sorted(tdir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        validate_instance(data, "timeline_day")
        days.append(data)
    return days


def verify_workspace_history(workspace: Path, state: dict[str, Any], *, stage: WorkspaceStage | None = None) -> None:
    from .invariants import validate_pending_queue, validate_timeline_day_semantics

    days = stage.all_days() if stage is not None else _disk_days(workspace)
    active = state["context"].get("active_activity")
    processed = state["processed_through"]
    for day in days:
        validate_timeline_day_semantics(
            day,
            processed_through=processed,
            active_activity=active,
        )
    events = collect_ledger_events(days)
    for event in events:
        validate_actual_event_times(event)
    assert_unique_event_ids(events)
    assert_monotonic_timeline_events(events)
    verify_history(
        events,
        head_event_id=state["history"]["head_event_id"],
        history_hash=state["history"]["history_hash"],
        event_count=state["history"]["event_count"],
    )
    assert_active_not_already_finalized(
        active,
        collect_finalized_instance_ids(events),
    )
    validate_pending_queue(state)


def check_catchup_bounds(processed_through: datetime, target: datetime, life_epoch: datetime) -> str | None:
    if target < life_epoch:
        raise LifeEngineError(ErrorCode.TARGET_BEFORE_EPOCH, format_rfc3339(target))
    if target < processed_through:
        raise LifeEngineError(ErrorCode.TIME_REVERSAL, format_rfc3339(target))
    if target == processed_through:
        return "NOOP"
    gap_seconds = int((target - processed_through).total_seconds())
    if gap_seconds > CATCHUP_LIMIT_SECONDS:
        raise LifeEngineError(ErrorCode.CATCHUP_LIMIT_EXCEEDED, f"gap_seconds={gap_seconds}")
    return None


def _policy_rates(policy: dict[str, Any]) -> dict[str, int]:
    rates = policy["rates"]
    return {
        "hunger": int(rates["hunger_per_hour"]),
        "physical_fatigue": int(rates["physical_fatigue_per_hour"]),
        "stress": int(rates["stress_per_hour"]),
        "affect_valence": int(rates.get("affect_valence_per_hour", 0)),
        "social_battery": int(rates.get("social_battery_per_hour", 0)),
    }


def _integrate_until(state: dict[str, Any], policy: dict[str, Any], until: datetime) -> dict[str, Any]:
    out = deepcopy(state)
    current = parse_rfc3339(out["processed_through"], field="processed_through")
    elapsed = int((until - current).total_seconds())
    if elapsed < 0:
        raise LifeEngineError(ErrorCode.TIME_REVERSAL, format_rfc3339(until))
    if elapsed == 0:
        return out
    new_hs, new_rem = integrate_rates(
        out["human_state"],
        rates=_policy_rates(policy),
        elapsed_seconds=elapsed,
        remainders=out["integration"]["rate_remainders"],
    )
    out["human_state"] = new_hs
    out["integration"] = {
        "schema_version": 1,
        "rate_remainders": new_rem,
    }
    out["processed_through"] = format_rfc3339(until)
    return out


def _close_elapsed_days(state: dict[str, Any], stage: WorkspaceStage, as_of: datetime) -> None:
    for day in stage.all_days():
        if should_close_day(state, day, as_of=as_of):
            # Deterministic closed_at (not observation/as_of).
            stage.save_day(close_day(day))


def _process_event(
    state: dict[str, Any],
    policy: dict[str, Any],
    event: dict[str, Any],
    stage: WorkspaceStage,
    registry: IdRegistry,
    finalized_ids: list[str],
    known_finalized: set[str],
) -> tuple[dict[str, Any], int]:
    due = parse_rfc3339(event["due_at"], field="due_at")
    state = _integrate_until(state, policy, due)
    wakeups = 0
    kind = event["event_kind"]

    if kind in {"DECISION_WAKEUP", "IDLE_REASSESSMENT"}:
        wakeups = 1
        if kind == "IDLE_REASSESSMENT":
            interval = int(
                event.get("payload", {}).get("interval_min")
                or policy["idle_reassessment"]["interval_min"]
            )
            nxt = due + timedelta(minutes=interval)
            state = schedule_idle_wakeup(state, policy, nxt)

    elif kind == "ACTIVITY_END":
        active = state["context"].get("active_activity")
        if active is None:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "ACTIVITY_END without active_activity")
        payload_id = event.get("payload", {}).get("activity_instance_id")
        if payload_id != active["activity_instance_id"]:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "ACTIVITY_END activity_instance_id mismatch")
        end_str = format_rfc3339(due)
        state, actual = finalize_activity(
            state,
            actual_end=end_str,
            finalized_at=end_str,
            registry=registry,
            known_finalized_instance_ids=known_finalized,
        )
        state = apply_effects(state, actual.get("effects", []))
        date_str = belonging_date(actual["actual_start"])
        day = stage.load_day(date_str)
        day = append_actual_event(day, actual)
        stage.save_day(day)
        ledger = collect_ledger_events(stage.all_days())
        assert_unique_event_ids(ledger)
        assert_monotonic_timeline_events(ledger)
        prev = state["history"]["history_hash"]
        state["history"]["history_hash"] = next_history_hash(prev, actual)
        state["history"]["head_event_id"] = actual["event_id"]
        state["history"]["event_count"] = int(state["history"]["event_count"]) + 1
        finalized_ids.append(actual["event_id"])
        known_finalized.add(actual["activity_instance_id"])
        _close_elapsed_days(state, stage, due)
        # Effects may change human_state → reconcile threshold wakeups
        state = reconcile_threshold_wakeups(state, policy, from_time=due)
    else:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_EFFECT, f"unknown event_kind={kind}")

    return state, wakeups


def advance(
    workspace: Path | str,
    *,
    target: str,
    policy_path: Path | str,
    production_mode: bool = False,
    state_filename: str = "current_state.json",
) -> dict[str, Any]:
    ws = assert_safe_workspace(workspace)
    policy = load_policy(policy_path)
    assert_policy_allowed(policy, production_mode=production_mode)

    state_path = ws / state_filename
    if not state_path.exists():
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"missing checkpoint: {state_path}")
    # Fail-closed: ledger must match checkpoint before any mutation.
    # Load first without instance check, then verify_workspace_history enforces
    # active_activity not already finalized.
    state = load_checkpoint(state_path)
    verify_workspace_history(ws, state)
    # Re-validate checkpoint including active times + finalized-instance rejection.
    events = collect_ledger_events(_disk_days(ws))
    validate_checkpoint(
        state,
        finalized_instance_ids=collect_finalized_instance_ids(events),
    )
    assert_policy_matches_checkpoint(state, policy)

    target_dt = parse_rfc3339(target, field="target")
    processed = parse_rfc3339(state["processed_through"], field="processed_through")
    epoch = parse_rfc3339(state["life_epoch"], field="life_epoch")

    noop = check_catchup_bounds(processed, target_dt, epoch)
    if noop == "NOOP":
        result = {
            "schema_version": 1,
            "status": "NOOP",
            "processed_through": state["processed_through"],
            "events_processed_count": 0,
            "wakeups_fired_count": 0,
            "finalized_event_ids": [],
        }
        validate_instance(result, "advance_result")
        return result

    stage = WorkspaceStage(ws)
    known_finalized = collect_finalized_instance_ids(collect_ledger_events(stage.all_days()))

    state = reconcile_threshold_wakeups(state, policy, from_time=processed)
    state = ensure_idle_slots(state, policy, processed=processed, target=target_dt)

    max_events = int(
        policy["idle_reassessment"].get("max_events_per_advance", DEFAULT_MAX_EVENTS_PER_ADVANCE)
    )
    if max_events < 1:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "max_events_per_advance must be >= 1")

    registry = IdRegistry()
    events_processed = 0
    wakeups_fired = 0
    finalized_ids: list[str] = []

    # Entire event loop is in-memory; publish only after success.
    while True:
        due_events = [
            e
            for e in state["pending_queue"]["events"]
            if parse_rfc3339(e["due_at"], field="due_at") <= target_dt
        ]
        if not due_events:
            break
        due_events.sort(key=_sort_key)
        event = due_events[0]

        if events_processed >= max_events:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"max_events_per_advance exceeded: {max_events}",
            )

        state = deepcopy(state)
        state["pending_queue"]["events"] = [
            e for e in state["pending_queue"]["events"] if e["event_id"] != event["event_id"]
        ]
        state, w = _process_event(
            state, policy, event, stage, registry, finalized_ids, known_finalized
        )
        events_processed += 1
        wakeups_fired += w
        state = reconcile_threshold_wakeups(
            state,
            policy,
            from_time=parse_rfc3339(state["processed_through"], field="processed_through"),
        )

    state = _integrate_until(state, policy, target_dt)
    _close_elapsed_days(state, stage, target_dt)
    state = bump_revision(state)
    validate_checkpoint(state)
    verify_workspace_history(ws, state, stage=stage)

    # Publish staged timeline then checkpoint (checkpoint last so failure mid-publish
    # leaves verify-on-next-load able to detect mismatch if days partially written;
    # days use atomic replace per file).
    stage.publish()
    write_checkpoint(state_path, state)

    result = {
        "schema_version": 1,
        "status": "ADVANCED",
        "processed_through": state["processed_through"],
        "events_processed_count": events_processed,
        "wakeups_fired_count": wakeups_fired,
        "finalized_event_ids": finalized_ids,
    }
    validate_instance(result, "advance_result")
    return result


def verify_workspace(workspace: Path | str, *, state_filename: str = "current_state.json") -> None:
    ws = assert_safe_workspace(workspace)
    state = load_checkpoint(ws / state_filename)
    verify_workspace_history(ws, state)


def semantic_state_view(state: dict[str, Any]) -> dict[str, Any]:
    """Checkpoint view excluding operational/diagnostic fields."""
    out = deepcopy(state)
    for key in SEMANTIC_EXCLUSIONS:
        out.pop(key, None)
    return out


def snapshot_workspace_bytes(workspace: Path, *, state_filename: str = "current_state.json") -> dict[str, bytes]:
    """Fingerprint all runtime files under workspace for byte-equality tests."""
    out: dict[str, bytes] = {}
    state_path = workspace / state_filename
    if state_path.exists():
        out[state_filename] = state_path.read_bytes()
    tdir = workspace / "timeline"
    if tdir.exists():
        for path in sorted(tdir.glob("*.json")):
            out[f"timeline/{path.name}"] = path.read_bytes()
    return out
