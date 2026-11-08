"""CLI: configure | doctor | selftest | run | batch | build-image"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

from . import challenge as chal
from .common import ROOT, SafeError, Trace, load_local_env, now, read_json, write_json
from .controller import probe_egress, run_trial
from .models import make_model
from .sandbox import DockerSandbox, docker


def load_config(path):
    cfg = read_json(path)
    cfg.setdefault("name", Path(path).stem)
    return cfg


def cmd_configure(_):
    token = getpass.getpass("Duke AI Gateway token (input hidden): ").strip()
    env = ROOT / ".env"
    lines = [l for l in (env.read_text().splitlines() if env.exists() else []) if not l.startswith("LITELLM_TOKEN=")]
    lines.append(f"LITELLM_TOKEN={token}")
    env.write_text("\n".join(lines) + "\n")
    env.chmod(0o600)
    print("Saved to .env (git-ignored, mode 600).")


def cmd_build_image(args):
    cfg = load_config(args.config)
    print(docker("build", "-t", cfg["sandbox"]["image"], "-f", str(ROOT / "docker/solver.Dockerfile"),
                 str(ROOT / "docker"), timeout=1800)[-2000:])


def cmd_doctor(args):
    """Readiness checks. Saves evidence to runs/readiness/<timestamp>.json"""
    cfg = load_config(args.config)
    checks = {}

    def record(name, ok, detail=""):
        checks[name] = {"ok": bool(ok), "detail": detail}
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")

    try:
        docker("info", "--format", "{{.ServerVersion}}", timeout=20)
        record("docker_daemon", True)
    except SafeError as e:
        record("docker_daemon", False, str(e)[:200])
    try:
        docker("image", "inspect", cfg["sandbox"]["image"], timeout=20)
        record("solver_image", True, cfg["sandbox"]["image"])
    except SafeError:
        record("solver_image", False, "run: python -m ctfagent build-image")

    if checks["docker_daemon"]["ok"] and checks["solver_image"]["ok"]:
        out = ROOT / "runs/readiness"
        trace = Trace(out / f"doctor-{int(time.time())}.jsonl")
        net = "ctf-doctor-net"
        docker("network", "rm", net, check=False, timeout=30)
        docker("network", "create", "--internal", net, timeout=30)
        sb = DockerSandbox(cfg["sandbox"], net, f"ctf-doctor-{int(time.time())}")
        try:
            sb.start([])
            record("egress_blocked", probe_egress(sb, trace), "curl https://example.com from solver")
            r = sb.exec("getent hosts pypi.org || echo NO_DNS", 10, 2000)
            record("external_dns_blocked", "NO_DNS" in r["stdout"], r["stdout"].strip()[:80])
            r = sb.exec("ls -la /var/run/docker.sock 2>&1; id -u", 10, 2000)
            record("no_docker_socket", "No such file" in r["stdout"], "")
            record("non_root_solver", r["stdout"].strip().splitlines()[-1] != "0", "uid=" + r["stdout"].strip().splitlines()[-1])
            r = sb.exec("find / -name challenge.json 2>/dev/null | head -1; echo END", 30, 2000)
            record("no_answer_key_in_solver", r["stdout"].strip() == "END")
        finally:
            sb.stop()
            docker("network", "rm", net, check=False, timeout=30)

    load_local_env()
    try:
        m = make_model(cfg["model"])
        msg, usage, _ = m.chat([{"role": "user", "content": "Reply with the word ready."}], [
            {"type": "function", "function": {"name": "noop", "description": "unused",
                                              "parameters": {"type": "object", "properties": {}}}}], 60)
        record("model_reachable", True, f"{m.name} usage={usage}")
    except SafeError as e:
        record("model_reachable", False, str(e))
    write_json(ROOT / f"runs/readiness/doctor-{int(time.time())}.json", {"at": now(), "config": cfg["name"], "checks": checks})
    sys.exit(0 if all(c["ok"] for c in checks.values()) else 1)


def cmd_selftest(args):
    """Offline end-to-end check with the scripted fixture model and the local sandbox."""
    cfg = load_config(args.config)
    cfg["model"] = {**cfg["model"], "provider": "scripted", "name": "scripted-fixture"}
    cfg["name"] = "selftest"
    s = run_trial(ROOT / "challenges/sample/toy_b64", cfg, ROOT / "runs", 1, "selftest",
                  sandbox_kind="docker" if args.docker else "local")
    print(json.dumps({k: s[k] for k in ("run_id", "status", "stop_reason", "submissions", "blocked_actions",
                                         "model_calls", "tool_calls")}, indent=2))
    sys.exit(0 if s["solved"] else 1)


def cmd_run(args):
    load_local_env()
    cfg = load_config(args.config)
    s = run_trial(Path(args.challenge), cfg, ROOT / "runs", args.trial, args.label)
    print(json.dumps(s, indent=2))


def cmd_batch(args):
    """Trial-major order (every challenge once, then again) so partial batches still give pass@1."""
    load_local_env()
    cfg = load_config(args.config)
    dirs = chal.read_list(args.challenges)
    cap = cfg["budget"].get("batch_token_cap")
    spent = 0
    for trial in range(1, args.trials + 1):
        for d in dirs:
            cid = chal.challenge_id_for(d).replace("/", "__")
            done = list((ROOT / "runs" / args.label / cfg["name"] / cid).glob(f"trial-{trial}-*/summary.json"))
            if done and not args.rerun:
                print(f"skip {cid} trial {trial} (exists)")
                continue
            if cap and spent >= cap:
                print(f"STOP: batch token cap {cap} reached ({spent}).")
                return
            s = run_trial(d, cfg, ROOT / "runs", trial, args.label)
            spent += s.get("total_tokens", 0)
            print(f"{cid} trial {trial}: {s['status']} ({s['stop_reason']}) {s['wall_s']}s tokens={s.get('total_tokens')} spent={spent}")


def main():
    p = argparse.ArgumentParser(prog="ctfagent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("configure").set_defaults(fn=cmd_configure)
    for name, fn in (("doctor", cmd_doctor), ("selftest", cmd_selftest), ("build-image", cmd_build_image)):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default=str(ROOT / "configs/baseline.json"))
        if name == "selftest":
            sp.add_argument("--docker", action="store_true", help="run the fixture inside the Docker sandbox")
        sp.set_defaults(fn=fn)
    r = sub.add_parser("run")
    r.add_argument("--challenge", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--trial", type=int, default=1)
    r.add_argument("--label", default="dev", help="dev | pilot | scored  (kept in separate folders)")
    r.set_defaults(fn=cmd_run)
    b = sub.add_parser("batch")
    b.add_argument("--challenges", required=True, help="list file, e.g. challenges/eval.txt")
    b.add_argument("--config", required=True)
    b.add_argument("--trials", type=int, default=3)
    b.add_argument("--label", required=True)
    b.add_argument("--rerun", action="store_true")
    b.set_defaults(fn=cmd_batch)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
