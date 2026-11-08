"""Model providers.

GatewayModel   - OpenAI-compatible chat/completions with native tool calls (Duke LiteLLM
                 gateway by default). Retries 429/5xx with backoff. Adapted from Assignment 3.
ScriptedModel  - deterministic fixture for the offline self-test. NOT an LLM; never used
                 for reported results.
"""
from __future__ import annotations

import json
import os
import re
import time

from .common import SafeError, http_json


class GatewayModel:
    mode = "live_model"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.name = os.environ.get("CTF_MODEL") or cfg["name"]
        self.base = cfg.get("base_url", "https://litellm.oit.duke.edu/v1").rstrip("/")
        self.credential = os.environ.get("LITELLM_TOKEN") or os.environ.get("DUKE_AI_GATEWAY_API_KEY")
        if not self.credential:
            raise SafeError("model_credential_missing: set LITELLM_TOKEN in .env or environment")

    def chat(self, messages, tools, timeout):
        payload = {"model": self.name, "messages": messages, "tools": tools, "tool_choice": "auto",
                   "temperature": self.cfg.get("temperature", 0.2),
                   "max_tokens": self.cfg.get("max_output_tokens", 2048)}
        delay = 5
        for attempt in range(self.cfg.get("retries", 3) + 1):
            try:
                resp = http_json(self.base + "/chat/completions", timeout, payload, self.credential)
                break
            except SafeError as exc:
                retryable = str(exc) in {"http_status_429", "http_status_500", "http_status_502",
                                         "http_status_503", "http_status_504", "network_unavailable_or_timeout"}
                if not retryable or attempt == self.cfg.get("retries", 3):
                    raise
                time.sleep(delay)
                delay *= 2
        try:
            choice = resp["choices"][0]
            msg = {k: v for k, v in choice["message"].items() if k in {"role", "content", "tool_calls"}}
            msg["role"] = "assistant"
            if not msg.get("tool_calls"):
                msg.pop("tool_calls", None)
            return msg, resp.get("usage") or {}, choice.get("finish_reason")
        except (KeyError, IndexError, TypeError):
            raise SafeError("invalid_model_response") from None


class ScriptedModel:
    """Walks a fixed plan that exercises every tool, a blocked action, a wrong flag,
    and a correct flag found in tool output. Used only by `python -m ctfagent selftest`."""
    mode = "scripted_fixture"

    def __init__(self, *_):
        self.name = "scripted-fixture"
        self.step = 0

    def _call(self, name, args):
        self.step += 1
        return ({"role": "assistant", "content": f"step {self.step}",
                 "tool_calls": [{"id": f"s{self.step}", "type": "function",
                                 "function": {"name": name, "arguments": json.dumps(args)}}]},
                {"prompt_tokens": 100, "completion_tokens": 20}, "tool_calls")

    def chat(self, messages, tools, timeout):
        seen = "\n".join(m.get("content") or "" for m in messages if m.get("role") == "tool")
        if self.step == 0:
            return self._call("run_command", {"command": "ls -la /work && file /work/*"})
        if self.step == 1:
            return self._call("run_command", {"command": "curl -s https://www.google.com/search?q=toy_b64+writeup"})
        if self.step == 2:
            return self._call("write_file", {"path": "solve.sh", "content": "for f in /work/*.txt; do base64 -d < \"$f\"; done\n"})
        if self.step == 3:
            return self._call("run_command", {"command": "bash /work/solve.sh"})
        if self.step == 4:
            return self._call("submit_flag", {"flag": "flag{wrong_guess}"})
        m = re.search(r"flag\{[^}\s]+\}", seen.replace("flag{wrong_guess}", ""))
        if m and self.step == 5:
            return self._call("submit_flag", {"flag": m.group(0)})
        return self._call("give_up", {"reason": "scripted plan finished"})


def make_model(cfg: dict):
    if cfg.get("provider") == "scripted":
        return ScriptedModel(cfg)
    return GatewayModel(cfg)
