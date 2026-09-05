import subprocess

import pytest

from epitaph.detect import detect
from epitaph.store import TombstoneStore


def git(repo, *args):
    return subprocess.run(
        ("git",) + args,
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "test@example.com")
    git(r, "config", "user.name", "Test")
    (r / "lock.py").write_text("FLAG = 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "Add redis session lock")
    return r


def test_detect_creates_candidate_from_real_revert(repo):
    git(repo, "revert", "--no-edit", "HEAD")
    original_sha = git(repo, "log", "--pretty=%H", "HEAD~1").strip()

    report = detect(repo)

    assert report.reverts == 1
    assert len(report.created) == 1
    store = TombstoneStore(repo)
    tomb = store.get(report.created[0])

    # candidate confidence: an agent may never self-approve
    assert tomb.confidence == "candidate"
    assert tomb.status == "active"
    # attempt is the subject of the reverted commit, not the revert itself
    assert tomb.attempt == "Add redis session lock"
    # evidence links both commits
    assert any(e.startswith("revert ") for e in tomb.evidence)
    assert ("commit " + original_sha) in tomb.evidence
    # scope anchors are the files of the reverted commit
    assert tomb.scope == ["lock.py"]
    assert tomb.id.startswith("ts-")


def test_detect_is_idempotent(repo):
    git(repo, "revert", "--no-edit", "HEAD")
    first = detect(repo)
    # the scan position is recorded inside the ledger
    assert (TombstoneStore(repo).dir / ".cursor").is_file()

    # incremental: nothing new after the cursor, so nothing is even scanned
    second = detect(repo)
    assert second.reverts == 0
    assert second.created == []

    # a forced full rescan sees the same revert but never duplicates it
    third = detect(repo, full=True)
    assert third.created == []
    assert third.skipped == first.created
    assert len(TombstoneStore(repo).all()) == 1


def test_detect_incremental_scans_only_new_commits(repo):
    git(repo, "revert", "--no-edit", "HEAD")
    first = detect(repo)
    assert first.reverts == 1

    # a second, independent revert lands after the recorded cursor
    # (stage only the new file: `add -A` would sweep the ledger itself into
    # the commit, and reverting that commit would delete the tombstones)
    (repo / "queue.py").write_text("Q = 1\n")
    git(repo, "add", "queue.py")
    git(repo, "commit", "-q", "-m", "Add queue module")
    git(repo, "revert", "--no-edit", "HEAD")
    second = detect(repo)
    assert second.reverts == 1
    assert len(second.created) == 1
    assert len(TombstoneStore(repo).all()) == 2

    # and a third run finds nothing new
    third = detect(repo)
    assert third.reverts == 0 and third.created == []


def test_detect_survives_a_stale_cursor(repo):
    git(repo, "revert", "--no-edit", "HEAD")
    assert detect(repo).created
    # simulate a history rewrite that strands the cursor sha
    store = TombstoneStore(repo)
    (store.dir / ".cursor").write_text("0" * 40 + "\n", encoding="utf-8")
    report = detect(repo)  # falls back to a full scan instead of crashing
    assert report.reverts == 1
    assert report.skipped  # the previously created tombstone is still deduped


def test_detect_ignores_non_revert_commits(repo):
    (repo / "other.py").write_text("x = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Add other module")
    report = detect(repo)
    assert report.reverts == 0
    assert report.created == []


def test_detect_empty_repo_is_noop(tmp_path):
    r = tmp_path / "empty"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "test@example.com")
    git(r, "config", "user.name", "Test")
    report = detect(r)
    assert report.reverts == 0 and report.created == []


def test_detect_creates_store_dir_if_missing(repo):
    git(repo, "revert", "--no-edit", "HEAD")
    assert not (repo / ".tombstones").exists()  # precondition
    detect(repo)
    assert (repo / ".tombstones").is_dir()
