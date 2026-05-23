#!/usr/bin/env python3
"""Aggregate metrics across the aligned cgroup baseline trials.

Reads per-trial analysis JSONs produced by
`analyze_cgroup_realistic_smoke.py` (one per trial, e.g.
`cgroup_smoke_analysis_realistic_cgroup_aligned_a.json`) and aggregates
the key cross-policy comparisons across the 5 repeated trials.

For each policy we report:
- vs_rocksdb_static percent deltas: high P99, high tput, total tput,
  compaction write bytes.
- vs_cgroup_equal percent deltas: same metrics, same-mechanism baseline.
- Mean high-set overlap after warmup (used only by online policies).

Aggregates use mean / min / max / stdev / 95% CI (Student-t) when n>=3.
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
DEFAULT_GLOB = str(ROOT / "remote-results" / "cgroup_smoke_analysis_realistic_cgroup_aligned_*.json")

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

POLICIES = [
    "rocksdb_static",
    "rocksdb_online",
    "cgroup_equal",
    "cgroup_online",
    "cgroup_static_biased",
    "cgroup_oracle_tiered",
]

COMPARE_KEYS = [
    ("pct_high_mean_write_p99_us", "High write P99"),
    ("pct_high_sum_ops_per_sec", "High throughput"),
    ("pct_sum_ops_per_sec", "Total throughput"),
    ("pct_sum_compact_write_bytes", "Compaction write bytes"),
    ("pct_mean_write_p99_us", "Mean write P99 (all tiers)"),
    ("pct_mid_mean_write_p99_us", "Mid write P99"),
    ("pct_mid_sum_ops_per_sec", "Mid throughput"),
    ("pct_low_mean_write_p99_us", "Low write P99"),
    ("pct_low_sum_ops_per_sec", "Low throughput"),
]


def trial_id(path: str) -> str:
    base = os.path.basename(path)
    prefix = "cgroup_smoke_analysis_"
    suffix = ".json"
    if base.startswith(prefix) and base.endswith(suffix):
        return base[len(prefix) : -len(suffix)]
    return base


def summarize(values: list[float]) -> dict:
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
    if n >= 3 and n in T_CRIT_975:
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
    comp = payload.get("comparisons", {})
    overlap = payload.get("overlap", {})
    out_policies: dict[str, dict[str, float]] = {}
    for policy in POLICIES:
        c = comp.get(policy, {})
        rec: dict[str, float] = {"failed_tenants": float(c.get("failed_tenants", 0.0))}
        for prefix in ("vs_rocksdb_static", "vs_cgroup_equal"):
            for key, _label in COMPARE_KEYS:
                full = f"{prefix}_{key}"
                if full in c:
                    rec[full] = float(c[full])
        # Mean high-set overlap after warmup (out of high_count).
        o = overlap.get(policy, {})
        rec["overlap_mean_after_warmup"] = float(o.get("mean_after_warmup", 0.0))
        rec["overlap_exact_after_warmup"] = float(o.get("exact_after_warmup", 0.0))
        out_policies[policy] = rec
    return {"trial": trial_id(path), "path": path, "policies": out_policies}


def fmt_pct(stat: dict) -> str:
    if not stat or stat.get("n", 0) == 0:
        return "n/a"
    if stat.get("ci95_margin") is not None:
        return (
            f"mean={stat['mean']:+.2f}% min={stat['min']:+.2f}% max={stat['max']:+.2f}% "
            f"stdev={stat['stdev']:.2f} 95%CI=[{stat['ci95_low']:+.2f}%, {stat['ci95_high']:+.2f}%]"
        )
    return (
        f"mean={stat['mean']:+.2f}% min={stat['min']:+.2f}% max={stat['max']:+.2f}% "
        f"stdev={stat['stdev']:.2f} 95%CI=n/a"
    )


def render_markdown(per_trial: list[dict], aggregates: dict) -> str:
    lines: list[str] = []
    lines.append(f"# aligned cgroup baseline aggregate (n={len(per_trial)})")
    lines.append("")
    lines.append("Trials: " + ", ".join(t["trial"] for t in per_trial))
    lines.append("")
    lines.append(
        "Workload: 16 tenants, 8 epochs, num-keys=240000, writes-high=100000, "
        "high-count=4, low-count=4 (matches `realistic_big_*` scale)."
    )
    lines.append("")
    lines.append("All numbers are aggregate-level (sum/mean across tenants per trial) ")
    lines.append("then mean/CI across trials. Negative P99 / compaction-bytes deltas are improvements.")
    lines.append("")

    failed_total: dict[str, int] = {}
    for policy in POLICIES:
        failed_total[policy] = sum(
            int(t["policies"][policy].get("failed_tenants", 0.0)) for t in per_trial
        )

    for baseline_label, prefix in (
        ("vs `rocksdb_static`", "vs_rocksdb_static"),
        ("vs `cgroup_equal` (same-mechanism)", "vs_cgroup_equal"),
    ):
        lines.append(f"## Aggregate {baseline_label}")
        lines.append("")
        lines.append("| Policy | Failed (total) | High P99 mean | High P99 95%CI | High tput mean | High tput 95%CI | Total tput mean | Total tput 95%CI | Compact bytes mean | Compact bytes 95%CI | Overlap mean |")
        lines.append("|---|---:|---:|---|---:|---|---:|---|---:|---|---:|")
        for policy in POLICIES:
            if prefix == "vs_cgroup_equal" and not policy.startswith("cgroup_"):
                continue
            agg = aggregates[policy][prefix]
            p99 = agg["pct_high_mean_write_p99_us"]
            ht = agg["pct_high_sum_ops_per_sec"]
            tt = agg["pct_sum_ops_per_sec"]
            cb = agg["pct_sum_compact_write_bytes"]
            ov = aggregates[policy]["overlap"]
            def ci(stat: dict) -> str:
                if stat.get("ci95_margin") is None:
                    return "n/a"
                return f"[{stat['ci95_low']:+.2f}, {stat['ci95_high']:+.2f}]"

            lines.append(
                f"| {policy} | {failed_total[policy]} | "
                f"{p99['mean']:+.2f}% | {ci(p99)} | "
                f"{ht['mean']:+.2f}% | {ci(ht)} | "
                f"{tt['mean']:+.2f}% | {ci(tt)} | "
                f"{cb['mean']:+.2f}% | {ci(cb)} | "
                f"{ov['mean']:.2f}/4 |"
            )
        lines.append("")

    lines.append("## Per-trial details")
    lines.append("")
    for t in per_trial:
        lines.append(f"### {t['trial']}")
        lines.append("")
        lines.append("| Policy | Failed | High P99 vs static | High tput vs static | Total tput vs static | Compact bytes vs static | Overlap |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for policy in POLICIES:
            r = t["policies"][policy]
            lines.append(
                f"| {policy} | {int(r['failed_tenants'])} | "
                f"{r.get('vs_rocksdb_static_pct_high_mean_write_p99_us', 0.0):+.2f}% | "
                f"{r.get('vs_rocksdb_static_pct_high_sum_ops_per_sec', 0.0):+.2f}% | "
                f"{r.get('vs_rocksdb_static_pct_sum_ops_per_sec', 0.0):+.2f}% | "
                f"{r.get('vs_rocksdb_static_pct_sum_compact_write_bytes', 0.0):+.2f}% | "
                f"{r['overlap_mean_after_warmup']:.2f}/4 |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glob", default=DEFAULT_GLOB)
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-markdown", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = sorted(glob.glob(args.glob))
    if not paths:
        print(f"no analysis files matched: {args.glob}", file=sys.stderr)
        return 1

    per_trial = [load_trial(p) for p in paths]

    aggregates: dict[str, dict] = {}
    for policy in POLICIES:
        policy_agg: dict[str, dict] = {}
        for prefix in ("vs_rocksdb_static", "vs_cgroup_equal"):
            stats: dict[str, dict] = {}
            for key, _label in COMPARE_KEYS:
                full = f"{prefix}_{key}"
                values = [t["policies"][policy].get(full) for t in per_trial]
                values = [float(v) for v in values if v is not None]
                stats[key] = summarize(values)
            policy_agg[prefix] = stats
        overlap_values = [float(t["policies"][policy]["overlap_mean_after_warmup"]) for t in per_trial]
        policy_agg["overlap"] = summarize(overlap_values)
        aggregates[policy] = policy_agg

    payload = {
        "trial_ids": [t["trial"] for t in per_trial],
        "trial_count": len(per_trial),
        "per_trial": per_trial,
        "aggregates": aggregates,
    }

    md = render_markdown(per_trial, aggregates)

    if args.out_json:
        args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    if args.out_markdown:
        args.out_markdown.write_text(md, encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
