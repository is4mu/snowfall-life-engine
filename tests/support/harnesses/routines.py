"""Synthetic helpers for routine adapter tests (no I/O)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from engine.life.policy import load_policy
from engine.life.routine_adapters import (
    RoutineAdapterContext,
    RoutineAdapterResult,
    adapt_routine_domains,
)

from tests.support.constants import POLICY_V2_CANDIDATE_PATH

NOW = "2026-03-15T12:00:00+09:00"
HOME = "fixture-home"
GYM = "fixture-gym"
KITCHEN = "fixture-kitchen"


def policy() -> dict[str, Any]:
    return load_policy(POLICY_V2_CANDIDATE_PATH)


def ctx(
    *,
    now: str = NOW,
    location: str = HOME,
    available_window_min: int = 120,
    physical: Mapping[str, bool] | None = None,
    kickboxing_location_feasible: bool = True,
    kickboxing_self_generated_today: int = 0,
    kickboxing_equivalent_pending_plans: int = 0,
    physical_fatigue_band: str = "LOW",
) -> RoutineAdapterContext:
    # Default both facts for convenience; explicit mapping (including {}) replaces.
    if physical is None:
        phys: dict[str, bool] = {"HOUSEHOLD": True, "KICKBOXING": True}
    else:
        phys = dict(physical)
    return RoutineAdapterContext(
        now=now,
        current_location_id=location,
        available_window_min=available_window_min,
        physical_feasible_by_action=phys,
        kickboxing_location_feasible=kickboxing_location_feasible,
        kickboxing_self_generated_today=kickboxing_self_generated_today,
        kickboxing_equivalent_pending_plans=kickboxing_equivalent_pending_plans,
        physical_fatigue_band=physical_fatigue_band,
    )


def household(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "household_task_id": "hh-1",
        "created_at": "2026-03-13T12:00:00+09:00",  # 2 days → PRESSING at NOW
        "status": "OPEN",
        "location_id": HOME,
    }
    base.update(overrides)
    return base


def session(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "kb-1",
        "actual_start": "2026-03-14T18:00:00+09:00",
        "actual_end": "2026-03-14T19:00:00+09:00",
        "finalized_at": "2026-03-14T19:05:00+09:00",
    }
    base.update(overrides)
    return base


def adapt(
    *,
    context: RoutineAdapterContext | None = None,
    household_tasks: Sequence[Mapping[str, Any]] = (),
    kickboxing_sessions: Sequence[Mapping[str, Any]] = (),
    behavior_policy: Mapping[str, Any] | None = None,
) -> RoutineAdapterResult:
    return adapt_routine_domains(
        context=context or ctx(),
        behavior_policy=behavior_policy or policy(),
        household_tasks=household_tasks,
        kickboxing_sessions=kickboxing_sessions,
    )


def opp_keys(result: RoutineAdapterResult) -> list[str]:
    return [o.opportunity_key for o in result.opportunities]


def diag_codes(result: RoutineAdapterResult, *, source_ref: str | None = None) -> list[str]:
    out = []
    for d in result.diagnostics:
        if source_ref is None or d.source_ref == source_ref:
            out.append(d.code)
    return out


def boundary_keys(result: RoutineAdapterResult) -> list[str]:
    return [b.decision_key for b in result.decision_boundaries]
