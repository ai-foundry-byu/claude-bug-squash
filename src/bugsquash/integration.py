"""bugsquash.integration — wire the fixer harness to real side effects.

``fixer.py`` ships inert: ``merge`` hard-refuses and the notify/status seams are
silent no-ops. This module wires real implementations into ``FixerDeps``:

  1. **A notification per squashed bug** — through the pluggable notifier
     (``notify.py``). The message is the "reported / found / done" brief, plus the
     gate + panel outcome.
  2. **The adversarial REVIEW PANEL before any merge** — even after the structural
     diff gate (``gate.py``) says AUTO_MERGE, the change is run past the panel
     (``review_panel.py``). A single hard veto downgrades the merge to human-gate.
     No auto-fix reaches the trunk without passing BOTH gates.

The literal ``git merge`` stays behind a DOUBLE arm switch:

    AUTO_SQUASH_ENABLED=1        (kill switch — config.Posture)
    AUTO_SQUASH_SHADOW=0         (shadow off — config.Posture)
    AUTO_SQUASH_MERGE_ARMED=1    (this module — even with gate green + panel
                                 approved, the literal merge only runs when this is
                                 set. Default OFF → DRY-RUN: log + notify "WOULD
                                 merge", touch nothing.)

So the out-of-the-box posture is: triage + fix + structural-gate + panel + notify,
but NO real merge. You flip ``AUTO_SQUASH_MERGE_ARMED=1`` only after a clean shadow
record. ``merge_fn`` is also injected — even armed + approved, the actual git merge
only runs when you supply a real merger (default: refuse), so nothing merges by
accident before you wire it.

Tests inject ``notify_fn`` / ``panel_fn`` / ``merge_fn`` so nothing here ever sends
a real notification, spawns a real reviewer, or runs a real merge.
"""
from __future__ import annotations

import os
from typing import Callable, Optional

from . import config as config_mod
from . import gate as gate_mod
from . import notify as notify_mod
from . import review_panel
from .fixer import FixAttempt, FixerDeps
from .gate import GateVerdict

ENV_MERGE_ARMED = config_mod.ENV_MERGE_ARMED

NotifyFn = Callable[[str], bool]
PanelFn = Callable[..., review_panel.PanelVerdict]
MergeFn = Callable[[dict, FixAttempt, GateVerdict], dict]


def _truthy(v: str) -> bool:
    return (v or "").strip().lower() not in ("0", "false", "no", "off", "")


def merge_armed(env: Optional[dict] = None) -> bool:
    """The literal-merge arm switch. OFF by default → dry-run."""
    env = os.environ if env is None else env
    return _truthy(env.get(ENV_MERGE_ARMED, "0"))


# ── message rendering (the "reported / found / done" brief) ────────────────────
def _short(s: str, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _ticket_line(ticket: dict) -> str:
    repo = ticket.get("target_repo") or ticket.get("repo") or "?"
    sev = (ticket.get("severity") or "?").upper()
    tid = str(ticket.get("id") or "")[:8]
    title = _short(ticket.get("title") or "(no title)", 90)
    return f"🐛 {title}\n   {repo} · {sev} · #{tid}"


def render_panel_lines(panel: Optional[review_panel.PanelVerdict]) -> list[str]:
    if panel is None:
        return ["Panel: (not run — fix not merge-eligible)"]
    lines = [f"Panel: {panel.headline()}"]
    for s in panel.blockers:
        lines.append(f"   ⛔ {s.seat.upper()} vetoed: {_short(s.reasons, 160)}")
    for s in panel.soft_concerns:
        lines.append(f"   ⚠️ {s.seat.upper()} concern (uncited, not vetoing): "
                     f"{_short(s.reasons, 120)}")
    return lines


def render_notification(
    ticket: dict,
    attempt: FixAttempt,
    verdict: GateVerdict,
    panel: Optional[review_panel.PanelVerdict],
    *,
    outcome: str,
    dry_run: bool = False,
) -> str:
    """The brief emitted per squashed bug.

    ``outcome`` ∈ {merged, would_merge, human_gate, blocked, escalated}.
    """
    head = {
        "merged": "✅ Auto-squash MERGED",
        "would_merge": "🟡 Auto-squash WOULD merge (dry-run — merge not armed)",
        "human_gate": "🙋 Auto-squash needs a human tap",
        "blocked": "⛔ Auto-squash BLOCKED — diagnosis only",
        "escalated": "⚠️ Auto-squash escalated",
    }.get(outcome, "Auto-squash update")

    cs = verdict.changeset or {}
    body = ticket.get("body") or ticket.get("title") or ""
    lines = [head, "", _ticket_line(ticket), ""]
    lines.append(f"Reported: {_short(body, 200)}")
    if attempt.notes:
        lines.append(f"Done: {_short(attempt.notes, 200)}")
    lines.append(
        f"Diff: {cs.get('file_count', '?')} file(s), {cs.get('churn', '?')} lines"
        f"  ·  repro {'✓' if attempt.reproduced_before else '?'}→"
        f"{'✓' if attempt.reproduced_after else '✗'}"
    )
    lines.append(f"Gate: {verdict.headline()}")
    lines += render_panel_lines(panel)
    if attempt.branch:
        lines.append(f"Branch: {attempt.branch}")
    if outcome == "would_merge":
        lines.append("\nArm AUTO_SQUASH_MERGE_ARMED=1 to let it merge, "
                     "or review the branch.")
    elif outcome == "human_gate":
        lines.append("\nReview the branch; merge if you're happy.")
    return "\n".join(lines)


# ── the panel-gated merge (the load-bearing safety step) ───────────────────────
def panel_gated_merge(
    ticket: dict,
    attempt: FixAttempt,
    verdict: GateVerdict,
    *,
    panel_fn: Optional[PanelFn] = None,
    notify_fn: Optional[NotifyFn] = None,
    merge_fn: Optional[MergeFn] = None,
    diff_text: str = "",
    cwd: Optional[str] = None,
    agents_dir=None,
    panel_seats: Optional[tuple] = None,
    panel_model: Optional[str] = None,
    armed: Optional[bool] = None,
    env: Optional[dict] = None,
) -> dict:
    """Run the review panel, then merge IFF approved AND armed.

    Called from the wired ``FixerDeps.merge`` seam, i.e. only when the structural
    gate already returned AUTO_MERGE. Returns a result dict. NEVER raises — a
    panel/merge error downgrades to a human-gate notification, never a silent
    merge.

    Posture (all must hold for a REAL merge):
        * structural gate → AUTO_MERGE   (guaranteed by the caller)
        * panel.approved  (no hard veto)
        * merge_armed()   (AUTO_SQUASH_MERGE_ARMED=1)
    Anything short → dry-run / human-gate + a notification.
    """
    notify_fn = notify_fn or notify_mod.make_notifier()
    merge_fn = merge_fn or _refuse_real_merge

    # 1. The review gate — even an AUTO_MERGE-eligible fix faces the panel.
    if panel_fn is None:
        def panel_fn(**kw):  # noqa: ANN001 — bind the real panel with repo context
            kw.setdefault("cwd", cwd)
            kw.setdefault("agents_dir", agents_dir)
            if panel_seats:
                kw.setdefault("seats", panel_seats)
            if panel_model:
                kw.setdefault("model", panel_model)
            return review_panel.review(**kw)

    try:
        panel = panel_fn(ticket=ticket, diff=diff_text)
    except Exception as e:  # noqa: BLE001 — gate failure must not silently merge
        msg = render_notification(ticket, attempt, verdict, None, outcome="human_gate")
        msg += f"\n\n(panel run errored: {e} — withholding merge, needs a human tap)"
        notify_fn(msg)
        return {"merged": False, "outcome": "human_gate", "panel": None,
                "reason": f"panel error: {e}"}

    if not panel.approved:
        msg = render_notification(ticket, attempt, verdict, panel, outcome="human_gate")
        notify_fn(msg)
        return {"merged": False, "outcome": "human_gate",
                "panel": panel.to_dict(),
                "reason": "panel veto: " + ",".join(s.seat for s in panel.blockers)}

    # 2. Panel approved. The merge still only RUNS when explicitly armed.
    is_armed = armed if armed is not None else merge_armed(env)
    if not is_armed:
        msg = render_notification(ticket, attempt, verdict, panel,
                                  outcome="would_merge", dry_run=True)
        notify_fn(msg)
        return {"merged": False, "outcome": "would_merge",
                "panel": panel.to_dict(), "reason": "merge not armed (dry-run)"}

    # 3. Armed + approved + gate-green → the real merge.
    try:
        merge_res = merge_fn(ticket, attempt, verdict)
    except Exception as e:  # noqa: BLE001
        msg = render_notification(ticket, attempt, verdict, panel, outcome="escalated")
        msg += f"\n\n(merge FAILED: {e} — left on the branch, needs a human)"
        notify_fn(msg)
        return {"merged": False, "outcome": "escalated", "panel": panel.to_dict(),
                "reason": f"merge error: {e}"}

    msg = render_notification(ticket, attempt, verdict, panel, outcome="merged")
    notify_fn(msg)
    return {"merged": True, "outcome": "merged", "panel": panel.to_dict(),
            "merge": merge_res}


def _refuse_real_merge(ticket: dict, attempt: FixAttempt, verdict: GateVerdict) -> dict:
    """Default real-merge implementation: refuse.

    Wiring an ACTUAL ``git merge`` is a deliberate, separate step. ``git_merge.py``
    supplies a real merger; until one is wired, even an armed + approved fix raises
    here so nothing merges by accident.
    """
    raise RuntimeError(
        "bugsquash.integration: AUTO_SQUASH_MERGE_ARMED=1 and the panel approved, "
        "but no real merge_fn is wired. Refusing to merge. Supply merge_fn= to "
        "build_deps() (a function that does the actual gated git merge) when you "
        "intend real autonomous merges."
    )


# ── building the wired FixerDeps ───────────────────────────────────────────────
def build_deps(
    spawn_fix: Callable[[dict, int], FixAttempt],
    *,
    project: Optional[config_mod.ProjectConfig] = None,
    armed: Optional[bool] = None,
    notify_fn: Optional[NotifyFn] = None,
    panel_fn: Optional[PanelFn] = None,
    merge_fn: Optional[MergeFn] = None,
    set_status_fn: Optional[Callable[[str, str, dict], None]] = None,
    diff_for: Optional[Callable[[FixAttempt], str]] = None,
) -> FixerDeps:
    """Build a fully-wired ``FixerDeps`` for ``fixer.run_fix``.

    Wires:
      * merge          → panel-gated merge (review gate + arm-switched real merge)
      * escalate       → notify (BLOCK / NO_CHANGES / failed)
      * request_tap    → notify (HUMAN_GATE — fix on a branch awaiting a tap)
      * notify_shadow  → notify (SHADOW — what it WOULD have merged)
      * set_status     → store.update_ticket (or the injected writer)
    """
    notify_fn = notify_fn or notify_mod.make_notifier()
    diff_for = diff_for or _default_diff_for
    set_status_fn = set_status_fn or _default_set_status

    cwd = str(project.repo_root) if project else None
    agents_dir = project.panel.agents_dir if project else None
    panel_seats = project.panel.seats if project else None
    panel_model = project.panel.model if project else None

    def _merge(ticket: dict, attempt: FixAttempt, verdict: GateVerdict) -> None:
        panel_gated_merge(
            ticket, attempt, verdict,
            panel_fn=panel_fn, notify_fn=notify_fn, merge_fn=merge_fn,
            diff_text=diff_for(attempt), cwd=cwd, agents_dir=agents_dir,
            panel_seats=panel_seats, panel_model=panel_model, armed=armed,
        )

    def _escalate(ticket: dict, verdict: Optional[GateVerdict]) -> None:
        if verdict is None:
            notify_fn(
                f"⚠️ Auto-squash escalated\n\n{_ticket_line(ticket)}\n"
                f"No usable fix after the attempt cap — needs a human."
            )
            return
        outcome = "blocked" if verdict.decision == gate_mod.BLOCK else "escalated"
        stub = FixAttempt(changeset=_stub_cs(verdict),
                          reproduced_before=False, reproduced_after=False)
        notify_fn(render_notification(ticket, stub, verdict, None, outcome=outcome))

    def _request_tap(ticket: dict, verdict: GateVerdict) -> None:
        stub = FixAttempt(changeset=_stub_cs(verdict),
                          reproduced_before=False, reproduced_after=False)
        notify_fn(render_notification(ticket, stub, verdict, None, outcome="human_gate"))

    def _notify_shadow(ticket: dict, verdict: GateVerdict) -> None:
        cs = verdict.changeset or {}
        notify_fn(
            f"👻 Auto-squash SHADOW (would merge, not merging)\n\n"
            f"{_ticket_line(ticket)}\n"
            f"Diff: {cs.get('file_count','?')} file(s), {cs.get('churn','?')} lines\n"
            f"Gate: {verdict.headline()}\n"
            f"Flip AUTO_SQUASH_SHADOW=0 after a clean shadow record to go live."
        )

    return FixerDeps(
        spawn_fix=spawn_fix,
        merge=_merge,
        set_status=set_status_fn,
        escalate=_escalate,
        request_tap=_request_tap,
        notify_shadow=_notify_shadow,
    )


def _stub_cs(verdict: GateVerdict):
    from .diff import Changeset
    return Changeset(repo=verdict.repo)


def _default_diff_for(attempt: FixAttempt) -> str:
    """The panel reviews the diff the gate gated on; the spawn step attaches it to
    the attempt as ``notes``/branch. If unavailable, the panel still runs on the
    bug + changeset summary."""
    return getattr(attempt, "diff_text", "") or ""


def _default_set_status(ticket_id: str, status: str, extra: dict) -> None:
    """Write ticket status back through the local store. Best-effort."""
    try:
        from . import store
        store.update_ticket(ticket_id, {"status": status, **{k: v for k, v in extra.items()
                                                              if k in ("branch", "attempts")}})
    except Exception:
        pass
