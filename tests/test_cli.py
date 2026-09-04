import os
import re
import subprocess

import pytest

from tombstone.cli import main


def _git(cwd, *args):
    return subprocess.run(
        ("git",) + args,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    monkeypatch.chdir(r)
    return r


def _add_ok(**kw):
    argv = [
        "add",
        "--attempt", kw.get("attempt", "Redis-based distributed lock for sessions"),
        "--reason", kw.get("reason", "race window remained"),
        "--date", kw.get("date", "2026-08-12"),
    ]
    if kw.get("scope"):
        argv += ["--scope"] + kw["scope"]
    if kw.get("retry_when"):
        argv += ["--retry-when", kw["retry_when"]]
    if kw.get("confidence"):
        argv += ["--confidence", kw["confidence"]]
    assert main(argv) == 0


def _last_id(capsys):
    out = capsys.readouterr().out
    match = re.search(r"ts-\d{8}-[0-9a-f]{4}", out)
    assert match, out
    return match.group(0)


def test_init_creates_store(repo, capsys):
    assert main(["init"]) == 0
    assert (repo / ".tombstones").is_dir()
    assert "initialized" in capsys.readouterr().out


def test_full_flow(repo, capsys):
    assert main(["init"]) == 0
    capsys.readouterr()
    _add_ok(
        scope=["src/session/lock.py", "src/session/manager.py"],
        retry_when="with fencing tokens",
        confidence="approved",
    )
    tid = _last_id(capsys)

    assert main(["list"]) == 0
    assert "Redis-based distributed lock" in capsys.readouterr().out

    assert main(["show", tid]) == 0
    show = capsys.readouterr().out
    assert "retry_when: with fencing tokens" in show
    assert "confidence: approved" in show
    assert "src/session/lock.py" in show

    assert main(["check", "redis lock for session writes"]) == 0
    check = capsys.readouterr().out
    assert tid in check
    assert "approved" in check
    assert "testimony, not verdicts" in check

    assert main(["check", "--file", "src/session/lock.py"]) == 0
    assert tid in capsys.readouterr().out

    assert main(["check", "kubernetes operator pattern"]) == 0
    assert "no matching tombstones" in capsys.readouterr().out


def test_add_defaults_to_candidate_then_approve(repo, capsys):
    _add_ok()
    tid = _last_id(capsys)
    assert main(["show", tid]) == 0
    assert "confidence: candidate" in capsys.readouterr().out

    assert main(["approve", tid]) == 0
    assert main(["show", tid]) == 0
    assert "confidence: approved" in capsys.readouterr().out

    assert main(["approve", tid]) == 0
    assert "already approved" in capsys.readouterr().out


def test_approve_missing_id_fails(repo):
    main(["init"])
    assert main(["approve", "ts-20260101-ffff"]) == 1


def test_overturn_keeps_refutation(repo, capsys):
    _add_ok()
    tid = _last_id(capsys)
    assert main(["overturn", tid, "--reason", "retry with fencing tokens landed"]) == 0
    assert main(["show", tid]) == 0
    out = capsys.readouterr().out
    assert "status: overturned" in out
    assert "overturn_reason: retry with fencing tokens landed" in out
    assert main(["list", "--status", "overturned"]) == 0
    assert tid in capsys.readouterr().out


def test_list_filters(repo, capsys):
    _add_ok(scope=["src/session/lock.py"])
    first = _last_id(capsys)
    _add_ok(attempt="Rust parser rewrite", reason="too risky", scope=["src/parser/mod.rs"])
    second = _last_id(capsys)

    assert main(["list", "--scope", "src/session"]) == 0
    out = capsys.readouterr().out
    assert first in out
    assert second not in out

    assert main(["list", "--status", "overturned"]) == 0
    assert "no tombstones matching filters" in capsys.readouterr().out


def test_check_requires_query_or_files(repo):
    main(["init"])
    assert main(["check"]) == 1


def test_commands_fail_without_store(repo, capsys):
    assert main(["list"]) == 1
    assert "no .tombstones" in capsys.readouterr().err


def test_explicit_repo_flag(tmp_path, capsys, monkeypatch):
    repo = tmp_path / "elsewhere"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)  # cwd has no store; --repo must win
    assert main(["--repo", str(repo), "init"]) == 0
    assert (repo / ".tombstones").is_dir()


def test_install_hook_and_idempotency(tmp_path, monkeypatch, capsys):
    r = tmp_path / "hookrepo"
    r.mkdir()
    _git(r, "init", "-q")
    monkeypatch.chdir(r)

    assert main(["install-hook"]) == 0
    hook = r / ".git" / "hooks" / "post-commit"
    content = hook.read_text()
    assert "#!/bin/sh" in content
    assert "# >>> tombstone >>>" in content
    assert "tombstone detect" in content
    assert os.access(hook, os.X_OK)

    assert main(["install-hook"]) == 0
    assert "already installed" in capsys.readouterr().out

    # a pre-existing foreign hook gets the block appended, original kept
    hook.write_text("#!/bin/sh\necho custom\n")
    assert main(["--repo", str(r), "install-hook"]) == 0
    content = hook.read_text()
    assert "echo custom" in content and "# >>> tombstone >>>" in content


def test_detect_and_approve_cli_end_to_end(tmp_path, capsys):
    r = tmp_path / "e2e"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "test@example.com")
    _git(r, "config", "user.name", "Test")
    (r / "a.py").write_text("FLAG = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "Add feature flag")
    _git(r, "revert", "--no-edit", "HEAD")

    assert main(["--repo", str(r), "detect"]) == 0
    out = capsys.readouterr().out
    tid = re.search(r"ts-\d{8}-[0-9a-f]{4}", out).group(0)
    assert "candidate" in out
    assert "1 revert commit(s) scanned, 1 created" in out

    assert main(["--repo", str(r), "list"]) == 0
    assert tid in capsys.readouterr().out

    assert main(["--repo", str(r), "approve", tid]) == 0
    capsys.readouterr()

    assert main(["--repo", str(r), "check", "feature flag"]) == 0
    check = capsys.readouterr().out
    assert tid in check and "approved" in check
