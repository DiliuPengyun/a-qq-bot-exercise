"""Type stubs for mem0"""
from typing import TypedDict


class Mem0Metadata(TypedDict, total=False):
    spoken_by: list[str]
    user_id: str


class Mem0Memory(TypedDict, total=False):
    """Mem0 记忆条目。total=False 因为字段取决于 Mem0 版本和操作。"""
    id: str
    memory: str
    user_id: str
    agent_id: str
    run_id: str
    score: float
    created_at: str
    updated_at: str
    metadata: Mem0Metadata
    event: str


class Mem0SearchResponse(TypedDict, total=False):
    results: list[Mem0Memory]


class _Mem0CountResult:
    count: int


class _Mem0Client:
    def count(self, *, collection_name: str, count_filter: object, exact: bool) -> _Mem0CountResult: ...


class _Mem0Row:
    id: str | int
    payload: dict[str, object]


class _Mem0VectorStore:
    collection_name: str
    client: _Mem0Client

    def list(self, *, filters: dict[str, str] | None = ..., top_k: int = ...) -> tuple[list[_Mem0Row], object] | list[_Mem0Row]: ...
    def _create_filter(self, filters: dict[str, str] | None) -> object: ...


class Memory:
    vector_store: _Mem0VectorStore

    def __init__(self, config: object = ...) -> None: ...
    def add(self, text: str, *, user_id: str = ..., agent_id: str = ..., infer: bool = ..., metadata: Mem0Metadata | None = ...) -> Mem0Memory: ...
    def search(self, query: str, *, filters: dict[str, str] | None = ..., top_k: int = ...) -> Mem0SearchResponse | list[Mem0Memory]: ...
    def get_all(self, *, filters: dict[str, str] | None = ...) -> Mem0SearchResponse | list[Mem0Memory]: ...
    def get(self, memory_id: str) -> Mem0Memory | None: ...
    def update(self, memory_id: str, memory: str, *, metadata: Mem0Metadata | None = ...) -> Mem0Memory: ...
    def delete(self, memory_id: str) -> None: ...
