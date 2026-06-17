"""全局类型定义（TypedDict）。

从 agent.py 拆出，所有数据结构集中在此，供各子模块复用。
"""

from typing import TypedDict


# ── 类型定义 ──────────────────────────────────────────────


class EmotionEvent(TypedDict):
    start_at: str
    end_at: str
    event: str
    emotion: str


class EmotionHistoryEntry(TypedDict):
    at: str
    emotion: str
    trend: str


class EmotionUser(TypedDict, total=False):
    display_name: str
    summary_before_30d: str
    current_emotion: str
    emotion_trend: str
    emotion_history: list[EmotionHistoryEntry]
    logs: list[EmotionEvent]
    updated_at: str


class EmotionData(TypedDict):
    schema_version: int
    updated_at: str
    users: dict[str, EmotionUser]


class HistoryMsg(TypedDict, total=False):
    role: str
    content: str
    ts: str
    sender_id: str
    nickname: str
    person_id: str
    qq_name: str
    group_card: str


class MemMetadata(TypedDict, total=False):
    """Mem0 记忆元数据。值类型取决于来源，常见 spoken_by 为 list[str]。"""
    spoken_by: list[str]
    user_id: str


class MemSearchItem(TypedDict, total=False):
    """Mem0 搜索结果条目。total=False 因为字段取决于 Mem0 版本和搜索模式。"""
    id: str
    memory: str
    user_id: str
    agent_id: str
    run_id: str
    score: float
    created_at: str
    updated_at: str
    metadata: MemMetadata


class Mem0Diag(TypedDict, total=False):
    query: str
    candidates: int
    emb_min: float
    emb_max: float
    emb_avg: float
    qualified: int
    dropped_emb: int
    rerank_count: int
    rerank_min: float
    rerank_max: float
    passed: int
    dropped_rerank: int
    deduped_rerank: int
    invalid_rerank_indexes: int
    emb_candidates: list[dict[str, object]]
    rerank_candidates: list[dict[str, object]]
    round: int
    round_query: str


class ToolCallFunction(TypedDict):
    name: str
    arguments: str


class ToolCall(TypedDict):
    id: str
    function: ToolCallFunction


class RerankResult(TypedDict, total=False):
    """硅基流动 Reranker 返回的单条结果。"""
    index: int
    relevance_score: float


class GroupInfo(TypedDict, total=False):
    """群信息（adapter 实时查询注入）。"""
    member_count: int
    owner_name: str
    admin_names: list[str]


class ChatRequestBody(TypedDict, total=False):
    """/chat 请求体。total=False：user_id/message 虽必填但用 .get 统一访问。"""
    user_id: str
    nickname: str
    message: str
    is_direct: bool
    mentioned: bool
    gender: str
    sender_id: str
    message_time: str
    qq_name: str
    group_card: str
    bot_qq: str
    group_info: GroupInfo


class EmotionEventInput(TypedDict, total=False):
    """情感事件输入（WebUI 手动添加/修改时传入）。"""
    start_at: str
    end_at: str
    event: str
    emotion: str
    dimension: str
    valence: str
    impact: int
    person_id: str
    display_name: str
    index: int


class EmotionUpdateItem(TypedDict, total=False):
    """EMOTION_PROMPT 输出的单条更新（_update_emotions 解析）。"""
    display_name: str
    person_id: str
    sender_id: str
    current_emotion: str
    emotion_trend: str
    events: list[EmotionEventInput]


class DeepSeekMessage(TypedDict, total=False):
    """DeepSeek chat-completion 返回的 message 对象。"""
    role: str
    content: str
    tool_calls: list[ToolCall]


class ToolProperty(TypedDict, total=False):
    type: str
    description: str


class ToolFunctionParameters(TypedDict, total=False):
    type: str
    properties: dict[str, ToolProperty]
    required: list[str]


class ToolFunctionDef(TypedDict):
    name: str
    description: str
    parameters: ToolFunctionParameters


class ToolDef(TypedDict):
    type: str
    function: ToolFunctionDef


class ToolResponse(TypedDict):
    role: str
    tool_call_id: str
    content: str


class UserCtx(TypedDict, total=False):
    """search_memories 诊断日志附带的用户上下文。"""
    nickname: str
    message: str
    user_id: str


class Mem0LogEntry(TypedDict, total=False):
    """_mem0_log 环形缓冲的单条日志条目（含多轮诊断）。"""
    ts: str
    rounds: list[Mem0Diag]
    total: int
    nickname: str
    message: str
    user_id: str
