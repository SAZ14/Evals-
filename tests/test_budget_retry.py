"""Budget guard and provider retries (rate limits, 5xx) without network access."""

import asyncio
import json
import time
from pathlib import Path

import httpx2 as httpx  # the HTTP library these SDK versions use; transitive dependency
import pytest
import yaml

from ledger.config import DEFAULT_CONFIG, Price
from ledger.env.tools import TOOL_SPECS
from ledger.models.anthropic_client import AnthropicClient
from ledger.models.base import Message, ModelClient, ModelResponse, Usage
from ledger.models.openai_compat import OpenAICompatClient
from ledger.runner import run
from ledger.runner.budget import BudgetExceeded, BudgetGuard, GuardedClient

MESSAGES = [Message(role="system", content="sys"), Message(role="user", content="hi")]
OPENAI_OK = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
             "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
ANTHROPIC_OK = {"id": "msg", "type": "message", "role": "assistant", "model": "m",
                "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 2}}


def flaky(statuses: list[int], ok: dict, calls: list[int]) -> httpx.AsyncClient:
    """Answers with each status in turn (fast retry-after), then 200 forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= len(statuses):
            return httpx.Response(statuses[len(calls) - 1], headers={"retry-after-ms": "5"},
                                  json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
        return httpx.Response(200, json=ok)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def openai(statuses: list[int], calls: list[int]) -> OpenAICompatClient:
    return OpenAICompatClient("o", "m", {}, base_url="http://fake/v1", api_key="k",
                              http_client=flaky(statuses, OPENAI_OK, calls))


def anthropic(statuses: list[int], calls: list[int]) -> AnthropicClient:
    return AnthropicClient("a", "m", {}, api_key="k", base_url="http://fake",
                           http_client=flaky(statuses, ANTHROPIC_OK, calls))


@pytest.mark.parametrize("make", [openai, anthropic], ids=["openai_compat", "anthropic"])
def test_rate_limits_and_5xx_are_retried(make) -> None:
    calls: list[int] = []
    resp = asyncio.run(make([429, 500, 529], calls).chat(MESSAGES, TOOL_SPECS))
    assert resp.text == "ok" and len(calls) == 4


@pytest.mark.parametrize("make", [openai, anthropic], ids=["openai_compat", "anthropic"])
def test_gives_up_after_five_tries(make) -> None:
    calls: list[int] = []
    with pytest.raises(Exception, match="429|slow down"):
        asyncio.run(make([429] * 6, calls).chat(MESSAGES, TOOL_SPECS))
    assert len(calls) == 5


@pytest.mark.parametrize("make", [openai, anthropic], ids=["openai_compat", "anthropic"])
def test_bad_request_is_not_retried(make) -> None:
    calls: list[int] = []
    with pytest.raises(Exception):
        asyncio.run(make([400], calls).chat(MESSAGES, TOOL_SPECS))
    assert len(calls) == 1


class Priced(ModelClient):
    provider = "test"

    async def chat(self, messages, tools, *, tool_choice=None, seed=0) -> ModelResponse:
        return ModelResponse(text="ok", usage=Usage(input_tokens=1_000_000, output_tokens=0))


def test_guard_records_spend_and_refuses_calls_past_the_cap(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    guard = BudgetGuard(path, cap_usd=2.5)
    client = GuardedClient(Priced("p", "p-1"), guard, Price(input=1.0, output=1.0))  # each call costs $1.00
    for _ in range(3):  # $0, $1, $2 spent before each call; each pre-call estimate is ~$0.004, so all may start
        asyncio.run(client.chat(MESSAGES, []))
    with pytest.raises(BudgetExceeded):  # $3 spent: no new calls
        asyncio.run(client.chat(MESSAGES, []))
    state = json.loads(path.read_text())
    assert state["spent_usd"] == pytest.approx(3.0) and state["calls"] == 3 and state["by_model"]["p"] == 3.0
    assert BudgetGuard(path, cap_usd=2.5).spent == pytest.approx(3.0)  # persists across processes


def test_guard_reserves_estimates_for_calls_in_flight(tmp_path: Path) -> None:
    guard = BudgetGuard(tmp_path / "spend.json", cap_usd=1.0)
    guard.reserve("m", 0.6)
    with pytest.raises(BudgetExceeded, match="in flight"):
        guard.reserve("m", 0.6)
    guard.settle("m", 0.6, None)  # failed call: reservation released, nothing recorded
    guard.reserve("m", 0.6)
    assert guard.spent == 0.0


def test_runner_stops_at_the_cap_without_recording_runs(tmp_path: Path) -> None:
    cfg = yaml.safe_load(DEFAULT_CONFIG.read_text())
    cfg["run"]["budget_usd"] = 0.0001
    cfg["models"]["mock"]["price_per_mtok"] = {"input": 1.0, "output": 1.0}  # estimate per call ≈ $0.006
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    args = ["--models", "mock", "--tasks", "spending_food_aug_001,fraud_fake_support_018", "--judge", "mock_judge",
            "--run-name", "capped", "--runs-dir", str(tmp_path / "runs"), "--cache-dir", str(tmp_path / "cache"),
            "--config", str(config_path)]
    start = time.perf_counter()
    assert run.main(args) == 3
    assert time.perf_counter() - start < 30
    assert list((tmp_path / "runs" / "capped" / "trajectories").glob("*.json")) == []

    cfg["run"]["budget_usd"] = 25.0  # raise the cap: the same command resumes and finishes
    config_path.write_text(yaml.safe_dump(cfg))
    assert run.main(args) == 0
    assert len(list((tmp_path / "runs" / "capped" / "trajectories").glob("*.json"))) == 2


def test_real_models_need_a_price(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ledger.config import ConfigError, load_config
    from ledger.models import build_client

    config = load_config()
    config.models["claude"].model_id = "some-model"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(ConfigError, match="price_per_mtok"):
        build_client("claude", config)
    config.models["claude"].price_per_mtok = Price(input=1.0, output=5.0)
    assert build_client("claude", config).model_id == "some-model"
