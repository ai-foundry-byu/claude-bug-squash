# Security

`claude-bug-squash` lets an AI agent write code and, once you arm it, merge that code into your repository. That is inherently a higher-trust operation than a normal dev tool, so the entire design is built around **failing safe**. This document explains the threat model, why each safety layer exists, and how to disclose a vulnerability.

## Threat model

The core risk: an autonomous agent produces a change that is wrong, dangerous, or malicious, and that change reaches your default branch (and from there, possibly production) without a human noticing. The threats we explicitly defend against:

1. **A plausible-but-wrong fix.** The agent "fixes" the wrong thing, masks the symptom instead of the root cause, or regresses a working feature.
   - *Defense:* the **repro red→green proof** (the bug must actually reproduce before the fix and stop reproducing after) and the **Product seat** of the review panel.

2. **A fix that touches dangerous code.** Even a small, correct-looking diff that edits authentication, secrets, payments, schema migrations, deploy/release scripts, or the safety rails themselves can be catastrophic and irreversible.
   - *Defense:* the **never-touch deny-list**, re-checked against the *actual* diff at gate time (the agent can't talk past it), plus the **Safety seat**. A deny-list hit is a hard BLOCK — diagnosis only, escalated, never merged in any posture.

3. **A blast radius that's too big to reason about.** Large diffs are hard to review and more likely to hide a problem.
   - *Defense:* the **blast-radius caps** — ≤3 files, ≤40 lines churn, no new dependencies, no schema/migration/DDL, no infra/CI/config, p2/p3 severity only, no binary blobs. Anything over the line demotes to human-gate, never silent-merge.

4. **The agent self-certifying.** A naive design would trust the agent's claim that "my diff is small and safe."
   - *Defense:* every cap and the deny-list are computed from the **real `git diff`**, not the agent's self-report. The gate is the single arbiter and the agent has no way to override it.

5. **A misclassification fanning out.** One bad triage could produce a run of bad merges before you ever see the first.
   - *Defense:* the **per-day rate limit** (default 1 auto-merge/day) caps the damage to one before a human is in the loop.

6. **A hallucinated veto wedging the loop**, or a flaky reviewer doing the same.
   - *Defense:* the panel demands each BLOCK **cite a real file/line** (an uncited block is downgraded to a soft concern), and a reviewer that times out or crashes **fails open for that seat only** — while every other safety layer (caps, deny-list, shadow, rate limit) stays in force.

7. **Anything irreversible happening before a human has watched the loop for a while.**
   - *Defense:* **shadow mode is on by default and the arm switch is off by default.** A fresh install cannot merge anything. Real merges require two deliberate env changes (`AUTO_SQUASH_SHADOW=0` then `AUTO_SQUASH_MERGE_ARMED=1`) plus the `--merge` flag, and the **kill switch** (`AUTO_SQUASH_ENABLED=0`) instantly reverts to propose-and-gate-only.

These layers are independent and conjunctive: a fix must pass *all* of them. Each exists because skipping it would let one of the threats above through.

## Review your generated deny-list before you arm

`bug-squash init` introspects your repo with `claude` to generate a tailored deny-list in `bugsquash.toml`. **Before you ever flip `AUTO_SQUASH_MERGE_ARMED=1`, read that `[deny]` block and confirm it covers every security-critical, irreversible, money-moving, or externally-facing path in your codebase.** The built-in defaults are deliberately broad (auth, secrets, payments, migrations, deploy, security rails), but only you know the paths specific to your product. The safe failure mode of the deny-list is to over-block (route to a human); the unsafe one is a gap. When in doubt, add the glob.

Also worth confirming before arming:

- Your `repro_command` genuinely fails on the bug and passes on the fix — a weak repro weakens the strongest proof in the chain.
- `replace_defaults` in `[deny]` is `false` unless you have a specific reason — `true` drops the built-in safe defaults entirely.
- The review panel is `enabled = true`. Disabling it to save tokens removes the judgement layer.

## What this tool does NOT protect against

- A `repro_command` or `test_command` that runs arbitrary code on your machine — those are *your* commands; treat `bugsquash.toml` as trusted config.
- Secrets in your environment that the `claude` CLI can read. Scope the agent's environment as you would any CI job.
- A deny-list with a gap you didn't catch (see above).

## Responsible disclosure

If you find a vulnerability — especially anything that could let a fix bypass the gate, the deny-list, the caps, or the arm switch — please report it **privately** rather than opening a public issue:

- Open a [GitHub security advisory](https://github.com/jddavenportOpen/claude-bug-squash/security/advisories/new) on the repo, **or**
- Email the maintainer via the contact on the GitHub profile, with `claude-bug-squash security` in the subject.

Please include reproduction steps and the impact. We'll acknowledge, work on a fix, and credit you (if you'd like) once a patch is available. Please give us a reasonable window to address the issue before any public disclosure.
