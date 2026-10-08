"""Core domain contracts for commitments, tasks, routes, social assignments, and events."""

from __future__ import annotations

import unittest

import pytest

from engine.life.canonical import persisted_hash
from engine.life.contracts import (
    validate_commitment,
    validate_obligation_task,
    validate_route_profile,
    validate_social_archetype_assignment,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.schema import validate_instance

pytestmark = pytest.mark.contract


def _prov(**overrides) -> dict:
    base = {"origin": "ENGINE_DEFAULT", "notes": "synthetic"}
    base.update(overrides)
    return base


def _hist(*entries) -> list:
    return list(entries)


def _exact_commitment(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "commitment_id": "cmt-exact-1",
        "kind": "CLASS",
        "hardness": "HARD",
        "created_at": "2026-01-01T08:00:00+09:00",
        "timing": {
            "timing_kind": "EXACT",
            "planned_start": "2026-01-01T10:00:00+09:00",
            "planned_end": "2026-01-01T11:30:00+09:00",
        },
        "location_id": "fixture-campus",
        "participants": ["fixture-character"],
        "source_kind": "INTERNAL",
        "provenance": _prov(),
        "recurrence_id": None,
        "status": "PLANNED",
        "status_history": _hist(
            {
                "changed_at": "2026-01-01T08:00:00+09:00",
                "from": None,
                "to": "PLANNED",
                "reason": "created",
                "source_ref": "engine:create",
            }
        ),
    }
    base.update(overrides)
    return base


def _window_commitment(**overrides) -> dict:
    base = _exact_commitment(
        commitment_id="cmt-window-1",
        kind="SOCIAL",
        hardness="SOFT",
        source_kind="EXOGENOUS_SOCIAL",
        timing={
            "timing_kind": "WINDOW",
            "earliest_start": "2026-01-01T18:00:00+09:00",
            "latest_start": "2026-01-01T20:00:00+09:00",
            "duration_min": 60,
            "duration_max": 120,
        },
        location_id=None,
        participants=["fixture-character", "fixture-friend-a"],
    )
    base.update(overrides)
    return base


def _obligation(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "task_id": "task-1",
        "domain": "ACADEMIC",
        "created_at": "2026-01-01T09:00:00+09:00",
        "earliest_start": None,
        "due_at": "2026-01-08T23:59:00+09:00",
        "due_window": None,
        "effort_remaining_min": 120,
        "min_work_chunk_min": 30,
        "priority": "NORMAL",
        "location_constraints": ["fixture-desk"],
        "status": "OPEN",
        "provenance": _prov(),
        "source_kind": "INTERNAL",
        "caused_by_event_id": None,
    }
    base.update(overrides)
    return base


def _route(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "route_profile_id": "route-home-campus",
        "origin_location_id": "fixture-home",
        "destination_location_id": "fixture-campus",
        "mode": "PUBLIC_TRANSIT",
        "duration_min": 25,
        "duration_max": 45,
        "effort_class": "LIGHT_ACTIVE",
        "provenance": _prov(),
    }
    base.update(overrides)
    return base


def _event(event_type: str, details, **overrides) -> dict:
    base = {
        "schema_version": 1,
        "event_id": "evt-1",
        "activity_instance_id": "act-1",
        "event_type": event_type,
        "actual_start": "2026-01-01T10:00:00+09:00",
        "actual_end": "2026-01-01T10:30:00+09:00",
        "finalized_at": "2026-01-01T10:30:00+09:00",
        "captures": [],
        "provenance": {"origin": "ACTUAL_EVENT"},
        "details": details,
    }
    base.update(overrides)
    return base


class CommitmentTests(unittest.TestCase):
    def test_exact_valid(self):
        out = validate_commitment(_exact_commitment())
        self.assertTrue(out["created_at"].endswith("+09:00"))

    def test_window_valid(self):
        validate_commitment(_window_commitment())

    def test_utc_vs_tokyo_canonical(self):
        raw = _exact_commitment(
            created_at="2025-12-31T23:00:00Z",
            status_history=_hist(
                {
                    "changed_at": "2025-12-31T23:00:00Z",
                    "from": None,
                    "to": "PLANNED",
                    "reason": "created",
                    "source_ref": "engine:create",
                }
            ),
        )
        out = validate_commitment(raw)
        self.assertEqual(out["created_at"], "2026-01-01T08:00:00+09:00")
        self.assertEqual(out["status_history"][0]["changed_at"], "2026-01-01T08:00:00+09:00")
        self.assertEqual(
            persisted_hash(out),
            persisted_hash(
                _exact_commitment(
                    created_at="2026-01-01T08:00:00+09:00",
                )
            ),
        )

    def test_subsecond_and_naive_rejected(self):
        with self.assertRaises(LifeEngineError):
            validate_commitment(_exact_commitment(created_at="2026-01-01T08:00:00.5+09:00"))
        with self.assertRaises(LifeEngineError):
            validate_commitment(_exact_commitment(created_at="2026-01-01T08:00:00"))

    def test_participants_order_independent_hash(self):
        a = validate_commitment(
            _window_commitment(participants=["fixture-friend-a", "fixture-character"])
        )
        b = validate_commitment(
            _window_commitment(participants=["fixture-character", "fixture-friend-a"])
        )
        self.assertEqual(persisted_hash(a), persisted_hash(b))

    def test_bad_time_order_invalid(self):
        bad = _exact_commitment()
        bad["timing"]["planned_end"] = "2026-01-01T09:00:00+09:00"
        with self.assertRaises(LifeEngineError) as ctx:
            validate_commitment(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_non_monotonic_status_history_invalid(self):
        bad = _exact_commitment(
            status="COMPLETED",
            status_history=_hist(
                {
                    "changed_at": "2026-01-01T08:00:00+09:00",
                    "from": None,
                    "to": "PLANNED",
                    "reason": "created",
                    "source_ref": "engine:create",
                },
                {
                    "changed_at": "2026-01-01T07:00:00+09:00",
                    "from": "PLANNED",
                    "to": "COMPLETED",
                    "reason": "done",
                    "source_ref": "engine:complete",
                },
            ),
        )
        with self.assertRaises(LifeEngineError) as ctx:
            validate_commitment(bad)
        self.assertIn("monotonic", ctx.exception.detail)

    def test_transition_continuity(self):
        bad = _exact_commitment(
            status="COMPLETED",
            status_history=_hist(
                {
                    "changed_at": "2026-01-01T08:00:00+09:00",
                    "from": None,
                    "to": "PLANNED",
                    "reason": "created",
                    "source_ref": "engine:create",
                },
                {
                    "changed_at": "2026-01-01T09:00:00+09:00",
                    "from": "CANCELLED",
                    "to": "COMPLETED",
                    "reason": "bad",
                    "source_ref": "engine:x",
                },
            ),
        )
        with self.assertRaises(LifeEngineError) as ctx:
            validate_commitment(bad)
        self.assertIn("prior to", ctx.exception.detail)

    def test_latest_vs_current_mismatch(self):
        bad = _exact_commitment(status="CANCELLED")
        with self.assertRaises(LifeEngineError) as ctx:
            validate_commitment(bad)
        self.assertIn("latest status_history", ctx.exception.detail)

    def test_duplicate_participants(self):
        bad = _exact_commitment(participants=["a", "a"])
        with self.assertRaises(LifeEngineError) as ctx:
            validate_commitment(bad)
        self.assertIn("duplicate", ctx.exception.detail)

    def test_historical_creation_guard(self):
        bad = _exact_commitment(created_at="2026-01-01T12:00:00+09:00")
        bad["status_history"][0]["changed_at"] = "2026-01-01T12:00:00+09:00"
        with self.assertRaises(LifeEngineError) as ctx:
            validate_commitment(bad)
        self.assertIn("historical", ctx.exception.detail)

        ok = _exact_commitment(
            created_at="2026-01-01T12:00:00+09:00",
            provenance=_prov(origin="SIMULATION_BOOTSTRAP"),
            status_history=_hist(
                {
                    "changed_at": "2026-01-01T12:00:00+09:00",
                    "from": None,
                    "to": "PLANNED",
                    "reason": "bootstrap_import",
                    "source_ref": "bootstrap:import",
                }
            ),
        )
        validate_commitment(ok)

    def test_provenance_media_not_source_kind(self):
        bad = _exact_commitment(source_kind="FILE")
        with self.assertRaises(LifeEngineError) as ctx:
            validate_commitment(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)


class ObligationTaskTests(unittest.TestCase):
    def test_due_at_valid(self):
        validate_obligation_task(_obligation())

    def test_due_window_valid(self):
        validate_obligation_task(
            _obligation(
                due_at=None,
                due_window={
                    "earliest_due": "2026-01-07T00:00:00+09:00",
                    "latest_due": "2026-01-08T23:59:00+09:00",
                },
            )
        )

    def test_both_due_invalid(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_obligation_task(
                _obligation(
                    due_window={
                        "earliest_due": "2026-01-07T00:00:00+09:00",
                        "latest_due": "2026-01-08T23:59:00+09:00",
                    }
                )
            )
        self.assertIn("mutually exclusive", ctx.exception.detail)

    def test_no_deadline_both_null_allowed(self):
        validate_obligation_task(_obligation(due_at=None, due_window=None))

    def test_bad_window(self):
        with self.assertRaises(LifeEngineError):
            validate_obligation_task(
                _obligation(
                    due_at=None,
                    due_window={
                        "earliest_due": "2026-01-09T00:00:00+09:00",
                        "latest_due": "2026-01-08T00:00:00+09:00",
                    },
                )
            )

    def test_negative_effort(self):
        bad = _obligation(effort_remaining_min=-1)
        with self.assertRaises(LifeEngineError) as ctx:
            validate_obligation_task(bad)
        self.assertIn(
            ctx.exception.code,
            {ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE},
        )

    def test_chunk_exceeds_remaining_open(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_obligation_task(
                _obligation(status="OPEN", effort_remaining_min=10, min_work_chunk_min=30)
            )
        self.assertIn("min_work_chunk_min", ctx.exception.detail)

    def test_open_zero_effort_invalid(self):
        with self.assertRaises(LifeEngineError):
            validate_obligation_task(
                _obligation(status="OPEN", effort_remaining_min=0, min_work_chunk_min=1)
            )

    def test_unknown_field(self):
        bad = _obligation()
        bad["household_promise"] = True
        with self.assertRaises(LifeEngineError) as ctx:
            validate_obligation_task(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_done_effort_consistency(self):
        with self.assertRaises(LifeEngineError):
            validate_obligation_task(_obligation(status="DONE", effort_remaining_min=10))
        validate_obligation_task(_obligation(status="DONE", effort_remaining_min=0))

    def test_cancelled_may_keep_estimate(self):
        validate_obligation_task(
            _obligation(status="CANCELLED", effort_remaining_min=45, min_work_chunk_min=30)
        )

    def test_earliest_after_deadline_invalid(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_obligation_task(
                _obligation(
                    earliest_start="2026-01-09T00:00:00+09:00",
                    due_at="2026-01-08T23:59:00+09:00",
                )
            )
        self.assertIn("deadline", ctx.exception.detail)

    def test_utc_due_canonical(self):
        out = validate_obligation_task(_obligation(due_at="2026-01-08T14:59:00Z"))
        self.assertEqual(out["due_at"], "2026-01-08T23:59:00+09:00")

    def test_location_constraints_order_independent(self):
        a = validate_obligation_task(
            _obligation(location_constraints=["b-room", "a-desk"])
        )
        b = validate_obligation_task(
            _obligation(location_constraints=["a-desk", "b-room"])
        )
        self.assertEqual(persisted_hash(a), persisted_hash(b))


class RouteProfileTests(unittest.TestCase):
    def test_valid(self):
        validate_route_profile(_route())

    def test_zero_duration(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_route_profile(_route(duration_min=0, duration_max=10))
        self.assertIn(
            ctx.exception.code,
            {ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE},
        )

    def test_reversed_min_max(self):
        with self.assertRaises(LifeEngineError):
            validate_route_profile(_route(duration_min=50, duration_max=10))

    def test_origin_equals_destination(self):
        with self.assertRaises(LifeEngineError) as ctx:
            validate_route_profile(
                _route(
                    origin_location_id="same",
                    destination_location_id="same",
                )
            )
        self.assertIn("differ", ctx.exception.detail)

    def test_gps_address_extra_rejected(self):
        bad = _route()
        bad["gps"] = {"lat": 1, "lon": 2}
        with self.assertRaises(LifeEngineError) as ctx:
            validate_route_profile(bad)
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)
        bad2 = _route()
        bad2["address"] = "1-2-3 Somewhere"
        with self.assertRaises(LifeEngineError):
            validate_route_profile(bad2)


class EventDetailsTests(unittest.TestCase):
    def test_sleep_main_nap(self):
        validate_instance(_event("SLEEP", {"sleep_kind": "MAIN"}), "actual_event")
        validate_instance(_event("SLEEP", {"sleep_kind": "NAP"}), "actual_event")

    def test_meal(self):
        validate_instance(
            _event(
                "MEAL",
                {"meal_source": "HOME_COOKED", "meal_extent": "STANDARD"},
            ),
            "actual_event",
        )

    def test_social_contact(self):
        validate_instance(
            _event("SOCIAL_CONTACT", {"contact_mode": "CALL"}),
            "actual_event",
        )

    def test_slice2_types_require_details(self):
        for et in ("SLEEP", "MEAL", "SOCIAL_CONTACT"):
            with self.assertRaises(LifeEngineError):
                validate_instance(_event(et, None), "actual_event")
            omitted = _event(et, {"sleep_kind": "MAIN"} if et == "SLEEP" else None)
            if et == "MEAL":
                omitted["details"] = {
                    "meal_source": "HOME_COOKED",
                    "meal_extent": "LIGHT",
                }
            elif et == "SOCIAL_CONTACT":
                omitted["details"] = {"contact_mode": "LIGHTWEIGHT"}
            del omitted["details"]
            with self.assertRaises(LifeEngineError):
                validate_instance(omitted, "actual_event")

    def test_wrong_pairing(self):
        with self.assertRaises(LifeEngineError):
            validate_instance(
                _event("SLEEP", {"meal_source": "OUTSIDE", "meal_extent": "LIGHT"}),
                "actual_event",
            )
        with self.assertRaises(LifeEngineError):
            validate_instance(
                _event("MEAL", {"sleep_kind": "MAIN"}),
                "actual_event",
            )

    def test_arbitrary_metadata_rejected(self):
        with self.assertRaises(LifeEngineError):
            validate_instance(
                _event("SLEEP", {"sleep_kind": "MAIN", "notes": "nope"}),
                "actual_event",
            )

    def test_foundation_details_null(self):
        validate_instance(_event("IDLE", None), "actual_event")
        ev = _event("IDLE", None)
        del ev["details"]
        validate_instance(ev, "actual_event")


class SocialArchetypeTests(unittest.TestCase):
    def test_valid_synthetic(self):
        validate_social_archetype_assignment(
            {
                "schema_version": 1,
                "person_id": "fixture-friend-a",
                "archetype_id": "REMOTE_CLOSE_BURSTY",
                "provenance": _prov(),
            }
        )

    def test_unknown_archetype(self):
        with self.assertRaises(LifeEngineError):
            validate_social_archetype_assignment(
                {
                    "schema_version": 1,
                    "person_id": "fixture-friend-a",
                    "archetype_id": "NOT_A_REAL_ARCHETYPE",
                    "provenance": _prov(),
                }
            )
