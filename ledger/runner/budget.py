"""Budget guard: tracks estimated real-API spend in runs/spend.json and refuses calls that could pass the cap.

Spend is estimated from token counts x config prices. Each uncached call first reserves a pessimistic estimate of
its own cost; it is refused if spent + reserved + estimate would exceed the cap. Cache hits cost nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ledger.config import Price
from ledger.models.base import Message, ModelClient, ModelResponse, Usage

OUTPUT_RESERVE_TOKENS = 4000  # assumed worst-case output per call when estimating before the call
CHARS_PER_TOKEN = 3  # pessimistic: real text averages ~4 chars per token


class BudgetExceeded(RuntimeError):
    """Starting this call could push estimated spend past the cap."""


def cost_usd(usage: Usage, price: Price) -> float:
    return (usage.input_tokens * price.input + usage.output_tokens * price.output) / 1e6


def estimate_usd(messages: list[Message], tools: list[dict[str, Any]], price: Price) -> float:
    chars = len(json.dumps([m.model_dump() for m in messages], ensure_ascii=False)) + len(json.dumps(tools))
    return cost_usd(Usage(input_tokens=chars // CHARS_PER_TOKEN, output_tokens=OUTPUT_RESERVE_TOKENS), price)


class BudgetGuard:
    def __init__(self, path: Path, cap_usd: float) -> None:
        self.path, self.cap = Path(path), cap_usd
        self.state: dict[str, Any] = {"cap_usd": cap_usd, "spent_usd": 0.0, "calls": 0, "by_model": {}}
        if self.path.is_file():
            self.state.update(json.loads(self.path.read_text()))
            self.state["cap_usd"] = cap_usd
        self.reserved = 0.0

    @property
    def spent(self) -> float:
        return float(self.state["spent_usd"])

    def reserve(self, model: str, estimate: float) -> None:
        if self.spent + self.reserved + estimate > self.cap:
            raise BudgetExceeded(f"budget cap ${self.cap:.2f} reached: spent ${self.spent:.4f}, in flight "
                                 f"${self.reserved:.4f}, next {model} call estimated at ${estimate:.4f}")
        self.reserved += estimate

    def settle(self, model: str, estimate: float, actual: float | None) -> None:
        """Release a reservation; record the actual cost if the call completed (None if it failed)."""
        self.reserved = max(0.0, self.reserved - estimate)
        if actual is None:
            return
        self.state["spent_usd"] = self.spent + actual
        self.state["calls"] = int(self.state["calls"]) + 1
        self.state["by_model"][model] = float(self.state["by_model"].get(model, 0.0)) + actual
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2) + "\n")
        tmp.replace(self.path)


class GuardedClient(ModelClient):
    """Wraps a real client so every call is checked against, and recorded in, the budget."""

    def __init__(self, inner: ModelClient, guard: BudgetGuard, price: Price) -> None:
        super().__init__(inner.name, inner.model_id, inner.params, inner.pass_seed)
        self.inner, self.guard, self.price = inner, guard, price
        self.provider = inner.provider

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        estimate = estimate_usd(messages, tools, self.price)
        self.guard.reserve(self.name, estimate)
        try:
            resp = await self.inner.chat(messages, tools, tool_choice=tool_choice, seed=seed)
        except BaseException:
            self.guard.settle(self.name, estimate, None)
            raise
        self.guard.settle(self.name, estimate, cost_usd(resp.usage, self.price))
        return resp
