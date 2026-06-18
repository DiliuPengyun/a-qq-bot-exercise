"""情绪状态表 mood.json：PAD 三维模型 + 墙钟衰减 + 动态基线。

每轮对话更新情绪，每日结算时调整基线。
"""

import json
import math
import os
from datetime import datetime
from typing import cast

from config import (
    LOCAL_TZ,
    MOOD_EWMA_ALPHA,
    MOOD_FILE,
    MOOD_MAX_DELTA,
    MOOD_TAU_SECONDS,
)
from models import MoodData, MoodLogEntry, ParsedMood
from utils import now_iso, parse_dt


# ── 默认值 ──────────────────────────────────────────────


def _default_mood() -> MoodData:
    return {
        "p": 0.2,
        "a": 0.1,
        "d": 0.1,
        "baseline_p": 0.2,
        "baseline_a": 0.1,
        "baseline_d": 0.1,
        "last_update": now_iso(),
        "reason": "初始状态",
        "label": "平静",
        "today_log": [],
    }


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ── 读写 ────────────────────────────────────────────────


def load_mood() -> MoodData:
    if os.path.exists(MOOD_FILE):
        try:
            with open(MOOD_FILE, encoding="utf-8") as f:
                data = cast(dict[str, object], json.load(f))
            return cast(MoodData, data)
        except Exception as e:
            print(f"[Mood] 读取失败: {e}")
    return _default_mood()


def save_mood(data: MoodData) -> None:
    with open(MOOD_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 墙钟衰减 ────────────────────────────────────────────


def _decay_value(old: float, baseline: float, elapsed_seconds: float) -> float:
    """墙钟指数衰减：old 向 baseline 回归。"""
    decay = math.exp(-elapsed_seconds / MOOD_TAU_SECONDS)
    return old * decay + baseline * (1.0 - decay)


def _apply_decay(data: MoodData, now: datetime) -> tuple[float, float, float]:
    """对当前 P/A/D 应用墙钟衰减，返回衰减后的 (p, a, d)。"""
    last = parse_dt(data.get("last_update"))
    if last is None:
        return data["p"], data["a"], data["d"]
    if last.tzinfo is None:
        last = last.replace(tzinfo=LOCAL_TZ)
    elapsed = (now - last).total_seconds()
    if elapsed <= 0:
        return data["p"], data["a"], data["d"]
    p = _decay_value(data["p"], data["baseline_p"], elapsed)
    a = _decay_value(data["a"], data["baseline_a"], elapsed)
    d = _decay_value(data["d"], data["baseline_d"], elapsed)
    return p, a, d


# ── 每轮更新 ────────────────────────────────────────────


def update_mood(parsed: ParsedMood) -> MoodData:
    """每轮对话后更新情绪。

    1. 应用墙钟衰减到当前时刻
    2. 计算模型输出与衰减后状态的差值
    3. 硬上限 ±MOOD_MAX_DELTA
    4. 写入 today_log，保存
    """
    data = load_mood()
    now = datetime.now(LOCAL_TZ)

    # 衰减后的当前值
    cur_p, cur_a, cur_d = _apply_decay(data, now)

    # 模型输出的目标值
    new_p = float(parsed.get("p", cur_p))
    new_a = float(parsed.get("a", cur_a))
    new_d = float(parsed.get("d", cur_d))

    # 差值 + 硬上限
    dp = _clamp(new_p - cur_p, -MOOD_MAX_DELTA, MOOD_MAX_DELTA)
    da = _clamp(new_a - cur_a, -MOOD_MAX_DELTA, MOOD_MAX_DELTA)
    dd = _clamp(new_d - cur_d, -MOOD_MAX_DELTA, MOOD_MAX_DELTA)

    final_p = _clamp(cur_p + dp)
    final_a = _clamp(cur_a + da)
    final_d = _clamp(cur_d + dd)

    data["p"] = final_p
    data["a"] = final_a
    data["d"] = final_d
    data["last_update"] = now.isoformat()
    data["reason"] = str(parsed.get("reason", data.get("reason", "")))
    data["label"] = str(parsed.get("label", data.get("label", "")))

    # 记录到 today_log
    log: list[MoodLogEntry] = list(data.get("today_log") or [])
    log.append({"at": now.isoformat(), "p": final_p, "a": final_a, "d": final_d})
    data["today_log"] = log

    save_mood(data)
    return data


# ── 每日结算：基线调整 ──────────────────────────────────


def _integral_segment(
    start_val: float, baseline: float, dt_seconds: float
) -> float:
    """单段积分（闭式解）：∫₀^dt [baseline + (start - baseline) * exp(-t/τ)] dt

    = baseline * dt + (start - baseline) * τ * (1 - exp(-dt/τ))
    """
    tau = MOOD_TAU_SECONDS
    return baseline * dt_seconds + (start_val - baseline) * tau * (1.0 - math.exp(-dt_seconds / tau))


def adjust_mood_baseline() -> None:
    """每日结算时调用：用 today_log 算日均值 → EWMA 更新基线 → 清空 today_log。"""
    data = load_mood()
    log: list[MoodLogEntry] = list(data.get("today_log") or [])

    if not log:
        # 当天无 mood 更新，不更新基线
        return

    now = datetime.now(LOCAL_TZ)
    bp = data["baseline_p"]
    ba = data["baseline_a"]
    bd = data["baseline_d"]

    # 计算各段积分之和
    total_integral_p = 0.0
    total_integral_a = 0.0
    total_integral_d = 0.0
    total_seconds = 0.0

    points: list[tuple[datetime, float, float, float]] = []
    for entry in log:
        dt = parse_dt(entry.get("at"))
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=LOCAL_TZ)
        points.append((dt, float(entry.get("p", bp)), float(entry.get("a", ba)), float(entry.get("d", bd))))

    if not points:
        return

    # 第一段：从当天 0:00 (或第一个点之前) 到第一个点
    # 用 baseline 作为起始值
    first_dt = points[0][0]
    day_start = first_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    dt0 = (first_dt - day_start).total_seconds()
    if dt0 > 0:
        total_integral_p += _integral_segment(bp, bp, dt0)
        total_integral_a += _integral_segment(ba, ba, dt0)
        total_integral_d += _integral_segment(bd, bd, dt0)
        total_seconds += dt0

    # 各段：从一个点到下一个点
    for i in range(len(points)):
        start_dt, sp, sa, sd = points[i]
        if i + 1 < len(points):
            end_dt = points[i + 1][0]
        else:
            end_dt = now  # 最后一段到当前结算时刻
        dt_seg = (end_dt - start_dt).total_seconds()
        if dt_seg <= 0:
            continue
        total_integral_p += _integral_segment(sp, bp, dt_seg)
        total_integral_a += _integral_segment(sa, ba, dt_seg)
        total_integral_d += _integral_segment(sd, bd, dt_seg)
        total_seconds += dt_seg

    if total_seconds <= 0:
        return

    # 日均值
    avg_p = total_integral_p / total_seconds
    avg_a = total_integral_a / total_seconds
    avg_d = total_integral_d / total_seconds

    # EWMA 更新基线
    data["baseline_p"] = _clamp(MOOD_EWMA_ALPHA * avg_p + (1.0 - MOOD_EWMA_ALPHA) * bp)
    data["baseline_a"] = _clamp(MOOD_EWMA_ALPHA * avg_a + (1.0 - MOOD_EWMA_ALPHA) * ba)
    data["baseline_d"] = _clamp(MOOD_EWMA_ALPHA * avg_d + (1.0 - MOOD_EWMA_ALPHA) * bd)

    # 清空 today_log
    data["today_log"] = []
    save_mood(data)
    print(f"[Mood] 基线更新: P {data['baseline_p']:.3f} A {data['baseline_a']:.3f} D {data['baseline_d']:.3f}")


# ── Prompt 注入 ─────────────────────────────────────────


def _time_ago_label(last_update: str, now: datetime) -> str:
    """生成 '(3小时前)' 这样的时间差标注。"""
    dt = parse_dt(last_update)
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LOCAL_TZ)
    diff = now - dt
    hours = diff.total_seconds() / 3600.0
    if hours < 1.0:
        mins = int(diff.total_seconds() / 60.0)
        return f"（{mins}分钟前）"
    if hours < 24.0:
        return f"（{int(hours)}小时前）"
    return f"（{int(hours / 24)}天前）"


def format_mood_for_prompt() -> str:
    """格式化当前情绪状态给 user prompt。"""
    data = load_mood()
    now = datetime.now(LOCAL_TZ)

    # 应用衰减得到当前实时值
    cur_p, cur_a, cur_d = _apply_decay(data, now)

    label = str(data.get("label", ""))
    reason = str(data.get("reason", ""))
    ago = _time_ago_label(data.get("last_update", ""), now)

    lines = [
        "你当前的情绪状态：",
        f"愉悦度(P): {cur_p:.2f}",
        f"激活度(A): {cur_a:.2f}",
        f"支配度(D): {cur_d:.2f}",
    ]
    if label:
        lines.append(f"感受：{label}{ago}")
    if reason:
        lines.append(f"原因：{reason}{ago}")
    return "\n".join(lines)
