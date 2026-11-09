"""MiniMax M series and speech-2.8 tools."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from .core import Tool, ToolError, schema, string
from .http_client import JSONClient, text_completion


MAX_AUDIO_BYTES = 8 * 1024 * 1024
# HEX doubles the audio size; allow separate space for JSON metadata.
MAX_TTS_RESPONSE_BYTES = MAX_AUDIO_BYTES * 2 + 1024 * 1024


def _output_dir() -> Path:
    configured = os.getenv("SPONSOR_MCP_OUTPUT_DIR", "").strip()
    root = Path(configured).expanduser().resolve() if configured else (Path.home() / "SponsorMCPOutputs").resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


def tools(client: JSONClient | None = None) -> list[Tool]:
    api = client or JSONClient("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "https://api.minimax.cn")

    def chat(args: dict) -> dict:
        messages = []
        if args.get("system_prompt"):
            messages.append({"role": "system", "content": args["system_prompt"]})
        messages.append({"role": "user", "content": args["prompt"]})
        payload = {
            "model": args.get("model", "MiniMax-M3.1-Flash"),
            "messages": messages,
            "stream": False,
            "reasoning_effort": args.get("reasoning_effort", "medium"),
            "max_completion_tokens": args.get("max_completion_tokens", 4096),
        }
        return text_completion(api.request("POST", "/v1/chat/completions", body=payload, envelope="minimax"))

    def speak(args: dict) -> dict:
        voice_setting = {"voice_id": args.get("voice_id", "male-qn-qingse"),
                         "speed": args.get("speed", 1.0), "vol": 1.0}
        if args.get("emotion"):
            voice_setting["emotion"] = args["emotion"]
        payload = {
            "model": args.get("model", "speech-2.8-turbo"),
            "text": args["text"],
            "stream": False,
            "output_format": "hex",
            "voice_setting": voice_setting,
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
        }
        result = api.request("POST", "/v1/t2a_v2", body=payload, envelope="minimax",
                             max_response_bytes=MAX_TTS_RESPONSE_BYTES)
        audio = (result.get("data") or {}).get("audio")
        if not isinstance(audio, str) or not audio or len(audio) > MAX_AUDIO_BYTES * 2:
            raise ToolError("TTS 未返回有效音频数据或结果超限")
        try:
            raw = bytes.fromhex(audio)
        except ValueError:
            raise ToolError("TTS 音频 HEX 数据无效") from None
        if not raw:
            raise ToolError("TTS 音频为空")
        path = _output_dir() / f"minimax-{uuid.uuid4().hex}.mp3"
        path.write_bytes(raw)
        return {"file": str(path), "bytes": len(raw), "format": "mp3",
                "extra_info": result.get("extra_info") or {}}

    return [
        Tool("minimax_chat", "使用 MiniMax M3.1-Flash/M3 执行推理、代码、Agent 文本任务。", schema({
            "prompt": string("用户任务"),
            "system_prompt": string("可选系统指令", max_length=4000),
            "model": {"type": "string", "enum": ["MiniMax-M3.1-Flash", "MiniMax-M3"]},
            "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high", "xhigh", "max"]},
            "max_completion_tokens": {"type": "integer", "minimum": 1, "maximum": 16384},
        }, ["prompt"]), chat),
        Tool("minimax_speak", "使用 MiniMax speech-2.8 生成 MP3 音频并写入本地成果目录。真实请求可能消耗额度。", schema({
            "text": string("要合成的台词（不超过 3000 字符）", max_length=3000),
            "voice_id": string("MiniMax 音色 ID，默认 male-qn-qingse", max_length=128),
            "model": {"type": "string", "enum": ["speech-2.8-turbo", "speech-2.8-hd", "speech-2.6-turbo", "speech-2.6-hd"]},
            "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0},
            "emotion": {"type": "string", "enum": ["happy", "sad", "angry", "fearful", "disgusted", "surprised", "neutral"]},
        }, ["text"]), speak),
    ]
