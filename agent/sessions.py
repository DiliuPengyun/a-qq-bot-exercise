"""对话历史持久化：会话 JSON 读写 + 内存缓存。"""

import json
import os
from typing import cast

from config import SESSIONS_DIR
from models import HistoryMsg


# ── 持久化 ──────────────────────────────────────────────


def _session_path(user_id: str) -> str:
    safe = user_id.replace("\\", "_").replace("/", "_")
    return os.path.join(SESSIONS_DIR, f"{safe}.json")


def load_history(user_id: str) -> list[HistoryMsg]:
    path = _session_path(user_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return cast(list[HistoryMsg], json.load(f))
    return []


def save_history(user_id: str, history: list[HistoryMsg]) -> None:
    with open(_session_path(user_id), "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


_history_cache: dict[str, list[HistoryMsg]] = {}


def get_history(user_id: str) -> list[HistoryMsg]:
    if user_id not in _history_cache:
        _history_cache[user_id] = load_history(user_id)
    return _history_cache[user_id]
