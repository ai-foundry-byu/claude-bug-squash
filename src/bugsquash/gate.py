"""bug_squash.gate — THE diff gate (design §3.4 point 3 + §3.1 + §3.6).

This is the load-bearing safety layer of the whole project. It takes the ACTUAL
``git diff`` of a produced fix plus the ticket's triaged metadata and returns a
single ``GateVerdict`` that decides whether an autonomous merge may happen. It is
the layer "the agent cannot self-certify out of": even if the squash agent
believes its fix is tiny and safe, the gate counts the diff itself and re-checks
the deny-list against the real paths.

Decision precedence (most-restrictive wins; later checks never *upgrade* an
earlier verdict):

  1. NO_CHANGES   — the fix produced an empty diff. Nothing to merge; escalate.
  2. BLOCK        — the diff touches a §3.4 deny-list file (or a new file whose
                    content is outbound). HARD refuse + escalate, regardless of
                    tier, caps, or posture. The worst case the §3 rails exist for
                    (auth bypass / budget hard-stop / safety-rail edit) lives here.
  3. HUMAN_GATE   — fix is clean of deny-list but is NOT eligible for a no-tap
                    merge: the triaged tier isn't ``auto-safe``, OR a §3.1 cap is
                    violated, OR the kill switch is off, OR the daily rate limit is
                    spent. The fix stays on a branch; merge waits for a human's tap.
  4. SHADOW       — clean + auto-safe + within caps + rate budget, but shadow mode
                    is on. The gate records what it WOULD auto-merge; it does NOT
                    merge. This is the default posture of a fresh Phase-2 build.
  5. AUTO_MERGE   — clean + auto-safe + within caps + kill switch on + shadow off
                    + under the daily rate limit. The only verdict that authorizes
                    a no-human-tap merge.

Only ``AUTO_MERGE`` sets ``auto_merge_allowed``. Everything else leaves the merge
to a human (or refuses it entirely).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import caps as caps_mod
from . import config as config_mod
from . import deny_list
from .diff import Changeset
from .ratelimit import RateLimiter

# Decisions, ordered loosely most→least restrictive.
NO_CHANGES = "NO_CHANGES"
BLOCK = "BLOCK"            # never-auto: deny-list hit on the real diff
HUMAN_GATE = "HUMAN_GATE"  # fix on branch, needs a human's tap
SHADOW = "SHADOW"          # would auto-merge, but shadow mode → don't
AUTO_MERGE = "AUTO_MERGE"  # authorized no-tap merge

AUTO_SAFE_TIER = "auto-safe"
HUMAN_GATE_TIER = "human-gate"
NEVER_AUTO_TIER = "never-auto"


@dataclass(frozen=True)
class GateVerdict:
    """The gate's decision plus a full, replayable account of why."""

    decision: str
    repo: str
    severity: str
    tier_in: str                       # the triaged risk_tier handed to the gate
    reasons: tuple[str, ...] = ()      # human-readable, ordered
    denied_files: tuple[str, ...] = ()  # §3.4 hits on the actual diff
    cap_violations: tuple[caps_mod.CapViolation, ...] = ()
    changeset: Optional[dict] = None   # Changeset.summary()
    posture: Optional[dict] = None     # describe() + raw flags
    rate_remaining: Optional[int] = None

    @property
    def auto_merge_allowed(self) -> bool:
        return self.decision == AUTO_MERGE

    @property
    def blocked(self) -> bool:
        return self.decision == BLOCK

    @property
    def needs_human(self) -> bool:
        return self.decision in (HUMAN_GATE, BLOCK, NO_CHANGES)

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "auto_merge_allowed": self.auto_merge_allowed,
            "repo": self.repo,
            "severity": self.severity,
            "tier_in": self.tier_in,
            "reasons": list(self.reasons),
            "denied_files": list(self.denied_files),
            "cap_violations": [
                {"code": v.code, "detail": v.detail, "paths": list(v.paths)}
                for v in self.cap_violations
            ],
            "changeset": self.changeset,
            "posture": self.posture,
            "rate_remaining": self.rate_remaining,
        }

    def headline(self) -> str:
        bits = [f"{self.decision}", f"tier={self.tier_in}", f"sev={self.severity}"]
        if self.changeset:
            bits.append(
                f"{self.changeset['file_count']}f/{self.changeset['churn']}L"
            )
        return "  ".join(bits)


def _denied_in_diff(
    cs: Changeset,
    new_file_contents: dict[str, str] | None,
    globs=None,
    tells=None,
) -> list[str]:
    """Every diff path that hits the deny-list, plus any brand-new file whose
    content shells out to an outbound channel (§3.4 caveat 4).

    Checks the OLD path of renames too: moving a file *out* of a deny zone (e.g.
    renaming ``src/auth.ts``) still touches that file and must block.
    """
    candidate_paths = list(cs.paths)
    candidate_paths += [f.old_path for f in cs.files if f.old_path]
    hits = deny_list.filter_denied(candidate_paths, globs)
    if new_file_contents:
        for fc in cs.files:
            if fc.status == "A" and fc.path not in hits:
                body = new_file_contents.get(fc.path, "")
                if deny_list.content_is_outbound(body, tells):
                    hits.append(deny_list.normalize_path(fc.path))
    # de-dup, preserve order
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def evaluate(
    changeset: Changeset,
    *,
    tier: str,
    severity: str,
    reproduced_before: bool,
    reproduced_after: bool,
    posture: Optional[config_mod.Posture] = None,
    project: Optional[config_mod.ProjectConfig] = None,
    rate_limiter: Optional[RateLimiter] = None,
    new_file_contents: Optional[dict[str, str]] = None,
) -> GateVerdict:
    """Run the full diff gate and return a ``GateVerdict``.

    ``tier`` / ``severity`` are the triage verdict carried on the ticket.
    ``reproduced_before`` / ``reproduced_after`` are the squash agent's VERIFIED
    repro results (red→green proof, §3.1 #7). ``posture`` defaults to reading the
    env switches; ``project`` (optional ``ProjectConfig``) supplies the per-repo
    deny-list, outbound tells, and blast-radius caps — when None, built-in safe
    defaults are used. ``rate_limiter`` defaults to one rooted at the posture's
    state dir. ``new_file_contents`` (optional) maps newly-added paths → their
    content so a new outbound-comms file is caught even though its path matches
    nothing.

    This function never performs a merge or any side effect except, when a real
    AUTO_MERGE is authorized, recording one unit against the daily rate limit (so
    the budget is consumed atomically with the decision). Shadow/human/blocked
    verdicts touch nothing.
    """
    posture = posture or config_mod.load()
    rate_limiter = rate_limiter or RateLimiter(posture.state_dir)

    deny_globs = project.deny_globs if project else None
    outbound_tells = project.outbound_tells if project else None
    limits = project.limits if project else None

    repo = changeset.repo
    posture_d = {
        "describe": posture.describe(),
        "enabled": posture.enabled,
        "shadow": posture.shadow,
        "max_merges_per_day": posture.max_merges_per_day,
    }
    cs_summary = changeset.summary()

    def verdict(decision: str, reasons: list[str], **kw) -> GateVerdict:
        return GateVerdict(
            decision=decision,
            repo=repo,
            severity=severity,
            tier_in=tier,
            reasons=tuple(reasons),
            changeset=cs_summary,
            posture=posture_d,
            **kw,
        )

    # 1. empty diff — a "fix" that changed nothing. Escalate, never merge.
    if changeset.is_empty:
        return verdict(
            NO_CHANGES,
            ["the fix produced an empty diff — nothing to merge; escalate to a human"],
        )

    # 2. deny-list — the hard floor. Checked FIRST against the real diff so no
    #    later 'clean' path can ever talk past it.
    denied = _denied_in_diff(changeset, new_file_contents, deny_globs, outbound_tells)
    if denied:
        return verdict(
            BLOCK,
            [
                "NEVER-autonomous deny-list hit on the actual diff — hard refuse, "
                "escalate to a human with a diagnosis (no merge, gated or not):",
                *[f"  • {d}" for d in denied],
            ],
            denied_files=tuple(denied),
        )

    # 2b. never-auto tier — the design says these are diagnosis-only, NO
    #     agent-authored merge in ANY phase (not even a human-gated one). Treat a
    #     never-auto ticket that somehow reached the gate as a hard BLOCK.
    if tier == NEVER_AUTO_TIER:
        return verdict(
            BLOCK,
            [
                "ticket was triaged 'never-auto' (§3.4) — diagnosis only, no "
                "agent-authored merge in any phase; escalate to a human.",
            ],
        )

    # 3a. tier ceiling — only an auto-safe ticket is even a candidate for no-tap.
    reasons: list[str] = []
    forces_human = False
    if tier != AUTO_SAFE_TIER:
        forces_human = True
        reasons.append(
            f"triaged tier is {tier!r}, not {AUTO_SAFE_TIER!r} — only auto-safe "
            "tickets are eligible for a no-tap merge"
        )

    # 3b. §3.1 caps — counted against the real diff.
    violations = caps_mod.check(
        changeset,
        limits=limits,
        severity=severity,
        reproduced_before=reproduced_before,
        reproduced_after=reproduced_after,
    )
    if violations:
        forces_human = True
        reasons.extend(f"§3.1 cap violated [{v.code}]: {v.detail}" for v in violations)

    if forces_human:
        return verdict(
            HUMAN_GATE,
            reasons or ["not auto-eligible — fix stays on a branch, merge needs a human's tap"],
            cap_violations=tuple(violations),
        )

    # 4. clean + auto-safe. Now the POSTURE decides merge vs shadow vs gate.
    if not posture.enabled:
        return verdict(
            HUMAN_GATE,
            [
                "fix is within all §3.1 caps and clear of the §3.4 deny-list, but the "
                "kill switch is OFF (AUTO_SQUASH_ENABLED=0) — merge needs a human's tap",
            ],
        )

    rate_remaining = rate_limiter.remaining(posture.max_merges_per_day)
    if rate_limiter.would_exceed(posture.max_merges_per_day):
        return verdict(
            HUMAN_GATE,
            [
                f"fix is auto-eligible, but the daily auto-merge rate limit "
                f"({posture.max_merges_per_day}/day) is spent — merge needs a human's tap "
                "or waits for tomorrow",
            ],
            rate_remaining=0,
        )

    if posture.shadow:
        return verdict(
            SHADOW,
            [
                "fix WOULD auto-merge — within all §3.1 caps, clear of the §3.4 "
                "deny-list, auto-safe tier, under the rate limit. SHADOW mode is on "
                "(AUTO_SQUASH_SHADOW=1) so it is recorded, NOT merged. Flip "
                "AUTO_SQUASH_SHADOW=0 to go live after a clean shadow record.",
            ],
            rate_remaining=rate_remaining,
        )

    # 5. LIVE auto-merge. Consume one unit of the daily budget atomically with the
    #    decision so two concurrent gates can't both think they're under the cap.
    new_count = rate_limiter.record()
    return verdict(
        AUTO_MERGE,
        [
            "AUTHORIZED no-tap auto-merge — within all §3.1 caps, clear of the §3.4 "
            "deny-list, auto-safe tier, kill switch on, shadow off, under the daily "
            f"rate limit (merge {new_count}/{posture.max_merges_per_day} today).",
        ],
        rate_remaining=max(0, posture.max_merges_per_day - new_count),
    )
