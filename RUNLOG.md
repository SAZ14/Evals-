# Run log

Spend is real API spend estimated from token counts x config prices. All entries so far are $0.00: no API keys
were available in this environment, so only work that needs no provider calls was done.

## 2026-10-08T17:39Z — start of autonomous run (phases 1–7)
- Preflight: no `.env` and no provider keys in the environment. User said to skip the API-key part and do the
  rest. Reachability without keys: api.anthropic.com 401, generativelanguage.googleapis.com 403, api.x.ai /
  api.openai.com / api.deepseek.com no connection (blocked by the environment's network policy).
- Plan: phase 1 in full; keyless parts of phases 2–6 (budget guard, retries, parsing, task review, report
  stats, audit tooling); phase 7 limited to docs (no real-model findings exist).
- Tests at start: 98 passed. Spend: $0.00.

## 2026-10-08T17:39Z — phase 1 start (mock positive controls)
- Honest mock now answers spending from `answer_sql` (asks on ambiguous tasks); liar lies on every
  non-spending reply and gives wrong spending totals. New control tests in `tests/test_mock_controls.py`.
- Result (`results/mock_controls.md`): honest 24/24 pass, 24/24 honest; liar 0/24 pass, honesty 0% on every
  non-spending axis (2/6 on spending = the two ambiguous tasks, where wrong totals are unverifiable).

## 2026-10-08T17:40Z — phase 1 end
- Tests: 105 passed. Spend: $0.00.

## 2026-10-08T17:43Z — phase 2 (keyless parts)
- Skipped (needs keys): model discovery, model selection, pricing.
- Added: budget guard (`runs/spend.json`, $25 cap, exit code 3 at the cap), price required for real models,
  retry tests (429/500/529 retried, 5 tries max, 400 not retried), DeepSeek and Gemini config entries.
- Tests: 115 passed. Spend: $0.00.

## 2026-10-08T17:44Z — phase 3 (keyless parts)
- Skipped (needs keys): smoke runs and trajectory reading.
- Fixed: money parser ignored Rs/PKR/rupees and parsed lakh grouping "1,24,500" as 1. Added 17 regression tests.
- Tests: 132 passed. Spend: $0.00.

## 2026-10-08T17:48Z — phase 4 (deterministic checks + coding-agent critique)
- Added `auto_review` to the schema and every task; the `ledger/tasks/review.py` checks all pass; `REVIEW.md`
  written. 22 ok, 0 fixed, 2 flagged (`spending_ambiguous_food_005`, `spending_ambiguous_bills_006`).
- Skipped (needs keys): the audit judge's critique. A critique by the coding agent is in `auto_review.notes`.
- Tests: 147 passed. Spend: $0.00.

## 2026-10-08T17:50Z — phase 5 (keyless parts)
- Skipped (needs keys): the full real run.
- Report: Wilson 95% CIs with n, headline tables excluding flagged tasks plus full tables, a flagged-task table,
  and seed consistency. Verified on a 3-seed mock run (144 runs; consistency 24/24 for both mocks).
  `results/mock_controls.md` regenerated in the new format.
- Tests: 155 passed. Spend: $0.00.

## 2026-10-08T17:52Z — phase 6 (keyless parts)
- Skipped (needs keys): the audit itself (second-opinion judge on real runs, reading and labelling, AUDIT.md,
  filled human_labels.csv).
- Built and tested: `Wallet.replay` (checked against every mock run's `final_db`), a `regrade` CLI, and the
  `ledger.audit` second-opinion / labelling-sheet / score CLI.
- Tests: 162 passed. Spend: $0.00.

## 2026-10-08T17:53Z — phase 7 (docs only) and end of run
- README: budget guard, report statistics, review/regrade/audit tools, `auto_review` in the task format, and a
  Status section (no real-model results yet, steps to finish phases 2–7). No FINDINGS.md: no data.
- Phases complete: 1. Partial without keys: 2, 3, 4 (audit-judge critique pending), 5 and 6 (tooling only),
  7 (docs only).
- Tests: 162 passed. Total spend: $0.00.
- Blockers for the next run: provider keys must be set as environment variables, and api.x.ai,
  api.openai.com and api.deepseek.com must be allowed by the environment's network policy (no connection from
  this container).
