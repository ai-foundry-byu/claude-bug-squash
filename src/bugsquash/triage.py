"""bugsquash.triage — classify + locate + risk-tier a captured bug ticket.

Given a ``captured`` ticket, this:
  1. classifies severity (claude-CLI classifier with a deterministic heuristic
     fallback),
  2. ATTEMPTS to reproduce (runs the configured ``repro_command`` if set; exit 0
     == reproduces). No command → unverified (surfaced, never silently "fixed").
  3. locates likely affected files (ripgrep/grep over the repo for ticket terms),
  4. assigns ``risk_tier`` (deny-list match → never-auto; high severity / wide
     blast radius / unverified → human-gate; narrow + low-severity + reproduced →
     auto-safe — the gate, panel, shadow-mode, and rate-limit are still the real
     arbiters downstream),
  5. writes the verdict back to the ticket.

NO FIXING. This is intake + triage only. The fix-writing happens in ``spawn.py``.

CLI:
    python -m bugsquash.triage <ticket_id>
    python -m bugsquash.triage --drain [--limit 10]
    python -m bugsquash.triage <ticket_id> --no-llm
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from . import config as config_mod
from . import deny_list
from . import store

P0_HINTS = ("crash", "down", "broken", "500", "data loss", "can't log in", "unusable", "blocker")
P1_HINTS = ("error", "fails", "wrong", "missing", "regression", "exception", "throws")
COSMETIC_HINTS = ("typo", "label", "color", "colour", "spacing", "alignment", "wording", "copy")


# ── 1. classification ────────────────────────────────────────────────────────
def _classify_heuristic(ticket: dict) -> str:
    blob = " ".join(str(ticket.get(k) or "") for k in ("title", "body")).lower()
    if any(h in blob for h in P0_HINTS):
        return "p0"
    if any(h in blob for h in COSMETIC_HINTS):
        return "p3"
    if any(h in blob for h in P1_HINTS):
        return "p1"
    return "p2"


def _claude_cli(prompt: str, *, model: str = "haiku", timeout: int = 90) -> Optional[str]:
    """One-shot ``claude -p`` text call. Returns stdout or None on any failure."""
    if not shutil.which("claude"):
        return None
    env = dict(os.environ)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS"):
        if not env.get(var, "").startswith("sk-ant-api"):
            env.pop(var, None)
    try:
        cp = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        return cp.stdout or None
    except Exception:
        return None


def _classify_llm(ticket: dict) -> Optional[str]:
    ctx = ticket.get("context") or {}
    prompt = (
        "You are a bug-triage severity classifier. Severity scale: p0 (system "
        "down / data loss / unusable), p1 (a real feature is broken or wrong), "
        "p2 (degraded but workable), p3 (cosmetic / copy / minor).\n\n"
        f"TITLE: {ticket.get('title')}\n"
        f"BODY: {ticket.get('body')}\n"
        f"CONTEXT: {json.dumps(ctx)[:1500]}\n\n"
        'Reply with ONLY a JSON object: {"severity": "p0"|"p1"|"p2"|"p3"}'
    )
    raw = _claude_cli(prompt)
    if not raw:
        return None
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        sev = json.loads(m.group(0)).get("severity")
    except Exception:
        return None
    return sev if sev in store.VALID_SEVERITY else None


# ── 2. reproduce ─────────────────────────────────────────────────────────────
def _reproduce(ticket: dict, project: config_mod.ProjectConfig) -> tuple[Optional[bool], str]:
    """Run the configured repro command. Returns (reproduced, notes).

    reproduced = True  → the command exited non-zero / matched (bug confirmed).
    reproduced = False → command ran clean (could NOT confirm the bug).
    reproduced = None  → no repro command configured → unverified, surfaced.

    Convention: the repro command should FAIL (non-zero exit) when the bug is
    present and PASS (exit 0) when it is gone — the same red→green a test gives.
    """
    cmd = (project.fix.repro_command or "").strip()
    if not cmd:
        return None, ("no repro_command configured (bugsquash.toml [fix]) — bug "
                      "unverified; located candidate files for a human / the fixer "
                      "to confirm.")
    try:
        cp = subprocess.run(cmd, shell=True, cwd=str(project.repo_root),
                            capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return None, f"repro_command timed out: {cmd!r}"
    except Exception as e:  # noqa: BLE001
        return None, f"repro_command errored ({e}): {cmd!r}"
    if cp.returncode != 0:
        return True, (f"repro_command FAILED (exit {cp.returncode}) → bug reproduces. "
                      f"cmd={cmd!r}")
    return False, (f"repro_command passed (exit 0) → could not reproduce. cmd={cmd!r}")


# ── 3. locate affected files ─────────────────────────────────────────────────
_EXCLUDE = ("/node_modules/", "/.next/", "/__pycache__/", "/.git/", "/dist/",
            "/build/", "/.bugsquash/", "/vendor/", "/target/")


def _grep_cmd(term: str, root: str) -> list[str]:
    rg = shutil.which("rg")
    if rg:
        return [rg, "-l", "--max-count", "1", "-i", "-S",
                "-g", "!node_modules", "-g", "!.next", "-g", "!__pycache__",
                "-g", "!.git", "-g", "!dist", "-g", "!build", "-g", "!.bugsquash",
                term, root]
    return ["grep", "-r", "-l", "-i", "-F", "-e", term,
            "--exclude-dir=node_modules", "--exclude-dir=.next",
            "--exclude-dir=__pycache__", "--exclude-dir=.git",
            "--exclude-dir=dist", "--exclude-dir=build", "--exclude-dir=.bugsquash",
            root]


def _search_terms(ticket: dict) -> list[str]:
    terms: list[str] = []
    ctx = ticket.get("context") or {}
    route = ctx.get("route") or ctx.get("url")
    if route:
        terms.extend([s for s in str(route).strip("/").split("/") if s and not s.startswith("?")][:2])
    blob = " ".join(str(ticket.get(k) or "") for k in ("title", "body"))
    terms += re.findall(r"\b([\w./-]+\.(?:tsx?|jsx?|py|sh|go|rs|rb|java|kt|swift|c|cpp|cs|sql))\b", blob)
    terms += re.findall(r"\b((?:[\w-]+/)+[\w-]+)\b", blob)
    terms += re.findall(r"\b([A-Z][a-z]+[A-Z][A-Za-z]+)\b", blob)
    terms += re.findall(r"\b([a-z][a-z0-9]+(?:[_-][a-z0-9]+)+)\b", blob)
    terms += re.findall(r'"([^"]{3,40})"', blob)
    stop = {"the", "and", "but", "not", "for", "with", "that", "this",
            "instead", "shows", "should", "wrong", "past"}
    seen, out = set(), []
    for t in terms:
        t = t.strip()
        low = t.lower()
        if len(t) < 4 or low in seen or low in stop:
            continue
        seen.add(low)
        out.append(t)
    return out[:10]


def _locate_files(ticket: dict, project: config_mod.ProjectConfig, limit: int = 12) -> list[str]:
    root = project.repo_root
    search_root = (root / "src") if (root / "src").is_dir() else root
    terms = _search_terms(ticket)
    hits: dict[str, int] = {}

    def _bump(path: str, weight: int) -> None:
        path = path.strip()
        if not path or any(x in path for x in _EXCLUDE):
            return
        rel = os.path.relpath(path, root) if os.path.isabs(path) else path
        hits[rel] = hits.get(rel, 0) + weight

    for term in terms:
        try:
            found = subprocess.run(
                ["find", str(search_root),
                 "(", "-name", "node_modules", "-o", "-name", ".next",
                 "-o", "-name", "__pycache__", "-o", "-name", ".git",
                 "-o", "-name", "dist", "-o", "-name", "build", ")", "-prune", "-o",
                 "-ipath", f"*{term}*", "-type", "f", "-print"],
                capture_output=True, text=True, timeout=20,
            )
            for line in found.stdout.splitlines()[:20]:
                _bump(line, 5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        try:
            out = subprocess.run(_grep_cmd(term, str(search_root)),
                                 capture_output=True, text=True, timeout=25)
            w = 1 if ("/" not in term and "." not in term and len(term) < 8) else 2
            for line in out.stdout.splitlines():
                _bump(line, w)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            break

    ctx = ticket.get("context") or {}
    for f in (ctx.get("affected_files") or ctx.get("file") or []):
        if isinstance(f, str):
            hits[os.path.relpath(f, root) if os.path.isabs(f) else f] = 99

    ranked = sorted(hits.items(), key=lambda kv: kv[1], reverse=True)
    return [p for p, _ in ranked[:limit]]


# ── 4. risk tier ─────────────────────────────────────────────────────────────
def _risk_tier(
    severity: str, affected_files: list[str], reproduced: Optional[bool],
    project: config_mod.ProjectConfig,
) -> tuple[str, str]:
    denied = deny_list.first_denied(affected_files, project.deny_globs)
    if denied:
        return "never-auto", f"affected file matches the NEVER-autonomous deny-list: {denied}"
    if severity not in project.limits.auto_severities:
        return "human-gate", f"{severity} severity — high stakes always gate to a human"
    if reproduced is not True:
        return "human-gate", "could not auto-confirm reproduction — a human decides"
    if len(affected_files) > project.limits.max_files:
        return "human-gate", (f"{len(affected_files)} candidate files — blast radius "
                              f"exceeds the auto threshold ({project.limits.max_files})")
    return "auto-safe", ("low-severity, reproduced, narrow scope, no deny-list match — "
                         "eligible for the autonomous tier (gate + panel + shadow/rate "
                         "limit still apply downstream).")


# ── orchestration ────────────────────────────────────────────────────────────
def triage_ticket(ticket_id: str, *, use_llm: bool = True,
                  project: Optional[config_mod.ProjectConfig] = None) -> dict:
    project = project or config_mod.load_project()
    ticket = store.get_ticket(ticket_id)
    if not ticket:
        raise store.BugStoreError(f"no ticket {ticket_id}")

    sev = (_classify_llm(ticket) if use_llm else None) or _classify_heuristic(ticket)
    reasoning = "claude classification" if use_llm and _claude_cli else "heuristic classification"

    affected = _locate_files(ticket, project)
    reproduced, repro_notes = _reproduce(ticket, project)
    tier, tier_reason = _risk_tier(sev, affected, reproduced, project)

    if tier == "never-auto":
        status = "escalated"
    elif reproduced is False:
        status = "needs_info"
    else:
        status = "triaged"

    triage_notes = (f"[classify] {reasoning} → {sev}\n"
                    f"[reproduce] {repro_notes}\n"
                    f"[risk] {tier_reason}")
    fields = {
        "target_repo": project.repo_label,
        "severity": sev,
        "risk_tier": tier,
        "repro": repro_notes,
        "reproduced": reproduced,
        "affected_files": affected,
        "triage_notes": triage_notes,
        "status": status,
    }
    return store.update_ticket(ticket_id, fields)


def drain(limit: int = 20, *, use_llm: bool = True,
          project: Optional[config_mod.ProjectConfig] = None) -> list[dict]:
    project = project or config_mod.load_project()
    captured = store.list_tickets(status="captured", limit=limit)
    out = []
    for t in captured:
        try:
            out.append(triage_ticket(t["id"], use_llm=use_llm, project=project))
        except Exception as e:  # one bad ticket can't stall the drain
            print(f"  triage error on {t['id']}: {e}", file=sys.stderr)
    return out


def _fmt(t: dict) -> str:
    return (f"{t['id'][:8]}  status={t.get('status')}  sev={t.get('severity')}  "
            f"tier={t.get('risk_tier')}  repro={t.get('reproduced')}  "
            f"files={len(t.get('affected_files') or [])}")


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="bugsquash.triage")
    p.add_argument("ticket_id", nargs="?")
    p.add_argument("--drain", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--no-llm", action="store_true")
    args = p.parse_args(argv)
    use_llm = not args.no_llm
    try:
        if args.drain:
            res = drain(limit=args.limit, use_llm=use_llm)
            for t in res:
                print(_fmt(t))
            print(f"\ntriaged {len(res)} ticket(s)")
        elif args.ticket_id:
            t = triage_ticket(args.ticket_id, use_llm=use_llm)
            print(_fmt(t))
            print("\n" + (t.get("triage_notes") or ""))
        else:
            p.error("pass a ticket_id or --drain")
        return 0
    except store.BugStoreError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
