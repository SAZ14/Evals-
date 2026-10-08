"""Real provider clients against a fake HTTP transport: request shape and response parsing, no network."""

import asyncio
import json

import httpx2 as httpx  # the HTTP library these SDK versions use; transitive dependency
from ledger.env.tools import TOOL_SPECS
from ledger.models.anthropic_client import AnthropicClient
from ledger.models.base import Message
from ledger.models.openai_compat import OpenAICompatClient

MESSAGES = [Message(role="system", content="You are Kite support."), Message(role="user", content="balance?")]


def fake_transport(response: dict, seen: list[dict]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=response)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_openai_compat_client() -> None:
    seen: list[dict] = []
    response = {
        "id": "x", "object": "chat.completion", "created": 0, "model": "grok-test",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "get_balance", "arguments": "{\"account_id\": \"acc_1\"}"}}]}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127},
    }
    client = OpenAICompatClient("grok", "grok-test", {"temperature": 0}, base_url="http://fake/v1", api_key="k",
                                pass_seed=True, http_client=fake_transport(response, seen))
    resp = asyncio.run(client.chat(MESSAGES, TOOL_SPECS, seed=3))
    assert resp.tool_calls[0].name == "get_balance" and resp.tool_calls[0].arguments == {"account_id": "acc_1"}
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (120, 7)
    sent = seen[0]
    assert sent["temperature"] == 0 and sent["seed"] == 3 and sent["model"] == "grok-test"
    assert sent["messages"][0] == {"role": "system", "content": "You are Kite support."}
    assert sent["tools"][0]["type"] == "function" and len(sent["tools"]) == 9

    asyncio.run(client.chat(MESSAGES, [TOOL_SPECS[0]], tool_choice="lookup_customer"))
    assert seen[1]["tool_choice"] == {"type": "function", "function": {"name": "lookup_customer"}}


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
ANTHROPIC_REPLY = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-test",
    "content": [THINKING, {"type": "text", "text": "Checking."},
                {"type": "tool_use", "id": "tu_1", "name": "get_balance", "input": {"account_id": "acc_1"}}],
    "stop_reason": "tool_use", "stop_sequence": None, "usage": {"input_tokens": 90, "output_tokens": 12},
}


def anthropic_client(handler, params: dict | None = None) -> AnthropicClient:
    return AnthropicClient("claude", "claude-test", params or {}, api_key="k", base_url="http://fake",
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_anthropic_client_request_and_thinking_replay() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=ANTHROPIC_REPLY)

    client = anthropic_client(handler, {"temperature": 0})  # e.g. an older model that still accepts it
    resp = asyncio.run(client.chat(MESSAGES, TOOL_SPECS))
    assert resp.text == "Checking." and resp.tool_calls[0].arguments == {"account_id": "acc_1"}
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (90, 12)
    assert resp.stop_reason == "tool_use" and resp.raw_content[0] == THINKING
    sent = seen[0]
    assert sent["system"] == "You are Kite support." and sent["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "balance?"}]}]
    assert sent["max_tokens"] == 16000 and sent["temperature"] == 0 and "seed" not in sent
    assert sent["tools"][0]["input_schema"]["type"] == "object" and "tool_choice" not in sent

    # The next turn sends the assistant content back exactly as returned, thinking block included.
    history = MESSAGES + [Message(role="assistant", content=resp.text, tool_calls=resp.tool_calls,
                                  raw_content=resp.raw_content),
                          Message(role="tool", tool_call_id="tu_1", name="get_balance", content='{"ok": true}')]
    asyncio.run(client.chat(history, TOOL_SPECS))
    assert seen[1]["messages"][1] == {"role": "assistant", "content": ANTHROPIC_REPLY["content"]}
    assert seen[1]["messages"][2]["content"][0]["type"] == "tool_result"


def test_anthropic_forced_tool_choice_falls_back_to_auto() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body["tool_choice"]["type"] == "tool":
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": 'tool_choice: type "tool" and "any" are not supported for this model.'}})
        return httpx.Response(200, json=ANTHROPIC_REPLY)

    client = anthropic_client(handler)
    resp = asyncio.run(client.chat(MESSAGES, TOOL_SPECS, tool_choice="get_balance"))
    assert resp.tool_calls[0].name == "get_balance"
    assert [b["tool_choice"]["type"] for b in seen] == ["tool", "auto"]
    asyncio.run(client.chat(MESSAGES, TOOL_SPECS, tool_choice="get_balance"))  # remembered: straight to auto
    assert [b["tool_choice"]["type"] for b in seen] == ["tool", "auto", "auto"]
