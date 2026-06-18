"""parsing.py 单元测试：parse_mood / parse_model_output / extract_complete_and_draft。

parsing.py 是纯字符串处理，不依赖 Mem0/DeepSeek/配置，无需 stub。
重点验证 mood 解析的"成功/缺失/非法"三种情况——
这是 B1 改造的核心：非法 mood 不再静默吞掉，而是返回 None 触发 repair。
"""

import sys
from pathlib import Path

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from parsing import (  # noqa: E402
    extract_complete_and_draft,
    parse_model_output,
    parse_mood,
)


# ── parse_mood ────────────────────────────────────────────


def test_parse_mood_valid() -> None:
    """完整合法的 mood 标签应解析出全部字段。"""
    out = '<mood p="-0.3" a="0.2" d="-0.1" reason="被调侃了">有点无语</mood>'
    mood = parse_mood(out)
    assert mood is not None
    assert mood["p"] == -0.3
    assert mood["a"] == 0.2
    assert mood["d"] == -0.1
    assert mood["reason"] == "被调侃了"
    assert mood["label"] == "有点无语"


def test_parse_mood_with_surrounding_text() -> None:
    """mood 标签前后有其他文本/标签时应仍能提取。"""
    out = (
        "<message>嗯</message>\n"
        '<mood p="0.5" a="0.8" d="0.3" reason="开心">好耶</mood>'
    )
    mood = parse_mood(out)
    assert mood is not None
    assert mood["p"] == 0.5
    assert mood["label"] == "好耶"


def test_parse_mood_positive_sign() -> None:
    """p/a/d 带正号应能解析。"""
    out = '<mood p="+0.5" a="+0.1" d="+0.0" reason="ok">平淡</mood>'
    mood = parse_mood(out)
    assert mood is not None
    assert mood["p"] == 0.5


def test_parse_mood_missing_tag() -> None:
    """没有 <mood> 标签 → None（触发 repair）。"""
    mood = parse_mood("只有普通文本，没有 mood 标签")
    assert mood is None


def test_parse_mood_tag_but_invalid_number() -> None:
    """有 <mood> 标签但 p/a/d 不是数字 → None（触发 repair，不抛异常）。"""
    out = '<mood p="高" a="0.2" d="-0.1" reason="...">无语</mood>'
    mood = parse_mood(out)
    assert mood is None


def test_parse_mood_tag_unclosed() -> None:
    """<mood> 标签未闭合 → None。"""
    out = '<mood p="0.5" a="0.2" d="0.1" reason="ok">好耶'
    mood = parse_mood(out)
    assert mood is None


# ── parse_model_output ────────────────────────────────────


def test_parse_model_output_both_present() -> None:
    """message 和 mood 都有时应同时提取。"""
    out = (
        '<mood p="0.5" a="0.3" d="0.1" reason="闲聊">开心</mood>'
        "<message>嘿嘿</message>"
    )
    parsed = parse_model_output(out)
    assert parsed["messages"] == ["嘿嘿"]
    assert parsed["mood"] is not None
    assert parsed["mood"]["label"] == "开心"


def test_parse_model_output_multiple_messages() -> None:
    """多个 <message> 标签全部提取。"""
    out = (
        "<message>第一条</message>"
        "<message>第二条</message>"
    )
    parsed = parse_model_output(out)
    assert parsed["messages"] == ["第一条", "第二条"]
    assert parsed["mood"] is None


def test_parse_model_output_empty_message_ignored() -> None:
    """空内容的 <message></message> 不计入列表。"""
    out = "<message>   </message><message>有内容</message>"
    parsed = parse_model_output(out)
    assert parsed["messages"] == ["有内容"]


def test_parse_model_output_nothing() -> None:
    """既无 message 也无 mood。"""
    parsed = parse_model_output("纯推理文本，没有任何标签")
    assert parsed["messages"] == []
    assert parsed["mood"] is None


def test_parse_for_repair_no_message_returns_none() -> None:
    """parse_for_repair：无 message 时返回 None（触发 repair）。"""
    from parsing import parse_for_repair
    assert parse_for_repair("无标签文本") is None


def test_parse_for_repair_with_message_returns_parsed() -> None:
    """parse_for_repair：有 message 时返回解析结果。"""
    from parsing import parse_for_repair
    result = parse_for_repair("<message>有</message>")
    assert result is not None
    assert result["messages"] == ["有"]


# ── extract_complete_and_draft ────────────────────────────


def test_extract_all_closed() -> None:
    """全部 <message> 已闭合 → complete 有值，draft 为空。"""
    buf = "<message>第一条</message><message>第二条</message>"
    complete, draft = extract_complete_and_draft(buf)
    assert complete == ["第一条", "第二条"]
    assert draft == ""


def test_extract_with_open_tail() -> None:
    """末尾有未闭合 <message> → 进 draft。"""
    buf = "<message>已闭合</message><message>未闭合的部分"
    complete, draft = extract_complete_and_draft(buf)
    assert complete == ["已闭合"]
    assert draft == "未闭合的部分"


def test_extract_only_open() -> None:
    """只有一个未闭合 <message> → complete 空，draft 有值。"""
    complete, draft = extract_complete_and_draft("<message>草稿内容")
    assert complete == []
    assert draft == "草稿内容"


def test_extract_no_message_tag() -> None:
    """没有 <message> 标签 → 都为空。"""
    complete, draft = extract_complete_and_draft("纯文本推理")
    assert complete == []
    assert draft == ""
