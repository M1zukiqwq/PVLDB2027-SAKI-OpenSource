#!/usr/bin/env python3
"""Aggregate metrics across the realistic_big_a repeated trials.

By default this script only includes trials whose ids start with
`realistic_big_` (i.e. realistic_big_a, _b, _c, _d, _e, ...). The smaller
`realistic_a` trial is intentionally excluded because it uses a different
data scale (120k keys, 55k writes/high) and so cannot be aggregated with
the main `realistic_big_` family.

For each trial we read the per-trial analysis JSON written by
`analyze_realistic.py` and pull out the absolute and percent-delta numbers
that the epoch-level main table reports: high write P99, high tier
throughput, total throughput, compaction write bytes, plus the online
high-set overlap. We then compute per-metric mean/min/max/stdev across
trials, plus a 95% confidence interval using the Student t critical value
when n>=3. When n<3 we only report range/stdev and explicitly mark the CI
as "n/a".

Outputs both a JSON payload and a markdown summary suitable for pasting
into the paper docs.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOB = str(ROOT / "remote-results" / "realistic_analysis_realistic_big_*.json")

# Student-t 0.975 quantiles for small sample sizes (df = n-1).
# n: 2 -> df=1 -> 12.706, n: 3 -> 4.303, ... Source: standard tables.
T_CRIT_975 = {
    2: 12.706,
    3: 4.303,
    4: 3.182,
    5: 2.776,
    6: 2.571,
    7: 2.447,
    8: 2.365,
    9: 2.306,
    10: 2.262,
}

# Absolute metrics we report per-policy.
POLICY_METRICS = [
    ("high_mean_write_p99_us", "High write P99 (us)"),
    ("high_sum_ops_per_sec", "High throughput (ops/s)"),
    ("mid_mean_write_p99_us", "Mid write P99 (us)"),
    ("mid_sum_ops_per_sec", "Mid throughput (ops/s)"),
    ("low_mean_write_p99_us", "Low write P99 (us)"),
    ("low_sum_ops_per_sec", "Low throughput (ops/s)"),
    ("mean_write_p99_us", "Mean write P99 (us)"),
    ("sum_ops_per_sec", "Total throughput (ops/s)"),
    ("sum_compact_write_bytes", "Compaction write bytes"),
]

# Percent-delta metrics (online vs static) we aggregate across trials.
COMPARE_METRICS = [
    ("online_vs_static_pct_high_mean_write_p99_us", "High write P99"),
    ("online_vs_static_pct_high_sum_ops_per_sec", "High throughput"),
    ("online_vs_static_pct_sum_ops_per_sec", "Total throughput"),
    ("online_vs_static_pct_sum_compact_write_bytes", "Compaction write bytes"),
    ("online_vs_static_pct_mean_write_p99_us", "Mean write P99 (all tiers)"),
    ("online_vs_static_pct_mid_mean_write_p99_us", "Mid write P99"),
    ("online_vs_static_pct_mid_sum_ops_per_sec", "Mid throughput"),
    ("online_vs_static_pct_low_mean_write_p99_us", "Low write P99"),
    ("online_vs_static_pct_low_sum_ops_per_sec", "Low throughput"),
]


def trial_id(path: str) -> str:
    base = os.path.basename(path)
    prefix = "realistic_analysis_"
    suffix = ".json"
    if base.startswith(prefix) and base.endswith(suffix):
        return base[len(prefix):-len(suffix)]
    return base


def summarize(values: list[float], confidence: float = 0.95) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0}
    out = {
        "n": n,
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "range": max(values) - min(values),
    }
    if n > 1:
        out["stdev"] = statistics.stdev(values)
        out["sem"] = out["stdev"] / math.sqrt(n)
    else:
        out["stdev"] = 0.0
        out["sem"] = 0.0
    if n >= 3 and confidence == 0.95 and n in T_CRIT_975:
        margin = T_CRIT_975[n] * out["sem"]
        out["ci95_low"] = out["mean"] - margin
        out["ci95_high"] = out["mean"] + margin
        out["ci95_margin"] = margin
    else:
        out["ci95_low"] = None
        out["ci95_high"] = None
        out["ci95_margin"] = None
    return out


def load_trial(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    summary = payload["summary"]
    per_policy = {p: {} for p in ("static", "oracle", "online")}
    for key, _label in POLICY_METRICS:
        for p in per_policy:
            per_policy[p][key] = float(summary.get(f"{p}_{key}", 0.0))
    compare = {k: float(summary.get(k, 0.0)) for k, _label in COMPARE_METRICS}
    overlap_mean = float(summary.get("online_high_overlap_mean_after_warmup", 0.0))
    exact_eps = int(summary.get("online_high_exact_epochs_after_warmup", 0))
    return {
        "trial": trial_id(path),
        "path": path,
        "per_policy": per_policy,
        "compare": compare,
        "online_high_overlap_mean_after_warmup": overlap_mean,
        "online_high_exact_epochs_after_warmup": exact_eps,
    }


def fmt_pct_stat(stat: dict) -> str:
    if not stat or stat.get("n", 0) == 0:
        return "n/a"
    parts = [f"mean={stat['mean']:+.2f}%",
             f"min={stat['min']:+.2f}%",
             f"max={stat['max']:+.2f}%",
             f"stdev={stat['stdev']:.2f}"]
    if stat.get("ci95_margin") is not None:
        parts.append(f"95%CI=[{stat['ci95_low']:+.2f}%, {stat['ci95_high']:+.2f}%]")
    else:
        parts.append("95%CI=n/a")
    return " ".join(parts)


def render_markdown(per_trial: list[dict], compare_aggs: dict, overlap_agg: dict,
                    trial_ids: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"# realistic_big_a Repeated-Trial Aggregate (n={len(per_trial)})")
    lines.append("")
    lines.append(f"Trials: {', '.join(trial_ids)}")
    lines.append("")
    lines.append("## Per-trial online vs static (percent change)")
    lines.append("")
    header = ["Trial"] + [label for _, label in COMPARE_METRICS] + ["Mean overlap"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---:"] * len(header)) + "|")
    for t in per_trial:
        row = [t["trial"]]
        for key, _label in COMPARE_METRICS:
            row.append(f"{t['compare'][key]:+.2f}%")
        row.append(f"{t['online_high_overlap_mean_after_warmup']:.2f}/4")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## Aggregate online vs static (percent change)")
    lines.append("")
    lines.append("| Metric | n | Mean | Min | Max | Stdev | 95% CI |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for key, label in COMPARE_METRICS:
        stat = compare_aggs[key]
        if stat.get("ci95_margin") is not None:
            ci = f"[{stat['ci95_low']:+.2f}%, {stat['ci95_high']:+.2f}%]"
        else:
            ci = "n/a"
        lines.append(
            f"| {label} | {stat['n']} | {stat['mean']:+.2f}% | "
            f"{stat['min']:+.2f}% | {stat['max']:+.2f}% | "
            f"{stat['stdev']:.2f} | {ci} |"
        )
    lines.append("")
    lines.append(
        f"Online high-set overlap after warmup: mean={overlap_agg['mean']:.2f}/4, "
        f"min={overlap_agg['min']:.2f}, max={overlap_agg['max']:.2f}, "
        f"stdev={overlap_agg['stdev']:.2f}."
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", default=DEFAULT_GLOB,
                        help="Glob for realistic analysis JSONs (default restricts to realistic_big_*).")
    parser.add_argument("--include-trial", action="append", default=[],
                        help="Restrict to trial ids containing this substring (repeatable).")
    parser.add_argument("--exclude-substring", action="append", default=[],
                        help="Exclude trials whose id contains this substring (repeatable).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    parser.add_argument("--out-markdown", type=Path, default=None,
                        help="If set, also write the markdown summary to this path.")
    parser.add_argument("--out-json", type=Path, default=None,
                        help="If set, also write the JSON payload to this path.")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.glob))
    if args.include_trial:
        paths = [p for p in paths if any(s in trial_id(p) for s in args.include_trial)]
    for s in args.exclude_substring:
        paths = [p for p in paths if s not in trial_id(p)]
    if not paths:
        print("no analysis files matched", file=sys.stderr)
        return 1

    per_trial = [load_trial(p) for p in paths]
    trial_ids = [t["trial"] for t in per_trial]

    compare_aggs = {}
    for key, _label in COMPARE_METRICS:
        compare_aggs[key] = summarize([t["compare"][key] for t in per_trial])
    overlap_agg = summarize([t["online_high_overlap_mean_after_warmup"] for t in per_trial])

    payload = {
        "trial_ids": trial_ids,
        "per_trial": per_trial,
        "aggregates": {
            "online_vs_static_compare": compare_aggs,
            "online_high_overlap_after_warmup": overlap_agg,
            "trial_count": len(per_trial),
        },
    }

    md = render_markdown(per_trial, compare_aggs, overlap_agg, trial_ids)

    if args.out_markdown:
        args.out_markdown.write_text(md, encoding="utf-8")
    if args.out_json:
        args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(md)
    print()
    print("Per-metric aggregate (online vs static):")
    for key, label in COMPARE_METRICS:
        print(f"  {label}: {fmt_pct_stat(compare_aggs[key])}")
    print()
    print(
        f"Online high-set overlap after warmup: mean={overlap_agg['mean']:.2f}/4 "
        f"min={overlap_agg['min']:.2f} max={overlap_agg['max']:.2f} stdev={overlap_agg['stdev']:.2f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
