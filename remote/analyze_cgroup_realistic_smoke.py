#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_results_dir(requested: Path | None, trial: str, policies: list[str]) -> Path:
    if requested is not None:
        return requested
    root = default_root()
    candidates = [root / "results", root / "remote-results"]
    for candidate in candidates:
        if all((candidate / f"cgroup_smoke_{policy}_{trial}.json").exists() for policy in policies):
            return candidate
    return root / "remote-results"


DEFAULT_POLICIES = [
    "rocksdb_static",
    "rocksdb_online",
    "cgroup_equal",
    "cgroup_online",
    "cgroup_static_biased",
    "cgroup_oracle_tiered",
]


KEYS = [
    "high_mean_write_p99_us",
    "high_sum_ops_per_sec",
    "mid_mean_write_p99_us",
    "mid_sum_ops_per_sec",
    "low_mean_write_p99_us",
    "low_sum_ops_per_sec",
    "mean_write_p99_us",
    "sum_ops_per_sec",
    "sum_compact_write_bytes",
]


def pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0 if old else 0.0


def high_assigned(alloc: dict) -> list[str]:
    return sorted(name for name, values in alloc.items() if values["assigned_tier"] == "high")


def overlap(xs: list[str], ys: list[str]) -> int:
    return len(set(xs) & set(ys))


def load(policy: str, trial: str, results_dir: Path) -> dict:
    path = results_dir / f"cgroup_smoke_{policy}_{trial}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "policy": policy,
        "path": str(path),
        "summary": payload["summary"],
        "epochs": payload["epoch_summaries"],
        "alloc": payload.get("alloc_history", []),
        "tiers": payload.get("tier_history", []),
        "args": payload.get("args", {}),
    }


def add_comparison(entry: dict[str, float], summary: dict, baseline: dict, prefix: str) -> None:
    for key in KEYS:
        if key in summary and key in baseline:
            entry[f"{prefix}_pct_{key}"] = pct(float(summary[key]), float(baseline[key]))


def overlap_summary(row: dict) -> dict[str, object]:
    checks = []
    for epoch, alloc in enumerate(row["alloc"]):
        if epoch >= len(row["tiers"]):
            continue
        assigned = high_assigned(alloc)
        true_high = sorted(row["tiers"][epoch]["high"])
        checks.append(
            {
                "epoch": epoch,
                "true_high": true_high,
                "assigned_high": assigned,
                "overlap": overlap(assigned, true_high),
                "correct": assigned == true_high,
            }
        )
    warm = checks[1:] if len(checks) > 1 else checks
    return {
        "history": checks,
        "mean_after_warmup": sum(x["overlap"] for x in warm) / max(1, len(warm)),
        "exact_after_warmup": sum(1 for x in warm if x["correct"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", default="realistic_cgroup_smoke_a")
    parser.add_argument("--policies", nargs="*", default=DEFAULT_POLICIES)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Directory holding cgroup_smoke_<policy>_<trial>.json. Defaults to this repo's results/ or remote-results/.",
    )
    args = parser.parse_args()
    results_dir = resolve_results_dir(args.results_dir, args.trial, args.policies)

    rows = [load(policy, args.trial, results_dir) for policy in args.policies]
    by_policy = {row["policy"]: row for row in rows}
    rocksdb_baseline = by_policy["rocksdb_static"]["summary"]
    cgroup_baseline = by_policy.get("cgroup_equal", by_policy["rocksdb_static"])["summary"]

    comparisons: dict[str, dict[str, float]] = {}
    for row in rows:
        policy = row["policy"]
        summary = row["summary"]
        entry: dict[str, float] = {}
        for key in KEYS:
            if key in summary and key in rocksdb_baseline:
                entry[key] = float(summary[key])
        add_comparison(entry, summary, rocksdb_baseline, "vs_rocksdb_static")
        add_comparison(entry, summary, cgroup_baseline, "vs_cgroup_equal")
        entry["failed_tenants"] = float(summary.get("failed_tenants", 0.0))
        comparisons[policy] = entry

    overlaps = {policy: overlap_summary(row) for policy, row in by_policy.items()}
    payload = {
        "trial": args.trial,
        "policies": args.policies,
        "comparisons": comparisons,
        "overlap": overlaps,
        "sources": {row["policy"]: row["path"] for row in rows},
        "notes": [
            "rocksdb_static is the cross-mechanism reference.",
            "cgroup_equal is the same-mechanism reference for cgroup policies.",
            "Negative P99/compact-byte values are improvements; positive throughput values are improvements.",
        ],
    }
    out_json = results_dir / f"cgroup_smoke_analysis_{args.trial}.json"
    out_md = results_dir / f"cgroup_smoke_analysis_{args.trial}.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        f"# cgroup smoke analysis: {args.trial}",
        "",
        "Baseline for percentages: `rocksdb_static`.",
        "",
        "| Policy | Failed | High P99 | High tput | Total tput | Compact bytes | Mean high overlap |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for policy in args.policies:
        c = comparisons[policy]
        o = overlaps[policy]
        lines.append(
            "| {policy} | {failed:.0f} | {p99:+.1f}% | {ht:+.1f}% | {tt:+.1f}% | {cb:+.1f}% | {ov:.2f} |".format(
                policy=policy,
                failed=c["failed_tenants"],
                p99=c.get("vs_rocksdb_static_pct_high_mean_write_p99_us", 0.0),
                ht=c.get("vs_rocksdb_static_pct_high_sum_ops_per_sec", 0.0),
                tt=c.get("vs_rocksdb_static_pct_sum_ops_per_sec", 0.0),
                cb=c.get("vs_rocksdb_static_pct_sum_compact_write_bytes", 0.0),
                ov=o["mean_after_warmup"],
            )
        )
    lines.extend(["", "Negative P99/compact-byte values are improvements; positive throughput values are improvements.", ""])
    lines.extend(
        [
            "## Same-mechanism cgroup comparison",
            "",
            "Baseline for percentages: `cgroup_equal`.",
            "",
            "| Policy | High P99 | High tput | Total tput | Compact bytes | Mean high overlap |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for policy in args.policies:
        if not policy.startswith("cgroup_"):
            continue
        c = comparisons[policy]
        o = overlaps[policy]
        lines.append(
            "| {policy} | {p99:+.1f}% | {ht:+.1f}% | {tt:+.1f}% | {cb:+.1f}% | {ov:.2f} |".format(
                policy=policy,
                p99=c.get("vs_cgroup_equal_pct_high_mean_write_p99_us", 0.0),
                ht=c.get("vs_cgroup_equal_pct_high_sum_ops_per_sec", 0.0),
                tt=c.get("vs_cgroup_equal_pct_sum_ops_per_sec", 0.0),
                cb=c.get("vs_cgroup_equal_pct_sum_compact_write_bytes", 0.0),
                ov=o["mean_after_warmup"],
            )
        )
    lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(payload["comparisons"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
