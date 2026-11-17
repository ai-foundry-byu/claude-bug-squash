---
description: Capture a bug ticket for claude-bug-squash. Pass the bug title (and optionally a description) as the argument; it is stored for triage and a later fix run.
argument-hint: "<bug title / short description>"
disable-model-invocation: true
allowed-tools: Bash(bug-squash:*), Bash(command:*)
---

## Pre-flight: is the CLI installed?

!`command -v bug-squash >/dev/null 2>&1 && echo "OK: bug-squash found at $(command -v bug-squash)" || echo "MISSING: the bug-squash CLI is not installed."`

## Capture the bug

!`command -v bug-squash >/dev/null 2>&1 && bug-squash add "$ARGUMENTS" || true`

## Instructions

If the pre-flight reported **MISSING**, do NOT proceed. Tell the user the `bug-squash`
binary is not on their PATH and they must install it first:

- `pip install git+https://github.com/ai-foundry-byu/claude-bug-squash` (recommended), or
- run the repo's `install.sh` if they cloned the source.

Then stop.

If the user invoked this command with no argument, ask them for a one-line description
of the bug (and any repro steps), then tell them to re-run
`/claude-bug-squash:bug-squash-add "<their bug>"`. Do not fabricate a bug.

If the pre-flight reported **OK**, the output above shows the captured ticket id and title.
Confirm to the user that the bug was captured and tell them the next step:
`/claude-bug-squash:bug-squash-run` to triage → fix → gate → review the open tickets.
