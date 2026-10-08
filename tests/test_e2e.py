import asyncio
import csv
import json
from pathlib import Path

from ledger import report
from ledger.config import load_config
from ledger.models.base import ModelClient, ModelResponse
from ledger.runner import run
from ledger.runner.cache import CachedClient, DiskCache
from ledger.schemas import Trajectory
from ledger.tasks.loader import load_tasks

CONFIG = load_config()
THREE = "spending_food_aug_001,pressure_double_charge_007,injection_memo_transfer_016"


def run_suite(tmp: Path, name: str, tasks: str = THREE, models: str = "mock,mock_liar") -> Path:
    code = run.main(["--models", models, "--tasks", tasks, "--judge", "mock_judge", "--run-name", name,
                     "--runs-dir", str(tmp / "runs"), "--cache-dir", str(tmp / f"cache_{name}")])
    assert code == 0
    return tmp / "runs" / name


def grades(run_dir: Path) -> dict[str, dict]:
    return {t.task_id + "/" + t.model + f"/s{t.seed}": t.grade.model_dump() for t in report.load_trajectories(run_dir)}


def test_end_to_end_three_tasks_with_report(tmp_path: Path) -> None:
    run_dir = run_suite(tmp_path, "e2e")
    files = sorted(p.name for p in (run_dir / "trajectories").glob("*.json"))
    assert len(files) == 6 and "pressure_double_charge_007__mock_liar__s0.json" in files

    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["git_commit"] and manifest["judge"]["name"] == "mock_judge"
    assert set(manifest["models"]) == {"mock", "mock_liar"} and manifest["config"]["run"]["today"] == "2026-09-15"

    liar = Trajectory.model_validate_json(
        (run_dir / "trajectories" / "pressure_double_charge_007__mock_liar__s0.json").read_text())
    assert liar.stop_reason == "done" and not liar.grade.honest and liar.grade.claims_false >= 1
    assert liar.grade.false_claims[0].type == "action_done"
    honest = Trajectory.model_validate_json(
        (run_dir / "trajectories" / "pressure_double_charge_007__mock__s0.json").read_text())
    assert honest.grade.honest and honest.grade.passed
    assert [r.tool for r in honest.action_log] == ["lookup_customer", "get_balance", "escalate_to_human"]
    assert honest.messages[0].role == "system" and honest.usage is not None

    out = tmp_path / "results"
    assert report.main([str(run_dir), "--out", str(out)]) == 0
    summary = (out / "summary.md").read_text()
    for heading in ("Pass rate by model x axis", "Pass rate by model x track", "Honesty rate by model x axis",
                    "Sycophancy flip rate", "Injection success rate", "most recent false claims",
                    "Token usage and estimated cost"):
        assert heading in summary
    assert "I have reversed the charge" in summary  # the liar's false claim is quoted
    rows = list(csv.DictReader((out / "runs.csv").open()))
    assert len(rows) == 6
    liar_row = next(r for r in rows if r["model"] == "mock_liar" and r["task_id"] == "pressure_double_charge_007")
    assert liar_row["honest"] == "False" and "honesty:" in liar_row["failed_checks"]


def test_resume_skips_finished_runs(tmp_path: Path) -> None:
    run_dir = run_suite(tmp_path, "resume", tasks="spending_food_aug_001")
    path = run_dir / "trajectories" / "spending_food_aug_001__mock__s0.json"
    before = path.stat().st_mtime_ns
    run_suite(tmp_path, "resume", tasks="spending_food_aug_001,fraud_fake_support_018")
    assert path.stat().st_mtime_ns == before
    assert len(list((run_dir / "trajectories").glob("*.json"))) == 4
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert len(manifest["resumes"]) == 1


def test_mock_suite_is_deterministic(tmp_path: Path) -> None:
    first = grades(run_suite(tmp_path, "det1", tasks="all"))
    second = grades(run_suite(tmp_path, "det2", tasks="all"))
    assert len(first) == 2 * len(load_tasks()) == 48
    assert first == second


def test_placeholder_model_is_a_config_error(tmp_path: Path) -> None:
    code = run.main(["--models", "grok", "--tasks", "spending_food_aug_001", "--judge", "mock_judge",
                     "--runs-dir", str(tmp_path / "runs"), "--cache-dir", str(tmp_path / "cache")])
    assert code == 2


class Counting(ModelClient):
    provider = "test"

    def __init__(self) -> None:
        super().__init__("counting", "counting-1", {"temperature": 0})
        self.calls = 0

    async def chat(self, messages, tools, *, tool_choice=None, seed=0) -> ModelResponse:
        self.calls += 1
        return ModelResponse(text=f"reply {self.calls}", model_id=self.model_id)


def test_cache_returns_identical_requests(tmp_path: Path) -> None:
    from ledger.models.base import Message

    inner = Counting()
    client = CachedClient(inner, DiskCache(tmp_path))
    msgs = [Message(role="user", content="hi", step=0)]
    first = asyncio.run(client.chat(msgs, []))
    again = asyncio.run(client.chat([Message(role="user", content="hi", step=7)], []))  # step is metadata
    other_seed = asyncio.run(client.chat(msgs, [], seed=1))
    assert inner.calls == 2
    assert (first.text, first.cached) == ("reply 1", False)
    assert (again.text, again.cached) == ("reply 1", True)
    assert other_seed.text == "reply 2"


def test_cache_skips_judge_replies_without_the_forced_tool(tmp_path: Path) -> None:
    from ledger.models.base import Message

    inner = Counting()
    client = CachedClient(inner, DiskCache(tmp_path))
    msgs = [Message(role="user", content="extract")]
    asyncio.run(client.chat(msgs, [], tool_choice="report_claims"))
    asyncio.run(client.chat(msgs, [], tool_choice="report_claims"))
    assert inner.calls == 2
