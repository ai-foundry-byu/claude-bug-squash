from bugsquash import review_panel as rp


def _runner(map_):
    return lambda seat, prompt: map_.get(seat, "VERDICT: APPROVE")


def test_all_approve():
    panel = rp.review(ticket={"title": "x"}, diff="diff",
                      runner=_runner({}), parallel=False)
    assert panel.approved


def test_cited_block_vetoes():
    out = {"safety": "VERDICT: BLOCK\n- this leaks a secret in src/config.py line 12"}
    panel = rp.review(ticket={"title": "x"}, diff="d", runner=_runner(out), parallel=False)
    assert not panel.approved
    assert panel.blockers and panel.blockers[0].seat == "safety"


def test_uncited_block_is_soft_concern():
    out = {"product": "I don't like this.\nVERDICT: BLOCK"}
    panel = rp.review(ticket={"title": "x"}, diff="d", runner=_runner(out), parallel=False)
    assert panel.approved  # uncited block does not veto
    assert panel.soft_concerns


def test_unparseable_fails_open():
    out = {"architecture": "the model rambled with no verdict line"}
    panel = rp.review(ticket={"title": "x"}, diff="d", runner=_runner(out), parallel=False)
    assert panel.approved
    assert panel.failed_open_seats


def test_load_soul_builtin_fallback():
    soul = rp.load_soul("safety", None)
    assert "SAFETY" in soul


def test_load_soul_from_file(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    (d / "reviewer-product.md").write_text("---\nname: x\n---\n\nCustom product soul for THIS app.\n")
    soul = rp.load_soul("product", d)
    assert "Custom product soul" in soul
