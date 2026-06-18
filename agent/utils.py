"""通用工具：日期时间辅助 + 文本/spoken_by/person_id 处理。"""

import ast
import re
from datetime import datetime, timedelta
from typing import cast

from config import LOCAL_TZ
from models import HistoryMsg


# ── 日期时间 ────────────────────────────────────────────


def settlement_boundary() -> datetime:
    """返回结算边界：今天 2:00 还是昨天 2:00"""
    now = datetime.now(LOCAL_TZ)
    boundary = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if now < boundary:
        boundary -= timedelta(days=1)
    return boundary


def now_minute() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat()


def parse_dt(text: str | None) -> datetime | None:
    if not text:
        return None
    raw = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


# ── 文本 / spoken_by / person_id ────────────────────────


def _str_list(x: object) -> list[str]:
    """安全地将未知值转为 list[str]。"""
    if isinstance(x, list):
        items = cast(list[object], x)
        return [str(i) for i in items]
    return []


def split_spoken_by(spoken_by: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """把来源统一存成数组；字符串优先按 Python 字面量解析。"""
    parts: list[str] = []
    if isinstance(spoken_by, (list, tuple)):
        parts = [str(x).strip() for x in spoken_by]
    else:
        text = str(spoken_by or "").strip()
        if not text:
            return []
        try:
            value: object = ast.literal_eval(text)
            if isinstance(value, str):
                parts = [value.strip()]
            elif isinstance(value, (list, tuple)):
                parts = _str_list(cast(object, value))
                parts = [p.strip() for p in parts]
            else:
                parts = [text]
        except (SyntaxError, ValueError):
            parts = [text]
    return [p for p in parts if p]


def format_spoken_by(spoken_by: str | list[str] | tuple[str, ...] | None) -> str:
    """把来源数组格式化给提示词/日志显示。"""
    sources = split_spoken_by(spoken_by)
    return "、".join(sources) if sources else "未知"


def person_id(user_id: str, sender_id: str | None) -> str:
    """构建稳定的 person_id。

    群聊：group_群号:QQ号（复用 user_id 已含的 group_ 前缀）
    私聊：private_QQ号（不用 user_id，避免 private_987:987 重复）
    """
    sender = str(sender_id or "unknown").strip() or "unknown"
    safe = sender.replace("\n", " ").replace("\r", " ")
    if user_id.startswith("group_"):
        return f"{user_id}:{safe}"
    return f"private_{safe}"


def extract_msg_content(content: str) -> str:
    """从 history content 中提取纯消息文本。

    新格式：<sender ...>消息</sender> → 提取 > 和 </sender> 之间的文本
    旧格式：<昵称> 消息 → 取 > 后面的部分
    """
    if "</sender>" in content:
        m = re.search(r"<sender[^>]*>(.*)</sender>", content, re.DOTALL)
        if m:
            return m.group(1)
        return content
    # 旧格式：<昵称> 消息
    idx = content.find("> ")
    if idx >= 0:
        return content[idx + 2:]
    return content


def history_text(history: list[HistoryMsg]) -> str:
    """把历史转为给结算模型看的文本，输出 <sender> 格式。

    新消息（含 person_id 等字段）：输出完整 sender 标签
    旧消息（只有 nickname）：输出简化 sender 标签，只填 display
    """
    lines: list[str] = []
    for h in history:
        if h.get("role") != "user":
            continue
        content = h.get("content", "")
        # 新格式已有 sender 标签
        if "</sender>" in content:
            lines.append(content)
            continue
        # 旧格式或无标签：用 nickname 构造简化 sender
        nickname = h.get("nickname") or h.get("sender_id") or "unknown"
        ts = h.get("ts", "")
        ts_attr = f' ts="{ts}"' if ts else ""
        msg = extract_msg_content(content) if "<" in content[:5] else content
        lines.append(f'<sender display="{nickname}"{ts_attr}>{msg}</sender>')
    return "\n".join(lines)
