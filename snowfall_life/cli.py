"""Canonical CLI preview: normalize *basic diagnostic input* failures only.

Existing engine.life.cli still owns the parser and Foundation sandbox
operations. Only the proposed basic JSON-reader commands gain a narrow,
deterministic error boundary. This is NOT a finalized v1 stdout/exit ABI.
"""

from __future__ import annotations

import json
import sys

from engine.life import ErrorCode
from engine.life.cli import main as _legacy_main

_BASIC_JSON_COMMANDS = frozenset({"validate", "canonical-hash"})


def main(argv: list[str] | None = None) -> int:
    requested = list(sys.argv[1:] if argv is None else argv)
    command = requested[0] if requested else None
    try:
        return _legacy_main(requested, prog="snowfall-life")
    except (json.JSONDecodeError, UnicodeError):
        if command not in _BASIC_JSON_COMMANDS:
            raise
        code = (
            ErrorCode.SCHEMA_INVALID
            if command == "validate"
            else ErrorCode.INVALID_STATE
        )
        # No user-supplied path, JSON fragment, or exception text on stderr.
        print(f"ERROR {code.value}: invalid JSON or UTF-8 input", file=sys.stderr)
        return 2
    except OSError:
        if command not in _BASIC_JSON_COMMANDS:
            raise
        print(
            f"ERROR {ErrorCode.INVALID_STATE.value}: unable to read JSON input",
            file=sys.stderr,
        )
        return 2


__all__ = ["main"]
