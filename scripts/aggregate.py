#!/usr/bin/env python3
"""Aggregate run summaries into the required metrics.

Usage:
    python3 scripts/aggregate.py runs/scored [--baseline baseline] [--out results/scored]

Reads every <label>/<config>/<challenge>/trial-*/summary.json and writes:
    results.md        per-config metrics, per-challenge successes (x/3), comparison deltas, incidents
    attempts.csv      one row per attempt (failed attempts included)
    pass_at_k.png, solve_rate.png   (if matplotlib is installed)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from math import comb
from pathlib import Path


def load(root: Path):
    rows = []
    for p in sorted(root.rglob("summary.json")):
        s = json.loads(p.read_text())
        s["_path"] = str(p.parent)
        rows.append(s)
    return rows


def pass_at_k_unbiased(n, c, k):
    """HumanEval estimator: P(at least one success in k draws from n trials with c successes)."""
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def summarize(rows):
    by_chal = defaultdict(list)
    for r in rows:
        by_chal[r["challenge_id"]].append(r)
    for v in by_chal.values():
        v.sort(key=lambda r: r["trial"])
    n_trials = max((len(v) for v in by_chal.values()), default=0)
    chals = sorted(by_chal)

    # pass@k (empirical: solved in any of the first k trials) and unbiased estimator.
    pass_k = {}
    for k in range(1, n_trials + 1):
        emp = [any(r["solved"] for r in by_chal[c][:k]) for c in chals]
        unb = [pass_at_k_unbiased(len(by_chal[c]), sum(r["solved"] for r in by_chal[c]), k)
               for c in chals if len(by_chal[c]) >= k]
        pass_k[k] = {"empirical": sum(emp) / len(chals), "unbiased": sum(unb) / len(unb) if unb else None}

    # Solve rate per run (trial index t = one full pass over the set).
    per_run = {}
    for t in range(1, n_trials + 1):
        sel = [r for r in rows if r["trial"] == t]
        if sel:
            per_run[t] = sum(r["solved"] for r in sel) / len(sel)
    rates = list(per_run.values())
    mean = statistics.mean(rates) if rates else 0
    var = statistics.variance(rates) if len(rates) > 1 else 0.0

    def tot(key):
        return sum(r.get(key) or 0 for r in rows)

    n = len(rows) or 1
    return {
        "challenges": chals, "by_chal": by_chal, "n_trials": n_trials, "attempts": len(rows),
        "pass_k": pass_k, "per_run": per_run, "mean_solve_rate": mean, "var_solve_rate": var,
        "std_solve_rate": var ** 0.5,
        "wall_s_total": tot("wall_s"), "wall_s_mean": tot("wall_s") / n,
        "model_calls": tot("model_calls"), "tool_calls": tot("tool_calls"),
        "prompt_tokens": tot("prompt_tokens"), "completion_tokens": tot("completion_tokens"),
        "total_tokens": tot("total_tokens"), "tokens_mean": tot("total_tokens") / n,
        "cost_total": tot("est_cost_usd"), "cost_mean": tot("est_cost_usd") / n,
        "solved_attempts": [r for r in rows if r["solved"]],
        "stop_reasons": dict(sorted(_count(r["stop_reason"] for r in rows).items(), key=lambda x: -x[1])),
        "blocked": [(r["challenge_id"], r["trial"], r["run_id"], b) for r in rows for b in r.get("blocked_actions", [])],
        "dirty": any(r.get("dirty") for r in rows), "commits": sorted({r.get("commit") for r in rows}),
    }


def _count(items):
    d = defaultdict(int)
    for i in items:
        d[i] += 1
    return d


def fmt_pct(x):
    return "n/a" if x is None else f"{100 * x:.1f}%"


def write_report(root, out, stats, baseline):
    L = [f"# Results for `{root}`", ""]
    L += ["## Summary by configuration", "",
          "| Config | Attempts | Trials | " + " | ".join(f"pass@{k}" for k in range(1, 4)) +
          " | Mean solve rate ± std | Wall total (s) | Wall/attempt (s) | Model calls | Tool calls | Tokens total | Tokens/attempt | Est. cost total | Cost/attempt | Commit(s) |",
          "|" + "---|" * 16]
    for name, s in stats.items():
        pk = [fmt_pct(s["pass_k"].get(k, {}).get("empirical")) for k in range(1, 4)]
        L.append(f"| {name} | {s['attempts']} | {s['n_trials']} | " + " | ".join(pk) +
                 f" | {fmt_pct(s['mean_solve_rate'])} ± {100 * s['std_solve_rate']:.1f} pp | {s['wall_s_total']:.0f} | {s['wall_s_mean']:.0f} | "
                 f"{s['model_calls']} | {s['tool_calls']} | {s['total_tokens']:,} | {s['tokens_mean']:,.0f} | "
                 f"${s['cost_total']:.2f} | ${s['cost_mean']:.3f} | {', '.join(c[:8] for c in s['commits'])}{' (DIRTY)' if s['dirty'] else ''} |")
    L += ["", "pass@k is empirical (solved in any of the first k trials). Unbiased estimates:", ""]
    for name, s in stats.items():
        L.append(f"- {name}: " + ", ".join(f"pass@{k}={fmt_pct(v['unbiased'])}" for k, v in s["pass_k"].items()))
    L += ["", "Solve rate per run (trial index):", ""]
    for name, s in stats.items():
        L.append(f"- {name}: " + ", ".join(f"run {t}: {fmt_pct(v)}" for t, v in s["per_run"].items())
                 + f"; variance={s['var_solve_rate']:.4f}")

    allc = sorted({c for s in stats.values() for c in s["challenges"]})
    L += ["", "## Successes per challenge", "", "| Challenge | " + " | ".join(stats) + " |", "|---|" + "---|" * len(stats)]
    for c in allc:
        cells = []
        for s in stats.values():
            v = s["by_chal"].get(c, [])
            cells.append(f"{sum(r['solved'] for r in v)}/{len(v)} " + "".join("✓" if r["solved"] else "✗" for r in v) if v else "-")
        L.append(f"| {c} | " + " | ".join(cells) + " |")

    if baseline in stats and len(stats) > 1:
        b = stats[baseline]
        L += ["", f"## Change vs `{baseline}`", "",
              "| Config | Δ mean solve rate (pp) | Δ pass@1 (pp) | Δ pass@3 (pp) | Δ wall/attempt (s) | Δ tokens/attempt | Δ cost/attempt |",
              "|---|---|---|---|---|---|---|"]
        for name, s in stats.items():
            if name == baseline:
                continue

            def d(k):
                a, bb = s["pass_k"].get(k, {}).get("empirical"), b["pass_k"].get(k, {}).get("empirical")
                return "n/a" if a is None or bb is None else f"{100 * (a - bb):+.1f}"
            L.append(f"| {name} | {100 * (s['mean_solve_rate'] - b['mean_solve_rate']):+.1f} | {d(1)} | {d(3)} | "
                     f"{s['wall_s_mean'] - b['wall_s_mean']:+.0f} | {s['tokens_mean'] - b['tokens_mean']:+,.0f} | "
                     f"${s['cost_mean'] - b['cost_mean']:+.3f} |")

    L += ["", "## Stop reasons", ""]
    for name, s in stats.items():
        L.append(f"- {name}: " + ", ".join(f"{k}={v}" for k, v in s["stop_reasons"].items()))
    L += ["", "## Blocked actions / incidents", ""]
    any_blocked = False
    for name, s in stats.items():
        for c, t, rid, b in s["blocked"]:
            any_blocked = True
            L.append(f"- [{name}] {c} trial {t} run {rid}: **{b['reason']}** `{b.get('value')}` — `{(b.get('command') or '')[:120]}`")
    if not any_blocked:
        L.append("- none recorded")
    (out / "results.md").write_text("\n".join(L) + "\n")


def write_csv(out, rows):
    cols = ["config", "challenge_id", "category", "trial", "run_id", "solved", "status", "stop_reason", "wall_s",
            "model_calls", "tool_calls", "prompt_tokens", "completion_tokens", "total_tokens", "est_cost_usd",
            "incorrect_submissions", "commit", "dirty", "_path"]
    with (out / "attempts.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def plot(out, stats):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping plots")
        return
    fig, ax = plt.subplots(figsize=(5, 3.5))
    for name, s in stats.items():
        ks = sorted(s["pass_k"])
        ax.plot(ks, [100 * s["pass_k"][k]["empirical"] for k in ks], marker="o", label=name)
    ax.set_xlabel("k (trials)"); ax.set_ylabel("pass@k (%)"); ax.set_xticks([1, 2, 3]); ax.set_ylim(0, 100)
    ax.set_title("pass@k"); ax.legend(); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(out / "pass_at_k.png", dpi=150); plt.close(fig)

    fig, ax = plt.subplots(figsize=(5, 3.5))
    names = list(stats)
    means = [100 * stats[n]["mean_solve_rate"] for n in names]
    stds = [100 * stats[n]["std_solve_rate"] for n in names]
    ax.bar(names, means, yerr=stds, capsize=6, color="#4C78A8")
    for i, n in enumerate(names):
        ax.scatter([i] * len(stats[n]["per_run"]), [100 * v for v in stats[n]["per_run"].values()], color="black", zorder=3, s=14)
    ax.set_ylabel("solve rate per run (%)"); ax.set_ylim(0, 100); ax.set_title("Average solve rate (±1 std; dots = runs)")
    fig.tight_layout(); fig.savefig(out / "solve_rate.png", dpi=150); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", help="e.g. runs/scored")
    ap.add_argument("--baseline", default="baseline")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = Path(a.root)
    out = Path(a.out or Path("results") / root.name)
    out.mkdir(parents=True, exist_ok=True)
    rows = load(root)
    if not rows:
        raise SystemExit(f"no summary.json under {root}")
    by_cfg = defaultdict(list)
    for r in rows:
        by_cfg[r["config"]].append(r)
    stats = {name: summarize(v) for name, v in sorted(by_cfg.items(), key=lambda x: (x[0] != a.baseline, x[0]))}
    write_report(root, out, stats, a.baseline)
    write_csv(out, rows)
    plot(out, stats)
    print(f"wrote {out}/results.md, attempts.csv")


if __name__ == "__main__":
    main()
