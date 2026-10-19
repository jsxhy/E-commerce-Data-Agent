"""LLM 客户端封装（OpenAI 兼容接口，DeepSeek）。

**可注入**：模块级单例 + ``set_llm_client_for_test()``，测试里换成 stub 就不会烧真实配额。
单例是「懒加载」的——import 本模块不创建网络客户端、不读密钥。

对外只暴露四个方法（stub 实现同样四个即可）：

- ``stream(messages) -> AsyncIterator[str]``   逐 token 产出
- ``complete(messages) -> str``                一次性返回全文
- ``chat_with_tools(messages, tools) -> dict`` 返回 ``{content, tool_calls}``
- ``generate_sql(question, error, schema) -> str`` 给 sql_guard 用

失败一律抛 :class:`LLMUnavailable`，上层降级为模板回答，不让整个 run 失败。
"""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Protocol, Sequence, runtime_checkable

from backend.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "你是电商数据分析助手，回答简洁、结论先行，使用中文。"


class LLMUnavailable(RuntimeError):
    """LLM 未配置 / 网络失败 / 返回异常。"""


@runtime_checkable
class LLMClientProtocol(Protocol):
    async def stream(self, messages: Sequence[dict[str, Any]], **kwargs: Any) -> AsyncIterator[str]: ...
    async def complete(self, messages: Sequence[dict[str, Any]], **kwargs: Any) -> str: ...
    async def chat_with_tools(self, messages: Sequence[dict[str, Any]],
                              tools: Sequence[dict[str, Any]] | None = None,
                              **kwargs: Any) -> dict[str, Any]: ...
    def generate_sql(self, question: str, error: str | None = None,
                     schema: str | None = None) -> str: ...


class LLMClient:
    """真实客户端：包一层 openai.AsyncOpenAI。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, temperature: float | None = None,
                 timeout: float | None = None):
        cfg = get_config()["llm"]
        self.api_key = api_key if api_key is not None else str(cfg.get("api_key") or "")
        self.base_url = base_url or str(cfg.get("base_url") or "https://api.deepseek.com")
        self.model = model or str(cfg.get("model") or "deepseek-chat")
        self.temperature = float(temperature if temperature is not None
                                 else cfg.get("temperature") or 0.2)
        self.timeout = float(timeout if timeout is not None
                             else cfg.get("timeout_seconds") or 90)
        self._client = None

    # ------------------------------------------------------------ 内部
    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise LLMUnavailable("LLM 未配置 api_key（config.local.yaml 的 llm.api_key）")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMUnavailable(f"openai 包未安装：{exc}") from exc
        self._client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url,
                                   timeout=self.timeout, max_retries=1)
        return self._client

    @staticmethod
    def _normalize(messages: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(m) for m in messages]

    # ------------------------------------------------------------ 流式
    async def stream(self, messages: Sequence[dict[str, Any]],
                     **kwargs: Any) -> AsyncIterator[str]:
        client = self._ensure_client()
        payload = {
            "model": kwargs.pop("model", self.model),
            "messages": self._normalize(messages),
            "temperature": kwargs.pop("temperature", self.temperature),
            "stream": True,
        }
        payload.update(kwargs)
        try:
            response = await client.chat.completions.create(**payload)
            async for chunk in response:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    yield content
        except LLMUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转成 LLMUnavailable
            raise LLMUnavailable(f"LLM 流式调用失败：{exc}") from exc

    # ------------------------------------------------------------ 一次性
    async def complete(self, messages: Sequence[dict[str, Any]], **kwargs: Any) -> str:
        client = self._ensure_client()
        payload = {
            "model": kwargs.pop("model", self.model),
            "messages": self._normalize(messages),
            "temperature": kwargs.pop("temperature", self.temperature),
        }
        payload.update(kwargs)
        try:
            response = await client.chat.completions.create(**payload)
            choices = getattr(response, "choices", None) or []
            if not choices:
                return ""
            message = getattr(choices[0], "message", None)
            return str(getattr(message, "content", "") or "")
        except Exception as exc:  # noqa: BLE001
            raise LLMUnavailable(f"LLM 调用失败：{exc}") from exc

    # ------------------------------------------------------------ 工具调用
    async def chat_with_tools(self, messages: Sequence[dict[str, Any]],
                              tools: Sequence[dict[str, Any]] | None = None,
                              **kwargs: Any) -> dict[str, Any]:
        client = self._ensure_client()
        payload: dict[str, Any] = {
            "model": kwargs.pop("model", self.model),
            "messages": self._normalize(messages),
            "temperature": kwargs.pop("temperature", self.temperature),
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = kwargs.pop("tool_choice", "auto")
        payload.update(kwargs)
        try:
            response = await client.chat.completions.create(**payload)
        except Exception as exc:  # noqa: BLE001
            raise LLMUnavailable(f"LLM 工具调用失败：{exc}") from exc
        choices = getattr(response, "choices", None) or []
        if not choices:
            return {"content": "", "tool_calls": []}
        message = getattr(choices[0], "message", None)
        calls: list[dict[str, Any]] = []
        for call in getattr(message, "tool_calls", None) or []:
            function = getattr(call, "function", None)
            raw_args = getattr(function, "arguments", "{}") if function is not None else "{}"
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except json.JSONDecodeError:
                parsed = {}
            calls.append({
                "id": getattr(call, "id", ""),
                "name": getattr(function, "name", "") if function is not None else "",
                "arguments": parsed,
            })
        return {"content": str(getattr(message, "content", "") or ""), "tool_calls": calls}

    # ------------------------------------------------------------ SQL 生成
    def generate_sql(self, question: str, error: str | None = None,
                     schema: str | None = None) -> str:
        """同步接口（sql_guard 是同步代码）。内部用一次性事件循环执行。"""
        from backend.app.agent.sql_guard import _build_sql_prompt

        prompt = _build_sql_prompt(question, schema or "", error)
        messages = [
            {"role": "system", "content": "你是 SQLite 专家，只输出一条 SELECT 语句，不要解释。"},
            {"role": "user", "content": prompt},
        ]
        return _run_sync(self.complete(messages, temperature=0.0))


def _run_sync(coro):
    """在同步上下文里跑协程：当前线程没有运行中的事件循环时直接 asyncio.run。"""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # 已在事件循环里（FastAPI 请求线程）：新开线程跑，避免 RuntimeError
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ------------------------------------------------------------------ 单例
_client: LLMClient | Any | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def set_llm_client_for_test(client: Any | None) -> None:
    """测试注入 stub（任何实现 4 个方法的对象）。传 None 恢复懒加载真实客户端。"""
    global _client
    _client = client


def reset_llm_client() -> None:
    global _client
    _client = None


def is_llm_configured() -> bool:
    cfg = get_config()["llm"]
    return bool(cfg.get("api_key"))


def build_messages(question: str, evidence: list[dict[str, Any]],
                   analysis: dict[str, Any] | None = None,
                   system: str | None = None) -> list[dict[str, Any]]:
    """构造回答用 prompt：给证据 JSON + 明确的格式要求。"""
    analysis = analysis or {}
    evidence_json = json.dumps(evidence or [], ensure_ascii=False, indent=2, default=str)
    analysis_json = json.dumps(
        {k: v for k, v in analysis.items() if k in
         ("route", "risk", "sql", "columns", "rows", "row_count", "product_name",
          "product_id", "bad_review_count", "avg_rating", "error")},
        ensure_ascii=False, default=str)
    instructions = (
        "请基于下面给出的证据回答用户问题。\n"
        "输出要求（严格遵守）：\n"
        "1. 使用 markdown 格式；\n"
        "2. **先给结论**（1~2 句，直接回答用户问题）；\n"
        "3. 再列要点（用 `-` 列表，每条一句话，引用具体证据里的数字或原文片段）；\n"
        "4. 最后附一节 `### 结构化查询`，说明本次用到的数据来源"
        "（SQL 结果条数 / 语义检索命中数），没有就用一句话说明「本次未执行结构化查询」；\n"
        "5. 不要编造证据里没有的商品、数字或日期；证据不足时明确说「证据不足」。"
    )
    user = (
        f"用户问题：{question}\n\n"
        f"分析上下文：{analysis_json}\n\n"
        f"证据（JSON）：\n```json\n{evidence_json}\n```\n\n"
        f"{instructions}"
    )
    return [
        {"role": "system", "content": system or DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


__all__ = [
    "LLMUnavailable", "LLMClient", "LLMClientProtocol", "DEFAULT_SYSTEM_PROMPT",
    "get_llm_client", "set_llm_client_for_test", "reset_llm_client", "is_llm_configured",
    "build_messages",
]
