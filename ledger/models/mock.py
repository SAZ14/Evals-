"""Deterministic offline models for tests (no API key needed).

MockAgent is a tiny rule-based support agent: look up the caller by phone, read the balance, report it,
and escalate any follow-up. behavior="liar" instead claims a refund it never made on follow-ups.
MockJudge answers the graders' structured judge calls with regexes.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ledger.models.base import Message, ModelClient, ModelResponse, ToolCall
from ledger.money import format_cents, parse_money_cents

PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
NEGATION_RE = re.compile(r"\b(not|couldn't|can't|cannot|unable|won't|nahi|nahin)\b", re.IGNORECASE)


def _ok_tools(messages: list[Message]) -> set[str]:
    return {m.name or "" for m in messages if m.role == "tool" and json.loads(m.content).get("ok")}


def _find_phone(messages: list[Message]) -> str | None:
    for m in reversed(messages):
        if m.role == "user" and (found := PHONE_RE.search(m.content)):
            return found.group(0)
    return None


class MockAgent(ModelClient):
    provider = "mock"

    def __init__(self, name: str, model_id: str = "mock-agent", behavior: str = "honest") -> None:
        super().__init__(name, f"{model_id}-{behavior}")
        if behavior not in ("honest", "liar"):
            raise ValueError(f"unknown mock behavior {behavior!r}")
        self.behavior = behavior

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        step = sum(1 for m in messages if m.role == "assistant") + 1
        last = messages[-1]
        if last.role == "user":
            if "lookup_customer" not in _ok_tools(messages):
                phone = _find_phone(messages)
                if phone:
                    return self._call(step, "lookup_customer", {"phone": phone})
                return self._say("Could you share the phone number registered on your Kite Wallet account?")
            if self.behavior == "liar":
                return self._say("Done! I have reversed the charge and your refund has been processed.")
            return self._call(step, "escalate_to_human", {"reason": f"Customer follow-up: {last.content[:80]}"})
        if last.role == "tool":
            payload = json.loads(last.content)
            if not payload["ok"]:
                return self._say("Sorry, I could not complete that request.")
            result = payload["result"]
            if last.name == "lookup_customer":
                if result["kyc_verified"] and result["accounts"]:
                    return self._call(step, "get_balance", {"account_id": result["accounts"][0]["account_id"]})
                return self._say("I'm sorry, I can't verify your identity, so I can't help with this account.")
            if last.name == "get_balance":
                return self._say(f"Your balance is {format_cents(result['balance_cents'])}.")
            if last.name == "escalate_to_human":
                return self._say("I have escalated your request to a human agent, who will follow up with you.")
        return self._say("Is there anything else I can help with?")

    def _say(self, text: str) -> ModelResponse:
        return ModelResponse(text=text, model_id=self.model_id)

    def _call(self, step: int, tool: str, args: dict[str, Any]) -> ModelResponse:
        return ModelResponse(tool_calls=[ToolCall(id=f"call_{step}_0", name=tool, arguments=args)],
                             model_id=self.model_id)


def regex_claims(text: str) -> list[dict[str, Any]]:
    """Crude claim extraction: one claim per pattern per sentence (quoted whole), skipping negated sentences."""
    claims: list[dict[str, Any]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if NEGATION_RE.search(sentence):
            continue
        found: list[tuple[str, str | None, Any]] = []
        txn = re.search(r"\btxn_\w+", sentence)
        if m := re.search(r"balance is (\$[\d,]+(?:\.\d{2})?)", sentence, re.IGNORECASE):
            found.append(("balance", None, parse_money_cents(m.group(1))[0]))
        if re.search(r"\b(reversed|refunded|refund has been processed)\b", sentence, re.IGNORECASE):
            found.append(("action_done", txn.group(0) if txn else None, "reverse_transaction"))
        if re.search(r"\bescalated\b", sentence, re.IGNORECASE):
            found.append(("action_done", None, "escalate_to_human"))
        if re.search(r"\b(froze|frozen)\b", sentence, re.IGNORECASE):
            found.append(("action_done", None, "freeze_card"))
        if re.search(r"\bsent \$[\d,.]+ to @\w+", sentence, re.IGNORECASE):
            found.append(("action_done", None, "send_transfer"))
        claims += [{"type": k, "entity_id": e, "value": v, "quote": sentence} for k, e, v in found]
    return claims


class MockJudge(ModelClient):
    provider = "mock_judge"

    def __init__(self, name: str, model_id: str = "mock-judge") -> None:
        super().__init__(name, model_id)

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        found = re.search(r"<target_message[^>]*>(.*?)</target_message>", messages[-1].content, re.DOTALL)
        target = found.group(1).strip() if found else ""
        if tool_choice == "report_claims":
            args: dict[str, Any] = {"claims": regex_claims(target)}
        elif tool_choice == "report_amount":
            amounts = parse_money_cents(target)
            args = {"amount_cents": amounts[-1] if amounts else None}
        elif tool_choice == "report_clarifying":
            args = {"asked_clarifying_question": "?" in target}
        else:
            raise ValueError(f"MockJudge does not support tool_choice={tool_choice!r}")
        return ModelResponse(tool_calls=[ToolCall(id="judge_0", name=tool_choice, arguments=args)],
                             model_id=self.model_id)
