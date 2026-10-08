"""Task schema (pydantic) and loading. Bad tasks fail loudly with the file name."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

from ledger.env.tools import TOOL_NAMES
from ledger.env.wallet import COLUMNS, Wallet

TASKS_DIR = Path(__file__).parent
Axis = Literal["spending", "pressure", "sycophancy", "injection", "fraud"]
AXES: tuple[str, ...] = ("spending", "pressure", "sycophancy", "injection", "fraud")


class TaskError(ValueError):
    """A task file is invalid."""


class ActionSpec(BaseModel):
    """Matches action-log entries: same tool, and every listed arg equal (loosely) to the call's arg."""

    model_config = ConfigDict(extra="forbid")
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool")
    @classmethod
    def _known_tool(cls, value: str) -> str:
        if value not in TOOL_NAMES:
            raise ValueError(f"unknown tool {value!r}")
        return value


class SpendingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_sql: str
    ambiguous: bool = False


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    axis: Axis
    track: Literal["english", "roman_urdu"]
    pair_id: str | None = None
    variant: Literal["neutral", "pressured"] | None = None
    reviewed: bool = False
    fixture: str
    user_turns: list[str] = Field(min_length=1)
    expected_state: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)
    expected_actions: list[ActionSpec] = Field(default_factory=list)
    forbidden_actions: list[ActionSpec] = Field(default_factory=list)
    spending: SpendingSpec | None = None
    notes: str = ""

    _dir: Path = PrivateAttr(default=TASKS_DIR)

    @model_validator(mode="after")
    def _consistent(self) -> Task:
        if (self.axis == "spending") != (self.spending is not None):
            raise ValueError("`spending` must be set if and only if axis is 'spending'")
        paired = (self.pair_id is not None, self.variant is not None)
        if paired != ((True, True) if self.axis == "sycophancy" else (False, False)):
            raise ValueError("`pair_id` and `variant` must both be set if and only if axis is 'sycophancy'")
        if any(not turn.strip() for turn in self.user_turns):
            raise ValueError("user_turns must not be empty strings")
        for table, rows in self.expected_state.items():
            if table not in COLUMNS:
                raise ValueError(f"expected_state: unknown table {table!r}")
            for row_id, cols in rows.items():
                bad = set(cols) - set(COLUMNS[table])
                if bad:
                    raise ValueError(f"expected_state: unknown column(s) {sorted(bad)} for {table}.{row_id}")
        return self

    @property
    def fixture_path(self) -> Path:
        return self._dir / self.fixture

    def wallet(self, today: str) -> Wallet:
        """A fresh wallet for this task. `today` is YYYY-MM-DD; new rows are stamped at noon."""
        return Wallet.from_file(self.fixture_path, now=f"{today}T12:00:00")


def load_task(path: Path) -> Task:
    try:
        data = yaml.safe_load(path.read_text())
        task = Task.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise TaskError(f"{path.name}: {exc}") from exc
    if task.id != path.stem:
        raise TaskError(f"{path.name}: id {task.id!r} must match the file name")
    task._dir = path.parent
    if not task.fixture_path.is_file():
        raise TaskError(f"{path.name}: fixture not found: {task.fixture_path}")
    return task


def _check_pairs(tasks: list[Task]) -> None:
    pairs: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        if task.pair_id:
            pairs[task.pair_id].append(task)
    for pair_id, members in pairs.items():
        variants = sorted(t.variant or "" for t in members)
        if variants != ["neutral", "pressured"]:
            raise TaskError(f"pair {pair_id!r} needs exactly one neutral and one pressured task, got {variants}")
        if len({t.fixture for t in members}) != 1:
            raise TaskError(f"pair {pair_id!r}: both variants must use the same fixture")


def load_tasks(selector: str = "all", tasks_dir: Path = TASKS_DIR) -> list[Task]:
    """Load and validate every task, then select: 'all', or a comma list of task ids and/or axis names."""
    tasks = [load_task(p) for p in sorted(tasks_dir.glob("*.yaml"))]
    if not tasks:
        raise TaskError(f"no tasks found in {tasks_dir}")
    ids = [t.id for t in tasks]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise TaskError(f"duplicate task ids: {dupes}")
    _check_pairs(tasks)
    if selector == "all":
        return tasks
    wanted = [s.strip() for s in selector.split(",") if s.strip()]
    unknown = [w for w in wanted if w not in ids and w not in AXES]
    if unknown:
        raise TaskError(f"unknown task ids or axes: {unknown}")
    return [t for t in tasks if t.id in wanted or t.axis in wanted]
