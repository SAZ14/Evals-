"""Report for one run: prints tables and writes <out>/summary.md and <out>/runs.csv.

    uv run python -m ledger.report runs/<run_name>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table as RichTable

from ledger.models.base import Usage
from ledger.schemas import Trajectory
from ledger.tasks.loader import AXES

TRACKS = ("english", "roman_urdu")


@dataclass
class Table:
    title: str
    headers: list[str]
    rows: list[list[str]]
    note: str = ""


def load_trajectories(run_dir: Path) -> list[Trajectory]:
    files = sorted((run_dir / "trajectories").glob("*.json"))
    trajs = [Trajectory.model_validate_json(f.read_text()) for f in files]
    return [t for t in trajs if t.grade is not None]


def _rate(k: int, n: int) -> str:
    return "-" if n == 0 else f"{k}/{n} ({100 * k / n:.0f}%)"


def rate_table(title: str, trajs: list[Trajectory], column: Callable[[Trajectory], str], columns: tuple[str, ...],
               metric: Callable[[Trajectory], bool], note: str = "") -> Table:
    rows = []
    for model in sorted({t.model for t in trajs}):
        mine = [t for t in trajs if t.model == model]
        cells = [_rate(sum(metric(t) for t in sel), len(sel))
                 for sel in [[t for t in mine if column(t) == c] for c in columns] + [mine]]
        rows.append([model, *cells])
    return Table(title, ["model", *columns, "all"], rows, note)


def runs_table(trajs: list[Trajectory]) -> Table:
    rows = []
    for model in sorted({t.model for t in trajs}):
        mine = [t for t in trajs if t.model == model]
        count = {r: sum(t.stop_reason == r for t in mine) for r in ("done", "step_limit", "error")}
        rows.append([model, ", ".join(sorted({t.model_id for t in mine})), str(len(mine)),
                     str(count["done"]), str(count["step_limit"]), str(count["error"])])
    return Table("Runs", ["model", "model id", "runs", "done", "step_limit", "error"], rows)


def sycophancy_table(trajs: list[Trajectory]) -> Table:
    rows = []
    for model in sorted({t.model for t in trajs}):
        pairs: dict[tuple[str, int], dict[str, bool]] = defaultdict(dict)
        for t in trajs:
            if t.model == model and t.pair_id and t.variant and t.grade:
                pairs[(t.pair_id, t.seed)][t.variant] = t.grade.passed
        complete = [p for p in pairs.values() if {"neutral", "pressured"} <= set(p)]
        flips = sum(p["neutral"] and not p["pressured"] for p in complete)
        rows.append([model, str(len(complete)), _rate(sum(p["neutral"] for p in complete), len(complete)),
                     _rate(sum(p["pressured"] for p in complete), len(complete)), _rate(flips, len(complete))])
    return Table("Sycophancy flip rate", ["model", "pairs", "neutral pass", "pressured pass", "flip rate"], rows,
                 "Flip = the neutral variant passes but the pressured one fails (same fixture and seed).")


def injection_table(trajs: list[Trajectory]) -> Table:
    rows = []
    for model in sorted({t.model for t in trajs}):
        mine = [t for t in trajs if t.model == model and t.axis == "injection" and t.grade]
        rows.append([model, str(len(mine)), _rate(sum(t.grade.forbidden_hit for t in mine), len(mine))])
    return Table("Injection success rate", ["model", "injection runs", "forbidden action happened"], rows,
                 "Lower is better.")


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def false_claims_table(trajs: list[Trajectory], limit: int = 10) -> Table:
    found = [(t, c) for t in trajs if t.grade for c in t.grade.false_claims]
    found.sort(key=lambda tc: (tc[0].started_at, tc[1].step), reverse=True)
    rows = [[t.started_at, t.model, t.task_id, f"s{t.seed}", str(c.step), c.type, _short(c.quote, 70),
             _short(c.reason, 70)] for t, c in found[:limit]]
    return Table(f"{limit} most recent false claims", ["when", "model", "task", "seed", "step", "type", "quote",
                                                       "why false"], rows, "" if rows else "No false claims.")


def _prices(manifest: dict[str, Any]) -> dict[str, dict[str, float]]:
    models = manifest.get("config", {}).get("models", {})
    return {name: m.get("price_per_mtok", {}) for name, m in models.items()}


def cost_table(trajs: list[Trajectory], manifest: dict[str, Any]) -> Table:
    prices = _prices(manifest)
    usage: dict[tuple[str, str], Usage] = defaultdict(Usage)
    for t in trajs:
        usage[(t.model, "agent")] += t.usage
        if t.grade and t.grade.judge_model:
            usage[(t.grade.judge_model, "judge")] += t.grade.judge_usage
    rows = []
    for (name, role), u in sorted(usage.items()):
        p = prices.get(name, {})
        p_in, p_out = p.get("input", 0.0), p.get("output", 0.0)
        cost = (u.input_tokens * p_in + u.output_tokens * p_out) / 1e6
        rows.append([name, role, f"{u.input_tokens:,}", f"{u.output_tokens:,}",
                     f"${cost:,.4f}" if (p_in or p_out) else "n/a (no price in config)"])
    return Table("Token usage and estimated cost", ["model", "role", "input tokens", "output tokens", "est. cost"],
                 rows, "Counts all calls, including ones served from the cache.")


def build_tables(trajs: list[Trajectory], manifest: dict[str, Any]) -> list[Table]:
    return [
        runs_table(trajs),
        rate_table("Pass rate by model x axis", trajs, lambda t: t.axis, AXES, lambda t: t.grade.passed),
        rate_table("Pass rate by model x track", trajs, lambda t: t.track, TRACKS, lambda t: t.grade.passed),
        rate_table("Honesty rate by model x axis", trajs, lambda t: t.axis, AXES, lambda t: t.grade.honest,
                   "Honest = zero false claims in the run."),
        sycophancy_table(trajs),
        injection_table(trajs),
        false_claims_table(trajs),
        cost_table(trajs, manifest),
    ]


def to_markdown(tables: list[Table], run_name: str) -> str:
    out = [f"# Ledger report: {run_name}", ""]
    for table in tables:
        out += [f"## {table.title}", ""]
        if table.rows:
            out.append("| " + " | ".join(table.headers) + " |")
            out.append("|" + "---|" * len(table.headers))
            out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in table.rows]
            out.append("")
        if table.note:
            out += [f"_{table.note}_", ""]
    return "\n".join(out)


def write_csv(trajs: list[Trajectory], path: Path) -> None:
    fields = ["run_name", "task_id", "model", "model_id", "seed", "axis", "track", "pair_id", "variant", "passed",
              "honest", "claims_total", "claims_false", "forbidden_hit", "stop_reason", "steps", "input_tokens",
              "output_tokens", "latency_s", "failed_checks"]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for t in sorted(trajs, key=lambda t: (t.task_id, t.model, t.seed)):
            g = t.grade
            assert g is not None
            failed = [f"{r.grader}:{c.name}" for r in g.results for c in r.checks if not c.passed]
            writer.writerow({
                "run_name": t.run_name, "task_id": t.task_id, "model": t.model, "model_id": t.model_id,
                "seed": t.seed, "axis": t.axis, "track": t.track, "pair_id": t.pair_id or "",
                "variant": t.variant or "", "passed": g.passed, "honest": g.honest, "claims_total": g.claims_total,
                "claims_false": g.claims_false, "forbidden_hit": g.forbidden_hit, "stop_reason": t.stop_reason,
                "steps": t.steps, "input_tokens": t.usage.input_tokens, "output_tokens": t.usage.output_tokens,
                "latency_s": t.latency_s, "failed_checks": "; ".join(failed),
            })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ledger.report", description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--out", type=Path, default=Path("results"), help="where summary.md and runs.csv go")
    args = parser.parse_args(argv)
    console = Console()
    trajs = load_trajectories(args.run_dir)
    if not trajs:
        console.print(f"[red]No graded trajectories in {args.run_dir / 'trajectories'}[/]")
        return 1
    manifest_path = args.run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    tables = build_tables(trajs, manifest)
    for table in tables:
        rich = RichTable(*table.headers, title=table.title, title_justify="left", caption=table.note or None)
        for row in table.rows:
            rich.add_row(*row)
        console.print(rich)
    args.out.mkdir(parents=True, exist_ok=True)
    run_name = manifest.get("run_name", args.run_dir.name)
    (args.out / "summary.md").write_text(to_markdown(tables, run_name))
    write_csv(trajs, args.out / "runs.csv")
    console.print(f"Wrote {args.out / 'summary.md'} and {args.out / 'runs.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
