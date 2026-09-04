import subprocess

import pytest

from tombstone.detect import detect
from tombstone.store import TombstoneStore


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
    second = detect(repo)
    assert second.created == []
    assert second.skipped == first.created
    assert len(TombstoneStore(repo).all()) == 1


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
