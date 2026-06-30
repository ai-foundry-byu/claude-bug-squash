---
description: List claude-bug-squash tickets and the current autonomy posture. Optionally filter by status (e.g. triaged, fixed, merged).
argument-hint: "[--status <status>]"
disable-model-invocation: true
allowed-tools: Bash(bug-squash:*), Bash(command:*)
---

## Pre-flight: is the CLI installed?

!`command -v bug-squash >/dev/null 2>&1 && echo "OK: bug-squash found at $(command -v bug-squash)" || echo "MISSING: the bug-squash CLI is not installed."`

## Autonomy posture

!`command -v bug-squash >/dev/null 2>&1 && bug-squash posture || true`

## Tickets

!`command -v bug-squash >/dev/null 2>&1 && bug-squash status $ARGUMENTS || true`

## Instructions

If the pre-flight reported **MISSING**, do NOT proceed. Tell the user the `bug-squash`
binary is not on their PATH and they must install it first:

- `pip install claude-bug-squash` (recommended), or
- run the repo's `install.sh` if they cloned the source.

Then stop.

If the pre-flight reported **OK**, present the posture and the ticket list above to the
user in a concise summary (id, status, severity/risk tier, title). If there are no tickets,
say so and suggest `/claude-bug-squash:bug-squash-add "<a bug>"`. Do not invent tickets.
