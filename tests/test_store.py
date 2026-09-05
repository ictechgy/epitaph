import json

import pytest

from epitaph.schema import Tombstone
from epitaph.store import DIRNAME, StoreError, TombstoneStore


def make_tomb(**over):
    fields = dict(
        attempt="redis lock",
        scope=["src/lock.py"],
        rejected_at="2026-08-12",
        rejected_by="human-review",
        reason="racy",
        evidence=["PR #1"],
        retry_when="fencing tokens",
        status="active",
        confidence="candidate",
    )
    fields.update(over)
    return Tombstone(**fields)


def test_add_assigns_deterministic_id(tmp_path):
    store = TombstoneStore(tmp_path)
    tomb = store.add(make_tomb(), seed=("add", "redis lock"))
    assert tomb.id.startswith("ts-20260812-")
    assert store.path_for(tomb.id).exists()
    assert json.loads(store.path_for(tomb.id).read_text())["attempt"] == "redis lock"


def test_add_same_content_twice_gets_distinct_ids(tmp_path):
    store = TombstoneStore(tmp_path)
    first = store.add(make_tomb(), seed=("add", "redis lock"))
    second = store.add(make_tomb(), seed=("add", "redis lock"))
    assert first.id != second.id
    assert store.has(first.id) and store.has(second.id)


def test_add_with_explicit_existing_id_raises(tmp_path):
    store = TombstoneStore(tmp_path)
    first = store.add(make_tomb(id="ts-20260812-aaaa"))
    with pytest.raises(StoreError, match="already exists"):
        store.add(make_tomb(id=first.id))


def test_get_roundtrip(tmp_path):
    store = TombstoneStore(tmp_path)
    added = store.add(make_tomb())
    loaded = store.get(added.id)
    assert loaded == added
    assert store.get("ts-20260101-zzzz") is None


def test_get_corrupt_raises(tmp_path):
    store = TombstoneStore(tmp_path)
    store.create()
    (store.dir / "ts-20260101-beef.json").write_text("{ not json")
    with pytest.raises(StoreError, match="corrupt"):
        store.get("ts-20260101-beef")


def test_all_skips_unreadable_files(tmp_path):
    store = TombstoneStore(tmp_path)
    good = store.add(make_tomb())
    (store.dir / "ts-20260101-dead.json").write_text("{ nope")
    (store.dir / "ts-20260101-cafe.json").write_text('{"id": "oops"}')
    records = store.all()
    assert [t.id for t in records] == [good.id]
    assert store.last_skipped == ["ts-20260101-cafe.json", "ts-20260101-dead.json"]


def test_all_on_missing_dir(tmp_path):
    assert TombstoneStore(tmp_path / "nope").all() == []


def test_save_rejects_invalid_record(tmp_path):
    store = TombstoneStore(tmp_path)
    tomb = store.add(make_tomb())
    tomb.status = "banana"
    with pytest.raises(Exception):
        store.save(tomb)
    # file still holds the last valid state
    assert store.get(tomb.id).status == "active"


def test_create_is_idempotent(tmp_path):
    store = TombstoneStore(tmp_path)
    store.create()
    store.create()
    assert store.exists()


def test_find_walks_up_to_nearest_store(tmp_path):
    parent = tmp_path / "proj"
    child = parent / "src" / "deep"
    child.mkdir(parents=True)
    TombstoneStore(parent).create()
    found = TombstoneStore.find(child)
    assert found is not None
    assert found.root == parent.resolve()


def test_find_returns_none_without_store(tmp_path):
    assert TombstoneStore.find(tmp_path) is None


def test_dirname():
    assert DIRNAME == ".tombstones"


def test_get_rejects_non_tombstone_ids(tmp_path):
    store = TombstoneStore(tmp_path)
    # never touches the filesystem, so no traversal via crafted ids
    assert store.get("../../evil") is None
    assert store.get("") is None


def test_save_is_atomic_no_temp_left_behind(tmp_path):
    store = TombstoneStore(tmp_path)
    tomb = Tombstone(attempt="x", reason="y", rejected_at="2026-09-05", rejected_by="human-review")
    store.add(tomb, seed=("t",))
    leftovers = [p.name for p in store.dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []
    assert json.loads(store.path_for(tomb.id).read_text())["attempt"] == "x"


def test_all_skips_unreadable_entries(tmp_path):
    store = TombstoneStore(tmp_path)
    store.create()
    good = Tombstone(attempt="good", reason="r", rejected_at="2026-09-05", rejected_by="human-review")
    store.add(good, seed=("g",))
    # a directory named *.json raises IsADirectoryError (an OSError) on read
    (store.dir / "ts-20260905-dead.json").mkdir()
    records = store.all()
    assert [t.id for t in records] == [good.id]
    assert store.last_skipped == ["ts-20260905-dead.json"]
