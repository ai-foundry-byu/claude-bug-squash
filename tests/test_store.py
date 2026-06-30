import pytest

from bugsquash import store


@pytest.fixture
def db(tmp_path):
    return tmp_path / "t.db"


def test_create_get(db):
    row = store.create_ticket(title="crash on save", body="stack...", db=db)
    assert row["id"]
    got = store.get_ticket(row["id"], db=db)
    assert got["title"] == "crash on save"
    assert got["status"] == "captured"
    assert got["context"] == {}


def test_update_validates(db):
    row = store.create_ticket(title="x", db=db)
    with pytest.raises(store.BugStoreError):
        store.update_ticket(row["id"], {"severity": "p9"}, db=db)
    updated = store.update_ticket(row["id"], {"severity": "p2", "status": "triaged",
                                              "affected_files": ["a.py"]}, db=db)
    assert updated["severity"] == "p2"
    assert updated["affected_files"] == ["a.py"]


def test_list_filters(db):
    store.create_ticket(title="a", db=db)
    b = store.create_ticket(title="b", db=db)
    store.update_ticket(b["id"], {"status": "triaged"}, db=db)
    assert len(store.list_tickets(db=db)) == 2
    assert len(store.list_tickets(status="triaged", db=db)) == 1


def test_resolve_requires_note(db):
    row = store.create_ticket(title="x", db=db)
    with pytest.raises(store.BugStoreError):
        store.resolve_ticket(row["id"], reporter_note="", db=db)
    done = store.resolve_ticket(row["id"], reporter_note="fixed the null deref", db=db)
    assert done["status"] == "prod_verified"
    assert done["reporter_note"]


def test_reproduced_bool_roundtrip(db):
    row = store.create_ticket(title="x", db=db)
    store.update_ticket(row["id"], {"reproduced": True}, db=db)
    assert store.get_ticket(row["id"], db=db)["reproduced"] is True
