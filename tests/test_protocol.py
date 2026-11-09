import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sponsor_mcp.__main__ import build_server
from sponsor_mcp.core import MCP_VERSION, SUPPORTED_PROTOCOLS


@pytest.mark.parametrize("provider,expected_tools", [
    ("stepfun", 1), ("minimax", 2), ("qwenwork", 3), ("vivix", 5),
])
def test_all_servers_initialize_and_list_tools(provider, expected_tools):
    server = build_server(provider)
    init = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1.0"}}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"]["tools"]
    listed = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert len(listed["result"]["tools"]) == expected_tools
    assert len({tool["name"] for tool in listed["result"]["tools"]}) == expected_tools
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in listed["result"]["tools"])
    assert server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_input_schema_and_unknown_methods_are_errors():
    server = build_server("stepfun")
    unknown = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "something/else"})
    assert unknown["error"]["code"] == -32601
    invalid = server.dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "stepfun_reason", "arguments": {"prompt": "x", "max_tokens": 0}}})
    assert invalid["error"]["code"] == -32602
    assert server.dispatch({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["result"] == {"resources": []}


@pytest.mark.parametrize("version", sorted(SUPPORTED_PROTOCOLS) + ["2099-01-01"])
def test_initialize_negotiates_string_versions(version):
    server = build_server("stepfun")
    response = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": version}})
    expected = version if version in SUPPORTED_PROTOCOLS else MCP_VERSION
    assert response["result"]["protocolVersion"] == expected


def test_stdio_rejects_invalid_versions_and_continues_serving():
    env = os.environ.copy()
    src_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src_root, env.get("PYTHONPATH", "")]))
    requests = []
    for version in ([], {}, None, True, 123):
        requests.append({"jsonrpc": "2.0", "id": len(requests) + 1, "method": "initialize",
                         "params": {"protocolVersion": version}})
    requests.append({"jsonrpc": "2.0", "id": 6, "method": "initialize", "params": {}})
    requests.append({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    requests.append({"jsonrpc": "2.0", "id": 8, "method": "initialize",
                     "params": {"protocolVersion": "2025-06-18"}})
    process = subprocess.run([sys.executable, "-m", "sponsor_mcp", "stepfun"],
                             input="".join(json.dumps(request) + "\n" for request in requests),
                             capture_output=True, text=True, env=env, timeout=15)
    assert process.returncode == 0, process.stderr
    assert process.stderr == ""
    responses = [json.loads(line) for line in process.stdout.splitlines()]
    assert [response["id"] for response in responses] == list(range(1, 9))
    assert all(response["error"]["code"] == -32602 for response in responses[:6])
    assert responses[6]["result"] == {}
    assert responses[7]["result"]["protocolVersion"] == "2025-06-18"


def test_stdio_interaction_is_clean_and_missing_key_safe():
    env = os.environ.copy()
    env.pop("STEPFUN_API_KEY", None)
    src_root = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src_root, env.get("PYTHONPATH", "")]))
    p = subprocess.Popen([sys.executable, "-m", "sponsor_mcp", "stepfun"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, env=env)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "stepfun_reason", "arguments": {"prompt": "你好"}}},
    ]
    stdout, stderr = p.communicate("".join(json.dumps(q) + "\n" for q in requests), timeout=15)
    assert p.returncode == 0, stderr
    responses = [json.loads(line) for line in stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3]
    assert responses[2]["result"]["isError"] is True
    assert "STEPFUN_API_KEY" in responses[2]["result"]["content"][0]["text"]
    assert "Traceback" not in stdout
