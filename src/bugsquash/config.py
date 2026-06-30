"""bugsquash.config — autonomy posture (env) + per-project config (bugsquash.toml).

Two layers of configuration, kept deliberately separate:

1. **Autonomy posture** (``Posture``) — read from the ENVIRONMENT. These are the
   safety arm-switches that decide whether the gate may actually auto-merge. The
   DEFAULTS are the safest possible posture for a freshly-installed system:

     AUTO_SQUASH_ENABLED            default "1"  — the KILL SWITCH. Set "0" and the
                                                  autonomous tier is dead instantly:
                                                  intake + triage + propose-branch
                                                  keep working, nothing auto-merges.
     AUTO_SQUASH_SHADOW             default "1"  — SHADOW MODE. While on, the gate
                                                  computes what it WOULD auto-merge
                                                  and records it, but never merges.
                                                  Flip to "0" only after a clean
                                                  shadow record. Defaulting ON means
                                                  a fresh install cannot auto-merge
                                                  anything until you make that call.
     AUTO_SQUASH_MAX_MERGES_PER_DAY default 1   — RATE LIMIT. A triage misclass
                                                  can't fan out into a string of bad
                                                  merges before you see the first.
     AUTO_SQUASH_MERGE_ARMED        default "0" — even with the gate green + panel
                                                  approved, the literal git merge
                                                  only runs when this is set. Default
                                                  OFF → dry-run ("WOULD merge").
     AUTO_SQUASH_STATE_DIR          points the rate-limit ledger + ticket store at a
                                                  writable dir (default per-repo
                                                  ``.bugsquash/state``).

2. **Project config** (``ProjectConfig``) — read from ``bugsquash.toml`` at the repo
   root (or built-in safe defaults if absent). This is what makes the engine work
   on ANY product: the deny-list, the blast-radius caps, the review-panel seats,
   and the fix/repro/merge commands all live here, not hardcoded.

Both are merged into a single ``Settings`` for the runner's convenience, but the
gate takes them separately so a unit test can construct either in isolation.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

try:  # py3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    tomllib = None  # type: ignore

from . import caps as caps_mod
from . import deny_list as deny_mod

# ── env var names (autonomy posture) ───────────────────────────────────────────
ENV_ENABLED = "AUTO_SQUASH_ENABLED"
ENV_SHADOW = "AUTO_SQUASH_SHADOW"
ENV_MAX_MERGES = "AUTO_SQUASH_MAX_MERGES_PER_DAY"
ENV_MERGE_ARMED = "AUTO_SQUASH_MERGE_ARMED"
ENV_STATE_DIR = "AUTO_SQUASH_STATE_DIR"

DEFAULT_MAX_MERGES_PER_DAY = 1
CONFIG_FILENAME = "bugsquash.toml"


def _truthy(val: str) -> bool:
    return (val or "").strip().lower() not in ("0", "false", "no", "off", "")


# ── 1. autonomy posture (env) ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Posture:
    """A snapshot of the autonomy posture (read once per gate evaluation)."""

    enabled: bool                 # kill switch ON (autonomy alive)?
    shadow: bool                  # shadow mode ON (never actually merge)?
    merge_armed: bool             # the literal-merge arm switch (OFF → dry-run)
    max_merges_per_day: int
    state_dir: Path

    @property
    def may_auto_merge(self) -> bool:
        """The posture permits a real auto-merge (rate limit checked separately)."""
        return self.enabled and not self.shadow

    def describe(self) -> str:
        if not self.enabled:
            return "DISABLED (kill switch AUTO_SQUASH_ENABLED=0)"
        if self.shadow:
            return "SHADOW (AUTO_SQUASH_SHADOW=1 — proposes + gates, never merges)"
        armed = "armed" if self.merge_armed else "dry-run (AUTO_SQUASH_MERGE_ARMED=0)"
        return f"LIVE ({armed}, ≤{self.max_merges_per_day}/day)"


def _default_state_dir(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root) / ".bugsquash" / "state"
    return Path.home() / ".local" / "state" / "bugsquash"


def load(env: Optional[dict] = None, *, repo_root: Optional[Path] = None) -> Posture:
    """Read the autonomy posture from the environment (injectable for tests)."""
    env = os.environ if env is None else env

    enabled = _truthy(env.get(ENV_ENABLED, "1"))
    shadow = _truthy(env.get(ENV_SHADOW, "1"))      # shadow-first DEFAULT
    armed = _truthy(env.get(ENV_MERGE_ARMED, "0"))  # dry-run DEFAULT

    raw_max = env.get(ENV_MAX_MERGES, str(DEFAULT_MAX_MERGES_PER_DAY))
    try:
        max_merges = max(0, int(raw_max))
    except (TypeError, ValueError):
        max_merges = DEFAULT_MAX_MERGES_PER_DAY

    state = env.get(ENV_STATE_DIR)
    state_dir = Path(state) if state else _default_state_dir(repo_root)

    return Posture(
        enabled=enabled,
        shadow=shadow,
        merge_armed=armed,
        max_merges_per_day=max_merges,
        state_dir=state_dir,
    )


# ── 2. project config (bugsquash.toml) ──────────────────────────────────────────
@dataclass(frozen=True)
class Limits:
    """Blast-radius caps for the no-tap auto-merge tier (design §3.1)."""

    max_files: int = caps_mod.MAX_FILES
    max_lines: int = caps_mod.MAX_LINES
    auto_severities: tuple[str, ...] = caps_mod.AUTO_SEVERITIES
    dependency_globs: tuple[str, ...] = caps_mod.DEPENDENCY_GLOBS
    schema_globs: tuple[str, ...] = caps_mod.SCHEMA_GLOBS
    infra_globs: tuple[str, ...] = caps_mod.INFRA_GLOBS


@dataclass(frozen=True)
class PanelConfig:
    """The adversarial review-panel settings."""

    enabled: bool = True
    seats: tuple[str, ...] = ("product", "safety", "architecture")
    model: str = "opus"
    timeout_s: int = 240
    # directory holding per-seat agent definitions (souls); generated by `init`.
    agents_dir: Optional[Path] = None


@dataclass(frozen=True)
class FixConfig:
    """How a fix is produced, proven, branched, and merged."""

    # the shell command that REPRODUCES the bug (exit 0 == reproduces). Optional;
    # if empty, repro proof falls back to the squash agent's self-report (which
    # the caps still require, so a fix with no repro proof is human-gated).
    repro_command: str = ""
    # the test/build command run after a fix to prove green (exit 0 == pass).
    test_command: str = ""
    branch_prefix: str = "bugsquash/"
    base: str = ""               # base branch; "" → current branch / HEAD
    # the model the squash (fix-writing) agent runs as.
    fix_model: str = "opus"
    fix_timeout_s: int = 1200


@dataclass(frozen=True)
class ProjectConfig:
    """Everything that makes the engine product-specific. Loaded from bugsquash.toml."""

    repo_root: Path
    repo_label: str
    deny_globs: tuple[str, ...]
    outbound_tells: tuple[str, ...]
    limits: Limits = field(default_factory=Limits)
    panel: PanelConfig = field(default_factory=PanelConfig)
    fix: FixConfig = field(default_factory=FixConfig)
    config_path: Optional[Path] = None

    def describe(self) -> str:
        return (f"repo={self.repo_label} deny={len(self.deny_globs)} globs "
                f"caps={self.limits.max_files}f/{self.limits.max_lines}L "
                f"panel={'on' if self.panel.enabled else 'off'}")


def find_config(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (default cwd) looking for bugsquash.toml."""
    cur = Path(start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        cand = parent / CONFIG_FILENAME
        if cand.is_file():
            return cand
    return None


def git_root(start: Optional[Path] = None) -> Path:
    """Best-effort git top-level; falls back to ``start`` (or cwd)."""
    import subprocess
    base = Path(start or Path.cwd())
    try:
        out = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return base.resolve()


def load_project(start: Optional[Path] = None) -> ProjectConfig:
    """Load ProjectConfig from the nearest bugsquash.toml, or safe defaults.

    ``start`` is where to begin the upward search (default cwd). When no config
    file is found, returns built-in safe defaults rooted at the git top-level.
    """
    cfg_path = find_config(start)
    root = cfg_path.parent if cfg_path else git_root(start)
    data: dict = {}
    if cfg_path and tomllib is not None:
        try:
            data = tomllib.loads(cfg_path.read_text())
        except Exception:
            data = {}

    repo_tbl = data.get("repo", {})
    repo_label = repo_tbl.get("label") or root.name

    # deny-list: built-in safe defaults, optionally extended or replaced.
    deny_tbl = data.get("deny", {})
    user_globs = tuple(deny_tbl.get("globs", []) or [])
    if deny_tbl.get("replace_defaults"):
        deny_globs = user_globs
    else:
        deny_globs = tuple(deny_mod.DEFAULT_DENY_GLOBS) + user_globs
    outbound = deny_tbl.get("outbound_tells")
    outbound_tells = tuple(outbound) if outbound is not None else deny_mod.DEFAULT_OUTBOUND_TELLS

    # caps
    caps_tbl = data.get("caps", {})
    limits = Limits(
        max_files=int(caps_tbl.get("max_files", caps_mod.MAX_FILES)),
        max_lines=int(caps_tbl.get("max_lines", caps_mod.MAX_LINES)),
        auto_severities=tuple(caps_tbl.get("auto_severities", caps_mod.AUTO_SEVERITIES)),
        dependency_globs=tuple(caps_tbl.get("dependency_globs", caps_mod.DEPENDENCY_GLOBS)),
        schema_globs=tuple(caps_tbl.get("schema_globs", caps_mod.SCHEMA_GLOBS)),
        infra_globs=tuple(caps_tbl.get("infra_globs", caps_mod.INFRA_GLOBS)),
    )

    # panel
    panel_tbl = data.get("panel", {})
    agents_dir = panel_tbl.get("agents_dir")
    panel = PanelConfig(
        enabled=bool(panel_tbl.get("enabled", True)),
        seats=tuple(panel_tbl.get("seats", ("product", "safety", "architecture"))),
        model=panel_tbl.get("model", "opus"),
        timeout_s=int(panel_tbl.get("timeout_s", 240)),
        agents_dir=(root / agents_dir) if agents_dir else (root / ".bugsquash" / "agents"),
    )

    # fix
    fix_tbl = data.get("fix", {})
    fix = FixConfig(
        repro_command=fix_tbl.get("repro_command", ""),
        test_command=fix_tbl.get("test_command", ""),
        branch_prefix=fix_tbl.get("branch_prefix", "bugsquash/"),
        base=fix_tbl.get("base", ""),
        fix_model=fix_tbl.get("fix_model", "opus"),
        fix_timeout_s=int(fix_tbl.get("fix_timeout_s", 1200)),
    )

    return ProjectConfig(
        repo_root=root,
        repo_label=repo_label,
        deny_globs=deny_globs,
        outbound_tells=outbound_tells,
        limits=limits,
        panel=panel,
        fix=fix,
        config_path=cfg_path,
    )


@dataclass(frozen=True)
class Settings:
    """Convenience bundle: autonomy posture + project config together."""

    posture: Posture
    project: ProjectConfig


def load_all(start: Optional[Path] = None, env: Optional[dict] = None) -> Settings:
    project = load_project(start)
    posture = load(env, repo_root=project.repo_root)
    return Settings(posture=posture, project=project)
