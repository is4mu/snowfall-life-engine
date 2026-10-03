"""Dry-run workspace isolation — never mutate repo runtime roots."""

from __future__ import annotations

from pathlib import Path

from .errors import ErrorCode, LifeEngineError

RESERVED_DIR_NAMES = frozenset(
    {
        "timeline",
        "camera-roll",
        "character",
        "environment",
        "benchmark",
        ".git",
        ".github",
    }
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def assert_safe_workspace(workspace: Path | str, *, repo: Path | None = None) -> Path:
    """Reject workspace pointing at real timeline/, camera-roll/, or reserved checkout roots."""
    root = (repo or repo_root()).resolve()
    ws = Path(workspace).resolve()

    if not ws.exists():
        # Caller may create; still check path components
        pass
    elif not ws.is_dir():
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"workspace not a directory: {ws}")

    # Exact reserved paths under repo
    for name in ("timeline", "camera-roll"):
        reserved = (root / name).resolve()
        try:
            ws.relative_to(reserved)
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"workspace must not point at repo {name}/: {ws}",
            )
        except ValueError:
            pass
        if ws == reserved:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"workspace is repo {name}/")

    # Disallow using the repository root itself as workspace
    if ws == root:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "workspace must not be repository root")

    # If workspace is inside repo, require it under an explicit sandbox-like folder
    try:
        rel = ws.relative_to(root)
    except ValueError:
        return ws  # outside repo — OK

    parts = rel.parts
    if not parts:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "workspace must not be repository root")
    if parts[0] in RESERVED_DIR_NAMES:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"workspace under reserved root {parts[0]}/ is forbidden",
        )
    return ws
