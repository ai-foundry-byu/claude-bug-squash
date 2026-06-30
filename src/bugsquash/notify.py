"""bugsquash.notify — pluggable outbound notifier.

The engine emits one notification per squashed bug (and on escalations / human
gates). Where that notification GOES is configurable so the tool fits any setup,
replacing what was a single hardcoded channel:

  * **console** (default) — print to stdout. Always works, zero config.
  * **file**             — append JSON lines to a log file (``BUGSQUASH_NOTIFY_FILE``
                           or ``.bugsquash/notifications.jsonl``).
  * **webhook**          — HTTP POST ``{"text": ...}`` to ``BUGSQUASH_NOTIFY_WEBHOOK``
                           (works with Slack / Discord / Telegram bot / anything).
  * **command**          — run ``BUGSQUASH_NOTIFY_COMMAND`` with the message on
                           stdin (shell out to your own notifier).

Select with ``BUGSQUASH_NOTIFY`` (comma-separated to fan out to several). Default
is ``console``. A notifier failure is swallowed — a broken webhook must never
crash the squash run (the work already happened).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

NotifyFn = Callable[[str], bool]


def _console(text: str) -> bool:
    print(text, file=sys.stdout, flush=True)
    return True


def _file(text: str, path: Optional[Path] = None) -> bool:
    path = path or Path(os.getenv("BUGSQUASH_NOTIFY_FILE", "")) or Path(".bugsquash/notifications.jsonl")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": datetime.now(timezone.utc).isoformat(), "text": text}
        with path.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        return True
    except Exception:
        return False


def _webhook(text: str, url: Optional[str] = None) -> bool:
    url = url or os.getenv("BUGSQUASH_NOTIFY_WEBHOOK", "")
    if not url:
        return False
    try:
        import urllib.request
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception:
        return False


def _command(text: str, cmd: Optional[str] = None) -> bool:
    cmd = cmd or os.getenv("BUGSQUASH_NOTIFY_COMMAND", "")
    if not cmd:
        return False
    try:
        res = subprocess.run(cmd, shell=True, input=text, text=True,
                             capture_output=True, timeout=30)
        return res.returncode == 0
    except Exception:
        return False


_BACKENDS: dict[str, NotifyFn] = {
    "console": _console,
    "file": _file,
    "webhook": _webhook,
    "command": _command,
}


def make_notifier(spec: Optional[str] = None) -> NotifyFn:
    """Build a notifier from a comma-separated backend ``spec`` (env-default).

    Returns a ``(text) -> bool`` that fans the message out to every selected
    backend and returns True if ANY backend accepted it. Never raises.
    """
    spec = spec if spec is not None else os.getenv("BUGSQUASH_NOTIFY", "console")
    names = [s.strip() for s in spec.split(",") if s.strip()] or ["console"]
    backends = [_BACKENDS.get(n) for n in names]
    backends = [b for b in backends if b]
    if not backends:
        backends = [_console]

    def _notify(text: str) -> bool:
        ok = False
        for b in backends:
            try:
                ok = b(text) or ok
            except Exception:
                pass
        return ok

    return _notify
