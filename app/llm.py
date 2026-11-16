"""Thin streaming wrapper over the OpenAI Chat Completions API.

Always streams (so Q&A text reaches the caller token by token), accumulates
tool-call deltas, and returns token usage for cost accounting.
Determinism: temperature=0 and seed=0 on every call.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from openai import AsyncOpenAI

from .config import settings


@dataclass
class Completion:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # {id, name, arguments}
    prompt_tokens: int = 0
    completion_tokens: int = 0
    ms: float = 0.0
    finish_reason: str | None = None


class LLMNotConfigured(RuntimeError):
    pass


class LLM:
    def __init__(self):
        self.model = settings.openai_model
        key = os.environ.get("OPENAI_API_KEY", "")
        # Start without a key (so `make up` and /health work), fail per request instead.
        self.configured = bool(key) and key != "sk-..."
        self.client = AsyncOpenAI(
            api_key=key or "not-configured",
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout_s,
            max_retries=2,
        )

    async def stream(self, messages: list[dict], tools: list[dict] | None = None,
                     tool_choice: str | dict | None = None) -> AsyncIterator[tuple[str, object]]:
        """Yields ("delta", str) for content tokens, then ("done", Completion)."""
        if not self.configured:
            raise LLMNotConfigured("OPENAI_API_KEY is not set (see .env.example)")
        t0 = time.perf_counter()
        kwargs: dict = dict(model=self.model, messages=messages, temperature=0, seed=0, stream=True,
                            stream_options={"include_usage": True})
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = False
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
        out = Completion()
        partial: dict[int, dict] = {}
        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.usage:
                out.prompt_tokens = chunk.usage.prompt_tokens or 0
                out.completion_tokens = chunk.usage.completion_tokens or 0
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            d = choice.delta
            if d and d.content:
                out.content += d.content
                yield "delta", d.content
            if d and d.tool_calls:
                for tc in d.tool_calls:
                    slot = partial.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments
            if choice.finish_reason:
                out.finish_reason = choice.finish_reason
        out.tool_calls = [partial[i] for i in sorted(partial)]
        out.ms = round((time.perf_counter() - t0) * 1000, 1)
        yield "done", out

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(prompt_tokens / 1e6 * settings.price_in_per_1m
                     + completion_tokens / 1e6 * settings.price_out_per_1m, 6)
