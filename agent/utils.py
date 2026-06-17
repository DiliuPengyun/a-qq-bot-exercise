"""通用工具：日期时间辅助 + 文本/spoken_by/person_id 处理。"""

import ast
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


def person_id(session_id: str, sender_id: str | None, nickname: str) -> str:
    sender = str(sender_id or nickname or "unknown").strip() or "unknown"
    safe = sender.replace("\n", " ").replace("\r", " ")
    return f"{session_id}:{safe}"


def history_text(history: list[HistoryMsg]) -> str:
    user_msgs = [h for h in history if h.get("role") == "user"]
    lines: list[str] = []
    for h in user_msgs:
        content = h.get("content", "")
        ts = h.get("ts")
        pid = h.get("person_id", "")
        tag = f"[{ts}]" if ts else ""
        pid_tag = f"({pid})" if pid else ""
        lines.append(f"{tag}{pid_tag} {content}" if tag or pid_tag else content)
    return "\n".join(lines)
