#!/usr/bin/env python3
"""Print a run's trace as a numbered timeline for failure analysis / report annotation.

Usage: python3 scripts/show_trace.py runs/dev/baseline/<challenge>/trial-1-<id>/trace.jsonl [--full]
"""
import json
import sys


def short(x, n):
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)
    s = s.replace("\n", "⏎ ")
    return s if len(s) <= n else s[:n] + f"…(+{len(s) - n})"


def main():
    path = sys.argv[1]
    n = 100000 if "--full" in sys.argv else 300
    for line in open(path, encoding="utf-8"):
        e = json.loads(line)
        k, head = e["event"], f"#{e['seq']:>3} t={e['t']:>7.1f}s"
        if k == "model_call":
            u = e.get("usage") or {}
            print(f"{head} MODEL[{e['n']}] tok={u.get('prompt_tokens')}+{u.get('completion_tokens')} {short(e.get('content') or '', n)}")
            for c in e.get("tool_calls", []):
                print(f"{'':16} -> {c['name']}({short(c['arguments'], n)})")
        elif k == "tool_result":
            print(f"{head} RESULT {e['tool']}: {short(e['result'], n)}")
        elif k in ("prompt",):
            print(f"{head} PROMPT user={short(e['user'], n)}")
        else:
            rest = {x: y for x, y in e.items() if x not in ("seq", "t", "ts", "event")}
            print(f"{head} {k.upper()} {short(rest, n)}")


if __name__ == "__main__":
    main()
