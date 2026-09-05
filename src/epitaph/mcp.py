"""Zero-dependency MCP server for tombstone (stdio, newline-delimited
JSON-RPC 2.0). Run with `python -m epitaph.mcp [--repo PATH]`.

Implements the MCP handshake (`initialize`, `notifications/initialized`,
`tools/list`, `tools/call`, plus `ping`) with two tools:
`check_nogo(attempt?, files?)` and `recent_tombstones(scope?, limit?)`.

Repository resolution order: --repo flag > EPITAPH_REPO env var >
walk up from the working directory. Fully local; no network calls.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .matcher import match_tombstones, normalized
from .render import format_matches
from .store import TombstoneStore

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "epitaph"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602

TOOLS = [
    {
        "name": "check_nogo",
        "description": (
            "Check whether an approach you are about to try, or the files you "
            "are about to touch, have tombstones in this repo — i.e. were "
            "previously tried and rejected or rolled back. Call this BEFORE "
            "implementing an approach that the codebase may have already "
            "buried. Tombstones are testimony, not verdicts: a match means "
            "'rejected on <date> for <reason>; safe to revisit when "
            "<retry_when>'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attempt": {
                    "type": "string",
                    "description": "The approach you are about to try, as a short phrase.",
                },
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths you are about to modify.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "recent_tombstones",
        "description": (
            "Browse this repo's ledger of rejected attempts, newest first, "
            "optionally filtered by a scope path. Useful when planning work "
            "in an area to see what has already failed there."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": "Optional file/symbol anchor to filter by.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max records to return (default 10).",
                },
            },
            "additionalProperties": False,
        },
    },
]


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class Server:
    def __init__(self, repo=None):
        self._repo = repo
        self._store = None

    @property
    def store(self) -> TombstoneStore:
        if self._store is None:
            root = self._repo or os.environ.get("EPITAPH_REPO") or "."
            self._store = TombstoneStore.find(root) or TombstoneStore(Path(root).resolve())
        return self._store

    def handle(self, msg):
        """One decoded JSON-RPC message -> response dict, or None (notification)."""
        if not isinstance(msg, dict):
            return _error(None, JSONRPC_INVALID_REQUEST, "Request must be an object")
        method = msg.get("method")
        msg_id = msg.get("id")
        if not isinstance(method, str) or not method:
            if msg_id is None:
                return None
            return _error(msg_id, JSONRPC_INVALID_REQUEST, "Missing method")
        if msg_id is None:
            # Notification: never respond, even for unknown methods.
            return None
        if method == "initialize":
            return _result(msg_id, self._initialize(msg.get("params")))
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._tools_call(msg_id, msg.get("params"))
        return _error(msg_id, JSONRPC_METHOD_NOT_FOUND, "Method not found: %s" % method)

    def _initialize(self, params):
        version = PROTOCOL_VERSION
        if isinstance(params, dict) and isinstance(params.get("protocolVersion"), str):
            version = params["protocolVersion"]
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": (
                "tombstone keeps this repo's ledger of rejected attempts. Call "
                "check_nogo before retrying an approach that may have been tried "
                "and rejected before; call recent_tombstones when planning work "
                "in an area. Tombstones are testimony, not verdicts — always "
                "check retry_when."
            ),
        }

    def _tools_call(self, msg_id, params):
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(msg_id, JSONRPC_INVALID_PARAMS, "tools/call requires params.name")
        name = params["name"]
        args = params.get("arguments")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _error(msg_id, JSONRPC_INVALID_PARAMS, "tools/call arguments must be an object")
        try:
            if name == "check_nogo":
                text = self._check_nogo(args)
            elif name == "recent_tombstones":
                text = self._recent(args)
            else:
                return _error(msg_id, JSONRPC_INVALID_PARAMS, "Unknown tool: %s" % name)
        except Exception as exc:  # tool failures surface as isError, not crashes
            return _result(
                msg_id,
                {
                    "content": [{"type": "text", "text": "tool error: %s" % exc}],
                    "isError": True,
                },
            )
        return _result(msg_id, {"content": [{"type": "text", "text": text}]})

    def _check_nogo(self, args):
        attempt = args.get("attempt")
        if attempt is not None and not isinstance(attempt, str):
            raise ValueError("'attempt' must be a string")
        files = args.get("files")
        if isinstance(files, str):
            files = [files]
        if files is not None:
            if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
                raise ValueError("'files' must be a list of strings")
        if not (attempt or "").strip() and not files:
            raise ValueError(
                "provide 'attempt' (text) and/or 'files' (paths you are about to modify)."
            )
        records = self.store.all()
        if not records:
            return (
                "no tombstones recorded in %s yet — nothing known against this "
                "attempt. Record rejections with `epitaph add`." % self.store.dir
            )
        return format_matches(
            match_tombstones(query=attempt or None, files=files or [], tombstones=records)
        )

    def _recent(self, args):
        scope = args.get("scope")
        if scope is not None and not isinstance(scope, str):
            raise ValueError("'scope' must be a string")
        limit = args.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            limit = 10
        records = self.store.all()
        if scope:
            needle = normalized(scope)
            records = [
                t
                for t in records
                if needle
                and any(needle in normalized(s) or normalized(s) in needle for s in t.scope)
            ]
        records.sort(key=lambda t: (t.rejected_at, t.id), reverse=True)
        records = records[:limit]
        if not records:
            return "no tombstones%s." % (
                " matching scope %r" % scope if scope else " recorded yet"
            )
        lines = ["%d tombstone(s):" % len(records)]
        for t in records:
            lines.append("")
            lines.append(
                "[%s/%s] %s  (%s)" % (t.confidence, t.status, t.id, t.rejected_at)
            )
            lines.append("  attempt: %s" % t.attempt)
            lines.append("  reason: %s" % (t.reason or "(none)"))
            if t.scope:
                lines.append("  scope: %s" % ", ".join(t.scope))
        return "\n".join(lines)


def process_line(line, server):
    """One raw stdin line -> raw response line, or None for no output."""
    if not line.strip():
        return None
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return json.dumps(_error(None, JSONRPC_PARSE_ERROR, "Parse error"))
    response = server.handle(msg)
    if response is None:
        return None
    return json.dumps(response, ensure_ascii=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m epitaph.mcp",
        description="tombstone MCP server (stdio, zero dependencies)",
    )
    parser.add_argument("--repo", default=None, help="repository root (default: cwd walk-up)")
    args = parser.parse_args(argv)
    server = Server(repo=args.repo)
    for raw in sys.stdin:
        out = process_line(raw, server)
        if out is not None:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
