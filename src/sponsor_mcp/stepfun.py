"""StepFun Step 5 Preview multimodal MCP tools."""

from __future__ import annotations

from .core import Tool, schema, string, ToolError
from .http_client import JSONClient, text_completion


def tools(client: JSONClient | None = None) -> list[Tool]:
    api = client or JSONClient("STEPFUN_API_KEY", "STEPFUN_BASE_URL", "https://api.stepfun.com/v1")

    def call(args: dict) -> dict:
        message: dict = {"role": "user", "content": args["prompt"]}
        if args.get("image_url"):
            message["content"] = [
                {"type": "text", "text": args["prompt"]},
                {"type": "image_url", "image_url": {"url": args["image_url"]}},
            ]
        messages = []
        if args.get("system_prompt"):
            messages.append({"role": "system", "content": args["system_prompt"]})
        messages.append(message)
        payload = {
            "model": args.get("model", "step-5-preview"),
            "messages": messages,
            "stream": False,
            "reasoning_effort": args.get("reasoning_effort", "medium"),
            "max_tokens": args.get("max_tokens", 4096),
        }
        return text_completion(api.request("POST", "/chat/completions", body=payload))

    return [Tool("stepfun_reason", "使用 Step 5 Preview 进行推理、编码分析或图像理解；可选传入公开 HTTPS 图片 URL。",
                 schema({
                     "prompt": string("任务或问题"),
                     "image_url": string("图片 HTTPS URL，非必填", min_length=10, max_length=2000,
                                         pattern="^https://"),
                     "system_prompt": string("可选系统指令", min_length=1, max_length=4000),
                     "model": string("阶跃模型 ID，默认 step-5-preview", max_length=100),
                     "reasoning_effort": {"type": "string", "enum": ["low", "medium", "high"]},
                     "max_tokens": {"type": "integer", "minimum": 1, "maximum": 16384},
                 }, ["prompt"]), call)]
