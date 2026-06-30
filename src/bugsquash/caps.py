"""bug_squash.caps — the §3.1 scope / blast-radius caps, enforced on the diff.

Design §3.1: for the autonomous (no-human-tap) tier a fix is auto-eligible ONLY if
ALL of the following hold. Each is checked against the ACTUAL ``Changeset``, never
the agent's self-report:

  1. ≤ 3 files changed.
  2. ≤ 40 lines changed (we read "net lines" as total churn = added + deleted —
     the conservative blast-radius measure; deleting 100 lines is a big change
     even though its signed net is negative).
  3. 0 new dependencies (no package.json / lockfile / requirements / pyproject…).
  4. 0 schema / migration / DDL changes (no *.sql, nothing under */migrations/*).
  5. 0 infra / config changes (.github/workflows, vercel.json, *.config.*,
     tsconfig*, *.plist, .env*, crontab/*.cron).
  6. Severity is p2 or p3 (p0/p1 always gets a human tap).
  7. The bug reproduced before the fix and does NOT reproduce after (proven).
  8. (added) No binary blobs in the diff — unverifiable churn in a "small" fix.

A deny-list hit (§3.4) is handled separately by ``bug_squash.gate`` as a hard
BLOCK; this module only reports the non-deny caps. Any cap violation here demotes
an otherwise-auto-safe ticket to ``human-gate`` (fix stays on a branch, merge waits
for a human's tap) — it never silently downgrades to a merge.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from .diff import Changeset

# ── the hard numbers (design §3.1) ─────────────────────────────────────────────
MAX_FILES = 3
MAX_LINES = 40            # total churn (added + deleted)
AUTO_SEVERITIES = ("p2", "p3")

# ── path classes that are NEVER inside the autonomous envelope ──────────────────
# (4) dependency manifests / lockfiles — any change rewrites the dep graph.
DEPENDENCY_GLOBS = (
    "**/package.json",
    "**/package-lock.json",
    "**/npm-shrinkwrap.json",
    "**/yarn.lock",
    "**/pnpm-lock.yaml",
    "**/requirements*.txt",
    "**/Pipfile",
    "**/Pipfile.lock",
    "**/poetry.lock",
    "**/pyproject.toml",
    "**/setup.py",
    "**/setup.cfg",
    "**/Gemfile",
    "**/Gemfile.lock",
    "**/go.mod",
    "**/go.sum",
    "**/Cargo.toml",
    "**/Cargo.lock",
)

# (5) schema / migration / DDL.
SCHEMA_GLOBS = (
    "**/*.sql",
    "**/migrations/**",
    "**/migration/**",
)

# (6) infra / config — changes how the system deploys/runs.
INFRA_GLOBS = (
    "**/.github/workflows/**",
    "**/vercel.json",
    "**/*.config.js",
    "**/*.config.cjs",
    "**/*.config.mjs",
    "**/*.config.ts",
    "**/tsconfig*.json",
    "**/*.plist",
    "**/.env",
    "**/.env.*",
    "**/crontab",
    "**/crontab.*",
    "**/*.cron",
    "**/Dockerfile",
    "**/docker-compose*.yml",
    "**/docker-compose*.yaml",
)

# Reasons, named so callers/tests can match on a stable code.
CAP_FILES = "files"
CAP_LINES = "lines"
CAP_DEPS = "dependencies"
CAP_SCHEMA = "schema"
CAP_INFRA = "infra"
CAP_SEVERITY = "severity"
CAP_REPRO = "repro"
CAP_BINARY = "binary"


@dataclass(frozen=True)
class CapViolation:
    """One failed §3.1 cap."""

    code: str            # one of CAP_*
    detail: str          # human-readable explanation
    paths: tuple[str, ...] = ()   # the offending files, when path-scoped


def _match_any(path: str, globs) -> bool:
    # match both the full path and any trailing segment-run so "**/x" works even
    # where fnmatch's "**" doesn't span "/".
    if any(fnmatch.fnmatch(path, g) for g in globs):
        return True
    segs = path.split("/")
    tails = ["/".join(segs[i:]) for i in range(len(segs))]
    for g in globs:
        if g.startswith("**/"):
            suf = g[3:]
            if any(fnmatch.fnmatch(t, suf) for t in tails):
                return True
    return False


def _paths_matching(cs: Changeset, globs) -> list[str]:
    return [f.path for f in cs.files if _match_any(f.path, globs)]


def check(
    cs: Changeset,
    *,
    severity: str,
    reproduced_before: bool,
    reproduced_after: bool,
    limits=None,
) -> list[CapViolation]:
    """Return every §3.1 cap the changeset violates. Empty list ⇒ within the
    autonomous envelope (deny-list still checked separately by the gate).

    ``reproduced_before`` / ``reproduced_after`` encode the §3.1 #7 proof: the bug
    must have reproduced before the fix and must NOT reproduce after. Both come
    from the squash agent's verified repro runs (red→green), not a self-claim.

    ``limits`` (optional ``config.Limits``) overrides the built-in caps so a
    project can widen/narrow the autonomous envelope via bugsquash.toml. When
    None, the module defaults are used.
    """
    max_files = MAX_FILES if limits is None else limits.max_files
    max_lines = MAX_LINES if limits is None else limits.max_lines
    auto_sev = AUTO_SEVERITIES if limits is None else limits.auto_severities
    dep_globs = DEPENDENCY_GLOBS if limits is None else limits.dependency_globs
    schema_globs = SCHEMA_GLOBS if limits is None else limits.schema_globs
    infra_globs = INFRA_GLOBS if limits is None else limits.infra_globs

    violations: list[CapViolation] = []

    # (1) file count
    if cs.file_count > max_files:
        violations.append(
            CapViolation(
                CAP_FILES,
                f"{cs.file_count} files changed (cap {max_files})",
                tuple(cs.paths),
            )
        )

    # (2) line churn
    if cs.churn > max_lines:
        violations.append(
            CapViolation(
                CAP_LINES,
                f"{cs.churn} lines changed (+{cs.added}/-{cs.deleted}; cap {max_lines})",
            )
        )

    # (3) new dependencies
    deps = _paths_matching(cs, dep_globs)
    if deps:
        violations.append(
            CapViolation(CAP_DEPS, "touches dependency manifest/lockfile", tuple(deps))
        )

    # (4) schema / DDL
    schema = _paths_matching(cs, schema_globs)
    if schema:
        violations.append(
            CapViolation(CAP_SCHEMA, "touches schema/migration/DDL", tuple(schema))
        )

    # (5) infra / config
    infra = _paths_matching(cs, infra_globs)
    if infra:
        violations.append(
            CapViolation(CAP_INFRA, "touches infra/CI/build config", tuple(infra))
        )

    # (6) severity
    if severity not in auto_sev:
        violations.append(
            CapViolation(
                CAP_SEVERITY,
                f"severity {severity!r} — only {'/'.join(auto_sev)} are auto-eligible",
            )
        )

    # (7) repro proof (red→green)
    if not reproduced_before:
        violations.append(
            CapViolation(
                CAP_REPRO,
                "bug did not reproduce BEFORE the fix — can't prove the fix did anything",
            )
        )
    elif reproduced_after:
        violations.append(
            CapViolation(
                CAP_REPRO,
                "bug STILL reproduces after the fix — not actually fixed",
            )
        )

    # (8) binary blobs
    binaries = [f.path for f in cs.files if f.is_binary]
    if binaries:
        violations.append(
            CapViolation(
                CAP_BINARY,
                "binary blob in diff — unverifiable churn in a small fix",
                tuple(binaries),
            )
        )

    return violations
