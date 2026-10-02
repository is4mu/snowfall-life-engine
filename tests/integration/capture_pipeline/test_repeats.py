"""Repeated-frame capture policy, identity, and chain tests."""

from __future__ import annotations

import ast
import copy
import inspect
import json
import unittest

import pytest
from pathlib import Path

from engine.life.camera_roll import project_camera_roll_records
from engine.life.canonical import canonical_hash
from engine.life.capture_decisions import (
    RESULT_SKIP,
    RESULT_TAKE,
    resolve_capture_opportunity,
)
from engine.life.capture_emitters import (
    EMITTER_RULE_TO_CAPTURE_KIND,
    build_capture_source_moment,
    emit_capture_opportunities,
)
from engine.life.capture_pipeline import apply_capture_take
from engine.life.capture_repeats import (
    REPEAT_RULE_ID,
    apply_repeat_frame_take,
    build_next_repeat_frame_opportunity,
    derive_repeat_frame_source_key,
    parent_capture_input_ref,
)
from engine.life.captures import validate_capture_fact
from engine.life.errors import LifeEngineError
from engine.life.events import build_actual_event_from_activity
from engine.life.ids import stable_id
from engine.life.policy import load_policy, normalize_policy
from engine.life.schema import load_schema_document
import engine.life.capture_repeats as capture_repeats_module
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.builders.captures import (
    PUBLIC_NO_CAPTURE_EVENT_HASH,
    PUBLIC_NO_CAPTURE_HISTORY_HASH,
    build_test_capture_fact,
)

FIXTURE = POLICY_V2_CANDIDATE_PATH
CHAR = "fixture-character"
WORLD_SEED = "public-capture-repeat-seed"
POLICY_VERSION = load_policy(POLICY_V2_CANDIDATE_PATH)["behavior_policy_version"]
MODULE_PATH = Path(capture_repeats_module.__file__)


def _policy() -> dict:
    return load_policy(FIXTURE)


def _policy_mut() -> dict:
    return copy.deepcopy(_policy())


def _force_take_policy() -> dict:
    policy = _policy_mut()
    for kind in policy["capture_policy"]["activation_permille_by_kind"]:
        policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
    return normalize_policy(policy)


def _force_skip_policy() -> dict:
    policy = _policy_mut()
    for kind in policy["capture_policy"]["activation_permille_by_kind"]:
        policy["capture_policy"]["activation_permille_by_kind"][kind] = 0
    return normalize_policy(policy)


def _visual_context(**overrides) -> dict:
    base = {
        "character_id": CHAR,
        "character_source_sha": "char-sha-capture-repeat",
        "engine_commit_sha": "engine-sha-capture-repeat",
        "behavior_policy_version": POLICY_VERSION,
        "location_id": "fixture-home",
        "appearance": {
            "outfit_state_id": "outfit-1",
            "outfit_item_ids": ["item-b", "item-a"],
            "makeup_level": "NORMAL",
            "hair_state_id": "hair-1",
        },
        "home_revision": "home-rev-1",
        "wardrobe_revision": "wardrobe-rev-1",
        "photographer_ref": None,
        "companion_refs": [],
        "lighting_context": "NATURAL",
    }
    appearance_over = overrides.pop("appearance", None)
    base.update(overrides)
    if appearance_over is not None:
        app = dict(base["appearance"])
        app.update(appearance_over)
        base["appearance"] = app
    return base


def _activity(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "activity_instance_id": "activity:capture-repeat-fixture-1",
        "activity_type": "LEISURE",
        "actual_start": "2026-04-01T10:00:00+09:00",
        "location_id": "fixture-home",
        "summary": "capture repeat fixture",
    }
    base.update(overrides)
    return base


def _moment(
    *,
    emitter_rule_id: str = "slice4b2.selfie.appearance",
    source_context_kind: str = "PRE_OUTING_SELF_CHECK",
    activity_instance_id: str = "activity:capture-repeat-fixture-1",
    semantic_source_ref: str = "semantic:repeat:1",
    moment_ref: str = "moment:repeat:1",
    moment_at: str = "2026-04-01T10:30:00+09:00",
    subject_refs: list | None = None,
    visual_context: dict | None = None,
    input_state_refs: list | None = None,
) -> dict:
    vc = visual_context or _visual_context()
    kind = EMITTER_RULE_TO_CAPTURE_KIND[emitter_rule_id]
    refs = [] if subject_refs is None else list(subject_refs)
    if kind == "SELFIE":
        vc = dict(vc)
        vc["photographer_ref"] = CHAR
        if CHAR not in refs:
            refs = [CHAR] + list(refs)
    if kind == "PEOPLE" and not refs:
        refs = ["friend-a"]
    return build_capture_source_moment(
        emitter_rule_id=emitter_rule_id,
        source_context_kind=source_context_kind,
        activity_instance_id=activity_instance_id,
        semantic_source_ref=semantic_source_ref,
        moment_ref=moment_ref,
        moment_at=moment_at,
        subject_refs=refs,
        visual_context=vc,
        input_state_refs=["state:repeat-b", "state:repeat-a"]
        if input_state_refs is None
        else input_state_refs,
    )


def _primary_pending(
    *,
    emitter: str = "slice4b2.selfie.appearance",
    context: str = "PRE_OUTING_SELF_CHECK",
    policy: dict | None = None,
    activity: dict | None = None,
    moment_kwargs: dict | None = None,
    world_seed: str = WORLD_SEED,
) -> tuple[dict, dict, dict]:
    """Return (policy, activity_with_pending, primary_capture)."""
    pol = policy if policy is not None else _force_take_policy()
    act = activity if activity is not None else _activity()
    mk = moment_kwargs or {}
    moment = _moment(emitter_rule_id=emitter, source_context_kind=context, **mk)
    emitted = emit_capture_opportunities(
        policy=pol,
        active_activity=act,
        stress=100,
        stress_state_ref="state:stress:4b3",
        source_moments=[moment],
    )
    assert len(emitted.opportunities) == 1
    opp = emitted.opportunities[0]
    res = resolve_capture_opportunity(
        world_seed=world_seed,
        policy=pol,
        active_activity=act,
        opportunity=opp,
    )
    assert res.result_kind == RESULT_TAKE
    pending = apply_capture_take(
        world_seed=world_seed,
        policy=pol,
        active_activity=act,
        opportunity=opp,
        resolution=res,
    )
    return pol, pending, pending["pending_captures"][0]


def _take_repeat_link(
    *,
    policy: dict,
    activity: dict,
    parent_capture_id: str,
    world_seed: str = WORLD_SEED,
) -> tuple[dict, dict, dict]:
    """One-step: build successor -> resolve TAKE -> apply. Returns (act, child, opp)."""
    opp = build_next_repeat_frame_opportunity(
        active_activity=activity,
        parent_capture_id=parent_capture_id,
    )
    assert opp is not None
    res = resolve_capture_opportunity(
        world_seed=world_seed,
        policy=policy,
        active_activity=activity,
        opportunity=opp,
    )
    assert res.result_kind == RESULT_TAKE, res.result_kind
    updated = apply_repeat_frame_take(
        world_seed=world_seed,
        policy=policy,
        active_activity=activity,
        parent_capture_id=parent_capture_id,
        opportunity=opp,
        resolution=res,
    )
    child = next(
        c
        for c in updated["pending_captures"]
        if parent_capture_input_ref(parent_capture_id)
        in c["capture_decision_evidence"]["input_state_refs"]
    )
    return updated, child, opp

pytestmark = pytest.mark.integration


class RepeatFrameResolverChainTests(unittest.TestCase):
    def test_35_50_resolver_chain_bridge(self) -> None:
        # 35–37 reuse 4B1; activation unchanged on candidate-3
        policy = _policy()
        self.assertEqual(
            policy["capture_policy"]["activation_permille_by_kind"]["SELFIE"], 500
        )
        self.assertEqual(
            policy["capture_policy"]["activation_permille_by_kind"]["PEOPLE"], 600
        )
        pol_take = _force_take_policy()
        _, act, primary = _primary_pending(policy=pol_take)
        opp = build_next_repeat_frame_opportunity(
            active_activity=act, parent_capture_id=primary["capture_id"]
        )
        # Force-take policy used for chain length; candidate values separately asserted.
        res1 = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=pol_take,
            active_activity=act,
            opportunity=opp,
        )
        self.assertEqual(res1.result_kind, RESULT_TAKE)
        # 38 replay exact
        res1b = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=pol_take,
            active_activity=act,
            opportunity=opp,
        )
        self.assertEqual(canonical_hash(res1.as_dict()), canonical_hash(res1b.as_dict()))
        # 39–41 first/second/third+ frames reachable
        act2, child1, _ = _take_repeat_link(
            policy=pol_take, activity=act, parent_capture_id=primary["capture_id"]
        )
        act3, child2, _ = _take_repeat_link(
            policy=pol_take, activity=act2, parent_capture_id=child1["capture_id"]
        )
        act4, child3, _ = _take_repeat_link(
            policy=pol_take, activity=act3, parent_capture_id=child2["capture_id"]
        )
        self.assertEqual(len(act4["pending_captures"]), 4)
        ids = [c["capture_id"] for c in act4["pending_captures"]]
        self.assertEqual(len(set(ids)), 4)
        # 42–43 SKIP ends; no child
        skip_pol = _force_skip_policy()
        skip_opp = build_next_repeat_frame_opportunity(
            active_activity=act4, parent_capture_id=child3["capture_id"]
        )
        skip_res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=skip_pol,
            active_activity=act4,
            opportunity=skip_opp,
        )
        self.assertEqual(skip_res.result_kind, RESULT_SKIP)
        before = copy.deepcopy(act4)
        # SKIP must not call take bridge; pending unchanged if we don't apply
        self.assertEqual(len(before["pending_captures"]), 4)
        # 44 forged opportunity rejected
        forged_opp = copy.deepcopy(opp)
        forged_opp["source_key"] = stable_id("capture-source-alternate-frame", "forged")
        forged_opp["opportunity_key"] = stable_id(
            "capture-opportunity",
            forged_opp["activity_instance_id"],
            forged_opp["source_key"],
        )
        with self.assertRaises(LifeEngineError):
            apply_repeat_frame_take(
                world_seed=WORLD_SEED,
                policy=pol_take,
                active_activity=act,
                parent_capture_id=primary["capture_id"],
                opportunity=forged_opp,
                resolution=res1,
            )
        # 45 forged resolution rejected
        forged_res = copy.deepcopy(res1.as_dict())
        forged_res["activation_permille"] = 1
        with self.assertRaises(LifeEngineError):
            apply_repeat_frame_take(
                world_seed=WORLD_SEED,
                policy=pol_take,
                active_activity=act,
                parent_capture_id=primary["capture_id"],
                opportunity=opp,
                resolution=forged_res,
            )
        # 46–47 exactly one child; replay idempotent
        updated = apply_repeat_frame_take(
            world_seed=WORLD_SEED,
            policy=pol_take,
            active_activity=act,
            parent_capture_id=primary["capture_id"],
            opportunity=opp,
            resolution=res1,
        )
        self.assertEqual(len(updated["pending_captures"]), 2)
        again = apply_repeat_frame_take(
            world_seed=WORLD_SEED,
            policy=pol_take,
            active_activity=updated,
            parent_capture_id=primary["capture_id"],
            opportunity=opp,
            resolution=res1,
        )
        self.assertEqual(len(again["pending_captures"]), 2)
        self.assertEqual(canonical_hash(again), canonical_hash(updated))
        # 48 conflicting child identity fails closed
        conflict = copy.deepcopy(updated)
        child = next(
            c
            for c in conflict["pending_captures"]
            if c["capture_id"] != primary["capture_id"]
        )
        child["captured_at"] = "2026-04-01T10:31:00+09:00"
        # Re-validate path via attach would fail; apply_repeat with same ids but
        # pre-poisoned conflicting semantics on replay:
        with self.assertRaises(LifeEngineError):
            from engine.life.captures import attach_capture_to_active_activity

            attach_capture_to_active_activity(
                conflict,
                next(
                    c
                    for c in updated["pending_captures"]
                    if c["capture_id"] != primary["capture_id"]
                ),
            )
        # 49–50 parent and prior links unchanged
        parent_after = next(
            c for c in act4["pending_captures"] if c["capture_id"] == primary["capture_id"]
        )
        self.assertEqual(canonical_hash(parent_after), canonical_hash(primary))
        mid = next(
            c for c in act4["pending_captures"] if c["capture_id"] == child1["capture_id"]
        )
        self.assertEqual(canonical_hash(mid), canonical_hash(child1))

class RepeatFrameEvidenceIntegrationTests(unittest.TestCase):
    def test_51_60_evidence_chain_and_camera_roll(self) -> None:
        pol = _force_take_policy()
        _, act, f1 = _primary_pending(policy=pol)
        act2, f2, opp2 = _take_repeat_link(
            policy=pol, activity=act, parent_capture_id=f1["capture_id"]
        )
        act3, f3, opp3 = _take_repeat_link(
            policy=pol, activity=act2, parent_capture_id=f2["capture_id"]
        )
        for frame, parent in ((f2, f1), (f3, f2)):
            ev = frame["capture_decision_evidence"]
            self.assertIn(REPEAT_RULE_ID, ev["rule_ids"])
            parent_refs = [r for r in ev["input_state_refs"] if r.startswith("capture-parent:")]
            self.assertEqual(parent_refs, [parent_capture_input_ref(parent["capture_id"])])
            self.assertEqual(ev["selected_result"], RESULT_TAKE)
            self.assertEqual(len(ev["random_key"]), 64)
        self.assertEqual(
            f2["capture_decision_evidence"]["source_key"],
            derive_repeat_frame_source_key(
                f1["capture_decision_evidence"]["opportunity_key"]
            ),
        )
        self.assertEqual(
            f2["capture_decision_evidence"]["opportunity_key"], opp2["opportunity_key"]
        )
        # 57 frame3 -> frame2 not frame1
        self.assertEqual(
            [
                r
                for r in f3["capture_decision_evidence"]["input_state_refs"]
                if r.startswith("capture-parent:")
            ],
            [parent_capture_input_ref(f2["capture_id"])],
        )
        # 58 reconstruct chain
        by_id = {c["capture_id"]: c for c in act3["pending_captures"]}
        chain = [f3["capture_id"]]
        cursor = f3["capture_id"]
        while True:
            refs = [
                r
                for r in by_id[cursor]["capture_decision_evidence"]["input_state_refs"]
                if r.startswith("capture-parent:")
            ]
            if not refs:
                break
            parent_id = refs[0].removeprefix("capture-parent:")
            chain.append(parent_id)
            cursor = parent_id
        self.assertEqual(chain, [f3["capture_id"], f2["capture_id"], f1["capture_id"]])
        # 59 survive finalization
        event = build_actual_event_from_activity(
            act3,
            actual_end="2026-04-01T12:00:00+09:00",
            finalized_at="2026-04-01T12:00:00+09:00",
        )
        fin = {c["capture_id"]: c for c in event["captures"]}
        self.assertEqual(
            fin[f3["capture_id"]]["capture_decision_evidence"]["input_state_refs"],
            f3["capture_decision_evidence"]["input_state_refs"],
        )
        # 60 CameraRollRecord does not copy evidence
        with self.assertRaises(LifeEngineError):
            project_camera_roll_records(act3)
        records = project_camera_roll_records(event)
        self.assertEqual(len(records), 3)
        for rec in records:
            self.assertNotIn("capture_decision_evidence", rec)

class RepeatFrameCompatibilityTests(unittest.TestCase):
    def test_no_capture_semantic_hashes_remain_stable(self) -> None:
        from engine.life.canonical import persisted_hash
        from engine.life.history import EMPTY_HISTORY_HASH, next_history_hash

        activity = {
            "schema_version": 1,
            "activity_instance_id": "activity:baseline-fixture-1",
            "activity_type": "LEISURE",
            "actual_start": "2026-04-01T10:00:00+09:00",
            "location_id": "home",
            "summary": "baseline no-capture",
        }
        event = build_actual_event_from_activity(
            activity,
            actual_end="2026-04-01T11:00:00+09:00",
            finalized_at="2026-04-01T11:00:00+09:00",
        )
        self.assertEqual(persisted_hash(event), PUBLIC_NO_CAPTURE_EVENT_HASH)
        self.assertEqual(
            next_history_hash(EMPTY_HISTORY_HASH, event),
            PUBLIC_NO_CAPTURE_HISTORY_HASH,
        )
