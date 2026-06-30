"""bugsquash.store — the local bug-ticket data layer (SQLite).

A single chokepoint every intake source + the triage/fix stages write through, so
the ticket contract lives in one place. Backed by a SQLite file (zero-setup, no
server, travels with the repo) under the state dir — by default
``<repo>/.bugsquash/state/tickets.db``. Override with ``BUGSQUASH_DB``.

CLI:
    python -m bugsquash.store list [--status captured] [--limit 20]
    python -m bugsquash.store get <id>
    python -m bugsquash.store create --source cli --title "..." --body "..."
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

VALID_SEVERITY = ("p0", "p1", "p2", "p3")
VALID_RISK_TIER = ("auto-safe", "human-gate", "never-auto")
VALID_STATUS = (
    "captured", "triaged", "needs_info", "fixing", "in_review",
    "merged", "prod_verified", "escalated", "wontfix", "failed",
)

# JSON-encoded columns
_JSON_COLS = ("context", "affected_files")
_BOOL_COLS = ("reproduced",)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id            TEXT PRIMARY KEY,
    source        TEXT,
    title         TEXT NOT NULL,
    body          TEXT,
    context       TEXT,
    status        TEXT NOT NULL DEFAULT 'captured',
    target_repo   TEXT,
    severity      TEXT,
    risk_tier     TEXT,
    repro         TEXT,
    reproduced    INTEGER,
    affected_files TEXT,
    triage_notes  TEXT,
    branch        TEXT,
    pr_url        TEXT,
    attempts      INTEGER DEFAULT 0,
    resolution    TEXT,
    reporter_note TEXT,
    resolved_at   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets(status);
"""


class BugStoreError(RuntimeError):
    """A ticket store operation failed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    """Resolve the SQLite path: ``BUGSQUASH_DB`` env, else the per-repo state dir.

    Uses ``config.load_all()`` so the posture is rooted at the git top-level —
    i.e. the DB defaults to ``<repo>/.bugsquash/state/tickets.db`` and travels with
    the repo, instead of a machine-global path that would leak tickets between
    repos. ``BUGSQUASH_DB`` / ``AUTO_SQUASH_STATE_DIR`` still override.
    """
    env = os.getenv("BUGSQUASH_DB")
    if env:
        return Path(env)
    from . import config
    return config.load_all().posture.state_dir / "tickets.db"


def _connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for col in _JSON_COLS:
        if d.get(col):
            try:
                d[col] = json.loads(d[col])
            except (TypeError, json.JSONDecodeError):
                pass
        elif col in d:
            d[col] = {} if col == "context" else []
    for col in _BOOL_COLS:
        if d.get(col) is not None:
            d[col] = bool(d[col])
    return d


def _encode(fields: dict) -> dict:
    out = dict(fields)
    for col in _JSON_COLS:
        if col in out and not isinstance(out[col], (str, type(None))):
            out[col] = json.dumps(out[col])
    for col in _BOOL_COLS:
        if col in out and out[col] is not None:
            out[col] = 1 if out[col] else 0
    return out


# ── Write paths ──────────────────────────────────────────────────────────────
def create_ticket(
    *,
    source: str = "cli",
    title: str,
    body: Optional[str] = None,
    context: Optional[dict] = None,
    status: str = "captured",
    db: Optional[Path] = None,
) -> dict:
    """Insert a new captured ticket. Returns the inserted row (with id)."""
    if not (title and title.strip()):
        raise BugStoreError("title is required and cannot be blank")
    if status not in VALID_STATUS:
        raise BugStoreError(f"invalid status {status!r}")
    tid = uuid.uuid4().hex
    now = _now()
    row = _encode({
        "id": tid,
        "source": source,
        "title": title.strip()[:500],
        "body": (body or "").strip() or None,
        "context": context or {},
        "status": status,
        "created_at": now,
        "updated_at": now,
    })
    cols = ", ".join(row.keys())
    ph = ", ".join("?" for _ in row)
    with _connect(db) as conn:
        conn.execute(f"INSERT INTO tickets ({cols}) VALUES ({ph})", list(row.values()))
    return get_ticket(tid, db=db) or {}


def update_ticket(ticket_id: str, fields: dict[str, Any], db: Optional[Path] = None) -> dict:
    """Patch an existing ticket. Validates the constrained columns before writing."""
    if not fields:
        raise BugStoreError("update_ticket called with no fields")
    checks = {
        "severity": VALID_SEVERITY,
        "risk_tier": VALID_RISK_TIER,
        "status": VALID_STATUS,
    }
    for key, allowed in checks.items():
        if key in fields and fields[key] is not None and fields[key] not in allowed:
            raise BugStoreError(f"invalid {key}={fields[key]!r}; must be one of {allowed}")
    payload = _encode({**fields, "updated_at": _now()})
    sets = ", ".join(f"{k} = ?" for k in payload)
    with _connect(db) as conn:
        cur = conn.execute(f"UPDATE tickets SET {sets} WHERE id = ?",
                           [*payload.values(), ticket_id])
        if cur.rowcount == 0:
            raise BugStoreError(f"no ticket {ticket_id}")
    return get_ticket(ticket_id, db=db) or {}


def resolve_ticket(
    ticket_id: str,
    *,
    reporter_note: str,
    status: str = "prod_verified",
    resolution: str = "fixed",
    branch: Optional[str] = None,
    pr_url: Optional[str] = None,
    db: Optional[Path] = None,
) -> dict:
    """Mark a ticket resolved with a human-facing note (the close-the-loop step).

    ``reporter_note`` is REQUIRED + non-blank — the "what changed" sentence the
    reporter sees. Binding the close to the fix is what stops tickets rotting.
    """
    note = (reporter_note or "").strip()
    if not note:
        raise BugStoreError(
            "reporter_note is required to resolve a ticket — the loop cannot close "
            "to the reporter without a human-facing 'what changed' note."
        )
    if status not in ("merged", "prod_verified"):
        raise BugStoreError(f"resolve status must be merged|prod_verified, got {status!r}")
    fields: dict[str, Any] = {
        "status": status,
        "resolution": resolution,
        "reporter_note": note[:1000],
        "resolved_at": _now(),
    }
    if branch:
        fields["branch"] = branch
    if pr_url:
        fields["pr_url"] = pr_url
    return update_ticket(ticket_id, fields, db=db)


# ── Read paths ───────────────────────────────────────────────────────────────
def get_ticket(ticket_id: str, db: Optional[Path] = None) -> Optional[dict]:
    with _connect(db) as conn:
        cur = conn.execute("SELECT * FROM tickets WHERE id = ? LIMIT 1", (ticket_id,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def list_tickets(
    *,
    status: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
    db: Optional[Path] = None,
) -> list[dict]:
    q = "SELECT * FROM tickets"
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if source:
        where.append("source = ?")
        params.append(source)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with _connect(db) as conn:
        rows = conn.execute(q, params).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── CLI ──────────────────────────────────────────────────────────────────────
def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="bugsquash.store")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list tickets")
    pl.add_argument("--status")
    pl.add_argument("--source")
    pl.add_argument("--limit", type=int, default=20)

    pg = sub.add_parser("get", help="get one ticket by id")
    pg.add_argument("id")

    pc = sub.add_parser("create", help="create a ticket")
    pc.add_argument("--source", default="cli")
    pc.add_argument("--title", required=True)
    pc.add_argument("--body", default="")
    pc.add_argument("--context", default="", help="JSON object")

    args = p.parse_args(argv)
    try:
        if args.cmd == "list":
            rows = list_tickets(status=args.status, source=args.source, limit=args.limit)
            for r in rows:
                print(f"{r['id'][:8]}  [{(r.get('status') or ''):>13}] "
                      f"{(r.get('severity') or '--'):>2}/{(r.get('risk_tier') or '--')}  "
                      f"{(r.get('title') or '')[:60]}")
            print(f"\n{len(rows)} ticket(s)")
        elif args.cmd == "get":
            row = get_ticket(args.id)
            print(json.dumps(row, indent=2, default=str) if row else "not found")
        elif args.cmd == "create":
            ctx = json.loads(args.context) if args.context else {}
            row = create_ticket(source=args.source, title=args.title, body=args.body, context=ctx)
            print(f"created {row['id']}")
        return 0
    except BugStoreError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
