"""bug_squash.diff — parse a fix's ACTUAL git diff into a structured Changeset.

The diff gate counts the diff itself; it never trusts the agent's self-report of
"only 2 files." This module is the counter. It parses ``git diff --numstat`` (line
counts) joined with ``git diff --name-status`` (add/modify/delete/rename), or reads
a live worktree via ``from_git``.

Per-file line counts come from ``--numstat``: ``<added>\\t<deleted>\\t<path>``.
Binary files show ``-\\t-\\t<path>`` — we flag those (``is_binary``) because a binary
change in a "cosmetic" fix is exactly the kind of thing that should never auto-merge.
Renames in ``--numstat`` appear as ``<a>\\t<d>\\t<old> => <new>`` (or with ``{ }``
braces under ``-M``); we resolve to the new path.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class FileChange:
    """One changed file in a diff."""

    path: str                 # repo-relative path (the NEW path for renames)
    status: str               # A | M | D | R | C | ? (single letter)
    added: int = 0            # lines added (0 for binary/unknown)
    deleted: int = 0          # lines deleted (0 for binary/unknown)
    is_binary: bool = False
    old_path: Optional[str] = None   # set for renames/copies

    @property
    def churn(self) -> int:
        """Lines touched = added + deleted (the blast-radius measure)."""
        return self.added + self.deleted


@dataclass(frozen=True)
class Changeset:
    """The full set of changes a fix produced, with the repo it came from."""

    repo: str                                  # the repo label this diff came from
    files: tuple[FileChange, ...] = field(default_factory=tuple)

    # ── aggregate measures the caps check against ──────────────────────────────
    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def added(self) -> int:
        return sum(f.added for f in self.files)

    @property
    def deleted(self) -> int:
        return sum(f.deleted for f in self.files)

    @property
    def churn(self) -> int:
        """Total lines touched across all files (added + deleted)."""
        return sum(f.churn for f in self.files)

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def has_binary(self) -> bool:
        return any(f.is_binary for f in self.files)

    @property
    def is_empty(self) -> bool:
        return not self.files

    def summary(self) -> dict:
        return {
            "repo": self.repo,
            "file_count": self.file_count,
            "added": self.added,
            "deleted": self.deleted,
            "churn": self.churn,
            "binary": self.has_binary,
            "paths": self.paths,
        }


def _resolve_rename_path(raw: str) -> tuple[str, Optional[str]]:
    """Resolve a numstat path field that may encode a rename.

    Forms git emits:
      - ``a/b/c.py``                          → (a/b/c.py, None)
      - ``old.py => new.py``                  → (new.py, old.py)
      - ``src/{old => new}/file.py``          → (src/new/file.py, src/old/file.py)
    """
    raw = raw.strip()
    if " => " not in raw:
        return raw, None

    # brace form: prefix{old => new}suffix
    if "{" in raw and "}" in raw:
        pre, rest = raw.split("{", 1)
        mid, post = rest.split("}", 1)
        old_mid, new_mid = (s.strip() for s in mid.split(" => ", 1))
        new_path = (pre + new_mid + post).replace("//", "/")
        old_path = (pre + old_mid + post).replace("//", "/")
        return new_path.strip(), old_path.strip()

    # plain form: old => new
    old_path, new_path = (s.strip() for s in raw.split(" => ", 1))
    return new_path, old_path


def parse_numstat(numstat: str) -> dict[str, FileChange]:
    """Parse ``git diff --numstat`` output → {path: FileChange} (status filled later)."""
    out: dict[str, FileChange] = {}
    for line in numstat.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a_raw, d_raw, path_raw = parts[0], parts[1], "\t".join(parts[2:])
        is_binary = a_raw.strip() == "-" or d_raw.strip() == "-"
        added = 0 if is_binary else _safe_int(a_raw)
        deleted = 0 if is_binary else _safe_int(d_raw)
        path, old_path = _resolve_rename_path(path_raw)
        out[path] = FileChange(
            path=path,
            status="R" if old_path else "M",
            added=added,
            deleted=deleted,
            is_binary=is_binary,
            old_path=old_path,
        )
    return out


def _safe_int(s: str) -> int:
    try:
        return int(s.strip())
    except (ValueError, AttributeError):
        return 0


def parse_name_status(name_status: str) -> dict[str, str]:
    """Parse ``git diff --name-status`` → {new_path: status_letter}."""
    out: dict[str, str] = {}
    for line in name_status.splitlines():
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0].strip()
        letter = status[0] if status else "M"  # R100/C75 → R/C
        if letter in ("R", "C") and len(parts) >= 3:
            out[parts[2].strip()] = letter           # new path
        elif len(parts) >= 2:
            out[parts[-1].strip()] = letter
    return out


def parse(numstat: str, name_status: str = "", repo: str = "repo") -> Changeset:
    """Build a Changeset from raw ``--numstat`` (+ optional ``--name-status``)."""
    files = parse_numstat(numstat)
    statuses = parse_name_status(name_status) if name_status else {}
    merged: list[FileChange] = []
    for path, fc in files.items():
        st = statuses.get(path, fc.status)
        merged.append(
            FileChange(
                path=fc.path,
                status=st,
                added=fc.added,
                deleted=fc.deleted,
                is_binary=fc.is_binary,
                old_path=fc.old_path,
            )
        )
    # A pure rename/delete with no numstat row (rare) — pick up from name-status.
    for path, st in statuses.items():
        if path not in files:
            merged.append(FileChange(path=path, status=st))
    merged.sort(key=lambda f: f.path)
    return Changeset(repo=repo, files=tuple(merged))


def from_git(
    worktree: str,
    base: str = "HEAD",
    repo: str = "repo",
    *,
    runner=subprocess.run,
) -> Changeset:
    """Read the diff of ``worktree`` vs ``base`` straight from git.

    ``runner`` is injectable so tests don't shell out. Diffs ``base...`` against the
    working tree (staged + unstaged), which is what a freshly-authored fix looks
    like before commit. Raises ``RuntimeError`` if git fails.
    """
    def _git(*git_args: str) -> str:
        proc = runner(
            ["git", "-C", worktree, "diff", base, *git_args],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git diff failed ({proc.returncode}): {proc.stderr.strip()[:300]}"
            )
        return proc.stdout

    numstat = _git("--numstat")
    name_status = _git("--name-status")
    return parse(numstat, name_status, repo=repo)
