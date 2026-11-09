"""Fixed-origin API clients with bounded responses and provider error checking."""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit
from typing import Any

import httpx

from .core import ToolError


class JSONClient:
    def __init__(self, key_env: str, base_env: str, default_base: str, *, transport: httpx.BaseTransport | None = None):
        self.key_env = key_env
        self.key = os.getenv(key_env, "").strip()
        self.base = os.getenv(base_env, default_base).rstrip("/")
        parsed = urlsplit(self.base)
        official = urlsplit(default_base)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (parsed.scheme != "https" or parsed.hostname != official.hostname
                or parsed.username is not None or parsed.password is not None
                or port not in (None, 443) or parsed.query or parsed.fragment
                or ".." in parsed.path or "\\" in parsed.path or "%" in parsed.path):
            raise ValueError(f"{base_env} 只允许所选厂商官方 HTTPS API 主机及有效路径")
        self.transport = transport

    def request(self, method: str, path: str, *, body: dict | None = None, params: dict | None = None,
                envelope: str = "none", max_response_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
        if not self.key:
            raise ToolError(f"未配置 {self.key_env}，未执行远端请求")
        if not path.startswith("/") or path.startswith("//") or ".." in path or "://" in path:
            raise ToolError("API 路径非法")
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        limit = f"{max_response_bytes / (1024 * 1024):g} MiB"
        try:
            with httpx.Client(timeout=httpx.Timeout(35.0, connect=10.0), follow_redirects=False,
                              transport=self.transport, trust_env=self.transport is None) as client:
                with client.stream(method.upper(), self.base + path, headers=headers, json=body,
                                   params=params) as resp:
                    if resp.status_code >= 300:
                        raise ToolError(f"上游 HTTP {resp.status_code}，未取得有效业务结果")
                    declared = resp.headers.get("content-length")
                    if declared and declared.isdigit() and int(declared) > max_response_bytes:
                        raise ToolError(f"上游结果超过 {limit} 限额")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > max_response_bytes:
                            raise ToolError(f"上游结果超过 {limit} 限额")
                        chunks.append(chunk)
                    data = json.loads(b"".join(chunks))
        except httpx.TimeoutException:
            raise ToolError("上游请求超时") from None
        except httpx.RequestError:
            raise ToolError("无法连接上游 API") from None
        except (ValueError, UnicodeDecodeError):
            raise ToolError("上游返回的不是有效 JSON") from None
        if not isinstance(data, dict):
            raise ToolError("上游返回格式异常")
        if envelope == "vivix":
            if data.get("code") != 0:
                raise ToolError(f"Vivix 业务失败，错误码 {data.get('code', 'unknown')}")
            result = data.get("data")
            if not isinstance(result, dict):
                raise ToolError("Vivix data 对象缺失")
            if result.get("error_code", 0) not in (None, 0):
                raise ToolError(f"Vivix 任务失败，错误码 {result.get('error_code')}")
            return result
        if envelope == "minimax":
            status = data.get("base_resp")
            if isinstance(status, dict) and status.get("status_code", 0) != 0:
                raise ToolError(f"MiniMax 业务失败，错误码 {status.get('status_code')}")
        if "error" in data and not data.get("choices"):
            raise ToolError("上游 API 返回错误")
        return data


def text_completion(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ToolError("模型未返回有效 choices")
    msg = choices[0].get("message", {})
    if not isinstance(msg, dict):
        raise ToolError("模型 message 格式异常")
    return {
        "model": data.get("model"),
        "content": msg.get("content") or "",
        "finish_reason": choices[0].get("finish_reason"),
        "usage": data.get("usage") or {},
    }
