"""Life Engine v2 Slice 2B — derived pressures & classifiers (math layer only).

Derived-only: never persisted into Human State / current_state.
"""

from __future__ import annotations

from typing import Any, Mapping, NamedTuple, Sequence

from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .fixed_point import BOUNDS, SECONDS_PER_HOUR, clamp_int, to_scaled

MINUTES_PER_DAY = 1440
SLEEP_NEED_MIN_LO = 420
SLEEP_NEED_MIN_HI = 540

HUNGER_BAND_ORDER = ("LOW", "NOTICEABLE", "MEAL_NEEDED", "URGENT")
FATIGUE_BAND_ORDER = ("LOW", "MODERATE", "HIGH", "VERY_HIGH")
STRESS_BAND_ORDER = ("LOW", "MODERATE", "HIGH", "VERY_HIGH")
SOCIAL_BAND_ORDER = ("LOW", "MODERATE", "HIGH")
SLEEP_PRESSURE_BAND_ORDER = ("LOW", "MODERATE", "HIGH", "CRITICAL")


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label} must be int (no bool/float)")
    return value


def piecewise_linear_interpolate(
    x: int,
    knots: Sequence[Mapping[str, Any]] | Sequence[tuple[int, int]],
) -> int:
    """Integer piecewise-linear interpolation.

    Rounding rule: truncating division toward −∞ (Python ``//``) on
    ``y0 + (y1 - y0) * (x - x0) // (x1 - x0)``.

    Below first knot → first y; above last knot → last y.
    Knot x values must be strictly increasing.
    """
    x = _require_int(x, "x")
    parsed: list[tuple[int, int]] = []
    for knot in knots:
        if isinstance(knot, Mapping):
            kx = _require_int(knot["x"], "knot.x")
            ky = _require_int(knot["y"], "knot.y")
        else:
            kx = _require_int(knot[0], "knot.x")
            ky = _require_int(knot[1], "knot.y")
        parsed.append((kx, ky))
    if len(parsed) < 1:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "knots must be non-empty")
    for i in range(1, len(parsed)):
        if parsed[i][0] <= parsed[i - 1][0]:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "knot x must be strictly increasing")
    if x <= parsed[0][0]:
        return parsed[0][1]
    if x >= parsed[-1][0]:
        return parsed[-1][1]
    for i in range(len(parsed) - 1):
        x0, y0 = parsed[i]
        x1, y1 = parsed[i + 1]
        if x0 <= x <= x1:
            return y0 + (y1 - y0) * (x - x0) // (x1 - x0)
    raise LifeEngineError(ErrorCode.INVALID_STATE, "piecewise interpolation failed")


def _classify_ascending(value: int, thresholds: Mapping[str, int], order: Sequence[str]) -> str:
    """Highest label whose threshold is <= value; labels before first threshold are LOW."""
    value = _require_int(value, "band value")
    current = order[0]
    for label in order[1:]:
        thr = _require_int(thresholds[label], f"threshold.{label}")
        if value >= thr:
            current = label
        else:
            break
    return current


def classify_hunger_band(value: int, policy: Mapping[str, Any]) -> str:
    pol = require_v2_production_format(policy)
    return _classify_ascending(value, pol["derived_bands"]["hunger"], HUNGER_BAND_ORDER)


def classify_physical_fatigue_band(value: int, policy: Mapping[str, Any]) -> str:
    pol = require_v2_production_format(policy)
    return _classify_ascending(
        value, pol["derived_bands"]["physical_fatigue"], FATIGUE_BAND_ORDER
    )


def classify_stress_band(value: int, policy: Mapping[str, Any]) -> str:
    pol = require_v2_production_format(policy)
    return _classify_ascending(value, pol["derived_bands"]["stress"], STRESS_BAND_ORDER)


def classify_social_battery_band(value: int, policy: Mapping[str, Any]) -> str:
    pol = require_v2_production_format(policy)
    return _classify_ascending(value, pol["derived_bands"]["social_battery"], SOCIAL_BAND_ORDER)


def classify_sleep_pressure_band(value: int, policy: Mapping[str, Any]) -> str:
    pol = require_v2_production_format(policy)
    return _classify_ascending(
        value, pol["derived_bands"]["sleep_pressure"], SLEEP_PRESSURE_BAND_ORDER
    )


class SyntheticSleepProfile(NamedTuple):
    sleep_need_min: int
    preferred_sleep_window_start_minute_local: int
    preferred_sleep_window_end_minute_local: int
    preferred_wake_window_start_minute_local: int
    preferred_wake_window_end_minute_local: int


def validate_synthetic_sleep_profile(profile: Mapping[str, Any] | SyntheticSleepProfile) -> SyntheticSleepProfile:
    if isinstance(profile, SyntheticSleepProfile):
        data = profile._asdict()
    else:
        data = dict(profile)
    required = (
        "sleep_need_min",
        "preferred_sleep_window_start_minute_local",
        "preferred_sleep_window_end_minute_local",
        "preferred_wake_window_start_minute_local",
        "preferred_wake_window_end_minute_local",
    )
    for key in required:
        if key not in data:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"missing sleep profile field: {key}")
    need = _require_int(data["sleep_need_min"], "sleep_need_min")
    if need < SLEEP_NEED_MIN_LO or need > SLEEP_NEED_MIN_HI:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"sleep_need_min out of range: {need}")
    minutes = []
    for key in required[1:]:
        m = _require_int(data[key], key)
        if m < 0 or m > 1439:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"{key} out of range: {m}")
        minutes.append(m)
    ss, se, ws, we = minutes
    if ws == we:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "wake window must be non-empty")
    if ss == se:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "sleep onset window must be non-empty")
    return SyntheticSleepProfile(need, ss, se, ws, we)


def default_synthetic_sleep_profile() -> SyntheticSleepProfile:
    """Test-only synthetic profile (NOT Yukino): onset 23:00–01:00, wake 07:00–09:00, need 480."""
    return SyntheticSleepProfile(
        sleep_need_min=480,
        preferred_sleep_window_start_minute_local=23 * 60,
        preferred_sleep_window_end_minute_local=1 * 60,
        preferred_wake_window_start_minute_local=7 * 60,
        preferred_wake_window_end_minute_local=9 * 60,
    )


def forward_minutes(start: int, end: int) -> int:
    """Forward circular distance in minutes from start to end on a 1440-minute day."""
    start = _require_int(start, "start")
    end = _require_int(end, "end")
    return (end - start) % MINUTES_PER_DAY


def circular_midpoint_forward(start: int, end: int) -> int:
    """Midpoint minute-of-day walking forward from start to end (truncating half)."""
    delta = forward_minutes(start, end)
    return (start + delta // 2) % MINUTES_PER_DAY


class CircadianAnchor(NamedTuple):
    unfolded_minute: int
    minute_local: int
    bias: int


def build_circadian_anchors(
    profile: Mapping[str, Any] | SyntheticSleepProfile,
    policy: Mapping[str, Any],
) -> list[CircadianAnchor]:
    """Build §21 circular piecewise anchors; fail closed on invalid overlap/order."""
    pol = require_v2_production_format(policy)
    prof = validate_synthetic_sleep_profile(profile)
    mech = pol["sleep_policy"]["circadian_bias_mechanics"]
    ramp = _require_int(mech["pre_sleep_ramp_min"], "pre_sleep_ramp_min")
    wake_bias = _require_int(mech["wake_window_bias"], "wake_window_bias")
    day_bias = _require_int(mech["daytime_floor_bias"], "daytime_floor_bias")
    pre_target = _require_int(mech["pre_sleep_target_bias"], "pre_sleep_target_bias")
    sleep_bias = _require_int(mech["sleep_window_bias"], "sleep_window_bias")
    late_bias = _require_int(mech["late_awake_cap_bias"], "late_awake_cap_bias")

    ws = prof.preferred_wake_window_start_minute_local
    we = prof.preferred_wake_window_end_minute_local
    ss = prof.preferred_sleep_window_start_minute_local
    se = prof.preferred_sleep_window_end_minute_local

    def unfold(m: int) -> int:
        return ws + forward_minutes(ws, m)

    day_floor_m = (we + 120) % MINUTES_PER_DAY
    pre_sleep_m = (ss - ramp) % MINUTES_PER_DAY
    late_m = circular_midpoint_forward(se, ws)

    specs: list[tuple[int, int]] = [
        (ws, wake_bias),
        (we, wake_bias),
        (day_floor_m, day_bias),
        (pre_sleep_m, 0),
        (ss, pre_target),
        (se, sleep_bias),
        (late_m, late_bias),
        (ws, wake_bias),  # next wake start (close circle)
    ]

    anchors: list[CircadianAnchor] = []
    for i, (minute_local, bias) in enumerate(specs):
        if i == 0:
            u = ws
        elif i == len(specs) - 1:
            u = ws + MINUTES_PER_DAY
        else:
            u = unfold(minute_local)
            # Sleep-end / late-awake after a wrapping sleep window must sit after sleep start.
            if i >= 5 and u <= anchors[4].unfolded_minute:
                u += MINUTES_PER_DAY
            if i >= 6 and u <= anchors[5].unfolded_minute:
                u += MINUTES_PER_DAY
        anchors.append(CircadianAnchor(u, minute_local % MINUTES_PER_DAY, bias))

    for i in range(1, len(anchors)):
        if anchors[i].unfolded_minute <= anchors[i - 1].unfolded_minute:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"circadian anchor order invalid at index {i}: "
                f"{anchors[i - 1]} -> {anchors[i]}",
            )
    return anchors


def circadian_bias_at_minute(
    minute_local: int,
    profile: Mapping[str, Any] | SyntheticSleepProfile,
    policy: Mapping[str, Any],
) -> int:
    """§21 circular piecewise-linear circadian bias at minute-of-day (0..1439)."""
    minute_local = _require_int(minute_local, "minute_local")
    if minute_local < 0 or minute_local > 1439:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"minute_local out of range: {minute_local}")
    anchors = build_circadian_anchors(profile, policy)
    ws = anchors[0].minute_local
    u = anchors[0].unfolded_minute + forward_minutes(ws, minute_local)
    for i in range(len(anchors) - 1):
        a0 = anchors[i]
        a1 = anchors[i + 1]
        if a0.unfolded_minute <= u <= a1.unfolded_minute:
            span = a1.unfolded_minute - a0.unfolded_minute
            if span == 0:
                return a0.bias
            return a0.bias + (a1.bias - a0.bias) * (u - a0.unfolded_minute) // span
    raise LifeEngineError(ErrorCode.INVALID_STATE, "circadian minute not covered by anchors")


def nap_awake_load_credit_min(duration_min: int, policy: Mapping[str, Any]) -> int:
    pol = require_v2_production_format(policy)
    duration_min = _require_int(duration_min, "duration_min")
    if duration_min < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative nap duration")
    nap = pol["sleep_policy"]["nap"]
    num = _require_int(nap["awake_load_credit"]["numerator"], "awake_load_credit.numerator")
    den = _require_int(nap["awake_load_credit"]["denominator"], "awake_load_credit.denominator")
    if den <= 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "awake_load_credit.denominator must be > 0")
    cap = _require_int(nap["awake_load_credit_cap_min"], "awake_load_credit_cap_min")
    credit = (duration_min * num) // den
    if credit > cap:
        return cap
    return credit


def effective_awake_load_min(
    *,
    elapsed_since_last_main_sleep_end_min: int,
    total_nap_awake_credit_min: int,
) -> int:
    elapsed = _require_int(elapsed_since_last_main_sleep_end_min, "elapsed_since_last_main_sleep_end_min")
    credit = _require_int(total_nap_awake_credit_min, "total_nap_awake_credit_min")
    if elapsed < 0 or credit < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "awake load inputs must be >= 0")
    return max(0, elapsed - credit)


class SleepPressureBreakdown(NamedTuple):
    effective_awake_load_min: int
    awake_fraction_permille: int
    homeostatic: int
    debt: int
    fatigue: int
    circadian: int
    total: int
    band: str


def compute_sleep_pressure_breakdown(
    *,
    policy: Mapping[str, Any],
    profile: Mapping[str, Any] | SyntheticSleepProfile,
    sleep_debt_min: int,
    physical_fatigue: int,
    minute_local: int,
    elapsed_since_last_main_sleep_end_min: int,
    total_nap_awake_credit_min: int = 0,
) -> SleepPressureBreakdown:
    """Pure sleep-pressure breakdown (DERIVED_ONLY — do not persist)."""
    pol = require_v2_production_format(policy)
    prof = validate_synthetic_sleep_profile(profile)
    sleep_debt_min = _require_int(sleep_debt_min, "sleep_debt_min")
    physical_fatigue = _require_int(physical_fatigue, "physical_fatigue")
    lo_d, hi_d = BOUNDS["sleep_debt_min"]
    lo_f, hi_f = BOUNDS["physical_fatigue"]
    if sleep_debt_min < lo_d or sleep_debt_min > hi_d:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"sleep_debt_min out of bounds: {sleep_debt_min}")
    if physical_fatigue < lo_f or physical_fatigue > hi_f:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"physical_fatigue out of bounds: {physical_fatigue}")

    eff = effective_awake_load_min(
        elapsed_since_last_main_sleep_end_min=elapsed_since_last_main_sleep_end_min,
        total_nap_awake_credit_min=total_nap_awake_credit_min,
    )
    nominal = MINUTES_PER_DAY - prof.sleep_need_min
    if nominal <= 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "nominal_awake_span_min must be > 0")
    awake_fraction_permille = (eff * 1000) // nominal

    sleep = pol["sleep_policy"]
    homeostatic = piecewise_linear_interpolate(
        awake_fraction_permille, sleep["homeostatic_awake_fraction_knots"]
    )
    debt = piecewise_linear_interpolate(sleep_debt_min, sleep["sleep_debt_knots"])
    fatigue = piecewise_linear_interpolate(
        physical_fatigue, sleep["fatigue_contribution_knots"]
    )
    circadian = circadian_bias_at_minute(minute_local, prof, pol)
    total = clamp_int(homeostatic + debt + fatigue + circadian, 0, 1000)
    band = classify_sleep_pressure_band(total, pol)
    return SleepPressureBreakdown(
        effective_awake_load_min=eff,
        awake_fraction_permille=awake_fraction_permille,
        homeostatic=homeostatic,
        debt=debt,
        fatigue=fatigue,
        circadian=circadian,
        total=total,
        band=band,
    )


def stress_sleep_onset_friction(stress: int, policy: Mapping[str, Any]) -> int:
    pol = require_v2_production_format(policy)
    stress = _require_int(stress, "stress")
    fr = pol["sleep_policy"]["stress_onset_friction"]
    high_thr = _require_int(fr["high_threshold"], "high_threshold")
    very_thr = _require_int(fr["very_high_threshold"], "very_high_threshold")
    high_pen = _require_int(fr["high_penalty"], "high_penalty")
    very_pen = _require_int(fr["very_high_penalty"], "very_high_penalty")
    if stress < high_thr:
        return 0
    if stress < very_thr:
        return high_pen
    return very_pen


def effective_sleep_start_score(sleep_pressure: int, stress: int, policy: Mapping[str, Any]) -> int:
    """max(0, sleep_pressure - friction); does not modify base sleep_pressure."""
    sleep_pressure = _require_int(sleep_pressure, "sleep_pressure")
    friction = stress_sleep_onset_friction(stress, policy)
    return max(0, sleep_pressure - friction)


def settle_main_sleep_debt(
    *,
    current_debt_min: int,
    actual_main_sleep_duration_min: int,
    sleep_need_min: int,
    policy: Mapping[str, Any],
) -> int:
    pol = require_v2_production_format(policy)
    debt = _require_int(current_debt_min, "current_debt_min")
    actual = _require_int(actual_main_sleep_duration_min, "actual_main_sleep_duration_min")
    need = _require_int(sleep_need_min, "sleep_need_min")
    if actual < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative MAIN sleep duration")
    if need < SLEEP_NEED_MIN_LO or need > SLEEP_NEED_MIN_HI:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"sleep_need_min out of range: {need}")
    lo, hi = BOUNDS["sleep_debt_min"]
    debt = clamp_int(debt, lo, hi)
    main = pol["sleep_policy"]["main_settlement"]
    if actual < need:
        deficit = need - actual
        num = _require_int(main["deficit_credit"]["numerator"], "deficit_credit.numerator")
        den = _require_int(main["deficit_credit"]["denominator"], "deficit_credit.denominator")
        if den <= 0:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "deficit_credit.denominator must be > 0")
        debt += (deficit * num) // den
    elif actual > need:
        surplus = actual - need
        num = _require_int(main["surplus_repayment"]["numerator"], "surplus_repayment.numerator")
        den = _require_int(main["surplus_repayment"]["denominator"], "surplus_repayment.denominator")
        if den <= 0:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "surplus_repayment.denominator must be > 0")
        repay = (surplus * num) // den
        cap = _require_int(main["max_surplus_repayment_min"], "max_surplus_repayment_min")
        if repay > cap:
            repay = cap
        debt -= repay
    return clamp_int(debt, lo, hi)


def settle_nap_sleep_debt(
    *,
    current_debt_min: int,
    nap_duration_min: int,
    policy: Mapping[str, Any],
) -> int:
    pol = require_v2_production_format(policy)
    debt = _require_int(current_debt_min, "current_debt_min")
    duration = _require_int(nap_duration_min, "nap_duration_min")
    if duration < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative nap duration")
    lo, hi = BOUNDS["sleep_debt_min"]
    debt = clamp_int(debt, lo, hi)
    nap = pol["sleep_policy"]["nap"]
    num = _require_int(nap["debt_credit"]["numerator"], "debt_credit.numerator")
    den = _require_int(nap["debt_credit"]["denominator"], "debt_credit.denominator")
    if den <= 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "debt_credit.denominator must be > 0")
    cap = _require_int(nap["debt_credit_cap_min"], "debt_credit_cap_min")
    repay = (duration * num) // den
    if repay > cap:
        repay = cap
    debt -= repay
    return clamp_int(debt, lo, hi)


def hunger_threshold_seconds(
    *,
    hunger: int,
    hunger_remainder: int,
    hunger_rate_per_hour: int,
    threshold_name: str,
    policy: Mapping[str, Any],
) -> int | None:
    """Seconds until hunger field reaches named derived band threshold (ETA only)."""
    pol = require_v2_production_format(policy)
    hunger = _require_int(hunger, "hunger")
    hunger_remainder = _require_int(hunger_remainder, "hunger_remainder")
    rate = _require_int(hunger_rate_per_hour, "hunger_rate_per_hour")
    if threshold_name not in pol["derived_bands"]["hunger"]:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown hunger threshold: {threshold_name!r}")
    threshold = _require_int(pol["derived_bands"]["hunger"][threshold_name], threshold_name)
    scaled = to_scaled(hunger, hunger_remainder)
    target = threshold * SECONDS_PER_HOUR
    if scaled >= target:
        return 0
    if rate <= 0:
        return None
    needed = target - scaled
    return (needed + rate - 1) // rate


def meal_target_hunger(extent: str, policy: Mapping[str, Any]) -> int:
    pol = require_v2_production_format(policy)
    targets = pol["meal_policy"]["post_meal_target"]
    if extent not in targets:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown meal extent: {extent!r}")
    return _require_int(targets[extent], f"post_meal_target.{extent}")
