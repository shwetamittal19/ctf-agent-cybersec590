"""TRUSTED evaluator. Holds the answer key; the solver never sees this object's state.

The agent loop receives only `Evaluator.submit` (a callable returning a verdict) and
`Evaluator.redact` (to scrub the flag from saved traces). It never receives the flag,
challenge.json, or the challenge directory path.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path


class Evaluator:
    def __init__(self, challenge_json: Path, max_submissions: int):
        data = json.loads(Path(challenge_json).read_text(encoding="utf-8"))
        flag = data.get("flag")
        if isinstance(flag, dict):  # some formats store {"flag": "..."}
            flag = flag.get("flag")
        if not isinstance(flag, str) or not flag.strip():
            raise ValueError("challenge.json has no usable flag")
        self.__flag = flag.strip()
        self.max_submissions = max_submissions
        self.submissions = []  # [{n, verdict, candidate_or_hash}]

    def submit(self, candidate: str) -> dict:
        """Verify one candidate. Logs the candidate only when it is wrong."""
        if len(self.submissions) >= self.max_submissions:
            return {"verdict": "rejected", "reason": "submission_limit_reached"}
        cand = (candidate or "").strip()
        ok = hmac.compare_digest(cand.encode(), self.__flag.encode())
        rec = {"n": len(self.submissions) + 1, "verdict": "correct" if ok else "incorrect"}
        if ok:
            rec["candidate_sha256_prefix"] = hashlib.sha256(cand.encode()).hexdigest()[:12]
        else:
            rec["candidate"] = cand[:300]
        self.submissions.append(rec)
        return dict(rec)

    def redact(self, text: str) -> str:
        return text.replace(self.__flag, "[FLAG-REDACTED]")

    @property
    def solved(self) -> bool:
        return any(s["verdict"] == "correct" for s in self.submissions)
