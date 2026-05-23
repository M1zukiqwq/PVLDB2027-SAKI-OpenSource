#!/usr/bin/env python3
"""Analyze one Baleen static-only RocksDB smoke for Gate B.

The analyzer is deliberately single-policy: it reports only the static run and
does not make n=5 or policy-comparison claims.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import subprocess
from pathlib import Path


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (q / 100.0) * (len(vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def row_float(row: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def read_metrics(log_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for path in sorted(log_dir.glob("*_metrics.csv")):
        rows = []
        with path.open() as fh:
            for row in csv.DictReader(fh):
                rows.append(row)
        if rows:
            out[rows[0]["tenant"]] = rows
    return out


def git_diff_summary(root: Path) -> dict:
    try:
        stat = subprocess.check_output(["git", "-C", str(root), "diff", "--stat"], text=True, stderr=subprocess.DEVNULL)
    except Exception as exc:
        stat = f"unavailable: {exc}"
    try:
        short = subprocess.check_output(["git", "-C", str(root), "status", "--short"], text=True, stderr=subprocess.DEVNULL)
    except Exception as exc:
        short = f"unavailable: {exc}"
    return {"git_diff_stat": stat.strip(), "git_status_short": short.strip()}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--trial", required=True)
    p.add_argument("--schedule", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--warmup-windows", nargs="*", type=int, default=[0])
    p.add_argument("--engine-compact-mbps-gate", type=float, default=60.0)
    p.add_argument("--engine-pending-mib-gate", type=float, default=32.0)
    p.add_argument("--completion-ratio-gate", type=float, default=0.5)
    args = p.parse_args()
    args.root = args.root.expanduser().resolve()
    args.schedule = args.schedule.expanduser().resolve()
    args.out = args.out.expanduser().resolve()

    run_json = args.root / "results" / f"embedded_continuous_static_{args.trial}.json"
    log_dir = args.root / "logs" / f"embedded_continuous_static_{args.trial}"
    if not run_json.exists():
        raise SystemExit(f"missing run JSON {run_json}")
    run = json.loads(run_json.read_text())
    schedule = json.loads(args.schedule.read_text())
    metrics = read_metrics(log_dir)
    value_size = int(float(run.get("args", {}).get("value_size", 1024) or 1024))
    window_sec = int(float(run.get("args", {}).get("window_sec", 20) or 20))
    warmup = set(args.warmup_windows)

    rows_by_window: dict[int, list[dict]] = {}
    per_tenant = {}
    for tenant, rows in metrics.items():
        completed_ops = 0.0
        offered_ops = 0.0
        compact_bytes = 0.0
        rl_bytes = 0.0
        tenant_windows = 0
        for row in rows:
            w = int(row["window"])
            rows_by_window.setdefault(w, []).append(row)
            if w in warmup:
                continue
            tenant_windows += 1
            completed_ops += row_float(row, "write_ops")
            offered_ops += row_float(row, "write_qps_target") * window_sec
            compact_bytes += row_float(row, "compact_output_bytes")
            rl_bytes += row_float(row, "rate_limiter_bytes_delta")
        denom_sec = max(1.0, tenant_windows * window_sec)
        per_tenant[tenant] = {
            "completed_logical_write_mb_s": completed_ops * value_size / denom_sec / 1e6,
            "offered_write_mb_s": offered_ops * value_size / denom_sec / 1e6,
            "completion_ratio": completed_ops / max(1.0, offered_ops),
            "compact_output_mb_s": compact_bytes / denom_sec / 1e6,
            "rate_limiter_actual_mb_s": rl_bytes / denom_sec / 1e6,
            "windows_observed_excluding_warmup": tenant_windows,
        }

    compact_per_window = []
    offered_per_window = []
    completed_per_window = []
    completion_per_window = []
    limiter_per_window = []
    pending_values = []
    l0_values = []
    total_compact = 0.0
    total_completed_ops = 0.0
    total_offered_ops = 0.0
    total_rl_bytes = 0.0
    for w, rows in sorted(rows_by_window.items()):
        if w in warmup:
            continue
        compact = sum(row_float(r, "compact_output_bytes") for r in rows)
        completed_ops = sum(row_float(r, "write_ops") for r in rows)
        offered_ops = sum(row_float(r, "write_qps_target") for r in rows) * window_sec
        rl_bytes = sum(row_float(r, "rate_limiter_bytes_delta") for r in rows)
        compact_per_window.append(compact / window_sec / 1e6)
        offered_per_window.append(offered_ops * value_size / window_sec / 1e6)
        completed_per_window.append(completed_ops * value_size / window_sec / 1e6)
        completion_per_window.append(None if offered_ops <= 0.0 else completed_ops / offered_ops)
        limiter_per_window.append(rl_bytes / window_sec / 1e6)
        pending_values.extend(row_float(r, "pending_compaction_bytes") for r in rows)
        l0_values.extend(row_float(r, "l0_files") for r in rows)
        total_compact += compact
        total_completed_ops += completed_ops
        total_offered_ops += offered_ops
        total_rl_bytes += rl_bytes

    duration_sec = max(1.0, len(compact_per_window) * window_sec)
    offered = total_offered_ops * value_size / duration_sec / 1e6
    completed = total_completed_ops * value_size / duration_sec / 1e6
    limiter = total_rl_bytes / duration_sec / 1e6
    compact = total_compact / duration_sec / 1e6
    completion_ratio = None if total_offered_ops <= 0.0 else total_completed_ops / total_offered_ops
    pending_p95 = percentile(pending_values, 95)
    failed_tenants = int(run.get("summary", {}).get("failed_tenants", -1))
    engine_pass = compact >= args.engine_compact_mbps_gate or pending_p95 >= args.engine_pending_mib_gate * 1024 * 1024
    completion_pass = completion_ratio is not None and completion_ratio >= args.completion_ratio_gate
    correctness_pass = failed_tenants == 0

    seg = schedule["selected_segments"][0]
    final_rows_by_tenant = {r["tenant"]: r for r in run.get("final_rows", [])}
    per_tenant_failures = {
        tenant: {
            "exit_code": int(final_rows_by_tenant.get(tenant, {}).get("exit_code", -999)),
            "runtime_sec": float(final_rows_by_tenant.get(tenant, {}).get("runtime_sec", 0.0) or 0.0),
            "log": final_rows_by_tenant.get(tenant, {}).get("log"),
        }
        for tenant in sorted(metrics)
    }

    report = {
        "verdict": {
            "recommend_n5": bool(engine_pass and completion_pass and correctness_pass),
            "engine_stress_pass": bool(engine_pass),
            "completion_pass": bool(completion_pass),
            "correctness_pass": bool(correctness_pass),
            "reason_if_no_n5": (
                "pass_recommend_n5"
                if engine_pass and completion_pass and correctness_pass
                else ";".join(
                    reason for reason, ok in [
                        ("engine_stress_insufficient", engine_pass),
                        ("completion_ratio_insufficient", completion_pass),
                        ("failed_tenants_or_timeouts", correctness_pass),
                    ]
                    if not ok
                )
            ),
        },
        "config": {
            "policy": "static",
            "trial": args.trial,
            "run_json": str(run_json),
            "log_dir": str(log_dir),
            "schedule": str(args.schedule),
            "warmup_windows": sorted(warmup),
            "budget_contract": run.get("cachelib_external_metadata", {}).get("budget_contract"),
            "budget_contract_preserved": (
                run.get("cachelib_external_metadata", {}).get("budget_contract", {}).get("aggregate") == 112000000
            ),
            "actual_selected_segment_id": seg.get("segment_id") or seg.get("segment_index"),
            "actual_trace_id": seg.get("trace_id"),
            "actual_selected_users": seg.get("selected_user_ids") or seg.get("tenant_ids"),
        },
        "metrics": {
            "offered_write_mb_s": offered,
            "rate_limiter_actual_mb_s": limiter,
            "completed_logical_write_mb_s": completed,
            "compact_output_mb_s": compact,
            "pending_compaction_bytes_p95": pending_p95,
            "pending_compaction_bytes_p95_mib": pending_p95 / (1024 * 1024),
            "failed_tenants": failed_tenants,
            "completion_ratio": completion_ratio,
            "per_window": {
                "offered_write_mb_s": offered_per_window,
                "rate_limiter_actual_mb_s": limiter_per_window,
                "completed_logical_write_mb_s": completed_per_window,
                "compact_output_mb_s": compact_per_window,
                "completion_ratio": completion_per_window,
            },
            "l0_files": {
                "mean": statistics.fmean(l0_values) if l0_values else 0.0,
                "p95": percentile(l0_values, 95),
                "max": max(l0_values) if l0_values else 0.0,
            },
        },
        "per_tenant_completed_mb_s": per_tenant,
        "per_tenant_failures_timeouts": per_tenant_failures,
        "commands": {
            "tenant_commands": run.get("commands", {}),
            "driver_command": run.get("args", {}),
        },
        "changed_files_summary": git_diff_summary(args.root),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True))
    args.out.with_suffix(".md").write_text(render_md(report))
    print(json.dumps({
        "out": str(args.out),
        "md": str(args.out.with_suffix(".md")),
        "recommend_n5": report["verdict"]["recommend_n5"],
        "reason_if_no_n5": report["verdict"]["reason_if_no_n5"],
        "engine_stress_pass": engine_pass,
        "completion_pass": completion_pass,
        "correctness_pass": correctness_pass,
    }, indent=2))
    return 0


def render_md(report: dict) -> str:
    v = report["verdict"]
    m = report["metrics"]
    cfg = report["config"]
    lines = []
    lines.append("# Baleen Static-Only RocksDB Smoke\n")
    lines.append(f"- recommend_n5: **{v['recommend_n5']}**")
    lines.append(f"- reason_if_no_n5: `{v['reason_if_no_n5']}`")
    lines.append(f"- segment_id: `{cfg['actual_selected_segment_id']}`")
    lines.append(f"- trace_id: `{cfg['actual_trace_id']}`")
    lines.append(f"- budget_contract_preserved: {cfg['budget_contract_preserved']}")
    lines.append(f"- selected_users: {', '.join(cfg['actual_selected_users'])}")
    lines.append("")
    lines.append("## Gate B Metrics\n")
    lines.append("| metric | value | pass criterion |")
    lines.append("|---|---:|---|")
    lines.append(f"| offered_write_mb_s | {m['offered_write_mb_s']:.3f} | diagnostic |")
    lines.append(f"| rate_limiter_actual_mb_s | {m['rate_limiter_actual_mb_s']:.3f} | diagnostic |")
    lines.append(f"| completed_logical_write_mb_s | {m['completed_logical_write_mb_s']:.3f} | diagnostic |")
    lines.append(f"| compact_output_mb_s | {m['compact_output_mb_s']:.3f} | >= 60 OR pending p95 >= 32 MiB |")
    lines.append(f"| pending_compaction_bytes_p95_mib | {m['pending_compaction_bytes_p95_mib']:.3f} | >= 32 MiB OR compact >= 60 |")
    completion_ratio = m["completion_ratio"]
    completion_ratio_text = "null" if completion_ratio is None else f"{completion_ratio:.4f}"
    lines.append(f"| completion_ratio | {completion_ratio_text} | >= 0.5 |")
    lines.append(f"| failed_tenants | {m['failed_tenants']} | == 0 |")
    lines.append("")
    lines.append("## Pass Flags\n")
    lines.append(f"- engine_stress_pass: {v['engine_stress_pass']}")
    lines.append(f"- completion_pass: {v['completion_pass']}")
    lines.append(f"- correctness_pass: {v['correctness_pass']}")
    lines.append("")
    lines.append("## Per-Tenant Completed MB/s\n")
    lines.append("| tenant | completed | offered | completion | limiter | compact | exit |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    failures = report["per_tenant_failures_timeouts"]
    for tenant, row in sorted(report["per_tenant_completed_mb_s"].items()):
        lines.append(
            f"| {tenant} | {row['completed_logical_write_mb_s']:.3f} "
            f"| {row['offered_write_mb_s']:.3f} "
            f"| {row['completion_ratio']:.4f} "
            f"| {row['rate_limiter_actual_mb_s']:.3f} "
            f"| {row['compact_output_mb_s']:.3f} "
            f"| {failures.get(tenant, {}).get('exit_code')} |"
        )
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
