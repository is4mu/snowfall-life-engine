"""Synthetic schedule fixtures shared by public runtime tests."""

from __future__ import annotations

AS_OF = "2026-04-01T12:00:00+09:00"
CHAR = "fixture-character"


def provenance(**overrides) -> dict:
    base = {"origin": "SIMULATION_BOOTSTRAP", "notes": "slice5a-synthetic"}
    base.update(overrides)
    return base


def commitment(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "commitment_id": "cmt-a",
        "kind": "WORK",
        "hardness": "HARD",
        "created_at": "2026-04-01T08:00:00+09:00",
        "timing": {
            "timing_kind": "EXACT",
            "planned_start": "2026-04-02T14:00:00+09:00",
            "planned_end": "2026-04-02T18:00:00+09:00",
        },
        "location_id": "fixture-campus",
        "participants": [CHAR],
        "source_kind": "INTERNAL",
        "provenance": provenance(),
        "recurrence_id": None,
        "status": "PLANNED",
        "status_history": [
            {
                "changed_at": "2026-04-01T08:00:00+09:00",
                "from": None,
                "to": "PLANNED",
                "reason": "created",
                "source_ref": "engine:create",
            }
        ],
    }
    timing = overrides.pop("timing", None)
    history = overrides.pop("status_history", None)
    base.update(overrides)
    if timing is not None:
        base["timing"] = timing
    if history is not None:
        base["status_history"] = history
    return base


def task(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "task_id": "task-a",
        "domain": "ACADEMIC",
        "created_at": "2026-04-01T09:00:00+09:00",
        "earliest_start": None,
        "due_at": "2026-04-10T18:00:00+09:00",
        "due_window": None,
        "effort_remaining_min": 120,
        "min_work_chunk_min": 30,
        "priority": "NORMAL",
        "location_constraints": ["fixture-desk"],
        "status": "OPEN",
        "provenance": provenance(),
        "source_kind": "INTERNAL",
        "caused_by_event_id": None,
    }
    base.update(overrides)
    return base


