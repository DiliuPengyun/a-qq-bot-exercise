"""
薄适配层 —— 连接 NcatBot 和你的 Agent 框架。

启动: python adapter.py
前提: NapCat 已启动，WebSocket 端口 3001 已启用
"""

import re
import json
import asyncio
from datetime import datetime
from typing import TypedDict, cast
import httpx
from ncatbot.app.client import BotClient
from ncatbot.core.registry import registrar
from ncatbot.types.qq import MessageEventData, MessageType
from ncatbot.types.common.segment import At
from ncatbot.utils import get_config_manager


class GroupInfo(TypedDict, total=False):
    """群信息（注入 Agent system prompt）。"""
    member_count: int
    owner_name: str
    admin_names: list[str]


class ChatRequestBody(TypedDict, total=False):
    """/chat 请求体（adapter 构造，发给 Agent）。"""
    user_id: str
    nickname: str
    message: str
    is_direct: bool
    bot_qq: str
    sender_id: str
    message_time: str
    gender: str
    mentioned: bool
    group_info: GroupInfo
    qq_name: str
    group_card: str


AGENT_URL = "http://127.0.0.1:8081/chat"
AGENT_TIMEOUT = 120  # SSE 流式响应，需覆盖工具调用多轮 + DeepSeek 流式时间

cfg = get_config_manager()

bot = BotClient()


async def get_group_info(event: MessageEventData) -> GroupInfo:
    """获取群基本信息（每次实时查询，群主和管理员可能变更）"""
    group_id = str(event.group_id)
    try:
        members = await event.api.query.get_group_member_list(group_id)
        owner: str = ""
        admins: list[str] = []
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


async def resolve_at_mentions(event: MessageEventData) -> str:
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
    def repl(m: re.Match[str]) -> str:
        qq = m.group(1)
        return f"@{names.get(qq, qq)}"

    return re.sub(r"\[CQ:at,qq=(\d+)\]", repl, raw)


def has_at_mention(event: MessageEventData) -> bool:
    """检测是否 @ 了 Bot（仅告知模型，不强制回复）"""
    bot_qq = str(cfg.bot_uin)
    if event.message:
        for seg in event.message:
            if isinstance(seg, At) and seg.user_id == bot_qq:
                return True
    return False


async def _send(event: MessageEventData, reply: str, quote: bool, is_group: bool) -> None:
    """发送一条消息到 QQ"""
    if quote:
        await event.reply(text=reply)
    elif is_group:
        await event.api.post_group_msg(group_id=event.group_id, text=reply)
    else:
        await event.api.post_private_msg(user_id=event.user_id, text=reply)


async def call_agent(
    event: MessageEventData, is_group: bool,
    user_id: str, nickname: str, message: str, is_direct: bool, group_info: GroupInfo | None = None,
    mentioned: bool = False, gender: str = "", sender_id: str = "", message_time: str = "",
    qq_name: str = "", group_card: str = "",
) -> None:
    """调用 Agent（SSE 流式），逐条收到 reply 就发 QQ"""
    body: ChatRequestBody = {
        "user_id": user_id,
        "nickname": nickname,
        "message": message,
        "is_direct": is_direct,
        "bot_qq": str(cfg.bot_uin),
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
    if qq_name:
        body["qq_name"] = qq_name
    if group_card:
        body["group_card"] = group_card
    async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
        async with client.stream("POST", AGENT_URL, json=body) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    data = cast(dict[str, object], json.loads(data_str))
                except json.JSONDecodeError:
                    continue
                if data.get("done") or data.get("cancelled"):
                    break
                if data.get("error"):
                    await _send(event, str(data["error"]), False, is_group)
                    break
                reply = str(data.get("reply", ""))
                quote = bool(data.get("quote", False))
                if reply:
                    await _send(event, reply, quote, is_group)
                    await asyncio.sleep(0.6)  # 多条消息间隔一小段时间


@registrar.on("message")
async def handle_message(event: MessageEventData) -> None:
    is_group = event.message_type == MessageType.GROUP
    # 群聊：群名片(QQ昵称)；私聊：QQ昵称
    # 半角 () 包裹 QQ 昵称，名字内的半角括号用 \ 转义
    def _escape_parens(s: str) -> str:
        return s.replace("(", r"\(").replace(")", r"\)")

    if is_group:
        card = event.sender.card or ""
        qq_name = event.sender.nickname or event.user_id
        if card:
            nickname = f"{_escape_parens(card)}({_escape_parens(qq_name)})"
        else:
            nickname = _escape_parens(qq_name)
        gender = event.sender.sex or ""
    else:
        qq_name = event.sender.nickname or event.user_id
        nickname = _escape_parens(qq_name)
        card = ""
        gender = ""

    if is_group:
        user_id = f"group_{event.group_id}"
    else:
        user_id = f"private_{event.user_id}"

    # is_direct: 私聊永远 true，群聊全部交给模型判断
    is_direct = not is_group
    mentioned = is_group and has_at_mention(event)

    # 解析 @ 为昵称
    print(f"[Adapter] 收到消息: {event.raw_message[:50]}")
    try:
        resolved_msg = await resolve_at_mentions(event) if is_group else event.raw_message
        print(f"[Adapter] @解析完成: {resolved_msg[:50]}")
        # 获取群信息
        group_info = await get_group_info(event) if is_group else None
        print(f"[Adapter] 群信息获取完成: {group_info.get('member_count', 0) if group_info else 0}人")
    except Exception as e:
        print(f"[Adapter] 预处理异常: {e}")
        import traceback; traceback.print_exc()
        resolved_msg = event.raw_message
        group_info = None
    sender_id = str(event.user_id)
    raw_time = event.time
    if raw_time:
        try:
            message_time = datetime.fromtimestamp(int(raw_time)).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError, OSError):
            message_time = ""
    else:
        message_time = ""

    try:
        print(f"[Adapter] 调用 Agent: user_id={user_id}, msg={resolved_msg[:30]}")
        await call_agent(event, is_group, user_id, nickname, resolved_msg, is_direct, group_info, mentioned, gender, sender_id, message_time, qq_name, card)
        print(f"[Adapter] Agent 流式调用完成")
    except Exception as e:
        print(f"[Adapter] Agent 调用异常: {e}")
        import traceback; traceback.print_exc()
        await _send(event, f"出错了：{e}", False, is_group)


if __name__ == "__main__":
    bot.run()
