"""模型输出解析：正则提取 <message>/<mood> 标签 + 流式增量扫描。

纯字符串处理，不依赖 Mem0/DeepSeek/配置，可独立单元测试。
从 chat.py 抽出，跨模块 API 去 `_` 前缀（模块边界取代单文件封装）。
"""

import re

from models import ModelOutput, ParsedMood


# ── 正则 ────────────────────────────────────────────────

_MESSAGE_CONTENT_RE = re.compile(r"<message\s*>(.*?)</message>", re.DOTALL)
_MOOD_RE = re.compile(
    r'<mood\s+'
    r'p="([+-]?\d*\.?\d+)"\s+'
    r'a="([+-]?\d*\.?\d+)"\s+'
    r'd="([+-]?\d*\.?\d+)"\s+'
    r'reason="([^"]*)"\s*>'
    r'(.*?)</mood>',
    re.DOTALL,
)
_MESSAGE_OPEN_RE = re.compile(r"<message\s*>")


# ── mood 提取 ────────────────────────────────────────────


def parse_mood(output: str) -> ParsedMood | None:
    """从输出中提取 <mood> 标签，无标签或 p/a/d 值非法时返回 None。

    返回 None 的两种情况：
    - 输出中没有 <mood> 标签
    - <mood> 标签存在但 p/a/d 无法解析为 float（格式错误）
    两种情况都应由调用方触发 repair 让模型重发。
    """
    m = _MOOD_RE.search(output)
    if m is None:
        return None
    try:
        return {
            "p": float(m.group(1)),
            "a": float(m.group(2)),
            "d": float(m.group(3)),
            "reason": m.group(4),
            "label": m.group(5).strip(),
        }
    except (ValueError, TypeError) as e:
        print(f"[Parse] mood 标签解析失败: {e}")
        return None


# ── 完整输出解析 ─────────────────────────────────────────


def parse_model_output(output: str) -> ModelOutput:
    """用正则提取 <message> 和 <mood> 标签，不依赖 XML 整体解析。

    模型输出中的非法 XML 字符（未转义 < > & 等）不会导致整体解析失败。
    """
    messages: list[str] = []
    for m in _MESSAGE_CONTENT_RE.finditer(output):
        text = m.group(1).strip()
        if text:
            messages.append(text)
    return {"messages": messages, "mood": parse_mood(output)}


def parse_for_repair(output: str) -> ModelOutput | None:
    """repair_until_valid 的 parse_func：无 <message> 时返回 None。"""
    parsed = parse_model_output(output)
    if not parsed.get("messages"):
        return None
    return parsed


# ── 流式增量扫描 ────────────────────────────────────────


def extract_complete_and_draft(buffer: str) -> tuple[list[str], str]:
    """从 buffer 中提取已闭合的 <message> 内容和未闭合的草稿。

    Returns:
        (complete_messages, draft)
        - complete_messages: 已闭合 <message> 标签的文本内容列表
        - draft: 最后一个未闭合 <message> 标签内的文本（不含标签本身）
    """
    messages: list[str] = []
    draft = ""
    pos = 0

    while True:
        m = _MESSAGE_OPEN_RE.search(buffer, pos)
        if m is None:
            break
        start = m.end()
        end = buffer.find("</message>", start)
        if end == -1:
            # 未闭合的 <message>，提取内容作为草稿
            draft = buffer[start:].strip()
            break
        content = buffer[start:end].strip()
        if content:
            messages.append(content)
        pos = end + len("</message>")

    return messages, draft
