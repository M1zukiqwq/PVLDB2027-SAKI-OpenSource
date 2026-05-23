#!/usr/bin/env python3
"""Analyze the RateLimiter coverage microstudy.

Sweeps a single per-tenant budget B for 4 identical tenants under the static
policy, n=3 seeds (a,b,c) per budget plus an optional negative control with
``--no-rocksdb-rate-limiter`` at B=6 MB/s (seeds a,b). Computes per-run and
per-budget aggregates of rate-limiter actual MB/s, compaction output MB/s,
flush output MB/s, total background MB/s, and a coverage ratio relating
rate-limiter byte deltas to flush+compaction output bytes. Also reports write
throughput, write P99, and pending-compaction backlog as sanity signals.

This is internal-validity / knob-coverage validation. It is not a SAKI policy
comparison and must not be used to claim a new main result.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_BUDGETS = [1_000_000, 3_000_000, 6_000_000, 11_000_000]
DEFAULT_SEEDS = ["a", "b", "c"]
DEFAULT_NEG_SEEDS = ["a", "b"]
DEFAULT_NEG_BUDGET = 6_000_000

TRIAL_PREFIX_DEFAULT = "rlcov"


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_run(results_dir: Path, trial: str) -> Optional[dict]:
    path = results_dir / f"embedded_continuous_static_{trial}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    payload["_trial"] = trial
    return payload


def per_run_metrics(payload: dict, drop_first_window: bool = True) -> dict:
    rows = payload.get("window_records", [])
    summary = payload.get("summary", {})
    window_sec = _f(summary.get("window_sec"), 0.0)
    tenant_count = int(_f(summary.get("tenant_count"), 0.0))

    if drop_first_window:
        kept = [r for r in rows if int(_f(r.get("window"), 0.0)) > 0]
    else:
        kept = list(rows)

    # Total tenant-seconds across kept rows.
    total_sec = sum(
        max(_f(r.get("elapsed_end_sec")) - _f(r.get("elapsed_start_sec")), 0.0)
        for r in kept
    )
    if total_sec <= 0.0 and window_sec > 0.0 and tenant_count > 0:
        retained_windows = len({int(_f(r.get("window"), 0.0)) for r in kept})
        total_sec = window_sec * retained_windows * tenant_count

    rl_bytes = sum(_f(r.get("rate_limiter_bytes_delta")) for r in kept)
    compact_bytes = sum(_f(r.get("compact_output_bytes")) for r in kept)
    flush_bytes = sum(_f(r.get("flush_output_bytes")) for r in kept)
    write_ops = sum(_f(r.get("write_ops")) for r in kept)
    # weighted write P99 by write ops
    wp99_num = sum(_f(r.get("write_p99_us")) * _f(r.get("write_ops")) for r in kept)
    wp99_den = sum(_f(r.get("write_ops")) for r in kept)
    write_p99_us = wp99_num / wp99_den if wp99_den > 0 else 0.0

    pending_vals = [_f(r.get("pending_compaction_bytes")) for r in kept]
    pending_mean = statistics.mean(pending_vals) if pending_vals else 0.0
    if len(pending_vals) >= 2:
        pending_p95 = statistics.quantiles(pending_vals, n=20, method="inclusive")[18]
    else:
        pending_p95 = pending_vals[0] if pending_vals else 0.0

    background_bytes = compact_bytes + flush_bytes
    coverage_ratio = rl_bytes / background_bytes if background_bytes > 1.0 else 0.0

    retained_windows = sorted({int(_f(r.get("window"), 0.0)) for r in kept})

    return {
        "trial": payload.get("_trial"),
        "path": payload.get("_path"),
        "failed_tenants": int(_f(summary.get("failed_tenants"), 0.0)),
        "tenant_count": tenant_count,
        "window_sec": window_sec,
        "windows_recorded": int(_f(summary.get("windows_recorded"), 0.0)),
        "windows_retained": len(retained_windows),
        "total_tenant_sec": total_sec,
        "rate_limiter_actual_mbps": rl_bytes / total_sec / 1e6 if total_sec else 0.0,
        "compact_output_mbps": compact_bytes / total_sec / 1e6 if total_sec else 0.0,
        "flush_output_mbps": flush_bytes / total_sec / 1e6 if total_sec else 0.0,
        "background_output_mbps": background_bytes / total_sec / 1e6 if total_sec else 0.0,
        "coverage_ratio": coverage_ratio,
        "write_throughput_ops_s": write_ops / total_sec if total_sec else 0.0,
        "write_p99_us": write_p99_us,
        "pending_compaction_bytes_mean": pending_mean,
        "pending_compaction_bytes_p95": pending_p95,
    }


def aggregate(runs: List[dict]) -> dict:
    if not runs:
        return {}
    keys = [
        "rate_limiter_actual_mbps",
        "compact_output_mbps",
        "flush_output_mbps",
        "background_output_mbps",
        "coverage_ratio",
        "write_throughput_ops_s",
        "write_p99_us",
        "pending_compaction_bytes_mean",
        "pending_compaction_bytes_p95",
    ]
    agg = {"n": len(runs)}
    for k in keys:
        vals = [r[k] for r in runs]
        agg[k + "_mean"] = statistics.mean(vals)
        agg[k + "_min"] = min(vals)
        agg[k + "_max"] = max(vals)
    agg["failed_tenants_total"] = sum(r["failed_tenants"] for r in runs)
    agg["windows_retained_min"] = min(r["windows_retained"] for r in runs)
    return agg


def fail_if_missing_runs(missing: List[str], negative_missing: List[str], allow_missing: bool) -> None:
    all_missing = missing + negative_missing
    if allow_missing or not all_missing:
        return
    print(
        "Refusing to write rate-limiter coverage summary: required raw runs are missing.",
        file=sys.stderr,
    )
    print(
        "Restore the full results directory, or pass --allow-missing for an explicit "
        "partial diagnostic.",
        file=sys.stderr,
    )
    for trial in missing:
        print(f"  missing sweep run: {trial}", file=sys.stderr)
    for trial in negative_missing:
        print(f"  missing negative-control run: {trial}", file=sys.stderr)
    raise SystemExit(2)


def fail_if_outputs_exist(paths: Iterable[Path], overwrite: bool) -> None:
    existing = [p for p in paths if p.exists()]
    if overwrite or not existing:
        return
    print("Refusing to overwrite existing rate-limiter coverage artifacts.", file=sys.stderr)
    print("Pass --overwrite after restoring the required raw run files.", file=sys.stderr)
    for path in existing:
        print(f"  exists: {path}", file=sys.stderr)
    raise SystemExit(3)


def monotonic_diagnostic(values: List[Tuple[float, float]]) -> dict:
    """Given [(budget, mean)] sorted ascending in budget, check non-decreasing."""
    ok = True
    pairs = []
    for (b1, v1), (b2, v2) in zip(values, values[1:]):
        delta = v2 - v1
        pairs.append({"from_mbps": b1, "to_mbps": b2, "delta_mbps": delta})
        if delta < 0:
            ok = False
    return {"monotonic_nondecreasing": ok, "pairwise": pairs}


def fmt_mbps(mean: float, lo: float, hi: float) -> str:
    return f"{mean:5.2f} [{lo:5.2f},{hi:5.2f}]"


def render_markdown(report: dict) -> str:
    lines: List[str] = []
    lines.append("# Rate-Limiter Coverage Microstudy")
    lines.append("")
    lines.append(
        "4 identical tenants, static policy, n=3 seeds per per-tenant budget; "
        "window 0 dropped. Numbers below are means across seeds with "
        "[min, max] in brackets. This sweep changes the per-tenant rate-limiter "
        "ceiling only; it is **not** a SAKI policy comparison and does not alter "
        "the 112 MB/s fixed-budget main results."
    )
    lines.append("")
    lines.append(
        "| Budget MB/s | n | Failed | Limiter actual MB/s | Compact MB/s | "
        "Flush MB/s | Background MB/s | Coverage | Write P99 ms | Pending p95 MiB |"
    )
    lines.append(
        "|---:|---:|---:|---|---|---|---|---:|---:|---:|"
    )
    for entry in report["sweep"]:
        b = entry["budget_mbps"]
        a = entry["aggregate"]
        if not a:
            lines.append(
                f"| {b:.1f} | 0 | n/a | (no runs) | | | | | | |"
            )
            continue
        lines.append(
            "| {b:.1f} | {n} | {f} | {rl} | {co} | {fl} | {bg} | {cov:.2f} "
            "[{cov_lo:.2f},{cov_hi:.2f}] | {p99:.2f} | {pend:.1f} |".format(
                b=b,
                n=a["n"],
                f=a["failed_tenants_total"],
                rl=fmt_mbps(a["rate_limiter_actual_mbps_mean"],
                            a["rate_limiter_actual_mbps_min"],
                            a["rate_limiter_actual_mbps_max"]),
                co=fmt_mbps(a["compact_output_mbps_mean"],
                            a["compact_output_mbps_min"],
                            a["compact_output_mbps_max"]),
                fl=fmt_mbps(a["flush_output_mbps_mean"],
                            a["flush_output_mbps_min"],
                            a["flush_output_mbps_max"]),
                bg=fmt_mbps(a["background_output_mbps_mean"],
                            a["background_output_mbps_min"],
                            a["background_output_mbps_max"]),
                cov=a["coverage_ratio_mean"],
                cov_lo=a["coverage_ratio_min"],
                cov_hi=a["coverage_ratio_max"],
                p99=a["write_p99_us_mean"] / 1000.0,
                pend=a["pending_compaction_bytes_p95_mean"] / (1024.0 * 1024.0),
            )
        )

    lines.append("")
    diag = report.get("monotonic_diagnostics", {})
    lines.append("## Monotonicity diagnostics")
    lines.append("")
    lines.append(
        f"- Rate-limiter actual MB/s mean non-decreasing with budget: "
        f"**{diag.get('rate_limiter_actual_mbps', {}).get('monotonic_nondecreasing')}**"
    )
    lines.append(
        f"- Background (flush+compact) MB/s mean non-decreasing with budget: "
        f"**{diag.get('background_output_mbps', {}).get('monotonic_nondecreasing')}**"
    )
    lines.append(
        f"- Zero failed tenants across all sweep runs: "
        f"**{report['sweep_zero_failed']}**"
    )

    if report.get("negative_control"):
        lines.append("")
        lines.append("## Negative control: --no-rocksdb-rate-limiter at 6 MB/s")
        lines.append("")
        nc = report["negative_control"]["aggregate"]
        if nc:
            lines.append(
                "| n | Failed | Limiter actual MB/s | Compact MB/s | "
                "Flush MB/s | Background MB/s | Write P99 ms |"
            )
            lines.append("|---:|---:|---|---|---|---|---:|")
            lines.append(
                "| {n} | {f} | {rl} | {co} | {fl} | {bg} | {p99:.2f} |".format(
                    n=nc["n"],
                    f=nc["failed_tenants_total"],
                    rl=fmt_mbps(nc["rate_limiter_actual_mbps_mean"],
                                nc["rate_limiter_actual_mbps_min"],
                                nc["rate_limiter_actual_mbps_max"]),
                    co=fmt_mbps(nc["compact_output_mbps_mean"],
                                nc["compact_output_mbps_min"],
                                nc["compact_output_mbps_max"]),
                    fl=fmt_mbps(nc["flush_output_mbps_mean"],
                                nc["flush_output_mbps_min"],
                                nc["flush_output_mbps_max"]),
                    bg=fmt_mbps(nc["background_output_mbps_mean"],
                                nc["background_output_mbps_min"],
                                nc["background_output_mbps_max"]),
                    p99=nc["write_p99_us_mean"] / 1000.0,
                )
            )
            lines.append("")
            lines.append(
                "Negative control runs are a sanity check only (n=2); they are "
                "not a SAKI baseline."
            )

    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "This validates that the runtime actuation path exercises RocksDB "
        "background maintenance I/O; it is not a SAKI policy comparison and "
        "does not alter the fixed-budget main experiments."
    )
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repo root or remote experiment root. Default: parent of this script.",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Directory containing embedded_continuous_static_<trial>.json files. "
        "Defaults to <root>/remote-results or <root>/results, whichever exists.",
    )
    parser.add_argument(
        "--trial-prefix",
        default=TRIAL_PREFIX_DEFAULT,
        help="Trial prefix; trial names are <prefix>_<budget>_<seed>. Default 'rlcov'.",
    )
    parser.add_argument(
        "--budgets-bytes",
        type=int,
        nargs="+",
        default=DEFAULT_BUDGETS,
        help="Per-tenant budgets in bytes/s.",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        default=DEFAULT_SEEDS,
    )
    parser.add_argument(
        "--negative-control-seeds",
        nargs="+",
        default=DEFAULT_NEG_SEEDS,
    )
    parser.add_argument(
        "--negative-control-budget-bytes",
        type=int,
        default=DEFAULT_NEG_BUDGET,
    )
    parser.add_argument("--include-negative-control", action="store_true")
    parser.add_argument(
        "--out-json",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--out-md",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing existing rate_limiter_coverage artifacts.",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Allow writing a partial diagnostic when one or more expected raw runs are missing.",
    )
    return parser.parse_args()


def resolve_results_dir(root: Path, override: Optional[Path]) -> Path:
    if override is not None:
        return override
    cand1 = root / "remote-results"
    cand2 = root / "results"
    if cand1.exists():
        return cand1
    if cand2.exists():
        return cand2
    return cand1


def main() -> int:
    args = parse_args()
    root: Path = args.root
    results_dir = resolve_results_dir(root, args.results_dir)
    tables_dir = root / "remote-results" / "paper_tables"
    if (root / "remote-results").exists():
        tables_dir = root / "remote-results" / "paper_tables"
    else:
        tables_dir = root / "results" / "paper_tables"
    out_json = args.out_json or (tables_dir / "rate_limiter_coverage.json")
    out_md = args.out_md or (tables_dir / "rate_limiter_coverage.md")

    sweep_entries = []
    missing: List[str] = []
    negative_missing: List[str] = []
    sweep_zero_failed = True

    for budget in args.budgets_bytes:
        runs: List[dict] = []
        for seed in args.seeds:
            trial = f"{args.trial_prefix}_{budget}_{seed}"
            payload = load_run(results_dir, trial)
            if payload is None:
                missing.append(trial)
                continue
            runs.append(per_run_metrics(payload))
        agg = aggregate(runs)
        if agg and agg["failed_tenants_total"] > 0:
            sweep_zero_failed = False
        sweep_entries.append({
            "budget_bytes": budget,
            "budget_mbps": budget / 1e6,
            "runs": runs,
            "aggregate": agg,
        })

    # Monotonic diagnostics across budgets (sorted ascending).
    by_budget = sorted(
        [(e["budget_mbps"], e["aggregate"]) for e in sweep_entries if e["aggregate"]],
        key=lambda x: x[0],
    )
    def _seq(field: str):
        return [(b, a[field + "_mean"]) for b, a in by_budget]
    diagnostics = {
        "rate_limiter_actual_mbps": monotonic_diagnostic(_seq("rate_limiter_actual_mbps"))
        if by_budget else {},
        "background_output_mbps": monotonic_diagnostic(_seq("background_output_mbps"))
        if by_budget else {},
    }

    negative_control = None
    if args.include_negative_control:
        nc_runs: List[dict] = []
        budget = args.negative_control_budget_bytes
        for seed in args.negative_control_seeds:
            trial = f"{args.trial_prefix}_norl_{budget}_{seed}"
            payload = load_run(results_dir, trial)
            if payload is None:
                negative_missing.append(trial)
                continue
            nc_runs.append(per_run_metrics(payload))
        negative_control = {
            "budget_bytes": budget,
            "budget_mbps": budget / 1e6,
            "runs": nc_runs,
            "missing": negative_missing,
            "aggregate": aggregate(nc_runs),
        }

    fail_if_missing_runs(missing, negative_missing, args.allow_missing)
    fail_if_outputs_exist([out_json, out_md], args.overwrite)

    report = {
        "design": {
            "policy": "static",
            "tenant_count": 4,
            "drift_tenants": 2,
            "high_count": 1,
            "low_count": 1,
            "duration_sec": 120,
            "window_sec": 20,
            "warmup_dropped_window": 0,
            "offered_workload": {
                "high_write_qps": 2400,
                "mid_write_qps": 2400,
                "low_write_qps": 2400,
                "high_read_qps": 0,
                "mid_read_qps": 0,
                "low_read_qps": 0,
                "high_hot_frac": 0.18,
                "mid_hot_frac": 0.18,
                "low_hot_frac": 0.18,
            },
            "rocksdb_stress_knobs": {
                "num_keys": 80000,
                "prefill_keys": 80000,
                "value_size": 1024,
                "write_buffer_size": 2097152,
                "max_write_buffer_number": 3,
                "l0_compact_trigger": 2,
                "l0_slowdown_trigger": 5,
                "l0_stop_trigger": 9,
                "target_file_size_base": 2097152,
                "max_background_jobs": 2,
            },
            "note": (
                "Sweep changes the per-tenant rate-limiter budget. Total host "
                "budget therefore varies with the sweep point; this is knob "
                "coverage validation, not a SAKI fixed-budget comparison."
            ),
        },
        "results_dir": str(results_dir),
        "missing_runs": missing,
        "sweep": sweep_entries,
        "sweep_zero_failed": bool(sweep_zero_failed),
        "monotonic_diagnostics": diagnostics,
        "negative_control": negative_control,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    out_md.write_text(render_markdown(report), encoding="utf-8")

    print(f"Wrote {out_json}")
    print(f"Wrote {out_md}")
    if missing:
        print("Missing sweep runs:")
        for t in missing:
            print(f"  {t}")
    if negative_control and negative_control["missing"]:
        print("Missing negative-control runs:")
        for t in negative_control["missing"]:
            print(f"  {t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
