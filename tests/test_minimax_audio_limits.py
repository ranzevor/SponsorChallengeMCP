import json
from pathlib import Path

import httpx
import pytest

from sponsor_mcp.core import StdioServer
from sponsor_mcp.http_client import JSONClient
from sponsor_mcp.minimax import tools


MIB = 1024 * 1024


def synthesize(monkeypatch, tmp_path, response):
    monkeypatch.setenv("MINIMAX_API_KEY", "local-audio-test-key")
    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    monkeypatch.setenv("SPONSOR_MCP_OUTPUT_DIR", str(tmp_path))

    def respond(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/t2a_v2"
        assert payload["output_format"] == "hex"
        assert payload["audio_setting"]["format"] == "mp3"
        return response

    client = JSONClient("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "https://api.minimax.cn",
                        transport=httpx.MockTransport(respond))
    server = StdioServer("minimax", tools(client))
    return server.dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "minimax_speak", "arguments": {
            "text": "你好<#99.99#>你好<#99.99#>你好<#99.99#>你好"}},
    })["result"]


def audio_response(size):
    audio = b"ID3" + b"\x00" * (size - 3)
    return httpx.Response(200, json={
        "base_resp": {"status_code": 0},
        "data": {"audio": audio.hex(), "status": 2},
    })


@pytest.mark.parametrize("size", [4 * MIB + 128, 8 * MIB])
def test_large_audio_within_limit_is_saved(monkeypatch, tmp_path, size):
    response = audio_response(size)
    assert len(response.content) > 8 * MIB
    result = synthesize(monkeypatch, tmp_path, response)
    assert result["isError"] is False
    output = json.loads(result["content"][0]["text"])
    path = Path(output["file"])
    assert path.parent == tmp_path
    assert output["bytes"] == path.stat().st_size == size
    with path.open("rb") as audio:
        assert audio.read(3) == b"ID3"


def test_audio_exceeding_limit_is_not_saved(monkeypatch, tmp_path):
    result = synthesize(monkeypatch, tmp_path, audio_response(8 * MIB + 1))
    assert result["isError"] is True
    assert "TTS 未返回有效音频数据或结果超限" in result["content"][0]["text"]
    assert list(tmp_path.iterdir()) == []


def test_oversized_tts_content_length_is_rejected(monkeypatch, tmp_path):
    response = httpx.Response(200, headers={"Content-Length": str(17 * MIB + 1)}, content=b"{}")
    result = synthesize(monkeypatch, tmp_path, response)
    assert result["isError"] is True
    assert "17 MiB" in result["content"][0]["text"]
    assert list(tmp_path.iterdir()) == []


def test_oversized_tts_without_content_length_is_rejected(monkeypatch, tmp_path):
    class LargeResponse(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(17):
                yield b" " * MIB
            yield b" "

    response = httpx.Response(200, stream=LargeResponse())
    result = synthesize(monkeypatch, tmp_path, response)
    assert result["isError"] is True
    assert "17 MiB" in result["content"][0]["text"]
    assert list(tmp_path.iterdir()) == []
