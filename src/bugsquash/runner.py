"""bugsquash.runner — end-to-end orchestration: triage → fix → gate → panel → act.

This is what ``bug-squash run`` calls. It wires the real fix-writer
(``spawn.make_spawn_fix``) and side effects (``integration.build_deps``) and drives
each eligible ticket through ``fixer.run_fix``. The autonomy posture (shadow / arm
switch / rate limit) and the gate/panel are the real arbiters — the runner just
sequences the work and reports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import config as config_mod
from . import fixer as fixer_mod
from . import integration
from . import spawn as spawn_mod
from . import store
from . import triage as triage_mod
from .ratelimit import RateLimiter


@dataclass
class RunReport:
    triaged: int = 0
    attempted: int = 0
    outcomes: Optional[list] = None

    def to_dict(self) -> dict:
        return {"triaged": self.triaged, "attempted": self.attempted,
                "outcomes": [o.to_dict() for o in (self.outcomes or [])]}


def _eligible(ticket: dict) -> bool:
    """A ticket the fixer should attempt: triaged, not a never-auto/escalated stop."""
    return (ticket.get("status") == "triaged"
            and ticket.get("risk_tier") in ("auto-safe", "human-gate"))


def run(
    *,
    ticket_id: Optional[str] = None,
    limit: int = 20,
    triage_first: bool = True,
    use_llm: bool = True,
    enable_merge: bool = False,
    settings: Optional[config_mod.Settings] = None,
) -> RunReport:
    """Run the squash pipeline.

    ``ticket_id`` — run one ticket; else drain up to ``limit`` triaged tickets.
    ``triage_first`` — triage any captured tickets before fixing.
    ``enable_merge`` — wire the real git merger (still gated by AUTO_SQUASH_MERGE_
                       ARMED + panel + gate). Default False → dry-run merges.
    """
    settings = settings or config_mod.load_all()
    posture, project = settings.posture, settings.project

    report = RunReport(outcomes=[])

    if triage_first:
        triaged = triage_mod.drain(limit=limit, use_llm=use_llm, project=project)
        report.triaged = len(triaged)

    if ticket_id:
        tickets = [t for t in [store.get_ticket(ticket_id)] if t]
    else:
        tickets = [t for t in store.list_tickets(status="triaged", limit=limit) if _eligible(t)]

    spawn_fix = spawn_mod.make_spawn_fix(project)
    merge_fn = None
    if enable_merge:
        from . import git_merge
        merge_fn = git_merge.make_merge(project)
    deps = integration.build_deps(spawn_fix, project=project, merge_fn=merge_fn,
                                  armed=posture.merge_armed)

    rate_limiter = RateLimiter(posture.state_dir)
    for t in tickets:
        store.update_ticket(t["id"], {"status": "fixing"})
        outcome = fixer_mod.run_fix(t, deps, posture=posture, project=project,
                                    rate_limiter=rate_limiter)
        report.outcomes.append(outcome)
    report.attempted = len(tickets)
    return report
