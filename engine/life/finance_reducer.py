"""Slice 3E — pure FinanceState reducer for strict FINANCE_TRANSACTION.

Applies explicit signed-integer JPY transactions only. Does not wire into
Foundation ``apply_effect``, CurrentState, clock/queue, runtime persistence,
or ActualEvent finalization. No category/merchant/account/budget/salary/rent/
savings inference, transaction ledger, or initial-balance invention.

Layering:
- JSON Schema (``effect.schema.json``) is the **structural** layer (required keys,
  types, ``additionalProperties: false``).
- ``normalize_finance_transaction_effect`` is the **semantic** layer: canonicalize
  ``transaction_at`` to Asia/Tokyo, reject bad/naive/fractional timestamps, and
  require true integers (not bool/string/float) for monetary fields.
- ``reduce_finance_transaction`` consumes that already-canonicalized payload and
  applies known-vs-unknown balance / arithmetic / immediate-replay rules.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339
from .world_state import compute_domain_state_hash, validate_finance_state


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_int(value: Any, *, label: str) -> int:
    # bool is a subclass of int — reject explicitly; no string/float coercion.
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label}: expected integer (bool/float/string rejected)")
    return value


def _require_nullable_int(value: Any, *, label: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, label=label)


def normalize_finance_transaction_effect(effect: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic normalize for strict FINANCE_TRANSACTION; does not mutate caller input.

    Structural validation via JSON Schema, then:
    - canonicalize ``transaction_at`` to Asia/Tokyo ``+09:00`` second precision
    - reject bad / naive / fractional timestamps (via WorldState-local time rules)
    - require ``amount_minor`` true integer (not bool/string/float)
    - require ``balance_after_minor`` null or true integer
    - preserve the exact signed integer values

    Returns a new envelope whose ``payload.transaction_at`` is already canonical.
    """
    data = dict(effect)
    if "schema_version" not in data:
        data["schema_version"] = 1
    reject_binary_floats(data, path="$.effect")
    validate_instance(data, "effect")
    if data.get("effect_type") != "FINANCE_TRANSACTION":
        _fail(f"expected FINANCE_TRANSACTION, got {data.get('effect_type')!r}")
    raw_payload = data.get("payload")
    if not isinstance(raw_payload, Mapping):
        _fail("FINANCE_TRANSACTION payload must be an object")

    transaction_at = canonicalize_timestamp(
        raw_payload["transaction_at"], field="transaction_at"
    )
    amount_minor = _require_int(raw_payload["amount_minor"], label="amount_minor")
    balance_after_minor = _require_nullable_int(
        raw_payload["balance_after_minor"], label="balance_after_minor"
    )

    data["payload"] = {
        "transaction_at": transaction_at,
        "amount_minor": amount_minor,
        "balance_after_minor": balance_after_minor,
    }
    return data


def _is_immediate_replay(
    prior: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    source_event_id: str,
) -> bool:
    """Immediate finalized-event replay via snapshot provenance only (no ledger)."""
    prov = prior.get("provenance")
    if not isinstance(prov, Mapping):
        return False
    return (
        prov.get("origin") == "ACTUAL_EVENT"
        and prov.get("source_event_id") == source_event_id
        and prior.get("as_of") == payload["transaction_at"]
        and prior.get("balance_minor") == payload["balance_after_minor"]
    )


def reduce_finance_transaction(
    finance_state: Mapping[str, Any],
    effect: Mapping[str, Any],
    *,
    source_event_id: str,
) -> dict[str, Any]:
    """Pure FinanceState transition for one strict FINANCE_TRANSACTION effect.

    Inputs are not mutated. No file/network I/O, wall clock, PRNG, transaction
    ledger/cursor, or initial-balance invention from amount.
    """
    if not isinstance(source_event_id, str) or not source_event_id:
        _fail("source_event_id: expected non-empty string")

    prior = validate_finance_state(finance_state)
    typed = normalize_finance_transaction_effect(effect)
    payload = typed["payload"]

    transaction_at = payload["transaction_at"]
    amount_minor = payload["amount_minor"]
    balance_after_minor = payload["balance_after_minor"]

    prior_as_of = prior["as_of"]
    if parse_rfc3339(transaction_at, field="transaction_at") < parse_rfc3339(
        prior_as_of, field="as_of"
    ):
        _fail(
            f"FINANCE_TRANSACTION transaction_at before state.as_of "
            f"(transaction_at={transaction_at}, as_of={prior_as_of})"
        )

    # Immediate replay of the same immutable ActualEvent: do not re-apply amount.
    if _is_immediate_replay(prior, payload, source_event_id=source_event_id):
        out_replay = deepcopy(dict(prior))
        return validate_finance_state(out_replay)

    prior_balance = prior["balance_minor"]
    if prior_balance is None:
        if balance_after_minor is not None:
            _fail(
                "unknown prior balance_minor cannot become known via "
                "FINANCE_TRANSACTION (balance_after_minor must be null)"
            )
        # amount is recorded only as an explicit fact; it must not invent balance.
        new_balance: int | None = None
    else:
        if balance_after_minor is None:
            _fail(
                "known prior balance_minor requires integer balance_after_minor "
                "(null rejected)"
            )
        expected = prior_balance + amount_minor
        if balance_after_minor != expected:
            _fail(
                f"balance_after_minor arithmetic mismatch: "
                f"expected {expected} "
                f"(prior {prior_balance} + amount {amount_minor}), "
                f"got {balance_after_minor}"
            )
        new_balance = balance_after_minor

    out: dict[str, Any] = {
        "schema_version": prior["schema_version"],
        "domain": prior["domain"],
        "character_id": prior["character_id"],
        "as_of": transaction_at,
        "provenance": {
            "origin": "ACTUAL_EVENT",
            "source_event_id": source_event_id,
        },
        "currency": prior["currency"],
        "balance_minor": new_balance,
        "revision": "",
        "state_hash": "0" * 64,
    }
    digest = compute_domain_state_hash(out)
    out["state_hash"] = digest
    out["revision"] = digest
    return validate_finance_state(out)
