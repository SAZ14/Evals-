"""config.yaml schema and loading, plus a minimal .env reader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_CONFIG = Path(__file__).parent / "config.yaml"


class ConfigError(ValueError):
    """config.yaml or the environment is not usable as given."""


class Price(BaseModel):
    input: float = 0.0  # USD per million input tokens
    output: float = 0.0  # USD per million output tokens


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai_compat", "anthropic", "mock", "mock_judge"]
    model_id: str = ""
    base_url: str | None = None
    api_key_env: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)  # passed straight to the provider API
    pass_seed: bool = False  # send the seed index to providers that accept a `seed` parameter
    behavior: Literal["honest", "liar"] = "honest"  # mock only
    price_per_mtok: Price = Field(default_factory=Price)


class RunSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_limit: int = 15
    concurrency: int = 4
    today: str = "2026-09-15"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: RunSettings = Field(default_factory=RunSettings)
    judge: str
    models: dict[str, ModelConfig]

    @model_validator(mode="after")
    def _judge_known(self) -> Config:
        if self.judge not in self.models:
            raise ValueError(f"judge {self.judge!r} is not a key under models")
        return self


def load_config(path: Path = DEFAULT_CONFIG) -> Config:
    return Config.model_validate(yaml.safe_load(Path(path).read_text()))


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines into os.environ without overriding variables that are already set."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip("'\""))
