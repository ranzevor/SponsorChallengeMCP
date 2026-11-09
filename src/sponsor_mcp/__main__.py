"""CLI entrypoint for separate STDIO MCP servers."""

from __future__ import annotations

import argparse
import sys

from .core import StdioServer


def build_server(provider: str) -> StdioServer:
    if provider == "stepfun":
        from .stepfun import tools
    elif provider == "minimax":
        from .minimax import tools
    elif provider == "qwenwork":
        from .qwenwork import tools
    elif provider == "vivix":
        from .vivix import tools
    else:
        raise ValueError(f"未知厂商：{provider}")
    return StdioServer(f"sponsor-{provider}-mcp", tools())


def main() -> None:
    parser = argparse.ArgumentParser(description="B站 AI大玩家活动 MCP 服务")
    parser.add_argument("provider", choices=["stepfun", "minimax", "qwenwork", "vivix"])
    args = parser.parse_args()
    try:
        build_server(args.provider).serve()
    except (OSError, ValueError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
