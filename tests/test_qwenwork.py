import csv
from pathlib import Path

from docx import Document
import pytest

from sponsor_mcp.__main__ import build_server


def call(server, name, args):
    return server.dispatch({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                            "params": {"name": name, "arguments": args}})["result"]


def test_document_and_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("SPONSOR_MCP_OUTPUT_DIR", str(tmp_path))
    server = build_server("qwenwork")
    import json
    result = call(server, "qwenwork_create_document", {
        "title": "直播复盘", "sections": [{"heading": "观众反馈", "paragraphs": ["测试了四个服务。"], "bullets": ["正常运行"]}]})
    assert not result["isError"]
    path = Path(json.loads(result["content"][0]["text"])["file"])
    assert path.parent == tmp_path
    document = Document(path)
    assert "直播复盘" in [p.text for p in document.paragraphs]
    assert any("测试了四个服务" in p.text for p in document.paragraphs)

    csvresult = call(server, "qwenwork_export_csv", {"headers": ["名称", "值"],
                     "rows": [["测试", "=SUM(A1:A2)"], ["正常", 12]]})
    assert not csvresult["isError"]
    datafile = Path(json.loads(csvresult["content"][0]["text"])["file"])
    with datafile.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[1][1].startswith("'=")


def test_poll_tie_and_invalid_votes():
    server = build_server("qwenwork")
    import json
    answer = call(server, "qwenwork_poll_report", {"topic": "最强模型", "options": ["A", "B"], "votes": ["B", "A"]})
    obj = json.loads(answer["content"][0]["text"])
    assert obj["winners"] == ["A", "B"]
    assert obj["tied"] is True
    bad = call(server, "qwenwork_poll_report", {"topic": "最强模型", "options": ["A", "B"], "votes": ["C"]})
    assert bad["isError"] is True


def test_bad_headers_and_unknown_extra_arguments():
    server = build_server("qwenwork")
    invalid = call(server, "qwenwork_export_csv", {"headers": ["A", "B"], "rows": [[1]]})
    assert invalid["isError"]
    unknown = server.dispatch({"jsonrpc": "2.0", "id": 20, "method": "tools/call",
                               "params": {"name": "qwenwork_export_csv",
                                          "arguments": {"headers": ["A"], "rows": [], "external_path": "/tmp/x"}}})
    assert unknown["error"]["code"] == -32602


def test_empty_output_dir_uses_default_home(monkeypatch, tmp_path):
    import json
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SPONSOR_MCP_OUTPUT_DIR", "")
    server = build_server("qwenwork")
    answer = call(server, "qwenwork_export_csv", {"headers": ["项目"], "rows": [["成功"]]})
    assert not answer["isError"]
    path = Path(json.loads(answer["content"][0]["text"])["file"])
    assert path.parent == tmp_path / "SponsorMCPOutputs"
