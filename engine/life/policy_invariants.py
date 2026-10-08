"""Behavior Policy v2 invariant checks (post JSON Schema)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .errors import ErrorCode, LifeEngineError

# Behavior-changing top-level groups that provenance_index must cover.
REQUIRED_PROVENANCE_PATHS = frozenset(
    {
        "time_resolution",
        "engine_safety",
        "state_dynamics",
        "derived_bands",
        "sleep_policy",
        "meal_policy",
        "decision_thresholds",
        "planning_policy",
        "schedule_generation",
        "domain_policy",
        "social_policy",
        "behavior_modes",
        "capture_policy",
    }
)

CAPTURE_KIND_KEYS = (
    "SELFIE",
    "PEOPLE",
    "FOOD",
    "SCENERY",
    "OBJECT",
    "DAILY_LIFE",
)

# Clock / schedule fields that belong to bootstrap or later slices — never in policy.
FORBIDDEN_POLICY_CLOCK_FIELDS = frozenset(
    {
        "preferred_sleep_start",
        "preferred_sleep_end",
        "preferred_wake_time",
        "sleep_need_min",
        "wake_window_start",
        "wake_window_end",
        "sleep_window_start",
        "sleep_window_end",
        "meal_clock",
        "breakfast_time",
        "lunch_time",
        "dinner_time",
        "cook_probability",
        "skip_meal_probability",
        "shift_template",
        "ordinary_shift_start",
        "ordinary_shift_end",
        "quantum_min",
    }
)

PERSON_SPECIFIC_SOCIAL_FIELDS = frozenset(
    {
        "person_id",
        "person_ids",
        "friend_id",
        "character_person_id",
        "character_person_map",
    }
)

HOMEOSTATIC_Y_MAX = 1000
DEBT_FATIGUE_Y_MAX = 1000


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _assert_strict_increasing_x(knots: Sequence[Mapping[str, Any]], *, label: str) -> None:
    prev: int | None = None
    for i, knot in enumerate(knots):
        x = int(knot["x"])
        if prev is not None and x <= prev:
            _fail(f"{label}: knot x must be strictly increasing at index {i}")
        prev = x


def _assert_nondecreasing_y(knots: Sequence[Mapping[str, Any]], *, label: str) -> None:
    prev: int | None = None
    for i, knot in enumerate(knots):
        y = int(knot["y"])
        if prev is not None and y < prev:
            _fail(f"{label}: knot y must be monotonic nondecreasing at index {i}")
        prev = y


def _assert_min_le_max(lo: int, hi: int, *, label: str) -> None:
    if lo > hi:
        _fail(f"{label}: min={lo} > max={hi}")


def _assert_strict_monotonic_values(values: Sequence[int], *, label: str) -> None:
    prev: int | None = None
    for i, value in enumerate(values):
        if prev is not None and value <= prev:
            _fail(f"{label}: thresholds must be strictly monotonic at index {i}")
        prev = value


def _reject_forbidden_keys(obj: Any, *, path: str = "$") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in FORBIDDEN_POLICY_CLOCK_FIELDS:
                _fail(f"bootstrap-only/clock field forbidden in policy at {path}.{key}")
            _reject_forbidden_keys(value, path=f"{path}.{key}")
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _reject_forbidden_keys(value, path=f"{path}[{i}]")


def _reject_person_specific_social(social_policy: Mapping[str, Any]) -> None:
    def walk(obj: Any, *, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in PERSON_SPECIFIC_SOCIAL_FIELDS:
                    _fail(f"person-specific social field forbidden at {path}.{key}")
                walk(value, path=f"{path}.{key}")
        elif isinstance(obj, list):
            for i, value in enumerate(obj):
                walk(value, path=f"{path}[{i}]")

    walk(social_policy, path="social_policy")


def _validate_affect_bands(bands: Sequence[Mapping[str, Any]]) -> None:
    if not bands:
        _fail("affect_return_to_baseline.bands empty")
    prev_hi = -1
    for i, band in enumerate(bands):
        lo = int(band["abs_valence_lo"])
        hi = int(band["abs_valence_hi"])
        _assert_min_le_max(lo, hi, label=f"affect_return_to_baseline.bands[{i}]")
        if i == 0 and lo != 0:
            _fail("affect_return_to_baseline.bands must start at 0")
        if i > 0 and lo != prev_hi + 1:
            _fail(f"affect_return_to_baseline.bands[{i}] must be contiguous")
        prev_hi = hi
    if prev_hi != 1000:
        _fail("affect_return_to_baseline.bands must end at 1000")


def _validate_recency_bands(bands: Sequence[Mapping[str, Any]], *, label: str) -> None:
    if not bands:
        _fail(f"{label}: empty recency_multipliers")
    prev_max: int | None = None
    for i, band in enumerate(bands):
        lo = int(band["min_hours_inclusive"])
        hi = band["max_hours_exclusive"]
        if i == 0 and lo != 0:
            _fail(f"{label}: first band must start at min_hours_inclusive=0")
        if hi is None:
            if i != len(bands) - 1:
                _fail(f"{label}: only final band may have null max_hours_exclusive")
        else:
            hi_i = int(hi)
            if not (lo < hi_i):
                _fail(f"{label}[{i}]: min_hours_inclusive must be < max_hours_exclusive")
        if prev_max is not None:
            if lo < prev_max:
                _fail(f"{label}[{i}]: overlapping/unordered vs prior max={prev_max}")
            if lo > prev_max:
                _fail(f"{label}[{i}]: gap after prior max={prev_max} (bands must be contiguous)")
            # lo == prev_max → contiguous non-overlapping half-open intervals
        if hi is None:
            prev_max = None
        else:
            prev_max = int(hi)


def _assert_permille_0_1000(value: int, *, label: str) -> None:
    if value < 0 or value > 1000:
        _fail(f"{label} must be in 0..1000, got {value}")


def _assert_strict_permille(value: object, *, label: str) -> int:
    """Strict integer permille 0..1000 (no bool / float / numeric string)."""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be strict integer permille 0..1000 (no bool/float/string)")
    _assert_permille_0_1000(value, label=label)
    return value


def _validate_capture_policy(capture_policy: Mapping[str, Any]) -> None:
    if not isinstance(capture_policy, Mapping):
        _fail("capture_policy must be an object")
    if set(capture_policy.keys()) != {"activation_permille_by_kind"}:
        _fail("capture_policy must contain only activation_permille_by_kind")
    by_kind = capture_policy.get("activation_permille_by_kind")
    if not isinstance(by_kind, Mapping):
        _fail("capture_policy.activation_permille_by_kind must be an object")
    expected = set(CAPTURE_KIND_KEYS)
    keys = set(by_kind.keys())
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        _fail(
            "capture_policy.activation_permille_by_kind keys invalid; "
            f"missing={missing} unknown={unknown}"
        )
    for kind in CAPTURE_KIND_KEYS:
        _assert_strict_permille(
            by_kind[kind],
            label=f"capture_policy.activation_permille_by_kind.{kind}",
        )


def _validate_sleep_knots(
    knots: Sequence[Mapping[str, Any]],
    *,
    label: str,
    homeostatic: bool,
) -> None:
    _assert_strict_increasing_x(knots, label=label)
    _assert_nondecreasing_y(knots, label=label)
    for i, knot in enumerate(knots):
        y = int(knot["y"])
        if homeostatic:
            if y < 0 or y > HOMEOSTATIC_Y_MAX:
                _fail(f"{label}[{i}].y must be in 0..{HOMEOSTATIC_Y_MAX}")
        else:
            if y < 0 or y > DEBT_FATIGUE_Y_MAX:
                _fail(f"{label}[{i}].y must be nonnegative and <= {DEBT_FATIGUE_Y_MAX}")


def _validate_provenance_index(entries: Sequence[Mapping[str, Any]]) -> None:
    paths = [str(e["path"]) for e in entries]
    if paths != sorted(paths):
        _fail("provenance_index paths must be canonical sorted")
    if len(paths) != len(set(paths)):
        _fail("provenance_index paths must be unique")
    missing = REQUIRED_PROVENANCE_PATHS - set(paths)
    if missing:
        _fail(f"provenance_index missing coverage: {sorted(missing)}")
    for entry in entries:
        kind = entry["source_kind"]
        sha = entry.get("source_commit_sha")
        if kind == "FILE" and sha is None:
            _fail(f"provenance_index FILE entry requires source_commit_sha path={entry['path']}")
        if kind != "FILE" and sha is not None and not isinstance(sha, str):
            _fail(f"provenance_index source_commit_sha invalid path={entry['path']}")


def validate_behavior_policy_v2_invariants(policy: Mapping[str, Any]) -> None:
    """Fail-closed invariants beyond JSON Schema for schema_version=2."""
    if int(policy.get("schema_version", -1)) != 2:
        _fail("validate_behavior_policy_v2_invariants requires schema_version=2")
    if policy.get("policy_mode") != "PRODUCTION":
        _fail("v2 policy_mode must be PRODUCTION")

    cal = policy["calibration_metadata"]
    for forbidden in ("status", "approved_hash", "approved_by", "approved_at"):
        if forbidden in cal:
            _fail(f"calibration_metadata must not contain {forbidden}")
    gen = cal.get("calibration_generation")
    if not isinstance(gen, int) or isinstance(gen, bool) or gen < 0:
        _fail("calibration_metadata.calibration_generation must be integer >= 0")

    _reject_forbidden_keys(policy)
    _reject_person_specific_social(policy["social_policy"])

    bands = policy["derived_bands"]
    _assert_strict_monotonic_values(
        [bands["hunger"]["NOTICEABLE"], bands["hunger"]["MEAL_NEEDED"], bands["hunger"]["URGENT"]],
        label="derived_bands.hunger",
    )
    _assert_strict_monotonic_values(
        [
            bands["physical_fatigue"]["MODERATE"],
            bands["physical_fatigue"]["HIGH"],
            bands["physical_fatigue"]["VERY_HIGH"],
        ],
        label="derived_bands.physical_fatigue",
    )
    _assert_strict_monotonic_values(
        [bands["stress"]["MODERATE"], bands["stress"]["HIGH"], bands["stress"]["VERY_HIGH"]],
        label="derived_bands.stress",
    )
    _assert_strict_monotonic_values(
        [bands["social_battery"]["MODERATE"], bands["social_battery"]["HIGH"]],
        label="derived_bands.social_battery",
    )
    _assert_strict_monotonic_values(
        [
            bands["sleep_pressure"]["MODERATE"],
            bands["sleep_pressure"]["HIGH"],
            bands["sleep_pressure"]["CRITICAL"],
        ],
        label="derived_bands.sleep_pressure",
    )

    dynamics = policy["state_dynamics"]
    affect = dynamics["affect_impulses"]
    if not (int(affect["SMALL"]) < int(affect["MEDIUM"]) < int(affect["LARGE"])):
        _fail("affect_impulses must satisfy SMALL < MEDIUM < LARGE")
    stress = dynamics["stress_impulses"]
    if not (0 < int(stress["MINOR"]) < int(stress["MODERATE"]) < int(stress["MAJOR"])):
        _fail("stress_impulses must satisfy 0 < MINOR < MODERATE < MAJOR")
    if int(stress["RELIEF_MINOR"]) >= 0 or int(stress["RELIEF_MODERATE"]) >= 0:
        _fail("stress_impulses RELIEF_* must be negative")
    if abs(int(stress["RELIEF_MINOR"])) > abs(int(stress["RELIEF_MODERATE"])):
        # Optional magnitude order: MODERATE relief stronger (more negative)
        pass
    if not (int(stress["RELIEF_MODERATE"]) < int(stress["RELIEF_MINOR"]) < 0):
        _fail("stress_impulses RELIEF_MODERATE < RELIEF_MINOR < 0")

    _validate_affect_bands(dynamics["affect_return_to_baseline"]["bands"])

    sleep = policy["sleep_policy"]
    _validate_sleep_knots(
        sleep["homeostatic_awake_fraction_knots"],
        label="sleep_policy.homeostatic_awake_fraction_knots",
        homeostatic=True,
    )
    _validate_sleep_knots(
        sleep["sleep_debt_knots"],
        label="sleep_policy.sleep_debt_knots",
        homeostatic=False,
    )
    _validate_sleep_knots(
        sleep["fatigue_contribution_knots"],
        label="sleep_policy.fatigue_contribution_knots",
        homeostatic=False,
    )
    friction = sleep["stress_onset_friction"]
    if int(friction["high_threshold"]) >= int(friction["very_high_threshold"]):
        _fail("sleep_policy.stress_onset_friction thresholds must be strictly increasing")
    nap = sleep["nap"]
    _assert_min_le_max(int(nap["duration_min"]), int(nap["duration_max"]), label="sleep_policy.nap")
    wake = sleep["wake_variation"]
    if "quantum_min" in wake:
        _fail("sleep_policy.wake_variation must not duplicate quantum_min; use time_resolution")

    meal = policy["meal_policy"]
    for name, family in meal["duration_families"].items():
        _assert_min_le_max(
            int(family["duration_min"]),
            int(family["duration_max"]),
            label=f"meal_policy.duration_families.{name}",
        )

    planning = policy["planning_policy"]
    _assert_min_le_max(
        int(planning["prep_buffer_min"]),
        int(planning["prep_buffer_max"]),
        label="planning_policy.prep_buffer",
    )
    _assert_min_le_max(
        int(planning["work_horizon_min_days"]),
        int(planning["work_horizon_target_days"]),
        label="planning_policy.work_horizon",
    )

    work = policy["schedule_generation"]["work"]
    _assert_min_le_max(
        int(work["ordinary_days_per_week_min"]),
        int(work["ordinary_days_per_week_max"]),
        label="schedule_generation.work.ordinary_days_per_week",
    )

    domain = policy["domain_policy"]
    _assert_min_le_max(
        int(domain["study"]["chunk_min"]),
        int(domain["study"]["chunk_max"]),
        label="domain_policy.study.chunk",
    )
    ex = domain["exercise_kickboxing"]
    _assert_min_le_max(int(ex["duration_min"]), int(ex["duration_max"]), label="domain_policy.exercise")
    hh = domain["household_age"]
    if not (
        int(hh["aging_start_min"]) < int(hh["pressing_start_min"]) < int(hh["overdue_start_min"])
    ):
        _fail("domain_policy.household_age aging thresholds must be strictly increasing")
    _assert_min_le_max(
        int(hh["duration_min"]),
        int(hh["duration_max"]),
        label="domain_policy.household_age.duration",
    )
    errand = domain["errand"]
    _assert_min_le_max(
        int(errand["duration_min"]),
        int(errand["duration_max"]),
        label="domain_policy.errand.duration",
    )

    social = policy["social_policy"]
    remote = social["archetypes"]["REMOTE_CLOSE_BURSTY"]
    local = social["archetypes"]["LOCAL_CLOSE_INVITER"]
    work_ctx = social["archetypes"]["WORK_CONTEXTUAL"]
    for label, value in (
        ("REMOTE_CLOSE_BURSTY.base_activation_permille", remote["base_activation_permille"]),
        ("REMOTE_CLOSE_BURSTY.activation_cap_permille", remote["activation_cap_permille"]),
        ("LOCAL_CLOSE_INVITER.base_activation_permille", local["base_activation_permille"]),
        ("LOCAL_CLOSE_INVITER.activation_cap_permille", local["activation_cap_permille"]),
        ("LOCAL_CLOSE_INVITER.invite.base_activation_permille", local["invite"]["base_activation_permille"]),
        ("WORK_CONTEXTUAL.post_work_activation_permille", work_ctx["post_work_activation_permille"]),
    ):
        _assert_permille_0_1000(int(value), label=label)

    _validate_recency_bands(
        remote["recency_multipliers"],
        label="REMOTE_CLOSE_BURSTY.recency_multipliers",
    )
    _validate_recency_bands(
        local["recency_multipliers"],
        label="LOCAL_CLOSE_INVITER.recency_multipliers",
    )

    invite = local["invite"]
    split_sum = int(invite["same_day_split_permille"]) + int(invite["future_split_permille"])
    if split_sum != 1000:
        _fail(f"LOCAL_CLOSE_INVITER invite split must sum to 1000, got {split_sum}")
    _assert_min_le_max(
        int(invite["future_horizon_min_days"]),
        int(invite["future_horizon_max_days"]),
        label="LOCAL_CLOSE_INVITER.invite.future_horizon",
    )
    for mode, duration in social["contact_mode_durations"].items():
        if not duration:
            continue
        if "duration_min" in duration and "duration_max" in duration:
            _assert_min_le_max(
                int(duration["duration_min"]),
                int(duration["duration_max"]),
                label=f"contact_mode_durations.{mode}",
            )

    if "capture_policy" not in policy:
        _fail("capture_policy required")
    _validate_capture_policy(policy["capture_policy"])

    _validate_provenance_index(policy["provenance_index"])
