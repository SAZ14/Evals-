"""CLI: run tasks x models x seeds, grade each run, and write trajectories plus a manifest. Resumable.

    uv run python -m ledger.runner.run --models grok,claude --tasks all --seeds 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from ledger.config import DEFAULT_CONFIG, Config, ConfigError, load_config, load_dotenv
from ledger.graders import grade
from ledger.models import build_client
from ledger.runner.agent_loop import run_episode
from ledger.runner.cache import CachedClient, DiskCache
from ledger.tasks.loader import Task, load_tasks

console = Console()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_file(run_dir: Path, task_id: str, model: str, seed: int) -> Path:
    return run_dir / "trajectories" / f"{task_id}__{model}__s{seed}.json"


def git_info() -> dict[str, Any]:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": "unknown", "git_dirty": None}
    return {"git_commit": commit.strip(), "git_dirty": bool(status.strip())}


def write_manifest(run_dir: Path, run_name: str, config: Config, clients: dict[str, CachedClient],
                   judge: CachedClient, tasks: list[Task], seeds: int) -> None:
    """Create manifest.json, or append a `resumes` entry if the run already exists."""
    path = run_dir / "manifest.json"
    invocation = {"timestamp": _now(), **git_info(), "models": sorted(clients), "tasks": [t.id for t in tasks],
                  "seeds": seeds}
    if path.exists():
        manifest = json.loads(path.read_text())
        manifest.setdefault("resumes", []).append(invocation)
    else:
        manifest = {
            "run_name": run_name, "created_at": invocation["timestamp"], **git_info(),
            "models": {n: {"provider": c.provider, "model_id": c.model_id} for n, c in clients.items()},
            "judge": {"name": judge.name, "provider": judge.provider, "model_id": judge.model_id},
            "tasks": invocation["tasks"], "seeds": seeds, "config": config.model_dump(), "resumes": [],
        }
    path.write_text(json.dumps(manifest, indent=2) + "\n")


async def run_all(config: Config, model_names: list[str], tasks: list[Task], seeds: int, run_name: str,
                  judge_name: str, runs_dir: Path = Path("runs"), cache_dir: Path = Path(".cache")) -> tuple[Path, int]:
    """Run every missing (task, model, seed). Returns the run dir and the number of grading errors."""
    run_dir = runs_dir / run_name
    (run_dir / "trajectories").mkdir(parents=True, exist_ok=True)
    cache = DiskCache(cache_dir)
    agents = {m: CachedClient(build_client(m, config), cache) for m in model_names}
    judge = CachedClient(build_client(judge_name, config), cache)
    write_manifest(run_dir, run_name, config, agents, judge, tasks, seeds)

    jobs = [(t, m, s) for t in tasks for m in model_names for s in range(seeds)]
    todo = [(t, m, s) for t, m, s in jobs if not run_file(run_dir, t.id, m, s).exists()]
    console.print(f"[bold]{run_name}[/]: {len(jobs)} runs, {len(jobs) - len(todo)} already done, {len(todo)} to go")
    semaphore = asyncio.Semaphore(config.run.concurrency)
    errors = 0
    today = config.run.today

    async def one(task: Task, model: str, seed: int) -> None:
        nonlocal errors
        async with semaphore:
            wallet = task.wallet(today)
            traj = await run_episode(task, agents[model], wallet, today=today, step_limit=config.run.step_limit,
                                     seed=seed, run_name=run_name)
            try:
                traj.grade = await grade(task, traj, task.wallet(today), wallet, judge)
            except Exception:  # noqa: BLE001 - logged loudly; the run is retried on resume
                errors += 1
                tb = traceback.format_exc()
                with (run_dir / "errors.log").open("a") as fh:
                    fh.write(f"--- {_now()} grading failed: {task.id} {model} s{seed}\n{tb}\n")
                console.print(f"[red]GRADING ERROR[/] {task.id} {model} s{seed}: {tb.strip().splitlines()[-1]}")
                return
        path = run_file(run_dir, task.id, model, seed)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(traj.model_dump_json(indent=1))
        tmp.replace(path)
        g = traj.grade
        verdict = "[green]PASS[/]" if g.passed else "[red]FAIL[/]"
        console.print(f"{verdict} {task.id:44s} {model:10s} s{seed}  honest={'yes' if g.honest else '[red]no[/]'}"
                      f"  false_claims={g.claims_false}/{g.claims_total}  stop={traj.stop_reason}")

    await asyncio.gather(*(one(*job) for job in todo))
    return run_dir, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ledger.runner.run", description=__doc__.splitlines()[0])
    parser.add_argument("--models", required=True, help="comma-separated keys under `models` in config.yaml")
    parser.add_argument("--tasks", default="all", help="'all', or comma-separated task ids and/or axis names")
    parser.add_argument("--seeds", type=int, default=1, help="number of samples per task and model")
    parser.add_argument("--run-name", help="default: <date>_<models>; reuse a name to resume")
    parser.add_argument("--judge", help="judge model key (default: `judge` in config.yaml)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    args = parser.parse_args(argv)

    load_dotenv()
    config = load_config(args.config)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    tasks = load_tasks(args.tasks)
    run_name = args.run_name or f"{datetime.now().date().isoformat()}_{'-'.join(models)}"
    try:
        run_dir, errors = asyncio.run(run_all(config, models, tasks, args.seeds, run_name, args.judge or config.judge,
                                              args.runs_dir, args.cache_dir))
    except ConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        return 2
    if errors:
        console.print(f"[red]{errors} run(s) failed grading; see {run_dir / 'errors.log'}. Rerun to retry them.[/]")
    console.print(f"\nReport: uv run python -m ledger.report {run_dir}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
