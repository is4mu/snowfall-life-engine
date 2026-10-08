"""Social response modes, capacity gates, contact, and post-work contracts."""

from __future__ import annotations

from dataclasses import replace
import copy
import unittest

import pytest

from engine.life.decisions import parse_resolved_opportunity
from engine.life.errors import LifeEngineError
from engine.life.ids import stable_id
from engine.life.social_opportunities import SocialExogenousOpportunity
from engine.life.social_response import adapt_social_responses, parse_social_exogenous_opportunity

from tests.support.harnesses.social_response import (
    MIZUKI,
    RENA,
    SAYAKA,
    adapt,
    ctx,
    fingerprint,
    human_state,
    local_contact,
    local_invite,
    policy,
    post_work_opp,
    prior,
    remote_contact,
    post_work_opp,
)


def _as_mapping(opp: SocialExogenousOpportunity) -> dict:
    return opp.as_dict()


pytestmark = pytest.mark.unit


class SocialResponseRuleTests(unittest.TestCase):
    def test_r1_remote_contact_call(self) -> None:
        result = adapt(opportunities=[remote_contact()])
        self.assertEqual(len(result.responses), 1)
        r = result.responses[0]
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(r.reason_code, "CONTACT_READY")
        self.assertEqual(r.contact_mode, "CALL")
        self.assertEqual(r.duration_min, 15)
        self.assertEqual(r.duration_max, 45)
        self.assertEqual(len(result.opportunities), 1)

    def test_r2_local_contact_lightweight(self) -> None:
        result = adapt(opportunities=[local_contact()])
        r = result.responses[0]
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(r.contact_mode, "LIGHTWEIGHT")
        self.assertIsNone(r.duration_min)
        self.assertIsNone(r.duration_max)

    def test_r3_work_post_work_mode(self) -> None:
        result = adapt(opportunities=[post_work_opp()])
        r = result.responses[0]
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(r.reason_code, "POST_WORK_READY")
        self.assertEqual(r.contact_mode, "POST_WORK")
        self.assertEqual(r.duration_min, 30)

    def test_r4_local_invite_local_outing(self) -> None:
        opp = local_invite()
        result = adapt(
            opportunities=[opp],
            availability_windows=[
                {
                    "availability_id": "a1",
                    "opportunity_id": opp.opportunity_id,
                    "earliest_start": "2026-03-15T19:30:00+09:00",
                    "latest_end": "2026-03-15T22:30:00+09:00",
                    "location_id": None,
                    "hard_conflict": False,
                }
            ],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "ACCEPT")
        self.assertEqual(r.contact_mode, "LOCAL_OUTING")
        self.assertEqual(len(result.commitment_proposals), 1)

    def test_r5_kind_archetype_mismatch_fail(self) -> None:
        from dataclasses import replace

        mismatched = replace(remote_contact(), archetype_id="WORK_CONTEXTUAL")
        with self.assertRaises(LifeEngineError):
            adapt(opportunities=[mismatched])

    def test_r6_no_response_rng(self) -> None:
        from pathlib import Path
        import engine.life.social_response as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        for banned in (
            "u01(",
            "keyed_digest",
            "random.",
            "Random(",
            "uuid4",
            "acceptance_draw",
            "roll_acceptance",
        ):
            self.assertNotIn(banned, src)

    def test_r7_repeat_identical(self) -> None:
        opps = [remote_contact(), local_contact(), post_work_opp()]
        a = fingerprint(adapt(opportunities=opps))
        b = fingerprint(adapt(opportunities=opps))
        self.assertEqual(a, b)

    def test_r8_reverse_input_order(self) -> None:
        opps = [remote_contact(), local_contact(), post_work_opp()]
        a = fingerprint(adapt(opportunities=opps))
        b = fingerprint(adapt(opportunities=list(reversed(opps))))
        self.assertEqual(a, b)

    def test_r9_duplicate_opportunity_id_fail(self) -> None:
        with self.assertRaises(LifeEngineError):
            adapt(opportunities=[remote_contact("dup"), remote_contact("dup")])

    def test_r10_unknown_person_fail(self) -> None:
        with self.assertRaises(LifeEngineError):
            adapt(
                opportunities=[remote_contact()],
                known_person_ids=(SAYAKA, RENA),
            )

    def test_r11_future_not_yet_available(self) -> None:
        result = adapt(
            context=ctx(now="2026-03-14T12:00:00+09:00"),
            opportunities=[remote_contact(available_local_date="2026-03-15")],
        )
        self.assertEqual(result.responses, ())
        self.assertEqual(len(result.diagnostics), 1)
        self.assertEqual(result.diagnostics[0].code, "NOT_YET_AVAILABLE")

    def test_r12_stale_contact_decline_expired(self) -> None:
        result = adapt(
            opportunities=[remote_contact(available_local_date="2026-03-14")],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(r.reason_code, "OPPORTUNITY_EXPIRED")

    def test_r13_stale_post_work_decline_expired(self) -> None:
        result = adapt(
            opportunities=[
                post_work_opp(
                    available_local_date="2026-03-14",
                    target_local_date="2026-03-14",
                )
            ],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(r.reason_code, "OPPORTUNITY_EXPIRED")

    def test_r14_stale_invite_target_expired(self) -> None:
        result = adapt(
            opportunities=[
                local_invite(
                    timing="FUTURE_SOFT",
                    available_local_date="2026-03-10",
                    target_local_date="2026-03-14",
                )
            ],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(r.reason_code, "OPPORTUNITY_EXPIRED")

    def test_r15_contact_hard_block_defer(self) -> None:
        result = adapt(
            context=ctx(hard_commitment_blocking_now=True),
            opportunities=[remote_contact()],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DEFER")
        self.assertEqual(r.reason_code, "HARD_COMMITMENT_BLOCKING")

    def test_r16_contact_low_battery_defer(self) -> None:
        result = adapt(
            context=ctx(human=human_state(social_battery=100)),
            opportunities=[remote_contact()],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DEFER")
        self.assertEqual(r.reason_code, "LOW_SOCIAL_BATTERY")

    def test_r17_contact_very_high_fatigue_defer(self) -> None:
        result = adapt(
            context=ctx(human=human_state(physical_fatigue=800)),
            opportunities=[remote_contact()],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DEFER")
        self.assertEqual(r.reason_code, "VERY_HIGH_FATIGUE")

    def test_r18_contact_critical_sleep_defer(self) -> None:
        result = adapt(
            context=ctx(sleep_pressure=960),
            opportunities=[remote_contact()],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DEFER")
        self.assertEqual(r.reason_code, "CRITICAL_SLEEP_PRESSURE")

    def test_r19_remote_call_insufficient_window_defer(self) -> None:
        result = adapt(
            context=ctx(available_window_min=10),
            opportunities=[remote_contact()],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DEFER")
        self.assertEqual(r.reason_code, "INSUFFICIENT_WINDOW")

    def test_r20_eligible_contact_resolved_opportunity(self) -> None:
        result = adapt(opportunities=[remote_contact("opp-r20")])
        self.assertEqual(len(result.opportunities), 1)
        opp = result.opportunities[0]
        parse_resolved_opportunity(opp)
        self.assertEqual(opp.opportunity_class, "SOCIAL_PROMISE")
        self.assertEqual(opp.action_kind, "SOCIAL_CONTACT")
        self.assertEqual(opp.source_kind, "SOCIAL")
        self.assertEqual(opp.source_ref, "opp-r20")
        self.assertTrue(opp.soft_candidate)
        self.assertTrue(opp.time_feasible)
        self.assertTrue(opp.location_feasible)
        self.assertTrue(opp.physical_feasible)
        self.assertTrue(opp.domain_guard_satisfied)
        self.assertIsNone(opp.local_preference_permille)
        self.assertEqual(
            opp.opportunity_key,
            stable_id("social-resolved-opportunity", "opp-r20"),
        )

    def test_r21_post_work_capacity_block_decline(self) -> None:
        result = adapt(
            context=ctx(human=human_state(social_battery=100)),
            opportunities=[post_work_opp()],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(r.reason_code, "LOW_SOCIAL_BATTERY")

    def test_r22_post_work_location_false_decline(self) -> None:
        result = adapt(
            context=ctx(post_work_location_feasible=False),
            opportunities=[post_work_opp()],
        )
        r = result.responses[0]
        self.assertEqual(r.response, "DECLINE")
        self.assertEqual(r.reason_code, "LOCATION_BLOCKED")

    def test_r23_eligible_post_work_social_contact(self) -> None:
        result = adapt(opportunities=[post_work_opp("opp-r23")])
        self.assertEqual(result.responses[0].response, "ACCEPT")
        self.assertEqual(len(result.opportunities), 1)
        self.assertEqual(result.opportunities[0].action_kind, "SOCIAL_CONTACT")
        self.assertEqual(result.commitment_proposals, ())


class SocialOpportunityValidationTests(unittest.TestCase):
    def test_dataclass_empty_person_id_fail_closed(self) -> None:
        bad = replace(remote_contact("dc-empty-person"), person_id="")
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(bad)
        with self.assertRaises(LifeEngineError):
            adapt(opportunities=[bad])

    def test_mapping_empty_person_id_fail_closed(self) -> None:
        bad = _as_mapping(remote_contact("map-empty-person"))
        bad["person_id"] = ""
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(bad)
        with self.assertRaises(LifeEngineError):
            adapt(opportunities=[bad])

    def test_dataclass_empty_opportunity_id_fail_closed(self) -> None:
        bad = replace(remote_contact("dc-empty-oid"), opportunity_id="")
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(bad)

    def test_dataclass_and_mapping_share_semantics_path(self) -> None:
        """Same malformed payload fails whether dataclass or mapping."""
        base = remote_contact("shared-path")
        # CONTACT must not carry invite timing.
        dc = replace(base, invite_timing_kind="SAME_DAY_SOFT")
        mp = _as_mapping(base)
        mp["invite_timing_kind"] = "SAME_DAY_SOFT"
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_contact_wrong_source_kind_fail_both_paths(self) -> None:
        base = remote_contact("contact-src")
        dc = replace(base, source_kind="POST_WORK_CONTEXT")
        mp = _as_mapping(base)
        mp["source_kind"] = "POST_WORK_CONTEXT"
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_post_work_wrong_source_kind_fail_both_paths(self) -> None:
        from tests.support.harnesses.social_response import post_work_opp

        base = post_work_opp("pw-src")
        dc = replace(base, source_kind="WEEKLY_SLOT")
        mp = _as_mapping(base)
        mp["source_kind"] = "WEEKLY_SLOT"
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_contact_must_not_set_target_or_timing_keys(self) -> None:
        base = remote_contact("contact-keys")
        for kwargs in (
            {"target_local_date": "2026-03-15"},
            {"timing_random_key": "t-key"},
            {"future_day_random_key": "f-key"},
        ):
            dc = replace(base, **kwargs)
            mp = _as_mapping(base)
            mp.update(kwargs)
            with self.assertRaises(LifeEngineError):
                parse_social_exogenous_opportunity(dc)
            with self.assertRaises(LifeEngineError):
                parse_social_exogenous_opportunity(mp)

    def test_future_soft_same_day_target_fail_closed_both_paths(self) -> None:
        """FUTURE_SOFT + same-day target must not bypass capacity via invite path."""
        base = local_invite(
            "fut-same-day",
            timing="FUTURE_SOFT",
            available_local_date="2026-03-15",
            target_local_date="2026-03-18",
        )
        # Corrupt target to same day as available.
        dc = replace(base, target_local_date="2026-03-15")
        mp = _as_mapping(base)
        mp["target_local_date"] = "2026-03-15"
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)
        # Also via adapt: must fail before capacity-insensitive ACCEPT.
        with self.assertRaises(LifeEngineError):
            adapt(
                context=ctx(human=human_state(social_battery=50)),
                opportunities=[dc],
            )

    def test_same_day_soft_future_target_fail_closed_both_paths(self) -> None:
        base = local_invite(
            "same-future-target",
            timing="SAME_DAY_SOFT",
            available_local_date="2026-03-15",
            target_local_date="2026-03-15",
        )
        dc = replace(base, target_local_date="2026-03-18")
        mp = _as_mapping(base)
        mp["target_local_date"] = "2026-03-18"
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_future_soft_requires_future_day_key_both_paths(self) -> None:
        base = local_invite("fut-nokey", timing="FUTURE_SOFT")
        dc = replace(base, future_day_random_key=None)
        mp = _as_mapping(base)
        mp["future_day_random_key"] = None
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_same_day_soft_forbids_future_day_key_both_paths(self) -> None:
        base = local_invite("same-fkey", timing="SAME_DAY_SOFT")
        dc = replace(base, future_day_random_key="unexpected")
        mp = _as_mapping(base)
        mp["future_day_random_key"] = "unexpected"
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_invite_requires_timing_key_both_paths(self) -> None:
        base = local_invite("inv-nokey", timing="SAME_DAY_SOFT")
        dc = replace(base, timing_random_key=None)
        mp = _as_mapping(base)
        mp["timing_random_key"] = None
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_post_work_target_must_match_available_both_paths(self) -> None:
        from tests.support.harnesses.social_response import post_work_opp

        base = post_work_opp("pw-target")
        dc = replace(base, target_local_date="2026-03-16")
        mp = _as_mapping(base)
        mp["target_local_date"] = "2026-03-16"
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(dc)
        with self.assertRaises(LifeEngineError):
            parse_social_exogenous_opportunity(mp)

    def test_valid_opportunities_still_parse_both_paths(self) -> None:
        for factory in (remote_contact, local_invite):
            if factory is local_invite:
                opp = factory("ok-inv", timing="FUTURE_SOFT")
            else:
                opp = factory("ok-contact")
            parsed_dc = parse_social_exogenous_opportunity(opp)
            parsed_mp = parse_social_exogenous_opportunity(opp.as_dict())
            self.assertEqual(parsed_dc.opportunity_id, parsed_mp.opportunity_id)
            self.assertEqual(parsed_dc.opportunity_kind, parsed_mp.opportunity_kind)

class PriorResponseIdentityTests(unittest.TestCase):
    def test_prior_arbitrary_response_id_fail_closed_both_paths(self) -> None:
        from engine.life.ids import stable_id
        from engine.life.social_response import (
            PriorSocialResponseFact,
            parse_prior_response,
        )

        opp = remote_contact("prior-arb")
        known = {opp.opportunity_id}
        now = "2026-03-15T19:00:00+09:00"
        canonical = stable_id("social-response", "prior-arb", "ACCEPT")
        dc = PriorSocialResponseFact(
            response_id="arbitrary-nonempty-id",
            opportunity_id="prior-arb",
            response="ACCEPT",
            decided_at="2026-03-15T18:00:00+09:00",
            reason_code="CONTACT_READY",
        )
        mp = {
            "response_id": "arbitrary-nonempty-id",
            "opportunity_id": "prior-arb",
            "response": "ACCEPT",
            "decided_at": "2026-03-15T18:00:00+09:00",
            "reason_code": "CONTACT_READY",
        }
        with self.assertRaises(LifeEngineError):
            parse_prior_response(dc, now=now, known_opp_ids=known)
        with self.assertRaises(LifeEngineError):
            parse_prior_response(mp, now=now, known_opp_ids=known)
        with self.assertRaises(LifeEngineError):
            adapt(opportunities=[opp], prior_responses=[dc])
        with self.assertRaises(LifeEngineError):
            adapt(opportunities=[opp], prior_responses=[mp])
        # Canonical identity still accepted on both paths.
        good_dc = replace(dc, response_id=canonical)
        good_mp = dict(mp)
        good_mp["response_id"] = canonical
        self.assertEqual(
            parse_prior_response(good_dc, now=now, known_opp_ids=known).response_id,
            canonical,
        )
        self.assertEqual(
            parse_prior_response(good_mp, now=now, known_opp_ids=known).response_id,
            canonical,
        )

    def test_prior_mismatched_response_component_fail_closed(self) -> None:
        """ACCEPT identity used with DECLINE response must fail."""
        from engine.life.ids import stable_id
        from engine.life.social_response import (
            PriorSocialResponseFact,
            parse_prior_response,
        )

        opp = remote_contact("prior-mismatch")
        known = {opp.opportunity_id}
        now = "2026-03-15T19:00:00+09:00"
        accept_id = stable_id("social-response", "prior-mismatch", "ACCEPT")
        dc = PriorSocialResponseFact(
            response_id=accept_id,
            opportunity_id="prior-mismatch",
            response="DECLINE",
            decided_at="2026-03-15T18:00:00+09:00",
            reason_code="OPPORTUNITY_EXPIRED",
        )
        mp = {
            "response_id": accept_id,
            "opportunity_id": "prior-mismatch",
            "response": "DECLINE",
            "decided_at": "2026-03-15T18:00:00+09:00",
            "reason_code": "OPPORTUNITY_EXPIRED",
        }
        with self.assertRaises(LifeEngineError):
            parse_prior_response(dc, now=now, known_opp_ids=known)
        with self.assertRaises(LifeEngineError):
            parse_prior_response(mp, now=now, known_opp_ids=known)
