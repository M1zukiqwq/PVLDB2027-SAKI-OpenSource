#!/usr/bin/env python3
"""Aggregate static_biased results across realistic_big repeated trials.

Reads per-trial analysis JSONs from analyze_realistic_static_biased.py
and computes mean/min/max/stdev/95%-CI for:
  - static_biased vs static
  - online vs static_biased
  - online vs static (for reference)

Outputs markdown and JSON to remote-results/paper_tables/.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GLOB = str(ROOT / "remote-results" / "realistic_analysis_static_biased_realistic_big_*.json")

T_CRIT_975 = {
    2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776,
    6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262,
}

COMPARE_PAIRS = [
    ("static_biased_vs_static", [
        ("static_biased_vs_static_pct_high_mean_write_p99_us", "High write P99"),
        ("static_biased_vs_static_pct_high_sum_ops_per_sec", "High throughput"),
        ("static_biased_vs_static_pct_sum_ops_per_sec", "Total throughput"),
        ("static_biased_vs_static_pct_sum_compact_write_bytes", "Compaction write bytes"),
        ("static_biased_vs_static_pct_mid_mean_write_p99_us", "Mid write P99"),
        ("static_biased_vs_static_pct_mid_sum_ops_per_sec", "Mid throughput"),
        ("static_biased_vs_static_pct_low_mean_write_p99_us", "Low write P99"),
        ("static_biased_vs_static_pct_low_sum_ops_per_sec", "Low throughput"),
    ]),
    ("online_vs_static_biased", [
        ("online_vs_static_biased_pct_high_mean_write_p99_us", "High write P99"),
        ("online_vs_static_biased_pct_high_sum_ops_per_sec", "High throughput"),
        ("online_vs_static_biased_pct_sum_ops_per_sec", "Total throughput"),
        ("online_vs_static_biased_pct_sum_compact_write_bytes", "Compaction write bytes"),
        ("online_vs_static_biased_pct_mid_mean_write_p99_us", "Mid write P99"),
        ("online_vs_static_biased_pct_mid_sum_ops_per_sec", "Mid throughput"),
        ("online_vs_static_biased_pct_low_mean_write_p99_us", "Low write P99"),
        ("online_vs_static_biased_pct_low_sum_ops_per_sec", "Low throughput"),
    ]),
]


def trial_id(path: str) -> str:
    base = str(Path(path).name)
    prefix = "realistic_analysis_static_biased_"
    suffix = ".json"
    if base.startswith(prefix) and base.endswith(suffix):
        return base[len(prefix):-len(suffix)]
    return base


def summarize(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0}
    out = {"n": n, "mean": statistics.fmean(values), "min": min(values), "max": max(values)}
    if n > 1:
        out["stdev"] = statistics.stdev(values)
        out["sem"] = out["stdev"] / math.sqrt(n)
    else:
        out["stdev"] = 0.0
        out["sem"] = 0.0
    if n >= 3 and n in T_CRIT_975:
        margin = T_CRIT_975[n] * out["sem"]
        out["ci95_low"] = out["mean"] - margin
        out["ci95_high"] = out["mean"] + margin
    else:
        out["ci95_low"] = None
        out["ci95_high"] = None
    return out


def load_trial(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    s = payload["summary"]
    data = {"trial": trial_id(path)}
    for pair_name, metrics in COMPARE_PAIRS:
        data[pair_name] = {k: float(s.get(k, 0.0)) for k, _ in metrics}
    data["sb_overlap"] = float(s.get("static_biased_high_overlap_mean_after_warmup", 0.0))
    data["online_overlap"] = float(s.get("online_high_overlap_mean_after_warmup", 0.0))
    return data


def fmt_ci(stat: dict) -> str:
    if stat.get("ci95_low") is not None:
        return f"[{stat['ci95_low']:+.2f}%, {stat['ci95_high']:+.2f}%]"
    return "n/a"


def render_markdown(per_trial: list[dict], aggs: dict, overlap_aggs: dict) -> str:
    lines: list[str] = []
    lines.append(f"# realistic_big static_biased Repeated-Trial Aggregate (n={len(per_trial)})")
    lines.append("")
    trial_ids = [t["trial"] for t in per_trial]
    lines.append(f"Trials: {', '.join(trial_ids)}")
    lines.append("")

    for pair_name, metrics in COMPARE_PAIRS:
        label = pair_name.replace("_", " ").title()
        lines.append(f"## Per-trial {label} (percent change)")
        lines.append("")
        header = ["Trial"] + [m[1] for m in metrics]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---:"] * len(header)) + "|")
        for t in per_trial:
            row = [t["trial"]]
            for k, _ in metrics:
                row.append(f"{t[pair_name][k]:+.2f}%")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

        lines.append(f"## Aggregate {label}")
        lines.append("")
        lines.append("| Metric | n | Mean | Min | Max | Stdev | 95% CI |")
        lines.append("|---|---:|---:|---:|---:|---:|---|")
        for k, label_m in metrics:
            stat = aggs[pair_name][k]
            ci = fmt_ci(stat)
            lines.append(
                f"| {label_m} | {stat['n']} | {stat['mean']:+.2f}% | "
                f"{stat['min']:+.2f}% | {stat['max']:+.2f}% | "
                f"{stat['stdev']:.2f} | {ci} |"
            )
        lines.append("")

    # Overlap
    lines.append("## Overlap after warmup")
    lines.append("")
    for policy, agg_key in [("static_biased", "sb_overlap"), ("online", "online_overlap")]:
        a = overlap_aggs[agg_key]
        lines.append(f"- {policy}: mean={a['mean']:.2f}/4, min={a['min']:.2f}, max={a['max']:.2f}, stdev={a['stdev']:.2f}")
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", default=DEFAULT_GLOB)
    parser.add_argument("--out-markdown", type=Path, default=None)
    parser.add_argument("--out-json", type=Path, default=None)
    args = parser.parse_args()

    paths = sorted(glob.glob(args.glob))
    if not paths:
        print("no analysis files matched", file=sys.stderr)
        return 1

    per_trial = [load_trial(p) for p in paths]

    aggs = {}
    for pair_name, metrics in COMPARE_PAIRS:
        aggs[pair_name] = {}
        for k, _ in metrics:
            aggs[pair_name][k] = summarize([t[pair_name][k] for t in per_trial])

    overlap_aggs = {
        "sb_overlap": summarize([t["sb_overlap"] for t in per_trial]),
        "online_overlap": summarize([t["online_overlap"] for t in per_trial]),
    }

    payload = {
        "trial_ids": [t["trial"] for t in per_trial],
        "per_trial": per_trial,
        "aggregates": {**aggs, "overlap": overlap_aggs, "trial_count": len(per_trial)},
    }

    md = render_markdown(per_trial, aggs, overlap_aggs)

    if args.out_markdown:
        args.out_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.out_markdown.write_text(md, encoding="utf-8")
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
