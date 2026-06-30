"""bugsquash.spawn — the autonomous fix-writer (the ``spawn_fix`` seam).

This is the piece that turns a triaged ticket into a candidate fix. It is the seam
the fixer harness (``fixer.run_fix``) calls; everything downstream (gate, panel,
shadow/arm switches) re-checks its output, so this module is allowed to be the one
place that actually edits code — but ONLY inside a throwaway git worktree it
creates and tears down. It can never touch your real working tree and can never
merge: the gate + the double arm switch own that.

Flow for one attempt:
  1. ``git worktree add`` a fresh branch off the base — an isolated copy.
  2. Run the configured ``repro_command`` there → ``reproduced_before``.
  3. Spawn a scoped ``claude`` (Edit/Write/Read/Grep/Glob + the repro/test commands)
     with a prompt to fix the reported bug at its root cause, minimally.
  4. Run the ``repro_command`` again → ``reproduced_after``.
  5. Parse the worktree's diff (``diff.from_git``) and read new-file contents.
  6. Return a ``FixAttempt``. ``usable=False`` (empty diff / agent gave up) →
     the harness retries up to the attempt cap.

The worktree is kept when a usable fix is produced (so a human / a real merge can
use the branch) and removed when the attempt is empty.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import config as config_mod
from . import diff as diff_mod
from .diff import Changeset
from .fixer import FixAttempt

FIX_ALLOWED_TOOLS_DEFAULT = "Edit,Write,Read,Grep,Glob,MultiEdit"


def _git(repo: Path, *args: str, check: bool = False, timeout: int = 60) -> subprocess.CompletedProcess:
    cp = subprocess.run(["git", "-C", str(repo), *args],
                        capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {cp.stderr.strip()[:300]}")
    return cp


def _current_branch(repo: Path) -> str:
    cp = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return cp.stdout.strip() or "HEAD"


def _run_repro(cmd: str, cwd: Path, timeout: int = 600) -> Optional[bool]:
    """Run the repro command. True == reproduces (non-zero exit), False == clean,
    None == no command / error."""
    cmd = (cmd or "").strip()
    if not cmd:
        return None
    try:
        cp = subprocess.run(cmd, shell=True, cwd=str(cwd),
                            capture_output=True, text=True, timeout=timeout)
        return cp.returncode != 0
    except Exception:
        return None


def _allowed_tools(project: config_mod.ProjectConfig) -> str:
    """Scoped tool allowlist for the fixer: edits + git + the configured
    repro/test commands (so the agent can iterate), nothing else."""
    base = os.getenv("BUGSQUASH_FIX_ALLOWED_TOOLS", FIX_ALLOWED_TOOLS_DEFAULT)
    tools = [base, "Bash(git:*)"]
    for cmd in (project.fix.repro_command, project.fix.test_command):
        tok = (cmd or "").strip().split()
        if tok:
            tools.append(f"Bash({tok[0]}:*)")
    return ",".join(t for t in tools if t)


def _fix_prompt(ticket: dict, project: config_mod.ProjectConfig, attempt_n: int) -> str:
    title = (ticket.get("title") or "").strip()
    body = (ticket.get("body") or "").strip()
    affected = ticket.get("affected_files") or []
    repro = project.fix.repro_command
    test = project.fix.test_command
    retry = ("\nThis is a RETRY — a previous attempt did not produce a working fix. "
             "Re-read the code carefully and try a different root cause.\n") if attempt_n > 1 else ""
    return (
        "You are an autonomous bug-fixer working in an ISOLATED throwaway git "
        "worktree. Fix the reported bug at its ROOT CAUSE with the SMALLEST correct "
        "change. Do not refactor, do not touch unrelated code, do not add features.\n"
        f"{retry}\n"
        f"=== BUG ===\n"
        f"title: {title}\n"
        f"report: {body[:2000]}\n"
        f"likely files (from triage, may be incomplete): {', '.join(affected[:12]) or '(none located)'}\n\n"
        "Rules:\n"
        "1. Find the real cause — read the relevant code first. Don't paper over a "
        "symptom.\n"
        "2. Keep the diff tiny and contained. A good fix here is usually a few "
        "lines in 1-3 files.\n"
        "3. Do NOT edit auth, secrets, payment, migration, deploy, or CI files — "
        "those are off-limits and will be hard-blocked downstream anyway.\n"
        "4. If you genuinely cannot find or fix the bug, make NO changes and say so "
        "— an empty diff is better than a wrong guess.\n"
        + (f"5. Reproduce with: `{repro}` (it FAILS while the bug is present). " if repro else "")
        + (f"Verify your fix makes it PASS.\n" if repro else "")
        + (f"6. Make sure the test suite passes: `{test}`.\n" if test else "")
        + "\nWhen done, briefly state what the root cause was and what you changed."
    )


def _spawn_claude(prompt: str, cwd: Path, project: config_mod.ProjectConfig) -> str:
    """Spawn the scoped fix agent. Returns its stdout (its notes)."""
    if not shutil.which("claude"):
        raise RuntimeError("`claude` CLI not found on PATH — install Claude Code to "
                           "run the autonomous fixer.")
    env = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS"):
        if not env.get(var, "").startswith("sk-ant-api"):
            env.pop(var, None)
    cmd = [
        "claude", "-p", prompt,
        "--model", project.fix.fix_model,
        "--allowedTools", _allowed_tools(project),
        "--permission-mode", "acceptEdits",
    ]
    cp = subprocess.run(cmd, capture_output=True, text=True,
                        timeout=project.fix.fix_timeout_s, cwd=str(cwd), env=env)
    return (cp.stdout or "").strip()


def _read_new_files(worktree: Path, changeset: Changeset) -> dict[str, str]:
    out: dict[str, str] = {}
    for fc in changeset.files:
        if fc.status == "A":
            try:
                out[fc.path] = (worktree / fc.path).read_text(errors="ignore")[:20000]
            except Exception:
                pass
    return out


def make_spawn_fix(project: Optional[config_mod.ProjectConfig] = None):
    """Build a ``spawn_fix(ticket, attempt_n) -> FixAttempt`` bound to ``project``.

    This is what you hand to ``integration.build_deps(spawn_fix=...)``.
    """
    project = project or config_mod.load_project()
    repo = project.repo_root

    def spawn_fix(ticket: dict, attempt_n: int) -> FixAttempt:
        tid = str(ticket.get("id") or "x")[:8]
        suffix = f"-a{attempt_n}" if attempt_n > 1 else ""
        branch = f"{project.fix.branch_prefix}{tid}{suffix}"
        base = project.fix.base or _current_branch(repo)
        wt = Path(repo) / ".bugsquash" / "worktrees" / f"{tid}{suffix}"

        # clean any stale worktree/branch from a prior run
        _git(repo, "worktree", "remove", "--force", str(wt))
        _git(repo, "branch", "-D", branch)
        _git(repo, "worktree", "add", "-b", branch, str(wt), base, check=True, timeout=120)

        try:
            reproduced_before = _run_repro(project.fix.repro_command, wt)
            notes = _spawn_claude(_fix_prompt(ticket, project, attempt_n), wt, project)
            changeset = diff_mod.from_git(str(wt), base="HEAD", repo=project.repo_label)
            if changeset.is_empty:
                _git(repo, "worktree", "remove", "--force", str(wt))
                _git(repo, "branch", "-D", branch)
                return FixAttempt(changeset=changeset, reproduced_before=bool(reproduced_before),
                                  reproduced_after=False, usable=False,
                                  notes=notes or "agent made no changes")
            reproduced_after = _run_repro(project.fix.repro_command, wt)
            new_files = _read_new_files(wt, changeset)
            diff_text = _git(wt, "diff", "HEAD").stdout
            # With no repro command both are None→False, so the caps' repro-proof
            # check (gate §3.1 #7) forces human-gate — never a silent auto-merge.
            return FixAttempt(
                changeset=changeset,
                reproduced_before=bool(reproduced_before),
                reproduced_after=bool(reproduced_after),
                branch=branch,
                new_file_contents=new_files,
                notes=notes,
                diff_text=diff_text,
                worktree=str(wt),
                usable=True,
            )
        except Exception as e:  # noqa: BLE001 — a fixer crash is just a failed attempt
            _git(repo, "worktree", "remove", "--force", str(wt))
            return FixAttempt(changeset=Changeset(repo=project.repo_label),
                              reproduced_before=False, reproduced_after=False,
                              usable=False, notes=f"fixer error: {e}")

    return spawn_fix
