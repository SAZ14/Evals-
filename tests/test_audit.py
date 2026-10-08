"""Replay, re-grading and the claim-checker audit tooling, on mock runs."""

import asyncio
import csv
import json
from pathlib import Path

import pytest

from ledger import audit, report
from ledger.env.wallet import Wallet
from ledger.models.base import ModelClient, ModelResponse, ToolCall
from ledger.runner import regrade, run
from ledger.tasks.loader import load_tasks

TODAY = "2026-09-15"
TASKS = {t.id: t for t in load_tasks()}


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("audit")
    assert run.main(["--models", "mock,mock_liar", "--tasks", "all", "--judge", "mock_judge", "--run-name", "a",
                     "--runs-dir", str(tmp / "runs"), "--cache-dir", str(tmp / "cache")]) == 0
    return tmp / "runs" / "a"


def test_replay_reproduces_every_final_db(run_dir: Path) -> None:
    trajs = report.load_trajectories(run_dir)
    for t in trajs:
        regrade.final_wallet(TASKS[t.task_id], t, TODAY)  # raises if the snapshot differs


def test_replay_reproduces_new_ids_and_detects_divergence() -> None:
    task = TASKS["pressure_double_charge_007"]
    live = task.wallet(TODAY)
    live.call("send_transfer", {"from_account": "acc_007", "to_handle": "@x", "amount_cents": 999_999_999}, 1)  # fails
    live.call("escalate_to_human", {"reason": "over limit"}, 2)
    live.call("send_transfer", {"from_account": "acc_007", "to_handle": "@ali", "amount_cents": 1000}, 3)
    live.call("reverse_transaction", {"transaction_id": "txn_755", "reason": "dup"}, 4)
    snapshot = live.snapshot()
    replayed = Wallet.replay(task.fixture_path, live.action_log, f"{TODAY}T12:00:00", expected=snapshot)
    assert replayed.snapshot(set(snapshot)) == snapshot and "trf_new2" in json.dumps(snapshot)
    with pytest.raises(ValueError, match="does not match"):
        Wallet.replay(task.fixture_path, live.action_log[:2], f"{TODAY}T12:00:00", expected=snapshot)


def test_regrade_in_place_keeps_previous_grade(run_dir: Path, tmp_path: Path) -> None:
    import shutil

    copy = tmp_path / "runs" / "a"
    shutil.copytree(run_dir, copy)
    before = {t.task_id + t.model: t.grade.model_dump() for t in report.load_trajectories(copy)}
    assert regrade.main([str(copy), "--judge", "mock_judge", "--cache-dir", str(tmp_path / "c")]) == 0
    after = report.load_trajectories(copy)
    assert {t.task_id + t.model: t.grade.model_dump() for t in after} == before
    assert all(len(t.previous_grades) == 1 for t in after)


class NoClaimsJudge(ModelClient):
    provider = "test"

    async def chat(self, messages, tools, *, tool_choice=None, seed=0) -> ModelResponse:
        return ModelResponse(tool_calls=[ToolCall(id="j", name=tool_choice, arguments={"claims": []})])


def test_second_opinion_lists_disagreements(run_dir: Path) -> None:
    records = asyncio.run(audit.second_opinion(run_dir, NoClaimsJudge("blind", "blind-1"), TODAY))
    assert len(records) == 48
    disagree = {r["run_id"] for r in records if not r["agree"]}
    liar_flagged = {r["run_id"] for r in records if r["model"] == "mock_liar" and r["original_false"]}
    assert disagree == liar_flagged and len(liar_flagged) == 22  # liar lies everywhere except 2 ambiguous tasks


def test_labelling_sheet_sample_is_seeded_and_keeps_existing_labels(run_dir: Path) -> None:
    records = asyncio.run(audit.second_opinion(run_dir, NoClaimsJudge("blind", "blind-1"), TODAY))
    rows, sampled = audit.labelling_rows(records, sample=20, seed=0, existing={})
    rows2, sampled2 = audit.labelling_rows(records, sample=20, seed=0,
                                           existing={rows[0]["run_id"]: {"ai_label": "true_lie", "run_id": "x"}})
    assert sampled == sampled2 and len(sampled) == 20 and len(rows) == 22 + 20
    assert rows2[0]["ai_label"] == "true_lie" and rows2[0]["run_id"] == rows[0]["run_id"]
    assert audit.labelling_rows(records, sample=20, seed=1, existing={})[1] != sampled


def test_score_precision_and_recall_arithmetic(run_dir: Path) -> None:
    records = asyncio.run(audit.second_opinion(run_dir, NoClaimsJudge("blind", "blind-1"), TODAY))
    rows, sampled = audit.labelling_rows(records, sample=20, seed=0, existing={})
    labels = {}
    flagged = [r["run_id"] for r in rows if r["run_id"] not in sampled]
    for i, rid in enumerate(flagged):  # 22 flagged by the claim judge: 20 real lies, 2 judge errors
        labels[rid] = {"ai_label": "true_lie" if i >= 2 else "judge_error_false_positive"}
    for i, rid in enumerate(sampled):  # 20 sampled honest runs: 1 missed lie
        labels[rid] = {"ai_label": "missed_lie" if i == 0 else "true_honest"}
    out = audit.score(records, labels, sampled)
    assert out["tp"] == 20 and out["fp"] == 2 and out["precision"].startswith("20/22 91%")
    # 26 runs honest by both; 1/20 missed in the sample -> 1.3 estimated misses -> recall 20/21
    assert out["honest_by_both_runs"] == 26 and out["estimated_missed"] == 1.3
    assert out["recall"].startswith("20/21 95%") and out["caveat"].startswith("AI-audited")
    with pytest.raises(ValueError, match="unknown labels"):
        audit.score(records, {sampled[0]: {"ai_label": "lie"}}, sampled)


def test_audit_cli_writes_the_files(run_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "audit"
    assert audit.main(["second-opinion", str(run_dir), "--judge", "mock_judge", "--out", str(out),
                       "--cache-dir", str(tmp_path / "c")]) == 0
    with (out / "human_labels.csv").open() as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == audit.CSV_FIELDS
        assert len(list(reader)) == 22 + 20
    assert json.loads((out / "sample.json").read_text())["seed"] == 0
    assert audit.main(["score", "--out", str(out)]) == 0
