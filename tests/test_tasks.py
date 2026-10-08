from collections import Counter
from pathlib import Path

import pytest

from ledger.env.wallet import Wallet
from ledger.tasks.loader import TaskError, load_task, load_tasks

TASKS = load_tasks()


def test_all_24_tasks_validate() -> None:
    assert len(TASKS) == 24
    assert all(t.reviewed is False for t in TASKS)


def test_axis_and_track_mix() -> None:
    assert Counter(t.axis for t in TASKS) == {
        "spending": 6, "pressure": 5, "sycophancy": 8, "injection": 3, "fraud": 2}
    roman_urdu = [t for t in TASKS if t.track == "roman_urdu"]
    assert len(roman_urdu) == 8
    assert {t.axis for t in roman_urdu} == {"spending", "pressure", "sycophancy", "injection", "fraud"}
    assert sum(1 for t in TASKS if t.spending and t.spending.ambiguous) == 2


def test_sycophancy_pairs() -> None:
    pairs = Counter(t.pair_id for t in TASKS if t.axis == "sycophancy")
    assert len(pairs) == 4 and set(pairs.values()) == {2}


@pytest.mark.parametrize("task", [t for t in TASKS if t.spending], ids=lambda t: t.id)
def test_spending_sql_runs(task) -> None:
    value = task.wallet("2026-09-15").scalar(task.spending.answer_sql)
    assert isinstance(value, int) and value > 0


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t.id)
def test_fixture_is_realistic_and_referenced_rows_exist(task) -> None:
    wallet: Wallet = task.wallet("2026-09-15")
    n = wallet.scalar("SELECT COUNT(*) FROM transactions")
    assert 30 <= n <= 80
    statuses = {r["status"] for r in wallet.query("SELECT DISTINCT status FROM transactions")}
    assert {"posted", "pending", "reversed"} <= statuses
    for table, rows in task.expected_state.items():
        for row_id in rows:
            assert wallet.row(table, row_id) is not None, f"{table}.{row_id} missing"
    for spec in task.forbidden_actions + task.expected_actions:
        txn = spec.args.get("transaction_id")
        if txn:
            assert wallet.row("transactions", txn) is not None


def test_selector_by_axis_and_id() -> None:
    assert len(load_tasks("injection")) == 3
    assert [t.id for t in load_tasks("fraud_fake_support_018")] == ["fraud_fake_support_018"]
    with pytest.raises(TaskError):
        load_tasks("nope")


def _write(tmp: Path, name: str, body: str) -> Path:
    (tmp / "fixtures").mkdir(exist_ok=True)
    (tmp / "fixtures" / "f.json").write_text("{}")
    path = tmp / f"{name}.yaml"
    path.write_text(body)
    return path


BASE = "axis: pressure\ntrack: english\nfixture: fixtures/f.json\nuser_turns: ['hi']\n"


@pytest.mark.parametrize("extra", [
    "spending: {answer_sql: 'SELECT 1'}\n",                 # spending block on a non-spending axis
    "forbidden_actions: [{tool: launch_rockets}]\n",       # unknown tool
    "expected_state: {wallets: {x: {status: a}}}\n",       # unknown table
    "expected_state: {cards: {card_1: {colour: red}}}\n",  # unknown column
    "surprise_field: 1\n",                                 # extra key
    "pair_id: p1\n",                                       # pair_id outside sycophancy
])
def test_bad_tasks_fail_loudly(tmp_path: Path, extra: str) -> None:
    path = _write(tmp_path, "bad_task", "id: bad_task\n" + BASE + extra)
    with pytest.raises(TaskError, match="bad_task.yaml"):
        load_task(path)


def test_id_must_match_filename(tmp_path: Path) -> None:
    path = _write(tmp_path, "other_name", "id: bad_task\n" + BASE)
    with pytest.raises(TaskError, match="must match"):
        load_task(path)
