from bugsquash import gate
from bugsquash.diff import Changeset
from bugsquash.ratelimit import RateLimiter


def _rl(posture):
    return RateLimiter(posture.state_dir)


def test_empty_diff_is_no_changes(posture):
    v = gate.evaluate(Changeset(repo="repo"), tier="auto-safe", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=posture, rate_limiter=_rl(posture))
    assert v.decision == gate.NO_CHANGES


def test_denylist_hit_blocks(posture, cs):
    v = gate.evaluate(cs(("src/auth/session.ts", 2, 0)), tier="auto-safe", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=posture, rate_limiter=_rl(posture))
    assert v.decision == gate.BLOCK
    assert v.denied_files


def test_never_auto_tier_blocks(posture, cs):
    v = gate.evaluate(cs(("src/util.ts", 2, 0)), tier="never-auto", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=posture, rate_limiter=_rl(posture))
    assert v.decision == gate.BLOCK


def test_too_many_files_human_gate(posture, cs):
    v = gate.evaluate(cs(("a.ts",), ("b.ts",), ("c.ts",), ("d.ts",)),
                      tier="auto-safe", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=posture, rate_limiter=_rl(posture))
    assert v.decision == gate.HUMAN_GATE
    assert any(c.code == "files" for c in v.cap_violations)


def test_no_repro_proof_human_gate(posture, cs):
    v = gate.evaluate(cs(("a.ts", 3, 1)), tier="auto-safe", severity="p2",
                      reproduced_before=False, reproduced_after=False,
                      posture=posture, rate_limiter=_rl(posture))
    assert v.decision == gate.HUMAN_GATE


def test_clean_autosafe_shadow(posture, cs):
    # shadow on (default) → SHADOW, not merge
    v = gate.evaluate(cs(("a.ts", 3, 1)), tier="auto-safe", severity="p3",
                      reproduced_before=True, reproduced_after=False,
                      posture=posture, rate_limiter=_rl(posture))
    assert v.decision == gate.SHADOW


def test_kill_switch_off_human_gate(cs, tmp_path):
    from bugsquash import config
    p = config.Posture(enabled=False, shadow=False, merge_armed=True,
                       max_merges_per_day=1, state_dir=tmp_path / "s")
    v = gate.evaluate(cs(("a.ts", 3, 1)), tier="auto-safe", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=p, rate_limiter=RateLimiter(p.state_dir))
    assert v.decision == gate.HUMAN_GATE


def test_live_autosafe_auto_merges(live_posture, cs):
    v = gate.evaluate(cs(("a.ts", 3, 1)), tier="auto-safe", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=live_posture, rate_limiter=RateLimiter(live_posture.state_dir))
    assert v.decision == gate.AUTO_MERGE
    assert v.auto_merge_allowed


def test_rate_limit_exhausts(live_posture, cs):
    rl = RateLimiter(live_posture.state_dir)
    # exhaust the daily budget
    for _ in range(live_posture.max_merges_per_day):
        rl.record()
    v = gate.evaluate(cs(("a.ts", 3, 1)), tier="auto-safe", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=live_posture, rate_limiter=rl)
    assert v.decision == gate.HUMAN_GATE


def test_project_caps_override(live_posture, cs):
    from bugsquash import config
    proj = config.ProjectConfig(
        repo_root=live_posture.state_dir, repo_label="t",
        deny_globs=config.deny_mod.DEFAULT_DENY_GLOBS,
        outbound_tells=config.deny_mod.DEFAULT_OUTBOUND_TELLS,
        limits=config.Limits(max_files=1),
    )
    # 2 files now exceeds the tightened cap → human gate
    v = gate.evaluate(cs(("a.ts", 1, 0), ("b.ts", 1, 0)),
                      tier="auto-safe", severity="p2",
                      reproduced_before=True, reproduced_after=False,
                      posture=live_posture, project=proj,
                      rate_limiter=RateLimiter(live_posture.state_dir))
    assert v.decision == gate.HUMAN_GATE
