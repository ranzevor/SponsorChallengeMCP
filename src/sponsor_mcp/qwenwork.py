"""Local office deliverables and live-interaction analysis for QwenWork MCP."""

from __future__ import annotations

import csv
import os
import re
import uuid
from collections import Counter
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt

from .core import Tool, ToolError, schema, string


def _dir() -> Path:
    configured = os.getenv("SPONSOR_MCP_OUTPUT_DIR", "").strip()
    root = Path(configured).expanduser().resolve() if configured else (Path.home() / "SponsorMCPOutputs").resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def _file(prefix: str, ext: str) -> Path:
    return _dir() / f"{prefix}-{uuid.uuid4().hex[:12]}.{ext}"


def _safe_csv(v: object) -> object:
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    val = str(v)
    # Prevent CSV formula injection when opened in Excel/other office software.
    if val.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + val
    return val


def tools() -> list[Tool]:
    def create_document(args: dict) -> dict:
        document = Document()
        section = document.sections[0]
        section.top_margin = Cm(2.2)
        section.bottom_margin = Cm(2.1)
        section.left_margin = section.right_margin = Cm(2.4)
        styles = document.styles
        styles["Normal"].font.size = Pt(10.5)
        title = document.add_heading(args["title"], 0)
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if args.get("subtitle"):
            paragraph = document.add_paragraph(args["subtitle"])
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for chapter in args["sections"]:
            document.add_heading(chapter["heading"], level=1)
            for paragraph in chapter["paragraphs"]:
                document.add_paragraph(paragraph)
            if chapter.get("bullets"):
                for point in chapter["bullets"]:
                    document.add_paragraph(point, style="List Bullet")
        path = _file("qwenwork-document", "docx")
        document.save(path)
        return {"file": str(path), "format": "docx", "sections": len(args["sections"])}

    def export_csv(args: dict) -> dict:
        headers = args["headers"]
        rows = args["rows"]
        if any(len(row) != len(headers) for row in rows):
            raise ToolError("数据行的列数与表头不一致")
        path = _file("qwenwork-data", "csv")
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([_safe_csv(x) for x in headers])
            for row in rows:
                writer.writerow([_safe_csv(v) for v in row])
        return {"file": str(path), "format": "csv", "rows": len(rows), "columns": len(headers)}

    def poll_report(args: dict) -> dict:
        votes = args["votes"]
        allowed = args["options"]
        labels = set(allowed)
        invalid = [v for v in votes if v not in labels]
        if invalid:
            raise ToolError(f"包含 {len(invalid)} 个不在候选项中的投票，请先清洗输入")
        counts = Counter(votes)
        total = len(votes)
        rows = []
        for name in allowed:
            count = counts[name]
            rows.append({"option": name, "votes": count, "percentage": round(count * 100 / total, 2) if total else 0.0})
        high = max(counts.values(), default=0)
        winners = [name for name in allowed if counts[name] == high] if total else []
        return {"topic": args["topic"], "total_votes": total, "results": rows, "winners": winners,
                "tied": len(winners) > 1}

    sections_schema = {
        "type": "array", "minItems": 1, "maxItems": 30,
        "items": {"type": "object", "additionalProperties": False,
                  "properties": {"heading": string("章节标题", max_length=200),
                                 "paragraphs": {"type": "array", "maxItems": 20,
                                                "items": string("段落正文", min_length=0, max_length=4000)},
                                 "bullets": {"type": "array", "maxItems": 30,
                                             "items": string("列表文字", max_length=300)}},
                  "required": ["heading", "paragraphs"]},
    }
    cell = {"anyOf": [{"type": "string", "maxLength": 12000}, {"type": "number"}, {"type": "boolean"}, {"type": "null"}]}
    return [
        Tool("qwenwork_create_document", "由千问办公规划内容，生成可直接交付的 DOCX 文档，保存在指定输出目录。",
             schema({"title": string("文档标题", max_length=200),
                     "subtitle": string("副标题", max_length=300),
                     "sections": sections_schema}, ["title", "sections"]), create_document),
        Tool("qwenwork_export_csv", "将结构化表格导出为 UTF-8 BOM CSV，可供 Excel 打开，防止公式注入。",
             schema({"headers": {"type": "array", "minItems": 1, "maxItems": 30,
                                  "items": string("列名", max_length=120)},
                     "rows": {"type": "array", "maxItems": 5000,
                              "items": {"type": "array", "maxItems": 30, "items": cell}}},
                    ["headers", "rows"]), export_csv),
        Tool("qwenwork_poll_report", "统计直播间投票结果并处理并列最高票，返回适合直接写入报告的结构化数据。",
             schema({"topic": string("投票主题", max_length=200),
                     "options": {"type": "array", "minItems": 2, "maxItems": 20, "uniqueItems": True,
                                 "items": string("候选项", max_length=200)},
                     "votes": {"type": "array", "maxItems": 10000,
                               "items": string("已收到的投票", max_length=200)}},
                    ["topic", "options", "votes"]), poll_report),
    ]
