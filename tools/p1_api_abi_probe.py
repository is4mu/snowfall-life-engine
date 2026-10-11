"""Pure candidate ABI probe for installed wheel/sdist and source CI.

A machine-readable *proposal*, not a declaration of approved stable APIs.
Import only the canonical package and the existing historical ErrorCode; do
not write files or inspect/private user applications. All diagnostics are
deterministic JSON, suitable for comparing clean isolated package installs.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib
import inspect
import json
from pathlib import Path
from typing import Any, Mapping


def _lookup(qualified: str) -> Any:
    package, sep, name = qualified.rpartition(".")
    if not sep:
        raise AssertionError(f"not a fully qualified symbol: {qualified}")
    return getattr(importlib.import_module(package), name)


def inspect_proposal(spec: Mapping[str, Any], scope: Mapping[str, Any]) -> dict:
    """Verify proposed symbol, call, data-shape and Provider contracts."""
    if spec["record_version"] != 1 or spec["status"] != "review-proposal-exact-ABI-NOT-APPROVED":
        raise AssertionError("ABI review status/version was silently changed")
    if spec["owner_approved_scope_ref"] != "oss/p1-v1-basic-api-scope.json":
        raise AssertionError("ABI record lost its approved functional-scope origin")
    assert not spec["exact_signatures_approved"]
    assert not spec["cli_error_contract_approved"]
    assert not spec["final_rc_approved"]
    assert not spec["main_merge_authorized"]
    assert not spec["release_authorized"]
    assert scope["exact_public_signatures_approved"] is False
    assert scope["release_candidate_passed"] is False
    whitelist = scope["candidate_public_python_modules"]
    if set(whitelist) != {
        "snowfall_life", "snowfall_life.schema", "snowfall_life.runtime",
        "snowfall_life.persistence", "snowfall_life.spatial",
    }:
        raise AssertionError("the owner-approved five core modules drifted")

    for module_name, symbols in whitelist.items():
        module = importlib.import_module(module_name)
        if set(getattr(module, "__all__", ())) != set(symbols):
            raise AssertionError(f"core module export drift: {module_name}")
    if "snowfall_life.upgrade" not in spec["excluded_stable_imports"]:
        raise AssertionError("experimental upgrade was silently promoted")
    if set(spec["cli_stability_candidates"]) != set(scope["candidate_basic_cli"]):
        raise AssertionError("the CLI proposal widened approved basic scope")

    function_signatures = {}
    for symbol, expected in spec["function_parameter_candidates"].items():
        if not callable(_lookup(symbol)):
            raise AssertionError(f"not callable: {symbol}")
        signature = inspect.signature(_lookup(symbol))
        got = [
            {
                "name": parameter.name,
                "kind": parameter.kind.name,
                "default": "required" if parameter.default is inspect.Signature.empty else "optional",
            }
            for parameter in signature.parameters.values()
        ]
        if got != expected:
            raise AssertionError(f"proposed ABI drift for {symbol}: {got!r} != {expected!r}")
        if signature.return_annotation is inspect.Signature.empty:
            raise AssertionError(f"public function missing result annotation: {symbol}")
        function_signatures[symbol] = str(signature)

    provider = _lookup("snowfall_life.runtime.RuntimeFactProvider")
    signatures = {}
    for method_name, keywords in spec["provider_protocol_keyword_only_candidates"].items():
        method = getattr(provider, method_name)
        signature = inspect.signature(method)
        args = list(signature.parameters.values())
        if not args or args[0].name != "self":
            raise AssertionError(f"missing self: {method_name}")
        if any(p.kind is not inspect.Parameter.KEYWORD_ONLY for p in args[1:]):
            raise AssertionError(f"non-keyword Provider argument: {method_name}")
        if [p.name for p in args[1:]] != keywords:
            raise AssertionError(f"Provider callback drift: {method_name}")
        if signature.return_annotation is inspect.Signature.empty:
            raise AssertionError(f"Provider result annotation missing: {method_name}")
        signatures[method_name] = str(signature)
    if set(signatures) != {
        "decision_inputs", "materialization_facts",
        "wakeup_facts", "capture_source_moments",
    }:
        raise AssertionError("Provider callback set widened/narrowed")

    classes = {}
    for symbol, expected_fields in spec["frozen_dataclass_field_candidates"].items():
        cls = _lookup(symbol)
        if not dataclasses.is_dataclass(cls):
            raise AssertionError(f"not a dataclass: {symbol}")
        if not cls.__dataclass_params__.frozen:
            raise AssertionError(f"result is not frozen: {symbol}")
        fields = [item.name for item in dataclasses.fields(cls)]
        if fields != expected_fields:
            raise AssertionError(f"data shape drift: {symbol}: {fields!r}")
        classes[symbol] = fields

    from engine.life import ENGINE_VERSION, ErrorCode, LifeEngineError
    if ENGINE_VERSION != spec["storage_epoch"] != scope["persisted_engine_epoch"]:
        raise AssertionError("stored engine epoch changed")
    if spec["storage_writer"] != "W1" or spec["schema_families"] != 16:
        raise AssertionError("initial writer W1 changed")
    if not issubclass(LifeEngineError, Exception):
        raise AssertionError("typed engine exception lost")
    for code in ("INVALID_STATE", "SCHEMA_INVALID", "UNSUPPORTED_VERSION",
                 "HISTORY_HASH_MISMATCH", "POLICY_HASH_MISMATCH"):
        if getattr(ErrorCode, code).value != code:
            raise AssertionError(f"public error enum drift: {code}")

    normalized = {
        "functions": function_signatures,
        "provider_callbacks": signatures,
        "frozen_dataclasses": classes,
        "canonical_core_exports": {k: sorted(v) for k, v in whitelist.items()},
    }
    source = json.dumps(normalized, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return {
        "status": "PASS",
        "scope": "candidate-v1-abi-review-only",
        "api_approved": False,
        "module_count": len(whitelist),
        "functions_verified": len(function_signatures),
        "provider_callbacks_verified": len(signatures),
        "frozen_shapes_verified": len(classes),
        "candidate_signature_sha256": digest,
        "w1_engine_epoch": ENGINE_VERSION,
        "experimental_upgrade_excluded": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--scope", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    print(json.dumps(inspect_proposal(spec, scope), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
