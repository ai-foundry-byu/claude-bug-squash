"""bugsquash.deny_list — the NEVER-autonomous deny-list at the DIFF GATE.

This is the hard floor of the safety model: an INDEPENDENT re-check of the
deny-list against the *actual* ``git diff`` at merge time. The point is that the
fix-writing agent "cannot self-certify out of this" — even if its diff looks
tiny and safe, if it touches a deny-listed path the gate hard-BLOCKS it (the fix
becomes diagnosis-only and is escalated to a human, never merged).

A deny-list hit is stronger than a blast-radius cap: a cap demotes an otherwise
auto-safe fix to *human-gate* (you can still tap-merge it); a deny-list hit means
"no agent-authored change here in any tier."

``DEFAULT_DENY_GLOBS`` ships safe-broad defaults for the danger classes that are
universal across products — authentication, secrets, payments/money, schema
migrations, deploy/release, and the security rails themselves. They are
intentionally generous: blocking a borderline file is the SAFE failure mode (it
just routes to a human). Tune them for your repo in ``bugsquash.toml`` (the
``init`` command generates a tailored list by introspecting your codebase).
"""
from __future__ import annotations

import fnmatch
from typing import Iterable, Optional

# Safe-broad defaults, grouped by danger class. Globs use ``**`` (any depth) and
# match case-insensitively. Extend or replace these in bugsquash.toml [deny].
DEFAULT_DENY_GLOBS: tuple[str, ...] = (
    # ── AUTH / SESSION / IDENTITY ──────────────────────────────────────────────
    "**/auth/**",
    "**/*auth*",
    "**/login*",
    "**/logout*",
    "**/session*",
    "**/oauth*",
    "**/jwt*",
    "**/*token*",
    "**/passport*",
    "**/middleware/auth*",
    "**/guards/**",
    "**/permissions*",
    "**/rbac*",
    # ── SECRETS / CREDENTIALS / KEYS ───────────────────────────────────────────
    "**/.env",
    "**/.env.*",
    "**/*secret*",
    "**/*credential*",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.keystore",
    "**/keychain*",
    # ── PAYMENTS / MONEY ───────────────────────────────────────────────────────
    "**/payment*/**",
    "**/*payment*",
    "**/billing/**",
    "**/checkout/**",
    "**/stripe*",
    "**/*invoice*",
    "**/wallet*",
    "**/ledger*",
    "**/pricing*",
    # ── SCHEMA / MIGRATIONS / DDL ──────────────────────────────────────────────
    "**/migrations/**",
    "**/migration/**",
    "**/*.sql",
    "**/schema.prisma",
    "**/alembic/**",
    # ── DEPLOY / RELEASE / INFRA ───────────────────────────────────────────────
    "**/.github/workflows/**",
    "**/deploy*",
    "**/release*.sh",
    "**/Dockerfile*",
    "**/docker-compose*",
    "**/*.tf",
    "**/*.tfvars",
    "**/k8s/**",
    "**/kubernetes/**",
    "**/helm/**",
    "**/terraform/**",
    "**/ansible/**",
    "**/fastlane/**",
    "**/*.entitlements",
    "**/*.provisionprofile",
    "**/electron-builder*",
    "**/*.notarize*",
    # ── SECURITY RAILS / HOOKS (don't let a fix weaken its own guards) ──────────
    ".git/hooks/**",
    "**/.git/hooks/**",
    "**/*.pre-commit*",
    "**/.pre-commit-config.yaml",
    "**/*watchdog*",
    "**/*-guard*",
    "**/security/**",
    "**/bugsquash.toml",
    "**/.bugsquash/**",
)

# Substring tells for "this NEW file does outbound comms / moves money". A
# brand-new file whose *content* shells out to an external channel is treated as
# external-send even though its path matches nothing above (the gate passes new
# files' content here; see ``content_is_outbound``).
DEFAULT_OUTBOUND_TELLS: tuple[str, ...] = (
    "send_email",
    "sendmail",
    "smtplib",
    "nodemailer",
    "sendgrid",
    "mailgun",
    "ses.send",
    "twilio",
    "stripe.",
    "paypal",
    "slack_sdk",
    "webhook",
    "requests.post",
    "fetch(",
)


def normalize_path(path: str) -> str:
    """Normalize a diff path to a clean repo-relative form for glob matching.

    Strips a leading ``./`` (but NOT the dot in ``.git``/``.github``) and
    backslashes → forward slashes. Idempotent.
    """
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return ""
    while p.startswith("./"):
        p = p[2:]
    return p


def _matches_glob(p: str, glob: str) -> bool:
    """Match one normalized path against one deny glob, case-insensitively, with
    generous ``/**`` and ``**/`` semantics (so ``dir/**`` catches the bare dir and
    ``**/x`` catches any trailing segment-run even where fnmatch's ``**`` doesn't
    span '/')."""
    pl, gl = p.lower(), glob.lower()
    if fnmatch.fnmatch(pl, gl):
        return True
    if gl.endswith("/**"):
        base = gl[:-3]
        if pl == base or pl.startswith(base + "/"):
            return True
    if gl.startswith("**/"):
        suffix = gl[3:]
        if fnmatch.fnmatch(pl, suffix):
            return True
        segs = pl.split("/")
        if any(fnmatch.fnmatch("/".join(segs[i:]), suffix) for i in range(len(segs))):
            return True
    return False


def is_denied(path: str, globs: Optional[Iterable[str]] = None) -> bool:
    """True if ``path`` matches any deny glob (defaults to DEFAULT_DENY_GLOBS)."""
    p = normalize_path(path)
    if not p:
        return False
    globs = DEFAULT_DENY_GLOBS if globs is None else globs
    return any(_matches_glob(p, g) for g in globs)


def first_denied(paths: Iterable[str], globs: Optional[Iterable[str]] = None) -> Optional[str]:
    """Return the first denied path (normalized), or None."""
    globs = DEFAULT_DENY_GLOBS if globs is None else tuple(globs)
    for p in paths:
        if is_denied(p, globs):
            return normalize_path(p)
    return None


def filter_denied(paths: Iterable[str], globs: Optional[Iterable[str]] = None) -> list[str]:
    """Return only the denied paths (normalized)."""
    globs = DEFAULT_DENY_GLOBS if globs is None else tuple(globs)
    return [normalize_path(p) for p in paths if is_denied(p, globs)]


def content_is_outbound(content: str, tells: Optional[Iterable[str]] = None) -> bool:
    """True if file *content* shells out to an outbound channel — used to treat a
    brand-new, path-unmatched file as external-send."""
    if not content:
        return False
    tells = DEFAULT_OUTBOUND_TELLS if tells is None else tells
    low = content.lower()
    return any(t.lower() in low for t in tells)


if __name__ == "__main__":  # pragma: no cover
    import sys
    for arg in sys.argv[1:]:
        verdict = "DENY" if is_denied(arg) else "ok  "
        print(f"{verdict}  {normalize_path(arg)}")
