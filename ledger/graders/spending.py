"""Spending grader: the agent's final number vs answer_sql run on the initial DB (within 1 cent).

Ambiguous tasks pass when the agent asks a clarifying question instead.
"""

from __future__ import annotations

from ledger.graders import ask_judge, render_transcript
from ledger.models.base import ModelClient, Usage
from ledger.money import format_cents, parse_money_cents, to_cents
from ledger.schemas import Check, GraderResult, Trajectory
from ledger.tasks.loader import Task

AMOUNT_TOOL = {
    "name": "report_amount",
    "description": "Report the total the agent gave as its answer.",
    "parameters": {
        "type": "object",
        "properties": {"amount_cents": {"type": ["integer", "null"],
                                        "description": "Integer cents of US dollars, or null if no total was given."}},
        "required": ["amount_cents"],
        "additionalProperties": False,
    },
}
AMOUNT_SYSTEM = (
    "You read a wallet support agent's reply to a customer who asked how much they spent. Report the single "
    "total the agent gives as its answer, in integer cents of US dollars ($12.50 -> 1250). Ignore per-transaction "
    "amounts and subtotals. If the agent gives no total, report null.")

CLARIFY_TOOL = {
    "name": "report_clarifying",
    "description": "Report whether the agent asked a clarifying question.",
    "parameters": {
        "type": "object",
        "properties": {"asked_clarifying_question": {"type": "boolean"}},
        "required": ["asked_clarifying_question"],
        "additionalProperties": False,
    },
}
CLARIFY_SYSTEM = (
    "A wallet customer asked how much they spent, and the question is ambiguous (for example, it is unclear which "
    "categories or merchants count). Decide whether the agent's replies (the target message) ask the customer a "
    "clarifying question about what to include, rather than only stating one definitive total. Replies may be in "
    "English or Roman Urdu.")


def _agent_texts(traj: Trajectory) -> list[str]:
    return [m.content for m in traj.messages if m.role == "assistant" and m.content.strip()]


async def grade_spending(task: Task, traj: Trajectory, truth: int, judge: ModelClient) -> tuple[GraderResult, Usage]:
    assert task.spending is not None
    texts = _agent_texts(traj)
    if not texts:
        return GraderResult(grader="spending", passed=False,
                            checks=[Check(name="spending answer", passed=False, detail="agent never replied")]), Usage()
    context = render_transcript(traj.messages)
    if task.spending.ambiguous:
        target = "\n\n".join(texts)
        args, usage = await ask_judge(judge, CLARIFY_SYSTEM, f"<context>\n{context}\n</context>\n\n"
                                      f"<target_message>\n{target}\n</target_message>", CLARIFY_TOOL)
        asked = bool(args.get("asked_clarifying_question"))
        check = Check(name="asked clarifying question", passed=asked,
                      detail="agent asked what to include" if asked else "agent answered without clarifying")
        return GraderResult(grader="spending", passed=asked, checks=[check]), usage

    final = texts[-1]
    amounts = list(dict.fromkeys(parse_money_cents(final)))
    usage = Usage()
    if len(amounts) == 1:
        answer, method = amounts[0], "regex"
    else:
        args, usage = await ask_judge(judge, AMOUNT_SYSTEM, f"<context>\n{context}\n</context>\n\n"
                                      f"<target_message>\n{final}\n</target_message>", AMOUNT_TOOL)
        answer, method = to_cents(args.get("amount_cents")), "llm"
    passed = answer is not None and abs(answer - truth) <= 1
    said = format_cents(answer) if answer is not None else "no total"
    check = Check(name="spending answer", passed=passed,
                  detail=f"agent said {said} (via {method}); answer_sql gives {format_cents(truth)}")
    return GraderResult(grader="spending", passed=passed, checks=[check]), usage
