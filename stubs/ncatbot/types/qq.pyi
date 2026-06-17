"""Type stubs for ncatbot.types.qq"""

class MessageType:
    GROUP: str
    PRIVATE: str


class GroupMember:
    user_id: str
    nickname: str
    card: str
    role: str
    sex: str


class BotApiQuery:
    async def get_group_member_list(self, group_id: str) -> list[GroupMember]: ...
    async def get_group_member_info(self, group_id: str, user_id: str) -> GroupMember: ...


class BotApi:
    query: BotApiQuery

    async def post_group_msg(self, *, group_id: str, text: str) -> None: ...
    async def post_private_msg(self, *, user_id: str, text: str) -> None: ...


class MessageEvent:
    group_id: str
    user_id: str
    raw_message: str
    message: list[object]
    message_type: str
    time: int
    sender: GroupMember
    api: BotApi

    async def reply(self, *, text: str) -> None: ...
