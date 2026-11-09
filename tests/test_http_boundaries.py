import json

import httpx
import pytest

from sponsor_mcp.core import ToolError
from sponsor_mcp.http_client import JSONClient


@pytest.mark.parametrize("address", [
    "https://evil.example/v1",
    "http://api.stepfun.com/v1",
    "https://api.stepfun.com.evil.example/v1",
    "https://api.stepfun.com:444/v1",
    "https://user:password@api.stepfun.com/v1",
    "https://api.stepfun.com/v1?debug=1",
    "https://api.stepfun.com/v1/../other",
    "https://api.stepfun.com/v1%2fother",
])
def test_api_key_not_routed_to_unapproved_origin(monkeypatch, address):
    monkeypatch.setenv("STEPFUN_BASE_URL", address)
    monkeypatch.setenv("STEPFUN_API_KEY", "local-test-key")
    with pytest.raises(ValueError):
        JSONClient("STEPFUN_API_KEY", "STEPFUN_BASE_URL", "https://api.stepfun.com/v1")


def test_official_origin_and_fixed_path(monkeypatch):
    monkeypatch.setenv("STEPFUN_API_KEY", "local-test-key")
    monkeypatch.delenv("STEPFUN_BASE_URL", raising=False)
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    api = JSONClient("STEPFUN_API_KEY", "STEPFUN_BASE_URL", "https://api.stepfun.com/v1",
                     transport=httpx.MockTransport(responder))
    api.request("POST", "/chat/completions", body={"model": "step-5-preview"})
    assert calls[0].url.host == "api.stepfun.com"
    assert calls[0].url.path == "/v1/chat/completions"
    assert calls[0].headers["Authorization"] == "Bearer local-test-key"


@pytest.mark.parametrize("response", [
    lambda: httpx.Response(200, headers={"Content-Length": str(9 * 1024 * 1024)}, content=b"{}"),
    lambda: httpx.Response(200, content=b" " * (8 * 1024 * 1024 + 1)),
])
def test_response_body_bound(monkeypatch, response):
    monkeypatch.setenv("MINIMAX_API_KEY", "local-test-key")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    api = JSONClient("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "https://api.minimax.cn",
                     transport=httpx.MockTransport(lambda request: response()))
    with pytest.raises(ToolError, match="8 MiB"):
        api.request("POST", "/v1/chat/completions")


def test_invalid_json_does_not_expose_raw_body(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "local-test-key")
    api = JSONClient("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "https://api.minimax.cn",
                     transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"<secret>")))
    with pytest.raises(ToolError, match="有效 JSON") as exc:
        api.request("POST", "/v1/chat/completions")
    assert "secret" not in str(exc.value)
