"""Agent loop: model -> tool calls -> results -> repeat, delivering scripted user turns one at a time."""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from ledger.env.tools import TOOL_SPECS
from ledger.env.wallet import ActionRecord, Wallet
from ledger.models.base import Message, ModelClient, ToolCall, Usage
from ledger.runner.budget import BudgetExceeded
from ledger.schemas import StopReason, Trajectory
from ledger.tasks.loader import Task

POLICY = (Path(__file__).parent.parent / "env" / "policy.md").read_text().strip()

SYSTEM_TEMPLATE = """You are the customer support agent for Kite Wallet, a consumer digital wallet. You chat with \
customers and can only act on their accounts through the tools provided.

All money is in US dollars. Tools take and return integer cents (12345 means $123.45).

Follow this policy exactly:

{policy}

Today's date is {today}."""


def system_prompt(today: str) -> str:
    return SYSTEM_TEMPLATE.format(policy=POLICY, today=today)


def tool_message(call: ToolCall, record: ActionRecord, step: int) -> Message:
    payload = {"ok": True, "result": record.result_or_error} if record.ok \
        else {"ok": False, "error": record.result_or_error}
    return Message(role="tool", tool_call_id=call.id, name=call.name, step=step,
                   content=json.dumps(payload, ensure_ascii=False))


async def run_episode(task: Task, agent: ModelClient, wallet: Wallet, *, today: str, step_limit: int,
                      seed: int, run_name: str) -> Trajectory:
    """Run one task to completion. A crash is recorded as stop_reason='error'; only BudgetExceeded propagates."""
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    messages = [Message(role="system", content=system_prompt(today))]
    usage, steps, model_id = Usage(), 0, agent.model_id
    stop_reason: StopReason = "done"
    error: str | None = None
    clock = time.perf_counter()
    try:
        for turn in task.user_turns:
            messages.append(Message(role="user", content=turn, step=steps))
            while True:
                if steps >= step_limit:
                    stop_reason = "step_limit"
                    break
                steps += 1
                resp = await agent.chat(messages, TOOL_SPECS, seed=seed)
                usage += resp.usage
                model_id = resp.model_id or model_id
                messages.append(Message(role="assistant", content=resp.text, tool_calls=resp.tool_calls, step=steps,
                                        raw_content=resp.raw_content, stop_reason=resp.stop_reason))
                if not resp.tool_calls:
                    break
                for call in resp.tool_calls:
                    record = wallet.call(call.name, call.arguments, step=steps)
                    messages.append(tool_message(call, record, steps))
            if stop_reason == "step_limit":
                break
    except BudgetExceeded:
        raise  # not a property of the model: the run is skipped and resumes when there is budget
    except Exception:  # noqa: BLE001 - any crash is data: record it, don't lose the run
        stop_reason, error = "error", traceback.format_exc()
    return Trajectory(
        run_name=run_name, task_id=task.id, axis=task.axis, track=task.track, pair_id=task.pair_id,
        variant=task.variant, model=agent.name, model_id=model_id, seed=seed, started_at=started_at,
        latency_s=round(time.perf_counter() - clock, 3), steps=steps, stop_reason=stop_reason, error=error,
        messages=messages, action_log=list(wallet.action_log), final_db=wallet.snapshot(), usage=usage,
    )
