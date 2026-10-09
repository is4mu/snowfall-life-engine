"""Canonical CLI entry-point, delegating semantics to the existing CLI."""

from __future__ import annotations

from engine.life.cli import main as _legacy_main


def main(argv: list[str] | None = None) -> int:
    return _legacy_main(argv, prog="snowfall-life")


__all__ = ["main"]
