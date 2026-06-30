"""End-to-end harness test with fakes — no claude, no git, no network."""
from bugsquash import fixer, integration, review_panel as rp
from bugsquash.diff import Changeset, FileChange
from bugsquash.ratelimit import RateLimiter


def _attempt(paths_churn, *, repro=(True, False), branch="bugsquash/x", usable=True):
    files = tuple(FileChange(path=p, status="M", added=a, deleted=0) for p, a in paths_churn)
    return fixer.FixAttempt(changeset=Changeset(repo="repo", files=files),
                            reproduced_before=repro[0], reproduced_after=repro[1],
                            branch=branch, usable=usable, notes="fixed", diff_text="--- diff ---")


def _deps(spawn, **over):
    over.setdefault("notify_fn", lambda m: True)
    over.setdefault("panel_fn", lambda **kw: rp.PanelVerdict(
        seats=(rp.SeatVerdict("product", "approve", "ok"),)))
    return integration.build_deps(spawn, **over)


def test_clean_fix_armed_merges(live_posture):
    captured = {}
    deps = _deps(lambda t, n: _attempt([("a.ts", 3)]), armed=True,
                 merge_fn=lambda t, a, v: captured.setdefault("merged", True) or {"ok": 1})
    out = fixer.run_fix({"id": "1", "severity": "p2", "risk_tier": "auto-safe"},
                        deps, posture=live_posture,
                        rate_limiter=RateLimiter(live_posture.state_dir))
    assert out.status == "merged"
    assert captured.get("merged")


def test_clean_fix_not_armed_is_dry_run(live_posture):
    # gate AUTO_MERGE + panel approves, but armed=False → dry-run, merge_fn NOT called
    captured = {}
    deps = _deps(lambda t, n: _attempt([("a.ts", 3)]), armed=False,
                 merge_fn=lambda t, a, v: captured.setdefault("merged", True) or {"ok": 1})
    out = fixer.run_fix({"id": "1", "severity": "p2", "risk_tier": "auto-safe"},
                        deps, posture=live_posture,
                        rate_limiter=RateLimiter(live_posture.state_dir))
    # fixer records the gate's AUTO_MERGE decision as status; the real merge stayed gated
    assert out.status == "merged"
    assert not captured.get("merged")


def test_shadow_posture_does_not_merge(posture):
    seen = {}
    deps = _deps(lambda t, n: _attempt([("a.ts", 3)]))
    # patch notify_shadow capture via build_deps default (notify_fn)
    out = fixer.run_fix({"id": "1", "severity": "p2", "risk_tier": "auto-safe"},
                        deps, posture=posture,
                        rate_limiter=RateLimiter(posture.state_dir))
    assert out.status == "in_review"
    assert "SHADOW" in out.message


def test_denylisted_fix_blocks(live_posture):
    deps = _deps(lambda t, n: _attempt([("src/auth/login.ts", 3)]))
    out = fixer.run_fix({"id": "1", "severity": "p2", "risk_tier": "auto-safe"},
                        deps, posture=live_posture,
                        rate_limiter=RateLimiter(live_posture.state_dir))
    assert out.status == "escalated"
    assert out.verdict.decision == "BLOCK"


def test_no_usable_fix_escalates(live_posture):
    deps = _deps(lambda t, n: _attempt([("a.ts", 3)], usable=False))
    out = fixer.run_fix({"id": "1", "severity": "p2", "risk_tier": "auto-safe"},
                        deps, posture=live_posture,
                        rate_limiter=RateLimiter(live_posture.state_dir),
                        max_attempts=2)
    assert out.status == "failed"
    assert out.attempts == 2
