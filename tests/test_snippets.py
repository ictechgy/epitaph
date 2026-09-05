import pytest

from tombstone.cli import main, SNIPPET, SNIPPET_BEGIN


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    monkeypatch.chdir(r)
    return r


def test_snippets_creates_agents_md(repo, capsys):
    assert main(["snippets"]) == 0
    content = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert SNIPPET in content
    assert not (repo / "CLAUDE.md").exists()
    assert "created AGENTS.md" in capsys.readouterr().out


def test_snippets_appends_to_existing_files(repo):
    (repo / "AGENTS.md").write_text("# My rules\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("# Claude rules\n", encoding="utf-8")
    assert main(["snippets"]) == 0
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert agents.startswith("# My rules\n")
    assert SNIPPET in agents
    assert SNIPPET in (repo / "CLAUDE.md").read_text(encoding="utf-8")


def test_snippets_never_creates_claude_md(repo):
    assert main(["snippets"]) == 0
    assert not (repo / "CLAUDE.md").exists()


def test_snippets_idempotent(repo, capsys):
    assert main(["snippets"]) == 0
    assert main(["snippets"]) == 0
    content = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert content.count(SNIPPET_BEGIN) == 1
    assert "already has the tombstone rule" in capsys.readouterr().out


def test_init_snippets_flag(repo):
    assert main(["init", "--snippets"]) == 0
    assert (repo / ".tombstones").is_dir()
    assert SNIPPET in (repo / "AGENTS.md").read_text(encoding="utf-8")


def test_snippets_respects_repo_flag(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    assert main(["--repo", str(other), "snippets"]) == 0
    assert (other / "AGENTS.md").exists()
    assert not (repo / "AGENTS.md").exists()
