"""情感表 emotions.json：读写 + 格式化 + CRUD + 滚动摘要 + 结算更新。"""

import json
import os
from datetime import datetime, timedelta
from typing import cast

from config import EMOTION_PROMPT, EMOTION_RECENT_DAYS, EMOTION_ROLLUP_PROMPT, EMOTIONS_FILE, LOCAL_TZ, SETTLE_MODEL
from llm import call_deepseek_text, json_array_with_repair
from models import (
    EmotionData,
    EmotionEvent,
    EmotionEventInput,
    EmotionUser,
    HistoryMsg,
)
from utils import history_text, now_minute, parse_dt, person_id


# ── 读写 ────────────────────────────────────────────────


def _load_emotions() -> EmotionData:
    if os.path.exists(EMOTIONS_FILE):
        try:
            with open(EMOTIONS_FILE, encoding="utf-8") as f:
                data = cast(EmotionData, json.load(f))
            users = data.get("users") or {}
            if users:
                if "schema_version" not in data:
                    data["schema_version"] = 1
                if "updated_at" not in data:
                    data["updated_at"] = ""
                return data
        except Exception as e:
            print(f"[Emotion] 读取失败: {e}")
    return {"schema_version": 1, "updated_at": "", "users": {}}


def _save_emotions(data: EmotionData) -> None:
    data["schema_version"] = 1
    data["updated_at"] = now_minute()
    if "users" not in data:
        data["users"] = {}
    with open(EMOTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _session_rank(person_id: str, session_id: str) -> int:
    return 0 if person_id.startswith(f"{session_id}:") else 1


def _event_time_key(event: EmotionEvent) -> str:
    return str(event.get("start_at") or event.get("end_at") or "")


def _format_event(event: EmotionEvent) -> str:
    start = str(event.get("start_at") or "未知时间")
    end = str(event.get("end_at") or start)
    when = start if end == start else f"{start}-{end[-5:]}"
    text = str(event.get("event") or "").strip()
    emotion = str(event.get("emotion") or "").strip()
    if text and emotion:
        return f"{when}：{text} → {emotion}"
    return f"{when}：{text or emotion}"


def _sort_emotion_users(users: dict[str, EmotionUser], session_id: str) -> list[tuple[str, EmotionUser]]:
    items = list(users.items())
    current = [it for it in items if _session_rank(it[0], session_id) == 0]
    other = [it for it in items if _session_rank(it[0], session_id) != 0]

    def _sort_key(kv: tuple[str, EmotionUser]) -> tuple[str, int]:
        ua = kv[1].get("updated_at") or ""
        sb = kv[1].get("summary_before_30d") or ""
        return (ua, 1 if sb else 0)

    current.sort(key=_sort_key, reverse=True)
    other.sort(key=_sort_key, reverse=True)
    return current + other


def format_emotions_for_prompt(session_id: str) -> str:
    data = _load_emotions()
    users = data.get("users", {})
    if not users:
        return ""

    rows = ["| 用户 | 当前态度 | 趋势 | 情感变化轨迹 | 近期事件 |", "| --- | --- | --- | --- | --- |"]
    for pid, user in _sort_emotion_users(users, session_id):
        display = str(user.get("display_name") or pid)
        current = str(user.get("current_emotion") or "").strip()
        trend = str(user.get("emotion_trend") or "stable")
        trend_display = {"up": "↑", "stable": "→", "down": "↓"}.get(trend, "→")
        # 情感变化轨迹
        history_parts: list[str] = []
        for h in list(user.get("emotion_history") or []):
            at = str(h.get("at") or "")[5:16]  # "2026-06-05 12:02" → "06-05 12:02"
            em = str(h.get("emotion") or "")
            tr = str(h.get("trend") or "stable")
            tr_sym = {"up": "↑", "stable": "→", "down": "↓"}.get(tr, "→")
            history_parts.append(f"{at} {em} {tr_sym}")
        history_text = "<br>".join(history_parts) or "无"
        # 近期事件
        parts: list[str] = []
        logs = list(user.get("logs") or [])
        logs.sort(key=_event_time_key, reverse=True)
        for event in logs:
            parts.append(_format_event(event))
        summary = str(user.get("summary_before_30d") or "").strip()
        if summary:
            parts.append(f"30天前摘要：{summary}")
        if parts or current:
            rows.append(f"| {display} | {current or '无'} | {trend_display} | {history_text} | {'<br>'.join(parts) or '无'} |")
    if len(rows) == 2:
        return ""
    return "\n".join(rows)


# ── CRUD（WebUI 用） ────────────────────────────────────


def emotions_json() -> EmotionData:
    data = _load_emotions()
    users = data.get("users") or {}
    data["users"] = dict(users)
    return data


def emotion_upsert_user(person_id: str, display_name: str, summary_before_30d: str = "") -> EmotionUser:
    data = _load_emotions()
    if "users" not in data:
        data["users"] = {}
    users = data["users"]
    if person_id not in users:
        users[person_id] = {"display_name": display_name, "summary_before_30d": "", "current_emotion": "", "emotion_trend": "stable", "emotion_history": [], "logs": []}
    user = users[person_id]
    user["display_name"] = display_name or user.get("display_name") or person_id
    if summary_before_30d:
        user["summary_before_30d"] = summary_before_30d
    user["updated_at"] = now_minute()
    _save_emotions(data)
    return user


def emotion_add_event(person_id: str, display_name: str, event: EmotionEventInput) -> EmotionEvent:
    data = _load_emotions()
    if "users" not in data:
        data["users"] = {}
    users = data["users"]
    if person_id not in users:
        users[person_id] = {"display_name": display_name, "summary_before_30d": "", "current_emotion": "", "emotion_trend": "stable", "emotion_history": [], "logs": []}
    user = users[person_id]
    user["display_name"] = display_name or user.get("display_name") or person_id
    logs = user.get("logs") or []
    item: EmotionEvent = {
        "start_at": str(event.get("start_at") or now_minute()),
        "end_at": str(event.get("end_at") or event.get("start_at") or now_minute()),
        "event": str(event.get("event") or "").strip(),
        "emotion": str(event.get("emotion") or "").strip(),
    }
    if item["event"] or item["emotion"]:
        logs.append(item)
    user["logs"] = logs
    logs.sort(key=_event_time_key, reverse=True)
    user["updated_at"] = now_minute()
    _save_emotions(data)
    return item


def emotion_update_event(person_id: str, index: int, event: EmotionEventInput) -> EmotionEvent:
    data = _load_emotions()
    user = data["users"].get(person_id)
    if not user:
        raise ValueError("person not found")
    logs = user.get("logs") or []
    if index < 0 or index >= len(logs):
        raise ValueError("event not found")
    logs[index] = {
        "start_at": str(event.get("start_at") or logs[index].get("start_at") or now_minute()),
        "end_at": str(event.get("end_at") or event.get("start_at") or logs[index].get("end_at") or now_minute()),
        "event": str(event.get("event") or "").strip(),
        "emotion": str(event.get("emotion") or "").strip(),
    }
    user["logs"] = logs
    logs.sort(key=_event_time_key, reverse=True)
    user["updated_at"] = now_minute()
    _save_emotions(data)
    return logs[index]


def emotion_delete(person_id: str, index: int | None = None) -> None:
    data = _load_emotions()
    if "users" not in data:
        data["users"] = {}
    users = data["users"]
    if person_id not in users:
        return
    if index is None:
        users.pop(person_id, None)
    else:
        logs = users[person_id].get("logs") or []
        if 0 <= index < len(logs):
            logs.pop(index)
            users[person_id]["logs"] = logs
            users[person_id]["updated_at"] = now_minute()
    _save_emotions(data)


# ── 结算：滚动摘要 + 情感更新 ──────────────────────────


async def _rollup_emotion_user(user: EmotionUser) -> None:
    logs: list[EmotionEvent] = user.get("logs") or []
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=EMOTION_RECENT_DAYS)
    recent: list[EmotionEvent] = []
    expired: list[EmotionEvent] = []
    for event in logs:
        dt = parse_dt(event.get("start_at") or event.get("end_at"))
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        if dt and dt < cutoff:
            expired.append(event)
        else:
            recent.append(event)
    if not expired:
        user["logs"] = sorted(recent, key=_event_time_key, reverse=True)
        return

    old_summary = str(user.get("summary_before_30d") or "")
    expired_text = "\n".join(_format_event(e) for e in sorted(expired, key=_event_time_key))
    text = f"旧摘要：{old_summary}\n\n过期日志：\n{expired_text}"
    summary = await call_deepseek_text(EMOTION_ROLLUP_PROMPT, text, "EmotionRollup", model=SETTLE_MODEL)
    user["summary_before_30d"] = (summary or old_summary or expired_text).strip()
    user["logs"] = sorted(recent, key=_event_time_key, reverse=True)


async def update_emotions(history: list[HistoryMsg], session_id: str) -> None:
    text = history_text(history)
    if not text:
        return
    existing = format_emotions_for_prompt(session_id)
    payload = f"<已有情感记忆>\n{existing or '（无）'}\n</已有情感记忆>\n\n<昨天群聊记录>\n{text}\n</昨天群聊记录>"
    result = await call_deepseek_text(EMOTION_PROMPT, payload, "Emotion", model=SETTLE_MODEL)
    if not result:
        return
    updates = await json_array_with_repair(result, "Emotion")
    if updates is None:
        return

    # 从 history 构建 映射，用于 person_id 反查
    nickname_to_pid: dict[str, str] = {}       # 全昵称 → person_id
    subname_to_pid: dict[str, str] = {}        # QQ昵称/群名片 → person_id
    for h in history:
        if h.get("role") != "user":
            continue
        h_pid = str(h.get("person_id") or "").strip()
        h_nick = str(h.get("nickname") or "").strip()
        if h_pid and h_nick:
            nickname_to_pid[h_nick] = h_pid
            # 优先用 adapter 传入的独立字段
            h_qq = str(h.get("qq_name") or "").strip()
            h_card = str(h.get("group_card") or "").strip()
            if h_qq:
                subname_to_pid.setdefault(h_qq, h_pid)
            if h_card:
                subname_to_pid.setdefault(h_card, h_pid)

    # 当天日期，用于补全只有时分的时间
    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")

    data = _load_emotions()
    if "users" not in data:
        data["users"] = {}
    users = data["users"]
    for item in updates:
        display = str(item.get("display_name") or "").strip()
        pid = str(item.get("person_id") or "").strip()

        # person_id 格式校验：必须是 session_id:sender_id 格式
        if ":" not in pid:
            # 模型返回了昵称而非 person_id，尝试从 history 反查
            resolved = (
                nickname_to_pid.get(pid)
                or nickname_to_pid.get(display)
                or subname_to_pid.get(pid)
                or subname_to_pid.get(display)
            )
            if resolved:
                pid = resolved
            else:
                # 最后兜底：用 _person_id 构造
                sender_id_raw = item.get("sender_id")
                pid = person_id(session_id, str(sender_id_raw) if sender_id_raw else None, display)

        if not display:
            display = pid
        if pid not in users:
            users[pid] = {"display_name": display, "summary_before_30d": "", "current_emotion": "", "emotion_trend": "stable", "emotion_history": [], "logs": []}
        user = users[pid]
        user["display_name"] = display
        # 更新当前情感状态
        ce = str(item.get("current_emotion") or "").strip()
        et_raw = item.get("emotion_trend")
        et = str(et_raw or "").strip().lower()
        if et not in ("up", "stable", "down"):
            et = "stable"
        if ce:
            # 情感有变化或首次记录 → 追加到 history
            history_list = list(user.get("emotion_history") or [])
            history_list.append({"at": now_minute(), "emotion": ce, "trend": et})
            # 保留最近 30 条，防止无限增长
            if len(history_list) > 30:
                history_list = history_list[-30:]
            user["emotion_history"] = history_list
            user["current_emotion"] = ce
            user["emotion_trend"] = et
        logs: list[EmotionEvent] = list(user.get("logs") or [])
        events_raw = item.get("events")
        events_list: list[EmotionEventInput] = events_raw if events_raw else []
        for event in events_list:
            ev_dict: EmotionEventInput = event
            start_raw = ev_dict.get("start_at")
            end_raw = ev_dict.get("end_at")
            start = str(start_raw or now_minute()).strip()
            end = str(end_raw or start_raw or now_minute()).strip()
            # 补全日期：如果只有时分 (如 "23:13") 则补上当天日期
            if len(start) <= 5 and ":" in start:
                start = f"{today} {start}"
            if len(end) <= 5 and ":" in end:
                end = f"{today} {end}"
            entry: EmotionEvent = {
                "start_at": start,
                "end_at": end,
                "event": str(ev_dict.get("event") or "").strip(),
                "emotion": str(ev_dict.get("emotion") or "").strip(),
            }
            if entry["event"] or entry["emotion"]:
                logs.append(entry)
        user["logs"] = logs
        logs.sort(key=_event_time_key, reverse=True)
        user["updated_at"] = now_minute()
        await _rollup_emotion_user(user)
    _save_emotions(data)
    print(f"[Emotion] {session_id}: 更新 {len(updates)} 个用户")
