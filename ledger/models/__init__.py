"""Build model clients from config."""

from __future__ import annotations

import os

from ledger.config import Config, ConfigError
from ledger.models.base import ModelClient


def build_client(name: str, config: Config) -> ModelClient:
    if name not in config.models:
        raise ConfigError(f"unknown model {name!r}; config has: {', '.join(config.models)}")
    mc = config.models[name]
    if mc.provider == "mock":
        from ledger.models.mock import MockAgent

        return MockAgent(name, behavior=mc.behavior)
    if mc.provider == "mock_judge":
        from ledger.models.mock import MockJudge

        return MockJudge(name)
    if not mc.model_id or "FILL_ME" in mc.model_id:
        raise ConfigError(f"models.{name}.model_id is a placeholder; set it in ledger/config.yaml")
    if not mc.api_key_env or not os.environ.get(mc.api_key_env):
        raise ConfigError(f"models.{name}: environment variable {mc.api_key_env or '(api_key_env unset)'} "
                          "is not set; add it to .env")
    api_key = os.environ[mc.api_key_env]
    if mc.provider == "openai_compat":
        from ledger.models.openai_compat import OpenAICompatClient

        return OpenAICompatClient(name, mc.model_id, mc.params, base_url=mc.base_url, api_key=api_key,
                                  pass_seed=mc.pass_seed)
    from ledger.models.anthropic_client import AnthropicClient

    return AnthropicClient(name, mc.model_id, mc.params, api_key=api_key, pass_seed=mc.pass_seed)
