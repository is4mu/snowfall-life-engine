"""RuntimeDecisionFrame trigger, fact binding, resolution, and evidence integration tests."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from engine.life.activity_runtime import (
    ActivityStartSpec,
    start_activity_from_selection,
)
from engine.life.canonical import canonical_hash, canonical_json
from engine.life.checkpoint import empty_checkpoint, validate_checkpoint
from engine.life.decisions import (
    ActionCandidate,
    DecisionResolution,
    ResolvedOpportunity,
    candidate_id_for,
)
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.ids import stable_id
from engine.life.policy import hash_policy, load_policy
from engine.life.runtime_bundle import load_runtime_bundle
from engine.life.runtime_decision import (
    RuntimeDecisionFacts,
    RuntimeDecisionTrigger,
    bind_runtime_trigger_to_queued_wakeup,
    build_decision_facts_hash,
    build_runtime_decision_frame,
    decision_boundary_from_runtime_trigger,
    derive_managed_v2_decision_wakeup_event_id,
    derive_runtime_decision_key,
    merge_known_decision_boundaries,
    merge_resolved_opportunities,
    parse_runtime_decision_facts,
    parse_runtime_decision_trigger,
    runtime_trigger_from_v2_wakeup,
)
from engine.life.schedule_state import build_schedule_state
from engine.life.wakeups import (
    FutureCandidate,
    KnownDecisionBoundary,
    build_v2_decision_wakeup_event,
)

from tests.support.builders.runtime_decision import (
    AS_OF,
    CAMPUS,
    CHAR,
    HOME,
    POLICY,
    POLICY_VERSION,
    _facts,
    _frame_for,
    _household,
    _load_bundle,
    _refs,
    _route,
    _started_active,
    _threshold_wakeup,
    _v2_wakeup,
)
from tests.support.builders.schedule import commitment as _commitment, task as _task
from tests.support.constants import POLICY_PATH, POLICY_V2_CANDIDATE_PATH

import pytest

pytestmark = pytest.mark.integration


class RuntimeDecisionTriggerTests(unittest.TestCase):
    def test_01_valid_v2_queued_wakeup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, bundle, trigger, ev = _frame_for(Path(tmp))
            self.assertEqual(trigger.trigger_id, ev["event_id"])
            self.assertEqual(trigger.occurred_at, AS_OF)
            self.assertEqual(trigger.semantic_decision_key, "idle:fixture")
            self.assertEqual(frame.trigger.trigger_id, ev["event_id"])

    def test_02_queue_membership_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            bundle = _load_bundle(Path(tmp), events=[])
            with self.assertRaises(LifeEngineError) as ctx:
                runtime_trigger_from_v2_wakeup(bundle.current_state, ev)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)

    def test_03_due_equals_processed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup(due_at="2026-04-01T13:00:00+09:00")
            bundle = _load_bundle(Path(tmp), events=[ev])
            with self.assertRaises(LifeEngineError):
                runtime_trigger_from_v2_wakeup(bundle.current_state, ev)

    def test_04_v1_decision_wakeup_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = {
                "schema_version": 1,
                "event_id": "v1-decision-wakeup:x",
                "event_kind": "DECISION_WAKEUP",
                "due_at": AS_OF,
                "priority": 50,
                "payload": {"reason": "idle", "decision_key": "idle:v1"},
            }
            bundle = _load_bundle(Path(tmp), events=[ev])
            with self.assertRaises(LifeEngineError):
                runtime_trigger_from_v2_wakeup(bundle.current_state, ev)

    def test_05_non_decision_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = {
                "schema_version": 1,
                "event_id": "idle-1",
                "event_kind": "IDLE_REASSESSMENT",
                "due_at": AS_OF,
                "priority": 100,
                "payload": {"interval_min": 90},
            }
            bundle = _load_bundle(Path(tmp), events=[ev])
            with self.assertRaises(LifeEngineError):
                runtime_trigger_from_v2_wakeup(bundle.current_state, ev)

    def test_06_occurrence_key_replay_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame_a, _, trigger, ev = _frame_for(Path(tmp))
            key_a = derive_runtime_decision_key(
                character_id=CHAR, trigger_id=trigger.trigger_id
            )
            key_b = derive_runtime_decision_key(
                character_id=CHAR, trigger_id=ev["event_id"]
            )
            self.assertEqual(frame_a.runtime_decision_key, key_a)
            self.assertEqual(key_a, key_b)
            self.assertEqual(
                key_a,
                stable_id("runtime-decision", CHAR, trigger.trigger_id),
            )

    def test_07_same_semantic_key_different_event_ids(self) -> None:
        a = derive_runtime_decision_key(character_id=CHAR, trigger_id="event-a")
        b = derive_runtime_decision_key(character_id=CHAR, trigger_id="event-b")
        self.assertNotEqual(a, b)

    def test_08_semantic_key_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _threshold_wakeup()
            _, _, trigger, _ = _frame_for(Path(tmp), event=ev, facts=_facts(meal_feasible=True))
            self.assertEqual(
                trigger.semantic_decision_key, "threshold:HUNGER:MEAL_NEEDED:UP"
            )
            boundary = decision_boundary_from_runtime_trigger(
                trigger,
                runtime_decision_key=derive_runtime_decision_key(
                    character_id=CHAR, trigger_id=trigger.trigger_id
                ),
            )
            self.assertNotEqual(boundary.decision_key, trigger.semantic_decision_key)
            self.assertEqual(trigger.direction, "UP")

    def test_09_threshold_tuple_strict(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_runtime_decision_trigger(
                {
                    "trigger_id": "t1",
                    "occurred_at": AS_OF,
                    "trigger_kind": "THRESHOLD",
                    "semantic_decision_key": "bad",
                    "reason_code": "BAD",
                    "metric": "HUNGER",
                    "target_band": "MEAL_NEEDED",
                    "direction": "DOWN",
                    "source_kind": None,
                    "source_ref": None,
                }
            )

    def test_10_non_threshold_metric_null(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_runtime_decision_trigger(
                {
                    "trigger_id": "t1",
                    "occurred_at": AS_OF,
                    "trigger_kind": "IDLE",
                    "semantic_decision_key": "idle:x",
                    "reason_code": "IDLE",
                    "metric": "HUNGER",
                    "target_band": None,
                    "direction": None,
                    "source_kind": None,
                    "source_ref": None,
                }
            )

    def test_semantic_equality_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            bundle = _load_bundle(Path(tmp), events=[ev])
            mutated = copy.deepcopy(ev)
            mutated["payload"] = dict(mutated["payload"])
            mutated["payload"]["reason_code"] = "OTHER"
            with self.assertRaises(LifeEngineError):
                runtime_trigger_from_v2_wakeup(bundle.current_state, mutated)


class RuntimeDecisionOccurrenceBindingTests(unittest.TestCase):
    """Major review fix: occurrence identity must bind to authoritative queued v2 wakeup."""

    def test_hand_made_trigger_without_queue_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            bundle = _load_bundle(Path(tmp), events=[ev])
            handmade = RuntimeDecisionTrigger(
                trigger_id="hand-made-trigger-id",
                occurred_at=AS_OF,
                trigger_kind="IDLE",
                semantic_decision_key="idle:fixture",
                reason_code="IDLE_REASSESSMENT",
                metric=None,
                target_band=None,
                direction=None,
                source_kind=None,
                source_ref=None,
            )
            with self.assertRaises(LifeEngineError):
                build_runtime_decision_frame(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    trigger=handmade,
                    facts=_facts(),
                )

    def test_trigger_id_points_to_different_queued_event_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup(decision_key="idle:a")
            other = {
                "schema_version": 1,
                "event_id": "idle-other-event",
                "event_kind": "IDLE_REASSESSMENT",
                "due_at": AS_OF,
                "priority": 100,
                "payload": {"interval_min": 90},
            }
            bundle = _load_bundle(Path(tmp), events=[ev, other])
            trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, ev)
            # Point trigger_id at a different queued row (non-decision).
            spoofed = RuntimeDecisionTrigger(
                trigger_id=other["event_id"],
                occurred_at=trigger.occurred_at,
                trigger_kind=trigger.trigger_kind,
                semantic_decision_key=trigger.semantic_decision_key,
                reason_code=trigger.reason_code,
                metric=trigger.metric,
                target_band=trigger.target_band,
                direction=trigger.direction,
                source_kind=trigger.source_kind,
                source_ref=trigger.source_ref,
            )
            with self.assertRaises(LifeEngineError):
                build_runtime_decision_frame(
                    bundle=bundle,
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    trigger=spoofed,
                    facts=_facts(),
                )

    def test_supplied_trigger_semantics_differ_from_queue_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            bundle = _load_bundle(Path(tmp), events=[ev])
            trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, ev)
            mutated = RuntimeDecisionTrigger(
                trigger_id=trigger.trigger_id,
                occurred_at=trigger.occurred_at,
                trigger_kind=trigger.trigger_kind,
                semantic_decision_key=trigger.semantic_decision_key,
                reason_code="DIFFERENT_REASON",
                metric=trigger.metric,
                target_band=trigger.target_band,
                direction=trigger.direction,
                source_kind=trigger.source_kind,
                source_ref=trigger.source_ref,
            )
            with self.assertRaises(LifeEngineError):
                bind_runtime_trigger_to_queued_wakeup(bundle.current_state, mutated)

    def test_forged_queued_event_id_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            forged = copy.deepcopy(ev)
            forged["event_id"] = "forged-v2-decision-wakeup:deadbeef"
            # Keep managed identity formula distinguishable.
            self.assertNotEqual(
                forged["event_id"],
                derive_managed_v2_decision_wakeup_event_id(
                    character_id=CHAR,
                    decision_key=forged["payload"]["decision_key"],
                    due_at=forged["due_at"],
                ),
            )
            bundle = _load_bundle(Path(tmp), events=[forged])
            with self.assertRaises(LifeEngineError) as ctx:
                runtime_trigger_from_v2_wakeup(bundle.current_state, forged)
            self.assertIn("managed v2 identity", ctx.exception.detail)

    def test_exact_slice2c_built_event_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, trigger, ev = _frame_for(Path(tmp))
            expected = derive_managed_v2_decision_wakeup_event_id(
                character_id=CHAR,
                decision_key=ev["payload"]["decision_key"],
                due_at=ev["due_at"],
            )
            self.assertEqual(ev["event_id"], expected)
            # Exact parity with Slice 2C builder identity.
            rebuilt = build_v2_decision_wakeup_event(
                character_id=CHAR,
                candidate=FutureCandidate(
                    due_at=ev["due_at"],
                    trigger_kind=ev["payload"]["trigger_kind"],
                    decision_key=ev["payload"]["decision_key"],
                    reason_code=ev["payload"]["reason_code"],
                    metric=ev["payload"]["metric"],
                    target_band=ev["payload"]["target_band"],
                    direction=ev["payload"]["direction"],
                    source_kind=ev["payload"]["source_kind"],
                    source_ref=ev["payload"]["source_ref"],
                ),
            )
            self.assertEqual(expected, rebuilt["event_id"])
            self.assertEqual(trigger.trigger_id, expected)
            self.assertEqual(frame.trigger.trigger_id, expected)

    def test_same_event_replay_same_runtime_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fa, _, _, _ = _frame_for((Path(tmp) / "a"))
            fb, _, _, _ = _frame_for((Path(tmp) / "b"))
            self.assertEqual(fa.runtime_decision_key, fb.runtime_decision_key)

    def test_same_semantic_key_later_due_different_runtime_key(self) -> None:
        later = "2026-04-01T13:00:00+09:00"
        with tempfile.TemporaryDirectory() as tmp:
            ev_now = _v2_wakeup(decision_key="idle:same-family", due_at=AS_OF)
            ev_later = _v2_wakeup(decision_key="idle:same-family", due_at=later)
            self.assertEqual(
                ev_now["payload"]["decision_key"],
                ev_later["payload"]["decision_key"],
            )
            self.assertNotEqual(ev_now["event_id"], ev_later["event_id"])

            frame_now, _, _, _ = _frame_for(Path(tmp) / "now", event=ev_now)

            def mutate_later(current: dict) -> dict:
                current = dict(current)
                current["processed_through"] = later
                current["behavior_policy_version"] = POLICY_VERSION
                current["pending_queue"] = {"cursor": 0, "events": [ev_later]}
                return current

            root = build_synthetic_bundle_root(
                Path(tmp) / "later",
                processed_through=later,
                mutate_current=mutate_later,
            )
            bundle_later = load_runtime_bundle(root, reference_sets=_refs())
            trigger_later = runtime_trigger_from_v2_wakeup(
                bundle_later.current_state, ev_later
            )
            frame_later = build_runtime_decision_frame(
                bundle=bundle_later,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                trigger=trigger_later,
                facts=_facts(),
            )
            self.assertNotEqual(
                frame_now.runtime_decision_key, frame_later.runtime_decision_key
            )


class RuntimeDecisionFactsBindingTests(unittest.TestCase):
    def test_11_required_explicit_facts(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_runtime_decision_facts({"available_window_min": 10}, now=AS_OF)

    def test_12_no_binary_float(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_runtime_decision_facts(_facts(available_window_min=1.5), now=AS_OF)

    def test_13_bad_physical_map_types(self) -> None:
        with self.assertRaises(LifeEngineError):
            parse_runtime_decision_facts(
                _facts(structural_physical_feasible_by_action={"WORK": 1}),
                now=AS_OF,
            )

    def test_collection_rejects_non_list_tuple_sequence(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            parse_runtime_decision_facts(
                _facts(route_profiles={"not": "a-list"}),
                now=AS_OF,
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        with self.assertRaises(LifeEngineError):
            parse_runtime_decision_facts(
                _facts(household_tasks="bare-string"),
                now=AS_OF,
            )

    def test_route_item_must_be_mapping(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            parse_runtime_decision_facts(
                _facts(route_profiles=["not-a-mapping"]),
                now=AS_OF,
            )
        self.assertIn("must be a mapping", ctx.exception.detail)

    def test_route_list_of_pairs_not_coerced(self) -> None:
        # list-of-pairs would be silently accepted by dict(...) — must fail closed.
        pairs = [("route_profile_id", "x"), ("schema_version", 1)]
        with self.assertRaises(LifeEngineError) as ctx:
            parse_runtime_decision_facts(
                _facts(route_profiles=[pairs]),
                now=AS_OF,
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_STATE)
        self.assertNotIsInstance(ctx.exception.__cause__, TypeError)

    def test_15_no_active_continuation_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(Path(tmp), facts=_facts(continuation_feasible=None))
            self.assertIsNone(frame.resolution.activity_instance_id)

    def test_16_active_requires_bool_continuation(self) -> None:
        active = _started_active()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LifeEngineError):
                _frame_for(
                    Path(tmp),
                    active_activity=active,
                    facts=_facts(continuation_feasible=None),
                )
            frame, _, _, _ = _frame_for(
                (Path(tmp) / "ok"),
                active_activity=active,
                facts=_facts(continuation_feasible=True),
            )
            self.assertIsNotNone(frame.resolution)

    def test_17_legacy_active_without_runtime_context_reject(self) -> None:
        legacy = {
            "activity_instance_id": "act-legacy",
            "activity_type": "STUDY_BLOCK",
            "actual_start": AS_OF,
            "interruptibility": "REASSESSABLE",
            "planned_end": None,
            "expected_end": "2026-04-01T14:00:00+09:00",
            "expected_end_window": None,
            "location_id": "fixture-desk",
            "companions": [],
            "commitment_id": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LifeEngineError):
                _frame_for(
                    Path(tmp),
                    active_activity=legacy,
                    facts=_facts(continuation_feasible=True),
                )

    def test_19_valid_bundle_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            bundle = _load_bundle(Path(tmp), events=[ev])
            bad = {
                "current_state": bundle.current_state,
                "schedule_state": bundle.schedule_state,
                # missing world domains
            }
            with self.assertRaises(LifeEngineError):
                build_runtime_decision_frame(
                    bundle=bad,  # type: ignore[arg-type]
                    reference_sets=_refs(),
                    behavior_policy=POLICY,
                    trigger=runtime_trigger_from_v2_wakeup(bundle.current_state, ev),
                    facts=_facts(),
                )

    def test_20_policy_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LifeEngineError) as ctx:
                _frame_for(
                    Path(tmp),
                    mutate_current=lambda c: {
                        **c,
                        "behavior_policy_version": "other-version",
                    },
                )
            self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_VERSION)

    def test_21_v1_policy_reject(self) -> None:
        v1 = load_policy(POLICY_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LifeEngineError) as ctx:
                _frame_for(Path(tmp), policy=v1)
            self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_VERSION)

    def test_23_no_caller_spoof_of_bundle_facts(self) -> None:
        """Persisted facts come from RuntimeBundle; caller facts have no overrides."""
        with tempfile.TemporaryDirectory() as tmp:
            frame, bundle, _, _ = _frame_for(Path(tmp))
            self.assertEqual(
                frame.resolution.decision_key,
                derive_runtime_decision_key(
                    character_id=bundle.current_state["character_id"],
                    trigger_id=frame.trigger.trigger_id,
                ),
            )
            # character_id / world_seed / now are not RuntimeDecisionFacts fields.
            with self.assertRaises(LifeEngineError):
                parse_runtime_decision_facts(
                    {**_facts(), "character_id": "spoof"},
                    now=AS_OF,
                )


class RuntimeDecisionAdapterMergeTests(unittest.TestCase):
    def test_24_schedule_is_only_commitment_source(self) -> None:
        def mutate_schedule(schedule: dict) -> dict:
            return build_schedule_state(
                character_id=CHAR,
                as_of=AS_OF,
                commitments=[
                    _commitment(
                        timing={
                            "timing_kind": "EXACT",
                            "planned_start": "2026-04-01T14:00:00+09:00",
                            "planned_end": "2026-04-01T18:00:00+09:00",
                        }
                    )
                ],
                obligation_tasks=[_task(due_at="2026-04-01T18:00:00+09:00")],
            )

        with tempfile.TemporaryDirectory() as tmp:
            frame, bundle, _, _ = _frame_for(
                Path(tmp),
                mutate_schedule=mutate_schedule,
                facts=_facts(route_profiles=[_route()]),
            )
            self.assertEqual(len(bundle.schedule_state["commitments"]), 1)
            self.assertTrue(
                any(
                    o.source_kind == "COMMITMENT"
                    or "commitment" in o.opportunity_key
                    or o.opportunity_class == "HARD_COMMITMENT"
                    for o in frame.merged_opportunities
                )
                or any(
                    b.trigger_kind
                    in {"COMMITMENT_DEPARTURE", "PLANNED_WINDOW", "OBLIGATION"}
                    for b in frame.merged_known_boundaries
                )
            )

    def test_26_household_facts_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(
                Path(tmp),
                facts=_facts(household_tasks=[_household()]),
            )
            self.assertTrue(
                any("household" in o.opportunity_key for o in frame.merged_opportunities)
                or any(
                    "household" in b.decision_key for b in frame.merged_known_boundaries
                )
            )

    def test_29_no_home_state_household_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(Path(tmp), facts=_facts(household_tasks=[]))
            self.assertFalse(
                any("household" in o.opportunity_key for o in frame.merged_opportunities)
            )

    def test_31_input_order_invariance(self) -> None:
        routes_a = [_route(route_profile_id="a"), _route(route_profile_id="b", origin_location_id=HOME, destination_location_id="fixture-gym")]
        routes_b = list(reversed(routes_a))
        tasks_a = [_household(household_task_id="hh-a"), _household(household_task_id="hh-b")]
        tasks_b = list(reversed(tasks_a))
        with tempfile.TemporaryDirectory() as tmp:
            fa, _, _, _ = _frame_for(
                (Path(tmp) / "a"),
                facts=_facts(route_profiles=routes_a, household_tasks=tasks_a),
            )
            fb, _, _, _ = _frame_for(
                (Path(tmp) / "b"),
                facts=_facts(route_profiles=routes_b, household_tasks=tasks_b),
            )
            self.assertEqual(fa.decision_snapshot_hash, fb.decision_snapshot_hash)
            self.assertEqual(fa.decision_facts_hash, fb.decision_facts_hash)
            self.assertEqual(
                [o.opportunity_key for o in fa.merged_opportunities],
                [o.opportunity_key for o in fb.merged_opportunities],
            )

    def test_32_duplicate_opportunity_key_reject(self) -> None:
        opp = ResolvedOpportunity(
            opportunity_key="dup",
            opportunity_class="ROUTINE_HABIT",
            action_kind="HOUSEHOLD",
            source_kind="ROUTINE",
            source_ref="hh:1",
            soft_candidate=True,
            time_feasible=True,
            location_feasible=True,
            physical_feasible=True,
            domain_guard_satisfied=True,
            local_preference_permille=None,
            rule_ids=("r",),
        )
        with self.assertRaises(LifeEngineError):
            merge_resolved_opportunities([opp], [opp])

    def test_33_canonical_opportunity_sort(self) -> None:
        a = ResolvedOpportunity(
            opportunity_key="b-key",
            opportunity_class="ROUTINE_HABIT",
            action_kind="HOUSEHOLD",
            source_kind="ROUTINE",
            source_ref="hh:1",
            soft_candidate=True,
            time_feasible=True,
            location_feasible=True,
            physical_feasible=True,
            domain_guard_satisfied=True,
            local_preference_permille=None,
            rule_ids=("r",),
        )
        b = ResolvedOpportunity(
            opportunity_key="a-key",
            opportunity_class="ROUTINE_HABIT",
            action_kind="HOUSEHOLD",
            source_kind="ROUTINE",
            source_ref="hh:2",
            soft_candidate=True,
            time_feasible=True,
            location_feasible=True,
            physical_feasible=True,
            domain_guard_satisfied=True,
            local_preference_permille=None,
            rule_ids=("r",),
        )
        merged = merge_resolved_opportunities([a], [b])
        self.assertEqual([o.opportunity_key for o in merged], ["a-key", "b-key"])

    def test_34_duplicate_boundary_key_reject(self) -> None:
        b = KnownDecisionBoundary(
            due_at="2026-04-01T15:00:00+09:00",
            trigger_kind="OBLIGATION",
            decision_key="same",
            reason_code="X",
            source_kind="TASK",
            source_ref="task:a",
        )
        with self.assertRaises(LifeEngineError):
            merge_known_decision_boundaries([b], [b], now=AS_OF)

    def test_35_canonical_boundary_sort(self) -> None:
        later = KnownDecisionBoundary(
            due_at="2026-04-01T16:00:00+09:00",
            trigger_kind="OBLIGATION",
            decision_key="z",
            reason_code="X",
            source_kind=None,
            source_ref=None,
        )
        earlier = KnownDecisionBoundary(
            due_at="2026-04-01T15:00:00+09:00",
            trigger_kind="DAY_PLANNING",
            decision_key="a",
            reason_code="Y",
            source_kind=None,
            source_ref=None,
        )
        merged = merge_known_decision_boundaries([later], [earlier], now=AS_OF)
        self.assertEqual(
            [(b.due_at, b.trigger_kind, b.decision_key) for b in merged],
            [
                (earlier.due_at, earlier.trigger_kind, earlier.decision_key),
                (later.due_at, later.trigger_kind, later.decision_key),
            ],
        )


class RuntimeDecisionResolverTests(unittest.TestCase):
    def test_37_runtime_occurrence_key_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, trigger, _ = _frame_for(
                Path(tmp),
                facts=_facts(household_tasks=[_household()], meal_feasible=True),
            )
            expected = derive_runtime_decision_key(
                character_id=CHAR, trigger_id=trigger.trigger_id
            )
            self.assertEqual(frame.runtime_decision_key, expected)
            self.assertEqual(frame.resolution.decision_key, expected)
            for cand in frame.raw_candidates:
                self.assertTrue(
                    cand.candidate_id.startswith("action-candidate:"),
                )
                # candidate_id embeds decision_key via stable_id parts
                rebuilt = candidate_id_for(
                    character_id=CHAR,
                    decision_key=expected,
                    candidate_key=cand.candidate_key,
                )
                self.assertEqual(cand.candidate_id, rebuilt)

    def test_38_semantic_scheduler_key_not_for_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, trigger, _ = _frame_for(Path(tmp))
            semantic = trigger.semantic_decision_key
            for cand in frame.raw_candidates:
                wrong = candidate_id_for(
                    character_id=CHAR,
                    decision_key=semantic,
                    candidate_key=cand.candidate_key,
                )
                self.assertNotEqual(cand.candidate_id, wrong)

    def test_39_raw_considered_ids_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(
                Path(tmp), facts=_facts(household_tasks=[_household()])
            )
            self.assertEqual(
                tuple(sorted(c.candidate_id for c in frame.raw_candidates)),
                frame.resolution.considered_candidate_ids,
            )

    def test_40_select_recovers_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(
                Path(tmp),
                facts=_facts(household_tasks=[_household()]),
            )
            self.assertEqual(frame.resolution.result_kind, "SELECT_CANDIDATE")
            self.assertIsNotNone(frame.selected_candidate)
            assert frame.selected_candidate is not None
            self.assertEqual(
                frame.selected_candidate.candidate_id,
                frame.resolution.selected_candidate_id,
            )
            self.assertEqual(
                frame.selected_candidate.action_kind,
                frame.resolution.selected_action_kind,
            )

    def test_41_continue_exact(self) -> None:
        active = _started_active()
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(
                Path(tmp),
                active_activity=active,
                facts=_facts(continuation_feasible=True),
                event=_threshold_wakeup(),
            )
            self.assertIn(
                frame.resolution.result_kind,
                {"CONTINUE_CURRENT", "SELECT_CANDIDATE"},
            )
            self.assertIsNotNone(frame.selected_candidate)
            assert frame.selected_candidate is not None
            if frame.resolution.result_kind == "CONTINUE_CURRENT":
                self.assertEqual(frame.selected_candidate.action_kind, "CONTINUE_CURRENT")
                self.assertEqual(
                    frame.selected_candidate.activity_instance_id,
                    active["activity_instance_id"],
                )
            self.assertEqual(
                frame.selected_candidate.candidate_id,
                frame.resolution.selected_candidate_id,
            )
            self.assertEqual(
                frame.selected_candidate.action_kind,
                frame.resolution.selected_action_kind,
            )

    def test_42_no_eligible_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(
                Path(tmp),
                facts=_facts(
                    kickboxing_location_feasible=False,
                    routine_physical_feasible_by_action=_routine_phys(
                        HOUSEHOLD=False, KICKBOXING=False
                    ),
                    structural_physical_feasible_by_action=_structural_phys(
                        WORK=False,
                        HARD_COMMITMENT_ACTIVITY=False,
                        TRAVEL_TO_COMMITMENT=False,
                        SOCIAL_PROMISE=False,
                        ERRAND=False,
                        STUDY=False,
                    ),
                    meal_feasible=False,
                    rest_feasible=False,
                ),
            )
            self.assertEqual(frame.resolution.result_kind, "NO_ELIGIBLE_ACTION")
            self.assertIsNone(frame.selected_candidate)


class RuntimeDecisionEvidenceTests(unittest.TestCase):
    def test_45_trigger_ref_exact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, trigger, _ = _frame_for(Path(tmp))
            self.assertIn(f"trigger:{trigger.trigger_id}", frame.input_state_refs)

    def test_46_snapshot_hash_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts = _facts()
            fa, _, _, _ = _frame_for((Path(tmp) / "a"), facts=facts)
            fb, _, _, _ = _frame_for((Path(tmp) / "b"), facts=facts)
            self.assertEqual(fa.decision_snapshot_hash, fb.decision_snapshot_hash)
            self.assertEqual(fa.decision_facts_hash, fb.decision_facts_hash)
            self.assertEqual(
                fa.materialization_context_hash, fb.materialization_context_hash
            )
            self.assertEqual(fa.input_state_refs, fb.input_state_refs)
            parsed = parse_runtime_decision_facts(facts, now=AS_OF)
            self.assertEqual(fa.decision_facts_hash, build_decision_facts_hash(parsed))
            self.assertEqual(len(fa.materialization_context_hash), 64)

    def test_47_input_order_permutation_invariant(self) -> None:
        routes_a = [
            _route(route_profile_id="r1"),
            _route(
                route_profile_id="r2",
                destination_location_id="fixture-gym",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            fa, _, _, _ = _frame_for(
                (Path(tmp) / "a"), facts=_facts(route_profiles=routes_a)
            )
            fb, _, _, _ = _frame_for(
                (Path(tmp) / "b"), facts=_facts(route_profiles=list(reversed(routes_a)))
            )
            self.assertEqual(fa.decision_snapshot_hash, fb.decision_snapshot_hash)

    def test_48_fact_change_changes_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fa, _, _, _ = _frame_for((Path(tmp) / "a"), facts=_facts(available_window_min=60))
            fb, _, _, _ = _frame_for((Path(tmp) / "b"), facts=_facts(available_window_min=90))
            self.assertNotEqual(fa.decision_snapshot_hash, fb.decision_snapshot_hash)
            self.assertNotEqual(fa.decision_facts_hash, fb.decision_facts_hash)
            self.assertNotEqual(
                fa.materialization_context_hash, fb.materialization_context_hash
            )

    def test_49_policy_change_changes_hash(self) -> None:
        # Same version string but different hash body is hard without mutating policy.
        # Mutate a non-version calibration note via deep copy of normalized policy.
        alt = copy.deepcopy(dict(POLICY))
        # bump a decision threshold that remains schema-valid.
        thr = alt["decision_thresholds"]
        key = next(iter(thr))
        if isinstance(thr[key], int):
            thr[key] = thr[key] + 1
        else:
            # nested — poke soft_reconsider if present
            for k, v in list(thr.items()):
                if isinstance(v, int):
                    thr[k] = v + 1
                    break
        # Ensure version string still matches CurrentState binding.
        self.assertEqual(alt["behavior_policy_version"], POLICY_VERSION)
        self.assertNotEqual(hash_policy(alt), hash_policy(POLICY))
        with tempfile.TemporaryDirectory() as tmp:
            fa, _, _, _ = _frame_for((Path(tmp) / "a"), policy=POLICY)
            fb, _, _, _ = _frame_for((Path(tmp) / "b"), policy=alt)
            self.assertNotEqual(fa.decision_snapshot_hash, fb.decision_snapshot_hash)

    def test_50_world_seed_change_changes_hash_without_raw_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fa, ba, _, _ = _frame_for((Path(tmp) / "a"), world_seed="seed-aaa")
            fb, bb, _, _ = _frame_for((Path(tmp) / "b"), world_seed="seed-bbb")
            self.assertNotEqual(fa.decision_snapshot_hash, fb.decision_snapshot_hash)
            for ref in fa.input_state_refs + fb.input_state_refs:
                self.assertNotIn("seed-aaa", ref)
                self.assertNotIn("seed-bbb", ref)
            self.assertEqual(
                canonical_hash(ba.current_state["world_seed"]),
                canonical_hash("seed-aaa"),
            )

    def test_51_refs_unique_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame, _, _, _ = _frame_for(Path(tmp))
            self.assertEqual(len(frame.input_state_refs), 2)
            self.assertEqual(
                frame.input_state_refs, tuple(sorted(frame.input_state_refs))
            )
            self.assertEqual(len(set(frame.input_state_refs)), 2)
            self.assertTrue(
                frame.input_state_refs[0].startswith("decision-snapshot:")
                or frame.input_state_refs[0].startswith("trigger:")
            )


class RuntimeDecisionNonMutationTests(unittest.TestCase):
    def test_input_non_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ev = _v2_wakeup()
            bundle = _load_bundle(Path(tmp), events=[ev])
            facts = _facts(household_tasks=[_household()], route_profiles=[_route()])
            facts_before = canonical_json(facts)
            queue_before = canonical_json(bundle.current_state["pending_queue"])
            schedule_before = canonical_json(bundle.schedule_state)
            policy_before = canonical_json(dict(POLICY))
            revision_before = bundle.current_state["state_revision"]
            trigger = runtime_trigger_from_v2_wakeup(bundle.current_state, ev)
            build_runtime_decision_frame(
                bundle=bundle,
                reference_sets=_refs(),
                behavior_policy=POLICY,
                trigger=trigger,
                facts=facts,
            )
            self.assertEqual(canonical_json(facts), facts_before)
            self.assertEqual(
                canonical_json(bundle.current_state["pending_queue"]), queue_before
            )
            self.assertEqual(canonical_json(bundle.schedule_state), schedule_before)
            self.assertEqual(canonical_json(dict(POLICY)), policy_before)
            self.assertEqual(bundle.current_state["state_revision"], revision_before)
            # Queue still contains the wakeup (not popped).
            self.assertEqual(
                len(bundle.current_state["pending_queue"]["events"]), 1
            )
