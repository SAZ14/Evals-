"""Anthropic Messages API client."""

from __future__ import annotations

import time
from typing import Any

from ledger.models.base import Message, ModelClient, ModelResponse, ToolCall, Usage

DEFAULT_MAX_TOKENS = 1024


def to_anthropic_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    """Split out the system prompt and convert to content blocks, merging consecutive same-role turns."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            role, blocks = "user", [{"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}]
        elif m.role == "assistant":
            role = "assistant"
            blocks = [{"type": "text", "text": m.content}] if m.content else []
            blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
            if not blocks:  # the API rejects empty assistant turns
                blocks = [{"type": "text", "text": "(no response)"}]
        else:
            role, blocks = "user", [{"type": "text", "text": m.content}]
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})
    return system, out


class AnthropicClient(ModelClient):
    provider = "anthropic"

    def __init__(self, name: str, model_id: str, params: dict[str, Any] | None, *, api_key: str,
                 pass_seed: bool = False) -> None:
        super().__init__(name, model_id, params, pass_seed)
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=api_key, max_retries=4, timeout=120)

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        system, converted = to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {"model": self.model_id, "max_tokens": DEFAULT_MAX_TOKENS,
                                  "messages": converted, **self.params}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                               for t in tools]
        if tool_choice:
            kwargs["tool_choice"] = {"type": "tool", "name": tool_choice}
        start = time.perf_counter()
        resp = await self.client.messages.create(**kwargs)  # the API has no seed parameter
        latency = time.perf_counter() - start
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input)) for b in resp.content if b.type == "tool_use"]
        usage = Usage(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        return ModelResponse(text=text, tool_calls=calls, usage=usage, model_id=resp.model, latency_s=latency)
