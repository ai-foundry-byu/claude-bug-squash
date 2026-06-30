"""bugsquash.cli — the ``bug-squash`` command-line entrypoint.

    bug-squash init [--no-introspect] [--force]   set up bugsquash.toml + reviewers
    bug-squash add "title" [--body ...] [--sev p2] [--file path]   capture a bug
    bug-squash triage [<id> | --all]              classify + locate + risk-tier
    bug-squash run [<id>] [--no-triage] [--merge]  triage → fix → gate → panel → act
    bug-squash status [--status triaged]          list tickets
    bug-squash posture                            show the current autonomy posture
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__, config as config_mod
from . import init as init_mod
from . import store
from . import triage as triage_mod


def _cmd_init(args) -> int:
    res = init_mod.init_repo(introspect=not args.no_introspect, force=args.force)
    print(f"✓ bug-squash ready in {res['repo_root']}")
    print(f"  config: {res['config']}"
          + ("  (repo-introspected)" if res["introspected"] else "  (generic defaults)"))
    for w in res["written"]:
        print(f"  wrote: {w}")
    print("\nNext: `bug-squash add \"<bug>\"` then `bug-squash run`. "
          "Auto-merge stays OFF until you flip AUTO_SQUASH_SHADOW=0 + "
          "AUTO_SQUASH_MERGE_ARMED=1 (see README).")
    return 0


def _cmd_add(args) -> int:
    body = args.body or ""
    context = {}
    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"ERROR: no such file {args.file}", file=sys.stderr)
            return 1
        body = (body + "\n\n" + p.read_text(errors="ignore"))[:8000]
        context["source_file"] = str(p)
    if args.json_context:
        try:
            context.update(json.loads(args.json_context))
        except Exception as e:
            print(f"ERROR: bad --context JSON: {e}", file=sys.stderr)
            return 1
    row = store.create_ticket(source=args.source, title=args.title, body=body or None,
                              context=context or None)
    print(f"captured {row['id'][:8]}  {row['title']}")
    return 0


def _cmd_triage(args) -> int:
    project = config_mod.load_project()
    if args.all or not args.id:
        res = triage_mod.drain(limit=args.limit, use_llm=not args.no_llm, project=project)
        for t in res:
            print(triage_mod._fmt(t))
        print(f"\ntriaged {len(res)} ticket(s)")
    else:
        t = triage_mod.triage_ticket(args.id, use_llm=not args.no_llm, project=project)
        print(triage_mod._fmt(t))
        print("\n" + (t.get("triage_notes") or ""))
    return 0


def _cmd_run(args) -> int:
    from . import runner
    rep = runner.run(
        ticket_id=args.id,
        limit=args.limit,
        triage_first=not args.no_triage,
        use_llm=not args.no_llm,
        enable_merge=args.merge,
    )
    print(f"triaged {rep.triaged}, attempted {rep.attempted}")
    for o in (rep.outcomes or []):
        print(f"  {o.ticket_id[:8]}  {o.status:>10}  "
              f"{'MERGED' if o.merged else ''}  {o.message}")
    return 0


def _cmd_status(args) -> int:
    rows = store.list_tickets(status=args.status, limit=args.limit)
    for r in rows:
        print(f"{r['id'][:8]}  [{(r.get('status') or ''):>13}] "
              f"{(r.get('severity') or '--'):>2}/{(r.get('risk_tier') or '--')}  "
              f"{(r.get('title') or '')[:60]}")
    print(f"\n{len(rows)} ticket(s)")
    return 0


def _cmd_posture(args) -> int:
    s = config_mod.load_all()
    print(f"posture: {s.posture.describe()}")
    print(f"project: {s.project.describe()}")
    print(f"state:   {s.posture.state_dir}")
    if s.project.config_path:
        print(f"config:  {s.project.config_path}")
    else:
        print("config:  (none found — using built-in safe defaults; run `bug-squash init`)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bug-squash",
                                description="Autonomous, gated bug-squashing for any repo.")
    p.add_argument("--version", action="version", version=f"bug-squash {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="set up bugsquash.toml + review agents")
    pi.add_argument("--no-introspect", action="store_true",
                    help="skip the claude repo-introspection; write generic defaults")
    pi.add_argument("--force", action="store_true", help="overwrite existing files")
    pi.set_defaults(fn=_cmd_init)

    pa = sub.add_parser("add", help="capture a bug ticket")
    pa.add_argument("title")
    pa.add_argument("--body", default="")
    pa.add_argument("--sev", dest="severity", default=None,
                    choices=("p0", "p1", "p2", "p3"), help="(optional hint; triage decides)")
    pa.add_argument("--file", default=None, help="attach a log/repro file as context")
    pa.add_argument("--source", default="cli")
    pa.add_argument("--context", dest="json_context", default="", help="extra JSON context")
    pa.set_defaults(fn=_cmd_add)

    pt = sub.add_parser("triage", help="triage captured tickets")
    pt.add_argument("id", nargs="?")
    pt.add_argument("--all", action="store_true")
    pt.add_argument("--limit", type=int, default=20)
    pt.add_argument("--no-llm", action="store_true")
    pt.set_defaults(fn=_cmd_triage)

    pr = sub.add_parser("run", help="triage → fix → gate → panel → act")
    pr.add_argument("id", nargs="?")
    pr.add_argument("--limit", type=int, default=20)
    pr.add_argument("--no-triage", action="store_true")
    pr.add_argument("--no-llm", action="store_true")
    pr.add_argument("--merge", action="store_true",
                    help="wire the real git merger (still gated by the arm switch + panel)")
    pr.set_defaults(fn=_cmd_run)

    ps = sub.add_parser("status", help="list tickets")
    ps.add_argument("--status", default=None)
    ps.add_argument("--limit", type=int, default=30)
    ps.set_defaults(fn=_cmd_status)

    pp = sub.add_parser("posture", help="show the autonomy posture + project config")
    pp.set_defaults(fn=_cmd_posture)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except store.BugStoreError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
