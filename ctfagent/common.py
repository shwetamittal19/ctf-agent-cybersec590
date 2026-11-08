"""Shared helpers: env loading, redaction, JSON/JSONL persistence, HTTP, provenance.

Adapted from the team's Assignment 3 triage agent (triage/common.py).
Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
SECRET_ENV = ("LITELLM_TOKEN", "DUKE_AI_GATEWAY_API_KEY", "OPENAI_API_KEY")
KNOWN_ENV = SECRET_ENV + ("CTF_MODEL",)


class SafeError(Exception):
    """Application-defined error; message never contains remote bodies or secrets."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_local_env():
    """Fill known variables from an ignored .env without evaluating it."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in KNOWN_ENV:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


_KEEP_KEYS = {"max_output_tokens", "max_tokens", "prompt_tokens", "completion_tokens",
              "total_tokens", "max_total_tokens", "tokens", "token_cap", "batch_token_cap"}


def redact(value, extra=()):
    """Recursively remove credentials (and any extra strings, e.g. the flag)."""
    if isinstance(value, dict):
        return {k: "[REDACTED]" if re.search(r"authorization|api[_-]?key|token|password|secret", k, re.I)
                and k not in _KEEP_KEYS and not k.endswith("_tokens") else redact(v, extra)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, extra) for v in value]
    if not isinstance(value, str):
        return value
    for key in SECRET_ENV:
        secret = os.environ.get(key)
        if secret:
            value = value.replace(secret, "[REDACTED]")
    for s in extra:
        if s:
            value = value.replace(s, "[FLAG-REDACTED]")
    value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)


def write_json(path, obj, extra_redact=()):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(redact(obj, extra_redact), indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


class Trace:
    """Append-only JSONL event log. Every line is redacted before it hits disk."""

    def __init__(self, path, redactor=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.redactor = redactor or (lambda s: s)
        self.t0 = time.monotonic()
        self.seq = 0

    def event(self, _event, **fields):
        self.seq += 1
        rec = {"seq": self.seq, "t": round(time.monotonic() - self.t0, 3), "ts": now(), "event": _event, **fields}
        line = json.dumps(redact(rec), ensure_ascii=False)
        line = self.redactor(line)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return rec


def truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} chars truncated]...\n{text[-tail:]}"


def git_info():
    """Commit hash and dirty flag so every run can be tied to frozen code."""
    try:
        r = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        commit = r.stdout.strip() if r.returncode == 0 else ""
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "ctfagent", "configs", "docker", "scripts"],
                                    cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        commit, dirty = "", True
    return {"commit": commit or "uncommitted", "dirty": dirty, "code_sha256": code_manifest()}


def code_manifest():
    files = sorted(list((ROOT / "ctfagent").rglob("*.py")) + list((ROOT / "docker").glob("*")))
    blob = json.dumps({str(p.relative_to(ROOT)): sha256(p.read_bytes()) for p in files if p.is_file()}, sort_keys=True)
    return sha256(blob.encode())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafeError("redirect_refused")


def http_json(url, timeout, payload=None, credential=None, max_bytes=8_000_000):
    headers = {"User-Agent": "ctf-agent-course/1.0", "Accept": "application/json"}
    if credential:
        headers["Authorization"] = "Bearer " + credential
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=body, headers=headers)
    try:
        with build_opener(NoRedirect()).open(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
    except HTTPError as exc:
        raise SafeError(f"http_status_{exc.code}") from None
    except (URLError, TimeoutError, OSError):
        raise SafeError("network_unavailable_or_timeout") from None
    if len(data) > max_bytes:
        raise SafeError("response_size_exceeded")
    try:
        return json.loads(data)
    except ValueError:
        raise SafeError("invalid_json_response") from None
