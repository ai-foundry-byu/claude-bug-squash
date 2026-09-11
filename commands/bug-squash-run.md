---
description: Run the claude-bug-squash pipeline on captured bugs — triage → fix in an isolated worktree → prove red→green → blast-radius/deny-list gate → 3-seat adversarial review panel → act. Safe-by-default (dry-run unless armed).
argument-hint: "[<ticket-id>] [--merge] [--no-triage]"
disable-model-invocation: true
allowed-tools: Bash(bug-squash:*), Bash(command:*)
---

## Pre-flight: is the CLI installed?

!`command -v bug-squash >/dev/null 2>&1 && echo "OK: bug-squash found at $(command -v bug-squash)" || echo "MISSING: the bug-squash CLI is not installed."`

## Current autonomy posture

!`command -v bug-squash >/dev/null 2>&1 && bug-squash posture || true`

## Run the pipeline

!`command -v bug-squash >/dev/null 2>&1 && bug-squash run $ARGUMENTS || true`

## Instructions

If the pre-flight reported **MISSING**, do NOT proceed. Tell the user the `bug-squash`
binary is not on their PATH and they must install it first:

- `pip install git+https://github.com/jddavenportOpen/claude-bug-squash` (recommended), or
- run the repo's `install.sh` if they cloned the source.

Then stop.

If the pre-flight reported **OK**, summarize the run output above for the user:
- how many tickets were triaged and how many fixes were attempted,
- for each ticket, its final status and whether it MERGED or was a dry-run / human-gated.

Remind the user of the safe-by-default posture shown above: with the defaults
(`AUTO_SQUASH_SHADOW=1`, `AUTO_SQUASH_MERGE_ARMED=0`) nothing is actually merged — the
pipeline proposes a fix and reports what it WOULD do. To enable real merges they must set
`AUTO_SQUASH_SHADOW=0` and `AUTO_SQUASH_MERGE_ARMED=1` and pass `--merge` (still gated by
the blast-radius caps, the deny-list, the review panel, and the per-day rate limit).

Do not edit any files or run git yourself — the CLI owns the fix-in-worktree and merge.
