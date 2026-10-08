"""Disk cache for model calls, keyed on sha256(provider, model, messages, tools, params)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ledger.models.base import Message, ModelClient, ModelResponse


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @staticmethod
    def key(payload: dict[str, Any]) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        return json.loads(path.read_text()) if path.is_file() else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.replace(path)


class CachedClient(ModelClient):
    """Wraps a client: identical requests return the stored response (marked cached=True)."""

    def __init__(self, inner: ModelClient, cache: DiskCache) -> None:
        super().__init__(inner.name, inner.model_id, inner.params, inner.pass_seed)
        self.inner, self.cache = inner, cache
        self.provider = inner.provider

    def request_key(self, messages: list[Message], tools: list[dict[str, Any]], tool_choice: str | None,
                    seed: int) -> str:
        return self.cache.key({
            "provider": self.inner.provider, "model_id": self.inner.model_id, "params": self.inner.params,
            "messages": [m.model_dump(exclude={"step", "stop_reason"}) for m in messages],
            "tools": tools, "tool_choice": tool_choice, "seed": seed,
        })

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]], *,
                   tool_choice: str | None = None, seed: int = 0) -> ModelResponse:
        key = self.request_key(messages, tools, tool_choice, seed)
        hit = self.cache.get(key)
        if hit is not None:
            return ModelResponse.model_validate({**hit, "cached": True, "latency_s": 0.0})
        resp = await self.inner.chat(messages, tools, tool_choice=tool_choice, seed=seed)
        # Don't cache a judge reply that ignored the forced tool: a rerun should retry it, not replay it.
        if tool_choice is None or any(c.name == tool_choice for c in resp.tool_calls):
            self.cache.put(key, resp.model_dump())
        return resp
