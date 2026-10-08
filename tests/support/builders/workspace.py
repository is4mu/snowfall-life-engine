"""Synthetic runtime workspace helpers for public tests."""

from __future__ import annotations

import json
from pathlib import Path

from engine.life.checkpoint import empty_checkpoint, write_checkpoint
from engine.life.clock import enqueue_event
from engine.life.ids import stable_id
from engine.life.timeutil import canonicalize_timestamp, parse_rfc3339

from tests.support.constants import POLICY_PATH


def policy_path() -> Path:
    return POLICY_PATH


def make_workspace(tmp: Path, **kwargs) -> Path:
    ws = tmp / "life-ws"
    ws.mkdir(parents=True)
    (ws / "timeline").mkdir()
    defaults = dict(
        character_id="fixture-character",
        life_epoch="2026-01-01T00:00:00+09:00",
        world_seed="fixture-world-seed",
        behavior_policy_version="fixture-policy-1",
        engine_commit_sha="0" * 40,
        location_id="fixture-home",
        human_state={
            "sleep_debt_min": 0,
            "hunger": 100,
            "physical_fatigue": 100,
            "affect_valence": 0,
            "stress": 100,
            "social_battery": 800,
        },
    )
    defaults.update(kwargs)
    state = empty_checkpoint(**defaults)
    write_checkpoint(ws / "current_state.json", state)
    return ws


def load_state(ws: Path) -> dict:
    return json.loads((ws / "current_state.json").read_text(encoding="utf-8"))


def save_state(ws: Path, state: dict) -> None:
    write_checkpoint(ws / "current_state.json", state)


def queue_activity_end(ws: Path, *, activity: dict, due_at: str) -> None:
    state = load_state(ws)
    if "schema_version" not in activity:
        activity = {**activity, "schema_version": 1}
    if "activity_instance_id" not in activity and "activity_id" in activity:
        activity = {**activity, "activity_instance_id": activity["activity_id"]}
        activity = {k: v for k, v in activity.items() if k != "activity_id"}
    state["context"]["active_activity"] = activity

    start = parse_rfc3339(activity["actual_start"], field="actual_start")
    processed = parse_rfc3339(state["processed_through"], field="processed_through")
    if start > processed:
        state["processed_through"] = canonicalize_timestamp(
            activity["actual_start"], field="actual_start"
        )

    event = {
        "schema_version": 1,
        "event_id": stable_id("activity-end", activity["activity_instance_id"], due_at),
        "event_kind": "ACTIVITY_END",
        "due_at": due_at,
        "priority": 10,
        "payload": {"activity_instance_id": activity["activity_instance_id"]},
    }
    state = enqueue_event(state, event)
    save_state(ws, state)
