import subprocess

import pytest

from epitaph.cli import main
from epitaph.matcher import Match
from epitaph.render import MATCH_LIMIT, format_matches
from epitaph.schema import Tombstone
from epitaph.store import TombstoneStore


def _git(cwd, *args):
    return subprocess.run(
        ("git",) + args,
        cwd=str(cwd), check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    monkeypatch.chdir(r)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    return r


def _add(repo, attempt, **kw):
    argv = [
        "--repo", str(repo), "add",
        "--attempt", attempt,
        "--reason", kw.get("reason", "race window"),
        "--retry-when", kw.get("retry_when", "redis added a safe del"),
    ]
    assert main(argv) == 0


def test_init_recommends_detect_first(repo, capsys):
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    # cold start: the first suggested step mines existing history, not typing
    assert out.index("epitaph detect") < out.index("epitaph install-hook")
    assert "epitaph snippets" in out


def test_init_detect_mines_reverts(repo, capsys):
    (repo / "lock.py").write_text("FLAG = 1\n")
    _git(repo, "add", "lock.py")
    _git(repo, "commit", "-q", "-m", "Add redis session lock")
    _git(repo, "revert", "--no-edit", "HEAD")
    assert main(["init", "--detect"]) == 0
    out = capsys.readouterr().out
    assert "1 revert commit(s) scanned, 1 created" in out
    assert "epitaph review" in out  # candidates exist -> review is step 1


def test_review_approves_skips_and_quit(repo, capsys, monkeypatch):
    _add(repo, "Redis lock A")
    _add(repo, "Redis lock B")
    _add(repo, "Redis lock C")
    answers = iter(["y", "n", "q"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    assert main(["review"]) == 0
    out = capsys.readouterr().out
    assert "1 approved, 1 skipped, 1 candidate(s) remain" in out
    records = sorted(TombstoneStore(repo).all(), key=lambda t: t.id)
    confidences = sorted(t.confidence for t in records)
    assert confidences == ["approved", "candidate", "candidate"]


def test_review_eof_never_approves(repo, capsys, monkeypatch):
    _add(repo, "Redis lock A")
    _add(repo, "Redis lock B")

    def boom(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", boom)
    assert main(["review"]) == 0
    out = capsys.readouterr().out
    assert "stopping without approving" in out
    store = TombstoneStore(repo)
    assert all(t.confidence == "candidate" for t in store.all())


def test_review_empty_ledger(repo, capsys):
    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["review"]) == 0
    assert "no candidates awaiting review" in capsys.readouterr().out


def _many_matches(n):
    return [
        Match(
            tombstone=Tombstone(
                id="ts-20260905-%04x" % i,
                attempt="approach %d" % i,
                reason="rejected %d" % i,
                rejected_at="2026-09-05",
                rejected_by="human-review",
            ),
            score=1.0,
            reasons=["test"],
        )
        for i in range(n)
    ]


def test_format_matches_caps_output():
    text = format_matches(_many_matches(MATCH_LIMIT + 5))
    assert text.startswith("%d tombstone(s) match:" % (MATCH_LIMIT + 5))
    # only MATCH_LIMIT records are rendered
    assert text.count("attempt: approach ") == MATCH_LIMIT
    assert "and 5 more match(es) not shown" in text


def test_format_matches_under_limit_unchanged():
    text = format_matches(_many_matches(3))
    assert "attempt: approach " in text
    assert "more match(es) not shown" not in text
