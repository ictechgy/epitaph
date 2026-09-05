import json

import pytest

from epitaph.cli import main
from epitaph.store import TombstoneStore
from epitaph.transcripts import find_giveup_events, is_giveup_text


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = tmp_path / "repo"
    r.mkdir()
    monkeypatch.chdir(r)
    return r


def _claude_dir(tmp_path, repo):
    return tmp_path / "home" / ".claude" / "projects"


def _write_claude_session(claude_root, repo, session, records):
    import re
    munged = re.sub(r"[/\\:]", "-", str(repo.resolve()))
    d = claude_root / munged
    d.mkdir(parents=True, exist_ok=True)
    (d / (session + ".jsonl")).write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )


def _user(ts, text, session="s1", cwd=None):
    return {"type": "user", "sessionId": session, "timestamp": ts,
            "cwd": cwd, "message": {"content": text}}


def _assistant(ts, text, session="s1", cwd=None, content=None):
    return {"type": "assistant", "sessionId": session, "timestamp": ts,
            "cwd": cwd, "message": {"content": content if content is not None else [{"type": "text", "text": text}]}}


def test_is_giveup_text_patterns():
    assert is_giveup_text("That didn't work, so I'll scrap it.")
    assert is_giveup_text("I'll try a different approach for the parser.")
    assert is_giveup_text("이 방법으로는 안 되겠다")
    assert is_giveup_text("다른 방법을 시도해 보겠습니다")
    assert not is_giveup_text("The build is green, moving on to tests.")
    assert not is_giveup_text("")


def test_claude_giveup_event_flow(repo, tmp_path):
    cwd = str(repo.resolve())
    lock = str(repo / "src" / "lock.py")
    records = [
        _user("2026-09-06T10:00:00Z", "make session login fast", cwd=cwd),
        _assistant(
            "2026-09-06T10:01:00Z",
            "I'll implement a Redis-based lock for the session manager.",
            cwd=cwd,
            content=[
                {"type": "text", "text": "I'll implement a Redis-based lock for the session manager."},
                {"type": "tool_use", "id": "t1", "name": "Edit",
                 "input": {"file_path": lock}},
            ],
        ),
        _assistant("2026-09-06T10:05:00Z", "That didn't work; I'll try a different approach.", cwd=cwd),
    ]
    root = _claude_dir(tmp_path, repo)
    _write_claude_session(root, repo, "s1", records)

    events = find_giveup_events(repo, claude_root=root)
    assert len(events) == 1
    e = events[0]
    assert e.vendor == "claude"
    assert "different approach" in e.text
    assert "Redis-based lock" in e.previous_assistant
    assert "session login fast" in e.user_request
    assert e.edited_files == ["src/lock.py"]


def test_claude_other_repo_transcripts_ignored(repo, tmp_path):
    root = _claude_dir(tmp_path, repo)
    _write_claude_session(root, repo, "s1", [
        _user("2026-09-06T10:00:00Z", "x", cwd="/some/other/project"),
        _assistant("2026-09-06T10:01:00Z", "dead end", cwd="/some/other/project"),
    ])
    assert find_giveup_events(repo, claude_root=root) == []


def test_codex_giveup_events(repo, tmp_path):
    codex_root = tmp_path / "home" / ".codex" / "sessions" / "2026" / "09" / "06"
    codex_root.mkdir(parents=True)
    records = [
        {"type": "session_meta", "timestamp": "2026-09-06T09:00:00Z",
         "payload": {"id": "rollout-1", "cwd": str(repo.resolve())}},
        {"type": "response_item", "timestamp": "2026-09-06T09:01:00Z",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "fix the flaky test"}]}},
        {"type": "response_item", "timestamp": "2026-09-06T09:02:00Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "Trying retry loops..."}]}},
        {"type": "response_item", "timestamp": "2026-09-06T09:05:00Z",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "This isn't working, let's try a different way."}]}},
    ]
    (codex_root / "rollout-x.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    events = find_giveup_events(repo, codex_root=codex_root)
    assert len(events) == 1
    assert events[0].vendor == "codex"
    assert "different way" in events[0].text
    assert "retry loops" in events[0].previous_assistant


def test_events_newest_first(repo, tmp_path):
    cwd = str(repo.resolve())
    root = _claude_dir(tmp_path, repo)
    _write_claude_session(root, repo, "s1", [
        _user("2026-09-05T10:00:00Z", "u", cwd=cwd),
        _assistant("2026-09-05T10:05:00Z", "dead end", cwd=cwd),
    ])
    _write_claude_session(root, repo, "s2", [
        _user("2026-09-06T10:00:00Z", "u", cwd=cwd),
        _assistant("2026-09-06T10:05:00Z", "that did not work", cwd=cwd),
    ])
    events = find_giveup_events(repo, claude_root=root)
    assert [e.ts for e in events] == sorted((e.ts for e in events), reverse=True)


def test_giveup_cli_drafts_candidates_idempotently(repo, tmp_path, capsys, monkeypatch):
    cwd = str(repo.resolve())
    root = _claude_dir(tmp_path, repo)
    _write_claude_session(root, repo, "s1", [
        _user("2026-09-06T10:00:00Z", "make login fast", cwd=cwd),
        _assistant("2026-09-06T10:01:00Z", "I'll add a Redis cache layer.", cwd=cwd),
        _assistant("2026-09-06T10:05:00Z", "That didn't work; I'll try a different approach.", cwd=cwd),
    ])

    assert main(["init"]) == 0
    # point the adapter at the fake home for this CLI run
    monkeypatch.setattr(
        "epitaph.transcripts.ClaudeAdapter.default_root",
        lambda self: root,
    )
    assert main(["giveup"]) == 0
    out = capsys.readouterr().out
    assert "1 give-up transition(s), 1 drafted, 0 already recorded" in out
    store = TombstoneStore(repo)
    tomb = store.all()[0]
    assert tomb.rejected_by == "agent-gaveup"
    assert tomb.confidence == "candidate"
    assert "Redis cache layer" in tomb.attempt
    assert tomb.rejected_at == "2026-09-06"
    assert any(e.startswith("session claude:") for e in tomb.evidence)

    assert main(["giveup"]) == 0
    assert "0 drafted, 1 already recorded" in capsys.readouterr().out


def test_giveup_storeless_does_not_create(repo, tmp_path, capsys, monkeypatch):
    cwd = str(repo.resolve())
    root = _claude_dir(tmp_path, repo)
    _write_claude_session(root, repo, "s1", [
        _user("2026-09-06T10:00:00Z", "u", cwd=cwd),
        _assistant("2026-09-06T10:05:00Z", "dead end", cwd=cwd),
    ])
    monkeypatch.setattr(
        "epitaph.transcripts.ClaudeAdapter.default_root",
        lambda self: root,
    )
    assert main(["giveup"]) == 0
    out = capsys.readouterr().out
    assert "1 give-up transition(s)" in out
    assert "epitaph init" in out
    assert not TombstoneStore(repo).exists()


def test_giveup_limit(repo, tmp_path, capsys, monkeypatch):
    cwd = str(repo.resolve())
    root = _claude_dir(tmp_path, repo)
    records = [_user("2026-09-06T09:00:00Z", "u", cwd=cwd)]
    for i in range(5):
        records.append(_assistant("2026-09-06T09:%02d:00Z" % (10 + i), "dead end #%d" % i, cwd=cwd))
        records.append(_assistant("2026-09-06T09:%02d:30Z" % (10 + i), "continuing %d" % i, cwd=cwd))
    _write_claude_session(root, repo, "s1", records)
    monkeypatch.setattr(
        "epitaph.transcripts.ClaudeAdapter.default_root",
        lambda self: root,
    )
    assert main(["init"]) == 0
    assert main(["giveup", "--limit", "2"]) == 0
    assert "2 drafted" in capsys.readouterr().out
