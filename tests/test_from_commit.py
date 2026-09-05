import subprocess

import pytest

from epitaph.cli import main
from epitaph.mcp import Server
from epitaph.store import TombstoneStore
from epitaph.schema import Tombstone


def _git(cwd, *args):
    return subprocess.run(
        ("git",) + args, cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    monkeypatch.chdir(r)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "lock.py").write_text("FLAG = 1\n")
    _git(r, "add", "lock.py")
    _git(r, "commit", "-q", "-m", "Add redis session lock")
    return r


def test_add_from_revert_commit_prefills_target(repo, capsys):
    _git(repo, "revert", "--no-edit", "HEAD")
    revert_sha = _git(repo, "log", "-1", "--pretty=%H")
    original_sha = _git(repo, "log", "-1", "--pretty=%H", "HEAD~1")

    assert main(["add", "--from-commit", revert_sha, "--reason", "race window"]) == 0

    store = TombstoneStore(repo)
    tomb = store.all()[-1]
    # attempt/scope come from the reverted commit, not the revert
    assert tomb.attempt == "Add redis session lock"
    assert tomb.scope == ["lock.py"]
    assert ("revert " + revert_sha) in tomb.evidence
    assert ("commit " + original_sha) in tomb.evidence
    # rejected_at = committer date of the revert, not today
    revert_date = _git(repo, "log", "-1", "--date=short", "--pretty=%cd")
    assert tomb.rejected_at == revert_date


def test_add_from_plain_commit_uses_own_subject_and_files(repo, capsys):
    sha = _git(repo, "log", "-1", "--pretty=%H")
    assert main(["add", "--from-commit", sha, "--reason", "rejected in review"]) == 0
    tomb = TombstoneStore(repo).all()[-1]
    assert tomb.attempt == "Add redis session lock"
    assert tomb.scope == ["lock.py"]
    assert ("commit " + sha) in tomb.evidence


def test_add_from_commit_explicit_flags_win(repo):
    sha = _git(repo, "log", "-1", "--pretty=%H")
    assert main([
        "add", "--from-commit", sha,
        "--attempt", "custom attempt",
        "--scope", "other.py",
        "--evidence", "PR 7",
        "--date", "2026-01-02",
        "--reason", "r",
    ]) == 0
    tomb = TombstoneStore(repo).all()[-1]
    assert tomb.attempt == "custom attempt"
    assert tomb.scope == ["other.py"]
    assert tomb.evidence == ["PR 7"]
    assert tomb.rejected_at == "2026-01-02"


def test_add_from_commit_unknown_sha(repo, capsys):
    code = main(["add", "--from-commit", "0" * 40, "--reason", "r"])
    assert code == 1
    assert "no such commit" in capsys.readouterr().err


def test_add_still_requires_attempt_without_from_commit(repo, capsys):
    code = main(["add", "--reason", "r"])
    assert code == 1
    assert "--from-commit" in capsys.readouterr().err


def test_mcp_cache_sees_ledger_edits(repo, monkeypatch):
    store = TombstoneStore(repo)
    store.create()
    store.add(
        Tombstone(
            attempt="redis lock for sessions",
            reason="race window",
            rejected_at="2026-09-01",
            rejected_by="human-review",
        ),
        seed=("a",),
    )
    server = Server(repo=str(repo))

    calls = {"n": 0}
    real_all = TombstoneStore.all

    def counting_all(self):
        calls["n"] += 1
        return real_all(self)

    monkeypatch.setattr(TombstoneStore, "all", counting_all)

    first = server._check_nogo({"attempt": "redis lock"})
    assert "redis lock for sessions" in first
    again = server._check_nogo({"attempt": "redis lock"})
    assert again == first
    assert calls["n"] == 1  # second call served from cache

    # an edit through the CLI must be visible on the next tool call
    tomb = real_all(store)[0]
    tomb.attempt = "redis lock for sessions v2 with raft"
    store.save(tomb)
    third = server._check_nogo({"attempt": "redis lock"})
    assert "raft" in third
    assert calls["n"] == 2
