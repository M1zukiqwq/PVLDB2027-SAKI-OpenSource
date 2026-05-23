#!/usr/bin/env python3
from __future__ import annotations

import json
import argparse
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


parser = argparse.ArgumentParser()
parser.add_argument("--trial", default="realistic_a")
parser.add_argument(
    "--results-dir",
    type=Path,
    default=None,
    help="Directory holding realistic_<policy>_<trial>.json. Defaults to this repo's results/ or remote-results/.",
)
args = parser.parse_args()
results_dir = resolve_results_dir(args.results_dir, args.trial, ["static", "oracle_tiered", "online"])

files = [
    results_dir / f"realistic_static_{args.trial}.json",
    results_dir / f"realistic_oracle_tiered_{args.trial}.json",
    results_dir / f"realistic_online_{args.trial}.json",
]

rows = []
for path in files:
    payload = json.loads(path.read_text())
    rows.append(
        {
            "policy": payload["summary"]["policy"],
            "summary": payload["summary"],
            "epochs": payload["epoch_summaries"],
            "alloc": payload.get("alloc_history", []),
            "tiers": payload.get("tier_history", []),
        }
    )

by_policy = {row["policy"]: row for row in rows}
static = by_policy["static"]["summary"]
oracle = by_policy["oracle_tiered"]["summary"]
online = by_policy["online"]["summary"]
keys = [
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
summary: dict[str, object] = {}
for key in keys:
    s = float(static[key])
    o = float(oracle[key])
    n = float(online[key])
    summary[f"static_{key}"] = s
    summary[f"oracle_{key}"] = o
    summary[f"online_{key}"] = n
    summary[f"oracle_vs_static_pct_{key}"] = pct(o, s)
    summary[f"online_vs_static_pct_{key}"] = pct(n, s)
    summary[f"online_vs_oracle_pct_{key}"] = pct(n, o)

online_row = by_policy["online"]
alloc_checks = []
for epoch, alloc in enumerate(online_row["alloc"]):
    assigned = high_assigned(alloc)
    true_high = sorted(online_row["tiers"][epoch]["high"])
    alloc_checks.append(
        {
            "epoch": epoch,
            "true_high": true_high,
            "assigned_high": assigned,
            "overlap": overlap(assigned, true_high),
            "correct": assigned == true_high,
        }
    )
summary["online_high_alloc_history"] = alloc_checks
summary["online_high_overlap_mean_after_warmup"] = (
    sum(x["overlap"] for x in alloc_checks[1:]) / max(1, len(alloc_checks) - 1)
)
summary["online_high_exact_epochs_after_warmup"] = sum(1 for x in alloc_checks[1:] if x["correct"])

payload = {"summary": summary, "runs": rows}
out = results_dir / f"realistic_analysis_{args.trial}.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, indent=2, sort_keys=True))
print(json.dumps(summary, indent=2, sort_keys=True))
