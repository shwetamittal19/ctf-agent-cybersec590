# CTF Agent — CYBERSEC 590 Final Project, Part 1

An autonomous tool-using agent that solves CTF challenges inside an isolated Docker sandbox, with a
trusted controller/evaluator kept separate from the solver, hard resource budgets, full JSONL traces,
and an aggregation script for pass@k, solve rate, time, tokens, and cost.

## Team

| Member | Responsibility |
| --- | --- |
| Atharva Ashish Jog | _TODO_ |
| Varun Bhanushali | _TODO_ |
| Shweta Mittal | _TODO_ |
| Deekshitha Manjunatha Reddy | _TODO_ |

Reused code: `ctfagent/common.py` (redaction, safe HTTP, `.env` loading) and the gateway client in
`ctfagent/models.py` are adapted from our Assignment 3 triage agent. AI assistance: _TODO, cite per course policy_.

## Architecture

```
 HOST (trusted)                                         DOCKER network "ctfnet" (--internal, no egress)
 ┌──────────────────────────────────────────┐          ┌───────────────────────────┐
 │ controller.py                            │ compose  │ target container(s)       │
 │  - loads challenge.json (flag stays here)│─────────▶│ alias = challenge "box"   │
 │  - starts/resets targets per trial       │          └─────────────▲─────────────┘
 │  - Evaluator: submit(flag) -> verdict    │                        │ allowed endpoint only
 │  - Budget: wall clock / calls / tokens   │ docker   ┌─────────────┴─────────────┐
 │ agent.py loop                            │ exec ───▶│ solver container          │
 │  model -> tool call -> result -> model   │          │ non-root, cap-drop ALL,   │
 │ models.py -> Duke LiteLLM gateway (HTTPS)│          │ cpu/mem/pids caps,        │
 └──────────────────────────────────────────┘          │ /work = contestant files  │
                                                       │ no docker.sock, no flag   │
                                                       └───────────────────────────┘
```

* **Agent loop** (`ctfagent/agent.py`): every turn the model sees all prior tool results and picks
  the next tool call. The loop stops when the evaluator confirms the flag, the agent gives up, the
  submission limit is reached, repeated out-of-scope attempts occur, or a budget is exhausted.
* **Solver/evaluator separation**: the loop gets only the public brief (name, category, description,
  file names, allowed endpoints), a sandbox handle, and a `submit` callable. `challenge.json`, server
  sources, compose files, the Docker socket, and the gateway token never enter the solver container.
  The model API is called from the host, so the token never enters the container either.
* **Network scope**: `ctfnet` is created `--internal`, so the solver can reach the challenge target
  and nothing else (no internet, no external DNS). `scope.py` also refuses commands that name
  out-of-scope hosts, search engines or write-up sites, `challenge.json`, or Docker. These are
  recorded as `blocked_action` events, and 3 blocked actions stop the run as an incident.
* **Budgets** (`budget.py`, set per config): wall clock, model calls, tool calls, total tokens, and
  flag submissions per attempt, plus a token cap per batch. They are checked before every model call
  and every tool call, and each command timeout is clipped to the time remaining.

### Tools

| Tool | Inputs | Output | Validation | Timeout | Errors |
| --- | --- | --- | --- | --- | --- |
| `run_command` | `command` (str ≤ 20k), `timeout_s` (1–180, default 60) | `exit_code`, `stdout`, `stderr`, `timed_out`, `duration_s` (+ optional `warning`); capped at 200 KB, then middle-truncated to 6,000 chars for the model | scope policy (hosts, Docker, answer key) | `timeout -s KILL` in the container + host subprocess timeout, clipped to the run deadline | `invalid_command`, `blocked_by_policy`, timed-out result |
| `write_file` | `path` (under `/work` or `/tmp`), `content` (≤ 200 KB) | `written`, `bytes` | path normalization (no `..` escape), Docker/answer-key policy | 30 s | `path_outside_workdir`, `write_failed` |
| `submit_flag` | `flag` (≤ 500 chars) | `verdict` correct/incorrect | constant-time compare in the evaluator; max 5 per attempt | n/a | `submission_limit_reached` |
| `give_up` | `reason` | ack | — | — | — |

Bad JSON arguments, unknown tools, and more than 4 calls in one turn come back to the model as
structured errors, so it can recover from them.

### Records (one folder per attempt)

`runs/<label>/<config>/<challenge>/trial-<n>-<run_id>/`
* `trace.jsonl`: `run_start` (commit, dirty flag, code hash, config hash, model), `network_created`,
  `target_started`, `egress_probe`, `prompt`, every `model_call` (usage, latency, tool calls),
  every `tool_result`, `blocked_action`, `budget_exhausted`, `incident`, and `run_end`. The flag is
  replaced by `[FLAG-REDACTED]`, and credentials are redacted.
* `summary.json`: status, stop reason, wall time, calls, tokens, estimated cost, and submissions.
  Wrong candidates are logged as submitted; a correct one is logged only as a hash prefix.
* `config.json`: the exact configuration used.

Labels keep data apart: `dev` (development), `pilot` (pilot), `scored` (scored eval).

## Setup

Requires Python 3.10+ (standard library only; `matplotlib` is optional for plots) and Docker Desktop.

```bash
python3 -m ctfagent configure
```
```bash
python3 -m ctfagent build-image
```
```bash
python3 -m unittest discover -s tests -v
```
```bash
python3 -m ctfagent selftest
```
```bash
python3 -m ctfagent doctor --config configs/baseline.json
```

* `configure` saves your Duke gateway token to `.env` (git-ignored, mode 600) with hidden input.
* `selftest` runs offline with a scripted fixture, which is not an LLM. It exercises every tool, a
  blocked lookup, a wrong flag, and a verified flag.
* `doctor` runs the readiness checks: Docker, the image, blocked egress and external DNS, no Docker
  socket, non-root solver, no `challenge.json` in the solver, and a reachable model. Evidence is saved
  to `runs/readiness/`.

### Challenges

Put CTFTiny under `challenges/CTFTiny/` (git-ignored). Each challenge directory needs a
`challenge.json` in NYU CTF Bench format (`name`, `category`, `description`, `flag`, `files`, `box`,
`internal_port`, `compose`), plus `docker-compose.yml` or `Dockerfile` for server challenges.
Compose files must join the external network `ctfnet` with the `box` alias, as NYU CTF Bench
does. List directories in `challenges/dev.txt` (development) and `challenges/eval.txt` (10
scored challenges, disjoint from dev).

## Running experiments

```bash
python3 -m ctfagent run --challenge challenges/CTFTiny/<path> --config configs/baseline.json --label dev
```
```bash
python3 scripts/show_trace.py runs/dev/baseline/<challenge>/trial-1-<id>/trace.jsonl
```
```bash
python3 -m ctfagent batch --challenges challenges/dev.txt --config configs/candidate.json --trials 1 --label pilot
```
```bash
python3 -m ctfagent batch --challenges challenges/eval.txt --config configs/baseline.json --trials 3 --label scored
```
```bash
python3 -m ctfagent batch --challenges challenges/eval.txt --config configs/candidate.json --trials 3 --label scored
```
```bash
python3 scripts/aggregate.py runs/scored --baseline baseline
```

Batches run in trial-major order, so even a partial batch gives a complete pass@1. They resume by
skipping attempts that already exist, and they stop at `budget.batch_token_cap`. Commit before
scored runs: every summary records the commit and flags uncommitted (`dirty`) code.

`aggregate.py` writes `results/<label>/results.md` (pass@1–3, mean solve rate ± std, time, calls,
tokens, cost, per-challenge x/3, Δ vs baseline in pp, stop reasons, blocked actions), plus
`attempts.csv`, `pass_at_k.png`, and `solve_rate.png`.

## Configurations

| Config | Difference |
| --- | --- |
| `configs/baseline.json` | Base prompt, no repeat warning, full context |
| `configs/candidate.json` | **Placeholder**: planning prompt + repeat-command warning + elision of old tool outputs. Narrow it to the change your dev-trace hypothesis supports. |

**Selected configuration for the competition:** _TODO_. Launch command: _TODO_.
Frozen commit: _TODO_.

## Cost estimates

`usd_per_1m_input/output` in each config are placeholders (GPT-4.1 list prices). Replace them with
gateway or provider prices and label costs as estimates in the report.
