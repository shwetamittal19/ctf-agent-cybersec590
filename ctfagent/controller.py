"""TRUSTED controller: starts/resets targets, builds the solver sandbox, owns the evaluator,
runs one trial, and writes its records. Runs on the host; the solver never gets a handle to it.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import challenge as chal
from .agent import run_agent
from .budget import Budget
from .common import ROOT, SafeError, Trace, git_info, now, sha256, write_json
from .evaluator import Evaluator
from .models import make_model
from .sandbox import DockerSandbox, LocalSandbox, docker


class Targets:
    """Bring the challenge server up on a fresh internal network, and tear it down."""

    def __init__(self, spec: chal.ChallengeSpec, cfg: dict, run_id: str, trace: Trace):
        self.spec, self.cfg, self.run_id, self.trace = spec, cfg, run_id, trace
        self.network = cfg["network_name"]
        self.project = f"ctf-{run_id}"
        self.container = None

    def up(self):
        docker("network", "rm", self.network, check=False, timeout=60)
        # --internal: containers on it can reach each other but have NO route out.
        docker("network", "create", "--internal", self.network, timeout=60)
        self.trace.event("network_created", network=self.network, internal=True)
        if not self.spec.public.endpoints:
            return
        d = self.spec.directory
        if self.spec.compose:
            docker("compose", "-p", self.project, "-f", str(d / "docker-compose.yml"), "up", "-d", "--build",
                   timeout=self.cfg["target_start_timeout_s"])
        elif self.spec.has_dockerfile:
            tag = f"ctf-target-{sha256(str(d).encode())[:10]}"
            docker("build", "-t", tag, str(d), timeout=self.cfg["target_start_timeout_s"])
            self.container = docker("run", "-d", "--name", self.project, "--network", self.network,
                                    "--network-alias", self.spec.box, tag, timeout=120).strip()
        else:
            raise SafeError("target_has_no_compose_or_dockerfile")
        self.trace.event("target_started", mode="compose" if self.spec.compose else "dockerfile",
                         endpoints=self.spec.public.endpoints)

    def wait_ready(self, sandbox):
        if not self.spec.public.endpoints:
            return True
        host, port = self.spec.box, self.spec.port
        deadline = time.monotonic() + self.cfg["target_ready_timeout_s"]
        while time.monotonic() < deadline:
            r = sandbox.exec(f"timeout 3 bash -c '</dev/tcp/{host}/{port}' && echo UP", 10, 1000)
            if "UP" in r["stdout"]:
                self.trace.event("target_ready", host=host, port=port)
                return True
            time.sleep(2)
        self.trace.event("target_not_ready", host=host, port=port)
        return False

    def down(self):
        d = self.spec.directory
        if self.spec.compose and self.spec.public.endpoints:
            docker("compose", "-p", self.project, "-f", str(d / "docker-compose.yml"), "down", "-v",
                   "--remove-orphans", timeout=180, check=False)
        if self.container:
            docker("rm", "-f", self.container, timeout=60, check=False)
        docker("network", "rm", self.network, check=False, timeout=60)


def probe_egress(sandbox, trace):
    """Record evidence that the solver cannot reach the internet (expected: blocked)."""
    r = sandbox.exec("curl -sS -m 5 -o /dev/null -w '%{http_code}' https://example.com 2>&1; echo EXIT=$?", 15, 2000)
    blocked = "EXIT=0" not in r["stdout"]
    trace.event("egress_probe", target="https://example.com", blocked=blocked, output=r["stdout"][-300:])
    return blocked


def run_trial(challenge_dir, config: dict, out_root: Path, trial: int, label: str,
              sandbox_kind="docker", model_override=None) -> dict:
    spec = chal.load(challenge_dir)
    run_id = uuid.uuid4().hex[:10]
    out = Path(out_root) / label / config["name"] / spec.public.challenge_id.replace("/", "__") / f"trial-{trial}-{run_id}"
    out.mkdir(parents=True, exist_ok=True)

    evaluator = Evaluator(spec.answer_key, config["budget"]["max_submissions"])
    redactor = evaluator.redact if config.get("redact_flag_in_traces", True) else None
    trace = Trace(out / "trace.jsonl", redactor)
    prov = git_info()
    cfg_blob = json.dumps(config, sort_keys=True)
    trace.event("run_start", run_id=run_id, challenge_id=spec.public.challenge_id, trial=trial, label=label,
                config_name=config["name"], config_sha256=sha256(cfg_blob.encode())[:16],
                model=config["model"]["name"], sandbox=sandbox_kind, **prov)
    write_json(out / "config.json", config)

    budget = Budget(config["budget"])
    sandbox = None
    targets = None
    result = {"status": "error", "stop_reason": "not_started"}
    started = time.monotonic()
    try:
        model = model_override or make_model(config["model"])
        if sandbox_kind == "docker":
            targets = Targets(spec, config["sandbox"], run_id, trace)
            targets.up()
            sandbox = DockerSandbox(config["sandbox"], targets.network, f"ctf-solver-{run_id}")
        else:
            sandbox = LocalSandbox()
        sandbox.start(spec.file_paths)
        trace.event("sandbox_started", kind=sandbox.kind, files=spec.public.files)
        if sandbox_kind == "docker":
            probe_egress(sandbox, trace)
            if targets and not targets.wait_ready(sandbox):
                raise SafeError("target_not_ready")
        trace.event("verification_mode", mode="live" if evaluator.verified_mode else "unverified_record_only")
        result = run_agent(spec.public, model, sandbox, evaluator.submit, budget, trace, config,
                           verified=evaluator.verified_mode)
    except SafeError as exc:
        result = {"status": "error", "stop_reason": str(exc)}
        trace.event("error", reason=str(exc))
    except KeyboardInterrupt:
        result = {"status": "error", "stop_reason": "interrupted"}
        trace.event("error", reason="interrupted")
        raise
    finally:
        if sandbox:
            sandbox.stop()
        if targets:
            targets.down()
        solved = evaluator.solved
        summary = {
            "run_id": run_id, "challenge_id": spec.public.challenge_id, "category": spec.public.category,
            "trial": trial, "label": label, "config": config["name"], "model": config["model"]["name"],
            "sandbox": sandbox_kind, "commit": prov["commit"], "dirty": prov["dirty"],
            "verification": "live" if evaluator.verified_mode else "unverified",
            "candidate_flag": next((s["candidate"] for s in evaluator.submissions
                                    if s["verdict"] == "recorded_unverified"), None),
            "solved": solved, "status": "solved" if solved else result.get("status", "error"),
            "stop_reason": result.get("stop_reason"),
            "wall_s": round(time.monotonic() - started, 1), "agent_wall_s": round(budget.elapsed(), 1),
            **budget.usage(),
            "est_cost_usd": round(budget.cost(config["model"]), 5),
            "submissions": evaluator.submissions,
            "incorrect_submissions": sum(s["verdict"] == "incorrect" for s in evaluator.submissions),
            "blocked_actions": result.get("blocked_actions", []),
            "finished_at": now(),
        }
        write_json(out / "summary.json", summary, extra_redact=())
        trace.event("run_end", **{k: summary[k] for k in ("status", "solved", "stop_reason", "wall_s",
                                                         "model_calls", "tool_calls", "total_tokens", "est_cost_usd")})
    return summary
