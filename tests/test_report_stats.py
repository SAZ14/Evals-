"""Wilson intervals and the phase-5 report additions (headline vs flagged, consistency across seeds)."""

from pathlib import Path

import pytest

from ledger import report
from ledger.runner import run
from ledger.stats import fmt_rate, wilson


@pytest.mark.parametrize("k, n, lo, hi", [(0, 10, 0.0, 0.2775), (5, 10, 0.2366, 0.7634), (10, 10, 0.7225, 1.0),
                                          (1, 3, 0.0615, 0.7923)])
def test_wilson_matches_reference_values(k: int, n: int, lo: float, hi: float) -> None:
    got = wilson(k, n)
    assert got[0] == pytest.approx(lo, abs=1e-3) and got[1] == pytest.approx(hi, abs=1e-3)


def test_fmt_rate() -> None:
    assert fmt_rate(5, 10) == "5/10 50% [24–76]"
    assert fmt_rate(0, 0) == "-"


@pytest.fixture(scope="module")
def three_seeds(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list]:
    tmp = tmp_path_factory.mktemp("seeds")
    assert run.main(["--models", "mock,mock_liar", "--tasks", "all", "--seeds", "3", "--judge", "mock_judge",
                     "--run-name", "s3", "--runs-dir", str(tmp / "runs"), "--cache-dir", str(tmp / "cache")]) == 0
    trajs = report.load_trajectories(tmp / "runs" / "s3")
    assert len(trajs) == 2 * 24 * 3
    return tmp / "runs" / "s3", trajs


def test_headline_excludes_flagged_tasks_and_full_tables_include_them(three_seeds) -> None:
    _, trajs = three_seeds
    flagged = report.flagged_task_ids()
    assert flagged == {"spending_ambiguous_food_005", "spending_ambiguous_bills_006"}
    tables = {t.title: t for t in report.build_tables(trajs, {}, flagged)}
    headline = tables["Pass rate by model x axis (headline: excludes 2 flagged tasks)"]
    full = tables["Pass rate by model x axis (all tasks, including flagged)"]
    honest_head = dict((r[0], r) for r in headline.rows)["mock"]
    honest_full = dict((r[0], r) for r in full.rows)["mock"]
    spending = 1 + headline.headers[1:].index("spending")
    assert honest_head[spending].startswith("12/12 100% [") and honest_full[spending].startswith("18/18 100% [")
    flagged_rows = tables["Flagged tasks (excluded from headline numbers)"].rows
    assert {r[0] for r in flagged_rows} == flagged


def test_consistency_across_seeds(three_seeds) -> None:
    _, trajs = three_seeds
    table = report.consistency_table(trajs)
    assert {r[0]: (r[1], r[2]) for r in table.rows} == {"mock": ("3", "24/24 100% [86–100]"),
                                                         "mock_liar": ("3", "24/24 100% [86–100]")}


def test_report_cli_writes_flagged_column(three_seeds, tmp_path: Path) -> None:
    run_dir, _ = three_seeds
    assert report.main([str(run_dir), "--md", str(tmp_path / "s.md"), "--csv", str(tmp_path / "r.csv")]) == 0
    md = (tmp_path / "s.md").read_text()
    assert "Run-to-run consistency" in md and "[Wilson 95% CI]" in md
    assert "spending_ambiguous_food_005" in (tmp_path / "r.csv").read_text().split("\n", 1)[1]
    assert (tmp_path / "r.csv").read_text().splitlines()[0].count("flagged") == 1
