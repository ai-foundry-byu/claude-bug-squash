---
description: Set up claude-bug-squash in the current repo — writes bugsquash.toml (deny-list, blast-radius caps, review panel) and per-seat reviewer souls, introspecting the codebase to tailor them.
argument-hint: "[--no-introspect] [--force]"
disable-model-invocation: true
allowed-tools: Bash(bug-squash:*), Bash(command:*)
---

## Pre-flight: is the CLI installed?

!`command -v bug-squash >/dev/null 2>&1 && echo "OK: bug-squash found at $(command -v bug-squash)" || echo "MISSING: the bug-squash CLI is not installed."`

## Run init

!`command -v bug-squash >/dev/null 2>&1 && bug-squash init $ARGUMENTS || true`

## Instructions

If the pre-flight reported **MISSING**, do NOT proceed. Tell the user the `bug-squash`
binary is not on their PATH and they must install it first, with exactly these options:

- `pip install git+https://github.com/ai-foundry-byu/claude-bug-squash` (recommended), or
- run the repo's `install.sh` if they cloned the source.

Then stop.

If the pre-flight reported **OK**, the init output above shows what was written.
Briefly summarize for the user:
- the `bugsquash.toml` that was created (or note it already existed — re-run with `--force` to overwrite),
- whether it was repo-introspected (tailored deny-list + reviewer souls) or used generic defaults,
- the reviewer agent files written under `.bugsquash/agents/`.

Then tell them the safe-by-default posture is in force: auto-merge stays OFF until they
explicitly set `AUTO_SQUASH_SHADOW=0` and `AUTO_SQUASH_MERGE_ARMED=1` (see the README).
Suggest the next step: `/claude-bug-squash:bug-squash-add "<a bug to fix>"`.
