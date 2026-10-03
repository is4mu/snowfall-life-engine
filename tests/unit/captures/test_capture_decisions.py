"""Capture policy, opportunity identity, and deterministic capture-decision tests."""

from __future__ import annotations

import copy
import json
import unittest

import pytest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.camera_roll import project_camera_roll_records
from engine.life.capture_decisions import (
    DECISION_TYPE,
    RESOLVER_RULE_ID,
    RESULT_SKIP,
    RESULT_TAKE,
    SLEEP_GUARD_RULE_ID,
    CaptureDecisionResolution,
    build_capture_opportunity,
    derive_capture_occurrence_key,
    derive_capture_opportunity_key,
    resolve_capture_opportunity,
    validate_capture_opportunity,
)
from engine.life.captures import attach_capture_to_active_activity, build_capture_fact
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.ids import stable_id
from engine.life.policy import load_policy, normalize_policy
from engine.life.schema import load_schema_document
from engine.life.rng import keyed_digest
import engine.life.capture_decisions as capture_decisions_module
from tests.support.constants import POLICY_V2_CANDIDATE_PATH

FIXTURE = POLICY_V2_CANDIDATE_PATH
CHAR = "fixture-character"
WORLD_SEED = "public-capture-decision-seed"
PUBLIC_POLICY_VERSION = load_policy(POLICY_V2_CANDIDATE_PATH)["behavior_policy_version"]


def _policy() -> dict:
    return load_policy(FIXTURE)


def _policy_mut() -> dict:
    return copy.deepcopy(_policy())


def _visual_context(**overrides) -> dict:
    base = {
        "character_id": CHAR,
        "character_source_sha": "char-sha-capture-decision",
        "engine_commit_sha": "engine-sha-capture-decision",
        "behavior_policy_version": PUBLIC_POLICY_VERSION,
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
        "companion_refs": ["friend-b", "friend-a"],
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
        "activity_instance_id": "activity:capture-decision-fixture-1",
        "activity_type": "LEISURE",
        "actual_start": "2026-04-01T10:00:00+09:00",
        "location_id": "fixture-home",
        "summary": "capture decision fixture",
    }
    base.update(overrides)
    return base


def _opportunity(
    *,
    activity_instance_id: str = "activity:capture-decision-fixture-1",
    source_key: str = "source:photo:1",
    opportunity_at: str = "2026-04-01T10:30:00+09:00",
    capture_kind: str = "DAILY_LIFE",
    subject_refs: list | None = None,
    visual_context: dict | None = None,
    rule_ids: list | None = None,
    input_state_refs: list | None = None,
) -> dict:
    return build_capture_opportunity(
        activity_instance_id=activity_instance_id,
        source_key=source_key,
        opportunity_at=opportunity_at,
        capture_kind=capture_kind,
        subject_refs=[] if subject_refs is None else subject_refs,
        visual_context=visual_context or _visual_context(),
        rule_ids=["opp.rule.b", "opp.rule.a"] if rule_ids is None else rule_ids,
        input_state_refs=(
            ["state:b", "state:a"] if input_state_refs is None else input_state_refs
        ),
    )

pytestmark = pytest.mark.unit


class CaptureDecisionSchemaTests(unittest.TestCase):
    def test_modified_schemas_draft_2020_12_meta_valid(self) -> None:
        for name in ("behavior_policy_v2", "capture_opportunity"):
            schema = load_schema_document(name)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:  # pragma: no cover
                self.fail(f"{name} schema meta-invalid: {exc}")

class CaptureDecisionPolicyTests(unittest.TestCase):
    def test_01_capture_policy_required(self) -> None:
        data = _policy_mut()
        del data["capture_policy"]
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_02_all_six_kind_keys_required(self) -> None:
        data = _policy_mut()
        del data["capture_policy"]["activation_permille_by_kind"]["FOOD"]
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_03_unknown_kind_key_rejected(self) -> None:
        data = _policy_mut()
        data["capture_policy"]["activation_permille_by_kind"]["SELFIE_PLUS"] = 100
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_04_activation_0_valid(self) -> None:
        data = _policy_mut()
        for kind in data["capture_policy"]["activation_permille_by_kind"]:
            data["capture_policy"]["activation_permille_by_kind"][kind] = 0
        normalize_policy(data)

    def test_05_activation_1000_valid(self) -> None:
        data = _policy_mut()
        for kind in data["capture_policy"]["activation_permille_by_kind"]:
            data["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        normalize_policy(data)

    def test_06_activation_below_0_rejected(self) -> None:
        data = _policy_mut()
        data["capture_policy"]["activation_permille_by_kind"]["SELFIE"] = -1
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_07_activation_above_1000_rejected(self) -> None:
        data = _policy_mut()
        data["capture_policy"]["activation_permille_by_kind"]["SELFIE"] = 1001
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_08_bool_rejected(self) -> None:
        data = _policy_mut()
        data["capture_policy"]["activation_permille_by_kind"]["SELFIE"] = True
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_09_float_rejected(self) -> None:
        data = _policy_mut()
        data["capture_policy"]["activation_permille_by_kind"]["SELFIE"] = 500.0
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_10_numeric_string_rejected(self) -> None:
        data = _policy_mut()
        data["capture_policy"]["activation_permille_by_kind"]["SELFIE"] = "500"
        with self.assertRaises(LifeEngineError):
            normalize_policy(data)

    def test_11_provenance_coverage_requires_capture_policy(self) -> None:
        data = _policy_mut()
        data["provenance_index"] = [
            e for e in data["provenance_index"] if e["path"] != "capture_policy"
        ]
        with self.assertRaises(LifeEngineError) as ctx:
            normalize_policy(data)
        self.assertIn("missing coverage", ctx.exception.detail)
        self.assertIn("capture_policy", ctx.exception.detail)

    def test_public_fixture_capture_policy_values(self) -> None:
        policy = _policy()
        self.assertTrue(
            str(policy["behavior_policy_version"]).startswith("public-fixture-v2-candidate-")
        )
        self.assertEqual(
            policy["capture_policy"]["activation_permille_by_kind"],
            {
                "SELFIE": 500,
                "PEOPLE": 600,
                "FOOD": 300,
                "SCENERY": 650,
                "OBJECT": 300,
                "DAILY_LIFE": 400,
            },
        )

    def test_public_fixture_capture_policy_is_synthetic_and_non_authoritative(self) -> None:
        policy = _policy()
        notes = " ".join(policy["calibration_metadata"]["calibration_notes"]).lower()
        self.assertIn("synthetic public", notes)
        self.assertIn("not an approved production policy", notes)
        entry = next(
            e for e in policy["provenance_index"] if e["path"] == "capture_policy"
        )
        self.assertEqual(entry["origin"], "ENGINE_DEFAULT")
        self.assertEqual(entry["source_kind"], "ENGINE_RULE")
        self.assertEqual(entry["semantic_anchor"], "capture_policy")
        self.assertIn("public-synthetic", entry["source_ref"])
        self.assertIsNone(entry["source_commit_sha"])
        self.assertIn("synthetic public-test", entry["rationale"].lower())
        self.assertIn("non-authoritative", entry["rationale"].lower())

class CaptureOpportunityIdentityTests(unittest.TestCase):
    def test_14_deterministic_opportunity_key(self) -> None:
        key = derive_capture_opportunity_key("activity:a", "source:x")
        self.assertEqual(
            key, stable_id("capture-opportunity", "activity:a", "source:x")
        )

    def test_15_same_activity_source_same_key(self) -> None:
        a = derive_capture_opportunity_key("activity:a", "source:x")
        b = derive_capture_opportunity_key("activity:a", "source:x")
        self.assertEqual(a, b)

    def test_16_different_source_different_key(self) -> None:
        a = derive_capture_opportunity_key("activity:a", "source:x")
        b = derive_capture_opportunity_key("activity:a", "source:y")
        self.assertNotEqual(a, b)

    def test_17_same_second_different_source_distinct(self) -> None:
        ts = "2026-04-01T10:30:00+09:00"
        a = _opportunity(source_key="source:a", opportunity_at=ts)
        b = _opportunity(source_key="source:b", opportunity_at=ts)
        self.assertEqual(a["opportunity_at"], b["opportunity_at"])
        self.assertNotEqual(a["opportunity_key"], b["opportunity_key"])

    def test_18_bad_stored_opportunity_key_rejected(self) -> None:
        opp = _opportunity()
        bad = dict(opp)
        bad["opportunity_key"] = "capture-opportunity:deadbeef"
        with self.assertRaises(LifeEngineError):
            validate_capture_opportunity(bad)

    def test_19_missing_field_rejected(self) -> None:
        opp = _opportunity()
        for field in (
            "opportunity_key",
            "source_key",
            "activity_instance_id",
            "opportunity_at",
            "capture_kind",
            "subject_refs",
            "visual_context",
            "rule_ids",
            "input_state_refs",
        ):
            bad = dict(opp)
            del bad[field]
            with self.assertRaises(LifeEngineError):
                validate_capture_opportunity(bad)

    def test_20_unknown_field_rejected(self) -> None:
        opp = _opportunity()
        bad = dict(opp)
        bad["notes"] = "free form"
        with self.assertRaises(LifeEngineError):
            validate_capture_opportunity(bad)

    def test_21_bad_naive_fractional_opportunity_at_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            build_capture_opportunity(
                activity_instance_id="activity:capture-decision-fixture-1",
                source_key="source:1",
                opportunity_at="2026-04-01T10:30:00",
                capture_kind="DAILY_LIFE",
                subject_refs=[],
                visual_context=_visual_context(),
                rule_ids=["r1"],
                input_state_refs=["s1"],
            )
        with self.assertRaises(LifeEngineError):
            build_capture_opportunity(
                activity_instance_id="activity:capture-decision-fixture-1",
                source_key="source:1",
                opportunity_at="2026-04-01T10:30:00.5+09:00",
                capture_kind="DAILY_LIFE",
                subject_refs=[],
                visual_context=_visual_context(),
                rule_ids=["r1"],
                input_state_refs=["s1"],
            )

    def test_22_z_offset_canonicalizes_to_plus_0900(self) -> None:
        opp = build_capture_opportunity(
            activity_instance_id="activity:capture-decision-fixture-1",
            source_key="source:z",
            opportunity_at="2026-04-01T01:30:00Z",
            capture_kind="DAILY_LIFE",
            subject_refs=[],
            visual_context=_visual_context(),
            rule_ids=["r1"],
            input_state_refs=["s1"],
        )
        self.assertEqual(opp["opportunity_at"], "2026-04-01T10:30:00+09:00")

    def test_23_selfie_semantics_reused(self) -> None:
        with self.assertRaises(LifeEngineError):
            build_capture_opportunity(
                activity_instance_id="activity:capture-decision-fixture-1",
                source_key="source:selfie-bad",
                opportunity_at="2026-04-01T10:30:00+09:00",
                capture_kind="SELFIE",
                subject_refs=[CHAR],
                visual_context=_visual_context(photographer_ref=None),
                rule_ids=["r1"],
                input_state_refs=["s1"],
            )
        ok = build_capture_opportunity(
            activity_instance_id="activity:capture-decision-fixture-1",
            source_key="source:selfie-ok",
            opportunity_at="2026-04-01T10:30:00+09:00",
            capture_kind="SELFIE",
            subject_refs=[CHAR],
            visual_context=_visual_context(photographer_ref=CHAR),
            rule_ids=["r1"],
            input_state_refs=["s1"],
        )
        self.assertEqual(ok["capture_kind"], "SELFIE")

    def test_24_people_semantics_reused(self) -> None:
        with self.assertRaises(LifeEngineError):
            build_capture_opportunity(
                activity_instance_id="activity:capture-decision-fixture-1",
                source_key="source:people-bad",
                opportunity_at="2026-04-01T10:30:00+09:00",
                capture_kind="PEOPLE",
                subject_refs=[],
                visual_context=_visual_context(),
                rule_ids=["r1"],
                input_state_refs=["s1"],
            )
        ok = _opportunity(capture_kind="PEOPLE", subject_refs=["friend-a"])
        self.assertEqual(ok["capture_kind"], "PEOPLE")

    def test_25_rule_ids_container_and_items_strict(self) -> None:
        base_kwargs = dict(
            activity_instance_id="activity:capture-decision-fixture-1",
            opportunity_at="2026-04-01T10:30:00+09:00",
            capture_kind="DAILY_LIFE",
            subject_refs=[],
            visual_context=_visual_context(),
            input_state_refs=["s1"],
        )

        def reject_build(source_key: str, rule_ids: object) -> None:
            with self.assertRaises(LifeEngineError) as ctx:
                build_capture_opportunity(
                    source_key=source_key, rule_ids=rule_ids, **base_kwargs
                )
            self.assertIsInstance(ctx.exception, LifeEngineError)

        def reject_validate(source_key: str, rule_ids: object) -> None:
            good = _opportunity(source_key=source_key)
            bad = dict(good)
            bad["rule_ids"] = rule_ids  # type: ignore[assignment]
            with self.assertRaises(LifeEngineError):
                validate_capture_opportunity(bad)

        # Non-array containers: must not coerce via list(...).
        for label, bad in (
            ("str", "rule1"),
            ("bytes", b"rule1"),
            ("tuple", ("r1", "r2")),
            ("set", {"r1", "r2"}),
            ("generator", (x for x in ("r1",))),
            ("null", None),
            ("dict", {"r1": True}),
        ):
            reject_build(f"source:rules-bad-{label}", bad)
            reject_validate(f"source:rules-val-{label}", bad)

        # Item / duplicate violations.
        reject_build("source:rules-item", ["r1", 2])
        reject_build("source:rules-dup", ["r1", "r1"])
        reject_validate("source:rules-val-item", ["r1", 2])
        reject_validate("source:rules-val-dup", ["r1", "r1"])

        # Valid list reorders canonically (build + validate).
        built = build_capture_opportunity(
            source_key="source:rules-ok",
            rule_ids=["z-rule", "a-rule"],
            **base_kwargs,
        )
        self.assertEqual(built["rule_ids"], ["a-rule", "z-rule"])
        raw = dict(built)
        raw["rule_ids"] = ["z-rule", "a-rule"]
        validated = validate_capture_opportunity(raw)
        self.assertEqual(validated["rule_ids"], ["a-rule", "z-rule"])

    def test_26_input_state_refs_container_and_items_strict(self) -> None:
        base_kwargs = dict(
            activity_instance_id="activity:capture-decision-fixture-1",
            opportunity_at="2026-04-01T10:30:00+09:00",
            capture_kind="DAILY_LIFE",
            subject_refs=[],
            visual_context=_visual_context(),
            rule_ids=["r1"],
        )

        def reject_build(source_key: str, input_state_refs: object) -> None:
            with self.assertRaises(LifeEngineError):
                build_capture_opportunity(
                    source_key=source_key,
                    input_state_refs=input_state_refs,
                    **base_kwargs,
                )

        def reject_validate(source_key: str, input_state_refs: object) -> None:
            good = _opportunity(source_key=source_key)
            bad = dict(good)
            bad["input_state_refs"] = input_state_refs  # type: ignore[assignment]
            with self.assertRaises(LifeEngineError):
                validate_capture_opportunity(bad)

        for label, bad in (
            ("str", "state1"),
            ("bytes", b"state1"),
            ("tuple", ("s1", "s2")),
            ("set", {"s1", "s2"}),
            ("generator", (x for x in ("s1",))),
            ("null", None),
            ("dict", {"s1": True}),
        ):
            reject_build(f"source:inputs-bad-{label}", bad)
            reject_validate(f"source:inputs-val-{label}", bad)

        reject_build("source:inputs-item", ["s1", None])
        reject_build("source:inputs-dup", ["s1", "s1"])
        reject_validate("source:inputs-val-item", ["s1", None])
        reject_validate("source:inputs-val-dup", ["s1", "s1"])

        built = build_capture_opportunity(
            source_key="source:inputs-ok",
            input_state_refs=["z-state", "a-state"],
            **base_kwargs,
        )
        self.assertEqual(built["input_state_refs"], ["a-state", "z-state"])
        raw = dict(built)
        raw["input_state_refs"] = ["z-state", "a-state"]
        validated = validate_capture_opportunity(raw)
        self.assertEqual(validated["input_state_refs"], ["a-state", "z-state"])

    def test_27_rule_input_order_canonical(self) -> None:
        opp = _opportunity(
            rule_ids=["z-rule", "a-rule"],
            input_state_refs=["z-state", "a-state"],
        )
        self.assertEqual(opp["rule_ids"], ["a-rule", "z-rule"])
        self.assertEqual(opp["input_state_refs"], ["a-state", "z-state"])

    def test_28_build_validate_no_input_mutation(self) -> None:
        refs = ["friend-b", "friend-a"]
        rules = ["r-b", "r-a"]
        inputs = ["s-b", "s-a"]
        vc = _visual_context()
        refs_before = copy.deepcopy(refs)
        rules_before = copy.deepcopy(rules)
        inputs_before = copy.deepcopy(inputs)
        vc_before = copy.deepcopy(vc)
        opp = build_capture_opportunity(
            activity_instance_id="activity:capture-decision-fixture-1",
            source_key="source:mut",
            opportunity_at="2026-04-01T10:30:00+09:00",
            capture_kind="PEOPLE",
            subject_refs=refs,
            visual_context=vc,
            rule_ids=rules,
            input_state_refs=inputs,
        )
        self.assertEqual(refs, refs_before)
        self.assertEqual(rules, rules_before)
        self.assertEqual(inputs, inputs_before)
        self.assertEqual(vc, vc_before)
        raw = dict(opp)
        raw["rule_ids"] = ["z", "a"]
        raw["subject_refs"] = ["friend-b", "friend-a"]
        before = copy.deepcopy(raw)
        validate_capture_opportunity(raw)
        self.assertEqual(raw, before)

class CaptureOpportunityResolverTests(unittest.TestCase):
    def _resolve(self, *, policy=None, activity=None, opportunity=None, seed=WORLD_SEED):
        return resolve_capture_opportunity(
            world_seed=seed,
            policy=policy or _policy(),
            active_activity=activity or _activity(),
            opportunity=opportunity or _opportunity(),
        )

    def test_29_activation_0_always_skip(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 0
        policy = normalize_policy(policy)
        for i in range(20):
            res = self._resolve(
                policy=policy,
                opportunity=_opportunity(source_key=f"source:zero:{i}"),
            )
            self.assertEqual(res.result_kind, RESULT_SKIP)
            self.assertIsNone(res.occurrence_key)

    def test_30_activation_1000_always_take(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        policy = normalize_policy(policy)
        for i in range(20):
            res = self._resolve(
                policy=policy,
                opportunity=_opportunity(source_key=f"source:full:{i}"),
            )
            self.assertEqual(res.result_kind, RESULT_TAKE)
            self.assertIsNotNone(res.occurrence_key)

    def test_31_mid_activation_deterministic(self) -> None:
        res = self._resolve()
        self.assertIn(res.result_kind, {RESULT_TAKE, RESULT_SKIP})
        # Default opportunity kind is DAILY_LIFE → candidate-3 activation 400.
        self.assertEqual(res.activation_permille, 400)

    def test_32_same_seed_replay_exact(self) -> None:
        a = self._resolve()
        b = self._resolve()
        self.assertEqual(a.as_dict(), b.as_dict())

    def test_33_reordered_evaluation_unchanged(self) -> None:
        opps = [
            _opportunity(source_key="source:ord:a"),
            _opportunity(source_key="source:ord:b"),
            _opportunity(source_key="source:ord:c"),
        ]
        forward = [self._resolve(opportunity=o).as_dict() for o in opps]
        reverse = [self._resolve(opportunity=o).as_dict() for o in reversed(opps)]
        by_key_f = {r["opportunity_key"]: r for r in forward}
        by_key_r = {r["opportunity_key"]: r for r in reverse}
        self.assertEqual(by_key_f, by_key_r)

    def test_34_unrelated_opportunity_insertion_unchanged(self) -> None:
        target = _opportunity(source_key="source:target")
        before = self._resolve(opportunity=target).as_dict()
        _ = self._resolve(opportunity=_opportunity(source_key="source:unrelated"))
        after = self._resolve(opportunity=target).as_dict()
        self.assertEqual(before, after)

    def test_35_different_opportunity_key_different_random_key(self) -> None:
        a = self._resolve(opportunity=_opportunity(source_key="source:rk:a"))
        b = self._resolve(opportunity=_opportunity(source_key="source:rk:b"))
        self.assertNotEqual(a.opportunity_key, b.opportunity_key)
        self.assertIsNotNone(a.random_key)
        self.assertIsNotNone(b.random_key)
        self.assertNotEqual(a.random_key, b.random_key)

    def test_36_policy_character_mismatch_rejected(self) -> None:
        opp = _opportunity(visual_context=_visual_context(character_id="other-char"))
        with self.assertRaises(LifeEngineError):
            self._resolve(opportunity=opp)

    def test_37_policy_version_mismatch_rejected(self) -> None:
        opp = _opportunity(
            visual_context=_visual_context(behavior_policy_version="other-version")
        )
        with self.assertRaises(LifeEngineError):
            self._resolve(opportunity=opp)

    def test_38_activity_instance_mismatch_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            self._resolve(
                activity=_activity(activity_instance_id="activity:other"),
                opportunity=_opportunity(activity_instance_id="activity:capture-decision-fixture-1"),
            )

    def test_39_opportunity_before_actual_start_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            self._resolve(
                opportunity=_opportunity(opportunity_at="2026-04-01T09:59:59+09:00")
            )

    def test_40_sleep_never_take(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        policy = normalize_policy(policy)
        for i in range(30):
            res = self._resolve(
                policy=policy,
                activity=_activity(activity_type="SLEEP"),
                opportunity=_opportunity(source_key=f"source:sleep:{i}"),
            )
            self.assertEqual(res.result_kind, RESULT_SKIP)

    def test_41_sleep_hard_guard_no_occurrence(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        policy = normalize_policy(policy)
        res = self._resolve(
            policy=policy,
            activity=_activity(activity_type="SLEEP"),
        )
        self.assertIsNone(res.occurrence_key)
        self.assertIsNone(res.random_key)
        self.assertIn(SLEEP_GUARD_RULE_ID, res.rule_ids)
        self.assertEqual(res.decision_evidence["selected_result"], RESULT_SKIP)
        self.assertIsNone(res.decision_evidence["random_key"])

    def test_42_skip_occurrence_key_null(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 0
        policy = normalize_policy(policy)
        res = self._resolve(policy=policy)
        self.assertEqual(res.result_kind, RESULT_SKIP)
        self.assertIsNone(res.occurrence_key)

    def test_43_take_occurrence_key_deterministic(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        policy = normalize_policy(policy)
        opp = _opportunity(source_key="source:take-occ")
        res = self._resolve(policy=policy, opportunity=opp)
        expected = derive_capture_occurrence_key(
            opp["activity_instance_id"], opp["opportunity_key"]
        )
        self.assertEqual(res.occurrence_key, expected)
        self.assertEqual(
            expected,
            stable_id(
                "capture-occurrence",
                opp["activity_instance_id"],
                opp["opportunity_key"],
            ),
        )

    def test_44_occurrence_key_unaffected_by_evaluation_order(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        policy = normalize_policy(policy)
        opps = [
            _opportunity(source_key="source:occ:a"),
            _opportunity(source_key="source:occ:b"),
        ]
        forward = {
            o["opportunity_key"]: self._resolve(policy=policy, opportunity=o).occurrence_key
            for o in opps
        }
        reverse = {
            o["opportunity_key"]: self._resolve(policy=policy, opportunity=o).occurrence_key
            for o in reversed(opps)
        }
        self.assertEqual(forward, reverse)

    def test_45_take_does_not_attach_pending_capture(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        policy = normalize_policy(policy)
        act = _activity()
        act_before = copy.deepcopy(act)
        res = self._resolve(policy=policy, activity=act)
        self.assertEqual(res.result_kind, RESULT_TAKE)
        self.assertEqual(act, act_before)
        self.assertNotIn("pending_captures", act)
        # Resolver must not call attach; activity still has no pending captures.
        validated = act
        self.assertFalse(validated.get("pending_captures"))

    def test_46_resolver_input_objects_unchanged(self) -> None:
        policy = _policy_mut()
        activity = _activity()
        opportunity = _opportunity()
        p_before = copy.deepcopy(policy)
        a_before = copy.deepcopy(activity)
        o_before = copy.deepcopy(opportunity)
        self._resolve(policy=policy, activity=activity, opportunity=opportunity)
        self.assertEqual(policy, p_before)
        self.assertEqual(activity, a_before)
        self.assertEqual(opportunity, o_before)

class CaptureDecisionEvidenceTests(unittest.TestCase):
    def test_47_evidence_matches_existing_shape(self) -> None:
        res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=_opportunity(),
        )
        ev = res.decision_evidence
        self.assertEqual(
            set(ev.keys()),
            {
                "decision_type",
                "policy_version",
                "rule_ids",
                "input_state_refs",
                "random_key",
                "selected_result",
            },
        )
        self.assertEqual(ev["decision_type"], DECISION_TYPE)
        self.assertIsInstance(res, CaptureDecisionResolution)

    def test_48_selected_result_matches_result(self) -> None:
        res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=_opportunity(),
        )
        self.assertEqual(res.decision_evidence["selected_result"], res.result_kind)

    def test_49_stochastic_random_key_stable(self) -> None:
        opp = _opportunity(source_key="source:rk-stable")
        a = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=opp,
        )
        b = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=opp,
        )
        expected = keyed_digest(
            WORLD_SEED,
            "capture/opportunity",
            CHAR,
            DECISION_TYPE,
            opp["opportunity_key"],
        ).hex()
        self.assertEqual(a.random_key, expected)
        self.assertEqual(a.random_key, b.random_key)
        self.assertEqual(a.decision_evidence["random_key"], expected)

    def test_50_policy_version_correct(self) -> None:
        res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=_opportunity(),
        )
        self.assertEqual(res.policy_version, PUBLIC_POLICY_VERSION)
        self.assertEqual(res.decision_evidence["policy_version"], PUBLIC_POLICY_VERSION)

    def test_51_rule_ids_input_refs_canonical(self) -> None:
        opp = _opportunity(
            rule_ids=["z-opp", "a-opp"],
            input_state_refs=["z-in", "a-in"],
        )
        res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=opp,
        )
        self.assertEqual(list(res.input_state_refs), ["a-in", "z-in"])
        self.assertEqual(res.decision_evidence["input_state_refs"], ["a-in", "z-in"])
        self.assertEqual(list(res.rule_ids), sorted(res.rule_ids))
        self.assertIn(RESOLVER_RULE_ID, res.rule_ids)
        self.assertIn("a-opp", res.rule_ids)

    def test_52_no_free_form_cot_field(self) -> None:
        res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=_opportunity(),
        )
        blob = json.dumps(res.as_dict())
        for banned in ("reasoning", "explanation", "chain_of_thought", "notes", "rationale"):
            self.assertNotIn(f'"{banned}"', blob)

    def test_52b_decision_evidence_deep_immutable(self) -> None:
        res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=_policy(),
            active_activity=_activity(),
            opportunity=_opportunity(source_key="source:immutable-ev"),
        )
        before = copy.deepcopy(res.as_dict())

        # External as_dict() mutation must not affect the resolution.
        dumped = res.as_dict()
        dumped["decision_evidence"]["rule_ids"].append("mutated-from-as-dict")
        dumped["decision_evidence"]["selected_result"] = "MUTATED"
        dumped["rule_ids"].append("top-level-mutated")
        self.assertEqual(res.as_dict(), before)
        self.assertNotIn("mutated-from-as-dict", res.decision_evidence["rule_ids"])
        self.assertEqual(
            res.decision_evidence["selected_result"], before["decision_evidence"]["selected_result"]
        )

        # Public decision_evidence accessor returns a fresh dict; mutate it.
        ev = res.decision_evidence
        ev["rule_ids"].append("mutated-from-property")
        ev["input_state_refs"].append("mutated-input")
        ev["selected_result"] = "MUTATED_AGAIN"
        self.assertEqual(res.decision_evidence, before["decision_evidence"])
        self.assertEqual(res.as_dict()["decision_evidence"], before["decision_evidence"])

        # Repeated serialization remains byte/semantic equivalent.
        a = json.dumps(res.as_dict(), sort_keys=True, separators=(",", ":"))
        b = json.dumps(res.as_dict(), sort_keys=True, separators=(",", ":"))
        self.assertEqual(a, b)
        self.assertEqual(res.as_dict(), before)

class CaptureDecisionIsolationTests(unittest.TestCase):
    # capture.schema.json is intentionally extended by Slice 4B2 (decision evidence).
    # current_state.schema.json: Slice 5A (#66) exact one-delta freeze in test body.
    # active_activity.schema.json: Slice 5B1 (#68) + 5B2C1 (#75) approved-delta freeze.
    # actual_event.schema.json: Slice 5B2C1 (#75) approved social-mode/dynamics freeze.
    UNCHANGED = (
        "schemas/life/camera_roll_record.schema.json",
    )

    def test_59_no_capture_attachment_from_resolver(self) -> None:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        policy = normalize_policy(policy)
        act = _activity()
        res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=policy,
            active_activity=act,
            opportunity=_opportunity(),
        )
        self.assertEqual(res.result_kind, RESULT_TAKE)
        self.assertIsNone(act.get("pending_captures"))

    def test_resolver_has_no_io_or_camera_roll_dependencies(self) -> None:
        source = Path(capture_decisions_module.__file__).read_text(encoding="utf-8")
        for banned in (
            "project_camera_roll_records",
            "merge_camera_roll",
            "attach_capture_to_active_activity",
            "build_capture_fact",
            "write_text",
            "write_bytes",
            "open(",
        ):
            self.assertNotIn(banned, source)
