"""Provider-neutral chat types and the ModelClient interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None  # tool results only
    name: str | None = None  # tool name, tool results only
    step: int | None = None  # agent-loop step that produced this message (metadata, not sent to models)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(input_tokens=self.input_tokens + other.input_tokens,
                     output_tokens=self.output_tokens + other.output_tokens)


class ModelResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model_id: str = ""
    cached: bool = False
    latency_s: float = 0.0


class ModelClient(ABC):
    """chat(messages, tools) -> response. System prompt is the first message with role 'system'.

    `tools` are provider-neutral specs: {"name", "description", "parameters": <JSON schema>}.
    `tool_choice` names one tool the model must call (used for structured judge output).
    """

    provider: str = "base"

    def __init__(self, name: str, model_id: str, params: dict[str, Any] | None = None,
                 pass_seed: bool = False) -> None:
        self.name = name
        self.model_id = model_id
        self.params = dict(params or {})
        self.pass_seed = pass_seed

    @abstractmethod
    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        """Return the model's next message."""
