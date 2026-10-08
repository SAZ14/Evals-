"""Re-grade saved runs in place (e.g. after fixing a grader bug), keeping each old grade in previous_grades.

    uv run python -m ledger.runner.regrade runs/<run_name> [--judge <model>] [--tasks all]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from rich.console import Console

from ledger.config import DEFAULT_CONFIG, Config, ConfigError, load_config, load_dotenv
from ledger.env.wallet import Wallet
from ledger.graders import grade
from ledger.models.base import ModelClient
from ledger.runner.budget import BudgetExceeded, BudgetGuard
from ledger.runner.cache import DiskCache
from ledger.runner.run import make_client
from ledger.schemas import Trajectory
from ledger.tasks.loader import Task, load_tasks

console = Console()


def run_today(run_dir: Path, config: Config) -> str:
    """The `today` the run used (from its manifest), falling back to the current config."""
    manifest = run_dir / "manifest.json"
    if manifest.is_file():
        return json.loads(manifest.read_text()).get("config", {}).get("run", {}).get("today", config.run.today)
    return config.run.today


def final_wallet(task: Task, traj: Trajectory, today: str) -> Wallet:
    return Wallet.replay(task.fixture_path, traj.action_log, f"{today}T12:00:00", expected=traj.final_db)


async def regrade_dir(run_dir: Path, judge: ModelClient, today: str, selector: str = "all") -> int:
    tasks = {t.id: t for t in load_tasks(selector)}
    count = 0
    for path in sorted((run_dir / "trajectories").glob("*.json")):
        traj = Trajectory.model_validate_json(path.read_text())
        task = tasks.get(traj.task_id)
        if task is None:
            continue
        new = await grade(task, traj, task.wallet(today), final_wallet(task, traj, today), judge)
        if traj.grade is not None:
            traj.previous_grades.append(traj.grade)
        traj.grade = new
        tmp = path.with_suffix(".tmp")
        tmp.write_text(traj.model_dump_json(indent=1))
        tmp.replace(path)
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ledger.runner.regrade", description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--judge", help="judge model key (default: `judge` in config.yaml)")
    parser.add_argument("--tasks", default="all")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    args = parser.parse_args(argv)
    load_dotenv()
    config = load_config(args.config)
    guard = BudgetGuard(args.run_dir.parent / "spend.json", config.run.budget_usd)
    try:
        judge = make_client(args.judge or config.judge, config, DiskCache(args.cache_dir), guard)
        n = asyncio.run(regrade_dir(args.run_dir, judge, run_today(args.run_dir, config), args.tasks))
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        return 2
    except BudgetExceeded as exc:
        console.print(f"[red]BUDGET[/] {exc}")
        return 3
    console.print(f"Re-graded {n} run(s) in {args.run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
