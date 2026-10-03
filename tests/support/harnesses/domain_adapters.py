"""Synthetic helpers for domain adapter tests (no I/O)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from engine.life.domain_adapters import (
    AdapterResult,
    DomainAdapterContext,
    adapt_structural_domains,
)

NOW = "2026-03-15T12:00:00+09:00"
HOME = "fixture-home"
CAMPUS = "fixture-campus"
DESK = "fixture-desk"
GYM = "fixture-gym"


def _prov(**overrides: Any) -> dict[str, Any]:
    base = {"origin": "ENGINE_DEFAULT", "notes": "synthetic-slice2e"}
    base.update(overrides)
    return base


def ctx(
    *,
    now: str = NOW,
    location: str = HOME,
    available_window_min: int = 120,
    physical: Mapping[str, bool] | None = None,
) -> DomainAdapterContext:
    phys = {
        "WORK": True,
        "HARD_COMMITMENT_ACTIVITY": True,
        "TRAVEL_TO_COMMITMENT": True,
        "SOCIAL_PROMISE": True,
        "ERRAND": True,
        "STUDY": True,
    }
    if physical:
        phys.update(dict(physical))
    return DomainAdapterContext(
        now=now,
        current_location_id=location,
        available_window_min=available_window_min,
        physical_feasible_by_action=phys,
    )


def exact_commitment(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "commitment_id": "cmt-exact-1",
        "kind": "WORK",
        "hardness": "HARD",
        "created_at": "2026-03-15T08:00:00+09:00",
        "timing": {
            "timing_kind": "EXACT",
            "planned_start": "2026-03-15T14:00:00+09:00",
            "planned_end": "2026-03-15T18:00:00+09:00",
        },
        "location_id": CAMPUS,
        "participants": ["fixture-character"],
        "source_kind": "INTERNAL",
        "provenance": _prov(),
        "recurrence_id": None,
        "status": "PLANNED",
        "status_history": [
            {
                "changed_at": "2026-03-15T08:00:00+09:00",
                "from": None,
                "to": "PLANNED",
                "reason": "created",
                "source_ref": "engine:create",
            }
        ],
    }
    timing_override = overrides.pop("timing", None)
    history_override = overrides.pop("status_history", None)
    base.update(overrides)
    if timing_override is not None:
        base["timing"] = timing_override
    if history_override is not None:
        base["status_history"] = history_override
    return base


def window_commitment(**overrides: Any) -> dict[str, Any]:
    base = exact_commitment(
        commitment_id="cmt-window-1",
        kind="SOCIAL",
        hardness="SOFT",
        source_kind="EXOGENOUS_SOCIAL",
        timing={
            "timing_kind": "WINDOW",
            "earliest_start": "2026-03-15T18:00:00+09:00",
            "latest_start": "2026-03-15T20:00:00+09:00",
            "duration_min": 60,
            "duration_max": 120,
        },
        location_id=None,
        participants=["fixture-character", "fixture-friend-a"],
    )
    base.update(overrides)
    return base


def obligation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "task_id": "task-1",
        "domain": "ACADEMIC",
        "created_at": "2026-03-15T09:00:00+09:00",
        "earliest_start": None,
        "due_at": "2026-03-15T18:00:00+09:00",
        "due_window": None,
        "effort_remaining_min": 120,
        "min_work_chunk_min": 30,
        "priority": "NORMAL",
        "location_constraints": [DESK],
        "status": "OPEN",
        "provenance": _prov(),
        "source_kind": "INTERNAL",
        "caused_by_event_id": None,
    }
    base.update(overrides)
    return base


def route(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "route_profile_id": "route-home-campus",
        "origin_location_id": HOME,
        "destination_location_id": CAMPUS,
        "mode": "PUBLIC_TRANSIT",
        "duration_min": 25,
        "duration_max": 45,
        "effort_class": "LIGHT_ACTIVE",
        "provenance": _prov(),
    }
    base.update(overrides)
    return base


def adapt(
    *,
    context: DomainAdapterContext | None = None,
    commitments: Sequence[Mapping[str, Any]] = (),
    obligation_tasks: Sequence[Mapping[str, Any]] = (),
    route_profiles: Sequence[Mapping[str, Any]] = (),
) -> AdapterResult:
    return adapt_structural_domains(
        context=context or ctx(),
        commitments=commitments,
        obligation_tasks=obligation_tasks,
        route_profiles=route_profiles,
    )


def opp_keys(result: AdapterResult) -> list[str]:
    return [o.opportunity_key for o in result.opportunities]


def diag_codes(result: AdapterResult, *, source_ref: str | None = None) -> list[str]:
    out = []
    for d in result.diagnostics:
        if source_ref is None or d.source_ref == source_ref:
            out.append(d.code)
    return out
