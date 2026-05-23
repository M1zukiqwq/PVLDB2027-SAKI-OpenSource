#!/usr/bin/env python3
"""Analyze realistic scheduler results including static_autotuned (built-in) policy.

Reads static, static_autotuned, oracle_tiered, online (and optionally
static_biased) result JSONs for a trial, then computes percent-delta
comparisons and overlap metrics.

The static_autotuned policy uses the RocksDB built-in
`--rate_limiter_auto_tuned=1` flag with the same per-tenant ceilings as
`static` (so the aggregate budget is identical), and tests whether RocksDB's
own per-instance local auto-tuning closes the gap that the external
host-level online controller targets.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_results_dir(requested: Path | None, trial: str, policies: list[str]) -> Path:
    if requested is not None:
        return requested
    root = default_root()
    candidates = [root / "results", root / "remote-results"]
    for candidate in candidates:
        if all((candidate / f"realistic_{policy}_{trial}.json").exists() for policy in policies):
            return candidate
    return root / "remote-results"


def pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0 if old else 0.0


def high_assigned(alloc: dict) -> list[str]:
    return sorted(name for name, values in alloc.items() if values["assigned_tier"] == "high")


def overlap(xs: list[str], ys: list[str]) -> int:
    return len(set(xs) & set(ys))


def compute_overlap(alloc_history: list[dict], tier_history: list[dict], warmup: int = 1) -> dict:
    checks = []
    for epoch, alloc in enumerate(alloc_history):
        assigned = high_assigned(alloc)
        true_high = sorted(tier_history[epoch]["high"])
        checks.append({
            "epoch": epoch,
            "true_high": true_high,
            "assigned_high": assigned,
            "overlap": overlap(assigned, true_high),
            "correct": assigned == true_high,
        })
    after = checks[warmup:]
    mean_overlap = sum(x["overlap"] for x in after) / max(1, len(after))
    exact_count = sum(1 for x in after if x["correct"])
    return {
        "checks": checks,
        "mean_overlap_after_warmup": mean_overlap,
        "exact_epochs_after_warmup": exact_count,
    }


def load_policy(trial: str, policy: str, results_dir: Path) -> dict | None:
    path = results_dir / f"realistic_{policy}_{trial}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return {
        "policy": payload["summary"]["policy"],
        "summary": payload["summary"],
        "epochs": payload["epoch_summaries"],
        "alloc": payload.get("alloc_history", []),
        "tiers": payload.get("tier_history", []),
        "workload": payload.get("workload", []),
        "failed_tenants": int(float(payload["summary"].get("failed_tenants", 0.0))),
    }


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", default="realistic_big_a")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Directory holding realistic_<policy>_<trial>.json. Defaults to this repo's results/ or remote-results/.",
    )
    args = parser.parse_args()

    results_dir = resolve_results_dir(args.results_dir, args.trial, ["static", "static_autotuned"])

    static = load_policy(args.trial, "static", results_dir)
    builtin = load_policy(args.trial, "static_autotuned", results_dir)
    online = load_policy(args.trial, "online", results_dir)
    oracle = load_policy(args.trial, "oracle_tiered", results_dir)
    static_biased = load_policy(args.trial, "static_biased", results_dir)

    if not static:
        print(f"ERROR: static result not found for trial {args.trial}", file=sys.stderr)
        return 1
    if not builtin:
        print(f"ERROR: static_autotuned result not found for trial {args.trial}", file=sys.stderr)
        return 1

    summary: dict[str, object] = {}

    for label, data in [
        ("static", static),
        ("static_autotuned", builtin),
        ("oracle", oracle),
        ("online", online),
        ("static_biased", static_biased),
    ]:
        if data is None:
            continue
        for key in KEYS:
            summary[f"{label}_{key}"] = float(data["summary"][key])
        summary[f"{label}_failed_tenants"] = int(data["failed_tenants"])

    # static_autotuned vs static
    for key in KEYS:
        b_val = float(builtin["summary"][key])
        s_val = float(static["summary"][key])
        summary[f"builtin_vs_static_pct_{key}"] = pct(b_val, s_val)

    # online vs static_autotuned
    if online:
        for key in KEYS:
            o_val = float(online["summary"][key])
            b_val = float(builtin["summary"][key])
            summary[f"online_vs_builtin_pct_{key}"] = pct(o_val, b_val)

    # online vs static (standard comparison, for cross-reference)
    if online:
        for key in KEYS:
            o_val = float(online["summary"][key])
            s_val = float(static["summary"][key])
            summary[f"online_vs_static_pct_{key}"] = pct(o_val, s_val)

    # static_biased vs static_autotuned (optional, for completeness)
    if static_biased:
        for key in KEYS:
            sb_val = float(static_biased["summary"][key])
            b_val = float(builtin["summary"][key])
            summary[f"static_biased_vs_builtin_pct_{key}"] = pct(sb_val, b_val)

    # Overlap metrics. static_autotuned uses static-style equal allocation, so
    # its assigned high set is empty (or matches static's), giving overlap 0.
    builtin_overlap = compute_overlap(builtin["alloc"], builtin["tiers"])
    summary["builtin_high_overlap_mean_after_warmup"] = builtin_overlap["mean_overlap_after_warmup"]
    summary["builtin_overlap_checks"] = builtin_overlap["checks"]

    if online:
        online_overlap = compute_overlap(online["alloc"], online["tiers"])
        summary["online_high_overlap_mean_after_warmup"] = online_overlap["mean_overlap_after_warmup"]
        summary["online_high_exact_epochs_after_warmup"] = online_overlap["exact_epochs_after_warmup"]
        summary["online_overlap_checks"] = online_overlap["checks"]

    if oracle:
        oracle_overlap = compute_overlap(oracle["alloc"], oracle["tiers"])
        summary["oracle_high_overlap_mean_after_warmup"] = oracle_overlap["mean_overlap_after_warmup"]

    summary["tier_history"] = builtin["tiers"]

    payload = {"summary": summary, "trial": args.trial}
    out = results_dir / f"realistic_analysis_builtin_{args.trial}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
