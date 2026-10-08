"""Test-only synthetic script harness for (no ActualEvents / no I/O)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from engine.life.derived import (
    SyntheticSleepProfile,
    classify_hunger_band,
    classify_physical_fatigue_band,
    classify_sleep_pressure_band,
    classify_social_battery_band,
    classify_stress_band,
    compute_sleep_pressure_breakdown,
    default_synthetic_sleep_profile,
    meal_target_hunger,
    nap_awake_load_credit_min,
    settle_main_sleep_debt,
    settle_nap_sleep_debt,
)
from engine.life.dynamics import (
    apply_affect_impulse,
    apply_stress_impulse,
    integrate_v2_interval,
    resolve_v2_rates,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.fixed_point import (
    BOUNDS,
    HUMAN_STATE_FIELDS,
    empty_rate_remainders,
    saturation_seconds_in_segment,
    validate_human_state,
    validate_rate_remainders,
)

MINUTES_PER_DAY = 1440
SECONDS_PER_DAY = MINUTES_PER_DAY * 60


@dataclass(frozen=True)
class ScriptSegment:
    duration_seconds: int
    base_activity_class: str
    social_exposure: str = "NONE"


@dataclass(frozen=True)
class BoundaryOp:
    """Explicit boundary operation (never auto-inferred from state)."""

    kind: str
    extent: str | None = None
    impulse_name: str | None = None
    magnitude: str | None = None
    sign: str | None = None
    actual_duration_min: int | None = None
    nap_duration_min: int | None = None


@dataclass
class HarnessResult:
    scenario_name: str
    human_state: dict[str, int]
    rate_remainders: dict[str, int]
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    field_min: dict[str, int] = field(default_factory=dict)
    field_max: dict[str, int] = field(default_factory=dict)
    band_observations: set[str] = field(default_factory=set)
    # Decision-relevant sleep-pressure metrics (awake + post-MAIN/NAP-settlement wake).
    sleep_pressure_min: int | None = None
    sleep_pressure_max: int | None = None
    sleep_pressure_bands: set[str] = field(default_factory=set)
    # All math samples (including during MAIN/nap SLEEP) — not for decision calibration.
    sleep_pressure_math_min: int | None = None
    sleep_pressure_math_max: int | None = None
    sleep_pressure_math_bands: set[str] = field(default_factory=set)
    # Analytic saturation seconds (correct accounting) + continuous-rate bound hits
    # (hits from integrate_scaled_field rate motion reaching lo/hi — not impulse/meal clamps).
    saturation_lower_seconds: dict[str, int] = field(default_factory=dict)
    saturation_upper_seconds: dict[str, int] = field(default_factory=dict)
    continuous_rate_hit_lower: dict[str, int] = field(default_factory=dict)
    continuous_rate_hit_upper: dict[str, int] = field(default_factory=dict)
    total_nap_awake_credit_min: int = 0
    elapsed_since_main_end_seconds: int = 0
    local_clock_seconds: int = 0  # synthetic absolute local seconds from epoch day0 00:00

    @property
    def elapsed_since_main_end_min(self) -> int:
        """Minutes derived at sleep-pressure boundary (floor of exact seconds)."""
        return self.elapsed_since_main_end_seconds // 60

    @property
    def minute_local(self) -> int:
        return (self.local_clock_seconds // 60) % MINUTES_PER_DAY

    # Backward-compat aliases (deprecated names → continuous_rate_hit_*).
    @property
    def saturation_hit_lower(self) -> dict[str, int]:
        return self.continuous_rate_hit_lower

    @property
    def saturation_hit_upper(self) -> dict[str, int]:
        return self.continuous_rate_hit_upper


def _observe_bands(state: Mapping[str, int], policy: Mapping[str, Any], out: set[str]) -> None:
    out.add(f"hunger:{classify_hunger_band(state['hunger'], policy)}")
    out.add(f"fatigue:{classify_physical_fatigue_band(state['physical_fatigue'], policy)}")
    out.add(f"stress:{classify_stress_band(state['stress'], policy)}")
    out.add(f"social:{classify_social_battery_band(state['social_battery'], policy)}")


def _sat_fields() -> tuple[str, ...]:
    return ("hunger", "physical_fatigue", "stress", "social_battery")


def run_synthetic_script(
    *,
    scenario_name: str,
    policy: Mapping[str, Any],
    initial_state: Mapping[str, object],
    segments: list[ScriptSegment],
    boundary_ops_after_segment: dict[int, list[BoundaryOp]] | None = None,
    initial_remainders: Mapping[str, object] | None = None,
    sleep_profile: SyntheticSleepProfile | None = None,
    initial_elapsed_since_main_end_seconds: int = 0,
    initial_local_clock_seconds: int = 0,
    # Backward-compat alias (minutes → seconds).
    initial_elapsed_since_main_end_min: int | None = None,
    observe_minute_local: int | None = None,
) -> HarnessResult:
    """Run a scripted synthetic scenario (fixture/synthetic naming required)."""
    if "fixture" not in scenario_name.lower() and "synthetic" not in scenario_name.lower():
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "scenario_name must include 'fixture' or 'synthetic'",
        )
    profile = sleep_profile or default_synthetic_sleep_profile()
    state = validate_human_state(initial_state)
    rem = (
        empty_rate_remainders()
        if initial_remainders is None
        else validate_rate_remainders(initial_remainders)
    )
    ops_map = boundary_ops_after_segment or {}
    if initial_elapsed_since_main_end_min is not None:
        initial_elapsed_since_main_end_seconds = initial_elapsed_since_main_end_min * 60

    sat_keys = _sat_fields()
    result = HarnessResult(
        scenario_name=scenario_name,
        human_state=dict(state),
        rate_remainders=dict(rem),
        field_min={k: state[k] for k in HUMAN_STATE_FIELDS},
        field_max={k: state[k] for k in HUMAN_STATE_FIELDS},
        saturation_lower_seconds={k: 0 for k in sat_keys},
        saturation_upper_seconds={k: 0 for k in sat_keys},
        continuous_rate_hit_lower={k: 0 for k in sat_keys},
        continuous_rate_hit_upper={k: 0 for k in sat_keys},
        elapsed_since_main_end_seconds=initial_elapsed_since_main_end_seconds,
        local_clock_seconds=initial_local_clock_seconds,
    )
    _observe_bands(state, policy, result.band_observations)

    def _record_sp(bd, *, decision_relevant: bool) -> None:
        # Always record math samples.
        if result.sleep_pressure_math_min is None:
            result.sleep_pressure_math_min = bd.total
            result.sleep_pressure_math_max = bd.total
        else:
            result.sleep_pressure_math_min = min(result.sleep_pressure_math_min, bd.total)
            result.sleep_pressure_math_max = max(result.sleep_pressure_math_max, bd.total)
        result.sleep_pressure_math_bands.add(bd.band)
        if not decision_relevant:
            return
        # Decision calibration: awake samples + post-MAIN/NAP-settlement wake only.
        if result.sleep_pressure_min is None:
            result.sleep_pressure_min = bd.total
            result.sleep_pressure_max = bd.total
        else:
            result.sleep_pressure_min = min(result.sleep_pressure_min, bd.total)
            result.sleep_pressure_max = max(result.sleep_pressure_max, bd.total)
        result.sleep_pressure_bands.add(bd.band)
        result.band_observations.add(f"sleep_pressure:{bd.band}")

    def _observe_sleep_pressure(*, decision_relevant: bool) -> None:
        minute = (
            observe_minute_local
            if observe_minute_local is not None
            else result.minute_local
        )
        bd = compute_sleep_pressure_breakdown(
            policy=policy,
            profile=profile,
            sleep_debt_min=state["sleep_debt_min"],
            physical_fatigue=state["physical_fatigue"],
            minute_local=minute,
            elapsed_since_last_main_sleep_end_min=result.elapsed_since_main_end_min,
            total_nap_awake_credit_min=result.total_nap_awake_credit_min,
        )
        _record_sp(bd, decision_relevant=decision_relevant)

    def _track_extrema() -> None:
        for k in HUMAN_STATE_FIELDS:
            result.field_min[k] = min(result.field_min[k], state[k])
            result.field_max[k] = max(result.field_max[k], state[k])

    # Initial snapshot is math-only (not decision-relevant until awake/post-settle).
    _track_extrema()
    _observe_sleep_pressure(decision_relevant=False)

    for idx, seg in enumerate(segments):
        if seg.duration_seconds < 0:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "negative segment duration")
        ctx = {
            "base_activity_class": seg.base_activity_class,
            "social_exposure": seg.social_exposure,
        }
        rates = resolve_v2_rates(policy, ctx)
        # Analytic saturation for continuous rate fields (pre-integration state).
        for fk in sat_keys:
            lo, hi = BOUNDS[fk]
            sat = saturation_seconds_in_segment(
                field_value=state[fk],
                remainder=rem[fk],
                rate_per_hour=rates[fk],
                elapsed_seconds=seg.duration_seconds,
                lo=lo,
                hi=hi,
            )
            result.saturation_lower_seconds[fk] += sat["lower_seconds"]
            result.saturation_upper_seconds[fk] += sat["upper_seconds"]
            result.continuous_rate_hit_lower[fk] += sat["hit_lower"]
            result.continuous_rate_hit_upper[fk] += sat["hit_upper"]

        state, rem = integrate_v2_interval(state, rem, policy, ctx, seg.duration_seconds)
        # Wall elapsed since MAIN end includes ALL segments (including nap SLEEP).
        result.elapsed_since_main_end_seconds += seg.duration_seconds
        result.local_clock_seconds += seg.duration_seconds
        result.trajectory.append(
            {
                "segment_index": idx,
                "human_state": dict(state),
                "rate_remainders": dict(rem),
                "minute_local": result.minute_local,
                "elapsed_since_main_end_seconds": result.elapsed_since_main_end_seconds,
            }
        )
        _observe_bands(state, policy, result.band_observations)
        _track_extrema()
        # Decision-relevant only while awake (not during MAIN/nap SLEEP).
        _observe_sleep_pressure(decision_relevant=(seg.base_activity_class != "SLEEP"))

        for op in ops_map.get(idx, []):
            if op.kind == "meal":
                if op.extent is None:
                    raise LifeEngineError(ErrorCode.INVALID_STATE, "meal op requires extent")
                target = meal_target_hunger(op.extent, policy)
                state = dict(state)
                state["hunger"] = target
                rem = dict(rem)
                rem["hunger"] = 0
            elif op.kind == "stress_impulse":
                if op.impulse_name is None:
                    raise LifeEngineError(ErrorCode.INVALID_STATE, "stress_impulse requires name")
                state, rem = apply_stress_impulse(state, rem, policy, op.impulse_name)
            elif op.kind == "affect_impulse":
                if op.magnitude is None or op.sign is None:
                    raise LifeEngineError(ErrorCode.INVALID_STATE, "affect_impulse requires magnitude+sign")
                state, rem = apply_affect_impulse(
                    state, rem, policy, magnitude=op.magnitude, sign=op.sign
                )
            elif op.kind == "main_settle":
                if op.actual_duration_min is None:
                    raise LifeEngineError(ErrorCode.INVALID_STATE, "main_settle requires duration")
                state = dict(state)
                state["sleep_debt_min"] = settle_main_sleep_debt(
                    current_debt_min=state["sleep_debt_min"],
                    actual_main_sleep_duration_min=op.actual_duration_min,
                    sleep_need_min=profile.sleep_need_min,
                    policy=policy,
                )
                result.elapsed_since_main_end_seconds = 0
                result.total_nap_awake_credit_min = 0
            elif op.kind == "nap_settle":
                if op.nap_duration_min is None:
                    raise LifeEngineError(ErrorCode.INVALID_STATE, "nap_settle requires duration")
                state = dict(state)
                state["sleep_debt_min"] = settle_nap_sleep_debt(
                    current_debt_min=state["sleep_debt_min"],
                    nap_duration_min=op.nap_duration_min,
                    policy=policy,
                )
                result.total_nap_awake_credit_min += nap_awake_load_credit_min(
                    op.nap_duration_min, policy
                )
            else:
                raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown boundary op: {op.kind}")
            _observe_bands(state, policy, result.band_observations)
            _track_extrema()
            # Post-MAIN/NAP-settlement wake boundaries are decision-relevant after debt/credit
            # apply. Other ops follow the surrounding segment (awake vs sleep); exclude
            # active/pre-settlement sleep end-of-segment samples already handled above.
            _observe_sleep_pressure(
                decision_relevant=(op.kind in ("main_settle", "nap_settle"))
            )

    result.human_state = dict(state)
    result.rate_remainders = dict(rem)
    return result


def run_script_split(
    *,
    scenario_name: str,
    policy: Mapping[str, Any],
    initial_state: Mapping[str, object],
    segments: list[ScriptSegment],
    chunk_seconds: int,
    boundary_ops_after_segment: dict[int, list[BoundaryOp]] | None = None,
    **kwargs: Any,
) -> HarnessResult:
    """Same script with each segment subdivided into chunk_seconds pieces."""
    if chunk_seconds <= 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "chunk_seconds must be > 0")
    split_segments: list[ScriptSegment] = []
    split_ops: dict[int, list[BoundaryOp]] = {}
    ops_map = boundary_ops_after_segment or {}
    out_idx = 0
    for idx, seg in enumerate(segments):
        remaining = seg.duration_seconds
        while remaining > 0:
            step = chunk_seconds if remaining > chunk_seconds else remaining
            split_segments.append(
                ScriptSegment(step, seg.base_activity_class, seg.social_exposure)
            )
            remaining -= step
            out_idx += 1
        if idx in ops_map:
            split_ops[out_idx - 1] = list(ops_map[idx])
    return run_synthetic_script(
        scenario_name=scenario_name,
        policy=policy,
        initial_state=initial_state,
        segments=split_segments,
        boundary_ops_after_segment=split_ops,
        **kwargs,
    )


def leading_main_sleep_min(segs: list[ScriptSegment]) -> int:
    """Minutes of leading contiguous SLEEP at day start (pre-wake MAIN fragment)."""
    total = 0
    for seg in segs:
        if seg.base_activity_class != "SLEEP":
            break
        total += seg.duration_seconds // 60
    return total


def trailing_main_sleep_min(segs: list[ScriptSegment]) -> int:
    """Minutes of trailing contiguous SLEEP at day end (post-onset MAIN fragment).

    Stops before any non-SLEEP; naps in the middle of the day are not included
    because they are not contiguous with the trailing overnight block.
    """
    total = 0
    for seg in reversed(segs):
        if seg.base_activity_class != "SLEEP":
            break
        total += seg.duration_seconds // 60
    return total


def reconstruct_main_sleep_duration_min(
    *,
    prior_trailing_sleep_min: int,
    segs: list[ScriptSegment],
) -> int:
    """Reconstruct MAIN sleep = prior night trailing SLEEP + current leading SLEEP."""
    return prior_trailing_sleep_min + leading_main_sleep_min(segs)


def build_continuous_24h_day(
    *,
    day_index: int,
    sleep_need_min: int = 480,
) -> tuple[list[ScriptSegment], dict[int, list[BoundaryOp]]]:
    """Build an exact 24h synthetic day (seconds sum to 86400) with coherent MAIN sleep.

    Overnight MAIN is split across midnight for circadian realism:
      prior day 23:00–24:00 (60) + this day 00:00–07:00 (420) = **480** always.
    ``main_settle.actual_duration_min`` is always 480 and must equal the reconstructed
    MAIN duration (prior trailing + leading). Short/recovery MAIN variants stay in S5/S6.
    """
    social_eve = "ACTIVE" if day_index % 7 in (4, 5) else "PASSIVE"
    work = "LIGHT_ACTIVE" if day_index % 7 == 2 else "SEDENTARY_FOCUSED"
    phys = day_index % 7 == 3
    # Fixed coherent MAIN for 28-day calibration (not varied per day).
    main_dur = sleep_need_min  # 480
    pre_wake_sleep_min = 7 * 60  # 00:00–07:00
    post_onset_sleep_min = MINUTES_PER_DAY - 23 * 60  # 23:00–24:00 = 60
    if pre_wake_sleep_min + post_onset_sleep_min != main_dur:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"MAIN fragments {pre_wake_sleep_min}+{post_onset_sleep_min} != {main_dur}",
        )
    awake_span_min = MINUTES_PER_DAY - pre_wake_sleep_min - post_onset_sleep_min  # 960

    segs: list[ScriptSegment] = []
    segs.append(ScriptSegment(pre_wake_sleep_min * 60, "SLEEP", "NONE"))

    morning_rest = 45
    commute1 = 40
    work_block = 240 if not phys else 210
    commute2 = 50
    afternoon = 180
    phys_or_rest = 90 if phys else 30
    do_nap = day_index % 10 == 0
    nap_min = 30 if do_nap else 0
    evening = awake_span_min - (
        morning_rest + commute1 + work_block + commute2 + afternoon + phys_or_rest
    )
    if evening < 30:
        afternoon += evening - 60
        evening = 60

    blocks: list[tuple[int, str, str]] = [
        (morning_rest, "REST_AWAKE", "NONE"),
        (commute1, "LIGHT_ACTIVE", "NONE"),
        (work_block, work, "NONE" if work == "SEDENTARY_FOCUSED" else social_eve),
        (commute2, "LIGHT_ACTIVE", "NONE"),
    ]
    if do_nap:
        pre = afternoon // 2
        post = afternoon - pre - nap_min
        if post < 0:
            pre = afternoon - nap_min
            post = 0
        blocks.append((pre, "SEDENTARY_FOCUSED", "PASSIVE"))
        blocks.append((nap_min, "SLEEP", "NONE"))
        blocks.append((post, "SEDENTARY_FOCUSED", "PASSIVE"))
    else:
        blocks.append((afternoon, "SEDENTARY_FOCUSED", "PASSIVE"))
    blocks.append((phys_or_rest, "PHYSICALLY_ACTIVE" if phys else "REST_AWAKE", "NONE"))
    blocks.append((evening, "REST_AWAKE", social_eve))

    for dur, base, social in blocks:
        segs.append(ScriptSegment(dur * 60, base, social))
    segs.append(ScriptSegment(post_onset_sleep_min * 60, "SLEEP", "NONE"))

    total = sum(s.duration_seconds for s in segs)
    if total != SECONDS_PER_DAY:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"day {day_index} seconds={total} != {SECONDS_PER_DAY}",
        )

    ops: dict[int, list[BoundaryOp]] = {
        0: [BoundaryOp(kind="main_settle", actual_duration_min=main_dur)],
        1: [BoundaryOp(kind="meal", extent="STANDARD")],
        3: [BoundaryOp(kind="meal", extent="LIGHT")],
    }
    afternoon_meal_idx = 7 if do_nap else 5
    ops[afternoon_meal_idx] = [BoundaryOp(kind="meal", extent="STANDARD")]
    if do_nap:
        nap_idx = 6
        ops[nap_idx] = [BoundaryOp(kind="nap_settle", nap_duration_min=30)]
    phys_idx = afternoon_meal_idx + 1
    eve_idx = phys_idx + 1
    if phys:
        ops[phys_idx] = [BoundaryOp(kind="stress_impulse", impulse_name="RELIEF_MODERATE")]
    if day_index % 9 == 0:
        ops.setdefault(eve_idx, []).append(
            BoundaryOp(kind="affect_impulse", magnitude="SMALL", sign="POSITIVE")
        )
    return segs, ops


PRIOR_NIGHT_TRAILING_SLEEP_MIN = 60  # synthetic day−1 23:00–24:00 fragment
