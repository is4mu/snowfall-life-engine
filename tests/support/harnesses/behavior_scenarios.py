"""Reusable deterministic cross-domain decision scenarios for public tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from engine.life.timeutil import format_rfc3339, parse_rfc3339
from tests.support.harnesses.integrated_behavior import (
    CAMPUS,
    DESK,
    HOME,
    MIZUKI,
    RENA,
    WORLD_SEED,
    DecisionSnapshot,
    SocialGenerationSpec,
    SocialResponseSpec,
    contact_history,
    default_assignments,
    exact_commitment,
    household,
    human_state,
    obligation,
    post_work_context,
    resolver_boundary,
    route,
    window_commitment,
)
from tests.support.harnesses.resolver import active_ctx
from tests.support.harnesses.social_response import (
    availability,
    local_invite,
    post_work_opp,
)


SCENARIO_KIND_COUNT = 13


def _day_now(day: int, hour: int = 12) -> str:
    base = parse_rfc3339("2026-03-02T00:00:00+09:00")
    return format_rfc3339(base + timedelta(days=day, hours=hour))


def _local_date(ts: str) -> str:
    return parse_rfc3339(ts).date().isoformat()


def _monday_on_or_before(ts: str) -> str:
    d = parse_rfc3339(ts).date()
    return (d - timedelta(days=d.weekday())).isoformat()


@dataclass(frozen=True)
class DaySnapshotPlan:
    snapshot: DecisionSnapshot
    scenario_kind: int
    expect_hard_conflict: bool = False


def _build_day_snapshot(day: int, *, world_seed: str = WORLD_SEED) -> DaySnapshotPlan:
    """Explicit synthetic decision snapshot for one local date (contract §11)."""
    now = _day_now(day, hour=12 + (day % 5))
    local = _local_date(now)
    week_start = _monday_on_or_before(now)

    commitments: list = []
    tasks: list = []
    routes: list = []
    households: list = []
    kick_sessions: list = []
    social_gen = None
    social_resp = None
    free_key = None
    free_min = None
    leisure: tuple[str, ...] = ()
    location = HOME
    window = 90 + (day % 4) * 30
    hunger = 150 + (day % 9) * 80
    fatigue = 200 + (day % 6) * 100
    battery = 700 - (day % 8) * 70
    sleep_p = 150 + (day % 7) * 80
    active = None
    boundary = resolver_boundary(decision_key=f"idle:day-{day}")
    expect_hard_conflict = False

    kind = day % SCENARIO_KIND_COUNT

    if kind == 0:
        # HARD commitment + travel
        location = HOME
        created = format_rfc3339(parse_rfc3339(now) - timedelta(hours=6))
        commitments.append(
            exact_commitment(
                commitment_id=f"c-hard-{day}",
                created_at=created,
                timing={
                    "timing_kind": "EXACT",
                    "planned_start": format_rfc3339(
                        parse_rfc3339(now) + timedelta(hours=2)
                    ),
                    "planned_end": format_rfc3339(
                        parse_rfc3339(now) + timedelta(hours=5)
                    ),
                },
                location_id=CAMPUS,
            )
        )
        routes.append(route())
    elif kind == 1:
        # Work / deadline task
        location = DESK
        tasks.append(
            obligation(
                task_id=f"task-{day}",
                created_at=format_rfc3339(parse_rfc3339(now) - timedelta(hours=3)),
                priority="URGENT" if day % 2 == 0 else "NORMAL",
                location_constraints=[DESK],
                due_at=format_rfc3339(parse_rfc3339(now) + timedelta(hours=6)),
                effort_remaining_min=60,
            )
        )
    elif kind == 2:
        # Open / soft social window
        created = format_rfc3339(parse_rfc3339(now) - timedelta(hours=4))
        commitments.append(
            window_commitment(
                commitment_id=f"c-soft-{day}",
                created_at=created,
                timing={
                    "timing_kind": "WINDOW",
                    "earliest_start": format_rfc3339(
                        parse_rfc3339(now) - timedelta(minutes=30)
                    ),
                    "latest_start": format_rfc3339(
                        parse_rfc3339(now) + timedelta(hours=2)
                    ),
                    "duration_min": 30,
                    "duration_max": 90,
                },
                location_id=None,
            )
        )
    elif kind == 3:
        # Household
        households.append(
            household(
                household_task_id=f"hh-{day}",
                created_at=format_rfc3339(parse_rfc3339(now) - timedelta(days=2)),
            )
        )
    elif kind == 4:
        # Kickboxing opportunity (empty history)
        kick_sessions = []
    elif kind == 5:
        # Real Slice-2G-driven social generation (no exogenous injection).
        contact_end = format_rfc3339(parse_rfc3339(now) - timedelta(days=5))
        social_gen = SocialGenerationSpec(
            week_start_date=week_start,
            as_of=now,
            assignments=tuple(default_assignments()),
            contacts=(
                contact_history(
                    contact_id=f"hist-{day}",
                    person_id=MIZUKI,
                    actual_end=contact_end,
                    finalized_at=format_rfc3339(
                        parse_rfc3339(contact_end) + timedelta(minutes=5)
                    ),
                ),
            ),
            post_work_contexts=(
                post_work_context(
                    work_context_id=f"pwctx-{day}",
                    actual_end=format_rfc3339(
                        parse_rfc3339(now) - timedelta(days=2, hours=6)
                    ),
                    finalized_at=format_rfc3339(
                        parse_rfc3339(now) - timedelta(days=2, hours=6)
                        + timedelta(minutes=5)
                    ),
                    participant_person_ids=[RENA],
                ),
            ),
        )
        social_resp = SocialResponseSpec(hard_commitment_blocking_now=False)
    elif kind == 6:
        # Capacity-blocked contact via 2G + leisure open
        contact_end = format_rfc3339(parse_rfc3339(now) - timedelta(days=4))
        social_gen = SocialGenerationSpec(
            week_start_date=week_start,
            as_of=now,
            assignments=tuple(default_assignments()),
            contacts=(
                contact_history(
                    contact_id=f"hist-cap-{day}",
                    person_id=MIZUKI,
                    actual_end=contact_end,
                    finalized_at=format_rfc3339(
                        parse_rfc3339(contact_end) + timedelta(minutes=5)
                    ),
                ),
            ),
        )
        social_resp = SocialResponseSpec()
        battery = 40
        free_key = f"fw-{day}"
        free_min = 60
        leisure = ("MUSIC",)
    elif kind == 7:
        # Empty-ish conflict day: soft social + household + leisure
        created = format_rfc3339(parse_rfc3339(now) - timedelta(hours=4))
        commitments.append(
            window_commitment(
                commitment_id=f"c-mix-{day}",
                created_at=created,
                timing={
                    "timing_kind": "WINDOW",
                    "earliest_start": format_rfc3339(
                        parse_rfc3339(now) - timedelta(minutes=15)
                    ),
                    "latest_start": format_rfc3339(
                        parse_rfc3339(now) + timedelta(hours=1)
                    ),
                    "duration_min": 30,
                    "duration_max": 60,
                },
            )
        )
        households.append(
            household(
                household_task_id=f"hh-mix-{day}",
                created_at=format_rfc3339(parse_rfc3339(now) - timedelta(days=1)),
            )
        )
        free_key = f"fw-mix-{day}"
        free_min = 90
        leisure = ("MUSIC", "PASSIVE_HOME_MEDIA")
    elif kind == 8:
        # Future invite via direct exogenous injection (2H isolation intentional).
        oid = f"inv-fut-{day}"
        target = (parse_rfc3339(now).date() + timedelta(days=2)).isoformat()
        social_gen = SocialGenerationSpec(
            week_start_date=week_start,
            as_of=now,
            exogenous_opportunities=(
                local_invite(
                    oid,
                    timing="FUTURE_SOFT",
                    available_local_date=local,
                    target_local_date=target,
                ),
            ),
        )
        social_resp = SocialResponseSpec(
            availability_windows=(
                availability(
                    availability_id=f"a-fut-{day}",
                    opportunity_id=oid,
                    earliest_start=f"{target}T18:00:00+09:00",
                    latest_end=f"{target}T21:00:00+09:00",
                ),
            )
        )
        free_key = f"fw-fut-{day}"
        free_min = 60
        leisure = ("MUSIC",)
    elif kind == 9:
        # Post-work + deadline: keep controlled exogenous for deadline conflict;
        # also include a parallel 2G-driven day via kind 5.
        location = DESK
        tasks.append(
            obligation(
                task_id=f"task-pw-{day}",
                created_at=format_rfc3339(parse_rfc3339(now) - timedelta(hours=2)),
                priority="URGENT",
                location_constraints=[DESK],
                due_at=format_rfc3339(parse_rfc3339(now) + timedelta(hours=4)),
            )
        )
        social_gen = SocialGenerationSpec(
            week_start_date=week_start,
            as_of=now,
            exogenous_opportunities=(
                post_work_opp(
                    f"pw-{day}",
                    available_local_date=local,
                    target_local_date=local,
                ),
            ),
        )
        social_resp = SocialResponseSpec()
    elif kind == 10:
        # Active min-dwell (THRESHOLD anti-chatter continuation).
        households.append(
            household(
                household_task_id=f"hh-dwell-{day}",
                created_at=format_rfc3339(parse_rfc3339(now) - timedelta(days=1)),
            )
        )
        active = active_ctx(
            activity_instance_id=f"act-dwell-{day}",
            action_kind="STUDY",
            actual_start=format_rfc3339(parse_rfc3339(now) - timedelta(minutes=3)),
            interruptibility="REASSESSABLE",
            current_priority_tier="ROUTINE_HABIT",
            source_ref=f"task:dwell-{day}",
        )
        boundary = resolver_boundary(
            trigger_kind="THRESHOLD",
            decision_key=f"threshold:HUNGER:MEAL_NEEDED:UP:day-{day}",
            reason_code="THRESHOLD_HUNGER_MEAL_NEEDED_UP",
            metric="HUNGER",
            target_band="MEAL_NEEDED",
        )
        hunger = 200  # avoid urgent bio override of min-dwell
    elif kind == 11:
        # Explicit HARD commitment conflict (2E fail-closed).
        expect_hard_conflict = True
        created = format_rfc3339(parse_rfc3339(now) - timedelta(hours=6))
        commitments.append(
            exact_commitment(
                commitment_id=f"c-hc-a-{day}",
                created_at=created,
                timing={
                    "timing_kind": "EXACT",
                    "planned_start": format_rfc3339(
                        parse_rfc3339(now) - timedelta(hours=1)
                    ),
                    "planned_end": format_rfc3339(
                        parse_rfc3339(now) + timedelta(hours=3)
                    ),
                },
                location_id=CAMPUS,
            )
        )
        commitments.append(
            exact_commitment(
                commitment_id=f"c-hc-b-{day}",
                kind="APPOINTMENT",
                created_at=created,
                timing={
                    "timing_kind": "EXACT",
                    "planned_start": format_rfc3339(
                        parse_rfc3339(now) - timedelta(minutes=30)
                    ),
                    "planned_end": format_rfc3339(
                        parse_rfc3339(now) + timedelta(hours=1)
                    ),
                },
                location_id=CAMPUS,
            )
        )
        routes.append(route())
        location = CAMPUS
    else:
        # Open empty day with leisure only
        free_key = f"fw-open-{day}"
        free_min = 45 + (day % 3) * 15
        leisure = ("MUSIC",)

    # Occasional kickboxing history on household/kickboxing days
    if kind in {3, 4} and day % 3 == 0:
        kick_sessions.append(
            {
                "session_id": f"kb-{day}",
                "actual_start": format_rfc3339(
                    parse_rfc3339(now) - timedelta(days=1, hours=6)
                ),
                "actual_end": format_rfc3339(
                    parse_rfc3339(now) - timedelta(days=1, hours=5)
                ),
                "finalized_at": format_rfc3339(
                    parse_rfc3339(now) - timedelta(days=1, hours=5) + timedelta(minutes=5)
                ),
            }
        )

    snap = DecisionSnapshot(
        snapshot_id=f"day-{day:02d}",
        now=now,
        human_state=human_state(
            hunger=min(hunger, 1000),
            physical_fatigue=min(fatigue, 1000),
            social_battery=max(battery, 0),
        ),
        sleep_pressure=min(sleep_p, 1000),
        location_id=location,
        available_window_min=window,
        world_seed=world_seed,
        commitments=tuple(commitments),
        obligation_tasks=tuple(tasks),
        route_profiles=tuple(routes),
        household_tasks=tuple(households),
        kickboxing_sessions=tuple(kick_sessions),
        social_generation=social_gen,
        social_response=social_resp,
        free_window_key=free_key,
        free_window_min=free_min,
        feasible_leisure_categories=leisure,
        projection_horizon_end=format_rfc3339(
            parse_rfc3339(now) + timedelta(hours=24)
        ),
        resolver_boundary=boundary,
        active=active,
        meal_feasible=True,
        rest_feasible=True,
    )
    return DaySnapshotPlan(
        snapshot=snap,
        scenario_kind=kind,
        expect_hard_conflict=expect_hard_conflict,
    )
