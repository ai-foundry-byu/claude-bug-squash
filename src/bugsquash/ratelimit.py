"""bug_squash.ratelimit — the per-day auto-merge cap (design §3.6).

A triage misclassification must not fan out into a string of bad merges before you
sees the first one, so the autonomous tier is rate-limited (default 1 auto-merge /
day). The ledger is a tiny JSON file keyed by UTC date — durable across process
restarts, so a crashed-and-restarted drainer can't reset its own budget.

ONLY a real auto-merge is recorded. Shadow "would-merge" decisions and human-gated
merges do NOT consume the budget (they aren't autonomous merges). The gate calls
``would_exceed`` BEFORE merging and ``record`` only AFTER a real merge succeeds.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

LEDGER_NAME = "auto_merge_ledger.json"


def _utc_today(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).strftime("%Y-%m-%d")


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class RateLimiter:
    """File-backed daily auto-merge counter."""

    def __init__(self, state_dir: Path, *, clock: Callable[[], datetime] = _default_clock):
        self.state_dir = Path(state_dir)
        self.ledger_path = self.state_dir / LEDGER_NAME
        self._clock = clock

    # ── ledger io ──────────────────────────────────────────────────────────────
    def _read(self) -> dict:
        try:
            return json.loads(self.ledger_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(self.ledger_path)

    # ── queries ────────────────────────────────────────────────────────────────
    def count_today(self) -> int:
        return int(self._read().get(_utc_today(self._clock), 0))

    def would_exceed(self, max_per_day: int) -> bool:
        """True if recording one more merge today would break the cap."""
        if max_per_day <= 0:
            return True
        return self.count_today() >= max_per_day

    def remaining(self, max_per_day: int) -> int:
        return max(0, max_per_day - self.count_today())

    # ── mutation ───────────────────────────────────────────────────────────────
    def record(self, *, ticket_id: str | None = None) -> int:
        """Record one real auto-merge for today; return the new daily count.

        Only call this AFTER an actual merge succeeds. The ``ticket_id`` is kept in
        a small audit trail alongside the counter for replay.
        """
        data = self._read()
        day = _utc_today(self._clock)
        data[day] = int(data.get(day, 0)) + 1
        audit = data.setdefault("_audit", {})
        audit.setdefault(day, []).append(
            {"ticket_id": ticket_id, "at": self._clock().astimezone(timezone.utc).isoformat()}
        )
        # keep the audit from growing without bound — last 30 days only
        for key in list(audit.keys()):
            if key < _cutoff(self._clock):
                audit.pop(key, None)
        self._write(data)
        return int(data[day])


def _cutoff(clock: Callable[[], datetime], days: int = 30) -> str:
    from datetime import timedelta

    return (clock().astimezone(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
