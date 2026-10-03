"""Shared isolated local-Git builders for public transaction tests."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping

from engine.life.runtime_git_transaction import build_runtime_git_transaction
from tests.support.builders.persistence import (
    AS_OF,
    CHAR,
    ENGINE_SHA,
    POLICY,
    POLICY_VERSION,
    _advance_refs,
    _target_inputs,
    make_c3_continue_root,
    make_c3_exact_end_root,
    make_c3_noop_root,
    make_c3_start_root,
    read_json,
)

__all__ = [
    "AS_OF",
    "CHAR",
    "ENGINE_SHA",
    "POLICY",
    "POLICY_VERSION",
    "_advance_refs",
    "_commit_into_repo",
    "_target_inputs",
    "build_runtime_git_repo",
    "commit_tree_via_plumbing",
    "count_git_objects",
    "git",
    "git_out",
    "make_c3_continue_root",
    "make_c3_exact_end_root",
    "make_c3_noop_root",
    "make_c3_start_root",
    "object_db_fingerprint",
    "read_json",
    "refs_map",
    "run_transaction",
    "snapshot_index",
    "snapshot_refs_head",
    "snapshot_worktree",
]


def git(repo: Path, *args: str, check: bool = True, env: Mapping[str, str] | None = None, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    full_env = os.environ.copy()
    if env:
        full_env.update(dict(env))
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=full_env,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed: "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed


def git_out(repo: Path, *args: str, **kwargs: Any) -> str:
    return git(repo, *args, **kwargs).stdout.decode("utf-8").strip()


def commit_tree_via_plumbing(
    repo: Path,
    files: Mapping[str, bytes],
    *,
    message: str = "base\n",
    parent: str | None = None,
    author_date: str = "1000000000 +0000",
) -> str:
    """Create a commit from path->bytes without trusting worktree staging semantics."""
    git_dir = Path(git_out(repo, "rev-parse", "--absolute-git-dir"))
    index = repo.parent / f".idx-{hashlib.sha1(str(repo).encode()).hexdigest()[:8]}"
    if index.exists():
        index.unlink()
    env = {
        "GIT_DIR": str(git_dir),
        "GIT_INDEX_FILE": str(index),
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@test.invalid",
        "GIT_AUTHOR_DATE": author_date,
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@test.invalid",
        "GIT_COMMITTER_DATE": author_date,
        "GIT_TERMINAL_PROMPT": "0",
    }
    lines: list[bytes] = []
    for rel, data in sorted(files.items()):
        blob = subprocess.check_output(
            ["git", "hash-object", "-w", "--stdin"],
            input=data,
            env={**os.environ, **env},
        ).decode().strip()
        lines.append(f"100644 {blob}\t{rel}\n".encode())
    subprocess.run(
        ["git", "update-index", "--index-info"],
        input=b"".join(lines),
        env={**os.environ, **env},
        check=True,
    )
    tree = subprocess.check_output(
        ["git", "write-tree"],
        env={**os.environ, **env},
    ).decode().strip()
    cmd = ["git", "commit-tree", tree]
    if parent:
        cmd.extend(["-p", parent])
    cmd.extend(["-F", "-"])
    commit = subprocess.check_output(
        cmd,
        input=message.encode(),
        env={**os.environ, **env},
    ).decode().strip()
    if index.exists():
        index.unlink()
    return commit


def build_runtime_git_repo(
    tmp: Path | str,
    runtime_root: Path,
    *,
    bare: bool = False,
    branch: str = "runtime",
    extra_files: Mapping[str, bytes] | None = None,
    modes: Mapping[str, str] | None = None,
) -> Path:
    """Initialize a local Git repo whose HEAD tree is exactly the runtime files.

    ``modes`` may force non-100644 entries for negative tests (applied via
    update-index after initial commit rebuild). Default path uses plumbing with
    mode 100644 only.
    """
    tmp_path = Path(tmp)
    tmp_path.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    for path in sorted(runtime_root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(runtime_root)).replace(os.sep, "/")
            files[rel] = path.read_bytes()
    if extra_files:
        files.update({k: v for k, v in extra_files.items()})

    if bare:
        repo = tmp_path / "runtime.git"
        subprocess.check_call(["git", "init", "--bare", "-b", branch, str(repo)])
        # Build objects into bare repo via plumbing; set HEAD via update-ref once
        # for fixture genesis only (tests of 5C2 itself never call update-ref).
        commit = _commit_into_repo(repo, files, branch=branch, modes=modes)
        return repo

    repo = tmp_path / "runtime-repo"
    subprocess.check_call(["git", "init", "-b", branch, str(repo)])
    git(repo, "config", "user.email", "fixture@test.invalid")
    git(repo, "config", "user.name", "fixture")
    # Materialize files into worktree for realism, but commit via plumbing so
    # modes stay exact 100644 (or overridden for negative tests).
    for rel, data in files.items():
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    commit = _commit_into_repo(repo, files, branch=branch, modes=modes)
    # Ensure worktree matches committed tree for non-bare fixtures.
    git(repo, "read-tree", commit)
    # Do not checkout — 5C2 must not depend on worktree; leave files as written.
    return repo


def _commit_into_repo(
    repo: Path,
    files: Mapping[str, bytes],
    *,
    branch: str,
    modes: Mapping[str, str] | None,
) -> str:
    git_dir = Path(
        subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"]
        )
        .decode()
        .strip()
    )
    index = Path(tempfile_index_path(repo))
    if index.exists():
        index.unlink()
    env = {
        **os.environ,
        "GIT_DIR": str(git_dir),
        "GIT_INDEX_FILE": str(index),
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@test.invalid",
        "GIT_AUTHOR_DATE": "1000000000 +0000",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@test.invalid",
        "GIT_COMMITTER_DATE": "1000000000 +0000",
        "GIT_TERMINAL_PROMPT": "0",
    }
    lines: list[bytes] = []
    for rel, data in sorted(files.items()):
        blob = subprocess.check_output(
            ["git", "hash-object", "-w", "--stdin"],
            input=data,
            env=env,
        ).decode().strip()
        mode = "100644"
        if modes and rel in modes:
            mode = modes[rel]
        lines.append(f"{mode} {blob}\t{rel}\n".encode())
    subprocess.run(
        ["git", "update-index", "--index-info"],
        input=b"".join(lines),
        env=env,
        check=True,
    )
    tree = subprocess.check_output(["git", "write-tree"], env=env).decode().strip()
    commit = subprocess.check_output(
        ["git", "commit-tree", tree, "-F", "-"],
        input=b"runtime base\n",
        env=env,
    ).decode().strip()
    # Fixture-only: point branch at genesis commit (5C2 never does this).
    subprocess.check_call(
        ["git", "update-ref", f"refs/heads/{branch}", commit],
        env=env,
    )
    subprocess.check_call(
        ["git", "symbolic-ref", "HEAD", f"refs/heads/{branch}"],
        env=env,
    )
    if index.exists():
        index.unlink()
    return commit


def tempfile_index_path(repo: Path) -> str:
    return str(repo.parent / f".fixture-index-{repo.name}")


def run_transaction(
    repo: Path,
    *,
    reference_sets=None,
    behavior_policy: Mapping[str, Any] | None = None,
    target_inputs=None,
    executing_engine_commit_sha: str | None = None,
    current_engine_sha_from_head: bool = True,
):
    refs = reference_sets if reference_sets is not None else _advance_refs()
    policy = behavior_policy if behavior_policy is not None else POLICY
    inputs = target_inputs if target_inputs is not None else _target_inputs(event_budget=0)
    if executing_engine_commit_sha is not None:
        exec_sha = executing_engine_commit_sha
    elif current_engine_sha_from_head:
        # Read engine SHA from frozen HEAD blob state/current.json via plumbing.
        data = subprocess.check_output(
            ["git", "-C", str(repo), "show", "HEAD:state/current.json"]
        )
        import json

        exec_sha = json.loads(data.decode())["engine_commit_sha"]
    else:
        exec_sha = ENGINE_SHA
    return build_runtime_git_transaction(
        runtime_repo=repo,
        reference_sets=refs,
        behavior_policy=policy,
        target_inputs=inputs,
        executing_engine_commit_sha=exec_sha,
    )


def snapshot_refs_head(repo: Path) -> tuple[str, str | None, dict[str, str]]:
    head = git_out(repo, "rev-parse", "HEAD")
    sym = git(repo, "symbolic-ref", "-q", "HEAD", check=False)
    symbolic = sym.stdout.decode().strip() if sym.returncode == 0 else None
    raw = git_out(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    refs: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        name, oid = line.split(" ", 1)
        refs[name] = oid.lower()
    return head.lower(), symbolic or None, refs


def snapshot_index(repo: Path) -> str | None:
    git_dir = Path(git_out(repo, "rev-parse", "--absolute-git-dir"))
    index = git_dir / "index"
    if not index.is_file():
        return None
    return hashlib.sha256(index.read_bytes()).hexdigest()


def snapshot_worktree(repo: Path) -> str | None:
    bare = git_out(repo, "rev-parse", "--is-bare-repository") == "true"
    if bare:
        return None
    entries: list[str] = []
    git_dir = Path(git_out(repo, "rev-parse", "--absolute-git-dir"))
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        try:
            path.relative_to(git_dir)
            continue
        except ValueError:
            pass
        rel = str(path.relative_to(repo)).replace(os.sep, "/")
        entries.append(f"{rel}:{hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def count_git_objects(repo: Path) -> int:
    git_dir = Path(git_out(repo, "rev-parse", "--absolute-git-dir"))
    objects = git_dir / "objects"
    n = 0
    for path in objects.rglob("*"):
        if path.is_file() and path.parent.name != "info" and path.parent.name != "pack":
            if path.name != "packs" and not path.name.endswith(".idx"):
                n += 1
    for path in (objects / "pack").glob("*.pack") if (objects / "pack").is_dir() else []:
        n += 1
    return n


def object_db_fingerprint(repo: Path) -> str:
    git_dir = Path(git_out(repo, "rev-parse", "--absolute-git-dir"))
    objects = git_dir / "objects"
    rows: list[str] = []
    for path in sorted(objects.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(objects)).replace(os.sep, "/")
            rows.append(f"{rel}:{hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


def refs_map(repo: Path) -> dict[str, str]:
    return snapshot_refs_head(repo)[2]
