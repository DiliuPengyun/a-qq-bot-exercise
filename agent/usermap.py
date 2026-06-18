"""用户身份映射表 user_map.json：QQ 号 → 昵称/群名片映射 + 变更历史。

由代码框架自动维护，不交给大模型。
"""

import asyncio
import json
import os
from datetime import datetime
from typing import cast

from config import LOCAL_TZ, USER_MAP_FILE
from models import UserMapEntry
from utils import person_id

_lock: asyncio.Lock = asyncio.Lock()


def _today() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def _load_map() -> dict[str, UserMapEntry]:
    if os.path.exists(USER_MAP_FILE):
        try:
            with open(USER_MAP_FILE, encoding="utf-8") as f:
                return cast(dict[str, UserMapEntry], json.load(f))
        except Exception as e:
            print(f"[UserMap] 读取失败: {e}")
    return {}


def _save_map(data: dict[str, UserMapEntry]) -> None:
    with open(USER_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _extract_qq(sender_id: str) -> str:
    """从 sender_id 提取纯 QQ 号。"""
    return str(sender_id or "").strip()


def _extract_group_id(user_id: str) -> str:
    """从 user_id 提取群号（group_123456 → 123456）。私聊返回空。"""
    if user_id.startswith("group_"):
        return user_id[7:]
    return ""


async def update_user_map(
    user_id: str, sender_id: str, qq_name: str, group_card: str
) -> None:
    """每条用户消息进来时更新映射表。

    1. 提取纯 QQ 号
    2. 不在表里 → 新建
    3. 已存在 → 更新 last_seen，检查改名
    """
    qq = _extract_qq(sender_id)
    if not qq:
        return

    today = _today()
    now = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H:%M")

    async with _lock:
        data = _load_map()

        if qq not in data:
            # 新建条目
            entry: UserMapEntry = {
                "qq_name": qq_name or "",
                "first_seen": now,
                "last_seen": now,
                "qq_name_history": [
                    {"name": qq_name or "", "from": today, "to": None}
                ],
                "groups": {},
            }
            group_id = _extract_group_id(user_id)
            if group_id:
                entry["groups"] = {
                    group_id: {
                        "current_card": group_card or "",
                        "group_card_history": [
                            {"name": group_card or "", "from": today, "to": None}
                        ],
                    }
                }
            data[qq] = entry
            _save_map(data)
            return

        # 已存在 → 更新
        entry = data[qq]
        entry["last_seen"] = now

        # 检查 QQ 昵称变更
        old_qq_name = entry.get("qq_name", "")
        if qq_name and qq_name != old_qq_name:
            name_hist = list(entry.get("qq_name_history") or [])
            # 关闭旧条目
            for h in name_hist:
                if h.get("to") is None:
                    h["to"] = today
            name_hist.append({"name": qq_name, "from": today, "to": None})
            entry["qq_name_history"] = name_hist
            entry["qq_name"] = qq_name

        # 检查群名片变更
        group_id = _extract_group_id(user_id)
        if group_id:
            groups = dict(entry.get("groups") or {})
            if group_id not in groups:
                groups[group_id] = {
                    "current_card": group_card or "",
                    "group_card_history": [
                        {"name": group_card or "", "from": today, "to": None}
                    ],
                }
            else:
                g = groups[group_id]
                old_card = g.get("current_card", "")
                if group_card != old_card:
                    # 空变空不触发
                    if group_card or old_card:
                        card_hist = list(g.get("group_card_history") or [])
                        for h in card_hist:
                            if h.get("to") is None:
                                h["to"] = today
                        card_hist.append({"name": group_card or "", "from": today, "to": None})
                        g["group_card_history"] = card_hist
                        g["current_card"] = group_card or ""
                groups[group_id] = g
            entry["groups"] = groups

        data[qq] = entry
        _save_map(data)


def build_nickname_map(user_id: str) -> str:
    """构建当前会话的昵称映射表给 user prompt。

    格式：
    person_id | QQ号 | 群名片 | QQ昵称
    """
    data = _load_map()
    if not data:
        return ""

    group_id = _extract_group_id(user_id)
    lines: list[str] = []

    for qq, entry in data.items():
        # 群聊：person_id = group_群号:QQ号；私聊：private_QQ号
        pid = person_id(user_id, qq)
        qq_name = entry.get("qq_name", "")
        card = ""
        if group_id:
            g = entry.get("groups", {}).get(group_id)
            if g:
                card = g.get("current_card", "")
        card_display = card if card else "(无)"
        lines.append(f"{pid} | {qq} | {card_display} | {qq_name}")

    if not lines:
        return ""

    header = "person_id | QQ号 | 群名片 | QQ昵称"
    return header + "\n" + "\n".join(lines)


def build_person_id_map(
    history: list[dict[str, object]],
    emotions_users: dict[str, object],
    bot_qq: str,
    user_id: str,
) -> str:
    """构建合法 person_id 映射表给 EMOTION_PROMPT 结算用。

    包含：emotions.json 已有用户 + 本次 history 出现过的用户 + Bot 自己。
    格式与 build_nickname_map 相同。
    """
    data = _load_map()
    group_id = _extract_group_id(user_id)

    # 收集所有需要出现的 QQ 号
    qq_set: set[str] = set()

    # 从 emotions.json 已有用户中提取 QQ 号
    for pid_key in emotions_users:
        pid_str = str(pid_key)
        # pid 格式：group_xxx:QQ号 或 private_QQ号
        if ":" in pid_str:
            qq_set.add(pid_str.rsplit(":", 1)[1])
        elif pid_str.startswith("private_"):
            qq_set.add(pid_str[8:])

    # 从 history 中提取 QQ 号
    for h in history:
        if h.get("role") != "user":
            continue
        sid = str(h.get("sender_id") or "")
        if sid:
            qq_set.add(sid)

    # Bot 自己
    if bot_qq:
        qq_set.add(bot_qq)

    lines: list[str] = []
    for qq in sorted(qq_set):
        pid = person_id(user_id, qq)
        entry = data.get(qq)
        if entry:
            qq_name = entry.get("qq_name", "")
            card = ""
            if group_id:
                g = entry.get("groups", {}).get(group_id)
                if g:
                    card = g.get("current_card", "")
            card_display = card if card else "(无)"
            lines.append(f"{pid} | {qq} | {card_display} | {qq_name}")
        else:
            lines.append(f"{pid} | {qq} | (无) | (未知)")

    if not lines:
        return ""

    header = "person_id | QQ号 | 群名片 | QQ昵称"
    return header + "\n" + "\n".join(lines)
