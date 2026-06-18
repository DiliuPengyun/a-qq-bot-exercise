"""情感表 emotions.json v2：读写 + 格式化 + CRUD + 滚动摘要 + 结算更新。

v2: 亲近度(Affection) + 信任度(Trust)，基于 Sutcliffe & Wang (2012) 数学模型。
"""

import json
import os
from datetime import datetime, timedelta
from typing import cast

from config import (
    EMOTION_PROMPT,
    EMOTION_RECENT_DAYS,
    EMOTION_ROLLUP_PROMPT,
    EMOTIONS_FILE,
    LOCAL_TZ,
    SETTLE_MODEL,
)
from llm import call_deepseek_for_settle, call_deepseek_text, json_array_with_repair
from models import (
    EmotionData,
    EmotionEvent,
    EmotionEventInput,
    EmotionHistoryEntry,
    EmotionUser,
    HistoryMsg,
)
from usermap import build_person_id_map
from utils import now_minute, parse_dt, person_id


# ── 读写 ────────────────────────────────────────────────


def _default_v2() -> EmotionData:
    return {"schema_version": 2, "updated_at": "", "users": {}}


def _migrate_v1_to_v2(data: dict[str, object]) -> EmotionData:
    """v1 → v2 迁移：affection/trust 从 50 起步，旧 logs 按 Sutcliffe & Wang 累加。"""
    old_users = cast(dict[str, dict[str, object]], data.get("users") or {})
    new_users: dict[str, EmotionUser] = {}

    for pid, old_user in old_users.items():
        affection = 50.0
        trust = 50.0
        old_logs = cast(list[dict[str, object]], old_user.get("logs") or [])
        new_logs: list[EmotionEvent] = []

        for log in old_logs:
            # v1 事件可能有 start_at/end_at/emotion，没有 dimension/valence/impact
            dimension = str(log.get("dimension") or "affection")
            valence = str(log.get("valence") or "positive")
            impact_raw = log.get("impact")
            impact = int(impact_raw) if isinstance(impact_raw, (int, float)) else 3
            event_text = str(log.get("event") or log.get("emotion") or "").strip()
            at = str(log.get("at") or log.get("start_at") or log.get("end_at") or now_minute())

            if not event_text:
                continue

            new_logs.append(cast(EmotionEvent, {
                "at": at,
                "dimension": dimension,
                "impact": impact,
                "valence": valence,
                "event": event_text,
            }))

            # 应用 Sutcliffe & Wang 公式
            score = affection if dimension == "affection" else trust
            if valence == "positive":
                delta = impact * (1.0 - score / 100.0)
                score = min(100.0, score + delta)
            else:
                delta = impact * (1.0 - score / 200.0)
                score = max(0.0, score - delta)

            if dimension == "affection":
                affection = score
            else:
                trust = score

        log_count = len(new_logs)
        new_users[pid] = cast(EmotionUser, {
            "display_name": str(old_user.get("display_name") or pid),
            "affection": round(affection, 1),
            "trust": round(trust, 1),
            "current_emotion": _emotion_label(affection, trust),
            "emotion_trend": "stable",
            "last_interaction": "",
            "summary_before_30d": f"迁移自 v1，共 {log_count} 条历史事件",
            "emotion_history": [],
            "logs": new_logs,
            "updated_at": now_minute(),
        })

    return {"schema_version": 2, "updated_at": now_minute(), "users": new_users}


def _load_emotions() -> EmotionData:
    if os.path.exists(EMOTIONS_FILE):
        try:
            with open(EMOTIONS_FILE, encoding="utf-8") as f:
                raw = cast(dict[str, object], json.load(f))
            sv = raw.get("schema_version", 1)
            if sv == 1:
                print("[Emotion] 检测到 v1 格式，开始迁移到 v2...")
                migrated = _migrate_v1_to_v2(raw)
                _save_emotions(migrated)
                print(f"[Emotion] v1→v2 迁移完成，{len(migrated['users'])} 个用户")
                return migrated
            users_raw = raw.get("users")
            if isinstance(users_raw, dict) and users_raw:
                if "schema_version" not in raw:
                    raw["schema_version"] = 2
                if "updated_at" not in raw:
                    raw["updated_at"] = ""
                return cast(EmotionData, raw)
        except Exception as e:
            print(f"[Emotion] 读取失败: {e}")
    return _default_v2()


def _save_emotions(data: EmotionData) -> None:
    data["schema_version"] = 2
    data["updated_at"] = now_minute()
    if "users" not in data:
        data["users"] = {}
    with open(EMOTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── A/T → 态度标签 ──────────────────────────────────────


def _bucket(score: float) -> int:
    if score <= 33:
        return 0
    if score <= 66:
        return 1
    return 2


_LABELS: list[list[str]] = [
    ["陌生、警惕", "不太熟但还行", "陌生但还算信任"],
    ["有点亲近但不太信任", "一般/普通", "比较亲近、比较信任"],
    ["亲近但不太信任", "亲近、还算信任", "很亲近、很信任"],
]


def _emotion_label(affection: float, trust: float) -> str:
    return _LABELS[_bucket(affection)][_bucket(trust)]


# ── 事件格式化 ──────────────────────────────────────────


def _event_time_key(event: EmotionEvent) -> str:
    return str(event.get("at") or event.get("start_at") or event.get("end_at") or "")


def _format_event(event: EmotionEvent) -> str:
    at = str(event.get("at") or event.get("start_at") or "未知时间")
    dimension = str(event.get("dimension") or "affection")
    impact = event.get("impact", "?")
    valence = str(event.get("valence") or "positive")
    text = str(event.get("event") or "").strip()
    dim_short = "A" if dimension == "affection" else "T"
    val_sign = "+" if valence == "positive" else "-"
    return f"{at} {text} → {dim_short}{val_sign}{impact}"


def _sort_emotion_users(
    users: dict[str, EmotionUser], session_id: str
) -> list[tuple[str, EmotionUser]]:
    """排序：当前会话用户优先，按 last_interaction 降序。"""
    items = list(users.items())

    def _in_session(pid: str) -> bool:
        if session_id.startswith("group_"):
            return pid.startswith(f"{session_id}:")
        return pid.startswith(f"private_")

    current = [it for it in items if _in_session(it[0])]
    other = [it for it in items if not _in_session(it[0])]

    def _sort_key(kv: tuple[str, EmotionUser]) -> str:
        return str(kv[1].get("last_interaction") or kv[1].get("updated_at") or "")

    current.sort(key=_sort_key, reverse=True)
    other.sort(key=_sort_key, reverse=True)
    return current + other


def format_emotions_for_prompt(session_id: str) -> str:
    """格式化情感表给 system prompt 注入。

    只显示当前说话者 + 最近 3 天内活跃的前 5 人。
    """
    data = _load_emotions()
    users = data.get("users", {})
    if not users:
        return ""

    # 筛选最近 3 天内活跃的用户
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=3)
    active: list[tuple[str, EmotionUser]] = []
    for pid, user in users.items():
        li = user.get("last_interaction") or user.get("updated_at") or ""
        dt = parse_dt(li)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        if dt and dt >= cutoff:
            active.append((pid, user))

    # 当前会话用户始终包含
    sorted_users = _sort_emotion_users(dict(active), session_id)
    # 最多显示 6 人（当前说话者 + 5 人）
    sorted_users = sorted_users[:6]

    rows = [
        "| 用户 | 亲近度 | 信任度 | 当前态度 | 趋势 | 近期事件 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for pid, user in sorted_users:
        display = str(user.get("display_name") or pid)
        affection = float(user.get("affection", 50))
        trust = float(user.get("trust", 50))
        current = str(user.get("current_emotion") or "").strip()
        trend = str(user.get("emotion_trend") or "stable")
        trend_display = {"up": "↑", "stable": "→", "down": "↓"}.get(trend, "→")
        # 近期事件
        logs = list(user.get("logs") or [])
        logs.sort(key=_event_time_key, reverse=True)
        parts = [_format_event(e) for e in logs[:3]]
        rows.append(
            f"| {display} | {affection:.0f} | {trust:.0f} | {current or '无'} | {trend_display} | {'<br>'.join(parts) or '无'} |"
        )
    if len(rows) == 2:
        return ""
    return "\n".join(rows)


# ── CRUD（WebUI 用） ────────────────────────────────────


def emotions_json() -> EmotionData:
    data = _load_emotions()
    users = data.get("users") or {}
    data["users"] = dict(users)
    return data


def _ensure_user(data: EmotionData, pid: str, display_name: str) -> EmotionUser:
    if "users" not in data:
        data["users"] = {}
    users = data["users"]
    if pid not in users:
        users[pid] = cast(EmotionUser, {
            "display_name": display_name or pid,
            "affection": 50.0,
            "trust": 50.0,
            "current_emotion": _emotion_label(50.0, 50.0),
            "emotion_trend": "stable",
            "last_interaction": "",
            "summary_before_30d": "",
            "emotion_history": [],
            "logs": [],
        })
    return users[pid]


def emotion_upsert_user(person_id: str, display_name: str, summary_before_30d: str = "") -> EmotionUser:
    data = _load_emotions()
    user = _ensure_user(data, person_id, display_name)
    user["display_name"] = display_name or user.get("display_name") or person_id
    if summary_before_30d:
        user["summary_before_30d"] = summary_before_30d
    user["updated_at"] = now_minute()
    _save_emotions(data)
    return user


def emotion_add_event(person_id: str, display_name: str, event: EmotionEventInput) -> EmotionEvent:
    data = _load_emotions()
    user = _ensure_user(data, person_id, display_name)
    user["display_name"] = display_name or user.get("display_name") or person_id
    logs = list(user.get("logs") or [])
    item: EmotionEvent = cast(EmotionEvent, {
        "at": str(event.get("at") or event.get("start_at") or now_minute()),
        "dimension": str(event.get("dimension") or "affection"),
        "impact": int(event.get("impact") or 3),
        "valence": str(event.get("valence") or "positive"),
        "event": str(event.get("event") or "").strip(),
    })
    if item.get("event"):
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
    logs = list(user.get("logs") or [])
    if index < 0 or index >= len(logs):
        raise ValueError("event not found")
    logs[index] = cast(EmotionEvent, {
        "at": str(event.get("at") or event.get("start_at") or logs[index].get("at") or now_minute()),
        "dimension": str(event.get("dimension") or logs[index].get("dimension") or "affection"),
        "impact": int(event.get("impact") or logs[index].get("impact") or 3),
        "valence": str(event.get("valence") or logs[index].get("valence") or "positive"),
        "event": str(event.get("event") or "").strip(),
    })
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
        logs = list(users[person_id].get("logs") or [])
        if 0 <= index < len(logs):
            logs.pop(index)
            users[person_id]["logs"] = logs
            users[person_id]["updated_at"] = now_minute()
    _save_emotions(data)


# ── Sutcliffe & Wang 公式 ──────────────────────────────


def _apply_event(score: float, impact: int, valence: str) -> float:
    """应用 Sutcliffe & Wang 公式更新分数。"""
    if valence == "positive":
        delta = impact * (1.0 - score / 100.0)
        return min(100.0, score + delta)
    else:
        delta = impact * (1.0 - score / 200.0)
        return max(0.0, score - delta)


def _apply_daily_decay(data: EmotionData) -> None:
    """每日结算时对所有用户应用日常衰减（-0.5）。"""
    for user in data.get("users", {}).values():
        affection = float(user.get("affection", 50))
        trust = float(user.get("trust", 50))
        user["affection"] = max(0.0, affection - 0.5)
        user["trust"] = max(0.0, trust - 0.5)
        user["current_emotion"] = _emotion_label(
            float(user["affection"]), float(user["trust"])
        )


# ── 滚动摘要 ────────────────────────────────────────────


async def _rollup_emotion_user(user: EmotionUser) -> None:
    logs: list[EmotionEvent] = list(user.get("logs") or [])
    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=EMOTION_RECENT_DAYS)
    recent: list[EmotionEvent] = []
    expired: list[EmotionEvent] = []
    for event in logs:
        dt = parse_dt(event.get("at") or event.get("start_at"))
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


# ── 结算：情感更新 ──────────────────────────────────────


def _build_allowed_pids(
    history: list[HistoryMsg], bot_qq: str, user_id: str
) -> set[str]:
    """构建合法 person_id 集合。"""
    allowed: set[str] = set()
    data = _load_emotions()
    for pid in data.get("users", {}):
        allowed.add(str(pid))
    for h in history:
        if h.get("role") != "user":
            continue
        sid = str(h.get("sender_id") or "")
        if sid:
            allowed.add(person_id(user_id, sid))
    if bot_qq:
        allowed.add(person_id(user_id, bot_qq))
    return allowed


async def update_emotions(
    history: list[HistoryMsg], session_id: str, bot_qq: str = ""
) -> None:
    """每日结算时调用 EMOTION_PROMPT → 应用 Sutcliffe & Wang 模型更新 emotions.json"""
    if not history:
        return

    allowed = _build_allowed_pids(history, bot_qq, session_id)
    data = _load_emotions()

    # 构建合法 person_id 映射表
    pid_map = build_person_id_map(
        cast(list[dict[str, object]], history),
        cast(dict[str, object], data.get("users") or {}),
        bot_qq,
        session_id,
    )
    existing = format_emotions_for_prompt(session_id)
    extra = (
        f"合法 person_id 映射表：\n{pid_map}\n\n"
        f"已有情感记忆：\n{existing or '（无）'}"
    )

    result = await call_deepseek_for_settle(
        EMOTION_PROMPT, history, "Emotion", extra_context=extra
    )
    if not result:
        return

    updates = await json_array_with_repair(result, "Emotion")
    if updates is None:
        return

    today = datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")

    if "users" not in data:
        data["users"] = {}

    for item in updates:
        display = str(item.get("display_name") or "").strip()
        pid = str(item.get("person_id") or "").strip()

        # person_id 校验
        if pid not in allowed:
            print(f"[Emotion] 非法 person_id: {pid}，跳过")
            continue

        if not display:
            display = pid

        user = _ensure_user(data, pid, display)
        user["display_name"] = display
        user["last_interaction"] = today

        # 更新当前情感状态
        ce = str(item.get("current_emotion") or "").strip()
        et_raw = item.get("emotion_trend")
        et = str(et_raw or "").strip().lower()
        if et not in ("up", "stable", "down"):
            et = "stable"
        if ce:
            history_list = list(user.get("emotion_history") or [])
            history_list.append(cast(EmotionHistoryEntry, {
                "at": today,
                "affection": float(user.get("affection", 50)),
                "trust": float(user.get("trust", 50)),
                "trend": et,
            }))
            if len(history_list) > 30:
                history_list = history_list[-30:]
            user["emotion_history"] = history_list
            user["current_emotion"] = ce
            user["emotion_trend"] = et

        # 应用情感事件（Sutcliffe & Wang 公式）
        logs: list[EmotionEvent] = list(user.get("logs") or [])
        events_raw = item.get("events")
        events_list: list[EmotionEventInput] = events_raw if events_raw else []
        for ev in events_list:
            at = str(ev.get("at") or now_minute()).strip()
            # 补全日期：如果只有时分则补上当天日期
            if len(at) <= 5 and ":" in at:
                at = f"{today} {at}"
            dimension = str(ev.get("dimension") or "affection")
            valence = str(ev.get("valence") or "positive")
            impact = int(ev.get("impact") or 3)
            event_text = str(ev.get("event") or "").strip()

            if not event_text:
                continue

            # 校验
            if dimension not in ("affection", "trust"):
                dimension = "affection"
            if valence not in ("positive", "negative"):
                valence = "positive"
            if not (0 <= impact <= 10):
                impact = max(0, min(10, impact))

            # 应用公式
            if dimension == "affection":
                old_score = float(user.get("affection", 50))
                new_score = _apply_event(old_score, impact, valence)
                user["affection"] = round(new_score, 1)
            else:
                old_score = float(user.get("trust", 50))
                new_score = _apply_event(old_score, impact, valence)
                user["trust"] = round(new_score, 1)

            logs.append(cast(EmotionEvent, {
                "at": at,
                "dimension": dimension,
                "impact": impact,
                "valence": valence,
                "event": event_text,
            }))

        user["logs"] = logs
        logs.sort(key=_event_time_key, reverse=True)
        user["current_emotion"] = _emotion_label(
            float(user.get("affection", 50)), float(user.get("trust", 50))
        )
        user["updated_at"] = now_minute()
        await _rollup_emotion_user(user)

    # 每日衰减
    _apply_daily_decay(data)

    _save_emotions(data)
    print(f"[Emotion] {session_id}: 更新 {len(updates)} 个用户")
