"""Fixed-point human-state arithmetic (integers only).

Rate integration is partition-invariant via remainder accumulation:
  units, rem = divmod(rate * elapsed_seconds + rem, 3600)
Same total elapsed yields the same Human State regardless of heartbeat splits.

Mechanical remainders cover all continuously integrable non-sleep core fields:
hunger, physical_fatigue, affect_valence, stress, social_battery.
sleep_debt_min is intentionally excluded (sleep-history settlement, not a rate).

Signed rates are allowed (e.g. toward-neutral valence decay). Remainders stay
non-negative using the identity:
  rate*t + rem = units*3600 + new_rem,  0 <= new_rem < 3600
with truncating integer division toward -∞ for negative products via Python
divmod (which floors).
"""

from __future__ import annotations

from typing import Mapping

from .errors import ErrorCode, LifeEngineError

HUMAN_STATE_FIELDS = (
    "sleep_debt_min",
    "hunger",
    "physical_fatigue",
    "affect_valence",
    "stress",
    "social_battery",
)

# Continuously integrable fields (exclude sleep_debt_min).
RATE_REMAINDER_FIELDS = (
    "hunger",
    "physical_fatigue",
    "affect_valence",
    "stress",
    "social_battery",
)

BOUNDS: dict[str, tuple[int, int]] = {
    "sleep_debt_min": (0, 720),
    "hunger": (0, 1000),
    "physical_fatigue": (0, 1000),
    "affect_valence": (-1000, 1000),
    "stress": (0, 1000),
    "social_battery": (0, 1000),
}

SECONDS_PER_HOUR = 3600


def clamp_int(value: int, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"non-integer fixed-point value: {value!r}")
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def validate_human_state(state: Mapping[str, object]) -> dict[str, int]:
    if set(state.keys()) != set(HUMAN_STATE_FIELDS):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"human_state must have exactly {list(HUMAN_STATE_FIELDS)}; got {sorted(state)}",
        )
    out: dict[str, int] = {}
    for key in HUMAN_STATE_FIELDS:
        raw = state[key]
        if isinstance(raw, float) or isinstance(raw, bool) or not isinstance(raw, int):
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"binary float/non-int forbidden: {key}")
        lo, hi = BOUNDS[key]
        if raw < lo or raw > hi:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"{key} out of bounds: {raw}")
        out[key] = raw
    return out


def clamp_human_state(state: Mapping[str, object]) -> dict[str, int]:
    if set(state.keys()) - set(HUMAN_STATE_FIELDS):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"unknown human_state fields: {sorted(set(state) - set(HUMAN_STATE_FIELDS))}",
        )
    missing = set(HUMAN_STATE_FIELDS) - set(state.keys())
    if missing:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"missing human_state fields: {sorted(missing)}")
    out: dict[str, int] = {}
    for key in HUMAN_STATE_FIELDS:
        raw = state[key]
        if isinstance(raw, float) or isinstance(raw, bool) or not isinstance(raw, int):
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"binary float/non-int forbidden: {key}")
        lo, hi = BOUNDS[key]
        out[key] = clamp_int(raw, lo, hi)
    return out


def apply_delta(state: Mapping[str, object], delta: Mapping[str, object]) -> dict[str, int]:
    base = validate_human_state(state)
    for key, value in delta.items():
        if key not in HUMAN_STATE_FIELDS:
            raise LifeEngineError(ErrorCode.UNSUPPORTED_EFFECT, f"unknown human delta field: {key}")
        if isinstance(value, float) or isinstance(value, bool) or not isinstance(value, int):
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"delta must be int: {key}")
        lo, hi = BOUNDS[key]
        base[key] = clamp_int(base[key] + value, lo, hi)
    return base


def empty_rate_remainders() -> dict[str, int]:
    return {k: 0 for k in RATE_REMAINDER_FIELDS}


def validate_rate_remainders(remainders: Mapping[str, object] | None) -> dict[str, int]:
    if remainders is None:
        return empty_rate_remainders()
    if set(remainders.keys()) != set(RATE_REMAINDER_FIELDS):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"rate_remainders must have exactly {list(RATE_REMAINDER_FIELDS)}",
        )
    out: dict[str, int] = {}
    for key in RATE_REMAINDER_FIELDS:
        raw = remainders[key]
        if isinstance(raw, float) or isinstance(raw, bool) or not isinstance(raw, int):
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"remainder must be int: {key}")
        if raw < 0 or raw >= SECONDS_PER_HOUR:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"remainder out of range: {key}={raw}")
        out[key] = raw
    return out


def integrate_field(
    *,
    rate_per_hour: int,
    elapsed_seconds: int,
    remainder: int,
) -> tuple[int, int]:
    """Return (units_delta, new_remainder) for one field. Signed rates OK."""
    if isinstance(rate_per_hour, float) or isinstance(rate_per_hour, bool) or not isinstance(rate_per_hour, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "rate must be int")
    if elapsed_seconds < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative elapsed_seconds")
    if remainder < 0 or remainder >= SECONDS_PER_HOUR:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"bad remainder {remainder}")
    # For negative rates: accumulate as rate*t + rem with rem in [0, 3600).
    # Python divmod floors toward -∞, keeping remainder sign of divisor (positive).
    accum = rate_per_hour * elapsed_seconds + remainder
    units, leftover = divmod(accum, SECONDS_PER_HOUR)
    return units, leftover


def integrate_rates(
    state: Mapping[str, object],
    *,
    rates: Mapping[str, int],
    elapsed_seconds: int,
    remainders: Mapping[str, object] | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Partition-invariant interval integration for all RATE_REMAINDER_FIELDS.

    ``rates`` must provide an int per RATE_REMAINDER_FIELDS key (0 allowed).
    """
    if elapsed_seconds < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative elapsed_seconds")
    base = validate_human_state(state)
    rem = validate_rate_remainders(remainders)
    if set(rates.keys()) != set(RATE_REMAINDER_FIELDS):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"rates must have exactly {list(RATE_REMAINDER_FIELDS)}",
        )
    delta: dict[str, int] = {}
    new_rem: dict[str, int] = {}
    for field in RATE_REMAINDER_FIELDS:
        units, leftover = integrate_field(
            rate_per_hour=int(rates[field]),
            elapsed_seconds=elapsed_seconds,
            remainder=rem[field],
        )
        delta[field] = units
        new_rem[field] = leftover
    return apply_delta(base, delta), new_rem


def to_scaled(field_value: int, remainder: int) -> int:
    """Encode field + remainder as a single scaled integer (units of 1/3600 field)."""
    if isinstance(field_value, bool) or not isinstance(field_value, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"non-integer field: {field_value!r}")
    if isinstance(remainder, bool) or not isinstance(remainder, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"non-integer remainder: {remainder!r}")
    if remainder < 0 or remainder >= SECONDS_PER_HOUR:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"bad remainder {remainder}")
    return field_value * SECONDS_PER_HOUR + remainder


def from_scaled(scaled: int) -> tuple[int, int]:
    """Decode scaled integer via floor divmod (remainder in 0..3599)."""
    if isinstance(scaled, bool) or not isinstance(scaled, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"non-integer scaled: {scaled!r}")
    field, rem = divmod(scaled, SECONDS_PER_HOUR)
    return field, rem


def clamp_scaled(scaled: int, lo: int, hi: int) -> int:
    """Clamp an entire scaled value to field bounds (no hidden overshoot)."""
    if isinstance(scaled, bool) or not isinstance(scaled, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"non-integer scaled: {scaled!r}")
    if isinstance(lo, bool) or not isinstance(lo, int) or isinstance(hi, bool) or not isinstance(hi, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "non-integer scaled bounds")
    min_scaled = lo * SECONDS_PER_HOUR
    # hi field with rem=0 is the upper inclusive field; allow rem up to 3599 only below hi+1.
    # Bound the scaled value to [lo*3600, hi*3600] so saturation is exact at the field bound
    # with remainder 0 (no hidden fractional overshoot past hi).
    max_scaled = hi * SECONDS_PER_HOUR
    if scaled < min_scaled:
        return min_scaled
    if scaled > max_scaled:
        return max_scaled
    return scaled


def integrate_scaled_field(
    *,
    field_value: int,
    remainder: int,
    rate_per_hour: int,
    elapsed_seconds: int,
    lo: int,
    hi: int,
) -> tuple[int, int]:
    """V2 bounded fixed-point step: scale, add rate*t, clamp entire scaled, decode.

    Unlike :func:`integrate_field` + field-only clamp, remainder cannot hide overshoot
    past ``hi`` / below ``lo``. At exact saturation, remainder is 0.
    """
    if isinstance(rate_per_hour, bool) or not isinstance(rate_per_hour, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "rate must be int")
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "elapsed_seconds must be int")
    if elapsed_seconds < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative elapsed_seconds")
    scaled = to_scaled(field_value, remainder)
    scaled += rate_per_hour * elapsed_seconds
    scaled = clamp_scaled(scaled, lo, hi)
    return from_scaled(scaled)


def saturation_seconds_in_segment(
    *,
    field_value: int,
    remainder: int,
    rate_per_hour: int,
    elapsed_seconds: int,
    lo: int,
    hi: int,
) -> dict[str, int]:
    """Analytic lower/upper saturation duration within a constant-rate segment.

    No minute/second proportional loop: O(1) closed form using scaled arithmetic.
    Returns keys: lower_seconds, upper_seconds, hit_lower (0|1), hit_upper (0|1).

    ``hit_*`` counts continuous-rate bound arrivals within this segment (rate motion
    reaching lo/hi). Impulse/meal clamps are out of scope here — harness reports these
    as ``continuous_rate_hit_lower/upper``.
    Boundary clamps match :func:`integrate_scaled_field` / :func:`clamp_scaled`.
    """
    if isinstance(rate_per_hour, bool) or not isinstance(rate_per_hour, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "rate must be int")
    if isinstance(elapsed_seconds, bool) or not isinstance(elapsed_seconds, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "elapsed_seconds must be int")
    if elapsed_seconds < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative elapsed_seconds")
    lo_s = lo * SECONDS_PER_HOUR
    hi_s = hi * SECONDS_PER_HOUR
    start = to_scaled(field_value, remainder)
    start = clamp_scaled(start, lo, hi)
    rate = rate_per_hour
    t = elapsed_seconds
    lower_seconds = 0
    upper_seconds = 0
    hit_lower = 0
    hit_upper = 0

    if t == 0:
        return {
            "lower_seconds": 0,
            "upper_seconds": 0,
            "hit_lower": 0,
            "hit_upper": 0,
        }

    if rate == 0:
        if start <= lo_s:
            lower_seconds = t
        if start >= hi_s:
            upper_seconds = t
        return {
            "lower_seconds": lower_seconds,
            "upper_seconds": upper_seconds,
            "hit_lower": 0,
            "hit_upper": 0,
        }

    if rate > 0:
        if start >= hi_s:
            upper_seconds = t
        else:
            # seconds until scaled would reach hi_s (ceil)
            needed = hi_s - start
            t_hit = (needed + rate - 1) // rate
            if t_hit <= t:
                hit_upper = 1
                upper_seconds = t - t_hit
                # At exact hit instant we are at bound; count remaining as saturated.
        if start <= lo_s:
            # Leaving lower bound immediately when rate > 0 (unless already only at lo and...)
            pass
    else:
        # rate < 0
        if start <= lo_s:
            lower_seconds = t
        else:
            needed = start - lo_s
            t_hit = (needed + (-rate) - 1) // (-rate)
            if t_hit <= t:
                hit_lower = 1
                lower_seconds = t - t_hit
        if start >= hi_s:
            pass

    return {
        "lower_seconds": lower_seconds,
        "upper_seconds": upper_seconds,
        "hit_lower": hit_lower,
        "hit_upper": hit_upper,
    }
