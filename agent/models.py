"""全局类型定义（TypedDict）。

从 agent.py 拆出，所有数据结构集中在此，供各子模块复用。
"""

from typing import TypedDict


# ── 情感表 v2 ────────────────────────────────────────────


class EmotionEvent(TypedDict, total=False):
    """情感事件（v2 格式，total=False 兼容 v1 迁移）。"""
    # v2 字段
    at: str
    dimension: str       # affection / trust
    impact: int           # 0-10
    valence: str          # positive / negative
    event: str
    # v1 遗留字段（迁移后不再产生）
    start_at: str
    end_at: str
    emotion: str


class EmotionHistoryEntry(TypedDict, total=False):
    at: str
    affection: float
    trust: float
    trend: str
    # v1 遗留
    emotion: str


class EmotionUser(TypedDict, total=False):
    display_name: str
    affection: float               # 0-100 亲近度
    trust: float                   # 0-100 信任度
    current_emotion: str
    emotion_trend: str
    last_interaction: str          # YYYY-MM-DD
    summary_before_30d: str
    emotion_history: list[EmotionHistoryEntry]
    logs: list[EmotionEvent]
    updated_at: str


class EmotionData(TypedDict):
    schema_version: int
    updated_at: str
    users: dict[str, EmotionUser]


# ── 情绪表（Mood, PAD 模型） ────────────────────────────


class MoodLogEntry(TypedDict):
    """today_log 中的单条状态点。"""
    at: str       # ISO 时间戳
    p: float
    a: float
    d: float


class MoodData(TypedDict):
    p: float
    a: float
    d: float
    baseline_p: float
    baseline_a: float
    baseline_d: float
    last_update: str       # ISO 时间戳
    reason: str
    label: str
    today_log: list[MoodLogEntry]


class ParsedMood(TypedDict, total=False):
    """从模型输出 <mood> 标签解析出的情绪。"""
    p: float
    a: float
    d: float
    reason: str
    label: str


# ── 模型输出解析 ────────────────────────────────────────


class ModelOutput(TypedDict, total=False):
    """从模型输出解析出的结构化结果。"""
    messages: list[str]   # <message> 标签内容列表
    mood: ParsedMood | None


# ── 对话历史 ────────────────────────────────────────────


class HistoryMsg(TypedDict, total=False):
    role: str
    content: str
    ts: str
    sender_id: str
    nickname: str
    person_id: str
    qq_name: str
    group_card: str
    gender: str


# ── Mem0 ────────────────────────────────────────────────


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
    # v2
    at: str
    dimension: str
    valence: str
    impact: int
    event: str
    # v1 遗留
    start_at: str
    end_at: str
    emotion: str
    # WebUI 用
    person_id: str
    display_name: str
    index: int


class EmotionUpdateItem(TypedDict, total=False):
    """EMOTION_PROMPT 输出的单条更新（_update_emotions 解析）。"""
    display_name: str
    person_id: str
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


# ── 用户映射表（user_map.json） ─────────────────────────


# JSON key "from" 是 Python 保留字，用 dict[str, object] 避免 TypedDict 键名冲突
NameHistoryEntry = dict[str, str | None]


class GroupCardHistory(TypedDict, total=False):
    current_card: str
    group_card_history: list[NameHistoryEntry]


class UserMapEntry(TypedDict, total=False):
    qq_name: str
    first_seen: str
    last_seen: str
    qq_name_history: list[NameHistoryEntry]
    groups: dict[str, GroupCardHistory]
