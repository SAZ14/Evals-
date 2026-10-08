"""Anthropic Messages API client."""

from __future__ import annotations

import time
from typing import Any

from ledger.models.base import Message, ModelClient, ModelResponse, ToolCall, Usage

# Thinking is on by default on current models and counts toward max_tokens, so leave generous room.
DEFAULT_MAX_TOKENS = 16000
# anthropic>=1.0 removed sampling params from messages.create(). Current models reject them (400); older models
# still accept them, so if config sets one it is sent through extra_body.
SAMPLING_KEYS = ("temperature", "top_p", "top_k")


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
            if m.raw_content is not None:  # replay exactly what the API returned, thinking blocks included
                blocks = [dict(b) for b in m.raw_content]
            else:
                blocks = [{"type": "text", "text": m.content}] if m.content else []
                blocks += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                           for c in m.tool_calls]
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

        self.client = AsyncAnthropic(api_key=api_key, max_retries=4, timeout=600)
        # Some current models reject forced tool_choice; we switch to "auto" the first time one does.
        self.forced_tool_choice = True

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        from anthropic import BadRequestError

        system, converted = to_anthropic_messages(messages)
        params = {k: v for k, v in self.params.items() if k not in SAMPLING_KEYS}
        sampling = {k: v for k, v in self.params.items() if k in SAMPLING_KEYS}
        kwargs: dict[str, Any] = {"model": self.model_id, "max_tokens": DEFAULT_MAX_TOKENS, "messages": converted,
                                  **params}
        if sampling:
            kwargs["extra_body"] = {**kwargs.get("extra_body", {}), **sampling}
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                               for t in tools]
        start = time.perf_counter()
        resp = None  # the API has no seed parameter
        if tool_choice and self.forced_tool_choice:
            try:
                resp = await self.client.messages.create(**kwargs, tool_choice={"type": "tool", "name": tool_choice})
            except BadRequestError as exc:
                if "tool_choice" not in str(exc):
                    raise
                self.forced_tool_choice = False
        if resp is None:
            if tool_choice:  # the caller's prompt names the tool; graders retry once if it isn't called
                kwargs["tool_choice"] = {"type": "auto"}
            resp = await self.client.messages.create(**kwargs)
        latency = time.perf_counter() - start
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input)) for b in resp.content if b.type == "tool_use"]
        usage = Usage(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
        return ModelResponse(text=text, tool_calls=calls, usage=usage, model_id=resp.model, latency_s=latency,
                             raw_content=[b.to_dict(mode="json") for b in resp.content], stop_reason=resp.stop_reason)
