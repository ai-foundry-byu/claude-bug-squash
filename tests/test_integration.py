from bugsquash import integration, review_panel as rp
from bugsquash.fixer import FixAttempt
from bugsquash.gate import GateVerdict, AUTO_MERGE


def _attempt(cs):
    return FixAttempt(changeset=cs(("a.ts", 3, 1)), reproduced_before=True,
                      reproduced_after=False, branch="bugsquash/abc", notes="fixed it")


def _verdict(cs):
    return GateVerdict(decision=AUTO_MERGE, repo="repo", severity="p2",
                       tier_in="auto-safe", changeset=cs(("a.ts", 3, 1)).summary())


def _approve_panel(**kw):
    return rp.PanelVerdict(seats=(rp.SeatVerdict("product", "approve", "ok"),))


def _veto_panel(**kw):
    return rp.PanelVerdict(seats=(rp.SeatVerdict("safety", "block", "leak in x.py line 3", cited=True),))


def test_panel_approved_but_not_armed_is_dry_run(cs):
    msgs = []
    res = integration.panel_gated_merge(
        {"id": "1", "title": "t"}, _attempt(cs), _verdict(cs),
        panel_fn=_approve_panel, notify_fn=lambda m: msgs.append(m) or True,
        env={},  # AUTO_SQUASH_MERGE_ARMED unset → dry-run
    )
    assert res["outcome"] == "would_merge"
    assert not res["merged"]
    assert any("WOULD merge" in m for m in msgs)


def test_panel_veto_human_gates(cs):
    msgs = []
    res = integration.panel_gated_merge(
        {"id": "1", "title": "t"}, _attempt(cs), _verdict(cs),
        panel_fn=_veto_panel, notify_fn=lambda m: msgs.append(m) or True,
        env={"AUTO_SQUASH_MERGE_ARMED": "1"},
    )
    assert res["outcome"] == "human_gate"
    assert not res["merged"]


def test_armed_and_approved_calls_merge(cs):
    merged = {}
    res = integration.panel_gated_merge(
        {"id": "1", "title": "t"}, _attempt(cs), _verdict(cs),
        panel_fn=_approve_panel,
        merge_fn=lambda t, a, v: merged.setdefault("done", True) or {"ok": True},
        notify_fn=lambda m: True,
        env={"AUTO_SQUASH_MERGE_ARMED": "1"},
    )
    assert res["merged"]
    assert merged["done"]


def test_default_merge_fn_refuses(cs):
    # armed + approved but no real merge_fn wired → refuse → escalated, never merges
    res = integration.panel_gated_merge(
        {"id": "1", "title": "t"}, _attempt(cs), _verdict(cs),
        panel_fn=_approve_panel, notify_fn=lambda m: True,
        env={"AUTO_SQUASH_MERGE_ARMED": "1"},
    )
    assert not res["merged"]
    assert res["outcome"] == "escalated"
