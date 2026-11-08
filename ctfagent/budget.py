"""Hard resource limits enforced in code: wall clock, model turns, tool calls, tokens."""
from __future__ import annotations

import time


class BudgetExhausted(Exception):
    pass


class Budget:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.t0 = time.monotonic()
        self.deadline = self.t0 + cfg["wall_clock_s"]
        self.model_calls = 0
        self.tool_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def elapsed(self):
        return time.monotonic() - self.t0

    def remaining_s(self):
        return self.deadline - time.monotonic()

    def check(self):
        """Raise if ANY limit is hit. Called before every model call and tool call."""
        if self.remaining_s() <= 0:
            raise BudgetExhausted("wall_clock_exhausted")
        if self.model_calls >= self.cfg["max_model_calls"]:
            raise BudgetExhausted("model_call_budget_exhausted")
        if self.tool_calls >= self.cfg["max_tool_calls"]:
            raise BudgetExhausted("tool_call_budget_exhausted")
        if self.prompt_tokens + self.completion_tokens >= self.cfg["max_total_tokens"]:
            raise BudgetExhausted("token_budget_exhausted")

    def timeout(self, wanted: float) -> float:
        """Never let a single call outlive the run deadline."""
        rem = self.remaining_s()
        if rem <= 1:
            raise BudgetExhausted("wall_clock_exhausted")
        return max(1, min(wanted, rem))

    def add_model(self, usage: dict):
        self.model_calls += 1
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)

    def add_tool(self):
        self.tool_calls += 1

    def usage(self):
        return {"model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens}

    def cost(self, model_cfg: dict) -> float:
        return (self.prompt_tokens * model_cfg.get("usd_per_1m_input", 0)
                + self.completion_tokens * model_cfg.get("usd_per_1m_output", 0)) / 1e6
