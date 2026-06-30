"""bug_squash.fixer — the autonomous-fixer harness (design §2.4 + §3.5/§3.6).

This sequences ONE triaged ``auto-safe`` ticket through a gated fix and applies the
``GateVerdict``. It owns the control flow — attempt cap, stop-and-escalate, and the
verdict → action mapping — but it deliberately OWNS NO side effects. Every real
action (spawn the squash agent, perform the merge, write ticket status, notify the maintainer)
is an INJECTED callable on ``FixerDeps``.

Why injected, not built-in:
  • Testability — the whole control flow is exercised with in-memory fakes.
  • Safety — this harness, as shipped here, CANNOT spawn a live agent, merge, push,
    or deploy. ``spawn_fix`` has no default (the caller MUST wire the real, gated
    squash agent), and ``merge`` defaults to a hard refusal. A later agent-system
    session wires the real implementations behind these seams; until then the
    harness is inert. This matches the build-time guardrail: no merging/pushing/
    deploying and no spawning live sessions from here.

Attempt cap (design §3.6): an agent that can't produce a usable fix is retried at
most ``MAX_ATTEMPTS`` (2) times, then the ticket is escalated — never thrashed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import config as config_mod
from . import gate as gate_mod
from .diff import Changeset
from .gate import GateVerdict
from .ratelimit import RateLimiter

MAX_ATTEMPTS = 2

# Ticket statuses written back (mirror bug_loop.store.VALID_STATUS).
ST_MERGED = "merged"
ST_IN_REVIEW = "in_review"
ST_ESCALATED = "escalated"
ST_FAILED = "failed"


@dataclass
class FixAttempt:
    """What a single squash-agent run produced.

    Returned by the injected ``spawn_fix``. ``usable=False`` means the agent could
    not produce a candidate fix at all (give up / no change) — that triggers a
    retry, distinct from a fix that was produced but the gate then human-gates.
    """

    changeset: Changeset
    reproduced_before: bool
    reproduced_after: bool
    branch: Optional[str] = None
    new_file_contents: Optional[dict[str, str]] = None
    usable: bool = True
    notes: str = ""
    diff_text: str = ""           # the unified diff the panel reviews
    worktree: Optional[str] = None  # the throwaway worktree the fix was authored in


@dataclass
class FixOutcome:
    """The terminal result of running the harness on one ticket."""

    ticket_id: str
    status: str
    verdict: Optional[GateVerdict]
    attempts: int
    merged: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "status": self.status,
            "attempts": self.attempts,
            "merged": self.merged,
            "message": self.message,
            "verdict": self.verdict.to_dict() if self.verdict else None,
        }


# ── safe defaults for the side-effecting seams ─────────────────────────────────

def _refuse_merge(ticket: dict, attempt: FixAttempt, verdict: GateVerdict) -> None:
    raise RuntimeError(
        "bug_squash.fixer: a real merge was requested but no merge() was wired. "
        "Refusing — this harness never merges on its own. Wire FixerDeps.merge in "
        "the agent-system integration to perform the actual gated merge."
    )


def _log(prefix: str):
    def _fn(*args, **kwargs) -> None:
        # default observer: silent no-op (callers inject real notify/status writers)
        return None

    _fn.__name__ = prefix
    return _fn


@dataclass
class FixerDeps:
    """The injected side effects. Only ``spawn_fix`` is mandatory."""

    spawn_fix: Callable[[dict, int], FixAttempt]
    merge: Callable[[dict, FixAttempt, GateVerdict], None] = _refuse_merge
    set_status: Callable[[str, str, dict], None] = field(default_factory=lambda: _log("set_status"))
    escalate: Callable[[dict, GateVerdict], None] = field(default_factory=lambda: _log("escalate"))
    request_tap: Callable[[dict, GateVerdict], None] = field(default_factory=lambda: _log("request_tap"))
    notify_shadow: Callable[[dict, GateVerdict], None] = field(default_factory=lambda: _log("notify_shadow"))


def run_fix(
    ticket: dict,
    deps: FixerDeps,
    *,
    posture: Optional[config_mod.Posture] = None,
    project: Optional["config_mod.ProjectConfig"] = None,
    rate_limiter: Optional[RateLimiter] = None,
    max_attempts: int = MAX_ATTEMPTS,
) -> FixOutcome:
    """Drive one ticket through the gated fix and act on the verdict.

    ``ticket`` is a bug_tickets row dict (needs at least ``id``, ``severity``,
    ``risk_tier``, ``target_repo``). The gate is the single arbiter of whether an
    autonomous merge happens; this function only routes its decision to the wired
    side effects.
    """
    posture = posture or config_mod.load()
    rate_limiter = rate_limiter or RateLimiter(posture.state_dir)

    ticket_id = str(ticket.get("id"))
    severity = ticket.get("severity") or "p2"
    tier = ticket.get("risk_tier") or gate_mod.HUMAN_GATE_TIER

    attempt: Optional[FixAttempt] = None
    attempts_made = 0
    for n in range(1, max_attempts + 1):
        attempts_made = n
        candidate = deps.spawn_fix(ticket, n)
        if candidate is not None and candidate.usable and not candidate.changeset.is_empty:
            attempt = candidate
            break
        # not usable / empty → retry until the cap, then escalate below

    if attempt is None:
        # the agent could not produce a usable fix within the attempt cap.
        deps.set_status(ticket_id, ST_FAILED, {"attempts": attempts_made})
        deps.escalate(ticket, None)  # type: ignore[arg-type]
        return FixOutcome(
            ticket_id=ticket_id,
            status=ST_FAILED,
            verdict=None,
            attempts=attempts_made,
            message=f"no usable fix after {attempts_made} attempt(s) — escalated (attempt cap)",
        )

    verdict = gate_mod.evaluate(
        attempt.changeset,
        tier=tier,
        severity=severity,
        reproduced_before=attempt.reproduced_before,
        reproduced_after=attempt.reproduced_after,
        posture=posture,
        project=project,
        rate_limiter=rate_limiter,
        new_file_contents=attempt.new_file_contents,
    )

    return _apply(ticket_id, ticket, attempt, verdict, deps, attempts_made)


def _apply(
    ticket_id: str,
    ticket: dict,
    attempt: FixAttempt,
    verdict: GateVerdict,
    deps: FixerDeps,
    attempts: int,
) -> FixOutcome:
    """Route a GateVerdict to the wired side effects and produce a FixOutcome."""
    common = {"branch": attempt.branch, "attempts": attempts, "gate": verdict.headline()}

    if verdict.decision == gate_mod.AUTO_MERGE:
        # the gate already consumed a rate-limit unit; now do the actual merge.
        deps.merge(ticket, attempt, verdict)
        deps.set_status(ticket_id, ST_MERGED, common)
        return FixOutcome(ticket_id, ST_MERGED, verdict, attempts, merged=True,
                          message="auto-merged (gate authorized)")

    if verdict.decision == gate_mod.SHADOW:
        # would have merged — record + leave for you to compare. Do NOT merge.
        deps.notify_shadow(ticket, verdict)
        deps.set_status(ticket_id, ST_IN_REVIEW, {**common, "shadow": True})
        return FixOutcome(ticket_id, ST_IN_REVIEW, verdict, attempts,
                          message="SHADOW: fix would auto-merge; recorded, not merged")

    if verdict.decision == gate_mod.HUMAN_GATE:
        deps.request_tap(ticket, verdict)
        deps.set_status(ticket_id, ST_IN_REVIEW, common)
        return FixOutcome(ticket_id, ST_IN_REVIEW, verdict, attempts,
                          message="human-gate: fix on branch, awaiting a human's tap")

    if verdict.decision == gate_mod.BLOCK:
        deps.escalate(ticket, verdict)
        deps.set_status(ticket_id, ST_ESCALATED, {**common, "denied": list(verdict.denied_files)})
        return FixOutcome(ticket_id, ST_ESCALATED, verdict, attempts,
                          message="BLOCKED by §3.4 deny-list — diagnosis only, escalated")

    # NO_CHANGES (empty diff slipped past the usable check) → escalate.
    deps.escalate(ticket, verdict)
    deps.set_status(ticket_id, ST_FAILED, common)
    return FixOutcome(ticket_id, ST_FAILED, verdict, attempts,
                      message="no changes produced — escalated")
