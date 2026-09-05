import pytest

from epitaph.cli import main
from epitaph.stale import audit_stale, is_pathlike
from epitaph.store import TombstoneStore


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    monkeypatch.chdir(r)
    (r / "src").mkdir()
    (r / "src" / "lock.py").write_text("class SessionLock:\n    pass\n", encoding="utf-8")
    (r / "old.py").write_text("def gone_function():\n    pass\n", encoding="utf-8")
    return r


def _add(repo, attempt, scope):
    argv = ["--repo", str(repo), "add", "--attempt", attempt, "--reason", "r",
            "--retry-when", "w"]
    if scope:
        argv += ["--scope"] + scope
    assert main(argv) == 0
    # all() is filename-sorted, not insertion-ordered — fetch by content
    return next(t for t in TombstoneStore(repo).all() if t.attempt == attempt)


def test_is_pathlike():
    assert is_pathlike("src/lock.py")
    assert is_pathlike("lock.py")
    assert not is_pathlike("SessionLock")
    assert not is_pathlike("lock_session")


def test_all_anchors_gone_flips(repo):
    tomb = _add(repo, "redis lock", ["deleted_dir/impl.py"])
    findings = audit_stale(repo, TombstoneStore(repo))
    assert [f.tombstone.id for f in findings] == [tomb.id]
    assert findings[0].missing == ["deleted_dir/impl.py"]


def test_partial_survival_stays_active(repo):
    _add(repo, "mixed attempt", ["src/lock.py", "deleted_dir/impl.py"])
    findings = audit_stale(repo, TombstoneStore(repo))
    # one anchor still exists: testimony stands until the last anchor dies
    assert findings == []


def test_symbol_found_by_text_scan(repo):
    _add(repo, "lock via symbol", ["SessionLock"])
    assert audit_stale(repo, TombstoneStore(repo)) == []


def test_symbol_gone(repo):
    (r := repo / "old.py").unlink()
    _add(repo, "old approach", ["gone_function"])
    findings = audit_stale(repo, TombstoneStore(repo))
    assert len(findings) == 1
    assert findings[0].missing == ["gone_function"]


def test_unscoped_and_stale_records_not_audited(repo):
    _add(repo, "no scope", [])
    tomb = _add(repo, "already stale", ["deleted/x.py"])
    store = TombstoneStore(repo)
    tomb.status = "stale"
    store.save(tomb)
    assert audit_stale(repo, store) == []


def test_stale_cli_report_then_apply(repo, capsys):
    tomb = _add(repo, "redis lock", ["deleted_dir/impl.py"])
    assert main(["stale"]) == 0
    out = capsys.readouterr().out
    assert "stale candidate: %s" % tomb.id in out
    assert "would flip to stale" in out
    assert "epitaph stale --apply" in out
    assert TombstoneStore(repo).get(tomb.id).status == "active"  # dry run wrote nothing

    assert main(["stale", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "flipped to stale: %s" % tomb.id in out
    assert TombstoneStore(repo).get(tomb.id).status == "stale"

    # idempotent: a stale record is not re-audited
    assert main(["stale", "--apply"]) == 0
    assert "0 flipped" in capsys.readouterr().out


def test_symbol_scan_skips_binary_and_large(repo):
    (repo / "blob.bin").write_bytes(b"\x00" + b"x" * 100)
    _add(repo, "x", ["SessionLock"])  # must still be found despite binary junk
    assert audit_stale(repo, TombstoneStore(repo)) == []
