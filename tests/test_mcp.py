import io
import json

import pytest

from epitaph.mcp import Server, main as mcp_main, process_line
from epitaph.schema import Tombstone
from epitaph.store import TombstoneStore


def call(server, payload):
    line = process_line(json.dumps(payload), server)
    return None if line is None else json.loads(line)


@pytest.fixture
def server(tmp_path):
    store = TombstoneStore(tmp_path)
    store.create()
    store.add(
        Tombstone(
            id="ts-20260812-a3f2",
            attempt="Redis-based distributed lock",
            scope=["src/session/lock.py"],
            rejected_at="2026-08-12",
            rejected_by="human-review",
            reason="race window remained",
            evidence=["PR #412"],
            retry_when="with fencing tokens",
            status="active",
            confidence="approved",
        )
    )
    return Server(repo=str(tmp_path))


def test_initialize_handshake(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
        },
    )
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in resp["result"]["capabilities"]
    assert resp["result"]["serverInfo"]["name"] == "epitaph"


def test_initialize_defaults_protocol_version(server):
    resp = call(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp["result"]["protocolVersion"] == "2024-11-05"


def test_initialized_notification_gets_no_response(server):
    assert (
        process_line(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), server
        )
        is None
    )


def test_tools_list(server):
    resp = call(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = resp["result"]["tools"]
    assert [t["name"] for t in tools] == ["check_nogo", "recent_tombstones"]
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_tools_call_check_nogo_hit(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "check_nogo",
                "arguments": {"attempt": "maybe a redis lock would fix this"},
            },
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "1 tombstone(s) match" in text
    assert "ts-20260812-a3f2" in text
    assert "retry_when: with fencing tokens" in text
    assert "isError" not in resp["result"]


def test_tools_call_check_nogo_by_files(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "check_nogo", "arguments": {"files": ["src/session/lock.py"]}},
        },
    )
    assert "ts-20260812-a3f2" in resp["result"]["content"][0]["text"]


def test_tools_call_check_nogo_miss(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "check_nogo", "arguments": {"attempt": "quantum annealing"}},
        },
    )
    assert "no matching tombstones" in resp["result"]["content"][0]["text"]


def test_tools_call_check_nogo_requires_input(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "check_nogo", "arguments": {}},
        },
    )
    result = resp["result"]
    assert result["isError"] is True
    assert "provide" in result["content"][0]["text"]


def test_tools_call_recent_tombstones(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "recent_tombstones", "arguments": {"limit": 5}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "ts-20260812-a3f2" in text
    assert "Redis-based distributed lock" in text


def test_tools_call_recent_tombstones_scope_filter(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": "recent_tombstones",
                "arguments": {"scope": "src/nothing/here"},
            },
        },
    )
    assert "no tombstones" in resp["result"]["content"][0]["text"]


def test_parse_error_returns_error_with_null_id():
    resp = json.loads(process_line("{this is not json", Server(repo=".")))
    assert resp["error"]["code"] == -32700
    assert resp["id"] is None


def test_non_object_request_is_invalid():
    resp = json.loads(process_line("[1, 2, 3]", Server(repo=".")))
    assert resp["error"]["code"] == -32600


def test_missing_method():
    resp = json.loads(process_line(json.dumps({"jsonrpc": "2.0", "id": 9}), Server(repo=".")))
    assert resp["error"]["code"] == -32600


def test_unknown_method_is_method_not_found(server):
    resp = call(server, {"jsonrpc": "2.0", "id": 10, "method": "resources/list"})
    assert resp["error"]["code"] == -32601


def test_unknown_tool_is_invalid_params(server):
    resp = call(
        server,
        {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "nope"}},
    )
    assert resp["error"]["code"] == -32602


def test_tools_call_arguments_must_be_object(server):
    resp = call(
        server,
        {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "check_nogo", "arguments": "redis"},
        },
    )
    assert resp["error"]["code"] == -32602


def test_blank_line_is_ignored(server):
    assert process_line("   ", server) is None


def test_ping(server):
    resp = call(server, {"jsonrpc": "2.0", "id": 13, "method": "ping"})
    assert resp["result"] == {}


def test_main_stdio_roundtrip(tmp_path, monkeypatch, capsys):
    store = TombstoneStore(tmp_path)
    store.create()
    store.add(
        Tombstone(
            attempt="Kafka audit sink",
            rejected_at="2026-07-01",
            rejected_by="ci",
            reason="p99 blew up",
            retry_when="batch writer decoupled",
        )
    )
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "check_nogo", "arguments": {"attempt": "kafka audit"}},
            }
        ),
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    assert mcp_main(["--repo", str(tmp_path)]) == 0

    responses = [json.loads(l) for l in capsys.readouterr().out.strip().splitlines()]
    # the notification produced no output
    assert len(responses) == 3
    assert responses[0]["id"] == 0
    assert responses[0]["result"]["serverInfo"]["name"] == "epitaph"
    assert responses[1]["id"] == 1
    assert [t["name"] for t in responses[1]["result"]["tools"]] == [
        "check_nogo",
        "recent_tombstones",
    ]
    assert responses[2]["id"] == 2
    assert "Kafka audit sink" in responses[2]["result"]["content"][0]["text"]
