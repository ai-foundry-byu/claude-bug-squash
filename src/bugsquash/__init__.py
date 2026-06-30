"""claude-bug-squash — autonomous, gated bug-squashing for any repo.

Point Claude Code (or the standalone CLI) at your project and it will triage a
reported bug, write a fix in an isolated worktree, prove the bug red→green, and
run that fix through a layered safety gate (blast-radius caps + a never-touch
deny-list) and an adversarial review panel before anything merges — with shadow
mode and a per-day rate limit on by default so nothing reaches your trunk until
you explicitly arm it.

Public API:
    from bugsquash import config, store, triage, gate, review_panel, runner
    from bugsquash.spawn import make_spawn_fix
    from bugsquash.integration import build_deps
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "config",
    "store",
    "triage",
    "gate",
    "caps",
    "diff",
    "deny_list",
    "ratelimit",
    "fixer",
    "review_panel",
    "integration",
    "notify",
    "spawn",
    "runner",
    "init",
]
