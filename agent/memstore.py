"""Mem0 长期记忆：客户端单例 + 同步 CRUD + 双门槛检索。

共享可变状态（_memory / _mem0_lock / _mem0_log / _last_mem0_diag）归属本模块。
_mem_search 与 search_memories 同模块,_last_mem0_diag 作为模块全局在两者间传递。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from typing import TYPE_CHECKING, cast

import httpx
from mem0 import Memory
from mem0.configs.base import MemoryConfig
from mem0.embeddings.configs import EmbedderConfig
from mem0.llms.configs import LlmConfig
from mem0.vector_stores.configs import VectorStoreConfig

if TYPE_CHECKING:
    # Mem0Memory 仅存于 stubs，运行期 mem0 包未导出该名字；
    # 借助 from __future__ import annotations 让返回类型注解延迟求值。
    from mem0 import Mem0Memory

from config import (
    DEEPSEEK_KEY,
    EMBEDDING_MODEL,
    MEM0_LOG_FILE,
    MODEL,
    RERANK_MIN_RELEVANCE,
    RERANK_MIN_SIMILARITY,
    RERANKER_MODEL,
    SILICONFLOW_BASE,
    SILICONFLOW_KEY,
    LOCAL_TZ,
)
from models import Mem0Diag, Mem0LogEntry, MemMetadata, MemSearchItem, RerankResult, UserCtx
from utils import format_spoken_by, split_spoken_by


# ── Mem0（同步操作，用锁保护） ───────────────────────────

mem0_lock: asyncio.Lock = asyncio.Lock()

# Mem0 搜索日志（环形缓冲，供 /mem0 WebUI 查看）
mem0_log: list[Mem0LogEntry] = []
_MEM0_LOG_MAX: int = 200
_last_mem0_diag: Mem0Diag | None = None

# 加载持久化日志
if os.path.exists(MEM0_LOG_FILE):
    try:
        with open(MEM0_LOG_FILE, encoding="utf-8") as f:
            _raw_log: object = json.load(f)
            if isinstance(_raw_log, list):
                mem0_log = cast(list[Mem0LogEntry], _raw_log[:_MEM0_LOG_MAX])
    except Exception as e:
        print(f"[Mem0] 日志加载失败: {e}")

memory = Memory(
    MemoryConfig(
        llm=LlmConfig(
            provider="deepseek",
            config={"model": MODEL, "api_key": DEEPSEEK_KEY},
        ),
        embedder=EmbedderConfig(
            provider="openai",
            config={
                "model": EMBEDDING_MODEL,
                "api_key": SILICONFLOW_KEY,
                "openai_base_url": SILICONFLOW_BASE,
            },
        ),
        vector_store=VectorStoreConfig(
            provider="qdrant",
            config={
                "path": os.path.join(os.path.dirname(__file__), "qdrant_data"),
                "embedding_model_dims": 1024,
            },
        ),
    )
)


def mem_delete(memory_id: str) -> None:
    """同步删记忆（在锁内调用）"""
    memory.delete(memory_id)


def mem_add(text: str, user_id: str, spoken_by: str = "手动添加") -> Mem0Memory:
    """同步新增记忆（在锁内调用）"""
    return memory.add(
        text,
        user_id=user_id,
        agent_id="qq-bot",
        infer=False,
        metadata={"spoken_by": split_spoken_by(spoken_by) or ["手动添加"]},
    )


def mem_update(memory_id: str, text: str, user_id: str | None = None,
                spoken_by: str | None = None) -> Mem0Memory:
    """同步更新记忆（在锁内调用）"""
    old = memory.get(memory_id)
    old_metadata: MemMetadata = {}
    if old:
        md = old.get("metadata")
        if md:
            old_metadata = md
    metadata: MemMetadata = {**old_metadata}
    if user_id:
        metadata["user_id"] = user_id
    if spoken_by is not None:
        metadata["spoken_by"] = split_spoken_by(spoken_by)
    return memory.update(memory_id, text, metadata=metadata)


def mem_get_all(user_id: str | None = None, limit: int = 500) -> list[MemSearchItem]:
    """获取所有记忆（同步）"""
    try:
        if user_id:
            raw_result = memory.get_all(filters={"user_id": user_id})
            # Mem0 API 可能返回 dict 含 "results" 键，或直接返回 list
            if isinstance(raw_result, dict):
                val = raw_result.get("results", [])
            else:
                val = raw_result
            items = cast(list[MemSearchItem], val)
            return items[:limit]
        else:
            # 从 Qdrant 直接拉全部向量，再拼接记忆文本
            vs = memory.vector_store
            raw_rows_out = vs.list(filters=None, top_k=limit)
            # vs.list 可能返回 tuple(list, ...) 或 list
            if isinstance(raw_rows_out, tuple):
                raw_rows = cast(list[object], raw_rows_out[0] if raw_rows_out else [])
            else:
                raw_rows = cast(list[object], raw_rows_out)
            items: list[MemSearchItem] = []
            for row in raw_rows:
                payload: dict[str, object] = getattr(row, "payload", None) or {}
                metadata = cast(MemMetadata, {
                    k: v for k, v in payload.items() if k not in {
                        "data", "hash", "created_at", "updated_at", "id", "text_lemmatized",
                        "user_id", "agent_id", "run_id", "actor_id", "role", "attributed_to",
                    }
                })
                items.append({
                    "id": str(getattr(row, "id", "")),
                    "memory": str(payload.get("data", "")),
                    "user_id": str(payload.get("user_id", "")),
                    "agent_id": str(payload.get("agent_id", "")),
                    "run_id": str(payload.get("run_id", "")),
                    "created_at": str(payload.get("created_at", "")),
                    "updated_at": str(payload.get("updated_at", "")),
                    "metadata": metadata,
                })
        return items[:limit]
    except Exception as e:
        print(f"[Mem0] get_all 异常: {e}")
        return []


def _siliconflow_rerank(query: str, documents: list[str]) -> list[RerankResult]:
    """调硅基流动 Reranker API 重排所有文档（同步）"""
    if not documents:
        return []
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{SILICONFLOW_BASE}/rerank",
                headers={"Authorization": f"Bearer {SILICONFLOW_KEY}"},
                json={
                    "model": RERANKER_MODEL,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                },
            )
            resp.raise_for_status()
            data = cast(dict[str, object], resp.json())
            raw = data.get("results", [])
            if not isinstance(raw, list):
                return []
            return cast(list[RerankResult], raw)
    except Exception as e:
        print(f"[Reranker] 重排失败: {e}")
        return []


def _mem_count(filters: dict[str, str] | None = None) -> int:
    """返回当前过滤条件下的记忆总数，用作检索数量，避免人为截断。"""
    try:
        vs = memory.vector_store
        query_filter = vs._create_filter(filters) if filters else None
        count = vs.client.count(
            collection_name=vs.collection_name,
            count_filter=query_filter,
            exact=True,
        )
        return int(getattr(count, "count", 0) or 0)
    except Exception as e:
        print(f"[Mem0] count 异常: {e}")
        return 0


def _mem_search(query: str) -> dict[str, list[MemSearchItem]]:
    """跨用户检索记忆：海选(Embedding分) → 选拔(Reranker分)，双门槛全入围"""
    global _last_mem0_diag
    clean = re.sub(r"\[CQ:\w+,.*?\]", "", query).strip()
    if not clean:
        clean = query
    try:
        filters: dict[str, str] = {"user_id": "*"}
        total_memories = _mem_count(filters)
        if total_memories <= 0:
            return {"results": []}

        # 海选：Embedding 粗筛，不再设置固定候选上限，按当前记忆库总量取回。
        raw_result = memory.search(clean, filters=filters, top_k=total_memories)
        # Mem0 API 返回 dict 含 "results" 键，或直接返回 list
        if isinstance(raw_result, dict):
            results_val = raw_result.get("results", [])
        else:
            results_val = raw_result
        items = cast(list[MemSearchItem], results_val)
        if not items:
            return {"results": []}

        # 海选：统计 Embedding 分数分布
        emb_scores: list[float] = [float(item.get("score", 0)) for item in items]
        print(f"[Reranker] 海选 {len(items)} 条, Embedding分 min={min(emb_scores):.3f} max={max(emb_scores):.3f} avg={sum(emb_scores)/len(emb_scores):.3f}")

        qualified: list[MemSearchItem] = [item for item in items if float(item.get("score", 0)) >= RERANK_MIN_SIMILARITY]
        dropped = len(items) - len(qualified)
        if dropped > 0:
            print(f"[Reranker] 海选淘汰 {dropped} 条 (<{RERANK_MIN_SIMILARITY})")
        if not qualified:
            return {"results": []}

        # 太少了不值得重排
        if len(qualified) <= 2:
            return {"results": qualified}

        # 选拔：Reranker 精排
        docs: list[str] = [str(item.get("memory", "")) for item in qualified]
        rerank_results: list[RerankResult] = _siliconflow_rerank(clean, docs)

        if not rerank_results:
            return {"results": qualified}

        # Reranker 偶尔会返回重复 index；同一候选只保留最高分，避免 WebUI 和最终结果重复显示。
        best_by_index: dict[int, RerankResult] = {}
        invalid_rerank_indexes = 0
        for rr in rerank_results:
            idx_raw = rr.get("index")
            idx: int | None = None
            if idx_raw is not None:
                try:
                    idx = int(idx_raw)
                except (TypeError, ValueError):
                    invalid_rerank_indexes += 1
                    continue
            if idx is None:
                invalid_rerank_indexes += 1
                continue
            if not (0 <= idx < len(qualified)):
                invalid_rerank_indexes += 1
                continue

            score: float = float(rr.get("relevance_score", 0))
            old = best_by_index.get(idx)
            if old is None or score > float(old.get("relevance_score", 0)):
                rr_item = cast(RerankResult, dict(rr))
                rr_item["index"] = idx
                best_by_index[idx] = rr_item

        duplicate_rerank_indexes = len(rerank_results) - invalid_rerank_indexes - len(best_by_index)
        if duplicate_rerank_indexes > 0:
            print(f"[Reranker] 去重重复 index {duplicate_rerank_indexes} 条")

        sorted_reranks: list[RerankResult] = sorted(
            best_by_index.values(),
            key=lambda r: float(r.get("relevance_score", 0)),
            reverse=True,
        )

        # 双门槛：Reranker 分 ≥ 选拔分 的才入围
        final: list[MemSearchItem] = []
        failed: list[tuple[float, str]] = []
        for rr in sorted_reranks:
            score = float(rr.get("relevance_score", 0))
            ri = int(rr.get("index", -1))
            if ri < 0 or ri >= len(qualified):
                continue
            if score < RERANK_MIN_RELEVANCE:
                failed.append((score, str(qualified[ri].get("memory", ""))[:30]))
                continue
            item: MemSearchItem = {**qualified[ri]}
            item["score"] = score
            final.append(item)

        # 选拔：统计 Reranker 分数分布
        all_rerank: list[float] = [float(r.get("relevance_score", 0)) for r in sorted_reranks]
        if all_rerank:
            print(f"[Reranker] 选拔 {len(all_rerank)} 条, Reranker分 min={min(all_rerank):.3f} max={max(all_rerank):.3f}")

        if failed:
            print(f"[Reranker] 选拔淘汰 {len(failed)} 条 (<{RERANK_MIN_RELEVANCE}):")
            for s, txt in failed[:5]:
                print(f"  [{s:.3f}] {txt}...")

        # 记录诊断数据
        _last_mem0_diag = cast(Mem0Diag, {
            "query": clean,
            "candidates": len(items),
            "emb_min": round(min(emb_scores), 3),
            "emb_max": round(max(emb_scores), 3),
            "emb_avg": round(sum(emb_scores) / len(emb_scores), 3),
            "qualified": len(qualified),
            "dropped_emb": dropped,
            "rerank_count": len(all_rerank) if all_rerank else 0,
            "rerank_min": round(min(all_rerank), 3) if all_rerank else 0,
            "rerank_max": round(max(all_rerank), 3) if all_rerank else 0,
            "passed": len(final),
            "dropped_rerank": len(failed),
            "deduped_rerank": duplicate_rerank_indexes,
            "invalid_rerank_indexes": invalid_rerank_indexes,
            "emb_candidates": cast(list[dict[str, object]], [{
                "id": item.get("id", ""),
                "memory": str(item.get("memory", ""))[:80],
                "score": round(float(item.get("score", 0)), 3),
                "passed": float(item.get("score", 0)) >= RERANK_MIN_SIMILARITY,
            } for item in items]),
            "rerank_candidates": cast(list[dict[str, object]], [
                {
                    "id": qualified[ri2].get("id", ""),
                    "memory": str(qualified[ri2].get("memory", ""))[:80],
                    "rerank_score": round(float(rr.get("relevance_score", 0)), 3),
                    "emb_score": round(float(qualified[ri2].get("score", 0)), 3),
                    "passed": float(rr.get("relevance_score", 0)) >= RERANK_MIN_RELEVANCE,
                }
                for rr in sorted_reranks
                if 0 <= (ri2 := int(rr.get("index", -1))) < len(qualified)
            ]),
        })
        return {"results": final}
    except Exception as e:
        print(f"[Mem0] search 异常: {e}")
        _last_mem0_diag = None
        return {"results": []}


# ── 记忆检索（滚雪球） ──────────────────────────────────


async def search_memories(query: str, max_rounds: int = 2,
                          capture: bool = False, user_ctx: UserCtx | None = None) -> list[MemSearchItem]:
    """滚雪球记忆检索，直到 id 集合闭合或达到最大轮数"""
    global _last_mem0_diag
    known: dict[str, MemSearchItem] = {}
    q: str = query
    rounds_diag: list[Mem0Diag] = []

    for _ in range(max_rounds):
        async with mem0_lock:
            result = _mem_search(q)

        # 捕获本轮诊断
        if capture:
            if _last_mem0_diag:
                _last_mem0_diag["round"] = len(rounds_diag) + 1
                _last_mem0_diag["round_query"] = q[:100]
                rounds_diag.append(cast(Mem0Diag, dict(_last_mem0_diag)))

        if not result:
            print(f"[Mem0] 搜索返回异常: {result}")
            break

        items = result.get("results", [])
        print(f"[Mem0] 搜索 '{q[:30]}...' → {len(items)} 条")
        for item in items:
            if not item:
                continue
            score = float(item.get("score", 0))
            print(f"  [{str(item.get('id', '?'))[:12]}] {str(item.get('memory', ''))[:60]} ({score:.3f})")
        new_count = 0
        for item in items:
            if not item:
                continue
            mid = str(item.get("id", ""))
            if mid and mid not in known:
                known[mid] = item
                new_count += 1

        if new_count == 0:
            break

        # 拼接下轮 query
        parts: list[str] = [query]
        for m in known.values():
            if not m:
                continue
            meta = m.get("metadata") or {}
            spoken_by_raw = meta.get("spoken_by", "未知")
            spoken_by = format_spoken_by(spoken_by_raw)
            parts.append(f"[{spoken_by}] {m.get('memory', '')}")
        q = "\n".join(parts)
        # 限制 query 长度，避免超过 Mem0 的 512 token 限制
        if len(q) > 2000:
            q = q[:2000]

    # 记录到环形缓冲
    if capture and rounds_diag:
        entry: Mem0LogEntry = {
            "ts": datetime.now(LOCAL_TZ).isoformat(),
            "rounds": rounds_diag,
            "total": len(known),
        }
        if user_ctx:
            if "nickname" in user_ctx:
                entry["nickname"] = user_ctx["nickname"]
            if "message" in user_ctx:
                entry["message"] = user_ctx["message"]
            if "user_id" in user_ctx:
                entry["user_id"] = user_ctx["user_id"]
        mem0_log.append(entry)
        if len(mem0_log) > _MEM0_LOG_MAX:
            mem0_log.pop(0)
        try:
            with open(MEM0_LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(mem0_log, f, ensure_ascii=False)
        except Exception:
            pass

    return list(known.values())


# ── 矛盾检测 ────────────────────────────────────────────


def detect_conflicts(memories: list[MemSearchItem]) -> list[str]:
    """检测同主题不同 spoken_by 的矛盾记忆"""
    by_topic: dict[str, list[MemSearchItem]] = {}
    for m in memories:
        if not m:
            continue
        topic = str(m.get("memory", ""))[:10]
        by_topic.setdefault(topic, []).append(m)

    warnings: list[str] = []
    for items in by_topic.values():
        speakers: set[str] = set()
        for it in items:
            meta = it.get("metadata") or {}
            speakers.update(split_spoken_by(meta.get("spoken_by", "?")))
        if len(speakers) > 1 and len(items) > 1:
            ids = [str(it.get("id", "")) for it in items]
            warnings.append(
                f"⚠ 以下记忆关于同一主题但来源不同，可能存在矛盾：{', '.join(ids)}"
            )
    return warnings


def format_memories(memories: list[MemSearchItem]) -> str:
    """格式化记忆列表"""
    lines: list[str] = []
    for m in memories:
        if not m:
            continue
        meta = m.get("metadata") or {}
        spoken_by_raw = meta.get("spoken_by", "未知")
        spoken_by = format_spoken_by(spoken_by_raw)
        created = str(m.get("created_at", ""))[:10]
        lines.append(f"[{m.get('id', '?')}] [{spoken_by}] {m.get('memory', '')}（{created}）")
    return "\n".join(lines)
