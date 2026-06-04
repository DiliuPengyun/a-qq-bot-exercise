"""
薄适配层 —— 连接 NcatBot 和你的 Agent 框架。

启动: python adapter.py
前提: NapCat 已启动，WebSocket 端口 3001 已启用
"""

import os
import re
import asyncio
from datetime import datetime
from typing import Any
import httpx
from dotenv import load_dotenv
from ncatbot.app.client import BotClient
from ncatbot.core.registry import registrar
from ncatbot.types.qq import MessageType
from ncatbot.types.common.segment import At
from ncatbot.utils import get_config_manager

# 加载 agent/.env，让 BOT_NAME 等配置生效
load_dotenv(os.path.join(os.path.dirname(__file__), "agent", ".env"))

AGENT_URL = "http://127.0.0.1:8081/chat"
AGENT_TIMEOUT = 30

cfg = get_config_manager()
BOT_NAME = os.getenv("BOT_NAME", str(cfg.bot_uin))

bot = BotClient()


async def get_group_info(event: Any) -> dict[str, Any]:
    """获取群基本信息（每次实时查询，群主和管理员可能变更）"""
    group_id = str(event.group_id)
    try:
        members = await event.api.query.get_group_member_list(group_id)
        owner = ""
        admins = []
        for m in members:
            if m.role == "owner":
                owner = m.card or m.nickname or m.user_id
            elif m.role == "admin":
                admins.append(m.card or m.nickname or m.user_id)
        return {
            "member_count": len(members),
            "owner_name": owner,
            "admin_names": admins,
        }
    except Exception:
        return {"member_count": 0, "owner_name": "", "admin_names": []}


async def resolve_at_mentions(event: Any) -> str:
    """把 raw_message 中的 [CQ:at,qq=xxx] 替换成 @昵称"""
    raw = event.raw_message or ""
    group_id = getattr(event, "group_id", None) if hasattr(event, "message_type") and event.message_type == MessageType.GROUP else None
    if not group_id:
        return raw

    # 找到所有被 @ 的 QQ 号，逐个查 API
    qqs = set(re.findall(r"\[CQ:at,qq=(\d+)\]", raw))
    if not qqs:
        return raw

    names: dict[str, str] = {}
    for qq in qqs:
        try:
            info = await event.api.query.get_group_member_info(group_id, qq)
            names[qq] = info.card or info.nickname or qq
        except Exception:
            names[qq] = qq

    # 替换
    def repl(m):
        qq = m.group(1)
        return f"@{names.get(qq, qq)}"

    return re.sub(r"\[CQ:at,qq=(\d+)\]", repl, raw)


def has_at_mention(event: Any) -> bool:
    """检测是否 @ 了 Bot（仅告知模型，不强制回复）"""
    bot_qq = str(cfg.bot_uin)
    if hasattr(event, "message") and event.message:
        for seg in event.message:
            if isinstance(seg, At) and seg.user_id == bot_qq:
                return True
    return False


async def call_agent(
    user_id: str, nickname: str, message: str, is_direct: bool, group_info: dict[str, Any] | None = None,
    mentioned: bool = False, gender: str = "", sender_id: str = "", message_time: str = "",
) -> list[tuple[str, bool]]:
    """调用 Agent，返回 [(reply, quote), ...]"""
    body = {
        "user_id": user_id,
        "nickname": nickname,
        "message": message,
        "is_direct": is_direct,
        "bot_name": BOT_NAME,
    }
    if sender_id:
        body["sender_id"] = sender_id
    if message_time:
        body["message_time"] = message_time
    if gender:
        body["gender"] = gender
    if mentioned:
        body["mentioned"] = True
    if group_info:
        body["group_info"] = group_info
    async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
        resp = await client.post(AGENT_URL, json=body)
        resp.raise_for_status()
        data = resp.json()
        replies = data.get("replies", [])
        if replies:
            return [(r["reply"], r.get("quote", False)) for r in replies if r.get("reply")]
        return []


@registrar.on("message")
async def handle_message(event: Any) -> None:
    is_group = event.message_type == MessageType.GROUP
    # 群聊：群名片（QQ昵称）；私聊：QQ昵称
    if is_group:
        card = event.sender.card or ""
        qq_name = event.sender.nickname or event.user_id
        nickname = f"{card}（{qq_name}）" if card else qq_name
        gender = getattr(event.sender, "sex", "") or ""
    else:
        nickname = event.sender.nickname or event.user_id
        gender = ""

    if is_group:
        user_id = f"group_{event.group_id}"
    else:
        user_id = f"private_{event.user_id}"

    # is_direct: 私聊永远 true，群聊全部交给模型判断
    is_direct = not is_group
    mentioned = is_group and has_at_mention(event)

    # 解析 @ 为昵称
    resolved_msg = await resolve_at_mentions(event) if is_group else event.raw_message
    # 获取群信息
    group_info = await get_group_info(event) if is_group else None
    sender_id = str(event.user_id)
    raw_time = getattr(event, "time", None) or getattr(event, "message_time", None)
    if raw_time:
        try:
            message_time = datetime.fromtimestamp(int(raw_time)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            message_time = ""
    else:
        message_time = ""

    try:
        replies = await call_agent(user_id, nickname, resolved_msg, is_direct, group_info, mentioned, gender, sender_id, message_time)
    except Exception as e:
        replies = [(f"出错了：{e}", False)]

    for reply, quote in replies:
        if not reply:
            continue
        if quote:
            await event.reply(text=reply)
        elif is_group:
            await event.api.post_group_msg(group_id=event.group_id, text=reply)
        else:
            await event.api.post_private_msg(user_id=event.user_id, text=reply)
        # 多条消息间隔一小段时间
        await asyncio.sleep(0.6)


if __name__ == "__main__":
    bot.run()
