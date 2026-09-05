"""Vendor-neutral transcript reading for session give-up detection.

epitaph must stay vendor-agnostic: adapters are discovered by format, not by
asking the user which agent they use. Each adapter knows (1) the local root
where its vendor stores session logs and (2) how to prove a transcript
belongs to the audited repo (cwd fields inside the records) — a transcript
that cannot be attributed is skipped.

Parsing mirrors yield-audit's adapter contract: key-based and defensive.
Unknown record types, missing fields, and malformed lines are skipped, never
trusted. Only text is interpreted — plus edit-tool file paths, which become
scope anchors for the drafted tombstone.

Grounding (observed 2026-09, client versions 2.1.222–2.1.260 / codex-rs):

- Claude Code: ``~/.claude/projects/<munged-cwd>/<session>.jsonl``. Records
  carry ``type`` (``user``/``assistant``/``system``), ``sessionId``,
  ``timestamp``, ``cwd``, ``isSidechain``. ``message.content`` is a plain
  string (often user) or a list of blocks (``text`` blocks give text;
  ``tool_use`` blocks give ``name``/``input.file_path`` for edits).
- Codex CLI: ``~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl``. Records carry
  ``type`` (``session_meta``, ``response_item``, ...) and ``payload``.
  ``session_meta.payload`` holds ``id``/``cwd``; ``response_item.payload``
  of type ``message`` holds ``role`` and Chat-Completions-style ``content``
  with ``input_text``/``output_text`` blocks.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

EDIT_TOOLS = {"Write", "Edit", "NotebookEdit", "MultiEdit"}

# Deterministic give-up phrases. These are *candidates for human review*, not
# verdicts — recall-leaning is fine, precision comes from `epitaph review`.
GIVEUP_RES = (
    re.compile(
        r"i'?ll try (?:a |an |another |different |new |a different |a new |another new )?"
        r"(?:approach|way|strategy|method|angle|tack)\b",
        re.I,
    ),
    re.compile(
        r"let'?s try (?:a |an |another |different |new |a different |a new )?"
        r"(?:approach|way|strategy|method)\b",
        re.I,
    ),
    re.compile(r"(?:i'?ll|we'?ll|should) (?:switch|pivot) to (?:a|another|different)", re.I),
    re.compile(r"that (?:didn'?t|did not) work", re.I),
    re.compile(r"this (?:isn'?t|is not) working", re.I),
    re.compile(r"(?:doesn'?t|does not) seem to work", re.I),
    re.compile(r"scrapping (?:this|the|that) (?:approach|method|strategy|attempt|idea)", re.I),
    re.compile(r"(?:starting|start) over with (?:a|another|different)", re.I),
    re.compile(r"back to the drawing board", re.I),
    re.compile(r"\bgive up(?: on (?:this|the|that))?\b", re.I),
    re.compile(r"\bdead end\b", re.I),
    # Korean sessions
    re.compile(r"다른 (?:방법|접근|접근법|전략|방식)"),
    re.compile(r"(?:이|그|해당) 방법(?:으로|으로는) (?:안|않)"),
    re.compile(r"처음부터 다시"),
    re.compile(r"포기하(?:자|겠습니다|고)"),
)


@dataclass
class Message:
    role: str          # "user" | "assistant"
    session_id: str    # namespaced "<vendor>:<raw id>"
    ts: str            # raw ISO-8601 timestamp
    text: str          # concatenated text blocks ("" when none)
    edited_files: list = field(default_factory=list)  # repo-relative, session-to-date


@dataclass
class GiveUpEvent:
    vendor: str
    session_id: str
    ts: str
    text: str              # the give-up statement
    previous_assistant: str  # what the agent was doing right before
    user_request: str        # nearest preceding user message
    edited_files: list
    transcript: str          # evidence: file the event came from


def is_giveup_text(text: str) -> bool:
    if not text:
        return False
    return any(rx.search(text) for rx in GIVEUP_RES)


def _real(path) -> str:
    try:
        return os.path.realpath(str(path))
    except OSError:
        return str(path)


def _repo_relative(file_path, repo_real: str):
    if not isinstance(file_path, str) or not file_path:
        return None
    rel = os.path.relpath(_real(file_path), repo_real)
    if rel.startswith("..") or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, "/")


def _iso_date(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return date.today().isoformat()


class BaseAdapter:
    """Contract: discover files, stream Messages attributed to `repo`."""

    name = "adapter"

    def default_root(self) -> Path:
        raise NotImplementedError

    def iter_files(self, root: Path, repo_real: str) -> list:
        out = []
        if not root.is_dir():
            return out
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if filename.endswith(".jsonl"):
                    out.append(Path(dirpath) / filename)
        return sorted(out)

    def iter_messages(self, path: Path, repo_real: str):
        """Yield Message objects; stop early once the file proves it is
        about a different repository."""
        raise NotImplementedError


class ClaudeAdapter(BaseAdapter):
    name = "claude"

    def default_root(self) -> Path:
        return Path.home() / ".claude" / "projects"

    def iter_files(self, root: Path, repo_real: str) -> list:
        # Sessions for a cwd live under a dir named after it with path
        # separators replaced by '-': a 100x shortcut over walking every
        # project's logs.
        candidate = root / re.sub(r"[/\\:]", "-", repo_real)
        if candidate.is_dir():
            files = sorted(candidate.glob("*.jsonl"))
            if files:
                return files
        return super().iter_files(root, repo_real)

    def iter_messages(self, path: Path, repo_real: str):
        session_id = ""
        cwd_ok = None
        edited = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get("isSidechain") is True:
                    continue
                rtype = record.get("type")
                if rtype not in ("user", "assistant"):
                    continue
                sid = record.get("sessionId") or record.get("session_id")
                if not isinstance(sid, str) or not sid:
                    continue
                ts = record.get("timestamp")
                if not isinstance(ts, str) or not ts:
                    continue
                cwd = record.get("cwd")
                if cwd_ok is None and isinstance(cwd, str) and cwd:
                    if _real(cwd) != repo_real:
                        return  # this file is about another repository
                    cwd_ok = True
                if sid != session_id:
                    session_id = sid
                    edited = []
                text, edits = _claude_content(record.get("message"), repo_real)
                edited.extend(e for e in edits if e not in edited)
                yield Message(
                    role=rtype,
                    session_id="%s:%s" % (self.name, sid),
                    ts=ts,
                    text=text,
                    edited_files=list(edited),
                )


def _claude_content(message, repo_real: str):
    """(text, repo-relative edited files) from a claude message envelope."""
    if not isinstance(message, dict):
        return "", []
    content = message.get("content")
    texts, edits = [], []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                texts.append(item["text"])
            elif item.get("type") == "tool_use" and item.get("name") in EDIT_TOOLS:
                tool_input = item.get("input")
                if isinstance(tool_input, dict):
                    rel = _repo_relative(
                        tool_input.get("file_path") or tool_input.get("notebook_path"),
                        repo_real,
                    )
                    if rel:
                        edits.append(rel)
    return "\n".join(t for t in texts if t), edits


class CodexAdapter(BaseAdapter):
    name = "codex"

    def default_root(self) -> Path:
        return Path.home() / ".codex" / "sessions"

    def iter_messages(self, path: Path, repo_real: str):
        session_id = ""
        cwd_ok = False
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                rtype = record.get("type")
                payload = record.get("payload")
                if rtype == "session_meta" and isinstance(payload, dict):
                    if _real(payload.get("cwd") or "") != repo_real:
                        return
                    sid = payload.get("id")
                    if isinstance(sid, str) and sid:
                        session_id = sid
                    cwd_ok = True
                    continue
                if rtype != "response_item" or not cwd_ok or not isinstance(payload, dict):
                    continue
                if payload.get("type") != "message":
                    continue
                role = payload.get("role")
                ts = record.get("timestamp")
                if role not in ("user", "assistant") or not isinstance(ts, str):
                    continue
                content = payload.get("content")
                texts = []
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") in ("input_text", "output_text"):
                            if isinstance(item.get("text"), str):
                                texts.append(item["text"])
                yield Message(
                    role=role,
                    session_id="%s:%s" % (self.name, session_id),
                    ts=ts,
                    text="\n".join(texts),
                )


ADAPTERS = (ClaudeAdapter(), CodexAdapter())


def find_giveup_events(repo, claude_root=None, codex_root=None):
    """All give-up events across discovered transcript formats, newest first.

    Roots are injectable for tests; in production each adapter reads its own
    default location, and a missing root just means that format is absent.
    """
    repo = Path(repo).resolve()
    repo_real = _real(repo)
    events = []
    seen = set()
    for adapter in ADAPTERS:
        root = {"claude": claude_root, "codex": codex_root}.get(adapter.name) or adapter.default_root()
        root = Path(root)
        if not root.is_dir():
            continue
        for path in adapter.iter_files(root, repo_real):
            last_user = ""
            last_assistant = ""
            for msg in adapter.iter_messages(path, repo_real):
                if msg.role == "user":
                    if msg.text.strip():
                        last_user = msg.text.strip()
                    continue
                if msg.text.strip():
                    if is_giveup_text(msg.text):
                        key = (adapter.name, msg.session_id, msg.ts)
                        if key not in seen:
                            seen.add(key)
                            events.append(
                                GiveUpEvent(
                                    vendor=adapter.name,
                                    session_id=msg.session_id,
                                    ts=msg.ts,
                                    text=msg.text.strip(),
                                    previous_assistant=last_assistant,
                                    user_request=last_user,
                                    edited_files=list(msg.edited_files),
                                    transcript=path.name,
                                )
                            )
                    last_assistant = msg.text.strip()
    events.sort(key=lambda e: e.ts, reverse=True)
    return events


def draft_tombstone(event: GiveUpEvent):
    """Candidate record for one give-up event (local import: no cycles)."""
    from .schema import Tombstone

    snippet = event.previous_assistant or event.user_request
    attempt = " ".join(snippet.split())[:120] or "unnamed approach"
    reason = (
        " ".join(event.text.split())[:300]
        + " (auto-drafted from a %s session; correct attempt/reason during review)" % event.vendor
    )
    return Tombstone(
        attempt=attempt,
        scope=sorted(set(event.edited_files)),
        rejected_at=_iso_date(event.ts),
        rejected_by="agent-gaveup",
        reason=reason,
        evidence=[
            "session %s" % event.session_id,
            "transcript %s" % event.transcript,
            "at %s" % event.ts,
        ],
        retry_when="",
        status="active",
        confidence="candidate",
    )
