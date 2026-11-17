"""bugsquash.init — set up bug-squash in a target repo.

``bug-squash init`` writes the per-project config + the review-panel agent files
(souls) into the repo so the engine is ready to run. By default it tries to make
those artifacts REPO-SPECIFIC by introspecting the codebase with a one-shot
``claude`` call (the quality differentiator: a deny-list and reviewers tuned to
YOUR product). With ``--no-introspect`` (or when ``claude`` is unavailable) it
writes solid generic defaults instead.

Artifacts written (none overwritten unless ``--force``):
  * ``bugsquash.toml``                      — config (deny-list, caps, panel, fix)
  * ``.bugsquash/agents/reviewer-*.md``     — per-seat reviewer souls
  * appends ``.bugsquash/state/`` + ``.bugsquash/worktrees/`` to ``.gitignore``
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from . import config as config_mod
from . import review_panel

DEFAULT_TOML = """\
# bugsquash.toml — config for claude-bug-squash (https://github.com/ai-foundry-byu/claude-bug-squash)
# Safe-by-default: auto-merge is OFF until you flip the env switches (see README).

[repo]
label = "{label}"

[caps]
# A fix is eligible for the no-tap autonomous tier ONLY if it stays within these.
max_files = 3
max_lines = 40
auto_severities = ["p2", "p3"]   # p0/p1 always get a human

[deny]
# Files an autonomous fix may NEVER touch (hard BLOCK → diagnosis only). These
# EXTEND the built-in safe defaults (auth/secrets/payments/migrations/deploy/...).
# Set replace_defaults = true to use ONLY your list.
globs = [
{deny_globs}
]
replace_defaults = false

[panel]
# The adversarial review panel runs before any merge. Disable with enabled = false
# (or --no-panel) to save tokens — less safe.
enabled = true
seats = ["product", "safety", "architecture"]
model = "opus"
timeout_s = 240

[fix]
# How a fix is produced, proven, and branched.
# repro_command should FAIL (non-zero exit) while the bug is present and PASS once
# it's fixed — the red→green proof the gate requires for an autonomous merge.
repro_command = "{repro_command}"
test_command = "{test_command}"
branch_prefix = "bugsquash/"
base = ""              # base branch to fix off; "" = current branch
fix_model = "opus"
"""


def _detect(root: Path) -> dict:
    """Cheap heuristics to seed the config when not introspecting."""
    label = root.name
    test_command = ""
    if (root / "package.json").exists():
        try:
            pj = json.loads((root / "package.json").read_text())
            if "test" in (pj.get("scripts") or {}):
                test_command = "npm test"
        except Exception:
            pass
    elif (root / "pyproject.toml").exists() or (root / "pytest.ini").exists() or (root / "tests").is_dir():
        test_command = "pytest -q"
    elif (root / "Cargo.toml").exists():
        test_command = "cargo test"
    elif (root / "go.mod").exists():
        test_command = "go test ./..."
    return {"label": label, "test_command": test_command, "repro_command": ""}


def _introspect(root: Path, model: str = "haiku", timeout: int = 180) -> Optional[dict]:
    """Ask claude to propose repo-specific deny globs + reviewer souls."""
    if not shutil.which("claude"):
        return None
    prompt = (
        "You are setting up an autonomous bug-fixing tool on this repository. "
        "Inspect the codebase (read the file tree, key configs, and a few source "
        "files) and return ONLY a JSON object with:\n"
        '{\n'
        '  "deny_globs": [ up to 15 glob patterns for files an automated fix must '
        'NEVER touch in THIS repo — auth, secrets, payment, billing, migrations, '
        'deploy/release, infra, and any security-critical or irreversible code '
        'specific to this project ],\n'
        '  "test_command": "the command that runs this repo\'s tests",\n'
        '  "souls": {\n'
        '     "product": "2-3 sentences telling a PRODUCT reviewer what user-facing '
        'behaviour and invariants THIS product must never regress",\n'
        '     "safety": "2-3 sentences telling a SAFETY reviewer what in THIS repo is '
        'dangerous/irreversible/external-facing",\n'
        '     "architecture": "2-3 sentences telling an ARCHITECTURE reviewer the key '
        'conventions and structure of THIS codebase"\n'
        '  }\n'
        '}\n'
        "Be specific to what you actually see. No prose outside the JSON."
    )
    env = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS"):
        if not env.get(var, "").startswith("sk-ant-api"):
            env.pop(var, None)
    try:
        cp = subprocess.run(
            ["claude", "-p", prompt, "--model", model,
             "--allowedTools", "Read,Grep,Glob,Bash(ls:*),Bash(cat:*),Bash(find:*),Bash(git:*)",
             "--permission-mode", "default"],
            capture_output=True, text=True, timeout=timeout, cwd=str(root), env=env,
        )
        out = cp.stdout or ""
    except Exception:
        return None
    import re
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _write_agents(agents_dir: Path, souls: Optional[dict], seats, force: bool) -> list[str]:
    written = []
    agents_dir.mkdir(parents=True, exist_ok=True)
    for seat in seats:
        path = agents_dir / f"reviewer-{seat}.md"
        if path.exists() and not force:
            continue
        soul = (souls or {}).get(seat) if souls else None
        body = soul or review_panel.BUILTIN_SOULS.get(seat, "")
        front = (f"---\nname: reviewer-{seat}\n"
                 f"description: bug-squash {seat} review seat\n---\n\n")
        path.write_text(front + body.strip() + "\n")
        written.append(str(path))
    return written


def _ensure_gitignore(root: Path) -> None:
    gi = root / ".gitignore"
    lines = []
    if gi.exists():
        lines = gi.read_text().splitlines()
    for entry in (".bugsquash/state/", ".bugsquash/worktrees/"):
        if entry not in lines:
            lines.append(entry)
    gi.write_text("\n".join(lines) + "\n")


def init_repo(start: Optional[Path] = None, *, introspect: bool = True,
              force: bool = False, model: str = "haiku") -> dict:
    """Run init in the repo containing ``start`` (default cwd). Returns a summary."""
    root = config_mod.git_root(start)
    info = _detect(root)
    souls = None
    deny_globs: list[str] = []

    if introspect:
        res = _introspect(root, model=model)
        if res:
            souls = res.get("souls")
            deny_globs = [g for g in (res.get("deny_globs") or []) if isinstance(g, str)]
            if res.get("test_command"):
                info["test_command"] = res["test_command"]

    written = []
    toml_path = root / config_mod.CONFIG_FILENAME
    if not toml_path.exists() or force:
        deny_block = "\n".join(f'    "{g}",' for g in deny_globs) if deny_globs else \
            '    # "src/your/critical/path/**",'
        toml_path.write_text(DEFAULT_TOML.format(
            label=info["label"],
            deny_globs=deny_block,
            repro_command=info.get("repro_command", ""),
            test_command=info.get("test_command", ""),
        ))
        written.append(str(toml_path))

    project = config_mod.load_project(root)
    written += _write_agents(project.panel.agents_dir, souls, project.panel.seats, force)
    _ensure_gitignore(root)

    return {
        "repo_root": str(root),
        "config": str(toml_path),
        "introspected": bool(souls or deny_globs),
        "written": written,
        "deny_globs_added": len(deny_globs),
    }
