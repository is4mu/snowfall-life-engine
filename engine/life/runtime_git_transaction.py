"""Deterministic local Git transaction object for Life Engine runtime state.

Owns local Git object construction only: freeze HEAD as runtime base authority,
materialize the exact base tree into an isolated temp baseline, invoke 5C1
exactly once, and on CHANGE build one deterministic unreferenced candidate
commit whose single parent is the frozen base.

Never creates/updates branches or refs, never push/fetch/ls-remote, never
checkout/reset/merge/rebase, never mutates the real index or worktree.
Remote/genesis publication is application-owned and outside this module.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ErrorCode, LifeEngineError
from .runtime_bundle import RuntimeReferenceSets
from .runtime_orchestrator import (
    RuntimeFactProvider,
    RuntimeTargetAdvanceResult,
    RuntimeTargetInputs,
    RuntimeTargetRequest,
)
from .runtime_fact_provider import parse_runtime_target_request
from .runtime_persistence import (
    RuntimePersistentSnapshot,
    build_runtime_candidate_tree,
    build_runtime_candidate_tree_with_provider,
    build_runtime_candidate_tree_with_provider_factory,
    compute_runtime_semantic_tree_hash,
    is_allowed_runtime_relpath,
    list_runtime_files,
)
from .timeutil import parse_rfc3339, require_canonical_timestamp

AUTHOR_NAME = "Snowfall Life Engine"
AUTHOR_EMAIL = "life-engine@snowfall-life.invalid"
COMMITTER_NAME = AUTHOR_NAME
COMMITTER_EMAIL = AUTHOR_EMAIL

_HEX40_RE = re.compile(r"^[a-f0-9]{40}$")

# Repository-shaping / authority variables that must never be inherited from the
# caller process. 5C2 sets GIT_DIR (and optionally private GIT_INDEX_FILE) itself.
_SCRUBBED_GIT_VARS = frozenset(
    {
        "GIT_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_WORK_TREE",
        "GIT_NAMESPACE",
        "GIT_INDEX_FILE",
        "GIT_REPLACE_REF_BASE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_QUARANTINE_PATH",
    }
)

# Minimal OS execution keys preserved for finding the git binary / temp / locale.
_EXECUTION_ENV_KEYS = (
    "PATH",
    "HOME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
)


def _fail(detail: str, *, code: ErrorCode = ErrorCode.INVALID_STATE) -> None:
    raise LifeEngineError(code, detail)


def _cas_fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.RUNTIME_GIT_CAS_MISMATCH, detail)


def _require_hex40(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _HEX40_RE.fullmatch(value):
        _fail(f"{field} must be a lower-case 40-hex SHA-1")
    return value


@dataclass(frozen=True)
class RuntimeGitTransactionResult:
    status: str  # NOOP | CHANGE
    base_commit_sha: str
    candidate_tree_sha: str | None
    candidate_commit_sha: str | None
    run_id: str | None
    target_time: str
    processed_through: str
    state_before_hash: str
    state_after_hash: str
    files_changed: tuple[str, ...]
    last_run: Mapping[str, Any] | None
    runtime_result: RuntimeTargetAdvanceResult


@dataclass(frozen=True)
class RuntimeProviderTransactionPlan:
    """Deferred provider transaction inputs created from one frozen life base."""

    reference_sets: RuntimeReferenceSets
    behavior_policy: Mapping[str, Any]
    target_request: RuntimeTargetRequest | Mapping[str, Any]
    executing_engine_commit_sha: str
    fact_provider_factory: Callable[[RuntimePersistentSnapshot], RuntimeFactProvider]

@dataclass(frozen=True)
class _GitAuthoritySnapshot:
    head_sha: str
    symbolic_head: str | None
    refs: Mapping[str, str]
    index_digest: str | None
    index_bytes: bytes | None
    is_bare: bool
    git_dir: Path
    common_dir: Path
    work_tree: Path | None
    worktree_digest: str | None
    object_authority_fingerprint: str


def _execution_env_base() -> dict[str, str]:
    """OS necessities only — never copies arbitrary caller ``GIT_*`` authority."""
    env: dict[str, str] = {}
    for key in _EXECUTION_ENV_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


def _controlled_git_config_env() -> dict[str, str]:
    """Force signing off and UTF-8 commit encoding above local/global/system."""
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "commit.gpgsign",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "tag.gpgsign",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "i18n.commitEncoding",
        "GIT_CONFIG_VALUE_2": "UTF-8",
    }


def _probe_git_env() -> dict[str, str]:
    """Clean env for path probes via ``git -C`` (no inherited GIT_DIR/etc.)."""
    env = _execution_env_base()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    env.update(_controlled_git_config_env())
    for var in _SCRUBBED_GIT_VARS:
        env.pop(var, None)
    return env


def _base_git_env(*, git_dir: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Canonical 5C2 Git subprocess environment.

    Does not inherit repository-shaping ``GIT_*`` from the caller. Explicitly
    binds ``GIT_DIR``, disables replace objects and lazy fetch, and isolates
    global/system config while forcing UTF-8 commit encoding and unsigned commits.
    """
    env = _execution_env_base()
    env.update(
        {
            "GIT_DIR": str(git_dir),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    env.update(_controlled_git_config_env())
    # Drop any residual scrubbed vars, then apply only intentional extras
    # (private GIT_INDEX_FILE / author identity during commit-tree).
    for var in _SCRUBBED_GIT_VARS:
        if var != "GIT_DIR":
            env.pop(var, None)
    if extra:
        for key, value in extra.items():
            env[str(key)] = str(value)
    return env


def _run_git(
    args: Sequence[str],
    *,
    git_dir: Path,
    cwd: Path | None = None,
    env_extra: Mapping[str, str] | None = None,
    input_bytes: bytes | None = None,
    allow_object_write: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    """Run a Git plumbing command via argv array only (no shell, no network)."""
    if not args:
        _fail("empty git argv")
    cmd = args[0]
    # Hard reject network / ref-mutation / worktree mutation verbs.
    if cmd in {
        "push",
        "fetch",
        "ls-remote",
        "pull",
        "clone",
        "update-ref",
        "branch",
        "checkout",
        "switch",
        "reset",
        "merge",
        "rebase",
        "commit",
        "add",
        "remote",
    }:
        _fail(f"forbidden git command in 5C2: {cmd}")
    if cmd == "hash-object" and "-w" in args and not allow_object_write:
        _fail("hash-object -w forbidden before CAS / on NOOP")
    if cmd in {"write-tree", "commit-tree", "update-index", "mktree"} and not allow_object_write:
        _fail(f"{cmd} forbidden before CAS / on NOOP")

    env = _base_git_env(git_dir=git_dir, extra=env_extra)
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        _fail(f"git executable failure: {exc}")
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        _fail(f"git {' '.join(args)} failed: {detail or completed.returncode}")
    return completed


def _resolve_git_dir(runtime_repo: Path) -> tuple[Path, Path, bool, Path | None]:
    """Resolve git-dir + common-dir under a clean env (no inherited GIT_*)."""
    if not runtime_repo.exists():
        _fail(f"runtime_repo does not exist: {runtime_repo}")
    if not runtime_repo.is_dir():
        _fail(f"runtime_repo is not a directory: {runtime_repo}")

    probe_env = _probe_git_env()
    try:
        probe = subprocess.run(
            ["git", "-C", str(runtime_repo), "rev-parse", "--is-inside-work-tree"],
            env=probe_env,
            capture_output=True,
            check=False,
        )
        bare_probe = subprocess.run(
            ["git", "-C", str(runtime_repo), "rev-parse", "--is-bare-repository"],
            env=probe_env,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        _fail(f"git executable failure: {exc}")

    inside = probe.returncode == 0 and probe.stdout.strip() == b"true"
    is_bare = bare_probe.returncode == 0 and bare_probe.stdout.strip() == b"true"
    if not inside and not is_bare:
        _fail(f"runtime_repo is not a Git repository: {runtime_repo}")

    abs_git = subprocess.run(
        ["git", "-C", str(runtime_repo), "rev-parse", "--absolute-git-dir"],
        env=probe_env,
        capture_output=True,
        check=False,
    )
    if abs_git.returncode != 0:
        _fail(f"cannot resolve git dir for {runtime_repo}")
    git_dir = Path(abs_git.stdout.decode("utf-8").strip()).resolve()

    common = subprocess.run(
        ["git", "-C", str(runtime_repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        env=probe_env,
        capture_output=True,
        check=False,
    )
    if common.returncode != 0:
        # Older git without --path-format: resolve relative to cwd/repo.
        common = subprocess.run(
            ["git", "-C", str(runtime_repo), "rev-parse", "--git-common-dir"],
            env=probe_env,
            capture_output=True,
            check=False,
        )
    if common.returncode != 0:
        _fail(f"cannot resolve git common dir for {runtime_repo}")
    common_raw = Path(common.stdout.decode("utf-8").strip())
    if not common_raw.is_absolute():
        common_dir = (runtime_repo / common_raw).resolve()
    else:
        common_dir = common_raw.resolve()

    # Re-bind under GIT_DIR-only clean env to prove authority is the path we
    # resolved, not caller GIT_COMMON_DIR / NAMESPACE contamination.
    rebound = _run_git(
        ["rev-parse", "--absolute-git-dir"],
        git_dir=git_dir,
    )
    rebound_dir = Path(rebound.stdout.decode("utf-8").strip()).resolve()
    if rebound_dir != git_dir:
        _fail(
            f"clean-env git-dir rebound mismatch: resolved={git_dir} bound={rebound_dir}"
        )

    work_tree: Path | None = None
    if not is_bare:
        wt = subprocess.run(
            ["git", "-C", str(runtime_repo), "rev-parse", "--show-toplevel"],
            env=probe_env,
            capture_output=True,
            check=False,
        )
        if wt.returncode == 0:
            work_tree = Path(wt.stdout.decode("utf-8").strip()).resolve()
    return git_dir, common_dir, is_bare, work_tree


def _read_object_format(git_dir: Path) -> str:
    completed = _run_git(
        ["rev-parse", "--show-object-format"],
        git_dir=git_dir,
        check=False,
    )
    if completed.returncode != 0:
        # Older git may lack the flag; fall back to SHA-1 assumption only when
        # extensions.objectformat is absent.
        cfg = _run_git(
            ["config", "--get", "extensions.objectformat"],
            git_dir=git_dir,
            check=False,
        )
        if cfg.returncode == 0:
            fmt = cfg.stdout.decode("utf-8").strip().lower()
            return fmt or "sha1"
        return "sha1"
    return completed.stdout.decode("utf-8").strip().lower()


def _read_head_commit(git_dir: Path) -> str:
    completed = _run_git(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        git_dir=git_dir,
        check=False,
    )
    if completed.returncode != 0:
        _fail("unborn or missing HEAD commit (5C2 requires an existing base; genesis is 5D)")
    sha = completed.stdout.decode("utf-8").strip().lower()
    return _require_hex40(sha, field="HEAD^{commit}")


def _read_symbolic_head(git_dir: Path) -> str | None:
    completed = _run_git(
        ["symbolic-ref", "-q", "HEAD"],
        git_dir=git_dir,
        check=False,
    )
    if completed.returncode != 0:
        return None
    target = completed.stdout.decode("utf-8").strip()
    return target or None


def _read_refs_map(git_dir: Path) -> dict[str, str]:
    completed = _run_git(
        ["for-each-ref", "--format=%(refname)%00%(objectname)"],
        git_dir=git_dir,
    )
    refs: dict[str, str] = {}
    raw = completed.stdout
    if not raw:
        return refs
    for line in raw.split(b"\n"):
        if not line:
            continue
        parts = line.split(b"\0")
        if len(parts) != 2:
            _fail("malformed for-each-ref output")
        name = parts[0].decode("utf-8")
        oid = parts[1].decode("utf-8").strip().lower()
        refs[name] = _require_hex40(oid, field=f"ref {name}")
    return refs


def _read_index_bytes(git_dir: Path, *, is_bare: bool) -> tuple[bytes | None, str | None]:
    if is_bare:
        return None, None
    index_path = git_dir / "index"
    if not index_path.is_file():
        return None, None
    data = index_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return data, digest


def _worktree_digest(work_tree: Path | None, git_dir: Path) -> str | None:
    if work_tree is None:
        return None
    entries: list[str] = []
    for path in sorted(work_tree.rglob("*")):
        if not path.is_file():
            continue
        try:
            path.relative_to(git_dir)
            continue  # skip .git contents
        except ValueError:
            pass
        rel = str(path.relative_to(work_tree)).replace(os.sep, "/")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{rel}:{digest}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def _reject_replace_refs(refs: Mapping[str, str]) -> None:
    for name in refs:
        if name == "refs/replace" or name.startswith("refs/replace/"):
            _fail(f"refs/replace is forbidden in runtime Git authority: {name}")


def _authority_fail(detail: str, *, cas: bool) -> None:
    if cas:
        _cas_fail(detail)
    _fail(detail)


def _read_optional_file_bytes(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError as exc:
        _fail(f"cannot read authority file {path}: {exc}")
    return None  # pragma: no cover


def _config_regexp_lines(git_dir: Path, pattern: str) -> tuple[str, ...]:
    completed = _run_git(
        ["config", "--get-regexp", "--null", pattern],
        git_dir=git_dir,
        check=False,
    )
    # 0 = matches, 1 = no matches; anything else is fail-closed.
    if completed.returncode not in (0, 1):
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        _fail(f"git config --get-regexp {pattern!r} failed: {detail or completed.returncode}")
    if completed.returncode == 1 or not completed.stdout:
        return ()
    lines: list[str] = []
    for chunk in completed.stdout.split(b"\0"):
        if not chunk:
            continue
        lines.append(chunk.decode("utf-8", errors="replace"))
    return tuple(sorted(lines))


def _promisor_pack_markers(common_dir: Path) -> tuple[str, ...]:
    pack_dir = common_dir / "objects" / "pack"
    if not pack_dir.is_dir():
        return ()
    names: list[str] = []
    try:
        for entry in pack_dir.iterdir():
            if entry.is_file() and entry.name.endswith(".promisor"):
                names.append(entry.name)
    except OSError as exc:
        _fail(f"cannot scan pack directory for promisor markers: {exc}")
    return tuple(sorted(names))


def _assert_full_history_authority(
    git_dir: Path,
    common_dir: Path,
    *,
    cas: bool = False,
) -> None:
    """Require affirmative full-history proof; reject shallow/grafts."""
    shallow = _run_git(
        ["rev-parse", "--is-shallow-repository"],
        git_dir=git_dir,
        check=False,
    )
    if shallow.returncode != 0 or shallow.stdout.strip() != b"false":
        detail = shallow.stderr.decode("utf-8", errors="replace").strip()
        _authority_fail(
            "shallow probe must succeed with exactly 'false' "
            f"(returncode={shallow.returncode}, stdout={shallow.stdout!r}, "
            f"stderr={detail!r})",
            cas=cas,
        )
    shallow_marker = common_dir / "shallow"
    marker_bytes = _read_optional_file_bytes(shallow_marker)
    if marker_bytes is not None and marker_bytes.strip():
        _authority_fail(
            "non-empty common-dir shallow marker rejected "
            "(full linear history proof required)",
            cas=cas,
        )
    grafts = common_dir / "info" / "grafts"
    grafts_bytes = _read_optional_file_bytes(grafts)
    if grafts_bytes is not None and grafts_bytes.strip():
        _authority_fail("non-empty legacy grafts file rejected", cas=cas)


def _assert_self_contained_object_authority(
    git_dir: Path,
    common_dir: Path,
    *,
    cas: bool = False,
) -> None:
    """Reject partial/promisor/alternate object stores (local-only authority)."""
    for pattern, label in (
        (r"^remote\..+\.promisor$", "remote.*.promisor"),
        (r"^remote\..+\.partialclonefilter$", "remote.*.partialclonefilter"),
        (r"^extensions\.partialclone$", "extensions.partialclone"),
    ):
        matches = _config_regexp_lines(git_dir, pattern)
        if matches:
            _authority_fail(
                f"partial/promisor repository authority rejected ({label}): "
                + "; ".join(matches),
                cas=cas,
            )
    markers = _promisor_pack_markers(common_dir)
    if markers:
        _authority_fail(
            "promisor pack marker rejected: " + ", ".join(markers),
            cas=cas,
        )
    objects_info = common_dir / "objects" / "info"
    for name in ("alternates", "http-alternates"):
        path = objects_info / name
        data = _read_optional_file_bytes(path)
        if data is not None and data.strip():
            _authority_fail(
                f"non-empty objects/info/{name} rejected "
                "(external object store not covered by HEAD/ref CAS)",
                cas=cas,
            )


def _object_authority_fingerprint(git_dir: Path, common_dir: Path) -> str:
    """Exact fingerprint of history/object-authority controls for three-point CAS."""
    parts: list[str] = []
    shallow = _run_git(
        ["rev-parse", "--is-shallow-repository"],
        git_dir=git_dir,
        check=False,
    )
    parts.append(
        "shallow_probe:"
        f"{shallow.returncode}:{shallow.stdout.strip()!r}:{shallow.stderr.strip()!r}"
    )
    for rel in (
        "shallow",
        "info/grafts",
        "objects/info/alternates",
        "objects/info/http-alternates",
    ):
        data = _read_optional_file_bytes(common_dir / rel)
        if data is None:
            parts.append(f"{rel}:absent")
        else:
            parts.append(f"{rel}:{hashlib.sha256(data).hexdigest()}:{len(data)}")
    for pattern in (
        r"^remote\..+\.promisor$",
        r"^remote\..+\.partialclonefilter$",
        r"^extensions\.partialclone$",
    ):
        matches = _config_regexp_lines(git_dir, pattern)
        parts.append(f"config:{pattern}:{matches!r}")
    parts.append(f"promisor_packs:{_promisor_pack_markers(common_dir)!r}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _assert_base_objects_locally_complete(git_dir: Path, base_sha: str) -> None:
    """Read-only proof that frozen-base reachable objects are locally present."""
    completed = _run_git(
        ["rev-list", "--objects", "--missing=print", base_sha],
        git_dir=git_dir,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        _fail(
            "frozen base local-completeness proof failed: "
            f"{detail or completed.returncode}"
        )
    missing: list[str] = []
    for line in completed.stdout.splitlines():
        if line.startswith(b"?"):
            missing.append(line.decode("utf-8", errors="replace"))
    if missing:
        preview = ", ".join(missing[:8])
        more = "" if len(missing) <= 8 else f" (+{len(missing) - 8} more)"
        _fail(
            "frozen base has missing local objects under GIT_NO_LAZY_FETCH: "
            f"{preview}{more}"
        )


def _freeze_authority(runtime_repo: Path) -> _GitAuthoritySnapshot:
    git_dir, common_dir, is_bare, work_tree = _resolve_git_dir(runtime_repo)
    fmt = _read_object_format(git_dir)
    if fmt != "sha1":
        _fail(f"runtime Git object format must be sha1, got {fmt!r}")
    _assert_full_history_authority(git_dir, common_dir, cas=False)
    _assert_self_contained_object_authority(git_dir, common_dir, cas=False)
    head = _read_head_commit(git_dir)
    symbolic = _read_symbolic_head(git_dir)
    refs = _read_refs_map(git_dir)
    _reject_replace_refs(refs)
    index_bytes, index_digest = _read_index_bytes(git_dir, is_bare=is_bare)
    wt_digest = _worktree_digest(work_tree, git_dir)
    object_fp = _object_authority_fingerprint(git_dir, common_dir)
    return _GitAuthoritySnapshot(
        head_sha=head,
        symbolic_head=symbolic,
        refs=refs,
        index_digest=index_digest,
        index_bytes=index_bytes,
        is_bare=is_bare,
        git_dir=git_dir,
        common_dir=common_dir,
        work_tree=work_tree,
        worktree_digest=wt_digest,
        object_authority_fingerprint=object_fp,
    )


def _assert_authority_unchanged(
    expected: _GitAuthoritySnapshot,
    *,
    stage: str,
) -> None:
    head = _read_head_commit(expected.git_dir)
    if head != expected.head_sha:
        _cas_fail(f"{stage}: HEAD moved ({expected.head_sha} -> {head})")
    symbolic = _read_symbolic_head(expected.git_dir)
    if symbolic != expected.symbolic_head:
        _cas_fail(
            f"{stage}: symbolic HEAD changed "
            f"({expected.symbolic_head!r} -> {symbolic!r})"
        )
    refs = _read_refs_map(expected.git_dir)
    if refs != dict(expected.refs):
        _cas_fail(f"{stage}: ref map changed")
    index_bytes, index_digest = _read_index_bytes(
        expected.git_dir, is_bare=expected.is_bare
    )
    if index_digest != expected.index_digest or index_bytes != expected.index_bytes:
        _fail(f"{stage}: real Git index mutated")
    wt_digest = _worktree_digest(expected.work_tree, expected.git_dir)
    if wt_digest != expected.worktree_digest:
        _fail(f"{stage}: worktree files mutated")
    # History / object-store authority is part of the three-point CAS.
    _assert_full_history_authority(
        expected.git_dir, expected.common_dir, cas=True
    )
    _assert_self_contained_object_authority(
        expected.git_dir, expected.common_dir, cas=True
    )
    object_fp = _object_authority_fingerprint(
        expected.git_dir, expected.common_dir
    )
    if object_fp != expected.object_authority_fingerprint:
        _cas_fail(f"{stage}: object/history authority changed")


def _ls_tree_blobs(git_dir: Path, commit_sha: str) -> dict[str, tuple[str, str]]:
    """Return path -> (mode, blob_sha) for the commit tree (recursive)."""
    completed = _run_git(
        ["ls-tree", "-r", "--full-tree", "-z", commit_sha],
        git_dir=git_dir,
    )
    out: dict[str, tuple[str, str]] = {}
    raw = completed.stdout
    if not raw:
        return out
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        # format: <mode> SP <type> SP <object> TAB <file>
        try:
            meta, path_b = entry.split(b"\t", 1)
        except ValueError:
            _fail("malformed ls-tree entry")
        parts = meta.split(b" ")
        if len(parts) != 3:
            _fail("malformed ls-tree metadata")
        mode = parts[0].decode("ascii")
        obj_type = parts[1].decode("ascii")
        blob_sha = parts[2].decode("ascii").lower()
        try:
            path = path_b.decode("utf-8")
        except UnicodeDecodeError:
            _fail("base tree path is not UTF-8")
        if "\0" in path or "\n" in path or "\t" in path:
            _fail(f"malformed base tree path: {path!r}")
        if obj_type != "blob":
            _fail(f"base tree entry must be blob, got {obj_type} at {path}")
        if mode != "100644":
            _fail(f"base tree mode must be 100644, got {mode} at {path}")
        _require_hex40(blob_sha, field=f"blob at {path}")
        if not is_allowed_runtime_relpath(path):
            _fail(f"non-runtime path in base tree: {path}")
        if path in out:
            _fail(f"duplicate base tree path: {path}")
        out[path] = (mode, blob_sha)
    if not out:
        _fail("base tree contains no runtime files")
    return out


def _cat_blob(git_dir: Path, blob_sha: str) -> bytes:
    completed = _run_git(["cat-file", "blob", blob_sha], git_dir=git_dir)
    return completed.stdout


def _materialize_base_tree(
    git_dir: Path,
    commit_sha: str,
    baseline_root: Path,
) -> dict[str, bytes]:
    entries = _ls_tree_blobs(git_dir, commit_sha)
    files: dict[str, bytes] = {}
    for rel, (_mode, blob_sha) in sorted(entries.items()):
        data = _cat_blob(git_dir, blob_sha)
        dest = baseline_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        files[rel] = data
    return files


def _assert_linear_ancestry(git_dir: Path, base_sha: str) -> None:
    completed = _run_git(
        ["rev-list", "--min-parents=2", base_sha],
        git_dir=git_dir,
    )
    if completed.stdout.strip():
        _fail("reachable base history contains a merge commit (linear history required)")


def _git_date_from_target_time(target_time: str) -> str:
    ts = require_canonical_timestamp(target_time, field="target_time")
    dt = parse_rfc3339(ts, field="target_time")
    unix = int(dt.timestamp())
    offset = dt.strftime("%z")
    if len(offset) != 5:
        _fail(f"unexpected timezone offset for target_time: {offset!r}")
    return f"{unix} {offset}"


def _build_commit_message(last_run: Mapping[str, Any]) -> str:
    run_id = last_run["run_id"]
    prev = last_run["previous_processed_through"]
    through = last_run["processed_through"]
    rev_before = last_run["state_revision_before"]
    rev_after = last_run["state_revision_after"]
    msg = (
        f"Life Engine runtime transaction {run_id}\n"
        f"\n"
        f"processed_through: {prev} -> {through}\n"
        f"state_revision: {rev_before} -> {rev_after}\n"
    )
    if not msg.endswith("\n") or msg.endswith("\n\n"):
        _fail("commit message trailing newline contract violated")
    # Exactly one trailing newline: final char is \n and not \n\n at end.
    if msg[-1] != "\n" or msg[-2] == "\n":
        _fail("commit message must end with exactly one trailing newline")
    return msg


def _precompute_blob_ids(
    git_dir: Path,
    candidate_files: Mapping[str, bytes],
) -> dict[str, str]:
    blobs: dict[str, str] = {}
    for rel, data in sorted(candidate_files.items()):
        completed = _run_git(
            ["hash-object", "--stdin"],
            git_dir=git_dir,
            input_bytes=data,
            allow_object_write=False,
        )
        blob_sha = completed.stdout.decode("utf-8").strip().lower()
        blobs[rel] = _require_hex40(blob_sha, field=f"precomputed blob {rel}")
    return blobs


def _write_candidate_tree(
    git_dir: Path,
    *,
    index_file: Path,
    candidate_files: Mapping[str, bytes],
    expected_blobs: Mapping[str, str],
) -> str:
    # Ensure private index starts empty.
    if index_file.exists():
        index_file.unlink()
    index_file.parent.mkdir(parents=True, exist_ok=True)

    env_extra = {"GIT_INDEX_FILE": str(index_file)}
    # Write blobs then feed update-index --index-info.
    index_info_lines: list[bytes] = []
    for rel in sorted(candidate_files):
        data = candidate_files[rel]
        written = _run_git(
            ["hash-object", "-w", "--stdin"],
            git_dir=git_dir,
            input_bytes=data,
            env_extra=env_extra,
            allow_object_write=True,
        )
        blob_sha = written.stdout.decode("utf-8").strip().lower()
        blob_sha = _require_hex40(blob_sha, field=f"written blob {rel}")
        if blob_sha != expected_blobs[rel]:
            _fail(
                f"written blob SHA mismatch for {rel}: "
                f"expected {expected_blobs[rel]} got {blob_sha}"
            )
        index_info_lines.append(f"100644 {blob_sha}\t{rel}\n".encode("utf-8"))

    _run_git(
        ["update-index", "--index-info"],
        git_dir=git_dir,
        input_bytes=b"".join(index_info_lines),
        env_extra=env_extra,
        allow_object_write=True,
    )
    tree = _run_git(
        ["write-tree"],
        git_dir=git_dir,
        env_extra=env_extra,
        allow_object_write=True,
    )
    tree_sha = tree.stdout.decode("utf-8").strip().lower()
    tree_sha = _require_hex40(tree_sha, field="candidate tree")

    # Verify tree content exact.
    listed = _run_git(
        ["ls-tree", "-r", "--full-tree", "-z", tree_sha],
        git_dir=git_dir,
    )
    seen: dict[str, str] = {}
    for entry in listed.stdout.split(b"\0"):
        if not entry:
            continue
        meta, path_b = entry.split(b"\t", 1)
        mode_b, typ_b, sha_b = meta.split(b" ")
        mode = mode_b.decode("ascii")
        typ = typ_b.decode("ascii")
        sha = sha_b.decode("ascii").lower()
        path = path_b.decode("utf-8")
        if mode != "100644" or typ != "blob":
            _fail(f"candidate tree entry invalid: {mode} {typ} {path}")
        seen[path] = sha
    if set(seen) != set(candidate_files):
        _fail(
            "candidate tree file set mismatch: "
            f"tree={sorted(seen)} candidate={sorted(candidate_files)}"
        )
    for rel, sha in seen.items():
        if sha != expected_blobs[rel]:
            _fail(f"candidate tree blob mismatch at {rel}")
    return tree_sha


def _commit_tree(
    git_dir: Path,
    *,
    tree_sha: str,
    parent_sha: str,
    message: str,
    git_date: str,
) -> str:
    env_extra = {
        "GIT_AUTHOR_NAME": AUTHOR_NAME,
        "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": git_date,
        "GIT_COMMITTER_NAME": COMMITTER_NAME,
        "GIT_COMMITTER_EMAIL": COMMITTER_EMAIL,
        "GIT_COMMITTER_DATE": git_date,
    }
    completed = _run_git(
        ["commit-tree", tree_sha, "-p", parent_sha, "-F", "-"],
        git_dir=git_dir,
        input_bytes=message.encode("utf-8"),
        env_extra=env_extra,
        allow_object_write=True,
    )
    commit_sha = completed.stdout.decode("utf-8").strip().lower()
    return _require_hex40(commit_sha, field="candidate commit")


def _verify_commit_object(
    git_dir: Path,
    *,
    commit_sha: str,
    tree_sha: str,
    parent_sha: str,
    message: str,
    git_date: str,
    files_changed: Sequence[str],
) -> None:
    typ = _run_git(["cat-file", "-t", commit_sha], git_dir=git_dir)
    if typ.stdout.decode("utf-8").strip() != "commit":
        _fail("candidate object is not a commit")
    raw = _run_git(["cat-file", "-p", commit_sha], git_dir=git_dir).stdout.decode("utf-8")
    if not raw.endswith("\n"):
        _fail("commit object raw must end with newline")
    if "\n\n" not in raw:
        _fail("commit missing message separator")
    header, msg_stored = raw.split("\n\n", 1)
    header_lines = header.split("\n")
    # Exact approved header set: tree, one parent, author, committer.
    if len(header_lines) != 4:
        _fail(
            "commit header set must be exactly tree/parent/author/committer; "
            f"got {header_lines!r}"
        )
    tree_line, parent_line, author_line, committer_line = header_lines
    if not tree_line.startswith("tree "):
        _fail(f"commit missing tree header: {tree_line!r}")
    if tree_line.split(" ", 1)[1] != tree_sha:
        _fail("commit tree mismatch")
    if not parent_line.startswith("parent "):
        _fail(f"commit missing parent header: {parent_line!r}")
    if parent_line.split(" ", 1)[1] != parent_sha:
        _fail("candidate commit parent mismatch")
    expected_ident = f"{AUTHOR_NAME} <{AUTHOR_EMAIL}> {git_date}"
    if author_line != f"author {expected_ident}":
        _fail(f"author identity mismatch: {author_line!r}")
    if committer_line != f"committer {expected_ident}":
        _fail(f"committer identity mismatch: {committer_line!r}")
    # Length/prefix checks above already reject encoding/gpgsig/mergetag/extras.
    for line in header_lines:
        kind = line.split(" ", 1)[0]
        if kind not in {"tree", "parent", "author", "committer"}:
            _fail(f"forbidden commit header present: {line!r}")
    if "BEGIN PGP SIGNATURE" in raw:
        _fail("commit must not be GPG-signed")
    if msg_stored != message:
        _fail(
            "commit message mismatch: "
            f"expected={message!r} stored={msg_stored!r}"
        )

    diff = _run_git(
        [
            "diff-tree",
            "--no-renames",
            "--no-commit-id",
            "--name-only",
            "-r",
            parent_sha,
            commit_sha,
        ],
        git_dir=git_dir,
    )
    changed = [ln for ln in diff.stdout.decode("utf-8").splitlines() if ln]
    if changed != list(files_changed):
        _fail(
            "diff-tree paths mismatch: "
            f"got={changed} expected={list(files_changed)}"
        )


def _assert_object_in_intended_repo(git_dir: Path, object_sha: str) -> None:
    """Prove ``object_sha`` resolves from the intended repo under a clean env."""
    _run_git(["cat-file", "-e", object_sha], git_dir=git_dir)
    typ = _run_git(["cat-file", "-t", object_sha], git_dir=git_dir)
    if typ.stdout.decode("utf-8").strip() != "commit":
        _fail("candidate object is not a commit in intended repository")


def _commit_is_unreferenced(git_dir: Path, commit_sha: str) -> None:
    # Exists in intended object DB under the clean 5C2 environment.
    _assert_object_in_intended_repo(git_dir, commit_sha)
    head = _read_head_commit(git_dir)
    if head == commit_sha:
        _fail("candidate commit is HEAD")
    # Reachability via any ref (annotated tags / peeled commits included).
    containing = _run_git(
        ["for-each-ref", f"--contains={commit_sha}", "--format=%(refname)"],
        git_dir=git_dir,
    )
    names = [ln for ln in containing.stdout.decode("utf-8").splitlines() if ln]
    if names:
        _fail(
            "candidate commit is reachable from refs: " + ", ".join(sorted(names))
        )


def _byte_diff_paths(
    base_files: Mapping[str, bytes],
    candidate_files: Mapping[str, bytes],
) -> list[str]:
    keys = sorted(set(base_files) | set(candidate_files))
    changed: list[str] = []
    for key in keys:
        if base_files.get(key) != candidate_files.get(key):
            changed.append(key)
    return changed


def _strip_persistence_temp_paths(
    runtime_result: RuntimeTargetAdvanceResult,
) -> RuntimeTargetAdvanceResult:
    """Return the pure C3 result (already free of filesystem temp roots)."""
    return runtime_result


def _cleanup_owned_temp_root(temp_root: Path | None) -> None:
    """Fail closed if owned baseline/candidate/private-index temp remains."""
    if temp_root is None:
        return
    if not temp_root.exists():
        return
    try:
        shutil.rmtree(temp_root)
    except OSError as exc:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"owned temp root cleanup failed: {exc}",
        ) from exc
    if temp_root.exists():
        _fail(f"owned temp root still exists after cleanup: {temp_root}")


def build_runtime_git_transaction(
    *,
    runtime_repo: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    target_inputs: RuntimeTargetInputs | Mapping[str, Any],
    executing_engine_commit_sha: str,
) -> RuntimeGitTransactionResult:
    """Build a local transaction using the existing precomputed C3 input tape."""

    def _build_5c1(
        baseline_root: Path,
        candidate_root: Path,
        life_base_sha: str,
    ):
        return build_runtime_candidate_tree(
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            reference_sets=reference_sets,
            behavior_policy=behavior_policy,
            target_inputs=target_inputs,
            executing_engine_commit_sha=executing_engine_commit_sha,
            life_base_sha=life_base_sha,
        )

    def _requested_target_time() -> str:
        if isinstance(target_inputs, RuntimeTargetInputs):
            return target_inputs.target_time
        return require_canonical_timestamp(
            dict(target_inputs)["target_time"], field="target_time"
        )

    return _build_runtime_git_transaction_core(
        runtime_repo=runtime_repo,
        persistence_builder=_build_5c1,
        requested_target_time=_requested_target_time,
    )


def build_runtime_git_transaction_with_provider(
    *,
    runtime_repo: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    target_request: RuntimeTargetRequest | Mapping[str, Any],
    fact_provider: RuntimeFactProvider,
    executing_engine_commit_sha: str,
) -> RuntimeGitTransactionResult:
    """Build a local transaction using provider-driven C3 inputs."""

    def _build_5c1(
        baseline_root: Path,
        candidate_root: Path,
        life_base_sha: str,
    ):
        return build_runtime_candidate_tree_with_provider(
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            reference_sets=reference_sets,
            behavior_policy=behavior_policy,
            target_request=target_request,
            fact_provider=fact_provider,
            executing_engine_commit_sha=executing_engine_commit_sha,
            life_base_sha=life_base_sha,
        )

    def _requested_target_time() -> str:
        return parse_runtime_target_request(target_request).target_time

    return _build_runtime_git_transaction_core(
        runtime_repo=runtime_repo,
        persistence_builder=_build_5c1,
        requested_target_time=_requested_target_time,
    )


def build_runtime_git_transaction_with_provider_factory(
    *,
    runtime_repo: Path | str,
    plan_factory: Callable[[Path, str], RuntimeProviderTransactionPlan],
) -> RuntimeGitTransactionResult:
    """Build a provider transaction from a plan created inside the frozen base.

    plan_factory runs exactly once after 5C2 has frozen/materialized the
    authoritative life HEAD. The returned fact-provider factory runs exactly
    once later, after 5C1 has fully loaded/verified that same baseline snapshot.
    """

    if not callable(plan_factory):
        _fail("plan_factory must be callable")

    plan_calls = 0
    target_time_holder: list[str] = []

    def _build_5c1(
        baseline_root: Path,
        candidate_root: Path,
        life_base_sha: str,
    ):
        nonlocal plan_calls
        if plan_calls != 0:
            _fail("provider transaction plan_factory must run exactly once")
        plan = plan_factory(baseline_root, life_base_sha)
        plan_calls += 1
        if not isinstance(plan, RuntimeProviderTransactionPlan):
            _fail("plan_factory must return RuntimeProviderTransactionPlan")
        request = parse_runtime_target_request(plan.target_request)
        target_time_holder.append(request.target_time)
        return build_runtime_candidate_tree_with_provider_factory(
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            reference_sets=plan.reference_sets,
            behavior_policy=plan.behavior_policy,
            target_request=request,
            fact_provider_factory=plan.fact_provider_factory,
            executing_engine_commit_sha=plan.executing_engine_commit_sha,
            life_base_sha=life_base_sha,
        )

    def _requested_target_time() -> str:
        if len(target_time_holder) != 1:
            _fail("provider transaction target_time unavailable")
        return target_time_holder[0]

    return _build_runtime_git_transaction_core(
        runtime_repo=runtime_repo,
        persistence_builder=_build_5c1,
        requested_target_time=_requested_target_time,
    )

def _build_runtime_git_transaction_core(
    *,
    runtime_repo: Path | str,
    persistence_builder: Callable[[Path, Path, str], Any],
    requested_target_time: Callable[[], str],
) -> RuntimeGitTransactionResult:
    """Shared 5C2 Git authority/object semantics for both C3 input modes."""
    repo_path = Path(runtime_repo)
    temp_root: Path | None = None
    result: RuntimeGitTransactionResult | None = None
    try:
        authority = _freeze_authority(repo_path)
        # Validate runtime-only tree before ancestry so a project code tree is
        # rejected as non-runtime even when history contains merges.
        _ls_tree_blobs(authority.git_dir, authority.head_sha)
        _assert_linear_ancestry(authority.git_dir, authority.head_sha)
        # Local-only completeness under GIT_NO_LAZY_FETCH before any materialize/5C1.
        _assert_base_objects_locally_complete(authority.git_dir, authority.head_sha)

        temp_root = Path(
            tempfile.mkdtemp(prefix="life-engine-git-transaction-", dir=None)
        ).resolve()
        # Keep temps outside the runtime repo.
        try:
            temp_root.relative_to(authority.git_dir)
            _fail("temp workspace resolved inside git dir")
        except ValueError:
            pass
        if authority.work_tree is not None:
            try:
                temp_root.relative_to(authority.work_tree)
                _fail("temp workspace resolved inside worktree")
            except ValueError:
                pass

        baseline_root = temp_root / "baseline"
        candidate_root = temp_root / "candidate"
        index_file = temp_root / "private-index"
        baseline_root.mkdir(parents=True, exist_ok=True)
        candidate_root.mkdir(parents=True, exist_ok=True)

        base_files = _materialize_base_tree(
            authority.git_dir, authority.head_sha, baseline_root
        )

        # CAS before expensive 5C1 work (still after freeze / materialize start).
        _assert_authority_unchanged(authority, stage="pre-5C1")

        persistence = persistence_builder(
            baseline_root,
            candidate_root,
            authority.head_sha,
        )
        # Freeze candidate bytes immediately so later tampering cannot become
        # Git commit authority (even whitespace-only / JSON-insignificant edits).
        candidate_bytes_at_5c1: dict[str, bytes] | None = None
        if persistence.status == "CHANGE":
            candidate_bytes_at_5c1 = list_runtime_files(candidate_root)

        # Bind 5C1 baseline to frozen Git base.
        if persistence.life_base_sha != authority.head_sha:
            _fail("5C1 life_base_sha != frozen HEAD")
        baseline_disk = list_runtime_files(baseline_root)
        if set(baseline_disk) != set(base_files):
            _fail("5C1 baseline file set != frozen Git base tree")
        for rel, data in base_files.items():
            if baseline_disk.get(rel) != data:
                _fail(f"baseline byte mismatch vs frozen Git blob: {rel}")
        if persistence.state_before_hash != persistence.baseline_snapshot.semantic_tree_hash:
            _fail("state_before_hash binding mismatch")
        # Recompute via snapshot object already verified by 5C1; also require
        # equality to the hash 5C1 returned.
        if persistence.baseline_snapshot.semantic_tree_hash != persistence.state_before_hash:
            _fail("baseline semantic hash mismatch")

        runtime_result = _strip_persistence_temp_paths(persistence.runtime_result)
        target_time = require_canonical_timestamp(
            requested_target_time(), field="target_time"
        )

        processed_through = str(
            persistence.runtime_result.bundle.current_state["processed_through"]
        )

        if persistence.status == "NOOP":
            if persistence.files_changed != ():
                _fail("NOOP must have empty files_changed")
            if persistence.state_before_hash != persistence.state_after_hash:
                _fail("NOOP state hashes must match")
            if persistence.last_run is not None:
                _fail("NOOP must not produce last-run")
            if persistence.candidate_snapshot is not None:
                _fail("NOOP must not produce candidate snapshot")
            _assert_authority_unchanged(authority, stage="NOOP exit")
            result = RuntimeGitTransactionResult(
                status="NOOP",
                base_commit_sha=authority.head_sha,
                candidate_tree_sha=None,
                candidate_commit_sha=None,
                run_id=None,
                target_time=target_time,
                processed_through=processed_through,
                state_before_hash=persistence.state_before_hash,
                state_after_hash=persistence.state_after_hash,
                files_changed=(),
                last_run=None,
                runtime_result=runtime_result,
            )
        elif persistence.status != "CHANGE":
            _fail(f"unexpected 5C1 status: {persistence.status}")
        else:
            # CHANGE preflight.
            if persistence.candidate_snapshot is None:
                _fail("CHANGE requires candidate snapshot")
            if persistence.last_run is None:
                _fail("CHANGE requires last-run")
            last_run = dict(persistence.last_run)
            if last_run["life_base_sha"] != authority.head_sha:
                _fail("last_run.life_base_sha != frozen base")
            if last_run["state_before_hash"] != persistence.state_before_hash:
                _fail("last_run.state_before_hash mismatch")
            if last_run["state_after_hash"] != persistence.state_after_hash:
                _fail("last_run.state_after_hash mismatch")
            if (
                persistence.candidate_snapshot.semantic_tree_hash
                != persistence.state_after_hash
            ):
                _fail("candidate semantic hash != state_after_hash")
            target_time = require_canonical_timestamp(
                last_run["target_time"], field="last_run.target_time"
            )

            candidate_files = list_runtime_files(candidate_root)
            if (
                candidate_bytes_at_5c1 is not None
                and candidate_files != candidate_bytes_at_5c1
            ):
                _fail("candidate tampered after 5C1")
            for rel in candidate_files:
                if not is_allowed_runtime_relpath(rel):
                    _fail(f"forbidden path in candidate tree: {rel}")

            diff_paths = _byte_diff_paths(base_files, candidate_files)
            expected_changed = list(persistence.files_changed)
            last_run_changed = list(last_run["files_changed"])
            if diff_paths != expected_changed:
                _fail(
                    "candidate byte diff != 5C1 files_changed: "
                    f"{diff_paths} vs {expected_changed}"
                )
            if diff_paths != last_run_changed:
                _fail(
                    "candidate byte diff != last-run.files_changed: "
                    f"{diff_paths} vs {last_run_changed}"
                )

            # Semantic hash over parsed JSON must still match (defense in depth).
            disk_semantic = compute_runtime_semantic_tree_hash(candidate_root)
            if disk_semantic != persistence.state_after_hash:
                _fail(
                    "candidate disk semantic hash mismatch after 5C1 (tamper reject)"
                )

            # Precompute blob IDs read-only before CAS / object writes.
            expected_blobs = _precompute_blob_ids(authority.git_dir, candidate_files)

            # Recheck CAS immediately before first object-writing command.
            _assert_authority_unchanged(authority, stage="pre-object-write")

            tree_sha = _write_candidate_tree(
                authority.git_dir,
                index_file=index_file,
                candidate_files=candidate_files,
                expected_blobs=expected_blobs,
            )
            message = _build_commit_message(last_run)
            git_date = _git_date_from_target_time(target_time)
            commit_sha = _commit_tree(
                authority.git_dir,
                tree_sha=tree_sha,
                parent_sha=authority.head_sha,
                message=message,
                git_date=git_date,
            )

            try:
                _verify_commit_object(
                    authority.git_dir,
                    commit_sha=commit_sha,
                    tree_sha=tree_sha,
                    parent_sha=authority.head_sha,
                    message=message,
                    git_date=git_date,
                    files_changed=expected_changed,
                )
                _assert_authority_unchanged(authority, stage="post-build")
                _commit_is_unreferenced(authority.git_dir, commit_sha)
            except Exception:
                # Late failure: never return a publishable commit result.
                raise

            result = RuntimeGitTransactionResult(
                status="CHANGE",
                base_commit_sha=authority.head_sha,
                candidate_tree_sha=tree_sha,
                candidate_commit_sha=commit_sha,
                run_id=str(last_run["run_id"]),
                target_time=target_time,
                processed_through=str(last_run["processed_through"]),
                state_before_hash=persistence.state_before_hash,
                state_after_hash=persistence.state_after_hash,
                files_changed=tuple(expected_changed),
                last_run=last_run,
                runtime_result=runtime_result,
            )
    except BaseException as primary:
        try:
            _cleanup_owned_temp_root(temp_root)
        except Exception as cleanup_exc:
            raise cleanup_exc from primary
        raise

    # Success path: cleanup must complete before any publishable result returns.
    _cleanup_owned_temp_root(temp_root)
    if result is None:
        _fail("internal error: transaction produced no result")
    return result
