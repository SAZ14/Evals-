"""Pydantic models for grades and trajectories."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ledger.env.wallet import ActionRecord
from ledger.models.base import Message, Usage

ClaimType = Literal["action_done", "balance", "amount", "transaction_status", "transfer_status"]
StopReason = Literal["done", "step_limit", "error"]


class Check(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class GraderResult(BaseModel):
    grader: str
    passed: bool
    checks: list[Check] = Field(default_factory=list)


class Claim(BaseModel):
    type: ClaimType
    entity_id: str | None = None
    value: str | int | float | None = None
    quote: str


class VerifiedClaim(Claim):
    step: int
    verdict: Literal["true", "false", "unverifiable"]
    reason: str


class Grade(BaseModel):
    passed: bool  # every applicable grader passed
    honest: bool  # zero false claims
    results: list[GraderResult]
    claims: list[VerifiedClaim] = Field(default_factory=list)
    claims_total: int = 0
    claims_false: int = 0
    forbidden_hit: bool = False
    judge_model: str = ""  # config key of the judge
    judge_usage: Usage = Field(default_factory=Usage)

    @property
    def false_claims(self) -> list[VerifiedClaim]:
        return [c for c in self.claims if c.verdict == "false"]


class Trajectory(BaseModel):
    run_name: str
    task_id: str
    axis: str
    track: str
    pair_id: str | None = None
    variant: str | None = None
    model: str  # config key, e.g. "grok"
    model_id: str
    seed: int
    started_at: str
    latency_s: float = 0.0
    steps: int = 0
    stop_reason: StopReason
    error: str | None = None  # traceback when stop_reason == "error"
    messages: list[Message]
    action_log: list[ActionRecord]
    final_db: dict[str, list[dict[str, Any]]]  # tables touched by successful tool calls
    usage: Usage = Field(default_factory=Usage)
    grade: Grade | None = None
