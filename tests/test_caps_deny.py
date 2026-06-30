from bugsquash import caps, deny_list as dl
from bugsquash.diff import Changeset, FileChange


def _cs(*files):
    return Changeset(repo="repo", files=tuple(
        FileChange(path=p, status="M", added=a, deleted=d) for p, a, d in files))


def test_caps_clean_passes():
    assert caps.check(_cs(("a.ts", 3, 1)), severity="p2",
                      reproduced_before=True, reproduced_after=False) == []


def test_caps_line_churn():
    v = caps.check(_cs(("a.ts", 50, 0)), severity="p2",
                   reproduced_before=True, reproduced_after=False)
    assert any(c.code == "lines" for c in v)


def test_caps_dependency_manifest():
    v = caps.check(_cs(("package.json", 1, 1)), severity="p2",
                   reproduced_before=True, reproduced_after=False)
    assert any(c.code == "dependencies" for c in v)


def test_caps_limits_override():
    from bugsquash.config import Limits
    v = caps.check(_cs(("a.ts", 3, 0)), severity="p2",
                   reproduced_before=True, reproduced_after=False,
                   limits=Limits(max_lines=2))
    assert any(c.code == "lines" for c in v)


def test_deny_classes():
    assert dl.is_denied("src/lib/auth-timeout.ts")
    assert dl.is_denied("config/.env.production")
    assert dl.is_denied("server/billing/charge.go")
    assert dl.is_denied("db/migrations/0007_add.sql")
    assert dl.is_denied(".github/workflows/release.yml")
    assert dl.is_denied("infra/terraform/main.tf")
    assert not dl.is_denied("src/ui/Card.tsx")
    assert not dl.is_denied("lib/util/format.py")


def test_deny_custom_globs():
    globs = ("**/secret-sauce/**",)
    assert dl.is_denied("app/secret-sauce/x.py", globs)
    assert not dl.is_denied("app/auth/x.py", globs)  # custom replaces defaults here


def test_content_outbound():
    assert dl.content_is_outbound("import stripe\nstripe.charge(...)")
    assert dl.content_is_outbound("x = requests.post(url)")
    assert not dl.content_is_outbound("def add(a, b):\n    return a + b")
