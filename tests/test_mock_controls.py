"""Positive and negative controls: the honest mock must ace the suite, the liar must be caught everywhere."""

from collections import defaultdict
from pathlib import Path

import pytest

from ledger import report
from ledger.runner import run
from ledger.schemas import Trajectory
from ledger.tasks.loader import AXES, load_tasks


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Trajectory]]:
    tmp = tmp_path_factory.mktemp("controls")
    code = run.main(["--models", "mock,mock_liar", "--tasks", "all", "--judge", "mock_judge", "--run-name", "controls",
                     "--runs-dir", str(tmp / "runs"), "--cache-dir", str(tmp / "cache")])
    assert code == 0
    by_model: dict[str, list[Trajectory]] = defaultdict(list)
    for t in report.load_trajectories(tmp / "runs" / "controls"):
        by_model[t.model].append(t)
    return by_model


def test_opening_turns_are_unique_so_mocks_can_recognise_tasks() -> None:
    openings = [t.user_turns[0] for t in load_tasks()]
    assert len(set(openings)) == len(openings)


def test_honest_mock_passes_every_task_and_is_always_honest(runs: dict[str, list[Trajectory]]) -> None:
    honest = runs["mock"]
    assert len(honest) == 24
    failures = {t.task_id: [c.name for r in t.grade.results for c in r.checks if not c.passed]
                for t in honest if not t.grade.passed}
    assert failures == {}
    assert all(t.grade.honest for t in honest)
    spending_claims = [c for t in honest if t.axis == "spending" for c in t.grade.claims]
    assert any(c.type == "amount" and c.verdict == "true" for c in spending_claims)


def test_liar_fails_every_spending_task(runs: dict[str, list[Trajectory]]) -> None:
    spending = [t for t in runs["mock_liar"] if t.axis == "spending"]
    assert len(spending) == 6 and not any(t.grade.passed for t in spending)


@pytest.mark.parametrize("axis", [a for a in AXES if a != "spending"])
def test_liar_is_never_honest_outside_spending(runs: dict[str, list[Trajectory]], axis: str) -> None:
    mine = [t for t in runs["mock_liar"] if t.axis == axis]
    assert mine and not any(t.grade.honest for t in mine)
    assert all(not t.grade.passed for t in mine)
    # The lies are claims of actions that never happened: the tools were never called.
    for t in mine:
        lied_about = {c.value for c in t.grade.false_claims}
        assert not any(r.ok and r.tool in lied_about for r in t.action_log)
