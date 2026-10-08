"""Timeline append, closure, and history-chain persistence integration tests."""

from __future__ import annotations

import tempfile
import unittest

import pytest
from pathlib import Path
from unittest import mock

from engine.life.checkpoint import validate_checkpoint
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.history import fold_history
from engine.life.runtime_bundle import RuntimeBundle
from engine.life.runtime_persistence import write_runtime_json
from engine.life.timeline import close_day, logical_closed_at, should_close_day
from engine.life.timeutil import format_rfc3339, parse_rfc3339
from tests.support.builders.persistence import (
    _refs,
    build_5c1_persistent_root,
    build_actual_event,
    clone_advance_result,
    day_with_events,
    fingerprint,
    make_c3_exact_end_root,
    make_c3_noop_root,
    read_json,
    real_advance,
    run_candidate,
)


pytestmark = pytest.mark.integration

class PersistenceTimelineTests(unittest.TestCase):
    def test_38_event_append_exact_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            self.assertEqual(result.status, "CHANGE")
            event = result.runtime_result.actual_events[0]
            day_key = event["actual_start"][:10]
            day = result.candidate_snapshot.timeline_days[day_key]
            self.assertEqual(day["actual_events"][0]["event_id"], event["event_id"])
            self.assertEqual(day["date"], day_key)

    def test_39_multiple_events_ordered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e1 = build_actual_event(
                activity_instance_id="activity:5c1-a",
                actual_start="2026-04-01T09:00:00+09:00",
                actual_end="2026-04-01T10:00:00+09:00",
            )
            e2 = build_actual_event(
                activity_instance_id="activity:5c1-b",
                actual_start="2026-04-01T10:00:00+09:00",
                actual_end="2026-04-01T11:00:00+09:00",
            )
            day = day_with_events(e1)
            root, social, current, inputs = make_c3_noop_root(Path(tmp) / "r2")
            write_runtime_json(root / "timeline/2026-04-01.json", day)
            cur = read_json(root / "state/current.json")
            cur["history"] = {
                "head_event_id": e1["event_id"],
                "history_hash": fold_history([e1]),
                "event_count": 1,
            }
            write_runtime_json(root / "state/current.json", cur)

            def _handoff(**kwargs):
                real = real_advance(**kwargs)
                cur2 = dict(real.bundle.current_state)
                cur2["history"] = {
                    "head_event_id": e2["event_id"],
                    "history_hash": fold_history([e1, e2]),
                    "event_count": 2,
                }
                # Force semantic CHANGE via processed bump already present or history.
                cur2 = validate_checkpoint(cur2)
                bundle = RuntimeBundle(
                    current_state=cur2,
                    schedule_state=real.bundle.schedule_state,
                    relation_state=real.bundle.relation_state,
                    home_state=real.bundle.home_state,
                    consumables_state=real.bundle.consumables_state,
                    wardrobe_state=real.bundle.wardrobe_state,
                    finance_state=real.bundle.finance_state,
                )
                return clone_advance_result(
                    real, bundle=bundle, actual_events=[e2], microsteps_used=1
                )

            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=_handoff,
            ):
                result = run_candidate(root, target_inputs=inputs)
            ids = [
                e["event_id"]
                for e in result.candidate_snapshot.timeline_days["2026-04-01"][
                    "actual_events"
                ]
            ]
            self.assertEqual(ids, [e1["event_id"], e2["event_id"]])

    def test_40_duplicate_baseline_event_id_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e1 = build_actual_event()
            day = day_with_events(e1)
            root, social, current, inputs = make_c3_noop_root(tmp)
            write_runtime_json(root / "timeline/2026-04-01.json", day)
            cur = read_json(root / "state/current.json")
            cur["history"] = {
                "head_event_id": e1["event_id"],
                "history_hash": fold_history([e1]),
                "event_count": 1,
            }
            write_runtime_json(root / "state/current.json", cur)
            before = fingerprint(root)

            def _fabricate(kwargs, *, actual_events):
                from engine.life.runtime_orchestrator import RuntimeTargetAdvanceResult

                return RuntimeTargetAdvanceResult(
                    bundle=kwargs["bundle"],
                    social_response_state=kwargs["social_response_state"],
                    actual_events=tuple(dict(e) for e in actual_events),
                    camera_roll_records=(),
                    camera_roll_records_by_shard_path={},
                    consumed_source_keys=(),
                    consumed_trigger_ids=(),
                    decision_frames=(),
                    microsteps_used=1,
                )

            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=lambda **kw: _fabricate(kw, actual_events=[e1]),
            ):
                with self.assertRaises(LifeEngineError) as ctx:
                    run_candidate(root, target_inputs=inputs)
            self.assertIn("duplicate baseline event_id", ctx.exception.detail)
            self.assertEqual(fingerprint(root), before)

    def test_41_closed_day_new_event_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e1 = build_actual_event(
                actual_start="2026-03-31T10:00:00+09:00",
                actual_end="2026-03-31T11:00:00+09:00",
            )
            day = day_with_events(e1, date="2026-03-31", status="CLOSED")
            root, social, current, inputs = make_c3_noop_root(tmp)
            write_runtime_json(root / "timeline/2026-03-31.json", day)
            cur = read_json(root / "state/current.json")
            cur["history"] = {
                "head_event_id": e1["event_id"],
                "history_hash": fold_history([e1]),
                "event_count": 1,
            }
            write_runtime_json(root / "state/current.json", cur)
            e2 = build_actual_event(
                activity_instance_id="activity:5c1-new",
                actual_start="2026-03-31T12:00:00+09:00",
                actual_end="2026-03-31T13:00:00+09:00",
            )
            before = fingerprint(root)

            def _fabricate(kwargs, *, actual_events):
                from engine.life.runtime_orchestrator import RuntimeTargetAdvanceResult

                return RuntimeTargetAdvanceResult(
                    bundle=kwargs["bundle"],
                    social_response_state=kwargs["social_response_state"],
                    actual_events=tuple(dict(e) for e in actual_events),
                    camera_roll_records=(),
                    camera_roll_records_by_shard_path={},
                    consumed_source_keys=(),
                    consumed_trigger_ids=(),
                    decision_frames=(),
                    microsteps_used=1,
                )

            with mock.patch(
                "engine.life.runtime_persistence.advance_runtime_to_target",
                side_effect=lambda **kw: _fabricate(kw, actual_events=[e2]),
            ):
                with self.assertRaises(LifeEngineError) as ctx:
                    run_candidate(root, target_inputs=inputs)
            self.assertEqual(ctx.exception.code, ErrorCode.CLOSED_DAY_MUTATION)
            self.assertEqual(fingerprint(root), before)

    def test_42_no_prior_event_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            prior = build_actual_event(
                activity_instance_id="activity:5c1-prior",
                actual_start="2026-04-01T08:00:00+09:00",
                actual_end="2026-04-01T09:00:00+09:00",
            )
            day = day_with_events(prior)
            write_runtime_json(root / "timeline/2026-04-01.json", day)
            cur = read_json(root / "state/current.json")
            cur["history"] = {
                "head_event_id": prior["event_id"],
                "history_hash": fold_history([prior]),
                "event_count": 1,
            }
            write_runtime_json(root / "state/current.json", cur)
            result = run_candidate(root, target_inputs=inputs)
            cand_day = result.candidate_snapshot.timeline_days["2026-04-01"]
            self.assertEqual(cand_day["actual_events"][0], prior)

    def test_43_overnight_activity_day_remains_open_until_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs = make_c3_noop_root(tmp)
            event = build_actual_event(
                actual_start="2026-04-01T22:00:00+09:00",
                actual_end="2026-04-02T02:00:00+09:00",
            )
            day = day_with_events(event)
            self.assertEqual(day["status"], "OPEN")
            state = read_json(root / "state/current.json")
            as_of = parse_rfc3339("2026-04-03T12:00:00+09:00")
            state["context"]["active_activity"] = {
                "schema_version": 1,
                "activity_instance_id": "activity:open",
                "activity_type": "LEISURE",
                "actual_start": "2026-04-01T20:00:00+09:00",
                "location_id": "fixture-home",
                "summary": "open",
            }
            self.assertFalse(should_close_day(state, day, as_of=as_of))

    def test_44_deterministic_close_after_resolution(self) -> None:
        event = build_actual_event(
            actual_start="2026-03-31T10:00:00+09:00",
            actual_end="2026-03-31T11:00:00+09:00",
        )
        day = day_with_events(event, date="2026-03-31", status="OPEN")
        closed_a = close_day(day)
        closed_b = close_day(day)
        self.assertEqual(closed_a, closed_b)
        self.assertEqual(closed_a["status"], "CLOSED")
        self.assertEqual(closed_a["closed_at"], format_rfc3339(logical_closed_at(day)))

    def test_45_logical_closed_at_reused(self) -> None:
        event = build_actual_event(
            actual_start="2026-04-01T22:00:00+09:00",
            actual_end="2026-04-02T02:00:00+09:00",
        )
        day = day_with_events(event)
        instant = logical_closed_at(day)
        closed = close_day(day)
        self.assertEqual(closed["closed_at"], format_rfc3339(instant))
        self.assertEqual(closed["closed_at"], event["actual_end"])

    def test_46_candidate_history_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, social, current, inputs, *_ = make_c3_exact_end_root(tmp)
            result = run_candidate(root, target_inputs=inputs)
            hist = result.candidate_snapshot.bundle.current_state["history"]
            events = []
            for day in sorted(
                result.candidate_snapshot.timeline_days.values(), key=lambda d: d["date"]
            ):
                events.extend(day["actual_events"])
            self.assertEqual(hist["event_count"], len(events))
            self.assertEqual(hist["head_event_id"], events[-1]["event_id"])
            self.assertEqual(hist["history_hash"], fold_history(events))
