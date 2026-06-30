# Worked example: squashing a one-line off-by-one bug

This walks through `claude-bug-squash` end-to-end on a tiny sample repo with a single, real bug — an off-by-one error — and a failing test as the repro proof. It shows what `init`, `add`, `triage`, and `run` print, and exactly what **shadow mode** reports (the default posture that proposes a fix but never merges).

> Prerequisites: Python 3.11+, git, and the Claude Code `claude` CLI on your `PATH`. See the README.

## 1. The sample repo

Make a throwaway git repo with a buggy function and a test that catches it.

```bash
mkdir off-by-one-demo && cd off-by-one-demo
git init -q
```

`mathutil.py` — the bug is `<=` where it should be `<` (the range runs one past the end):

```python
# mathutil.py
def sum_first_n(values, n):
    """Sum the first n elements of `values`."""
    total = 0
    for i in range(n + 1):      # BUG: off-by-one — should be range(n)
        total += values[i]
    return total
```

`test_mathutil.py` — a failing test that reproduces the bug:

```python
# test_mathutil.py
from mathutil import sum_first_n

def test_sum_first_three():
    # first 3 of [1, 2, 3, 4] is 1+2+3 = 6
    assert sum_first_n([1, 2, 3, 4], 3) == 6
```

```bash
git add -A && git commit -qm "sample repo with an off-by-one bug"

# confirm the bug is real: this FAILS today
pytest -q
# >>> IndexError / assert ... — the test is RED
```

That failing test is our **repro command**: it fails (non-zero exit) while the bug is present and will pass once it's fixed — exactly the red→green proof the gate requires.

## 2. `bug-squash init`

```bash
bug-squash init
```

```
✓ bug-squash ready in /path/to/off-by-one-demo
  config: /path/to/off-by-one-demo/bugsquash.toml  (repo-introspected)
  wrote: /path/to/off-by-one-demo/bugsquash.toml
  wrote: /path/to/off-by-one-demo/.bugsquash/agents/reviewer-product.md
  wrote: /path/to/off-by-one-demo/.bugsquash/agents/reviewer-safety.md
  wrote: /path/to/off-by-one-demo/.bugsquash/agents/reviewer-architecture.md

Next: `bug-squash add "<bug>"` then `bug-squash run`. Auto-merge stays OFF until
you flip AUTO_SQUASH_SHADOW=0 + AUTO_SQUASH_MERGE_ARMED=1 (see README).
```

`init` detected this is a Python/pytest repo and seeded `test_command = "pytest -q"`. Open `bugsquash.toml` and set the **repro command** so triage can prove the bug red→green:

```toml
[fix]
repro_command = "pytest -q test_mathutil.py"
test_command = "pytest -q"
branch_prefix = "bugsquash/"
base = ""
fix_model = "opus"
```

(If `claude` wasn't available, `init` writes generic defaults instead — same flow, just an untailored deny-list and reviewer souls.)

## 3. `bug-squash add`

```bash
bug-squash add "sum_first_n returns the wrong total — sums one element too many" \
  --body "sum_first_n([1,2,3,4], 3) should be 6 but errors / overcounts; see test_mathutil.py" \
  --sev p3
```

```
captured a1b2c3d4  sum_first_n returns the wrong total — sums one element too many
```

## 4. `bug-squash triage`

```bash
bug-squash triage --all
```

```
a1b2c3d4  status=triaged  sev=p3  tier=auto-safe  repro=True  files=1

triaged 1 ticket(s)
```

Triage classified it **p3**, ran the repro command (it failed → `repro=True`, the bug reproduces), located **1** candidate file (`mathutil.py`), found no deny-list match, and assigned the **`auto-safe`** tier — narrow, low-severity, reproduced. The gate, panel, shadow mode, and rate limit are still the real arbiters downstream.

## 5. `bug-squash run` (shadow mode — the default)

```bash
bug-squash run
```

The runner spins up a throwaway git worktree, runs the repro command there (red), spawns a scoped `claude` to fix the bug at its root cause (`range(n + 1)` → `range(n)`), re-runs the repro (green), parses the diff, runs it through the gate, then the 3-seat panel — and **stops at shadow**:

```
triaged 0, attempted 1
  a1b2c3d4   in_review     SHADOW: fix would auto-merge; recorded, not merged
```

The fix is **on a branch** (`bugsquash/…`) for you to inspect; nothing was merged. Look at what it proposed:

```bash
git branch --list 'bugsquash/*'
git diff main..bugsquash/a1b2c3d4    # the one-line off-by-one fix
```

You can also confirm the posture and ticket state:

```bash
$ bug-squash posture
posture: SHADOW (AUTO_SQUASH_SHADOW=1 — proposes + gates, never merges)
project: repo=off-by-one-demo deny=62 globs caps=3f/40L panel=on
state:   /path/to/off-by-one-demo/.bugsquash/state
config:  /path/to/off-by-one-demo/bugsquash.toml

$ bug-squash status
a1b2c3d4  [    in_review] p3/auto-safe  sum_first_n returns the wrong total — su…

1 ticket(s)
```

## 6. What shadow mode is telling you

The `SHADOW: fix would auto-merge` line means the fix passed **every** safety layer — repro proof, blast-radius caps (1 file, ~1 line), the deny-list, and the panel — and the *only* thing stopping the merge is that shadow mode is on. That's the signal you're looking for during the 1–2 week shadow period: real fixes, fully gated, recorded for you to read.

## 7. Going live (only after a clean shadow record)

Once you've read enough proposed diffs to trust the loop on this repo:

```bash
export AUTO_SQUASH_SHADOW=0          # leave shadow → "LIVE", still dry-run
export AUTO_SQUASH_MERGE_ARMED=1     # arm the real merge
bug-squash run --merge               # --merge wires the real git merger
```

Now an `auto-safe` fix that clears the gate and the panel, under the daily rate limit, actually merges:

```
triaged 0, attempted 1
  a1b2c3d4       merged   MERGED   auto-merged (gate authorized)
```

Every safety layer still applies — arming only lets a fix that *already* passed all of them perform the final merge instead of stopping at "would merge." Set `AUTO_SQUASH_ENABLED=0` at any time to instantly drop back to propose-and-gate-only.
