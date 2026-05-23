#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0 if old else 0.0


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def weighted_mean(rows: list[dict], value: str, weight: str) -> float:
    num = sum(float(r.get(value, 0.0)) * float(r.get(weight, 0.0)) for r in rows)
    den = sum(float(r.get(weight, 0.0)) for r in rows)
    return num / den if den else mean([float(r.get(value, 0.0)) for r in rows])


def load(results_dir: Path, policy: str, trial: str) -> dict:
    path = results_dir / f"embedded_continuous_{policy}_{trial}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["_path"] = str(path)
    return payload


def rows(payload: dict) -> list[dict]:
    return payload.get("window_records", [])


def true_high(payload: dict, window: int) -> list[str]:
    hist = payload.get("allocation_history", [])
    if 0 <= window < len(hist):
        return sorted(hist[window]["tiers"]["high"])
    return []


def assigned_high(payload: dict, window: int) -> list[str]:
    hist = payload.get("allocation_history", [])
    if 0 <= window < len(hist):
        return sorted(
            name
            for name, alloc in hist[window]["allocation"].items()
            if alloc.get("assigned_tier") == "high"
        )
    return []


def overlap(a: list[str], b: list[str]) -> int:
    return len(set(a) & set(b))


def policy_metrics(payload: dict) -> dict[str, object]:
    all_rows = rows(payload)
    high_rows = [r for r in all_rows if r.get("true_tier") == "high"]
    total_sec = float(payload["summary"].get("duration_sec", 0.0))
    high_write_ops = sum(float(r.get("write_ops", 0.0)) for r in high_rows)
    total_write_ops = sum(float(r.get("write_ops", 0.0)) for r in all_rows)
    total_ops = sum(float(r.get("write_ops", 0.0)) + float(r.get("read_ops", 0.0)) for r in all_rows)
    high_ops = sum(float(r.get("write_ops", 0.0)) + float(r.get("read_ops", 0.0)) for r in high_rows)
    series = window_series(payload)
    compact_output_bytes = sum(float(r.get("compact_output_bytes", 0.0)) for r in all_rows)
    high_compact_output_bytes = sum(float(r.get("compact_output_bytes", 0.0)) for r in high_rows)
    return {
        "failed_tenants": float(payload["summary"].get("failed_tenants", 0.0)),
        "windows_recorded": float(payload["summary"].get("windows_recorded", 0.0)),
        "high_write_p99_us": weighted_mean(high_rows, "write_p99_us", "write_ops"),
        "high_write_p999_us": weighted_mean(high_rows, "write_p999_us", "write_ops"),
        "high_write_mean_us": weighted_mean(high_rows, "write_mean_us", "write_ops"),
        "high_write_throughput": high_write_ops / total_sec if total_sec else 0.0,
        "high_total_throughput": high_ops / total_sec if total_sec else 0.0,
        "total_write_throughput": total_write_ops / total_sec if total_sec else 0.0,
        "total_throughput": total_ops / total_sec if total_sec else 0.0,
        "compact_output_bytes": compact_output_bytes,
        "high_compact_output_bytes": high_compact_output_bytes,
        "compact_output_bytes_per_write": compact_output_bytes / total_write_ops if total_write_ops else 0.0,
        "high_compact_output_bytes_per_write": high_compact_output_bytes / high_write_ops if high_write_ops else 0.0,
        "flush_output_bytes": sum(float(r.get("flush_output_bytes", 0.0)) for r in all_rows),
        "high_flush_output_bytes": sum(float(r.get("flush_output_bytes", 0.0)) for r in high_rows),
        "mean_pending_compaction_bytes": mean([float(r.get("pending_compaction_bytes", 0.0)) for r in all_rows]),
        "mean_high_pending_compaction_bytes": mean([float(r.get("pending_compaction_bytes", 0.0)) for r in high_rows]),
        "mean_high_overlap_after_warmup": mean([float(r["high_overlap"]) for r in series[1:]]),
        "exact_high_windows_after_warmup": sum(1 for r in series[1:] if r["true_high"] == r["assigned_high"]),
        "adaptation_lag": adaptation_lag(series),
    }


def window_series(payload: dict) -> list[dict]:
    by_window: dict[int, list[dict]] = {}
    for r in rows(payload):
        by_window.setdefault(int(r["window"]), []).append(r)
    out = []
    for w in sorted(by_window):
        wr = by_window[w]
        high_names = true_high(payload, w)
        assigned = assigned_high(payload, w)
        high_set = set(high_names)
        high_rows = [r for r in wr if r.get("tenant") in high_set]
        out.append(
            {
                "window": w,
                "true_high": high_names,
                "assigned_high": assigned,
                "high_overlap": overlap(high_names, assigned),
                "high_write_ops": sum(float(r.get("write_ops", 0.0)) for r in high_rows),
                "total_write_ops": sum(float(r.get("write_ops", 0.0)) for r in wr),
                "high_write_p99_us": weighted_mean(high_rows, "write_p99_us", "write_ops"),
                "high_write_p999_us": weighted_mean(high_rows, "write_p999_us", "write_ops"),
                "high_compact_output_bytes": sum(float(r.get("compact_output_bytes", 0.0)) for r in high_rows),
                "total_compact_output_bytes": sum(float(r.get("compact_output_bytes", 0.0)) for r in wr),
                "alive_tenants": sum(1 for r in wr if int(r.get("process_exit_code", 1) or 1) == 0),
                "high_budget": mean([float(r.get("budget", 0.0)) for r in high_rows]),
                "total_budget": sum(float(r.get("budget", 0.0)) for r in wr),
            }
        )
    return out


def adaptation_lag(series: list[dict]) -> dict[str, object]:
    changes = []
    prev = None
    for row in series:
        cur = row["true_high"]
        if prev is not None and cur != prev:
            changes.append(row["window"])
        prev = cur
    lags = []
    for change in changes:
        lag = None
        for row in series:
            if row["window"] < change:
                continue
            threshold = max(1, len(row["true_high"]) - 1)
            if row["high_overlap"] >= threshold:
                lag = row["window"] - change
                break
        lags.append(lag)
    finite = [x for x in lags if x is not None]
    return {"change_windows": changes, "lags_windows": lags, "max_lag_windows": max(finite) if finite else None}


def compare(new: dict[str, object], old: dict[str, object], prefix: str) -> dict[str, float]:
    out = {}
    for key in [
        "high_write_p99_us",
        "high_write_p999_us",
        "high_write_throughput",
        "high_total_throughput",
        "total_throughput",
        "compact_output_bytes",
        "high_compact_output_bytes",
        "mean_high_pending_compaction_bytes",
        "compact_output_bytes_per_write",
        "high_compact_output_bytes_per_write",
    ]:
        out[f"{prefix}_pct_{key}"] = pct(float(new.get(key, 0.0)), float(old.get(key, 0.0)))
    return out


def gate(summary: dict[str, object], policies: list[str]) -> dict[str, object]:
    per = summary["per_policy"]
    verdict: dict[str, object] = {}
    oracle_policy = "oracle_tiered" if "oracle_tiered" in policies else ("oracle_drain" if "oracle_drain" in policies else None)
    if "static" in policies and oracle_policy is not None:
        s = per["static"]
        o = per[oracle_policy]
        oracle_cmp = summary["oracle_vs_static"]
        bytes_ok = float(oracle_cmp["oracle_vs_static_pct_compact_output_bytes"]) <= -10.0
        tail_ok = float(oracle_cmp["oracle_vs_static_pct_high_write_p99_us"]) <= -10.0 or float(
            oracle_cmp["oracle_vs_static_pct_high_write_p999_us"]
        ) <= -10.0
        throughput_ok = float(oracle_cmp["oracle_vs_static_pct_high_write_throughput"]) >= 10.0
        live_ok = float(o["failed_tenants"]) == 0.0 and float(s["failed_tenants"]) == 0.0
        verdict["oracle_validity_gate"] = {
            "pass": bool(live_ok and bytes_ok and (tail_ok or throughput_ok)),
            "bytes_ok": bool(bytes_ok),
            "tail_ok": bool(tail_ok),
            "throughput_ok": bool(throughput_ok),
            "liveness_ok": bool(live_ok),
            "oracle_policy": oracle_policy,
            "rule": "Pass only if the dynamic oracle vs static lowers compaction output bytes by >=10% and clearly improves high-tier tail or throughput, with no failed tenants.",
        }
    if "static" in policies and "online" in policies:
        online_cmp = summary["online_vs_static"]
        o = per["online"]
        p99_ok = float(online_cmp["online_vs_static_pct_high_write_p99_us"]) <= -15.0
        tput_ok = float(online_cmp["online_vs_static_pct_high_write_throughput"]) >= 20.0
        bytes_ok = float(online_cmp["online_vs_static_pct_compact_output_bytes"]) < 0.0
        total_ok = float(online_cmp["online_vs_static_pct_total_throughput"]) >= -5.0
        lag_info = o["adaptation_lag"]
        lags = lag_info.get("lags_windows", [])
        lag = lag_info.get("max_lag_windows")
        lag_ok = bool(lags) and all(x is not None and int(x) <= 2 for x in lags)
        live_ok = float(o["failed_tenants"]) == 0.0
        verdict["online_success_gate"] = {
            "pass": bool(live_ok and (p99_ok or tput_ok) and bytes_ok and total_ok and lag_ok),
            "p99_ok": bool(p99_ok),
            "throughput_ok": bool(tput_ok),
            "bytes_ok": bool(bytes_ok),
            "total_throughput_ok": bool(total_ok),
            "adaptation_ok": bool(lag_ok),
            "liveness_ok": bool(live_ok),
            "rule": "Pass if online improves high-tier P99 >=15% or high-tier write throughput >=20%, lowers compaction bytes, keeps total throughput loss ideally <5%, and converges within 1-2 windows.",
        }
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--results-dir", type=Path, default=None)
    parser.add_argument("--trial", required=True)
    parser.add_argument(
        "--policies",
        nargs="+",
        default=["static", "oracle_tiered", "online", "unlimited"],
        choices=["static", "oracle_tiered", "oracle_drain", "online", "unlimited", "static_biased"],
    )
    args = parser.parse_args()
    results_dir = args.results_dir or (args.root / "results")
    out = results_dir / f"embedded_continuous_analysis_{args.trial}.json"
    if out.exists():
        out.unlink()

    runs = {policy: load(results_dir, policy, args.trial) for policy in args.policies}
    summary: dict[str, object] = {
        "trial": args.trial,
        "policies": args.policies,
        "notes": {
            "continuous_processes": "Each tenant is one long-running embedded RocksDB process for the workload phase.",
            "runtime_control": "Workload mix/rate/hot range and budget files are changed while tenants continue running.",
            "actuation": "By default the tenant harness changes RocksDB RateLimiter::SetBytesPerSecond while the process runs; the LD_PRELOAD userspace write throttle is optional diagnostic machinery. max_background_jobs remains process-launch fixed.",
            "latency": "Per-window write P99/P999 are measured by the custom harness, not stock db_bench whole-run output.",
        },
    }
    per = {policy: policy_metrics(payload) for policy, payload in runs.items()}
    summary["per_policy"] = per
    oracle_policy = None
    for candidate in ["oracle_tiered", "oracle_drain"]:
        if candidate in per:
            oracle_policy = candidate
            break
    if "static" in per and oracle_policy is not None:
        summary["oracle_vs_static"] = compare(per[oracle_policy], per["static"], "oracle_vs_static")
    if "static" in per and "online" in per:
        summary["online_vs_static"] = compare(per["online"], per["static"], "online_vs_static")
    if "oracle_tiered" in per and "online" in per:
        summary["online_vs_oracle"] = compare(per["online"], per["oracle_tiered"], "online_vs_oracle")
    summary["window_series"] = {policy: window_series(payload) for policy, payload in runs.items()}
    summary["gate"] = gate(summary, args.policies)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary["per_policy"], indent=2, sort_keys=True))
    if "oracle_vs_static" in summary:
        print(json.dumps(summary["oracle_vs_static"], indent=2, sort_keys=True))
    if "online_vs_static" in summary:
        print(json.dumps(summary["online_vs_static"], indent=2, sort_keys=True))
    print(json.dumps(summary["gate"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
