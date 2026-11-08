"""Solver execution environments.

DockerSandbox  - real runs. Non-root container on an internal network, no docker socket,
                 capped CPU/memory/pids, contestant files copied into /work only.
LocalSandbox   - offline self-test ONLY (scripted model). Runs in a temp dir on the host;
                 refused for live models by the CLI.
"""
from __future__ import annotations

import posixpath
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .common import SafeError

WORKDIR = "/work"


def _run(cmd, timeout, input_bytes=None):
    try:
        p = subprocess.run(cmd, input=input_bytes, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired as exc:
        return -9, exc.stdout or b"", exc.stderr or b"", True


def docker(*args, timeout=600, check=True):
    code, out, err, timed_out = _run(["docker", *args], timeout)
    if check and (code != 0 or timed_out):
        raise SafeError(f"docker_{args[0]}_failed: {err.decode(errors='replace')[-400:]}")
    return out.decode(errors="replace")


def validate_path(path: str) -> str:
    if not isinstance(path, str) or not path or "\x00" in path:
        raise SafeError("invalid_path")
    full = posixpath.normpath(path if path.startswith("/") else posixpath.join(WORKDIR, path))
    if full != WORKDIR and not full.startswith(WORKDIR + "/") and not full.startswith("/tmp/"):
        raise SafeError("path_outside_workdir")
    return full


class DockerSandbox:
    kind = "docker"

    def __init__(self, cfg: dict, network: str | None, name: str):
        self.cfg = cfg
        self.network = network
        self.name = name
        self.cid = None

    def start(self, files: list[Path]):
        args = ["run", "-d", "--name", self.name, "--hostname", "solver",
                "--network", self.network or "none",
                "--cpus", str(self.cfg["cpus"]), "--memory", self.cfg["memory"],
                "--pids-limit", str(self.cfg["pids_limit"]),
                "--cap-drop", "ALL", "--cap-add", "SYS_PTRACE",
                "--security-opt", "no-new-privileges",
                "--user", "ctf", "--workdir", WORKDIR,
                self.cfg["image"], "sleep", "infinity"]
        self.cid = docker(*args, timeout=120).strip()
        for f in files:
            docker("cp", str(f), f"{self.cid}:{WORKDIR}/{f.name}", timeout=120)
        # docker cp creates root-owned files; make them readable to the ctf user.
        docker("exec", "-u", "root", self.cid, "chown", "-R", "ctf:ctf", WORKDIR, timeout=60)

    def exec(self, command: str, timeout: int, max_bytes: int):
        t0 = time.monotonic()
        code, out, err, to = _run(["docker", "exec", "-i", "-u", "ctf", "-w", WORKDIR, self.cid,
                                   "timeout", "-s", "KILL", str(timeout), "bash", "-lc", command], timeout + 10)
        if code == 137 or code == -9:
            to = True
        return {"exit_code": code, "stdout": out[:max_bytes].decode(errors="replace"),
                "stderr": err[:max_bytes].decode(errors="replace"), "timed_out": to,
                "duration_s": round(time.monotonic() - t0, 2)}

    def write_file(self, path: str, content: str):
        full = validate_path(path)
        code, _, err, _ = _run(["docker", "exec", "-i", "-u", "ctf", self.cid, "sh", "-c",
                                'mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", full], 30, content.encode())
        if code != 0:
            raise SafeError("write_failed: " + err.decode(errors="replace")[-200:])
        return full

    def stop(self):
        if self.cid:
            docker("rm", "-f", self.cid, timeout=60, check=False)


class LocalSandbox:
    """Self-test only. Not isolated. Never used with a real model."""
    kind = "local-selftest"

    def __init__(self, *_, **__):
        self.dir = Path(tempfile.mkdtemp(prefix="ctf-selftest-"))

    def start(self, files):
        for f in files:
            shutil.copy(f, self.dir / f.name)

    def _map(self, command):
        return command.replace(WORKDIR, str(self.dir))

    def exec(self, command, timeout, max_bytes):
        t0 = time.monotonic()
        code, out, err, to = _run(["bash", "-c", f"cd {self.dir} && {self._map(command)}"], timeout)
        return {"exit_code": code, "stdout": out[:max_bytes].decode(errors="replace"),
                "stderr": err[:max_bytes].decode(errors="replace"), "timed_out": to,
                "duration_s": round(time.monotonic() - t0, 2)}

    def write_file(self, path, content):
        full = validate_path(path)
        local = Path(self._map(full))
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(self._map(content))
        return full

    def stop(self):
        shutil.rmtree(self.dir, ignore_errors=True)
