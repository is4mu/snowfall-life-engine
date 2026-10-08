"""Historical social projection and runtime social composition integration tests."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_runtime import (
    ActivityStartSpec,
    derive_activity_instance_id,
    start_activity_from_selection,
)
from engine.life.canonical import canonical_json
from engine.life.decisions import candidate_id_for
from engine.life.errors import LifeEngineError
from engine.life.ids import stable_id
from engine.life.policy import load_policy
from engine.life.runtime_bundle import RuntimeReferenceSets, load_runtime_bundle
from engine.life.runtime_decision import (
    build_runtime_decision_frame,
    runtime_trigger_from_v2_wakeup,
)
from engine.life.social_opportunities import SocialExogenousOpportunity
from engine.life.social_runtime import (
    SuccessfulImmediateSocialStart,
    apply_pending_immediate_accept_after_successful_start,
    apply_social_response_decision,
    build_runtime_social_decision,
    build_social_response_state,
    generate_historical_social_opportunities,
    is_response_eligible_opportunity,
    merge_social_exogenous_opportunities,
    parse_pending_immediate_accept,
    parse_successful_immediate_social_start,
    relevant_week_start_dates,
)
from engine.life.world_state import project_initial_relation_state

from tests.support.builders.social_runtime import (
    AS_OF,
    CHAR,
    HORIZON,
    MIZUKI,
    POLICY,
    POLICY_VERSION,
    RENA,
    SAYAKA,
    SEED_BOTH,
    SEED_CONTACT,
    SEED_MULTI_INVITE,
    SEED_POST_WORK,
    SEED_PRIOR_WEEK_INVITE,
    SEED_SAME_DAY_INVITE,
    SOCIAL_KNOWN,
    _commitment,
    _decision_facts,
    _facts,
    _load_social_bundle,
    _run_social,
    _social_facts,
    _social_refs,
    _structural_phys,
    _successful_start_from_result,
    _v2_wakeup,
    contact,
    default_assignments,
    make_opp,
)

import pytest

pytestmark = pytest.mark.integration


class HistoricalProjectionTests(unittest.TestCase):
    def test_horizon_derived_lookback(self) -> None:
        weeks = relevant_week_start_dates(
            decision_local_date="2026-04-01", horizon_days=HORIZON
        )
        self.assertEqual(weeks, ("2026-03-23", "2026-03-30"))
        self.assertEqual(HORIZON, 7)

    def test_current_week_contact(self) -> None:
        gen = generate_historical_social_opportunities(
            character_id=CHAR,
            world_seed=SEED_CONTACT,
            as_of=AS_OF,
            behavior_policy=POLICY,
            known_person_ids=SOCIAL_KNOWN,
            assignments=default_assignments(),
            contacts=[
                contact(
                    actual_end="2026-03-20T12:00:00+09:00",
                    finalized_at="2026-03-20T12:05:00+09:00",
                )
            ],
        )
        contacts = [
            o
            for o in gen.opportunities
            if o.opportunity_kind == "CONTACT_OPPORTUNITY"
        ]
        self.assertTrue(contacts)
        self.assertTrue(
            all(o.available_local_date == "2026-04-01" for o in contacts)
        )

    def test_prior_week_future_invite_visible(self) -> None:
        gen = generate_historical_social_opportunities(
            character_id=CHAR,
            world_seed=SEED_PRIOR_WEEK_INVITE,
            as_of=AS_OF,
            behavior_policy=POLICY,
            known_person_ids=SOCIAL_KNOWN,
            assignments=default_assignments(),
        )
        invites = [
            o
            for o in gen.opportunities
            if o.opportunity_kind == "INVITE_OPPORTUNITY"
        ]
        self.assertTrue(invites)
        prior = invites[0]
        self.assertEqual(prior.invite_timing_kind, "FUTURE_SOFT")
        self.assertLess(prior.available_local_date, "2026-04-01")
        self.assertGreaterEqual(prior.target_local_date, "2026-04-01")
        self.assertTrue(
            is_response_eligible_opportunity(
                prior, decision_local_date="2026-04-01"
            )
        )

    def test_expired_invite_absent(self) -> None:
        expired = make_opp(
            opportunity_id="expired-invite",
            opportunity_kind="INVITE_OPPORTUNITY",
            person_id=SAYAKA,
            archetype_id="LOCAL_CLOSE_INVITER",
            available_local_date="2026-03-20",
            invite_timing_kind="FUTURE_SOFT",
            target_local_date="2026-03-31",
        )
        self.assertFalse(
            is_response_eligible_opportunity(
                expired, decision_local_date="2026-04-01"
            )
        )

    def test_post_work_evaluated_once(self) -> None:
        work = {
            "work_context_id": "work-1",
            "actual_end": "2026-04-01T18:00:00+09:00",
            "finalized_at": "2026-04-01T18:05:00+09:00",
            "participant_person_ids": [RENA],
        }
        gen = generate_historical_social_opportunities(
            character_id=CHAR,
            world_seed=SEED_CONTACT,
            as_of="2026-04-01T19:00:00+09:00",
            behavior_policy=POLICY,
            known_person_ids=SOCIAL_KNOWN,
            assignments=default_assignments(),
            post_work_contexts=[work],
        )
        keys = [
            f"{r.work_context_id}:{r.person_id}" for r in gen.post_work_resolutions
        ]
        self.assertEqual(len(keys), len(set(keys)))

    def test_unrelated_old_week_excluded(self) -> None:
        weeks = relevant_week_start_dates(
            decision_local_date="2026-04-01", horizon_days=HORIZON
        )
        self.assertNotIn("2026-03-16", weeks)

    def test_input_order_invariant(self) -> None:
        assigns = default_assignments()
        a = generate_historical_social_opportunities(
            character_id=CHAR,
            world_seed=SEED_BOTH,
            as_of=AS_OF,
            behavior_policy=POLICY,
            known_person_ids=SOCIAL_KNOWN,
            assignments=assigns,
        )
        b = generate_historical_social_opportunities(
            character_id=CHAR,
            world_seed=SEED_BOTH,
            as_of=AS_OF,
            behavior_policy=POLICY,
            known_person_ids=SOCIAL_KNOWN,
            assignments=list(reversed(assigns)),
        )
        self.assertEqual(
            [o.opportunity_id for o in a.opportunities],
            [o.opportunity_id for o in b.opportunities],
        )

    def test_duplicate_merge_guard_via_eligibility(self) -> None:
        base = make_opp(
            opportunity_id="dup-opp",
            opportunity_kind="CONTACT_OPPORTUNITY",
            person_id=MIZUKI,
            archetype_id="REMOTE_CLOSE_BURSTY",
            available_local_date="2026-04-01",
        )
        same = make_opp(
            opportunity_id="dup-opp",
            opportunity_kind="CONTACT_OPPORTUNITY",
            person_id=MIZUKI,
            archetype_id="REMOTE_CLOSE_BURSTY",
            available_local_date="2026-04-01",
        )
        merged = merge_social_exogenous_opportunities([base], [same])
        self.assertEqual(len(merged), 1)
        conflict = make_opp(
            opportunity_id="dup-opp",
            opportunity_kind="CONTACT_OPPORTUNITY",
            person_id=SAYAKA,  # different payload
            archetype_id="REMOTE_CLOSE_BURSTY",
            available_local_date="2026-04-01",
        )
        with self.assertRaises(LifeEngineError) as ctx:
            merge_social_exogenous_opportunities([base], [conflict])
        self.assertIn("duplicate opportunity_id with different payload", str(ctx.exception))


class RuntimeSocialCompositionTests(unittest.TestCase):
    def test_priors_sourced_from_social_response_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
            )
            invite = next(
                (
                    r
                    for r in probe.social_response.responses
                    if r.opportunity_kind == "INVITE_OPPORTUNITY"
                ),
                None,
            )
            self.assertIsNotNone(invite)
            assert invite is not None
            oid = invite.opportunity_id
            avail = _social_facts(
                availability_windows=[
                    {
                        "availability_id": "a1",
                        "opportunity_id": oid,
                        "earliest_start": "2026-04-01T19:00:00+09:00",
                        "latest_end": "2026-04-01T22:00:00+09:00",
                        "location_id": None,
                        "hard_conflict": False,
                    }
                ]
            )
            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
                social_facts=avail,
            )
            decision = next(
                r for r in result.social_response.responses if r.opportunity_id == oid
            )
            self.assertEqual(decision.response, "ACCEPT")
            self.assertTrue(
                any(
                    r.response == "ACCEPT" and r.opportunity_id == oid
                    for r in result.social_response_state.responses
                )
            )
            result_b, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
                social_state=result.social_response_state,
                social_facts=avail,
            )
            prior = next(
                r
                for r in result_b.social_response.responses
                if r.opportunity_id == oid
            )
            self.assertEqual(prior.reason_code, "PRIOR_TERMINAL_RESPONSE")
            self.assertEqual(result_b.social_response.commitment_proposals, ())

    def test_prior_decline_dedupes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gen = generate_historical_social_opportunities(
                character_id=CHAR,
                world_seed=SEED_CONTACT,
                as_of=AS_OF,
                behavior_policy=POLICY,
                known_person_ids=SOCIAL_KNOWN,
                assignments=default_assignments(),
                contacts=[
                    contact(
                        actual_end="2026-03-20T12:00:00+09:00",
                        finalized_at="2026-03-20T12:05:00+09:00",
                    )
                ],
            )
            contact_opp = next(
                o
                for o in gen.opportunities
                if o.opportunity_kind == "CONTACT_OPPORTUNITY"
            )
            state = build_social_response_state(
                character_id=CHAR,
                as_of=AS_OF,
                responses=[
                    {
                        "response_id": stable_id(
                            "social-response", contact_opp.opportunity_id, "DECLINE"
                        ),
                        "opportunity_id": contact_opp.opportunity_id,
                        "response": "DECLINE",
                        "decided_at": AS_OF,
                        "reason_code": "OPPORTUNITY_EXPIRED",
                    }
                ],
            )
            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                social_state=state,
            )
            prior = next(
                r
                for r in result.social_response.responses
                if r.opportunity_id == contact_opp.opportunity_id
            )
            self.assertEqual(prior.reason_code, "PRIOR_TERMINAL_RESPONSE")
            self.assertEqual(result.social_response.opportunities, ())

    def test_prior_defer_re_evaluates_without_churn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gen = generate_historical_social_opportunities(
                character_id=CHAR,
                world_seed=SEED_CONTACT,
                as_of=AS_OF,
                behavior_policy=POLICY,
                known_person_ids=SOCIAL_KNOWN,
                assignments=default_assignments(),
                contacts=[
                    contact(
                        actual_end="2026-03-20T12:00:00+09:00",
                        finalized_at="2026-03-20T12:05:00+09:00",
                    )
                ],
            )
            contact_opp = next(
                o
                for o in gen.opportunities
                if o.opportunity_kind == "CONTACT_OPPORTUNITY"
            )
            state = build_social_response_state(
                character_id=CHAR,
                as_of=AS_OF,
                responses=[
                    {
                        "response_id": stable_id(
                            "social-response",
                            contact_opp.opportunity_id,
                            "DEFER",
                        ),
                        "opportunity_id": contact_opp.opportunity_id,
                        "response": "DEFER",
                        "decided_at": AS_OF,
                        "reason_code": "HARD_COMMITMENT_BLOCKING",
                    }
                ],
            )
            # Still blocked → same DEFER reason → no hash churn.
            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                social_state=state,
                social_facts=_social_facts(hard_commitment_blocking_now=True),
            )
            self.assertEqual(
                result.social_response_state.revision, state.revision
            )
            decision = next(
                r
                for r in result.social_response.responses
                if r.opportunity_id == contact_opp.opportunity_id
            )
            self.assertEqual(decision.response, "DEFER")
            self.assertNotEqual(decision.reason_code, "PRIOR_TERMINAL_RESPONSE")

    def test_invite_insert_and_schedule_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Discover opportunity id first.
            probe, bundle0, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
            )
            invite = next(
                (
                    r
                    for r in probe.social_response.responses
                    if r.opportunity_kind == "INVITE_OPPORTUNITY"
                ),
                None,
            )
            self.assertIsNotNone(invite)
            assert invite is not None
            oid = invite.opportunity_id
            result, bundle, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
                social_facts=_social_facts(
                    availability_windows=[
                        {
                            "availability_id": "a1",
                            "opportunity_id": oid,
                            "earliest_start": "2026-04-01T19:00:00+09:00",
                            "latest_end": "2026-04-01T22:00:00+09:00",
                            "location_id": None,
                            "hard_conflict": False,
                        }
                    ]
                ),
            )
            decision = next(
                r
                for r in result.social_response.responses
                if r.opportunity_id == oid
            )
            self.assertEqual(decision.response, "ACCEPT")
            self.assertTrue(result.social_response.commitment_proposals)
            proposal = result.social_response.commitment_proposals[0]
            cids = [
                c["commitment_id"]
                for c in result.working_bundle.schedule_state["commitments"]
            ]
            self.assertIn(proposal.commitment["commitment_id"], cids)
            refs = result.working_bundle.current_state["domain_refs"]
            self.assertEqual(
                refs["schedule_revision"],
                result.working_bundle.schedule_state["revision"],
            )
            self.assertEqual(
                refs["schedule_hash"],
                result.working_bundle.schedule_state["state_hash"],
            )
            # Input bundle unchanged.
            self.assertEqual(
                bundle.schedule_state["revision"],
                bundle0.schedule_state["revision"],
            )
            terminal = next(
                r
                for r in result.social_response_state.responses
                if r.opportunity_id == oid
            )
            self.assertEqual(terminal.response, "ACCEPT")

    def test_invite_replay_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
            )
            invite = next(
                r
                for r in first.social_response.responses
                if r.opportunity_kind == "INVITE_OPPORTUNITY"
            )
            oid = invite.opportunity_id
            avail = _social_facts(
                availability_windows=[
                    {
                        "availability_id": "a1",
                        "opportunity_id": oid,
                        "earliest_start": "2026-04-01T19:00:00+09:00",
                        "latest_end": "2026-04-01T22:00:00+09:00",
                        "location_id": None,
                        "hard_conflict": False,
                    }
                ]
            )
            accepted, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
                social_facts=avail,
            )
            again, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
                social_state=accepted.social_response_state,
                social_facts=avail,
                # Start from working schedule that already has commitment.
                mutate_schedule=lambda s: dict(accepted.working_bundle.schedule_state),
            )
            # Working schedule still has exactly one matching commitment.
            cid = accepted.social_response.commitment_proposals[0].commitment[
                "commitment_id"
            ]
            matches = [
                c
                for c in again.working_bundle.schedule_state["commitments"]
                if c["commitment_id"] == cid
            ]
            self.assertEqual(len(matches), 1)

    def test_unselected_contact_no_terminal_accept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Force a HARD commitment winner via schedule + high window.
            def _with_hard(schedule: dict) -> dict:
                from engine.life.schedule_state import build_schedule_state

                return build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    commitments=[
                        _commitment(
                            commitment_id="hard-now",
                            kind="WORK",
                            hardness="HARD",
                            timing={
                                "timing_kind": "EXACT",
                                "planned_start": "2026-04-01T12:00:00+09:00",
                                "planned_end": "2026-04-01T13:00:00+09:00",
                            },
                            location_id="fixture-home",
                        )
                    ],
                )

            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                mutate_schedule=_with_hard,
                facts=_decision_facts(
                    available_window_min=180,
                    structural_physical_feasible_by_action=_structural_phys(),
                ),
            )
            contact_decisions = [
                r
                for r in result.social_response.responses
                if r.opportunity_kind == "CONTACT_OPPORTUNITY"
                and r.response == "ACCEPT"
            ]
            if not contact_decisions:
                self.skipTest("contact did not ACCEPT under hard-commitment setup")
            # If hard commitment selected, no pending and no terminal ACCEPT for contact.
            if (
                result.frame.selected_candidate is not None
                and result.frame.selected_candidate.action_kind
                != "SOCIAL_CONTACT"
            ):
                self.assertIsNone(result.pending_immediate_accept)
                for d in contact_decisions:
                    self.assertFalse(
                        any(
                            r.opportunity_id == d.opportunity_id and r.response == "ACCEPT"
                            for r in result.social_response_state.responses
                        )
                    )

    def test_selected_contact_pending_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                facts=_decision_facts(
                    available_window_min=180,
                    household_tasks=[],
                    meal_feasible=False,
                    rest_feasible=False,
                ),
            )
            accepts = [
                r
                for r in result.social_response.responses
                if r.opportunity_kind == "CONTACT_OPPORTUNITY"
                and r.response == "ACCEPT"
            ]
            self.assertTrue(accepts)
            self.assertIsNotNone(result.frame.selected_candidate)
            self.assertEqual(
                result.frame.selected_candidate.action_kind, "SOCIAL_CONTACT"
            )
            self.assertIsNotNone(result.pending_immediate_accept)
            pending = result.pending_immediate_accept
            assert pending is not None
            self.assertEqual(pending.opportunity_id, accepts[0].opportunity_id)
            self.assertEqual(pending.response_id, accepts[0].response_id)
            self.assertEqual(pending.decided_at, AS_OF)
            self.assertEqual(pending.reason_code, accepts[0].reason_code)
            self.assertEqual(pending.decision_key, result.frame.resolution.decision_key)
            self.assertEqual(pending.character_id, CHAR)
            self.assertEqual(pending.person_id, accepts[0].person_id)
            self.assertEqual(pending.opportunity_kind, accepts[0].opportunity_kind)
            self.assertEqual(pending.contact_mode, accepts[0].contact_mode)
            self.assertEqual(pending.duration_min, accepts[0].duration_min)
            self.assertEqual(pending.duration_max, accepts[0].duration_max)
            self.assertIsNotNone(pending.selected_response_hash)
            # Still non-terminal in working SocialResponseState.
            self.assertFalse(
                any(
                    r.opportunity_id == pending.opportunity_id and r.response == "ACCEPT"
                    for r in result.social_response_state.responses
                )
            )
            # Helper terminalizes once after authoritative ActiveActivity start.
            start = _successful_start_from_result(result)
            terminal = apply_pending_immediate_accept_after_successful_start(
                result.social_response_state,
                pending,
                successful_start=start,
            )
            self.assertTrue(
                any(
                    r.opportunity_id == pending.opportunity_id and r.response == "ACCEPT"
                    for r in terminal.responses
                )
            )
            again = apply_pending_immediate_accept_after_successful_start(
                terminal,
                pending,
                successful_start=start,
            )
            self.assertEqual(again.revision, terminal.revision)

    def test_forged_pending_cannot_terminalize_other_opportunity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                facts=_decision_facts(available_window_min=180),
            )
            accepts = [
                r
                for r in result.social_response.responses
                if r.opportunity_kind == "CONTACT_OPPORTUNITY"
                and r.response == "ACCEPT"
            ]
            self.assertTrue(accepts)
            self.assertIsNotNone(result.pending_immediate_accept)
            pending = result.pending_immediate_accept
            assert pending is not None
            forged_oid = "forged-other-opportunity"
            forged = {
                "opportunity_id": forged_oid,
                "response_id": stable_id("social-response", forged_oid, "ACCEPT"),
                "decided_at": pending.decided_at,
                "reason_code": pending.reason_code,
                # Keep original selected keys pointing at the real opportunity.
                "selected_opportunity_key": pending.selected_opportunity_key,
                "selected_candidate_id": pending.selected_candidate_id,
                "selected_candidate_key": pending.selected_candidate_key,
                "decision_key": pending.decision_key,
                "character_id": pending.character_id,
                "person_id": pending.person_id,
                "opportunity_kind": pending.opportunity_kind,
                "contact_mode": pending.contact_mode,
                "duration_min": pending.duration_min,
                "duration_max": pending.duration_max,
                "selected_response_hash": pending.selected_response_hash,
            }
            with self.assertRaises(LifeEngineError):
                parse_pending_immediate_accept(forged)
            # Internally-consistent forged pending for another opportunity.
            forged_key = stable_id("social-resolved-opportunity", forged_oid)
            forged_cand_id = candidate_id_for(
                character_id=CHAR,
                decision_key=pending.decision_key,
                candidate_key=forged_key,
            )
            consistent_forged = parse_pending_immediate_accept(
                {
                    "opportunity_id": forged_oid,
                    "response_id": stable_id("social-response", forged_oid, "ACCEPT"),
                    "decided_at": pending.decided_at,
                    "reason_code": "CONTACT_READY",
                    "selected_opportunity_key": forged_key,
                    "selected_candidate_id": forged_cand_id,
                    "selected_candidate_key": forged_key,
                    "decision_key": pending.decision_key,
                    "character_id": CHAR,
                    "person_id": pending.person_id,
                    "opportunity_kind": pending.opportunity_kind,
                    "contact_mode": pending.contact_mode,
                    "duration_min": pending.duration_min,
                    "duration_max": pending.duration_max,
                    "selected_response_hash": pending.selected_response_hash,
                }
            )
            # Real ActiveActivity from the selected (non-forged) frame must not
            # terminalize the forged opportunity.
            real_start = _successful_start_from_result(result)
            with self.assertRaises(LifeEngineError):
                apply_pending_immediate_accept_after_successful_start(
                    result.social_response_state,
                    consistent_forged,
                    successful_start=real_start,
                )
            self.assertFalse(
                any(
                    r.opportunity_id == forged_oid and r.response == "ACCEPT"
                    for r in result.social_response_state.responses
                )
            )

    def test_consistently_forged_pending_and_start_reject(self) -> None:
        """Both pending and successful_start forged consistently must fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                facts=_decision_facts(available_window_min=180),
            )
            self.assertIsNotNone(result.pending_immediate_accept)
            pending = result.pending_immediate_accept
            assert pending is not None

            forged_oid = "forged-consistent-opportunity"
            decision_key = "forged-decision-key"
            forged_key = stable_id("social-resolved-opportunity", forged_oid)
            forged_cand_id = candidate_id_for(
                character_id=CHAR,
                decision_key=decision_key,
                candidate_key=forged_key,
            )
            forged_activity_id = derive_activity_instance_id(
                character_id=CHAR,
                decision_key=decision_key,
                selected_candidate_id=forged_cand_id,
            )
            forged_pending = parse_pending_immediate_accept(
                {
                    "opportunity_id": forged_oid,
                    "response_id": stable_id("social-response", forged_oid, "ACCEPT"),
                    "decided_at": pending.decided_at,
                    "reason_code": "CONTACT_READY",
                    "selected_opportunity_key": forged_key,
                    "selected_candidate_id": forged_cand_id,
                    "selected_candidate_key": forged_key,
                    "decision_key": decision_key,
                    "character_id": CHAR,
                    "person_id": pending.person_id,
                    "opportunity_kind": pending.opportunity_kind,
                    "contact_mode": pending.contact_mode,
                    "duration_min": pending.duration_min,
                    "duration_max": pending.duration_max,
                    "selected_response_hash": pending.selected_response_hash,
                }
            )

            # Legacy string-only SuccessfulImmediateSocialStart shape rejects.
            with self.assertRaises(LifeEngineError):
                parse_successful_immediate_social_start(
                    {
                        "activity_instance_id": forged_activity_id,
                        "opportunity_id": forged_oid,
                        "selected_opportunity_key": forged_key,
                        "selected_candidate_id": forged_cand_id,
                        "selected_candidate_key": forged_key,
                    }
                )

            # Hand-rolled ActiveActivity with matching SOCIAL bindings but without
            # real 5B1 start provenance (missing decision_evidence / causes).
            forged_activity = {
                "schema_version": 1,
                "activity_instance_id": forged_activity_id,
                "activity_type": "SOCIAL_CONTACT",
                "actual_start": pending.decided_at,
                "planned_end": None,
                "expected_end": None,
                "expected_end_window": None,
                "commitment_id": None,
                "companions": [],
                "effects_on_end": [],
                "pending_captures": [],
                "details": {"contact_mode": "CALL"},
                "runtime_context": {
                    "schema_version": 1,
                    "decision_key": decision_key,
                    "selected_candidate_id": forged_cand_id,
                    "selected_candidate_key": forged_key,
                    "action_kind": "SOCIAL_CONTACT",
                    "priority_tier": "SOCIAL_PROMISE",
                    "source_kind": "SOCIAL",
                    "source_ref": forged_oid,
                    "interruptibility": "REASSESSABLE",
                    "local_preference_permille": None,
                    "rule_ids": ["rule.social"],
                },
            }
            with self.assertRaises(LifeEngineError):
                apply_pending_immediate_accept_after_successful_start(
                    result.social_response_state,
                    forged_pending,
                    successful_start=SuccessfulImmediateSocialStart(
                        active_activity=forged_activity
                    ),
                )

            # Even with forged decision_evidence/causes attached, a non-derived
            # activity_instance_id must fail closed.
            forged_with_evidence = dict(forged_activity)
            forged_with_evidence["activity_instance_id"] = "arbitrary-forged-instance"
            forged_with_evidence["decision_evidence"] = {
                "decision_type": "ACTION_SELECTION",
                "policy_version": POLICY_VERSION,
                "rule_ids": ["rule.social"],
                "input_state_refs": ["state:current"],
                "random_key": "a" * 64,
                "selected_result": forged_cand_id,
            }
            forged_with_evidence["causes"] = [
                {"cause_type": "DECISION", "ref": decision_key},
                {"cause_type": "SOCIAL", "ref": forged_oid},
            ]
            with self.assertRaises(LifeEngineError):
                apply_pending_immediate_accept_after_successful_start(
                    result.social_response_state,
                    forged_pending,
                    successful_start=SuccessfulImmediateSocialStart(
                        active_activity=forged_with_evidence
                    ),
                )
            self.assertFalse(
                any(
                    r.opportunity_id == forged_oid and r.response == "ACCEPT"
                    for r in result.social_response_state.responses
                )
            )

    def test_selected_post_work_pending_handoff(self) -> None:
        evening = "2026-04-01T19:00:00+09:00"
        work = {
            "work_context_id": "work-1",
            "actual_end": "2026-04-01T18:00:00+09:00",
            "finalized_at": "2026-04-01T18:05:00+09:00",
            "participant_person_ids": [RENA],
        }
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_POST_WORK,
                as_of=evening,
                social_facts=_social_facts(
                    post_work_contexts=[work],
                    post_work_location_feasible=True,
                ),
                facts=_decision_facts(available_window_min=180),
            )
            accepts = [
                r
                for r in result.social_response.responses
                if r.opportunity_kind == "POST_WORK_OPPORTUNITY"
                and r.response == "ACCEPT"
            ]
            self.assertTrue(accepts)
            self.assertIsNotNone(result.frame.selected_candidate)
            self.assertEqual(
                result.frame.selected_candidate.action_kind, "SOCIAL_CONTACT"
            )
            self.assertEqual(
                result.frame.selected_candidate.source_ref, accepts[0].opportunity_id
            )
            self.assertIsNotNone(result.pending_immediate_accept)
            pending = result.pending_immediate_accept
            assert pending is not None
            self.assertEqual(pending.opportunity_id, accepts[0].opportunity_id)
            self.assertEqual(pending.reason_code, accepts[0].reason_code)
            self.assertEqual(pending.response_id, accepts[0].response_id)
            self.assertEqual(pending.decision_key, result.frame.resolution.decision_key)
            self.assertFalse(
                any(
                    r.opportunity_id == pending.opportunity_id and r.response == "ACCEPT"
                    for r in result.social_response_state.responses
                )
            )
            terminal = apply_pending_immediate_accept_after_successful_start(
                result.social_response_state,
                pending,
                successful_start=_successful_start_from_result(result),
            )
            self.assertTrue(
                any(
                    r.opportunity_id == pending.opportunity_id and r.response == "ACCEPT"
                    for r in terminal.responses
                )
            )

    def test_single_resolver_includes_social(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, _, _, _ = _run_social(Path(tmp), world_seed=SEED_CONTACT)
            social_keys = {
                o.opportunity_key for o in result.social_response.opportunities
            }
            merged_keys = {o.opportunity_key for o in result.frame.merged_opportunities}
            self.assertTrue(social_keys.issubset(merged_keys))
            raw_ids = tuple(sorted(c.candidate_id for c in result.frame.raw_candidates))
            self.assertEqual(
                raw_ids, result.frame.resolution.considered_candidate_ids
            )

    def test_duplicate_cross_group_opportunity_reject(self) -> None:
        from engine.life.runtime_decision import merge_resolved_opportunities
        from engine.life.decisions import ResolvedOpportunity

        opp = ResolvedOpportunity(
            opportunity_key="dup-key",
            opportunity_class="SOCIAL_PROMISE",
            action_kind="SOCIAL_CONTACT",
            source_kind="SOCIAL",
            source_ref="opp-x",
            soft_candidate=True,
            time_feasible=True,
            location_feasible=True,
            physical_feasible=True,
            domain_guard_satisfied=True,
            local_preference_permille=None,
            rule_ids=("t",),
        )
        with self.assertRaises(LifeEngineError):
            merge_resolved_opportunities([opp], [opp])

    def test_5b2a_no_social_entrypoint_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            bundle = _load_social_bundle(Path(tmp), events=[ev], world_seed="nosocial")
            trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, ev)
            frame = build_runtime_decision_frame(
                bundle=bundle,
                reference_sets=_social_refs(),
                behavior_policy=POLICY,
                trigger=trigger,
                facts=_facts(),
            )
            self.assertTrue(
                all(
                    o.source_kind != "SOCIAL"
                    for o in frame.merged_opportunities
                )
            )

    def test_evidence_extensions_and_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a, _, _, _ = _run_social(Path(tmp), world_seed=SEED_CONTACT)
            b, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                social_facts=_social_facts(hard_commitment_blocking_now=True),
            )
            self.assertNotEqual(
                a.frame.decision_snapshot_hash, b.frame.decision_snapshot_hash
            )
            # Same RuntimeDecisionFacts → same decision_facts_hash even if
            # social snapshot extensions change the full snapshot hash.
            self.assertEqual(a.frame.decision_facts_hash, b.frame.decision_facts_hash)
            self.assertEqual(len(a.frame.decision_facts_hash), 64)
            self.assertEqual(len(a.frame.materialization_context_hash), 64)
            # Same CurrentState/Schedule/policy/facts → same materialization bind.
            self.assertEqual(
                a.frame.materialization_context_hash,
                b.frame.materialization_context_hash,
            )
            self.assertEqual(len(a.frame.input_state_refs), 2)
            joined = "|".join(a.frame.input_state_refs)
            self.assertIn("trigger:", joined)
            self.assertIn("decision-snapshot:", joined)
            self.assertNotIn(str(a.working_bundle.current_state["world_seed"]), joined)

    def test_order_permutation_preserves_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_a = _social_facts()
            facts_b = _social_facts(
                archetype_assignments=list(
                    reversed(list(facts_a["archetype_assignments"]))
                )
            )
            a, _, _, _ = _run_social(
                Path(tmp), world_seed=SEED_BOTH, social_facts=facts_a
            )
            b, _, _, _ = _run_social(
                Path(tmp), world_seed=SEED_BOTH, social_facts=facts_b
            )
            self.assertEqual(
                a.frame.decision_snapshot_hash, b.frame.decision_snapshot_hash
            )
            self.assertEqual(a.frame.decision_facts_hash, b.frame.decision_facts_hash)

    def test_invite_insert_failure_leaves_response_state_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, _, _, _ = _run_social(
                Path(tmp), world_seed=SEED_SAME_DAY_INVITE
            )
            invite = next(
                r
                for r in probe.social_response.responses
                if r.opportunity_kind == "INVITE_OPPORTUNITY"
            )
            oid = invite.opportunity_id
            cid = stable_id("social-commitment", oid)
            conflicting = _commitment(
                commitment_id=cid,
                kind="SOCIAL",
                hardness="SOFT",
                timing={
                    "timing_kind": "WINDOW",
                    "earliest_start": "2026-04-01T10:00:00+09:00",
                    "latest_start": "2026-04-01T11:00:00+09:00",
                    "duration_min": 60,
                    "duration_max": 90,
                },
                location_id=None,
                participants=[SAYAKA],
                source_kind="EXOGENOUS_SOCIAL",
            )

            def _seed_conflict(schedule: dict) -> dict:
                from engine.life.schedule_state import build_schedule_state

                return build_schedule_state(
                    character_id=CHAR,
                    as_of=AS_OF,
                    commitments=[conflicting],
                )

            pre_state = build_social_response_state(character_id=CHAR, as_of=AS_OF)
            with self.assertRaises(LifeEngineError):
                _run_social(
                    Path(tmp),
                    world_seed=SEED_SAME_DAY_INVITE,
                    social_state=pre_state,
                    mutate_schedule=_seed_conflict,
                    social_facts=_social_facts(
                        availability_windows=[
                            {
                                "availability_id": "a1",
                                "opportunity_id": oid,
                                "earliest_start": "2026-04-01T19:00:00+09:00",
                                "latest_end": "2026-04-01T22:00:00+09:00",
                                "location_id": None,
                                "hard_conflict": False,
                            }
                        ]
                    ),
                )
            # Input state object unchanged (apply path fails before ACCEPT).
            self.assertEqual(pre_state.responses, ())
            self.assertEqual(pre_state.revision, build_social_response_state(
                character_id=CHAR, as_of=AS_OF
            ).revision)

    def test_multi_invite_proposal_order_invariant(self) -> None:
        """Accepted invite proposals are applied in canonical opportunity_id order."""
        gen = generate_historical_social_opportunities(
            character_id=CHAR,
            world_seed=SEED_MULTI_INVITE,
            as_of=AS_OF,
            behavior_policy=POLICY,
            known_person_ids=SOCIAL_KNOWN,
            assignments=default_assignments(),
        )
        invites = [
            o
            for o in gen.opportunities
            if o.opportunity_kind == "INVITE_OPPORTUNITY"
        ]
        self.assertGreaterEqual(len(invites), 2)
        windows = [
            {
                "availability_id": f"a{i}",
                "opportunity_id": inv.opportunity_id,
                "earliest_start": f"{inv.target_local_date}T19:00:00+09:00",
                "latest_end": f"{inv.target_local_date}T21:00:00+09:00",
                "location_id": None,
                "hard_conflict": False,
            }
            for i, inv in enumerate(invites)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            a, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_MULTI_INVITE,
                social_facts=_social_facts(availability_windows=windows),
            )
            b, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_MULTI_INVITE,
                social_facts=_social_facts(
                    availability_windows=list(reversed(windows))
                ),
            )
            a_accepts = [
                r
                for r in a.social_response.responses
                if r.opportunity_kind == "INVITE_OPPORTUNITY" and r.response == "ACCEPT"
            ]
            self.assertGreaterEqual(len(a_accepts), 2)
            self.assertEqual(len(a.social_response.commitment_proposals), len(a_accepts))
            self.assertEqual(
                [
                    p.commitment["commitment_id"]
                    for p in a.social_response.commitment_proposals
                ],
                [
                    p.commitment["commitment_id"]
                    for p in b.social_response.commitment_proposals
                ],
            )
            self.assertEqual(
                a.working_bundle.schedule_state["revision"],
                b.working_bundle.schedule_state["revision"],
            )
            self.assertEqual(
                a.working_bundle.schedule_state["state_hash"],
                b.working_bundle.schedule_state["state_hash"],
            )
            self.assertEqual(
                a.social_response_state.revision,
                b.social_response_state.revision,
            )
            self.assertEqual(
                [(r.opportunity_id, r.response) for r in a.social_response_state.responses],
                [(r.opportunity_id, r.response) for r in b.social_response_state.responses],
            )

    def test_social_state_semantic_change_binds_snapshot_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gen = generate_historical_social_opportunities(
                character_id=CHAR,
                world_seed=SEED_CONTACT,
                as_of=AS_OF,
                behavior_policy=POLICY,
                known_person_ids=SOCIAL_KNOWN,
                assignments=default_assignments(),
                contacts=[
                    contact(
                        actual_end="2026-03-20T12:00:00+09:00",
                        finalized_at="2026-03-20T12:05:00+09:00",
                    )
                ],
            )
            contact_opp = next(
                o
                for o in gen.opportunities
                if o.opportunity_kind == "CONTACT_OPPORTUNITY"
            )
            empty = build_social_response_state(character_id=CHAR, as_of=AS_OF)
            deferred = build_social_response_state(
                character_id=CHAR,
                as_of=AS_OF,
                responses=[
                    {
                        "response_id": stable_id(
                            "social-response",
                            contact_opp.opportunity_id,
                            "DEFER",
                        ),
                        "opportunity_id": contact_opp.opportunity_id,
                        "response": "DEFER",
                        "decided_at": AS_OF,
                        "reason_code": "HARD_COMMITMENT_BLOCKING",
                    }
                ],
            )
            a, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                social_state=empty,
                social_facts=_social_facts(hard_commitment_blocking_now=True),
            )
            b, _, _, _ = _run_social(
                Path(tmp),
                world_seed=SEED_CONTACT,
                social_state=deferred,
                social_facts=_social_facts(hard_commitment_blocking_now=True),
            )
            self.assertNotEqual(
                a.frame.decision_snapshot_hash, b.frame.decision_snapshot_hash
            )

    def test_invite_schedule_mutation_binds_snapshot_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, _, _, _ = _run_social(
                Path(tmp), world_seed=SEED_SAME_DAY_INVITE
            )
            oid = next(
                r.opportunity_id
                for r in probe.social_response.responses
                if r.opportunity_kind == "INVITE_OPPORTUNITY"
            )
            without = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
                social_facts=_social_facts(),  # missing availability → no insert
            )[0]
            with_avail = _run_social(
                Path(tmp),
                world_seed=SEED_SAME_DAY_INVITE,
                social_facts=_social_facts(
                    availability_windows=[
                        {
                            "availability_id": "a1",
                            "opportunity_id": oid,
                            "earliest_start": "2026-04-01T19:00:00+09:00",
                            "latest_end": "2026-04-01T22:00:00+09:00",
                            "location_id": None,
                            "hard_conflict": False,
                        }
                    ]
                ),
            )[0]
            self.assertTrue(with_avail.social_response.commitment_proposals)
            self.assertNotEqual(
                without.working_bundle.schedule_state["revision"],
                with_avail.working_bundle.schedule_state["revision"],
            )
            self.assertNotEqual(
                without.frame.decision_snapshot_hash,
                with_avail.frame.decision_snapshot_hash,
            )

    def test_no_relation_or_revision_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result, bundle, _, _ = _run_social(Path(tmp), world_seed=SEED_CONTACT)
            self.assertEqual(
                canonical_json(result.working_bundle.relation_state),
                canonical_json(bundle.relation_state),
            )
            self.assertEqual(
                result.working_bundle.current_state["state_revision"],
                bundle.current_state["state_revision"],
            )
            self.assertEqual(
                len(result.working_bundle.current_state["pending_queue"]["events"]),
                len(bundle.current_state["pending_queue"]["events"]),
            )
