"""人工确认的等待/决策存储（纯内存，重启即失效）。

为什么不用数据库：契约里 410「确认检查点已失效」的语义就是「进程重启后没了」。
LangGraph 的 MemorySaver 检查点同样在内存里，两者生命周期一致，配合起来才自洽。

并发保护：同一 run 的 ``take_decision`` 只成功一次，第二次返回 ``duplicate=True``，
上层据此返回 409「该运行已处理过确认」。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"

# 等待确认的默认超时（秒）：SSE 长连接不能被无限期占住
DEFAULT_TIMEOUT = 300.0


class ConfirmStore:
    """``{run_id: {status, actions, created_at, decided_at}}`` + asyncio 事件。"""

    def __init__(self) -> None:
        self._entries: dict[int, dict[str, Any]] = {}
        self._events: dict[int, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ 写
    async def put_pending(self, run_id: int, actions: list[dict[str, Any]],
                          risk: str = "high", message: str = "") -> dict[str, Any]:
        key = int(run_id)
        async with self._lock:
            entry = {
                "status": STATUS_PENDING,
                "actions": list(actions or []),
                "risk": risk,
                "message": message,
                "created_at": time.time(),
                "decided_at": None,
            }
            self._entries[key] = entry
            self._events[key] = asyncio.Event()
            return dict(entry)

    async def take_decision(self, run_id: int, decision: str) -> dict[str, Any]:
        """登记用户决策。

        返回 ``{"ok": bool, "duplicate": bool, "reason": str, "entry": {...}}``：
        - 没有该 run / 已处理过 → ok=False，reason 区分 ``missing`` / ``duplicate``
        - 成功 → ok=True，并唤醒 ``await_confirm``
        """
        key = int(run_id)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return {"ok": False, "duplicate": False, "reason": "missing", "entry": None}
            if entry["status"] != STATUS_PENDING:
                return {"ok": False, "duplicate": True, "reason": "duplicate", "entry": dict(entry)}
            normalized = STATUS_APPROVED if decision == "approve" else STATUS_REJECTED
            entry["status"] = normalized
            entry["decision"] = "approve" if normalized == STATUS_APPROVED else "reject"
            entry["decided_at"] = time.time()
            event = self._events.get(key)
            if event is not None:
                event.set()
            return {"ok": True, "duplicate": False, "reason": "", "entry": dict(entry)}

    async def mark_processed(self, run_id: int) -> None:
        key = int(run_id)
        async with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                entry["processed_at"] = time.time()

    # ------------------------------------------------------------ 读
    async def await_confirm(self, run_id: int, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any] | None:
        """等用户决策。超时返回 None（调用方按「未确认」处理）。"""
        key = int(run_id)
        event = self._events.get(key)
        if event is None:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None
        async with self._lock:
            entry = self._entries.get(key)
            return dict(entry) if entry else None

    async def get(self, run_id: int) -> dict[str, Any] | None:
        async with self._lock:
            entry = self._entries.get(int(run_id))
            return dict(entry) if entry else None

    async def is_waiting(self, run_id: int) -> bool:
        entry = await self.get(run_id)
        return bool(entry and entry["status"] == STATUS_PENDING)

    async def status(self, run_id: int) -> str | None:
        entry = await self.get(run_id)
        return entry["status"] if entry else None

    async def is_known(self, run_id: int) -> bool:
        """检查点是否还在（重启后为 False → 上层返回 410）。"""
        async with self._lock:
            return int(run_id) in self._entries

    # ------------------------------------------------------------ 清理
    async def clear(self, run_id: int | None = None) -> None:
        async with self._lock:
            if run_id is None:
                self._entries.clear()
                self._events.clear()
                return
            key = int(run_id)
            self._entries.pop(key, None)
            self._events.pop(key, None)

    async def clear_all(self) -> None:
        await self.clear(None)

    async def size(self) -> int:
        async with self._lock:
            return len(self._entries)


_confirm_store = ConfirmStore()


def get_confirm_store() -> ConfirmStore:
    return _confirm_store


def reset_confirm_store() -> None:
    """测试用：丢弃单例（模拟进程重启）。"""
    global _confirm_store
    _confirm_store = ConfirmStore()


__all__ = [
    "STATUS_PENDING", "STATUS_APPROVED", "STATUS_REJECTED", "DEFAULT_TIMEOUT",
    "ConfirmStore", "get_confirm_store", "reset_confirm_store",
]
