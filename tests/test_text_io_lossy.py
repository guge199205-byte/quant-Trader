"""截断日志文件的读取容错。

2026-09-21 15:45 三个 agent 的 `log.jsonl` 被同时截到整 KiB 边界（110592 / 94208 字节），
其中两个的残尾**断在 UTF-8 多字节字符中间**。于是：

- `backend/services/agent_data._read_jsonl` 的 `for line in f` 抛 `UnicodeDecodeError`，
  它不是 `OSError`，下面的 `except OSError` 接不住 → `GET /api/agents/{agent}/logs` 整个 500，
  **同一文件里 4 条完好记录跟着一起消失**（前端只显示空对话流）。
- `backend/api_server.token_usage` 的 `read_text(encoding="utf-8")` 同样炸 → `/api/token-usage` 500。

这里钉住的是「读取端不许把整份文件陪葬」这条：坏字节退化成 U+FFFD → 该行
`json.JSONDecodeError` → 被各站点已有的分支跳过。**数据不动**：截断的残尾本来就解析不出来。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import agent_data
from backend.services.text_io import read_text_lossy


def _good_record(i: int) -> str:
    return json.dumps({"timestamp": f"2026-09-21T1{i}:00:00+08:00", "content": f"第{i}轮"}, ensure_ascii=False)


def _truncated_jsonl(tmp_path: Path) -> Path:
    """两条完整记录 + 一条断在汉字中间的残行（复刻 09-21 的现场）。

    残行**必须以多字节字符收尾**才复现得了线上那种文件：截断点落在 3 字节汉字的
    第 3 个字节上 → `'utf-8' codec can't decode bytes in position N-N+1:
    unexpected end of data`，与 09-21 15:45 两个 log.jsonl 的报错逐字相同。
    （截在 ASCII 的 `}` 上只会得到「JSON 不完整」，那是另一回事、不抛解码错误。）
    """
    p = tmp_path / "log.jsonl"
    body = (_good_record(1) + "\n" + _good_record(2) + "\n").encode("utf-8")
    partial = '{"content": "未写完的第三轮'.encode("utf-8")
    p.write_bytes(body + partial[:-1])  # 砍掉「轮」的最后一字节
    return p


def test_fixture_really_is_undecodable(tmp_path: Path):
    """先证明这个 fixture 真的复现了线上那种坏文件（否则下面的用例是假通过）。"""
    p = _truncated_jsonl(tmp_path)
    with pytest.raises(UnicodeDecodeError):
        p.read_text(encoding="utf-8")


def test_read_text_lossy_survives_truncated_tail(tmp_path: Path):
    text = read_text_lossy(_truncated_jsonl(tmp_path))

    lines = text.splitlines()
    assert json.loads(lines[0])["content"] == "第1轮"
    assert json.loads(lines[1])["content"] == "第2轮"


def test_read_jsonl_keeps_good_records_of_a_truncated_file(tmp_path: Path):
    """修复前这里抛 UnicodeDecodeError（= 端点 500、4 条好记录全丢）。"""
    rows = agent_data._read_jsonl(_truncated_jsonl(tmp_path))

    assert [r["content"] for r in rows] == ["第1轮", "第2轮"]


def test_read_text_lossy_passes_through_healthy_file(tmp_path: Path):
    """健康文件逐字节不变——容错不能顺带改正常路径。"""
    p = tmp_path / "ok.jsonl"
    p.write_text('{"a": "中文"}\n', encoding="utf-8")

    assert read_text_lossy(p) == '{"a": "中文"}\n'
