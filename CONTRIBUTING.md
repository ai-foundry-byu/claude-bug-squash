# Contributing to claude-bug-squash

Thanks for helping out. This is a small, dependency-free codebase by design — the bar for new code is "does it keep the safety model honest and stay stdlib-only."

## Dev setup

Python 3.11+ is required. From a clone:

```bash
# editable install with the dev extras (pytest)
pip install -e ".[dev]"
```

or, if you prefer the bundled installer:

```bash
./install.sh --dev
```

Either way you get the `bug-squash` console command pointing at your working tree, plus `pytest`.

The engine has **zero runtime dependencies** — it uses only the standard library (`sqlite3`, `tomllib`, `urllib`, `subprocess`). **Please keep it that way.** A new third-party runtime dependency needs a strong justification; most things the engine needs already exist in stdlib. Test-only dev dependencies are fine under `[project.optional-dependencies] dev`.

## Running the tests

```bash
pytest -q
```

The tests are fast and hermetic: the fix-writer, the review panel, and the `claude` CLI calls are all behind **injectable seams**, so the suite never spawns a live `claude`, never touches the network, and never performs a real git merge. When you touch a module with a seam (`fixer.py`, `review_panel.py`, `spawn.py`, `runner.py`, `git_merge.py`), keep the seam injectable so the test can swap in a fake.

Run a single test file while iterating:

```bash
pytest -q tests/test_gate.py
```

## Project layout

```
src/bugsquash/        the engine (stdlib only)
  cli.py              the bug-squash entrypoint
  config.py           autonomy posture (env) + project config (bugsquash.toml)
  init.py             repo-introspecting setup
  store.py            SQLite ticket store
  triage.py           classify / reproduce / locate / risk-tier
  diff.py             git diff → Changeset
  caps.py             blast-radius caps
  deny_list.py        never-touch deny-list + outbound tells
  spawn.py            fix-writer (throwaway worktree)
  fixer.py            per-ticket control flow
  gate.py             THE diff gate (load-bearing)
  review_panel.py     3-seat adversarial panel
  ratelimit.py        per-day auto-merge ledger
  git_merge.py        the real gated merge
  integration.py      wires the side-effecting seams
  runner.py           end-to-end orchestration for `bug-squash run`
  notify.py           pluggable notifier backends
tests/                pytest suite (hermetic)
bugsquash.example.toml  a documented sample config
examples/             worked examples
```

## PR norms

- **Keep changes surgical.** Fix the thing you came to fix; don't refactor adjacent code in the same PR.
- **The safety model is non-negotiable.** Any change near `gate.py`, `caps.py`, `deny_list.py`, `ratelimit.py`, or the autonomy switches in `config.py` must come with tests that prove the gate still fails safe. Defaults must stay safe-by-default: shadow on, arm switch off, rate limit 1/day.
- **Add a test for every behaviour change.** A bug fix gets a regression test; a new capability gets coverage of its happy path and at least one failure mode.
- **Run `pytest -q` before opening the PR** and make sure it's green.
- **Document config/env additions.** New `bugsquash.toml` fields or `AUTO_SQUASH_*` / `BUGSQUASH_*` env vars belong in the README reference tables and (for config) in `bugsquash.example.toml`.
- **No new runtime dependencies** without discussion in the issue first.
- **Keep commit messages and PR descriptions concrete** — say what changed and why, and call out anything that touches the safety rails.

If you're proposing something that changes how merges are authorized, open an issue first so we can talk through the threat model (see [`SECURITY.md`](SECURITY.md)).
