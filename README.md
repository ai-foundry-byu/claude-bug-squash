# claude-bug-squash

**An autonomous, gated bug-squasher for any repo.** Point Claude Code at your codebase, report a bug, and it triages the bug, writes a fix in an isolated git worktree, *proves* the bug goes red→green, runs the fix through a layered safety gate and a 3-seat adversarial review panel — and only merges behind shadow mode, a per-day rate limit, and an explicit arm switch.

**Safe by default: out of the box it proposes and gates, but NEVER merges.** You read the proposed diffs first. Merging is something you turn on, deliberately, after you trust what you see.

---

## Safety model

This is the whole point of the project, so it comes first. A fix has to pass **every** layer below before a single line lands on your branch. The layers are independent on purpose — the fix-writing agent cannot "self-certify" past any of them, because each one re-checks the *actual* `git diff`, not the agent's claims.

```
report a bug
    │
    ▼
1. TRIAGE → risk tier          classify severity, try to reproduce, locate files,
   (auto-safe / human-gate /   assign a risk tier. A deny-list match here is
    never-auto)                 never-auto immediately.
    │
    ▼
2. FIX in an isolated worktree  a scoped `claude` rewrites code in a throwaway
                                git worktree — never your real working tree.
    │
    ▼
3. REPRO PROOF (red → green)    the repro command must FAIL before the fix and
                                PASS after. No proof → human-gate, never auto.
    │
    ▼
4. BLAST-RADIUS CAPS            ≤3 files, ≤40 lines churn, no new deps, no
                                schema/migrations, no infra/CI/config, p2/p3 only,
                                no binaries. Counted on the real diff.
    │
    ▼
5. NEVER-TOUCH DENY-LIST        an independent re-check of the diff against the
                                deny-list (auth/secrets/payments/migrations/
                                deploy/security-rails). A hit is a HARD BLOCK —
                                diagnosis only, escalated, never merged.
    │
    ▼
6. 3-SEAT ADVERSARIAL PANEL     Product / Safety / Architecture, each a scoped,
                                read-only `claude -p`. Any one cited BLOCK vetoes
                                the merge.
    │
    ▼
7. SHADOW MODE                  even after all of the above, shadow mode RECORDS
                                what it *would* merge and stops. Default ON.
    │
    ▼
8. PER-DAY RATE LIMIT           at most N auto-merges/day (default 1) — a triage
                                misclassification can't fan out into a string of
                                bad merges before you see the first.
    │
    ▼
9. EXPLICIT ARM SWITCH          the literal `git merge` only runs when you set
                                AUTO_SQUASH_MERGE_ARMED=1 *and* pass `--merge`.
                                Default OFF → dry-run ("WOULD merge").
    │
    ▼
   merge (only if every layer above said yes)
```

**Nothing auto-merges out of the box.** A fresh install runs in shadow mode with the arm switch off, so the *most* it ever does is propose a branch, gate it, run the panel, and tell you what it would have merged. Turning on real merges is a sequence of deliberate env changes documented under [How autonomy works](#how-autonomy-works--how-to-arm-it).

The deny-list, blast-radius caps, panel seats, and repro/test commands all live in **`bugsquash.toml`** at your repo root — nothing is hardcoded, so the same engine works on any product.

---

## Install

`claude-bug-squash` runs on top of the **Claude Code `claude` CLI**, which you install separately (see [Prerequisites](#prerequisites)). There are two ways to install the squasher itself.

### (a) As a Claude Code plugin

The plugin adds slash commands that drive the `bug-squash` CLI, so install the CLI as well — see (b).

From your terminal (safe to paste whole):

```bash
claude plugin marketplace add ai-foundry-byu/claude-bug-squash && claude plugin install claude-bug-squash@claude-bug-squash
```

Or from inside Claude Code, one command at a time. Pasted together, `/plugin marketplace add` reads the second line as part of the repo name and fails.

```
/plugin marketplace add ai-foundry-byu/claude-bug-squash
```

```
/plugin install claude-bug-squash@claude-bug-squash
```

Restart Claude Code (or run `/reload-plugins`). Plugin commands are namespaced, so type them in full:

| Command | Does |
| --- | --- |
| `/claude-bug-squash:bug-squash-init` | set up `bugsquash.toml` + reviewer souls for this repo |
| `/claude-bug-squash:bug-squash-add` | capture a bug ticket |
| `/claude-bug-squash:bug-squash-run` | triage → fix → gate → panel → act |
| `/claude-bug-squash:bug-squash-status` | list tickets and their state |

### (b) As a CLI

It is not on PyPI. Install it straight from GitHub:

```bash
pip install git+https://github.com/ai-foundry-byu/claude-bug-squash
```

or, from a clone, the bundled installer:

```bash
git clone https://github.com/ai-foundry-byu/claude-bug-squash && cd claude-bug-squash && ./install.sh
```

Both install the `bug-squash` console command. Python 3.11+ is required; the engine itself has **zero runtime dependencies** (stdlib only).

---

## Quickstart

```bash
# 1. set up the repo (writes bugsquash.toml + .bugsquash/agents/reviewer-*.md)
bug-squash init

# 2. report a bug
bug-squash add "cart total is off by one cent on 3+ items" \
  --body "Add 3 items at \$1.00 each, total shows \$3.01"

# 3. triage → fix → gate → panel → act (shadow mode by default: proposes, never merges)
bug-squash run
```

Example shape of `bug-squash run` output (shadow mode):

```
triaged 1, attempted 1
  a1b2c3d4   in_review     SHADOW: fix would auto-merge; recorded, not merged
```

Check state any time:

```bash
$ bug-squash status
a1b2c3d4  [    in_review] p3/auto-safe  cart total is off by one cent on 3+ items

1 ticket(s)

$ bug-squash posture
posture: SHADOW (AUTO_SQUASH_SHADOW=1 — proposes + gates, never merges)
project: repo=your-app deny=62 globs caps=3f/40L panel=on
state:   /path/to/your-app/.bugsquash/state
config:  /path/to/your-app/bugsquash.toml
```

`bug-squash init` introspects your repo with `claude` to generate a **tailored** deny-list and per-seat reviewer souls (`.bugsquash/agents/reviewer-product.md`, `reviewer-safety.md`, `reviewer-architecture.md`). Use `bug-squash init --no-introspect` to skip that and write solid generic defaults instead, and `--force` to overwrite existing files.

A full worked example on a tiny sample repo (off-by-one bug + failing test as the repro command) is in [`examples/quickstart.md`](examples/quickstart.md).

---

## How autonomy works / how to ARM it

Autonomy is controlled entirely by **environment variables** (the safety arm-switches), kept separate from the per-repo config on purpose. Defaults are the safest possible posture.

| Env var | Default | Meaning |
| --- | --- | --- |
| `AUTO_SQUASH_ENABLED` | `1` | Kill switch. Set `0` and the autonomous tier is dead instantly — intake, triage, and propose-branch keep working, nothing auto-merges. |
| `AUTO_SQUASH_SHADOW` | `1` | Shadow mode. While on, the gate computes what it *would* auto-merge and records it, but never merges. |
| `AUTO_SQUASH_MERGE_ARMED` | `0` | The literal-merge arm switch. Even with the gate green and the panel approving, the real `git merge` only runs when this is `1`. Default → dry-run ("WOULD merge"). |
| `AUTO_SQUASH_MAX_MERGES_PER_DAY` | `1` | Per-day auto-merge rate limit. A misclassification can't fan out into a string of bad merges before you see the first. |
| `AUTO_SQUASH_STATE_DIR` | `<repo>/.bugsquash/state` | Where the rate-limit ledger + ticket store live. |

### Recommended rollout

1. **Run in shadow for 1–2 weeks (the default).** Do `bug-squash run` regularly. Every eligible fix lands on a branch and is recorded as `SHADOW: fix would auto-merge`. **Read the proposed diffs.** This is your evidence that triage, the caps, the deny-list, and the panel are behaving on *your* codebase.

2. **When the shadow record is clean, leave shadow:**
   ```bash
   export AUTO_SQUASH_SHADOW=0
   ```
   You're now "LIVE" — but still dry-run, because the arm switch is off. The gate will report `AUTO_MERGE` decisions without performing them.

3. **Arm the actual merge:**
   ```bash
   export AUTO_SQUASH_MERGE_ARMED=1
   bug-squash run --merge
   ```
   `--merge` wires the real git merger; the arm switch authorizes it to fire. Start with `AUTO_SQUASH_MAX_MERGES_PER_DAY=1` and raise it only once you trust the loop.

**The gate, caps, deny-list, and panel apply in every posture.** Arming does *not* loosen any of them — it only lets a fix that *already* passed all of them perform the final merge instead of stopping at "would merge." The kill switch (`AUTO_SQUASH_ENABLED=0`) instantly reverts to propose-and-gate-only at any time.

---

## Configuration reference (`bugsquash.toml`)

Generated by `bug-squash init` at your repo root. All sections are optional; anything omitted falls back to the built-in safe defaults shown here.

### `[repo]`

| Key | Default | Meaning |
| --- | --- | --- |
| `label` | repo directory name | A human label for the repo, shown in output and carried on tickets. |

### `[caps]` — blast-radius caps for the no-tap autonomous tier

| Key | Default | Meaning |
| --- | --- | --- |
| `max_files` | `3` | Max files an auto-eligible fix may change. |
| `max_lines` | `40` | Max line churn (added + deleted). |
| `auto_severities` | `["p2", "p3"]` | Severities eligible for the autonomous tier. p0/p1 always go to a human. |
| `dependency_globs` | built-in list | Paths treated as dependency manifests/lockfiles (any change → human-gate). |
| `schema_globs` | built-in list | Paths treated as schema/migration/DDL. |
| `infra_globs` | built-in list | Paths treated as infra/CI/build config. |

### `[deny]` — the never-touch deny-list (hard BLOCK)

| Key | Default | Meaning |
| --- | --- | --- |
| `globs` | `[]` | Your repo-specific globs. By default these **extend** the built-in safe defaults (auth/secrets/payments/migrations/deploy/security-rails). |
| `replace_defaults` | `false` | Set `true` to use ONLY your `globs` and drop the built-in defaults (not recommended). |
| `outbound_tells` | built-in list | Substring "tells" (e.g. `send_email`, `stripe.`, `requests.post`) that mark a brand-new file as outbound-comms even when its path matches nothing — also a hard BLOCK. |

### `[panel]` — the adversarial review panel

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Run the panel before any merge. `false` skips it (saves tokens, less safe). |
| `seats` | `["product", "safety", "architecture"]` | The reviewer seats; each loads its soul from `.bugsquash/agents/reviewer-<seat>.md`. |
| `model` | `"opus"` | The `claude` model each reviewer runs as. |
| `timeout_s` | `240` | Per-seat review timeout (a timed-out seat fails open; the merge stays gated by every other layer). |
| `agents_dir` | `.bugsquash/agents` | Where the per-seat reviewer souls live. |

### `[fix]` — how a fix is produced, proven, and branched

| Key | Default | Meaning |
| --- | --- | --- |
| `repro_command` | `""` | Shell command that **fails (non-zero) while the bug is present** and **passes (exit 0) once fixed** — the red→green proof the gate requires for an autonomous merge. Empty → fixes can't auto-merge (no proof). |
| `test_command` | `""` (init seeds it) | The test/build command run after a fix to prove green. `init` seeds this from your repo (`pytest -q`, `npm test`, `cargo test`, `go test ./...`). |
| `branch_prefix` | `"bugsquash/"` | Prefix for the branch each fix is authored on. |
| `base` | `""` | Base branch to fix off. `""` → current branch / HEAD. |
| `fix_model` | `"opus"` | The `claude` model the fix-writing agent runs as. |
| `fix_timeout_s` | `1200` | Timeout for the fix-writing run. |

> The defaults above are read from the engine source (`config.py` / `caps.py` / `deny_list.py`) and are the actual shipped values, not illustrative.

---

## Works on any project (including desktop apps)

The engine operates on **git diffs** and the **`claude` CLI** — it is language- and framework-agnostic. There is no parser for any specific language, framework, or runtime. To make it work on your project, set two things in `bugsquash.toml`:

- **`repro_command`** — how to reproduce the bug (a failing test, a script, a build that errors). The gate uses its red→green transition as proof.
- **`test_command`** — how to confirm the project is still green after a fix.

That's it. Whether your repo is a Python service, a TypeScript web app, a Rust CLI, a Go backend, or an Electron desktop app, the caps and deny-list run on the diff and the panel reads the diff plus your code. The deny-list ships generous cross-language defaults (e.g. `*.entitlements`, `*.provisionprofile`, `electron-builder*`, `*.notarize*` for desktop signing/release) and `init` tailors it further.

---

## Notifications

The engine emits one notification per squashed bug (and on escalations / human-gates). Where it goes is set with **`BUGSQUASH_NOTIFY`** (comma-separated to fan out to several backends; default `console`):

| Backend | Goes to | Config |
| --- | --- | --- |
| `console` | stdout (default — zero config) | — |
| `file` | appends JSON lines | `BUGSQUASH_NOTIFY_FILE` (default `.bugsquash/notifications.jsonl`) |
| `webhook` | HTTP POST `{"text": ...}` | `BUGSQUASH_NOTIFY_WEBHOOK` (Slack / Discord / Telegram bot / anything) |
| `command` | your command, message on stdin | `BUGSQUASH_NOTIFY_COMMAND` |

```bash
# fan out to a Slack webhook and a local log file
export BUGSQUASH_NOTIFY="webhook,file"
export BUGSQUASH_NOTIFY_WEBHOOK="https://hooks.slack.com/services/…"
```

A notifier failure is swallowed — a broken webhook never crashes a squash run (the work already happened).

---

## Architecture / module map

`src/bugsquash/` — pure stdlib, no runtime deps:

| Module | Role |
| --- | --- |
| `cli.py` | the `bug-squash` entrypoint (`init`, `add`, `triage`, `run`, `status`, `posture`). |
| `config.py` | the two config layers — autonomy **posture** (env switches) and **project config** (`bugsquash.toml`). |
| `init.py` | `bug-squash init`: introspects the repo with `claude` to write a tailored `bugsquash.toml` + reviewer souls. |
| `store.py` | the local ticket data layer (SQLite, zero-setup, lives under `.bugsquash/state/`). |
| `triage.py` | classify severity → reproduce → locate files → assign risk tier. |
| `diff.py` | parses a `git diff` into a `Changeset` (file count, churn, binary flags, renames). |
| `caps.py` | the blast-radius caps, counted on the real diff. |
| `deny_list.py` | the never-touch deny-list + outbound-comms content tells. |
| `spawn.py` | the fix-writer: authors a fix in a throwaway git worktree, runs repro before/after. |
| `fixer.py` | sequences one ticket through the gated fix and routes the verdict. |
| `gate.py` | **the diff gate** — the load-bearing decision (`NO_CHANGES`/`BLOCK`/`HUMAN_GATE`/`SHADOW`/`AUTO_MERGE`). |
| `review_panel.py` | the 3-seat adversarial review (each seat a scoped read-only `claude -p`). |
| `ratelimit.py` | the per-day auto-merge ledger. |
| `git_merge.py` | the real, gated git merge (only wired when you pass `--merge`). |
| `integration.py` / `runner.py` | wire the side-effecting seams and drive `bug-squash run`. |
| `notify.py` | the pluggable notifier backends. |

---

## Prerequisites

- **Python 3.11+** (for `tomllib`).
- **git** (the engine works on git worktrees + diffs).
- **The Claude Code `claude` CLI** — the fixer and the review panel shell out to it. Install it from [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and make sure `claude` is on your `PATH`. Without it, `init` falls back to generic defaults and the fix/panel stages can't run.

---

## License

MIT © JD Davenport. See [`LICENSE`](LICENSE).

Issues and PRs: <https://github.com/ai-foundry-byu/claude-bug-squash>. See [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`SECURITY.md`](SECURITY.md).
