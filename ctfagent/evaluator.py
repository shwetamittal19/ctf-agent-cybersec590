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
    def __init__(self, answer_key: tuple, max_submissions: int):
        """answer_key = (path, id). id None -> path is a challenge.json with a "flag" field.
        Otherwise path is private/flags.json {challenge_id: flag}. If no flag is available,
        the evaluator runs in UNVERIFIED mode: one submission is recorded, scored later."""
        path, cid = answer_key
        flag = None
        if Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            flag = data.get("flag") if cid is None else data.get(cid)
            if isinstance(flag, dict):
                flag = flag.get("flag")
        self.__flag = flag.strip() if isinstance(flag, str) and flag.strip() else None
        self.verified_mode = self.__flag is not None
        self.max_submissions = max_submissions if self.verified_mode else 1
        self.submissions = []  # [{n, verdict, candidate_or_hash}]

    def submit(self, candidate: str) -> dict:
        """Verify one candidate. Logs the candidate only when it is wrong."""
        if len(self.submissions) >= self.max_submissions:
            return {"verdict": "rejected", "reason": "submission_limit_reached"}
        cand = (candidate or "").strip()
        if not self.verified_mode:
            rec = {"n": len(self.submissions) + 1, "verdict": "recorded_unverified", "candidate": cand[:300]}
            self.submissions.append(rec)
            return dict(rec)
        ok = hmac.compare_digest(cand.encode(), self.__flag.encode())
        rec = {"n": len(self.submissions) + 1, "verdict": "correct" if ok else "incorrect"}
        if ok:
            rec["candidate_sha256_prefix"] = hashlib.sha256(cand.encode()).hexdigest()[:12]
        else:
            rec["candidate"] = cand[:300]
        self.submissions.append(rec)
        return dict(rec)

    def redact(self, text: str) -> str:
        return text.replace(self.__flag, "[FLAG-REDACTED]") if self.__flag else text

    @property
    def solved(self) -> bool:
        return any(s["verdict"] == "correct" for s in self.submissions)
