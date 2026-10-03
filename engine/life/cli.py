"""CLI for Life Engine Foundation (dry-run / sandbox only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import ENGINE_VERSION, SUPPORTED_ENGINE_VERSIONS, __name__ as pkg_name
from .bootstrap import hash_proposal, write_proposal
from .canonical import canonical_hash, persisted_hash
from .checkpoint import empty_checkpoint, write_checkpoint
from .clock import advance, verify_workspace
from .errors import LifeEngineError
from .schema import SCHEMA_NAMES, load_schema_document, validate_instance
from .timeutil import format_rfc3339, parse_rfc3339
from .workspace import assert_safe_workspace


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_validate(args: argparse.Namespace) -> int:
    data = _load_json(Path(args.file))
    validate_instance(data, args.schema)
    print(f"OK schema={args.schema}")
    return 0


def cmd_canonical_hash(args: argparse.Namespace) -> int:
    data = _load_json(Path(args.file))
    if args.raw:
        print(canonical_hash(data))
    else:
        # Default: Foundation persisted-object path (timestamps + unordered collections).
        print(persisted_hash(data))
    return 0


def cmd_bootstrap_propose(args: argparse.Namespace) -> int:
    # Synthetic / fixture only — never invents production character parameters from application canon.
    proposed_at = args.proposed_at
    parse_rfc3339(proposed_at, field="proposed_at")
    life_epoch = args.life_epoch
    parse_rfc3339(life_epoch, field="life_epoch")
    proposal = {
        "schema_version": 1,
        "status": "PROPOSED",
        "proposed_at": proposed_at,
        "character_id": args.character_id,
        "timezone": "Asia/Tokyo",
        "life_epoch": life_epoch,
        "world_seed": args.world_seed,
        "engine_version": ENGINE_VERSION,
        "behavior_policy_version": args.policy_version,
        "initial_location_id": args.location_id,
        "circadian_profile_ref": args.circadian_ref,
        "notes": "SYNTHETIC_FIXTURE_ONLY",
        "initial_human_state": {
            "sleep_debt_min": args.sleep_debt_min,
            "hunger": args.hunger,
            "physical_fatigue": args.physical_fatigue,
            "affect_valence": args.affect_valence,
            "stress": args.stress,
            "social_battery": args.social_battery,
        },
    }
    digest = write_proposal(proposal, args.output)
    print(digest)
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    result = advance(
        args.workspace,
        target=args.target,
        policy_path=args.policy,
        production_mode=args.production,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_verify_workspace(args: argparse.Namespace) -> int:
    verify_workspace(args.workspace)
    print("OK workspace history verified")
    return 0


def cmd_self_check(args: argparse.Namespace) -> int:
    # Importability + schema documents + canonical hash smoke
    assert ENGINE_VERSION in SUPPORTED_ENGINE_VERSIONS
    for name in SCHEMA_NAMES:
        load_schema_document(name)
    sample = {"a": 1, "b": "café"}
    h1 = canonical_hash(sample)
    h2 = canonical_hash({"b": "café", "a": 1})
    assert h1 == h2
    # NFC
    composed = "é"
    decomposed = "e\u0301"
    assert canonical_hash(composed) == canonical_hash(decomposed)
    print(f"OK self-check engine={ENGINE_VERSION} package={pkg_name}")
    return 0


def cmd_init_sandbox(args: argparse.Namespace) -> int:
    """Create a synthetic sandbox checkpoint (fixture IDs only)."""
    ws = assert_safe_workspace(args.workspace)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "timeline").mkdir(exist_ok=True)
    state = empty_checkpoint(
        character_id=args.character_id,
        life_epoch=args.life_epoch,
        world_seed=args.world_seed,
        behavior_policy_version=args.policy_version,
        engine_commit_sha=args.engine_commit_sha,
        location_id=args.location_id,
        human_state={
            "sleep_debt_min": args.sleep_debt_min,
            "hunger": args.hunger,
            "physical_fatigue": args.physical_fatigue,
            "affect_valence": args.affect_valence,
            "stress": args.stress,
            "social_battery": args.social_battery,
        },
    )
    write_checkpoint(ws / "current_state.json", state)
    print(f"wrote {ws / 'current_state.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m engine.life.cli",
        description="Life Engine v2 Slice 1 Foundation CLI (no production life start)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="Validate a JSON file against a named schema")
    p_val.add_argument("file")
    p_val.add_argument("--schema", required=True, choices=sorted(SCHEMA_NAMES))
    p_val.set_defaults(func=cmd_validate)

    p_hash = sub.add_parser(
        "canonical-hash",
        help="Print SHA-256 hash (default: persisted-object normalize for Foundation JSON)",
    )
    p_hash.add_argument("file")
    p_hash.add_argument(
        "--raw",
        action="store_true",
        help="Hash JSON as-is without Foundation timestamp/unordered normalization",
    )
    p_hash.set_defaults(func=cmd_canonical_hash)

    p_boot = sub.add_parser(
        "bootstrap-propose",
        help="Write a SYNTHETIC bootstrap proposal to --output (fixture only)",
    )
    p_boot.add_argument("--output", required=True)
    p_boot.add_argument("--character-id", default="fixture-character")
    p_boot.add_argument("--world-seed", default="fixture-world-seed")
    p_boot.add_argument("--life-epoch", default="2026-01-01T00:00:00+09:00")
    p_boot.add_argument("--proposed-at", default="2026-01-01T00:00:00+09:00")
    p_boot.add_argument("--policy-version", default="fixture-policy-1")
    p_boot.add_argument("--location-id", default="fixture-home")
    p_boot.add_argument("--circadian-ref", default="fixture:circadian-synthetic")
    p_boot.add_argument("--sleep-debt-min", type=int, default=0)
    p_boot.add_argument("--hunger", type=int, default=100)
    p_boot.add_argument("--physical-fatigue", type=int, default=100)
    p_boot.add_argument("--affect-valence", type=int, default=0)
    p_boot.add_argument("--stress", type=int, default=100)
    p_boot.add_argument("--social-battery", type=int, default=800)
    p_boot.set_defaults(func=cmd_bootstrap_propose)

    p_adv = sub.add_parser("advance", help="Advance workspace checkpoint to target time")
    p_adv.add_argument("--workspace", required=True)
    p_adv.add_argument("--target", required=True, help="RFC3339 Asia/Tokyo")
    p_adv.add_argument("--policy", required=True, help="Path to TEST_FIXTURE policy JSON")
    p_adv.add_argument(
        "--production",
        action="store_true",
        help="Production mode (rejects TEST_FIXTURE policy)",
    )
    p_adv.set_defaults(func=cmd_advance)

    p_ver = sub.add_parser("verify-workspace", help="Rehash ledger vs checkpoint")
    p_ver.add_argument("--workspace", required=True)
    p_ver.set_defaults(func=cmd_verify_workspace)

    p_self = sub.add_parser("self-check", help="Smoke checks for Foundation install")
    p_self.set_defaults(func=cmd_self_check)

    p_init = sub.add_parser("init-sandbox", help="Create synthetic sandbox checkpoint")
    p_init.add_argument("--workspace", required=True)
    p_init.add_argument("--character-id", default="fixture-character")
    p_init.add_argument("--world-seed", default="fixture-world-seed")
    p_init.add_argument("--life-epoch", default="2026-01-01T00:00:00+09:00")
    p_init.add_argument("--policy-version", default="fixture-policy-1")
    p_init.add_argument("--engine-commit-sha", default="0" * 40)
    p_init.add_argument("--location-id", default="fixture-home")
    p_init.add_argument("--sleep-debt-min", type=int, default=0)
    p_init.add_argument("--hunger", type=int, default=100)
    p_init.add_argument("--physical-fatigue", type=int, default=100)
    p_init.add_argument("--affect-valence", type=int, default=0)
    p_init.add_argument("--stress", type=int, default=100)
    p_init.add_argument("--social-battery", type=int, default=800)
    p_init.set_defaults(func=cmd_init_sandbox)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except LifeEngineError as exc:
        print(f"ERROR {exc.code.value}: {exc.detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
