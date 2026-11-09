"""Lightweight JSON-RPC MCP STDIO server and tool validation."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator

VERSION = "0.1.0"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
MCP_VERSION = "2025-06-18"


class ToolError(Exception):
    """A recoverable, user-facing tool execution failure."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "inputSchema": self.schema}


def schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def string(description: str, *, min_length: int = 1, max_length: int = 20000, **extras: Any) -> dict[str, Any]:
    return {"type": "string", "description": description, "minLength": min_length, "maxLength": max_length, **extras}


def rpc_error(code: int, message: str, request_id: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def sanitize_error(message: str) -> str:
    # Arbitrary upstream response bodies are not emitted verbatim to MCP clients.
    msg = message.replace("\n", " ").replace("\r", " ")
    return msg[:280]


class StdioServer:
    def __init__(self, name: str, tools: list[Tool]):
        self.name = name
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("重复的工具名称")
        for tool in tools:
            Draft202012Validator.check_schema(tool.schema)

    def dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        method = message.get("method")
        if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return rpc_error(-32600, "Invalid JSON-RPC request", request_id)
        if "id" not in message:
            # Initialization and cancellation notifications do not require a reply.
            return None
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return rpc_error(-32602, "Invalid params", request_id)
        if method == "initialize":
            version = params.get("protocolVersion")
            if not isinstance(version, str):
                return rpc_error(-32602, "protocolVersion must be a string", request_id)
            if version not in SUPPORTED_PROTOCOLS:
                version = MCP_VERSION
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.name, "version": VERSION},
            }}
        if method == "ping":
            result: Any = {}
        elif method == "tools/list":
            result = {"tools": [t.public() for t in self.tools.values()]}
        elif method == "resources/list":
            result = {"resources": []}
        elif method == "prompts/list":
            result = {"prompts": []}
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            if not isinstance(name, str) or name not in self.tools:
                return rpc_error(-32602, "Unknown tool", request_id)
            if not isinstance(args, dict):
                return rpc_error(-32602, "Tool arguments must be an object", request_id)
            tool = self.tools[name]
            errors = list(Draft202012Validator(tool.schema).iter_errors(args))
            if errors:
                details = "; ".join(e.message for e in errors[:3])
                return rpc_error(-32602, f"Tool arguments invalid: {details}", request_id)
            try:
                value = tool.handler(args)
                if isinstance(value, str):
                    payload = value
                else:
                    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
                result = {"content": [{"type": "text", "text": payload}], "isError": False}
            except (ToolError, ValueError, OSError) as exc:
                result = {"content": [{"type": "text", "text": sanitize_error(str(exc))}], "isError": True}
            except Exception:
                # Never include arbitrary upstream exception text or secrets.
                result = {"content": [{"type": "text", "text": "执行失败：服务内部错误"}], "isError": True}
        else:
            return rpc_error(-32601, "Method not found", request_id)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def serve(self) -> None:
        # MCP STDIO messages use UTF-8 even when the OS default encoding differs.
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                reply = rpc_error(-32700, "Parse error", None)
            else:
                if isinstance(request, dict):
                    reply = self.dispatch(request)
                else:
                    reply = rpc_error(-32600, "Invalid request", None)
            if reply is not None:
                sys.stdout.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
                sys.stdout.flush()
