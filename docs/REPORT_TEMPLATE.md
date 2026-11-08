# CTF Agent Benchmarking — Part 1 Report (≈3 pages)

Team: … | Commit (frozen): `…` | Selected config: `…`

## 1. Architecture and configuration
- Agent loop, tools (see README table), model + gateway, budgets (values from the config).
- Solver/evaluator separation and network scope. Cite `runs/readiness/doctor-*.json` evidence.
- Problems encountered during setup and how they were fixed.

## 2. Development findings (trace review)
For each issue: challenge, run_id, trace steps (`#seq`), what happened, which question it answers
(tool selection / inputs / use of results / answer-seeking / recovery & stopping).

## 3. Hypothesis and controlled comparison
- **Observed problem:** … (cite trace steps)
- **Hypothesis:** If we change X, then Y will improve because Z.
- **Change:** single diff between `baseline.json` and `candidate.json` (+ commit).
- **Pilot:** N dev attempts, label `pilot`; readiness checks; projected tokens = avg tokens/attempt × 10 × 3 × configs.
- **Controls:** same 10 eval challenges, same model/temperature/budgets, fresh target + sandbox per trial, trials = 3.

## 4. Results (paste from `results/scored/results.md`)
- Table: pass@1/2/3, mean solve rate ± std, wall time (per attempt & total), model/tool calls, tokens, est. cost.
- Per-challenge successes x/3 for each config.
- Δ in percentage points vs baseline, with Δ time / tokens / cost.
- Figures: `pass_at_k.png`, `solve_rate.png`.
- Explain behaviour change with specific trace steps from both configs.

## 5. Failure analysis (≥1 annotated case)
| # | Tool call | Result | Interpretation |
|---|---|---|---|
| … | | | |
Suggested testable improvement: …

## 6. Controls, incidents and resource records
- Egress probe results, blocked actions (from results.md), any incidents and how handled.
- Total tokens/cost vs. daily gateway limits; failed attempts included.
- Prior exposure / suspected recall disclosure for each eval challenge.

## 7. Configuration decision for the competition
Chosen config, launch command, tradeoff (solve rate vs. time/tokens/cost), remaining weaknesses.

## 8. Contributions and AI use
| Member | Contribution |
|---|---|
AI assistance and reused code citations.
