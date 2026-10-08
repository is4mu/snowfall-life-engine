"""Life Engine v2 Slice 2B — pure state dynamics (math layer only).

No action selection, no resolvers, no clock wiring, no I/O.
Requires Behavior Policy v2 PRODUCTION *format* validated via Slice 2A
normalize_policy; does not require approval.
"""

from __future__ import annotations

from typing import Any, Mapping

from .errors import ErrorCode, LifeEngineError
from .fixed_point import (
    BOUNDS,
    SECONDS_PER_HOUR,
    clamp_scaled,
    from_scaled,
    integrate_scaled_field,
    to_scaled,
    validate_human_state,
    validate_rate_remainders,
)
from .policy import normalize_policy

BASE_ACTIVITY_CLASSES = frozenset(
    {
        "SLEEP",
        "REST_AWAKE",
        "SEDENTARY_FOCUSED",
        "LIGHT_ACTIVE",
        "PHYSICALLY_ACTIVE",
    }
)

# Caller-facing social exposure tokens (SLEEP rate is internal-only when base=SLEEP).
SOCIAL_EXPOSURE_CLASSES = frozenset({"NONE", "PASSIVE", "ACTIVE"})

STRESS_IMPULSE_NAMES = frozenset(
    {"MINOR", "MODERATE", "MAJOR", "RELIEF_MINOR", "RELIEF_MODERATE"}
)

AFFECT_IMPULSE_MAGNITUDES = frozenset({"SMALL", "MEDIUM", "LARGE"})
AFFECT_IMPULSE_SIGNS = frozenset({"POSITIVE", "NEGATIVE"})


def require_v2_production_format(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Validate v2 PRODUCTION-format policy via Slice 2A normalize; no approval.

    Rejects Foundation v1 / TEST_FIXTURE / malformed / float / invariant failures.
    """
    if not isinstance(policy, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "policy must be a mapping")
    # normalize_policy: schema + float reject + v2 invariants (fail closed).
    normalized = normalize_policy(dict(policy))
    schema_version = normalized.get("schema_version")
    policy_mode = normalized.get("policy_mode")
    if schema_version != 2 or policy_mode != "PRODUCTION":
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"Slice 2B math requires schema_version=2 and policy_mode=PRODUCTION; "
            f"got schema_version={schema_version!r} policy_mode={policy_mode!r}",
        )
    return normalized


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label} must be int (no bool/float)")
    return value


def validate_activity_context(context: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(context, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "context must be a mapping")
    base = context.get("base_activity_class")
    social = context.get("social_exposure")
    if base not in BASE_ACTIVITY_CLASSES:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid base_activity_class: {base!r}")
    # Caller must always supply NONE|PASSIVE|ACTIVE (including when base=SLEEP).
    # The internal SLEEP social *rate* is selected from the policy table when base=SLEEP;
    # the caller token "SLEEP" is never accepted.
    if social is None or ("social_exposure" not in context):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "social_exposure required (NONE|PASSIVE|ACTIVE); omit/None fails closed",
        )
    if social not in SOCIAL_EXPOSURE_CLASSES:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid social_exposure: {social!r}")
    return {"base_activity_class": str(base), "social_exposure": str(social)}


def resolve_v2_rates(
    policy: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, int]:
    """Resolve per-hour rates for RATE_REMAINDER_FIELDS from policy + context.

    ``affect_valence`` rate is 0 here; affect uses piecewise return-to-zero separately.
    When base=SLEEP, social rate uses internal SLEEP table regardless of caller token.
    """
    pol = require_v2_production_format(policy)
    ctx = validate_activity_context(context)
    dynamics = pol["state_dynamics"]
    base = ctx["base_activity_class"]
    base_rates = dynamics["base_activity_rates"][base]
    if base == "SLEEP":
        social_rate = int(dynamics["social_exposure_rates"]["SLEEP"])
    else:
        social_rate = int(dynamics["social_exposure_rates"][ctx["social_exposure"]])
    return {
        "hunger": int(base_rates["hunger_per_hour"]),
        "physical_fatigue": int(base_rates["physical_fatigue_per_hour"]),
        "affect_valence": 0,
        "stress": int(base_rates["stress_per_hour"]),
        "social_battery": social_rate,
    }


def _affect_rate_toward_zero(abs_field: int, bands: list[Mapping[str, Any]]) -> int:
    """Return rate_toward_zero_per_hour for the band containing abs_field."""
    if abs_field < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "abs_field must be >= 0")
    for band in bands:
        lo = int(band["abs_valence_lo"])
        hi = int(band["abs_valence_hi"])
        if lo <= abs_field <= hi:
            return int(band["rate_toward_zero_per_hour"])
    if abs_field == 0:
        return 0
    raise LifeEngineError(ErrorCode.INVALID_STATE, f"abs_valence {abs_field} outside affect bands")


def _affect_abs_thresholds_scaled(bands: list[Mapping[str, Any]]) -> list[int]:
    """Descending abs scaled thresholds (band lo values + 0)."""
    los = sorted({int(b["abs_valence_lo"]) for b in bands})
    th = [lo * SECONDS_PER_HOUR for lo in los if lo > 0]
    th.append(0)
    return sorted(set(th), reverse=True)


def integrate_affect_return_to_neutral(
    *,
    valence: int,
    remainder: int,
    bands: list[Mapping[str, Any]],
    elapsed_seconds: int,
) -> tuple[int, int]:
    """Piecewise return-to-zero using binding signed scaled = field*3600 + rem.

    Band rate uses ``abs(scaled) // 3600`` (not ``abs(field)``), so positive/negative
    mirrored scaled latents stay symmetric across thresholds. Never crosses zero.
    Zero only when scaled latent is zero (valence==0 with nonzero rem continues).
    O(band count) chunks; split-invariant.
    """
    valence = _require_int(valence, "affect_valence")
    remainder = _require_int(remainder, "affect remainder")
    elapsed_seconds = _require_int(elapsed_seconds, "elapsed_seconds")
    if elapsed_seconds < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative elapsed_seconds")
    if remainder < 0 or remainder >= SECONDS_PER_HOUR:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"bad affect remainder {remainder}")
    lo_b, hi_b = BOUNDS["affect_valence"]
    if valence < lo_b or valence > hi_b:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"affect_valence out of bounds: {valence}")

    scaled = to_scaled(valence, remainder)
    if scaled == 0:
        return 0, 0

    sign = 1 if scaled > 0 else -1
    abs_scaled = abs(scaled)
    remaining = elapsed_seconds
    abs_thresholds = _affect_abs_thresholds_scaled(bands)

    for _ in range(len(abs_thresholds) + 2):
        if remaining == 0 or abs_scaled <= 0:
            break
        band_key = abs_scaled // SECONDS_PER_HOUR
        rate = _affect_rate_toward_zero(band_key, bands)
        if rate <= 0:
            break
        # Inclusive threshold: when abs_scaled sits exactly on a band lo, distance is 0
        # so the next forced 1s tick leaves the higher band (partition-invariant vs 1+rest).
        # Strict `<` would skip the current lo and keep the higher rate past the boundary.
        lower_candidates = [t for t in abs_thresholds if t <= abs_scaled]
        next_th = max(lower_candidates) if lower_candidates else 0
        distance = abs_scaled - next_th
        full, frac = divmod(distance, rate)
        t_band = full if frac == 0 else full + 1
        if t_band <= 0:
            t_band = 1
        step = remaining if t_band > remaining else t_band
        abs_scaled -= rate * step
        if abs_scaled < 0:
            abs_scaled = 0
        remaining -= step

    if abs_scaled <= 0:
        return 0, 0
    signed = sign * abs_scaled
    signed = clamp_scaled(signed, lo_b, hi_b)
    return from_scaled(signed)


def integrate_v2_interval(
    human_state: Mapping[str, object],
    rate_remainders: Mapping[str, object] | None,
    policy: Mapping[str, Any],
    context: Mapping[str, Any],
    elapsed_seconds: int,
) -> tuple[dict[str, int], dict[str, int]]:
    """Integrate continuous v2 rates over an interval (pure; no I/O; no mutate).

    Updates hunger, physical_fatigue, affect_valence, stress, social_battery.
    Does **not** update sleep_debt_min.
    """
    pol = require_v2_production_format(policy)
    elapsed_seconds = _require_int(elapsed_seconds, "elapsed_seconds")
    if elapsed_seconds < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative elapsed_seconds")
    state = validate_human_state(human_state)
    rem = validate_rate_remainders(rate_remainders)
    rates = resolve_v2_rates(pol, context)

    new_state = dict(state)
    new_rem = dict(rem)

    for field in ("hunger", "physical_fatigue", "stress", "social_battery"):
        lo, hi = BOUNDS[field]
        fv, rv = integrate_scaled_field(
            field_value=new_state[field],
            remainder=new_rem[field],
            rate_per_hour=rates[field],
            elapsed_seconds=elapsed_seconds,
            lo=lo,
            hi=hi,
        )
        new_state[field] = fv
        new_rem[field] = rv

    bands = pol["state_dynamics"]["affect_return_to_baseline"]["bands"]
    av, ar = integrate_affect_return_to_neutral(
        valence=new_state["affect_valence"],
        remainder=new_rem["affect_valence"],
        bands=bands,
        elapsed_seconds=elapsed_seconds,
    )
    new_state["affect_valence"] = av
    new_rem["affect_valence"] = ar

    validate_human_state(new_state)
    validate_rate_remainders(new_rem)
    return new_state, new_rem


def _apply_field_impulse_scaled(
    *,
    field_value: int,
    remainder: int,
    delta_units: int,
    lo: int,
    hi: int,
) -> tuple[int, int]:
    """Add integer field units on scaled latent; saturate ⇒ rem 0 at bound."""
    field_value = _require_int(field_value, "field")
    remainder = _require_int(remainder, "remainder")
    delta_units = _require_int(delta_units, "delta")
    if remainder < 0 or remainder >= SECONDS_PER_HOUR:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"bad remainder {remainder}")
    scaled = to_scaled(field_value, remainder)
    scaled += delta_units * SECONDS_PER_HOUR
    scaled = clamp_scaled(scaled, lo, hi)
    return from_scaled(scaled)


def apply_stress_impulse(
    human_state: Mapping[str, object],
    rate_remainders: Mapping[str, object] | None,
    policy: Mapping[str, Any],
    impulse_name: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """Apply a named stress impulse on field+remainder (pure). No free-numeric API."""
    pol = require_v2_production_format(policy)
    if impulse_name not in STRESS_IMPULSE_NAMES:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown stress impulse: {impulse_name!r}")
    delta = int(pol["state_dynamics"]["stress_impulses"][impulse_name])
    state = validate_human_state(human_state)
    rem = validate_rate_remainders(rate_remainders)
    lo, hi = BOUNDS["stress"]
    out = dict(state)
    out_rem = dict(rem)
    out["stress"], out_rem["stress"] = _apply_field_impulse_scaled(
        field_value=out["stress"],
        remainder=out_rem["stress"],
        delta_units=delta,
        lo=lo,
        hi=hi,
    )
    return out, out_rem


def apply_affect_impulse(
    human_state: Mapping[str, object],
    rate_remainders: Mapping[str, object] | None,
    policy: Mapping[str, Any],
    *,
    magnitude: str,
    sign: str,
) -> tuple[dict[str, int], dict[str, int]]:
    """Apply a named affect impulse on field+remainder (pure). No free-numeric API."""
    pol = require_v2_production_format(policy)
    if magnitude not in AFFECT_IMPULSE_MAGNITUDES:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown affect magnitude: {magnitude!r}")
    if sign not in AFFECT_IMPULSE_SIGNS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown affect sign: {sign!r}")
    amount = int(pol["state_dynamics"]["affect_impulses"][magnitude])
    delta = amount if sign == "POSITIVE" else -amount
    state = validate_human_state(human_state)
    rem = validate_rate_remainders(rate_remainders)
    lo, hi = BOUNDS["affect_valence"]
    out = dict(state)
    out_rem = dict(rem)
    out["affect_valence"], out_rem["affect_valence"] = _apply_field_impulse_scaled(
        field_value=out["affect_valence"],
        remainder=out_rem["affect_valence"],
        delta_units=delta,
        lo=lo,
        hi=hi,
    )
    return out, out_rem
