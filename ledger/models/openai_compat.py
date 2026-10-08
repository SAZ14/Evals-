"""OpenAI-compatible chat completions client: OpenAI, xAI (https://api.x.ai/v1), DeepSeek, etc."""

from __future__ import annotations

import json
import time
from typing import Any

from ledger.models.base import Message, ModelClient, ModelResponse, ToolCall, Usage


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    """Tool-call arguments arrive as a JSON string. Keep unparseable ones so the tool returns an error."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_unparseable_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_unparseable_arguments": raw}


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}}
                    for c in m.tool_calls]
            out.append(msg)
        else:
            out.append({"role": m.role, "content": m.content})
    return out


class OpenAICompatClient(ModelClient):
    provider = "openai_compat"

    def __init__(self, name: str, model_id: str, params: dict[str, Any] | None, *, base_url: str | None,
                 api_key: str, pass_seed: bool = False) -> None:
        super().__init__(name, model_id, params, pass_seed)
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=4, timeout=120)

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        kwargs: dict[str, Any] = {"model": self.model_id, "messages": to_openai_messages(messages), **self.params}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
        if tool_choice:
            kwargs["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
        if self.pass_seed:
            kwargs["seed"] = seed
        start = time.perf_counter()
        resp = await self.client.chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        msg = resp.choices[0].message
        calls = [ToolCall(id=c.id, name=c.function.name, arguments=_parse_arguments(c.function.arguments))
                 for c in (msg.tool_calls or []) if c.type == "function"]
        usage = Usage(input_tokens=resp.usage.prompt_tokens, output_tokens=resp.usage.completion_tokens) \
            if resp.usage else Usage()
        return ModelResponse(text=msg.content or "", tool_calls=calls, usage=usage, model_id=resp.model or self.model_id,
                             latency_s=latency)
