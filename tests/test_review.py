"""Deterministic task review: the real tasks pass, and each check catches a deliberately broken task."""

from pathlib import Path

import pytest

from ledger.tasks.loader import ActionSpec, SpendingSpec, load_tasks
from ledger.tasks.review import main, review_all, review_task, to_markdown

TASKS = load_tasks()
BY_ID = {t.id: t for t in TASKS}


def failed(task, tasks=TASKS) -> set[str]:
    return {f.check for f in review_task(task, tasks) if not f.ok}


def test_every_real_task_passes_and_has_an_auto_review() -> None:
    assert {tid: failed(t) for tid, t in BY_ID.items() if failed(t)} == {}
    assert all(t.auto_review is not None and t.reviewed is False for t in TASKS)
    assert main([]) == 0


def test_flagged_tasks_are_the_two_ambiguous_spending_tasks() -> None:
    flagged = {t.id for t in TASKS if t.auto_review.status == "flagged"}
    assert flagged == {"spending_ambiguous_food_005", "spending_ambiguous_bills_006"}


@pytest.mark.parametrize("task_id, update, check", [
    ("pressure_double_charge_007", {"expected_state": {"transactions": {"txn_9999": {"status": "posted"}}}}, "ids"),
    ("pressure_double_charge_007",
     {"forbidden_actions": [ActionSpec(tool="reverse_transaction", args={"transaction_id": "txn_1"})]}, "ids"),
    ("pressure_double_charge_007", {"forbidden_actions": [ActionSpec(tool="freeze_card")]}, "forbidden"),
    ("pressure_double_charge_007", {"user_turns": ["0333-6667788 here, please refund the duplicate charge."]},
     "language"),
    ("pressure_failed_transfer_008", {"user_turns": ["Mera number 0300-1112223 hai, transfer kar do please."]},
     "language"),
    ("pressure_failed_transfer_008", {"user_turns": ["Hi, my number is (212) 555-0999, send $900 please."]},
     "caller"),
    ("pressure_failed_transfer_008", {"axis": "injection"}, "injection"),
    ("pressure_failed_transfer_008", {"expected_state": {"cards": {"card_008": {"status": "lost"}}}}, "expected"),
    ("pressure_failed_transfer_008", {"fixture": "fixtures/cust_016.json"}, "injection"),  # stray injection text
    ("spending_food_aug_001", {"spending": SpendingSpec(answer_sql="SELECT SUM(amount_cents) FROM transactions")},
     "answer_sql"),
    ("spending_food_aug_001", {"spending": SpendingSpec(answer_sql="SELECT nope FROM nowhere")}, "answer_sql"),
])
def test_each_check_catches_a_broken_task(task_id: str, update: dict, check: str) -> None:
    broken = BY_ID[task_id].model_copy(update=update)
    assert check in failed(broken)


def test_pair_check_catches_variants_that_differ_beyond_user_turns() -> None:
    neutral = BY_ID["sycophancy_double_charge_012_neutral"]
    broken = neutral.model_copy(update={"expected_state": {}})
    others = [t for t in TASKS if t.id != neutral.id] + [broken]
    assert "pair" in failed(broken, others)


def test_review_markdown(tmp_path: Path) -> None:
    md = to_markdown(TASKS, review_all(TASKS))
    assert "22 ok, 0 fixed, 2 flagged" in md and "All deterministic checks pass." in md
    hand = md.split("## What to check by hand")[1]
    assert hand.index("Roman Urdu") < hand.index("Flagged tasks")
    assert all(f"`{t.id}`" in hand for t in TASKS if t.track == "roman_urdu")
