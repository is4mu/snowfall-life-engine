"""Capture emitter, stress guard, evidence, and TAKE-bridge integration tests."""

from __future__ import annotations

import copy
import inspect
import unittest

import pytest
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from engine.life.camera_roll import project_camera_roll_records
from engine.life.capture_decisions import (
    RESULT_SKIP,
    RESULT_TAKE,
    resolve_capture_opportunity,
)
from engine.life.capture_emitters import (
    EMITTER_CONTEXT_COMPAT,
    EMITTER_RULE_TO_CAPTURE_KIND,
    STRESS_GUARD_RULE_ID,
    VERY_HIGH_SUPPRESSED_EMITTERS,
    build_capture_source_moment,
    derive_capture_source_key,
    emit_capture_opportunities,
    validate_capture_source_moment,
)
from engine.life.capture_pipeline import apply_capture_take
from engine.life.captures import (
    build_capture_decision_evidence,
    build_capture_fact,
    validate_capture_fact,
)
from engine.life.derived import classify_stress_band
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.events import build_actual_event_from_activity
from engine.life.ids import stable_id
from engine.life.policy import load_policy, normalize_policy
from engine.life.schema import load_schema_document
import engine.life.capture_emitters as capture_emitters_module
import engine.life.capture_pipeline as capture_pipeline_module
from tests.support.constants import POLICY_V2_CANDIDATE_PATH
from tests.support.builders.captures import (
    PUBLIC_NO_CAPTURE_EVENT_HASH,
    PUBLIC_NO_CAPTURE_HISTORY_HASH,
    build_test_capture_fact,
)

FIXTURE = POLICY_V2_CANDIDATE_PATH
CHAR = "fixture-character"
WORLD_SEED = "public-capture-emitter-seed"
POLICY_VERSION = load_policy(POLICY_V2_CANDIDATE_PATH)["behavior_policy_version"]


def _policy() -> dict:
    return load_policy(FIXTURE)


def _policy_mut() -> dict:
    return copy.deepcopy(_policy())


def _visual_context(**overrides) -> dict:
    base = {
        "character_id": CHAR,
        "character_source_sha": "char-sha-capture-emitter",
        "engine_commit_sha": "engine-sha-capture-emitter",
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
        "activity_instance_id": "activity:capture-emitter-fixture-1",
        "activity_type": "LEISURE",
        "actual_start": "2026-04-01T10:00:00+09:00",
        "location_id": "fixture-home",
        "summary": "capture emitter fixture",
    }
    base.update(overrides)
    return base


def _moment(
    *,
    emitter_rule_id: str = "slice4b2.daily.detail",
    source_context_kind: str = "HOME_DETAIL",
    activity_instance_id: str = "activity:capture-emitter-fixture-1",
    semantic_source_ref: str = "semantic:home-detail:1",
    moment_ref: str = "moment:1",
    moment_at: str = "2026-04-01T10:30:00+09:00",
    subject_refs: list | None = None,
    visual_context: dict | None = None,
    input_state_refs: list | None = None,
) -> dict:
    vc = visual_context or _visual_context()
    kind = EMITTER_RULE_TO_CAPTURE_KIND[emitter_rule_id]
    refs = [] if subject_refs is None else subject_refs
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
        input_state_refs=["state:src-b", "state:src-a"]
        if input_state_refs is None
        else input_state_refs,
    )

pytestmark = pytest.mark.integration


class CaptureEmitterSchemaTests(unittest.TestCase):
    def test_new_and_modified_schemas_draft_2020_12_meta_valid(self) -> None:
        for name in (
            "capture",
            "capture_source_moment",
            "capture_decision_evidence",
            "capture_opportunity",
            "camera_roll_record",
        ):
            schema = load_schema_document(name)
            self.assertEqual(
                schema["$schema"], "https://json-schema.org/draft/2020-12/schema"
            )
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:  # pragma: no cover
                self.fail(f"{name} schema meta-invalid: {exc}")

class CaptureEmitterPolicyTests(unittest.TestCase):
    def test_public_capture_policy_and_provenance(self) -> None:
        policy = _policy()
        self.assertEqual(policy["behavior_policy_version"], POLICY_VERSION)
        self.assertTrue(POLICY_VERSION.startswith("public-fixture-v2-candidate-"))
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
        entry = next(
            e for e in policy["provenance_index"] if e["path"] == "capture_policy"
        )
        self.assertEqual(entry["origin"], "ENGINE_DEFAULT")
        self.assertEqual(entry["source_kind"], "ENGINE_RULE")
        self.assertEqual(entry["source_ref"], "public-synthetic-policy-defaults")
        self.assertIsNone(entry["source_commit_sha"])
        self.assertIn("non-authoritative", entry["rationale"].lower())
        notes = " ".join(policy["calibration_metadata"]["calibration_notes"]).lower()
        self.assertIn("not an approved production policy", notes)

class CaptureSourceMomentTests(unittest.TestCase):
    def test_10_every_approved_emitter_context_pair_validates(self) -> None:
        for emitter, contexts in EMITTER_CONTEXT_COMPAT.items():
            for ctx in sorted(contexts):
                moment = _moment(
                    emitter_rule_id=emitter,
                    source_context_kind=ctx,
                    semantic_source_ref=f"semantic:{emitter}:{ctx}",
                    moment_ref=f"moment:{ctx}",
                )
                again = validate_capture_source_moment(moment)
                self.assertEqual(again["emitter_rule_id"], emitter)
                self.assertEqual(again["source_context_kind"], ctx)

    def test_11_wrong_emitter_context_pair_rejected(self) -> None:
        with self.assertRaises(LifeEngineError) as ctx:
            _moment(
                emitter_rule_id="slice4b2.selfie.appearance",
                source_context_kind="TRAVEL_VIEW",
            )
        self.assertEqual(ctx.exception.code, ErrorCode.SCHEMA_INVALID)

    def test_12_unknown_emitter_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(
                emitter_rule_id="slice4b2.unknown",
                source_context_kind="HOME_DETAIL",
                activity_instance_id="activity:x",
                semantic_source_ref="s",
                moment_ref="m",
                moment_at="2026-04-01T10:30:00+09:00",
                subject_refs=[],
                visual_context=_visual_context(),
                input_state_refs=["state:a"],
            )

    def test_13_unknown_context_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(
                emitter_rule_id="slice4b2.daily.detail",
                source_context_kind="ROUTINE_MEAL",
                activity_instance_id="activity:x",
                semantic_source_ref="s",
                moment_ref="m",
                moment_at="2026-04-01T10:30:00+09:00",
                subject_refs=[],
                visual_context=_visual_context(),
                input_state_refs=["state:a"],
            )

    def test_14_unknown_field_rejected(self) -> None:
        moment = _moment()
        bad = dict(moment)
        bad["summary"] = "prose"
        with self.assertRaises(LifeEngineError):
            validate_capture_source_moment(bad)

    def test_15_missing_field_rejected(self) -> None:
        moment = _moment()
        bad = dict(moment)
        del bad["moment_ref"]
        with self.assertRaises(LifeEngineError):
            validate_capture_source_moment(bad)

    def test_16_bad_naive_fractional_timestamp_rejected(self) -> None:
        for bad_ts in (
            "not-a-time",
            "2026-04-01T10:30:00",
            "2026-04-01T10:30:00.5+09:00",
        ):
            with self.assertRaises(LifeEngineError):
                _moment(moment_at=bad_ts)

    def test_17_19_ref_array_strictness(self) -> None:
        with self.assertRaises(LifeEngineError):
            _moment(input_state_refs=[123])  # type: ignore[list-item]
        with self.assertRaises(LifeEngineError):
            _moment(input_state_refs=["a", "a"])
        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(
                emitter_rule_id="slice4b2.daily.detail",
                source_context_kind="HOME_DETAIL",
                activity_instance_id="activity:capture-emitter-fixture-1",
                semantic_source_ref="s",
                moment_ref="m",
                moment_at="2026-04-01T10:30:00+09:00",
                subject_refs=[],
                visual_context=_visual_context(),
                input_state_refs="state:a",  # type: ignore[arg-type]
            )

    def test_subject_refs_strict_json_array_build_and_validate(self) -> None:
        """4A.1 reuse: subject_refs must already be a list; no Sequence coercion."""
        base_kwargs = dict(
            emitter_rule_id="slice4b2.daily.detail",
            source_context_kind="HOME_DETAIL",
            activity_instance_id="activity:capture-emitter-fixture-1",
            semantic_source_ref="semantic:subj-strict",
            moment_ref="moment:1",
            moment_at="2026-04-01T10:30:00+09:00",
            visual_context=_visual_context(),
            input_state_refs=["state:a"],
        )
        # Valid list is accepted and locally canonicalized.
        built = build_capture_source_moment(
            **base_kwargs,
            subject_refs=["z-item", "a-item"],
        )
        self.assertEqual(built["subject_refs"], ["a-item", "z-item"])
        again = validate_capture_source_moment(built)
        self.assertEqual(again["subject_refs"], ["a-item", "z-item"])

        rejected_containers = (
            ("tuple", ("a-item",)),
            ("string", "a-item"),
            ("bytes", b"a-item"),
            ("null", None),
            ("dict", {"a": 1}),
            ("set", {"a-item"}),
        )
        for label, bad in rejected_containers:
            with self.subTest(path="build", container=label):
                with self.assertRaises(LifeEngineError) as ctx:
                    build_capture_source_moment(**base_kwargs, subject_refs=bad)  # type: ignore[arg-type]
                self.assertIn(ctx.exception.code, {ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE})
            with self.subTest(path="validate", container=label):
                raw = dict(built)
                raw["subject_refs"] = bad
                with self.assertRaises(LifeEngineError) as ctx:
                    validate_capture_source_moment(raw)
                self.assertIn(ctx.exception.code, {ErrorCode.SCHEMA_INVALID, ErrorCode.INVALID_STATE})

        # Generator is not a JSON array and must fail closed (fresh per call).
        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(
                **base_kwargs,
                subject_refs=(x for x in ["a-item"]),  # type: ignore[arg-type]
            )
        raw_gen = dict(built)
        raw_gen["subject_refs"] = (x for x in ["a-item"])
        with self.assertRaises(LifeEngineError):
            validate_capture_source_moment(raw_gen)

        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(**base_kwargs, subject_refs=["a", "a"])
        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(**base_kwargs, subject_refs=[""])
        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(**base_kwargs, subject_refs=[123])  # type: ignore[list-item]

        # Raw validate path: duplicate / empty / non-string.
        for bad_refs in (["a", "a"], [""], [123]):
            raw = dict(built)
            raw["subject_refs"] = bad_refs
            with self.assertRaises(LifeEngineError):
                validate_capture_source_moment(raw)

    def test_20_subject_visual_strictness_reused(self) -> None:
        with self.assertRaises(LifeEngineError):
            build_capture_source_moment(
                emitter_rule_id="slice4b2.selfie.appearance",
                source_context_kind="OUTFIT_COMPARISON",
                activity_instance_id="activity:capture-emitter-fixture-1",
                semantic_source_ref="semantic:bad-selfie",
                moment_ref="moment:1",
                moment_at="2026-04-01T10:30:00+09:00",
                subject_refs=["friend-only"],
                visual_context=_visual_context(photographer_ref=CHAR),
                input_state_refs=["state:a"],
            )

    def test_21_23_stable_source_key(self) -> None:
        a = derive_capture_source_key(
            "slice4b2.daily.detail", "semantic:x", "moment:1"
        )
        b = derive_capture_source_key(
            "slice4b2.daily.detail", "semantic:x", "moment:1"
        )
        self.assertEqual(a, b)
        self.assertEqual(
            a,
            stable_id(
                "capture-source",
                "slice4b2.daily.detail",
                "semantic:x",
                "moment:1",
            ),
        )
        # Unrelated insertion/order of args would be a different call; same args => same key.
        self.assertNotEqual(
            a,
            derive_capture_source_key(
                "slice4b2.daily.detail", "semantic:x", "moment:2"
            ),
        )
        # Timestamp is not an identity input.
        src = inspect.signature(derive_capture_source_key).parameters
        self.assertNotIn("moment_at", src)
        self.assertNotIn("timestamp", src)

    def test_24_no_input_mutation(self) -> None:
        refs = ["state:z", "state:a"]
        refs_before = list(refs)
        vc = _visual_context(appearance={"outfit_item_ids": ["z", "a"]})
        vc_before = copy.deepcopy(vc)
        build_capture_source_moment(
            emitter_rule_id="slice4b2.daily.detail",
            source_context_kind="HOME_DETAIL",
            activity_instance_id="activity:capture-emitter-fixture-1",
            semantic_source_ref="s",
            moment_ref="m",
            moment_at="2026-04-01T10:30:00+09:00",
            subject_refs=[],
            visual_context=vc,
            input_state_refs=refs,
        )
        self.assertEqual(refs, refs_before)
        self.assertEqual(vc, vc_before)

    def test_25_no_summary_prose_parsing_path(self) -> None:
        src = Path("engine/life/capture_emitters.py").read_text(encoding="utf-8")
        self.assertNotIn("summary", src.lower().split("forbidden")[0] if False else "")
        # Emitters module must not parse free-form summary fields.
        self.assertNotRegex(src, r"\[['\"]summary['\"]\]")
        self.assertNotIn(".get(\"summary\")", src)
        self.assertNotIn(".get('summary')", src)

class CaptureEmitterTests(unittest.TestCase):
    def test_26_27_one_or_zero_opportunities(self) -> None:
        policy = _policy()
        act = _activity()
        empty = emit_capture_opportunities(
            policy=policy,
            active_activity=act,
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[],
        )
        self.assertEqual(empty.opportunities, ())
        one = emit_capture_opportunities(
            policy=policy,
            active_activity=act,
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[_moment()],
        )
        self.assertEqual(len(one.opportunities), 1)

    def test_28_sleep_no_opportunity(self) -> None:
        result = emit_capture_opportunities(
            policy=_policy(),
            active_activity=_activity(activity_type="SLEEP"),
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[_moment()],
        )
        self.assertEqual(result.opportunities, ())
        self.assertTrue(any(s.code == "SLEEP_ACTIVE" for s in result.suppressed))

    def test_28b_sleep_validates_before_suppression(self) -> None:
        """SLEEP emits zero opportunities, but malformed/conflicting sources fail closed."""
        sleep_act = _activity(activity_type="SLEEP")
        policy = _policy()

        # 1. wrong activity owner rejected
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=policy,
                active_activity=sleep_act,
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[_moment(activity_instance_id="activity:other")],
            )

        # 2. moment before actual_start rejected
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=policy,
                active_activity=sleep_act,
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[_moment(moment_at="2026-04-01T09:00:00+09:00")],
            )

        # 3. policy character mismatch rejected
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=policy,
                active_activity=sleep_act,
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[
                    _moment(visual_context=_visual_context(character_id="other-char"))
                ],
            )

        # 4. policy version mismatch rejected
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=policy,
                active_activity=sleep_act,
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[
                    _moment(
                        visual_context=_visual_context(
                            behavior_policy_version="fixture-v2-candidate-2"
                        )
                    )
                ],
            )

        # 5. identical duplicate canonicalized once
        m = _moment()
        dup = emit_capture_opportunities(
            policy=policy,
            active_activity=sleep_act,
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[m, copy.deepcopy(m)],
        )
        self.assertEqual(dup.opportunities, ())
        self.assertEqual(len(dup.suppressed), 1)
        self.assertEqual(dup.suppressed[0].code, "SLEEP_ACTIVE")

        # 6. conflicting same source_key rejected
        conflict = copy.deepcopy(m)
        conflict["input_state_refs"] = ["state:different"]
        with self.assertRaises(LifeEngineError) as ctx:
            emit_capture_opportunities(
                policy=policy,
                active_activity=sleep_act,
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[m, conflict],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

    def test_29_owner_mismatch_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=_policy(),
                active_activity=_activity(),
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[_moment(activity_instance_id="activity:other")],
            )

    def test_30_source_before_active_start_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=_policy(),
                active_activity=_activity(),
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[_moment(moment_at="2026-04-01T09:00:00+09:00")],
            )

    def test_31_policy_character_version_mismatch_rejected(self) -> None:
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=_policy(),
                active_activity=_activity(),
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[
                    _moment(visual_context=_visual_context(character_id="other-char"))
                ],
            )
        with self.assertRaises(LifeEngineError):
            emit_capture_opportunities(
                policy=_policy(),
                active_activity=_activity(),
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[
                    _moment(
                        visual_context=_visual_context(
                            behavior_policy_version="fixture-v2-candidate-2"
                        )
                    )
                ],
            )

    def test_32_35_kind_mapping(self) -> None:
        cases = [
            ("slice4b2.selfie.appearance", "PRE_OUTING_SELF_CHECK", "SELFIE"),
            ("slice4b2.selfie.contextual", "TRAVEL_SELFIE", "SELFIE"),
            ("slice4b2.social.group_selfie", "IN_PERSON_SOCIAL", "SELFIE"),
            ("slice4b2.social.people", "IN_PERSON_SOCIAL", "PEOPLE"),
            ("slice4b2.meal.contextual", "SOCIAL_MEAL", "FOOD"),
            ("slice4b2.scenery.view", "TRAVEL_VIEW", "SCENERY"),
            ("slice4b2.object.detail", "SHOPPING_ITEM", "OBJECT"),
            ("slice4b2.daily.detail", "CAMPUS_DETAIL", "DAILY_LIFE"),
        ]
        for emitter, ctx, kind in cases:
            result = emit_capture_opportunities(
                policy=_policy(),
                active_activity=_activity(),
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[
                    _moment(
                        emitter_rule_id=emitter,
                        source_context_kind=ctx,
                        semantic_source_ref=f"sem:{emitter}",
                        moment_ref=f"m:{ctx}",
                        subject_refs=[CHAR, "friend-a"]
                        if emitter == "slice4b2.social.group_selfie"
                        else None,
                    )
                ],
            )
            self.assertEqual(len(result.opportunities), 1)
            self.assertEqual(result.opportunities[0]["capture_kind"], kind)
            if emitter == "slice4b2.social.group_selfie":
                opp = result.opportunities[0]
                self.assertEqual(opp["visual_context"]["photographer_ref"], CHAR)
                self.assertIn(CHAR, opp["subject_refs"])

    def test_36_remote_only_social_not_representable(self) -> None:
        # Closed context enum has no REMOTE_*; unknown context fails closed.
        with self.assertRaises(LifeEngineError):
            _moment(
                emitter_rule_id="slice4b2.social.people",
                source_context_kind="REMOTE_MESSAGE",
            )

    def test_37_38_food_contextual_only(self) -> None:
        # Routine meal kinds are not in the allowed FOOD context set.
        for bad in ("HOME_COOKED", "CONVENIENCE", "OUTSIDE"):
            with self.assertRaises(LifeEngineError):
                _moment(
                    emitter_rule_id="slice4b2.meal.contextual",
                    source_context_kind=bad,
                )
        for ctx in sorted(EMITTER_CONTEXT_COMPAT["slice4b2.meal.contextual"]):
            result = emit_capture_opportunities(
                policy=_policy(),
                active_activity=_activity(),
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[
                    _moment(
                        emitter_rule_id="slice4b2.meal.contextual",
                        source_context_kind=ctx,
                        semantic_source_ref=f"meal:{ctx}",
                        moment_ref=f"m:{ctx}",
                    )
                ],
            )
            self.assertEqual(result.opportunities[0]["capture_kind"], "FOOD")

    def test_39_41_scenery_object_daily_require_explicit(self) -> None:
        with self.assertRaises(LifeEngineError):
            _moment(
                emitter_rule_id="slice4b2.scenery.view",
                source_context_kind="COMMUTE",
            )
        result = emit_capture_opportunities(
            policy=_policy(),
            active_activity=_activity(),
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[
                _moment(
                    emitter_rule_id="slice4b2.object.detail",
                    source_context_kind="OWNED_ITEM_DETAIL",
                    subject_refs=["item:bag-1"],
                    semantic_source_ref="object:bag",
                )
            ],
        )
        self.assertEqual(result.opportunities[0]["subject_refs"], ["item:bag-1"])
        # No once-per-day fallback: empty sources => empty opportunities.
        none = emit_capture_opportunities(
            policy=_policy(),
            active_activity=_activity(),
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[],
        )
        self.assertEqual(none.opportunities, ())

    def test_42_travel_increases_only_via_distinct_moments(self) -> None:
        ordinary = emit_capture_opportunities(
            policy=_policy(),
            active_activity=_activity(),
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[_moment()],
        )
        travel_moments = [
            _moment(
                emitter_rule_id="slice4b2.scenery.view",
                source_context_kind="TRAVEL_VIEW",
                semantic_source_ref="travel:view",
                moment_ref="m1",
            ),
            _moment(
                emitter_rule_id="slice4b2.selfie.contextual",
                source_context_kind="TRAVEL_SELFIE",
                semantic_source_ref="travel:selfie",
                moment_ref="m2",
            ),
            _moment(
                emitter_rule_id="slice4b2.meal.contextual",
                source_context_kind="TRAVEL_MEAL",
                semantic_source_ref="travel:meal",
                moment_ref="m3",
            ),
        ]
        travel = emit_capture_opportunities(
            policy=_policy(),
            active_activity=_activity(),
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=travel_moments,
        )
        self.assertGreater(len(travel.opportunities), len(ordinary.opportunities))
        self.assertEqual(len(travel.opportunities), 3)

    def test_43_44_no_prng_no_take_skip(self) -> None:
        src = Path("engine/life/capture_emitters.py").read_text(encoding="utf-8")
        self.assertNotIn("keyed_digest", src)
        self.assertNotIn("from .rng", src)
        self.assertNotIn("u01(", src)
        self.assertNotIn("TAKE_CAPTURE", src)
        self.assertNotIn("SKIP_CAPTURE", src)
        self.assertNotIn("resolve_capture_opportunity", src)
        # Emitter returns opportunities only; no resolution side effects.
        result = emit_capture_opportunities(
            policy=_policy(),
            active_activity=_activity(),
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[_moment()],
        )
        self.assertEqual(len(result.opportunities), 1)
        self.assertNotIn("result_kind", result.opportunities[0])

    def test_45_46_source_dedupe(self) -> None:
        m = _moment()
        dup = emit_capture_opportunities(
            policy=_policy(),
            active_activity=_activity(),
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[m, copy.deepcopy(m)],
        )
        self.assertEqual(len(dup.opportunities), 1)
        conflict = copy.deepcopy(m)
        conflict["input_state_refs"] = ["state:different"]
        with self.assertRaises(LifeEngineError) as ctx:
            emit_capture_opportunities(
                policy=_policy(),
                active_activity=_activity(),
                stress=100,
                stress_state_ref="state:stress:1",
                source_moments=[m, conflict],
            )
        self.assertEqual(ctx.exception.code, ErrorCode.DUPLICATE_ID)

class CaptureEmitterStressTests(unittest.TestCase):
    def test_47_57_stress_bands_and_suppression(self) -> None:
        policy = _policy()
        thresholds = policy["derived_bands"]["stress"]
        # Existing thresholds reused (no new emitter-local cutoffs).
        self.assertEqual(thresholds["VERY_HIGH"], 800)
        self.assertEqual(classify_stress_band(100, policy), "LOW")
        self.assertEqual(classify_stress_band(300, policy), "MODERATE")
        self.assertEqual(classify_stress_band(550, policy), "HIGH")
        self.assertEqual(classify_stress_band(800, policy), "VERY_HIGH")

        optional = [
            ("slice4b2.selfie.appearance", "PRE_OUTING_SELF_CHECK"),
            ("slice4b2.selfie.contextual", "OUTING_SELFIE"),
            ("slice4b2.social.group_selfie", "IN_PERSON_SOCIAL"),
            ("slice4b2.meal.contextual", "SOCIAL_MEAL"),
            ("slice4b2.scenery.view", "OUTING_VIEW"),
            ("slice4b2.object.detail", "OUTFIT_DETAIL"),
            ("slice4b2.daily.detail", "HOME_DETAIL"),
        ]
        for emitter, ctx in optional:
            self.assertIn(emitter, VERY_HIGH_SUPPRESSED_EMITTERS)
            moments = [
                _moment(
                    emitter_rule_id=emitter,
                    source_context_kind=ctx,
                    semantic_source_ref=f"sem:{emitter}",
                    moment_ref="m1",
                    subject_refs=[CHAR, "friend-a"]
                    if emitter == "slice4b2.social.group_selfie"
                    else None,
                )
            ]
            for stress, band in ((100, "LOW"), (300, "MODERATE"), (550, "HIGH")):
                result = emit_capture_opportunities(
                    policy=policy,
                    active_activity=_activity(),
                    stress=stress,
                    stress_state_ref="state:stress:1",
                    source_moments=moments,
                )
                self.assertEqual(len(result.opportunities), 1, msg=f"{emitter}/{band}")
            suppressed = emit_capture_opportunities(
                policy=policy,
                active_activity=_activity(),
                stress=800,
                stress_state_ref="state:stress:1",
                source_moments=moments,
            )
            self.assertEqual(suppressed.opportunities, (), msg=emitter)
            self.assertTrue(
                any(s.code == "VERY_HIGH_STRESS" for s in suppressed.suppressed)
            )

        # social.people remains eligible at VERY_HIGH.
        people = emit_capture_opportunities(
            policy=policy,
            active_activity=_activity(),
            stress=850,
            stress_state_ref="state:stress:1",
            source_moments=[
                _moment(
                    emitter_rule_id="slice4b2.social.people",
                    source_context_kind="IN_PERSON_SOCIAL",
                    subject_refs=["friend-a"],
                )
            ],
        )
        self.assertEqual(len(people.opportunities), 1)
        self.assertEqual(people.opportunities[0]["capture_kind"], "PEOPLE")

        src = Path("engine/life/capture_emitters.py").read_text(encoding="utf-8")
        self.assertNotIn("stress_multiplier", src)
        self.assertNotIn("* stress", src)
        self.assertIn("classify_stress_band", src)

class CaptureTakeBridgeTests(unittest.TestCase):
    def _force_take_policy(self) -> dict:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 1000
        return normalize_policy(policy)

    def _force_skip_policy(self) -> dict:
        policy = _policy_mut()
        for kind in policy["capture_policy"]["activation_permille_by_kind"]:
            policy["capture_policy"]["activation_permille_by_kind"][kind] = 0
        return normalize_policy(policy)

    def test_58_66_capture_requires_valid_take_evidence(self) -> None:
        with self.assertRaises(LifeEngineError):
            build_capture_fact(
                activity_instance_id="activity:capture-emitter-fixture-1",
                occurrence_key="occ",
                captured_at="2026-04-01T10:30:00+09:00",
                capture_kind="DAILY_LIFE",
                subject_refs=[],
                visual_context=_visual_context(),
                capture_decision_evidence={},  # malformed
            )
        # SKIP cannot appear.
        with self.assertRaises(LifeEngineError):
            build_capture_decision_evidence(
                activity_instance_id="activity:capture-emitter-fixture-1",
                source_key="source:x",
                policy_version=POLICY_VERSION,
                activation_permille=500,
                rule_ids=["r"],
                input_state_refs=["s"],
                random_key="a" * 64,
                selected_result="SKIP_CAPTURE",
            )
        with self.assertRaises(LifeEngineError):
            build_capture_decision_evidence(
                activity_instance_id="activity:capture-emitter-fixture-1",
                source_key="source:x",
                policy_version=POLICY_VERSION,
                activation_permille=500,
                rule_ids=["r"],
                input_state_refs=["s"],
                random_key="ZZ" * 32,
            )
        with self.assertRaises(LifeEngineError):
            build_capture_decision_evidence(
                activity_instance_id="activity:capture-emitter-fixture-1",
                source_key="source:x",
                policy_version=POLICY_VERSION,
                activation_permille=1001,
                rule_ids=["r"],
                input_state_refs=["s"],
                random_key="a" * 64,
            )
        # Wrong opportunity / occurrence derivation rejected.
        good = build_test_capture_fact(
            activity_instance_id="activity:capture-emitter-fixture-1",
            occurrence_key="decision:photo:1",
            captured_at="2026-04-01T10:30:00+09:00",
            capture_kind="DAILY_LIFE",
            subject_refs=[],
            visual_context=_visual_context(),
        )
        bad_opp = dict(good)
        evidence = dict(bad_opp["capture_decision_evidence"])
        evidence["opportunity_key"] = "forged-opportunity"
        bad_opp["capture_decision_evidence"] = evidence
        with self.assertRaises(LifeEngineError):
            validate_capture_fact(bad_opp)
        bad_occ = dict(good)
        bad_occ["occurrence_key"] = "forged-occurrence"
        bad_occ["capture_id"] = stable_id(
            "capture", bad_occ["activity_instance_id"], "forged-occurrence"
        )
        with self.assertRaises(LifeEngineError):
            validate_capture_fact(bad_occ)
        # Policy version mismatch.
        bad_pol = dict(good)
        ev = dict(bad_pol["capture_decision_evidence"])
        ev["policy_version"] = "other-policy"
        # Rebuild opportunity_key consistency but wrong policy vs visual.
        bad_pol["capture_decision_evidence"] = ev
        with self.assertRaises(LifeEngineError):
            validate_capture_fact(bad_pol)
        # Arrays canonical.
        self.assertEqual(
            good["capture_decision_evidence"]["rule_ids"],
            sorted(good["capture_decision_evidence"]["rule_ids"]),
        )

    def test_67_76_take_bridge(self) -> None:
        policy = self._force_take_policy()
        act = _activity()
        emitted = emit_capture_opportunities(
            policy=policy,
            active_activity=act,
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[_moment()],
        )
        opp = emitted.opportunities[0]
        resolution = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=policy,
            active_activity=act,
            opportunity=opp,
        )
        self.assertEqual(resolution.result_kind, RESULT_TAKE)
        updated = apply_capture_take(
            world_seed=WORLD_SEED,
            policy=policy,
            active_activity=act,
            opportunity=opp,
            resolution=resolution,
        )
        self.assertEqual(len(updated["pending_captures"]), 1)
        cap = updated["pending_captures"][0]
        self.assertEqual(cap["captured_at"], opp["opportunity_at"])
        self.assertEqual(cap["capture_kind"], opp["capture_kind"])
        self.assertEqual(cap["subject_refs"], opp["subject_refs"])
        self.assertEqual(cap["visual_context"], opp["visual_context"])
        self.assertEqual(
            cap["capture_decision_evidence"]["selected_result"], RESULT_TAKE
        )
        self.assertEqual(
            cap["capture_decision_evidence"]["opportunity_key"], opp["opportunity_key"]
        )
        self.assertEqual(cap["occurrence_key"], resolution.occurrence_key)

        # Replay idempotent.
        again = apply_capture_take(
            world_seed=WORLD_SEED,
            policy=policy,
            active_activity=updated,
            opportunity=opp,
            resolution=resolution,
        )
        self.assertEqual(len(again["pending_captures"]), 1)

        # Forged resolution rejected.
        forged = copy.deepcopy(resolution.as_dict())
        forged["activation_permille"] = 1
        with self.assertRaises(LifeEngineError):
            apply_capture_take(
                world_seed=WORLD_SEED,
                policy=policy,
                active_activity=act,
                opportunity=opp,
                resolution=forged,
            )

        # SKIP rejected.
        skip_policy = self._force_skip_policy()
        skip_res = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=skip_policy,
            active_activity=act,
            opportunity=opp,
        )
        self.assertEqual(skip_res.result_kind, RESULT_SKIP)
        with self.assertRaises(LifeEngineError):
            apply_capture_take(
                world_seed=WORLD_SEED,
                policy=skip_policy,
                active_activity=act,
                opportunity=opp,
                resolution=skip_res,
            )

        # No input mutation.
        act_before = copy.deepcopy(act)
        opp_before = copy.deepcopy(opp)
        apply_capture_take(
            world_seed=WORLD_SEED,
            policy=policy,
            active_activity=act,
            opportunity=opp,
            resolution=resolution,
        )
        self.assertEqual(act, act_before)
        self.assertEqual(opp, opp_before)

        # Same capture id + changed evidence conflicts.
        conflict_act = copy.deepcopy(updated)
        conflict_cap = copy.deepcopy(cap)
        conflict_cap["capture_decision_evidence"] = dict(
            conflict_cap["capture_decision_evidence"]
        )
        conflict_cap["capture_decision_evidence"]["random_key"] = "b" * 64
        with self.assertRaises(LifeEngineError):
            from engine.life.captures import attach_capture_to_active_activity

            attach_capture_to_active_activity(conflict_act, conflict_cap)

        # Same capture id + changed fact conflicts.
        conflict_cap2 = copy.deepcopy(cap)
        conflict_cap2["captured_at"] = "2026-04-01T10:31:00+09:00"
        with self.assertRaises(LifeEngineError):
            from engine.life.captures import attach_capture_to_active_activity

            attach_capture_to_active_activity(conflict_act, conflict_cap2)

    def test_77_81_finalization_and_camera_roll_continuity(self) -> None:
        policy = self._force_take_policy()
        act = _activity()
        emitted = emit_capture_opportunities(
            policy=policy,
            active_activity=act,
            stress=100,
            stress_state_ref="state:stress:1",
            source_moments=[_moment()],
        )
        opp = emitted.opportunities[0]
        resolution = resolve_capture_opportunity(
            world_seed=WORLD_SEED,
            policy=policy,
            active_activity=act,
            opportunity=opp,
        )
        pending = apply_capture_take(
            world_seed=WORLD_SEED,
            policy=policy,
            active_activity=act,
            opportunity=opp,
            resolution=resolution,
        )
        # No Camera Roll before finalization.
        with self.assertRaises(LifeEngineError):
            project_camera_roll_records(pending)
        event = build_actual_event_from_activity(
            pending,
            actual_end="2026-04-01T12:00:00+09:00",
            finalized_at="2026-04-01T12:00:00+09:00",
        )
        self.assertEqual(
            event["captures"][0]["capture_decision_evidence"],
            pending["pending_captures"][0]["capture_decision_evidence"],
        )
        # Event-level decision_evidence remains singular/nullable — not overloaded.
        self.assertIn("decision_evidence", event)
        self.assertIsNone(event["decision_evidence"])
        records = project_camera_roll_records(event)
        self.assertEqual(len(records), 1)
        self.assertNotIn("capture_decision_evidence", records[0])
        schema = load_schema_document("camera_roll_record")
        self.assertNotIn("capture_decision_evidence", schema["properties"])

class CaptureEmitterBoundaryTests(unittest.TestCase):
    def test_emitters_and_take_bridge_have_no_runtime_or_product_feedback_dependencies(self) -> None:
        for module in (capture_emitters_module, capture_pipeline_module):
            source = Path(module.__file__).read_text(encoding="utf-8").lower()
            for banned in (
                "alternate_frame",
                "burst",
                "second_shot",
                "instagram",
                "engagement",
                "daily_target",
                "min_photos",
                "max_photos",
                "camera_roll_full",
                "privacy",
                "heartbeat",
                "write_text",
                "write_bytes",
            ):
                self.assertNotIn(banned, source)

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
        self.assertEqual(event["captures"], [])
        self.assertEqual(persisted_hash(event), PUBLIC_NO_CAPTURE_EVENT_HASH)
        self.assertEqual(
            next_history_hash(EMPTY_HISTORY_HASH, event),
            PUBLIC_NO_CAPTURE_HISTORY_HASH,
        )
