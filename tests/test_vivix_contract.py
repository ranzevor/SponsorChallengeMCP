import json

import httpx
import pytest

from sponsor_mcp.core import StdioServer
from sponsor_mcp.http_client import JSONClient
from sponsor_mcp.vivix import tools


@pytest.fixture
def dispatch(monkeypatch):
    monkeypatch.setenv("VIVIX_API_KEY", "local-contract-test-key")
    monkeypatch.delenv("VIVIX_BASE_URL", raising=False)
    requests = []

    def respond(request):
        requests.append((request.url.path, json.loads(request.content)))
        data = ({"session_id": "avatar-session-123", "status": "active"}
                if request.url.path.endswith("/sessions")
                else {"video_id": "vid_123", "status": "queued"})
        return httpx.Response(200, json={"code": 0, "data": data})

    server = StdioServer("vivix", tools(JSONClient(
        "VIVIX_API_KEY", "VIVIX_BASE_URL", "https://api.vivix.ai",
        transport=httpx.MockTransport(respond))))

    def call(name, **arguments):
        return server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": name, "arguments": arguments}})

    return call, requests


def avatar(**options):
    return {"persona": "A friendly host", "first_frame_image": "https://example.org/host.png",
            "image_description": "A host in a studio", "voice_id": "longanhuan_v3.6", **options}


def video(**options):
    return {"prompt": "The host welcomes viewers", "first_frame_image": "https://example.org/host.png",
            "dialogue": "Welcome", "voice_id": "JBFqnCBsd6RMkjVDRZzb", **options}


def test_realtime_elevenlabs_configuration(dispatch):
    call, requests = dispatch
    response = call("vivix_start_avatar", **avatar(
        voice_id="JBFqnCBsd6RMkjVDRZzb", tts_provider="elevenlabs", tts_model_id="eleven_v3"))
    assert response["result"]["isError"] is False
    assert requests[0][0] == "/v1/realtime-avatar/sessions"
    assert requests[0][1]["pipeline_config"]["tts_config"] == {
        "tts_voice_id": "JBFqnCBsd6RMkjVDRZzb", "tts_provider": "elevenlabs", "tts_model_id": "eleven_v3"}


def test_video_configuration_is_at_root(dispatch):
    call, requests = dispatch
    response = call("vivix_generate_video", **video(
        voice_id="longanhuan_v3.6", tts_provider="qwen-audio-3.0-tts-flash_ws",
        tts_model_id="qwen-audio-3.0-tts-flash"))
    assert response["result"]["isError"] is False
    body = requests[0][1]
    assert requests[0][0] == "/v1/videos/generations"
    assert body["voice_id"] == "longanhuan_v3.6"
    assert body["tts_config"] == {"tts_provider": "qwen-audio-3.0-tts-flash_ws",
                                  "tts_model_id": "qwen-audio-3.0-tts-flash"}
    assert "pipeline_config" not in body


@pytest.mark.parametrize("voice_id", ["longanhuan_v3.6", "voice_cloned123"])
def test_realtime_default_and_clone_do_not_override_provider(dispatch, voice_id):
    call, requests = dispatch
    assert call("vivix_start_avatar", **avatar(voice_id=voice_id))["result"]["isError"] is False
    assert requests[0][1]["pipeline_config"]["tts_config"] == {"tts_voice_id": voice_id}


@pytest.mark.parametrize("voice_id", ["JBFqnCBsd6RMkjVDRZzb", "voice_cloned123"])
def test_video_default_and_clone_do_not_override_provider(dispatch, voice_id):
    call, requests = dispatch
    assert call("vivix_generate_video", **video(voice_id=voice_id))["result"]["isError"] is False
    assert "tts_config" not in requests[0][1]


def test_realtime_other_provider_requires_model_before_request(dispatch):
    call, requests = dispatch
    response = call("vivix_start_avatar", **avatar(tts_provider="elevenlabs"))
    assert response["result"]["isError"] is True
    assert "tts_model_id" in response["result"]["content"][0]["text"]
    assert requests == []


def test_future_provider_and_model_are_forwarded(dispatch):
    call, requests = dispatch
    assert call("vivix_start_avatar", **avatar(
        tts_provider="new-provider", tts_model_id="new-model"))["result"]["isError"] is False
    assert requests[0][1]["pipeline_config"]["tts_config"]["tts_provider"] == "new-provider"


def test_square_avatar_is_rejected_without_http(dispatch):
    call, requests = dispatch
    assert call("vivix_start_avatar", **avatar(aspect_ratio="1:1"))["error"]["code"] == -32602
    assert requests == []


@pytest.mark.parametrize("ratio", ["9:16", "16:9"])
def test_supported_avatar_ratios(dispatch, ratio):
    call, requests = dispatch
    assert call("vivix_start_avatar", **avatar(aspect_ratio=ratio))["result"]["isError"] is False
    assert requests[0][1]["output"]["aspect_ratio"] == ratio


def test_audio_input_rejects_inapplicable_tts_settings(dispatch):
    call, requests = dispatch
    args = video(tts_provider="elevenlabs", audio_url="https://example.org/audio.mp3")
    del args["dialogue"]
    response = call("vivix_generate_video", **args)
    assert response["result"]["isError"] is True
    assert requests == []
