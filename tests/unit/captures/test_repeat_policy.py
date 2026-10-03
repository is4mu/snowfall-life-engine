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

pytestmark = pytest.mark.unit


class RepeatFrameEligibilityTests(unittest.TestCase):
    def test_01_04_selfie_people_and_repeat_children_eligible(self) -> None:
        # 1 primary SELFIE
        _, act_s, primary_s = _primary_pending()
        opp1 = build_next_repeat_frame_opportunity(
            active_activity=act_s, parent_capture_id=primary_s["capture_id"]
        )
        self.assertIsNotNone(opp1)
        self.assertEqual(opp1["capture_kind"], "SELFIE")
        self.assertEqual(opp1["rule_ids"], [REPEAT_RULE_ID])

        # 2 primary PEOPLE
        _, act_p, primary_p = _primary_pending(
            emitter="slice4b2.social.people",
            context="IN_PERSON_SOCIAL",
            moment_kwargs={
                "semantic_source_ref": "semantic:repeat:people",
                "moment_ref": "moment:repeat:people",
            },
        )
        opp_p = build_next_repeat_frame_opportunity(
            active_activity=act_p, parent_capture_id=primary_p["capture_id"]
        )
        self.assertIsNotNone(opp_p)
        self.assertEqual(opp_p["capture_kind"], "PEOPLE")

        # 3–4 repeat children eligible for next link
        pol = _force_take_policy()
        act2, child_s, _ = _take_repeat_link(
            policy=pol, activity=act_s, parent_capture_id=primary_s["capture_id"]
        )
        opp3 = build_next_repeat_frame_opportunity(
            active_activity=act2, parent_capture_id=child_s["capture_id"]
        )
        self.assertIsNotNone(opp3)
        self.assertEqual(opp3["capture_kind"], "SELFIE")

        act2p, child_p, _ = _take_repeat_link(
            policy=pol, activity=act_p, parent_capture_id=primary_p["capture_id"]
        )
        opp4 = build_next_repeat_frame_opportunity(
            active_activity=act2p, parent_capture_id=child_p["capture_id"]
        )
        self.assertIsNotNone(opp4)
        self.assertEqual(opp4["capture_kind"], "PEOPLE")

    def test_05_08_ineligible_kinds_return_none(self) -> None:
        cases = [
            ("slice4b2.meal.contextual", "SOCIAL_MEAL", "FOOD", {}),
            ("slice4b2.scenery.view", "OUTING_VIEW", "SCENERY", {}),
            (
                "slice4b2.object.detail",
                "OWNED_ITEM_DETAIL",
                "OBJECT",
                {"subject_refs": ["item:x"]},
            ),
            ("slice4b2.daily.detail", "HOME_DETAIL", "DAILY_LIFE", {}),
        ]
        for emitter, context, kind, mk in cases:
            with self.subTest(kind=kind):
                _, act, primary = _primary_pending(
                    emitter=emitter,
                    context=context,
                    moment_kwargs={
                        "semantic_source_ref": f"semantic:repeat:{kind}",
                        "moment_ref": f"moment:repeat:{kind}",
                        **mk,
                    },
                )
                self.assertEqual(primary["capture_kind"], kind)
                self.assertIsNone(
                    build_next_repeat_frame_opportunity(
                        active_activity=act, parent_capture_id=primary["capture_id"]
                    )
                )

    def test_09_14_fail_closed_and_skip_cannot_parent(self) -> None:
        pol, act, primary = _primary_pending()
        # 9 missing parent
        with self.assertRaises(LifeEngineError):
            build_next_repeat_frame_opportunity(
                active_activity=act, parent_capture_id="capture:does-not-exist"
            )
        # 10 malformed parent ID
        for bad in ("", None, 123, True, []):
            with self.subTest(bad=bad):
                with self.assertRaises(LifeEngineError):
                    build_next_repeat_frame_opportunity(
                        active_activity=act, parent_capture_id=bad
                    )
        # 11 invalid pending Capture fails closed (via activity validate)
        bad_act = copy.deepcopy(act)
        bad_act["pending_captures"][0]["capture_kind"] = "NOT_A_KIND"
        with self.assertRaises(LifeEngineError):
            build_next_repeat_frame_opportunity(
                active_activity=bad_act, parent_capture_id=primary["capture_id"]
            )
        # 12 owner mismatch — capture id present but activity owner differs
        foreign = copy.deepcopy(act)
        foreign["activity_instance_id"] = "activity:other-owner"
        # pending capture still carries original activity_instance_id
        with self.assertRaises(LifeEngineError):
            build_next_repeat_frame_opportunity(
                active_activity=foreign, parent_capture_id=primary["capture_id"]
            )
        # 13 finalized/historical-only cannot be used
        event = build_actual_event_from_activity(
            act,
            actual_end="2026-04-01T12:00:00+09:00",
            finalized_at="2026-04-01T12:00:00+09:00",
        )
        empty_pending = _activity()
        with self.assertRaises(LifeEngineError):
            build_next_repeat_frame_opportunity(
                active_activity=empty_pending,
                parent_capture_id=event["captures"][0]["capture_id"],
            )
        # 14 SKIP cannot parent — no Capture exists for SKIP
        skip_pol = _force_skip_policy()
        opp = build_next_repeat_frame_opportunity(
            active_activity=act, parent_capture_id=primary["capture_id"]
        )
        skip_res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=skip_pol,
            active_activity=act,
            opportunity=opp,
        )
        self.assertEqual(skip_res.result_kind, RESULT_SKIP)
        self.assertIsNone(skip_res.occurrence_key)
        with self.assertRaises(LifeEngineError):
            apply_repeat_frame_take(
                world_seed=WORLD_SEED,
                policy=skip_pol,
                active_activity=act,
                parent_capture_id=primary["capture_id"],
                opportunity=opp,
                resolution=skip_res,
            )

class RepeatFrameIdentityLinearityTests(unittest.TestCase):
    def test_15_25_identity_linearity_no_branch_no_cycle(self) -> None:
        pol, act, primary = _primary_pending()
        parent_opp_key = primary["capture_decision_evidence"]["opportunity_key"]
        # 15–16 same parent => same source key / exact namespace
        sk1 = derive_repeat_frame_source_key(parent_opp_key)
        sk2 = derive_repeat_frame_source_key(parent_opp_key)
        self.assertEqual(sk1, sk2)
        self.assertEqual(
            sk1, stable_id("capture-source-alternate-frame", parent_opp_key)
        )
        # 17 same parent => same opportunity
        opp_a = build_next_repeat_frame_opportunity(
            active_activity=act, parent_capture_id=primary["capture_id"]
        )
        opp_b = build_next_repeat_frame_opportunity(
            active_activity=act, parent_capture_id=primary["capture_id"]
        )
        self.assertEqual(canonical_hash(opp_a), canonical_hash(opp_b))
        # 18 different parent => different
        _, act2, primary2 = _primary_pending(
            moment_kwargs={
                "semantic_source_ref": "semantic:repeat:other",
                "moment_ref": "moment:repeat:other",
            }
        )
        opp_other = build_next_repeat_frame_opportunity(
            active_activity=act2, parent_capture_id=primary2["capture_id"]
        )
        self.assertNotEqual(opp_a["source_key"], opp_other["source_key"])
        self.assertNotEqual(opp_a["opportunity_key"], opp_other["opportunity_key"])
        # 19 child derives grandchild
        act_c, child, child_opp = _take_repeat_link(
            policy=pol, activity=act, parent_capture_id=primary["capture_id"]
        )
        grand_opp = build_next_repeat_frame_opportunity(
            active_activity=act_c, parent_capture_id=child["capture_id"]
        )
        self.assertIsNotNone(grand_opp)
        self.assertEqual(
            grand_opp["source_key"],
            derive_repeat_frame_source_key(child_opp["opportunity_key"]),
        )
        self.assertNotEqual(grand_opp["opportunity_key"], child_opp["opportunity_key"])
        # 20–21 unrelated insertion / ordering does not shift identities
        unrelated = build_test_capture_fact(
            activity_instance_id=act["activity_instance_id"],
            occurrence_key="unrelated-source-material",
            captured_at="2026-04-01T10:31:00+09:00",
            capture_kind="DAILY_LIFE",
            subject_refs=[],
            visual_context=_visual_context(),
            source_key="source:unrelated-4b3",
        )
        act_insert = copy.deepcopy(act)
        act_insert.setdefault("pending_captures", []).append(unrelated)
        # reorder
        act_insert["pending_captures"] = list(reversed(act_insert["pending_captures"]))
        opp_insert = build_next_repeat_frame_opportunity(
            active_activity=act_insert, parent_capture_id=primary["capture_id"]
        )
        self.assertEqual(canonical_hash(opp_insert), canonical_hash(opp_a))
        # 22 no frame index/ordinal in API / identity
        sig = inspect.signature(build_next_repeat_frame_opportunity)
        for banned in ("frame_index", "repeat_number", "variant_number", "ordinal"):
            self.assertNotIn(banned, sig.parameters)
        src = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("frame_index", src)
        self.assertNotIn("repeat_ordinal", src)
        # 23 same parent cannot create two sibling opportunities
        self.assertEqual(opp_a["opportunity_key"], opp_b["opportunity_key"])
        # 24 no cycle through valid API — parent refs form a tree/list toward root
        act3, child2, _ = _take_repeat_link(
            policy=pol, activity=act_c, parent_capture_id=child["capture_id"]
        )
        by_id = {c["capture_id"]: c for c in act3["pending_captures"]}
        seen: set[str] = set()
        cursor = child2["capture_id"]
        while True:
            self.assertNotIn(cursor, seen)
            seen.add(cursor)
            refs = by_id[cursor]["capture_decision_evidence"]["input_state_refs"]
            parents = [r for r in refs if r.startswith("capture-parent:")]
            if not parents:
                break
            self.assertEqual(len(parents), 1)
            cursor = parents[0].removeprefix("capture-parent:")
            if cursor not in by_id:
                break
        # 25 parent ref points to immediate parent
        self.assertIn(
            parent_capture_input_ref(primary["capture_id"]),
            child["capture_decision_evidence"]["input_state_refs"],
        )
        self.assertIn(
            parent_capture_input_ref(child["capture_id"]),
            child2["capture_decision_evidence"]["input_state_refs"],
        )
        self.assertNotIn(
            parent_capture_input_ref(primary["capture_id"]),
            child2["capture_decision_evidence"]["input_state_refs"],
        )

class RepeatFrameInheritanceTests(unittest.TestCase):
    def test_26_34_inheritance_and_shared_timestamp(self) -> None:
        pol, act, primary = _primary_pending()
        opp = build_next_repeat_frame_opportunity(
            active_activity=act, parent_capture_id=primary["capture_id"]
        )
        self.assertEqual(opp["capture_kind"], primary["capture_kind"])
        self.assertEqual(opp["subject_refs"], primary["subject_refs"])
        self.assertEqual(opp["visual_context"], primary["visual_context"])
        self.assertEqual(opp["opportunity_at"], primary["captured_at"])
        self.assertNotEqual(
            opp["opportunity_key"],
            primary["capture_decision_evidence"]["opportunity_key"],
        )
        act2, child, _ = _take_repeat_link(
            policy=pol, activity=act, parent_capture_id=primary["capture_id"]
        )
        self.assertNotEqual(child["capture_id"], primary["capture_id"])
        self.assertEqual(
            child["capture_context_hash"], primary["capture_context_hash"]
        )
        act3, child2, _ = _take_repeat_link(
            policy=pol, activity=act2, parent_capture_id=child["capture_id"]
        )
        # 33–34 frame1/2/3 may share captured_at; no synthetic +1s
        self.assertEqual(primary["captured_at"], child["captured_at"])
        self.assertEqual(child["captured_at"], child2["captured_at"])
        src = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("+1", src)
        self.assertNotIn("timedelta", src)
        self.assertNotIn("time.time", src)

class RepeatFrameBoundaryTests(unittest.TestCase):
    def test_public_policy_and_schemas_are_valid(self) -> None:
        policy = _policy()
        self.assertEqual(policy["behavior_policy_version"], POLICY_VERSION)
        self.assertTrue(POLICY_VERSION.startswith("public-fixture-v2-candidate-"))
        self.assertEqual(
            policy["capture_policy"]["activation_permille_by_kind"]["SELFIE"], 500
        )
        self.assertEqual(
            policy["capture_policy"]["activation_permille_by_kind"]["PEOPLE"], 600
        )
        for name in (
            "capture",
            "capture_decision_evidence",
            "capture_opportunity",
            "capture_source_moment",
            "camera_roll_record",
        ):
            schema = load_schema_document(name)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )

    def test_repeat_api_has_no_eager_recursion_or_product_feedback(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.While, ast.AsyncFor)):
                self.fail("capture_repeats must not contain eager unbounded loops")
        for banned in (
            "max_frames",
            "burst",
            "continuation_probability",
            "repeat_activation",
            "daily_quota",
            "frame_index",
            "instagram",
            "dashboard",
            "while True",
            "loop until",
            "write_text",
            "write_bytes",
        ):
            self.assertNotIn(banned.lower(), source.lower())

    def test_building_successor_does_not_mutate_active_activity(self) -> None:
        _, activity, primary = _primary_pending()
        before = copy.deepcopy(activity)
        opportunity = build_next_repeat_frame_opportunity(
            active_activity=activity,
            parent_capture_id=primary["capture_id"],
        )
        self.assertIsNotNone(opportunity)
        self.assertEqual(activity, before)
