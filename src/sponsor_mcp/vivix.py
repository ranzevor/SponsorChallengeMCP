"""Vivix A1 video generation and realtime avatar session MCP tools."""

from __future__ import annotations

from urllib.parse import quote

from .core import Tool, ToolError, schema, string
from .http_client import JSONClient


def _id(s: str) -> str:
    if not s or len(s) > 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in s):
        raise ToolError("资源 ID 含有非法字符")
    return quote(s, safe="")


def tools(client: JSONClient | None = None) -> list[Tool]:
    api = client or JSONClient("VIVIX_API_KEY", "VIVIX_BASE_URL", "https://api.vivix.ai")

    def tts_options(args: dict, *, realtime: bool = False) -> dict:
        provider = args.get("tts_provider")
        model = args.get("tts_model_id")
        if realtime and provider and provider != "qwen-audio-3.0-tts-flash_ws" and not model:
            raise ToolError("实时会话指定非默认 tts_provider 时必须提供匹配的 tts_model_id")
        return {key: args[key] for key in ("tts_provider", "tts_model_id") if key in args}

    def generate(args: dict) -> dict:
        dialogue = args.get("dialogue")
        audio = args.get("audio_url")
        if bool(dialogue) == bool(audio):
            raise ToolError("dialogue 和 audio_url 必须且只能填写一项")
        body = {
            "model": "vivix-a1",
            "prompt": args["prompt"],
            "first_frame_image": args["first_frame_image"],
            "resolution": args.get("resolution", "720p"),
        }
        if args.get("video_ratio"):
            body["video_ratio"] = args["video_ratio"]
        if dialogue:
            if not args.get("voice_id"):
                raise ToolError("使用 dialogue 时必须提供 voice_id")
            body["dialogue"] = dialogue
            body["voice_id"] = args["voice_id"]
            options = tts_options(args)
            if options:
                body["tts_config"] = options
        else:
            if args.get("tts_provider") or args.get("tts_model_id"):
                raise ToolError("tts_provider 和 tts_model_id 仅用于 dialogue 语音合成")
            body["audio"] = audio
        result = api.request("POST", "/v1/videos/generations", body=body, envelope="vivix")
        if not result.get("video_id"):
            raise ToolError("任务未返回 video_id")
        return {"video_id": result["video_id"], "status": result.get("status"),
                "note": "仅创建任务，使用 vivix_video_status 查询最终结果"}

    def video_status(args: dict) -> dict:
        video_id = _id(args["video_id"])
        data = api.request("GET", f"/v1/videos/generations/{video_id}", envelope="vivix")
        items = data.get("items")
        if not isinstance(items, list):
            raise ToolError("任务查询未返回 items")
        return {"items": [{
            "video_id": item.get("video_id"),
            "status": item.get("status"),
            "playback_url": item.get("playback_url"),
            "watch_error": item.get("watch_error"),
            "cost_time_ms": item.get("cost_time_ms"),
        } for item in items if isinstance(item, dict)]}

    def start_avatar(args: dict) -> dict:
        # Official Quickstart schema: create session, then connect RTC + WSS separately.
        body = {
            "model": "vivix-a1-stream",
            "output": {"aspect_ratio": args.get("aspect_ratio", "9:16"),
                       "resolution": args.get("resolution", "720p")},
            "avatars": [{
                "avatar_id": "host",
                "persona": args["persona"],
                "visual": {"source_images": [{
                    "source_image_id": "portrait",
                    "url": args["first_frame_image"],
                    "media_type": args.get("media_type", "image/png"),
                    "description": args["image_description"],
                }]},
            }],
            "pipeline_config": {"tts_config": {"tts_voice_id": args["voice_id"], **tts_options(args, realtime=True)}},
        }
        data = api.request("POST", "/v1/realtime-avatar/sessions", body=body, envelope="vivix")
        # Control/TRTC credentials remain in the response for the local playback client.
        if not data.get("session_id"):
            raise ToolError("未取得实时会话 ID")
        return {"session_id": data["session_id"],
                "control": data.get("control"), "delivery": data.get("delivery"),
                "status": data.get("status"),
                "note": "已创建计费会话；音视频播放及双向控制需连接 Vivix 官方 WSS/RTC 客户端。完成后调用 vivix_close_avatar。"}

    def close_avatar(args: dict) -> dict:
        session_id = _id(args["session_id"])
        return api.request("POST", f"/v1/realtime-avatar/sessions/{session_id}/close",
                           body={"close_reason": "user_requested"}, envelope="vivix")

    def list_characters(args: dict) -> dict:
        params = {"page_size": args.get("page_size", 50)}
        if args.get("page_token"):
            params["page_token"] = args["page_token"]
        return api.request("GET", "/v1/characters", params=params, envelope="vivix")

    return [
        Tool("vivix_generate_video", "用 Vivix-A1 以角色首帧图片+台词/音频提交异步视频生成任务。", schema({
            "prompt": string("角色动作、镜头和内容描述", max_length=3000),
            "first_frame_image": string("可公开访问的角色首帧 HTTPS 图片 URL", max_length=2000, pattern="^https://"),
            "dialogue": string("需要角色朗读的文本", max_length=4000),
            "voice_id": string("与语音提供商匹配的音色 ID 或 Vivix 克隆音色 ID", max_length=128),
            "tts_provider": string("dialogue 的语音提供商；省略时使用 ElevenLabs，克隆音色自动匹配", max_length=128),
            "tts_model_id": string("与语音提供商匹配的 TTS 模型 ID", max_length=128),
            "audio_url": string("已上传的音频 HTTPS URL，与 dialogue 二选一", max_length=2000, pattern="^https://"),
            "video_ratio": {"type": "string", "enum": ["16:9", "9:16"]},
            "resolution": {"type": "string", "enum": ["720p", "480p"]},
        }, ["prompt", "first_frame_image"]), generate),
        Tool("vivix_video_status", "查询 Vivix 异步视频任务状态与完成后的播放 URL。", schema({
            "video_id": string("视频任务 ID", max_length=128, pattern="^[A-Za-z0-9_-]+$")}, ["video_id"]), video_status),
        Tool("vivix_start_avatar", "启动 Vivix-A1 实时互动角色会话（会消耗会话资源）；需配合官方 RTC 播放端。", schema({
            "persona": string("角色人设与行为规则", max_length=5000),
            "first_frame_image": string("角色素材 HTTPS URL", max_length=2000, pattern="^https://"),
            "image_description": string("图片中角色/服装/场景的真实视觉描述", max_length=1000),
            "voice_id": string("内置 Qwen、其他提供商或 Vivix 克隆音色 ID", max_length=128),
            "tts_provider": string("语音提供商；内置 Qwen 和克隆音色可省略，其他提供商需同时指定模型", max_length=128),
            "tts_model_id": string("与语音提供商匹配的 TTS 模型 ID", max_length=128),
            "media_type": {"type": "string", "enum": ["image/png", "image/jpeg", "image/webp"]},
            "aspect_ratio": {"type": "string", "enum": ["9:16", "16:9"]},
            "resolution": {"type": "string", "enum": ["720p", "480p"]},
        }, ["persona", "first_frame_image", "image_description", "voice_id"]), start_avatar),
        Tool("vivix_close_avatar", "关闭 Vivix 实时互动角色会话，停止持续生成。", schema({
            "session_id": string("实时会话 ID", max_length=128, pattern="^[A-Za-z0-9_-]+$")}, ["session_id"]), close_avatar),
        Tool("vivix_list_characters", "读取 Vivix 工作区中已保存的角色配置摘要。", schema({
            "page_size": {"type": "integer", "minimum": 1, "maximum": 200},
            "page_token": string("上页返回的游标", max_length=1000),
        }), list_characters),
    ]
