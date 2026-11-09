import json
import os
from urllib.parse import urlsplit

import httpx
import pytest

from sponsor_mcp import minimax, stepfun, vivix
from sponsor_mcp.core import ToolError
from sponsor_mcp.http_client import JSONClient


def client(monkeypatch, provider, base, handler):
    monkeypatch.setenv(provider.upper() + "_API_KEY", provider + "-local-test-key")
    return JSONClient(provider.upper() + "_API_KEY", provider.upper() + "_BASE_URL", base,
                      transport=httpx.MockTransport(handler))


def get_tool(tools, name):
    return next(t for t in tools if t.name == name)


def test_stepfun_request_with_image(monkeypatch):
    captured = []

    def mock(req):
        assert req.url.path == "/v1/chat/completions"
        captured.append(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "已读取"}}],
                                         "model": "step-5-preview", "usage": {"total_tokens": 10}})
    tool = get_tool(stepfun.tools(client(monkeypatch, "stepfun", "https://api.stepfun.com/v1", mock)), "stepfun_reason")
    out = tool.handler({"prompt": "看图", "image_url": "https://example.org/sample.png", "reasoning_effort": "high"})
    assert out["content"] == "已读取"
    assert captured[0]["reasoning_effort"] == "high"
    assert captured[0]["messages"][0]["content"][1]["type"] == "image_url"


def test_minimax_chat_and_tts(monkeypatch, tmp_path):
    monkeypatch.setenv("SPONSOR_MCP_OUTPUT_DIR", str(tmp_path))
    called = []

    def mock(req):
        called.append((req.url.path, json.loads(req.content)))
        if req.url.path == "/v1/chat/completions":
            return httpx.Response(200, json={"choices": [{"message": {"content": "答案"}, "finish_reason": "stop"}],
                                             "base_resp": {"status_code": 0}})
        return httpx.Response(200, json={"base_resp": {"status_code": 0},
                                         "data": {"audio": b"ID3-test".hex(), "status": 2},
                                         "extra_info": {"audio_format": "mp3"}})
    tools = minimax.tools(client(monkeypatch, "minimax", "https://api.minimax.cn", mock))
    assert get_tool(tools, "minimax_chat").handler({"prompt": "问题"})["content"] == "答案"
    result = get_tool(tools, "minimax_speak").handler({"text": "你好"})
    assert open(result["file"], "rb").read() == b"ID3-test"
    assert [entry[0] for entry in called] == ["/v1/chat/completions", "/v1/t2a_v2"]
    assert called[1][1]["stream"] is False


def test_minimax_business_error_raises(monkeypatch):
    def mock(req):
        return httpx.Response(200, json={"base_resp": {"status_code": 1004, "status_msg": "invalid"}})
    tool = get_tool(minimax.tools(client(monkeypatch, "minimax", "https://api.minimax.cn", mock)), "minimax_chat")
    with pytest.raises(ToolError, match="1004"):
        tool.handler({"prompt": "任务"})


def test_vivix_tasks_and_session(monkeypatch):
    called = []

    def mock(req):
        body = json.loads(req.content) if req.content else None
        called.append((req.method, req.url.path, body))
        path = req.url.path
        if path == "/v1/videos/generations" and req.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"video_id": "vid_123", "status": "queued"}})
        if path == "/v1/videos/generations/vid_123":
            return httpx.Response(200, json={"code": 0, "data": {"items": [{
                "video_id": "vid_123", "status": "succeeded", "playback_url": "https://example.org/play.m3u8"}]}})
        if path == "/v1/realtime-avatar/sessions" and req.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"session_id": "avatar-session-123",
                                                                         "control": {"url": "wss://example.org"},
                                                                         "delivery": {"media": {"trtc": {"token": "secret-trtc"}}}}})
        if path.endswith("/close"):
            return httpx.Response(200, json={"code": 0, "data": {"status": "closed"}})
        return httpx.Response(200, json={"code": 0, "data": {"items": []}})

    tools = vivix.tools(client(monkeypatch, "vivix", "https://api.vivix.ai", mock))
    task = get_tool(tools, "vivix_generate_video").handler({"prompt": "会说话的动画人物",
                          "first_frame_image": "https://example.org/character.png", "dialogue": "欢迎", "voice_id": "test-voice"})
    assert task["status"] == "queued"
    assert called[0][2]["model"] == "vivix-a1"
    status = get_tool(tools, "vivix_video_status").handler({"video_id": task["video_id"]})
    assert status["items"][0]["status"] == "succeeded"
    live = get_tool(tools, "vivix_start_avatar").handler({
        "persona": "会响应观众的角色", "first_frame_image": "https://example.org/character.png",
        "image_description": "一个动画人物", "voice_id": "test-voice"})
    assert live["session_id"] == "avatar-session-123"
    assert called[2][2]["avatars"][0]["visual"]["source_images"][0]["url"].startswith("https://")
    assert get_tool(tools, "vivix_close_avatar").handler({"session_id": live["session_id"]})["status"] == "closed"
    assert get_tool(tools, "vivix_list_characters").handler({}) == {"items": []}


def test_vivix_rejects_missing_or_ambiguous_voice(monkeypatch):
    def mock(req):
        raise AssertionError("should not call provider")
    tool = get_tool(vivix.tools(client(monkeypatch, "vivix", "https://api.vivix.ai", mock)), "vivix_generate_video")
    with pytest.raises(ToolError):
        tool.handler({"prompt": "test", "first_frame_image": "https://example.org/img.png"})
    with pytest.raises(ToolError):
        tool.handler({"prompt": "test", "first_frame_image": "https://example.org/img.png",
                      "dialogue": "hello", "audio_url": "https://example.org/audio.mp3"})


def test_provider_missing_key_makes_no_network_call(monkeypatch):
    monkeypatch.delenv("VIVIX_API_KEY", raising=False)
    def mock(req):
        raise AssertionError("network call was not expected")
    tool = get_tool(vivix.tools(JSONClient("VIVIX_API_KEY", "VIVIX_BASE_URL", "https://api.vivix.ai",
                                                transport=httpx.MockTransport(mock))), "vivix_video_status")
    with pytest.raises(ToolError, match="VIVIX_API_KEY"):
        tool.handler({"video_id": "vid_test"})
