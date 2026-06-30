"""bugsquash.git_merge — the real merge function (opt-in, arm-switched).

This is the ONLY code that performs an actual ``git merge`` of a fix branch into
the base. It is wired into the pipeline only when you explicitly pass it to
``integration.build_deps(merge_fn=git_merge.merge)`` AND set
``AUTO_SQUASH_MERGE_ARMED=1``. Until both are true, nothing merges — the default
posture is dry-run.

Safety: refuses to merge if the base working tree is dirty (uncommitted changes),
and only pushes when ``BUGSQUASH_PUSH=1`` is set. Returns a result dict.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def _is_clean(repo: Path) -> bool:
    cp = _git(repo, "status", "--porcelain")
    return cp.returncode == 0 and not cp.stdout.strip()


def make_merge(project, *, push: Optional[bool] = None):
    """Build a ``merge(ticket, attempt, verdict) -> dict`` bound to ``project``."""
    repo = Path(project.repo_root)
    do_push = push if push is not None else os.getenv("BUGSQUASH_PUSH", "0") not in ("0", "", "false")
    base = project.fix.base or _current_branch(repo)

    def merge(ticket: dict, attempt, verdict) -> dict:
        branch = attempt.branch
        if not branch:
            raise RuntimeError("no fix branch to merge")
        if not _is_clean(repo):
            raise RuntimeError("base working tree is dirty — refusing to merge "
                               "(commit/stash your changes first)")
        # remove the worktree so the branch can be checked out / merged cleanly
        if attempt.worktree:
            _git(repo, "worktree", "remove", "--force", attempt.worktree)
        co = _git(repo, "checkout", base)
        if co.returncode != 0:
            raise RuntimeError(f"checkout {base} failed: {co.stderr.strip()[:200]}")
        mg = _git(repo, "merge", "--no-ff", "-m",
                  f"bugsquash: fix #{str(ticket.get('id'))[:8]} {ticket.get('title','')[:60]}",
                  branch)
        if mg.returncode != 0:
            _git(repo, "merge", "--abort")
            raise RuntimeError(f"merge failed: {mg.stderr.strip()[:200]}")
        pushed = False
        if do_push:
            ps = _git(repo, "push", "origin", base)
            pushed = ps.returncode == 0
        _git(repo, "branch", "-D", branch)
        return {"merged_into": base, "branch": branch, "pushed": pushed}

    return merge


def _current_branch(repo: Path) -> str:
    cp = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return cp.stdout.strip() or "main"


# convenience module-level (loads project lazily)
def merge(ticket: dict, attempt, verdict) -> dict:
    from . import config
    return make_merge(config.load_project())(ticket, attempt, verdict)
