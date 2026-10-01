"""Long-horizon deterministic decision-resolver invariant soaks."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import pytest

from tests.support.harnesses.resolver import (
    active_ctx,
    boundary,
    deadline_opp,
    hard_opp,
    hs,
    integrate_oneshot_vs_split,
    leisure_opp,
    load_v2_policy,
    resolve,
    routine_opp,
    soft_guard,
)

pytestmark = [pytest.mark.soak, pytest.mark.slow]


class Soak28DayTests(unittest.TestCase):
    def test_soak_28_day_synthetic_decisions(self) -> None:
        policy = load_v2_policy()
        source_less_selected = 0
        lower_tier_override = 0
        duplicate_ids = 0
        order_equiv_pass = True
        split_equiv_pass = True
        decisions = 0
        result_counts: dict[str, int] = {}
        tier_counts: dict[str, int] = {}
        soft_guard_filtered = 0
        min_dwell_forced = 0
        keyed_tie = 0
        ranked_deterministic = 0
        continue_current = 0
        no_eligible = 0

        from engine.life.timeutil import format_rfc3339, parse_rfc3339
        from datetime import timedelta

        start_dt = parse_rfc3339("2026-03-01T08:00:00+09:00")

        for day in range(28):
            for hour in (8, 12, 16, 20):
                now_dt = start_dt + timedelta(days=day, hours=hour - 8)
                now = format_rfc3339(now_dt)
                hunger = 100 + (day * 17 + hour * 3) % 800
                fatigue = 80 + (day * 13 + hour * 5) % 750
                state = hs(hunger=hunger, physical_fatigue=fatigue)
                opps = []
                if day % 3 == 0 and hour == 12:
                    opps.append(hard_opp(f"hard:d{day}"))
                if day % 4 == 0 and hour == 16:
                    # Ranked deterministic: large preference gap
                    opps.append(deadline_opp(f"dl:d{day}:a", pref=900))
                    opps.append(deadline_opp(f"dl:d{day}:b", pref=700))
                elif day % 4 == 1 and hour == 16:
                    # Keyed tie: near-equal prefs
                    opps.append(deadline_opp(f"dl:d{day}:a", pref=880))
                    opps.append(deadline_opp(f"dl:d{day}:b", pref=860))
                if day % 5 == 0:
                    opps.append(routine_opp(f"rt:d{day}"))
                if hour == 20:
                    opps.append(leisure_opp(f"lz:d{day}"))

                soft_guards = []
                if hour == 20 and day % 2 == 0:
                    soft_guards.append(
                        soft_guard(
                            candidate_key=f"leisure:MUSIC:fw:{day}",
                            decided_at=format_rfc3339(now_dt - timedelta(minutes=10)),
                        )
                    )

                active = None
                trigger = "THRESHOLD"
                metric: str | None = "HUNGER"
                band: str | None = "MEAL_NEEDED"
                if hour == 8 and day % 3 == 0:
                    # Min-dwell forced continuation scenarios
                    active = active_ctx(
                        actual_start=format_rfc3339(now_dt - timedelta(minutes=3)),
                        current_priority_tier="ROUTINE_HABIT",
                        action_kind="STUDY",
                        source_kind="TASK",
                        source_ref=f"task:d{day}",
                    )
                    opps.append(routine_opp(f"rt:dwell:{day}"))
                if hour == 20:
                    trigger = "PLANNED_WINDOW"
                    metric = None
                    band = None

                kwargs_common = dict(
                    human_state=state,
                    opportunities=opps,
                    soft_guards=soft_guards,
                    now=now,
                    boundary_fact=boundary(
                        decision_key=f"soak:{day}:{hour}",
                        trigger_kind=trigger,
                        reason_code="SOAK",
                        metric=metric,
                        target_band=band,
                    ),
                    meal_feasible=True,
                    rest_feasible=True,
                    free_window_key=f"fw:{day}" if hour == 20 else None,
                    free_window_min=50 if hour == 20 else None,
                    feasible_leisure_categories=["MUSIC", "MOVIE"] if hour == 20 else (),
                    active=active,
                )

                out1 = resolve(policy, **kwargs_common)
                out2 = resolve(
                    policy,
                    **{**kwargs_common, "opportunities": list(reversed(opps))},
                )
                if out1.selected_candidate_id != out2.selected_candidate_id:
                    order_equiv_pass = False
                if out1.random_key != out2.random_key:
                    order_equiv_pass = False

                if out1.selected_action_kind in {
                    "STUDY",
                    "WORK",
                    "ERRAND",
                    "HOUSEHOLD",
                    "SOCIAL_PROMISE",
                    "SOCIAL_CONTACT",
                    "KICKBOXING",
                }:
                    if out1.selected_candidate_key and not any(
                        o.opportunity_key == out1.selected_candidate_key and o.source_ref
                        for o in opps
                    ):
                        source_less_selected += 1

                if len(out1.considered_candidate_ids) != len(set(out1.considered_candidate_ids)):
                    duplicate_ids += 1

                soft_guard_filtered += sum(
                    1 for r in out1.rejected if r.reason == "SOFT_RECONSIDER_GUARD"
                )
                if out1.min_dwell_forced:
                    min_dwell_forced += 1
                if out1.selection_mode == "keyed_tie":
                    keyed_tie += 1
                if out1.selection_mode == "ranked_deterministic":
                    ranked_deterministic += 1
                if out1.result_kind == "CONTINUE_CURRENT":
                    continue_current += 1
                if out1.result_kind == "NO_ELIGIBLE_ACTION":
                    no_eligible += 1

                decisions += 1
                result_counts[out1.result_kind] = result_counts.get(out1.result_kind, 0) + 1
                if out1.selected_priority_tier:
                    tier_counts[out1.selected_priority_tier] = (
                        tier_counts.get(out1.selected_priority_tier, 0) + 1
                    )

        # Split equivalence sample
        hs_a, hs_b = integrate_oneshot_vs_split(
            policy,
            start_hs=hs(hunger=200),
            total_seconds=3600,
            parts=(1200, 1200, 1200),
        )
        if hs_a != hs_b:
            split_equiv_pass = False
        else:
            oa = resolve(
                policy,
                human_state=hs_a,
                opportunities=[deadline_opp("dl:split")],
                boundary_fact=boundary(decision_key="split-eq"),
            )
            ob = resolve(
                policy,
                human_state=hs_b,
                opportunities=[deadline_opp("dl:split")],
                boundary_fact=boundary(decision_key="split-eq"),
            )
            if oa.selected_candidate_id != ob.selected_candidate_id or oa.random_key != ob.random_key:
                split_equiv_pass = False

        for day in range(0, 28, 7):
            out = resolve(
                policy,
                human_state=hs(hunger=700),
                opportunities=[hard_opp(f"inv:h{day}"), deadline_opp(f"inv:d{day}", pref=1000)],
                meal_feasible=True,
                rest_feasible=True,
                boundary_fact=boundary(decision_key=f"inv:{day}"),
            )
            if out.selected_priority_tier != "HARD_COMMITMENT":
                lower_tier_override += 1

        metrics = {
            "decisions": decisions,
            "result_counts": result_counts,
            "tier_counts": tier_counts,
            "continue_current": continue_current,
            "no_eligible_action": no_eligible,
            "soft_guard_filtered": soft_guard_filtered,
            "min_dwell_forced_continuation": min_dwell_forced,
            "keyed_tie": keyed_tie,
            "ranked_deterministic": ranked_deterministic,
            "source_less_selected": source_less_selected,
            "lower_tier_override": lower_tier_override,
            "duplicate_candidate_ids": duplicate_ids,
            "order_equivalence": order_equiv_pass,
            "split_equivalence": split_equiv_pass,
        }
        out_path = Path("/tmp/slice2d_soak28_metrics.json")
        out_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print("SLICE2D_SOAK28_METRICS", json.dumps(metrics, sort_keys=True))

        self.assertEqual(source_less_selected, 0)
        self.assertEqual(lower_tier_override, 0)
        self.assertEqual(duplicate_ids, 0)
        self.assertTrue(order_equiv_pass)
        self.assertTrue(split_equiv_pass)
        self.assertGreater(decisions, 0)
        # Instrumentation must be exercised meaningfully (not tuned to targets).
        self.assertGreater(soft_guard_filtered, 0)
        self.assertGreater(min_dwell_forced, 0)
        self.assertGreater(keyed_tie, 0)
        self.assertGreater(ranked_deterministic, 0)
        self.assertGreater(continue_current, 0)
        self.assertGreater(no_eligible, 0)

class Soak90DayInvariantTests(unittest.TestCase):
    def test_soak_90_day_invariants(self) -> None:
        policy = load_v2_policy()
        failures = {
            "determinism": 0,
            "order_independence": 0,
            "priority_inversion": 0,
            "source_less": 0,
        }
        from engine.life.timeutil import format_rfc3339, parse_rfc3339
        from datetime import timedelta

        start_dt = parse_rfc3339("2026-01-01T09:00:00+09:00")
        for day in range(90):
            now = format_rfc3339(start_dt + timedelta(days=day))
            state = hs(
                hunger=150 + (day * 11) % 700,
                physical_fatigue=100 + (day * 7) % 700,
            )
            opps = [
                deadline_opp(f"d:{day}:a", pref=700 + (day % 200)),
                deadline_opp(f"d:{day}:b", pref=680 + (day % 200)),
                routine_opp(f"r:{day}"),
            ]
            if day % 2 == 0:
                opps.append(hard_opp(f"h:{day}"))
            kwargs = dict(
                human_state=state,
                opportunities=opps,
                now=now,
                boundary_fact=boundary(decision_key=f"d90:{day}"),
                meal_feasible=True,
            )
            a = resolve(policy, **kwargs)
            b = resolve(policy, **kwargs)
            if a.as_dict() != b.as_dict():
                failures["determinism"] += 1
            c = resolve(policy, **{**kwargs, "opportunities": list(reversed(opps))})
            if a.selected_candidate_id != c.selected_candidate_id or a.random_key != c.random_key:
                failures["order_independence"] += 1
            if any(o.opportunity_class == "HARD_COMMITMENT" for o in opps):
                if a.selected_priority_tier != "HARD_COMMITMENT":
                    failures["priority_inversion"] += 1
            if a.selected_action_kind in {
                "STUDY",
                "WORK",
                "ERRAND",
                "HOUSEHOLD",
                "SOCIAL_PROMISE",
                "SOCIAL_CONTACT",
                "KICKBOXING",
            }:
                matched = [
                    o
                    for o in opps
                    if o.opportunity_key == a.selected_candidate_key and o.source_ref
                ]
                if not matched:
                    failures["source_less"] += 1

        metrics = {"failures": failures, "days": 90}
        Path("/tmp/slice2d_soak90_metrics.json").write_text(
            json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print("SLICE2D_SOAK90_METRICS", json.dumps(metrics, sort_keys=True))
        self.assertEqual(failures["determinism"], 0)
        self.assertEqual(failures["order_independence"], 0)
        self.assertEqual(failures["priority_inversion"], 0)
        self.assertEqual(failures["source_less"], 0)

