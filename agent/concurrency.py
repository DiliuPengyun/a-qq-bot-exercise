"""对话级并发控制：同一 user_id 同时只能有一个生成任务。

新消息到达时取消旧任务，提取已闭合的 <message> 直接发送，
未闭合尾部作为 <draft> 打回新 prompt。
"""

import asyncio


class GenerationContext:
    """一次生成任务的可观察状态，供取消时提取部分产出。"""

    def __init__(self) -> None:
        self.complete_messages: list[str] = []
        self.draft: str = ""
        self.quote: bool = False
        self.task: asyncio.Task[list[tuple[str, bool]]] | None = None


_generations: dict[str, GenerationContext] = {}


async def acquire(user_id: str) -> GenerationContext | None:
    """取消旧任务（如有）并返回其上下文。

    如果没有旧任务，返回 None。
    旧任务已完成的也返回其上下文（draft 为空，complete_messages 可能有值）。
    """
    old_ctx = _generations.get(user_id)
    if old_ctx is None:
        return None
    task = old_ctx.task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    return old_ctx


def register(user_id: str, ctx: GenerationContext) -> None:
    """注册新的生成上下文。"""
    _generations[user_id] = ctx
