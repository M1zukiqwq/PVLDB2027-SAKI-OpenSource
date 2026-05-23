#!/usr/bin/env python3
"""Generate paper-ready tables from experiment results.

Produces structured JSON and markdown tables covering:
  - Main results (epoch-level realistic_big, continuous demand2f)
  - Fairness / collateral-damage (per-tier breakdown)
  - Supplementary (ablation, longer run, static_biased)
  - Aggregate statistics with mean/stdev/range

Usage:
  python3 remote/generate_paper_tables.py
  python3 remote/generate_paper_tables.py --output-dir remote-results/paper_tables
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "remote-results"
OUTPUT_NAMES = [
    "main_continuous_demand2f.json",
    "fairness_continuous_demand2f.json",
    "fairness_epoch_realistic_big.json",
    "aggregate_realistic_big.json",
    "ablation_score_modes.json",
    "longer_run_confirmation.json",
    "static_biased_comparison.json",
    "paper_tables.md",
]

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

# ── helpers ──────────────────────────────────────────────────────────────

def pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0 if old else 0.0


def fmt_pct(val: float) -> str:
    return f"{val:+.1f}%"


def ci95_half_width(stat: dict) -> float | None:
    n = int(stat.get("n", 0))
    if n < 2:
        return None
    return T_CRIT_975.get(n, 1.96) * float(stat.get("stdev", 0.0)) / math.sqrt(n)



def weighted_mean(rows: list[dict], value: str, weight: str) -> float:
    num = sum(float(r.get(value, 0.0)) * float(r.get(weight, 0.0)) for r in rows)
    den = sum(float(r.get(weight, 0.0)) for r in rows)
    return num / den if den else 0.0


def simple_mean(rows: list[dict], key: str) -> float:
    vals = [float(r.get(key, 0.0)) for r in rows]
    return statistics.fmean(vals) if vals else 0.0


def sum_float(rows: list[dict], key: str) -> float:
    return sum(float(r.get(key, 0.0)) for r in rows)


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.stdev(values) if len(values) > 1 else 0.0,
    }


# ── continuous demand2f aggregate ───────────────────────────────────────

DEMAND2F_TRIALS = [
    "embedded_demand2f_16t",
    "embedded_demand2f_16t_b",
    "embedded_demand2f_16t_c",
    "embedded_demand2f_16t_d",
    "embedded_demand2f_16t_e",
]

COMPARE_METRICS = [
    ("online_vs_static_pct_high_write_p99_us", "High P99"),
    ("online_vs_static_pct_high_write_p999_us", "High P999"),
    ("online_vs_static_pct_high_write_throughput", "High write tput"),
    ("online_vs_static_pct_total_throughput", "Total tput"),
    ("online_vs_static_pct_compact_output_bytes", "Compact bytes"),
    ("online_vs_static_pct_compact_output_bytes_per_write", "Bytes/write"),
]


def load_analysis(trial: str) -> dict | None:
    path = RESULTS / f"embedded_continuous_analysis_{trial}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def required_input_paths() -> list[Path]:
    """Raw/analysis inputs needed to regenerate the paper tables exactly.

    The public submission intentionally keeps only compact summary artifacts.
    This generator is for the restored full results directory; without these
    inputs it must fail before touching checked-in summary files.
    """
    paths: list[Path] = []
    paths.extend(RESULTS / f"embedded_continuous_analysis_{trial}.json" for trial in DEMAND2F_TRIALS)
    paths.extend(
        RESULTS / f"embedded_continuous_{policy}_{trial}.json"
        for trial in DEMAND2F_TRIALS
        for policy in ("static", "online")
    )
    paths.append(RESULTS / "embedded_continuous_static_biased_embedded_demand2f_16t.json")
    paths.extend(RESULTS / f"realistic_analysis_realistic_big_{suffix}.json" for suffix in ("a", "b", "c", "d", "e"))
    paths.extend(
        [
            RESULTS / "embedded_continuous_analysis_embedded_demand2f_16t_ablation_a.json",
            RESULTS / "embedded_continuous_analysis_embedded_demand2f_16t_ablation_a_hybrid.json",
            RESULTS / "embedded_continuous_analysis_embedded_demand2f_16t_long_a.json",
            RESULTS / "embedded_continuous_analysis_embedded_demand2f_16t_with_baseline.json",
            RESULTS / "paper_tables" / "continuous_read_cpu_collateral.md",
            RESULTS / "paper_tables" / "realistic_big_a_aggregate.json",
            RESULTS / "paper_tables" / "realistic_big_a_aggregate.md",
            RESULTS / "paper_tables" / "realistic_big_builtin_aggregate.md",
        ]
    )
    return paths


def fail_if_missing_inputs() -> None:
    missing = [p for p in required_input_paths() if not p.exists()]
    if not missing:
        return
    print(
        "Refusing to regenerate paper tables: the compact public artifact omits "
        "the raw/analysis inputs required by this script.",
        file=sys.stderr,
    )
    print("Restore the full remote-results directory, or use the checked-in summaries.", file=sys.stderr)
    for path in missing[:40]:
        print(f"  missing: {path}", file=sys.stderr)
    if len(missing) > 40:
        print(f"  ... and {len(missing) - 40} more", file=sys.stderr)
    raise SystemExit(2)


def fail_if_outputs_exist(outdir: Path, overwrite: bool) -> None:
    existing = [outdir / name for name in OUTPUT_NAMES if (outdir / name).exists()]
    if overwrite or not existing:
        return
    print("Refusing to overwrite existing paper-table artifacts.", file=sys.stderr)
    print("Pass --overwrite after restoring the full raw/analysis inputs.", file=sys.stderr)
    for path in existing:
        print(f"  exists: {path}", file=sys.stderr)
    raise SystemExit(3)


def aggregate_demand2f() -> dict:
    per_trial = []
    for trial in DEMAND2F_TRIALS:
        data = load_analysis(trial)
        if data is None:
            continue
        cmp = data.get("online_vs_static", {})
        online = data.get("per_policy", {}).get("online", {})
        lag = online.get("adaptation_lag", {}) or {}
        lags = lag.get("lags_windows") or []
        per_trial.append({
            "trial": trial,
            "compare": {k: float(cmp.get(k, 0.0)) for k, _ in COMPARE_METRICS},
            "mean_high_overlap_after_warmup": float(online.get("mean_high_overlap_after_warmup", 0.0)),
            "failed_tenants": int(online.get("failed_tenants", 0)),
            "adaptation_max_lag": lag.get("max_lag_windows"),
            "gate_pass": bool(data.get("gate", {}).get("online_success_gate", {}).get("pass", False)),
        })

    if not per_trial:
        return {"error": "no demand2f analysis files found"}

    aggregates = {}
    for key, label in COMPARE_METRICS:
        vals = [t["compare"][key] for t in per_trial]
        aggregates[key] = summarize(vals)
    aggregates["mean_high_overlap_after_warmup"] = summarize(
        [t["mean_high_overlap_after_warmup"] for t in per_trial]
    )
    finite_lags = [t["adaptation_max_lag"] for t in per_trial if t["adaptation_max_lag"] is not None]
    aggregates["adaptation_max_lag"] = summarize([float(x) for x in finite_lags])
    aggregates["gate_pass_count"] = sum(1 for t in per_trial if t["gate_pass"])
    aggregates["failed_tenants_total"] = sum(t["failed_tenants"] for t in per_trial)
    aggregates["trial_count"] = len(per_trial)

    return {"per_trial": per_trial, "aggregates": aggregates}


# ── continuous per-tier fairness ────────────────────────────────────────

POLICY_FILE_PREFIX = {
    "static": "embedded_continuous_static_",
    "online": "embedded_continuous_online_",
    "static_biased": "embedded_continuous_static_biased_",
    "unlimited": "embedded_continuous_unlimited_",
}


def load_raw(policy: str, trial: str) -> dict | None:
    prefix = POLICY_FILE_PREFIX.get(policy)
    if not prefix:
        return None
    path = RESULTS / f"{prefix}{trial}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def tier_rows(payload: dict, tier: str) -> list[dict]:
    return [r for r in payload["window_records"] if r.get("true_tier") == tier]


def tier_metrics(payload: dict, tier: str) -> dict:
    if tier == "all":
        rows = payload["window_records"]
    else:
        rows = tier_rows(payload, tier)
    total_sec = float(payload["summary"].get("duration_sec", 0.0))
    write_ops = sum_float(rows, "write_ops")
    read_ops = sum_float(rows, "read_ops")
    compact_bytes = sum_float(rows, "compact_output_bytes")
    return {
        "write_p99_us": weighted_mean(rows, "write_p99_us", "write_ops"),
        "write_p999_us": weighted_mean(rows, "write_p999_us", "write_ops"),
        "write_throughput": write_ops / total_sec if total_sec else 0.0,
        "total_throughput": (write_ops + read_ops) / total_sec if total_sec else 0.0,
        "write_ops": write_ops,
        "compact_output_bytes": compact_bytes,
        "compact_bytes_per_write": compact_bytes / write_ops if write_ops else 0.0,
    }


def fairness_for_trial(trial: str) -> dict | None:
    raws = {}
    for pol in ["static", "online", "static_biased"]:
        data = load_raw(pol, trial)
        if data:
            raws[pol] = data
    if "static" not in raws or "online" not in raws:
        return None

    per_tier = {}
    for pol, data in raws.items():
        per_tier[pol] = {t: tier_metrics(data, t) for t in ["high", "mid", "low", "all"]}

    deltas = {}
    for pol in per_tier:
        if pol == "static":
            continue
        pol_d = {}
        for tier in ["high", "mid", "low", "all"]:
            s = per_tier["static"][tier]
            p = per_tier[pol][tier]
            pol_d[tier] = {
                "write_p99_pct": pct(p["write_p99_us"], s["write_p99_us"]),
                "write_p999_pct": pct(p["write_p999_us"], s["write_p999_us"]),
                "write_throughput_pct": pct(p["write_throughput"], s["write_throughput"]),
                "total_throughput_pct": pct(p["total_throughput"], s["total_throughput"]),
                "compact_bytes_pct": pct(p["compact_output_bytes"], s["compact_output_bytes"]),
                "compact_bytes_per_write_pct": pct(p["compact_bytes_per_write"], s["compact_bytes_per_write"]),
            }
        deltas[pol] = pol_d

    # online vs static_biased
    online_vs_sb = {}
    if "static_biased" in per_tier:
        sb = per_tier["static_biased"]
        on = per_tier["online"]
        for tier in ["high", "mid", "low", "all"]:
            s = sb[tier]
            p = on[tier]
            online_vs_sb[tier] = {
                "write_p99_pct": pct(p["write_p99_us"], s["write_p99_us"]),
                "write_throughput_pct": pct(p["write_throughput"], s["write_throughput"]),
                "compact_bytes_per_write_pct": pct(p["compact_bytes_per_write"], s["compact_bytes_per_write"]),
            }

    return {"trial": trial, "per_tier": per_tier, "deltas_vs_static": deltas, "online_vs_static_biased": online_vs_sb}


def aggregate_fairness(results: list[dict]) -> dict:
    agg = {}
    for tier in ["high", "mid", "low", "all"]:
        agg[tier] = {}
        for metric in ["write_p99_pct", "write_p999_pct", "write_throughput_pct",
                        "total_throughput_pct", "compact_bytes_pct", "compact_bytes_per_write_pct"]:
            vals = []
            for r in results:
                d = r.get("deltas_vs_static", {}).get("online", {}).get(tier, {})
                if metric in d:
                    vals.append(d[metric])
            if vals:
                agg[tier][metric] = summarize(vals)
    return agg


# ── epoch-level realistic_big ────────────────────────────────────────────

def load_realistic_analysis(trial: str) -> dict | None:
    path = RESULTS / f"realistic_analysis_{trial}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def epoch_fairness(analysis: dict) -> dict:
    s = analysis.get("summary", {})
    tiers = {}
    for pol in ["static", "oracle", "online"]:
        tiers[pol] = {}
        for tier in ["high", "mid", "low"]:
            p99 = s.get(f"{pol}_{tier}_mean_write_p99_us")
            tput = s.get(f"{pol}_{tier}_sum_ops_per_sec")
            if p99 is not None:
                tiers[pol][tier] = {"p99_us": p99, "tput": tput}
        # Total
        p99 = s.get(f"{pol}_mean_write_p99_us")
        tput = s.get(f"{pol}_sum_ops_per_sec")
        compact = s.get(f"{pol}_sum_compact_write_bytes")
        if p99 is not None:
            tiers[pol]["all"] = {"p99_us": p99, "tput": tput, "compact_bytes": compact}

    deltas = {}
    for tier in ["high", "mid", "low", "all"]:
        if tier in tiers.get("static", {}) and tier in tiers.get("online", {}):
            st = tiers["static"][tier]
            on = tiers["online"][tier]
            deltas[tier] = {
                "p99_pct": pct(on.get("p99_us", 0), st.get("p99_us", 0)),
                "tput_pct": pct(on.get("tput", 0), st.get("tput", 0)),
            }
    if "static" in tiers and "online" in tiers:
        sa = tiers["static"].get("all", {})
        oa = tiers["online"].get("all", {})
        if sa and oa:
            deltas["all"]["compact_pct"] = pct(oa.get("compact_bytes", 0), sa.get("compact_bytes", 0))

    return {"tiers": tiers, "deltas": deltas}


# ── supplementary tables ────────────────────────────────────────────────

def ablation_table() -> dict:
    entries = []
    for mode, trial in [("demand", None), ("pressure", "embedded_demand2f_16t_ablation_a"),
                         ("hybrid", "embedded_demand2f_16t_ablation_a_hybrid")]:
        if trial is None:
            # Use demand2f aggregate
            agg = aggregate_demand2f()
            if "aggregates" not in agg:
                continue
            ag = agg["aggregates"]
            entry = {"mode": "demand", "source": "5-trial aggregate"}
            for key, label in COMPARE_METRICS:
                if key in ag:
                    entry[label] = ag[key]
            entry["overlap"] = ag.get("mean_high_overlap_after_warmup", {})
            entry["max_lag"] = ag.get("adaptation_max_lag", {})
            entry["gate"] = f"{ag.get('gate_pass_count', '?')}/5"
        else:
            data = load_analysis(trial)
            if data is None:
                continue
            cmp = data.get("online_vs_static", {})
            online = data.get("per_policy", {}).get("online", {})
            lag = online.get("adaptation_lag", {}) or {}
            entry = {
                "mode": mode,
                "source": trial,
                "High P99": {"mean": float(cmp.get("online_vs_static_pct_high_write_p99_us", 0))},
                "High P999": {"mean": float(cmp.get("online_vs_static_pct_high_write_p999_us", 0))},
                "High write tput": {"mean": float(cmp.get("online_vs_static_pct_high_write_throughput", 0))},
                "Total tput": {"mean": float(cmp.get("online_vs_static_pct_total_throughput", 0))},
                "Compact bytes": {"mean": float(cmp.get("online_vs_static_pct_compact_output_bytes", 0))},
                "Bytes/write": {"mean": float(cmp.get("online_vs_static_pct_compact_output_bytes_per_write", 0))},
                "overlap": {"mean": float(online.get("mean_high_overlap_after_warmup", 0))},
                "max_lag": {"mean": float(lag.get("max_lag_windows") or 0)},
                "gate": "fail",
            }
        entries.append(entry)
    return {"ablation": entries}


def longer_run_table() -> dict:
    data = load_analysis("embedded_demand2f_16t_long_a")
    if data is None:
        return {"error": "long_a not found"}
    cmp = data.get("online_vs_static", {})
    online = data.get("per_policy", {}).get("online", {})
    lag = online.get("adaptation_lag", {}) or {}
    return {
        "trial": "embedded_demand2f_16t_long_a",
        "duration_sec": 360,
        "drift_events": 16,
        "online_vs_static": {
            "high_p99_pct": float(cmp.get("online_vs_static_pct_high_write_p99_us", 0)),
            "high_p999_pct": float(cmp.get("online_vs_static_pct_high_write_p999_us", 0)),
            "high_write_tput_pct": float(cmp.get("online_vs_static_pct_high_write_throughput", 0)),
            "total_tput_pct": float(cmp.get("online_vs_static_pct_total_throughput", 0)),
            "compact_bytes_pct": float(cmp.get("online_vs_static_pct_compact_output_bytes", 0)),
            "bytes_per_write_pct": float(cmp.get("online_vs_static_pct_compact_output_bytes_per_write", 0)),
        },
        "adaptation": {
            "mean_overlap": float(online.get("mean_high_overlap_after_warmup", 0)),
            "max_lag": lag.get("max_lag_windows"),
        },
        "failed_tenants": int(online.get("failed_tenants", 0)),
    }


# ── static_biased comparison ────────────────────────────────────────────

def static_biased_table() -> dict:
    data = load_analysis("embedded_demand2f_16t_with_baseline")
    if data and "per_policy" in data:
        per = data["per_policy"]
    else:
        data2 = load_analysis("embedded_demand2f_16t")
        if data2 is None:
            return {"error": "no data"}
        per = data2.get("per_policy", {})

    result = {}
    for pol in ["static", "static_biased", "online"]:
        if pol not in per:
            continue
        p = per[pol]
        result[pol] = {
            "high_p99_us": float(p.get("high_write_p99_us", 0)),
            "high_p999_us": float(p.get("high_write_p999_us", 0)),
            "high_write_tput": float(p.get("high_write_throughput", 0)),
            "total_tput": float(p.get("total_throughput", 0)),
            "compact_bytes_per_write": float(p.get("compact_output_bytes_per_write", 0)),
            "mean_overlap": float(p.get("mean_high_overlap_after_warmup", 0)),
        }
    return result


# ── markdown table output ───────────────────────────────────────────────

def md_main_continuous(agg: dict) -> str:
    lines = [
        "## Main Result: Continuous demand2f (5 trials, online vs static)",
        "",
        "| Metric | Mean | Min | Max | Stdev | 95% CI",
        "|---|---:|---:|---:|---:|---:|",
    ]
    ag = agg.get("aggregates", {})
    labels = {
        "online_vs_static_pct_high_write_p99_us": "High write P99",
        "online_vs_static_pct_high_write_p999_us": "High write P999",
        "online_vs_static_pct_high_write_throughput": "High write throughput",
        "online_vs_static_pct_total_throughput": "Total throughput",
        "online_vs_static_pct_compact_output_bytes": "Compact bytes",
        "online_vs_static_pct_compact_output_bytes_per_write": "Bytes/write",
    }
    for key, label in labels.items():
        s = ag.get(key, {})
        if s.get("n", 0) == 0:
            continue
        ci = ""
        half_width = ci95_half_width(s)
        if half_width is not None:
            ci = f"±{half_width:.1f}pp"
        lines.append(f"| {label} | {s['mean']:+.1f}% | {s['min']:+.1f}% | {s['max']:+.1f}% | {s['stdev']:.1f} | {ci} |")

    overlap = ag.get("mean_high_overlap_after_warmup", {})
    lines.append(f"| Mean high overlap | {overlap.get('mean', 0):.2f}/4 | {overlap.get('min', 0):.2f} | {overlap.get('max', 0):.2f} | {overlap.get('stdev', 0):.2f} | - |")
    lines.append(f"| Gate pass | {ag.get('gate_pass_count', '?')}/{ag.get('trial_count', '?')} | - | - | - | - |")
    lines.append(f"| Failed tenants | {ag.get('failed_tenants_total', '?')} | - | - | - | - |")
    lines.append("")
    return "\n".join(lines)


def md_fairness_continuous(agg: dict) -> str:
    lines = [
        "## Fairness / Collateral Damage: Continuous demand2f (5 trials, online vs static)",
        "",
        "| Tier | P99 | P999 | Write tput | Total tput | Bytes | Bytes/write |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    metrics = ["write_p99_pct", "write_p999_pct", "write_throughput_pct",
               "total_throughput_pct", "compact_bytes_pct", "compact_bytes_per_write_pct"]
    headers = ["P99", "P999", "Write tput", "Total tput", "Bytes", "Bytes/write"]
    for tier in ["high", "mid", "low", "all"]:
        label = tier.upper() if tier != "all" else "ALL"
        parts = [label]
        for m in metrics:
            s = agg.get(tier, {}).get(m, {})
            if s.get("n", 0) > 0:
                parts.append(f"{s['mean']:+.1f}%")
            else:
                parts.append("n/a")
        lines.append("| " + " | ".join(parts) + " |")
    lines.append("")
    return "\n".join(lines)


def md_read_cpu_collateral() -> str:
    path = RESULTS / "paper_tables" / "continuous_read_cpu_collateral.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def md_epoch_fairness(ef: dict) -> str:
    lines = [
        "## Fairness: Epoch-level realistic_big_a (online vs static)",
        "",
        "| Tier | P99 delta | Throughput delta |",
        "|---|---:|---:|",
    ]
    for tier in ["high", "mid", "low", "all"]:
        label = tier.upper() if tier != "all" else "ALL"
        d = ef.get("deltas", {}).get(tier, {})
        p99 = f"{d['p99_pct']:+.1f}%" if "p99_pct" in d else "n/a"
        tput = f"{d['tput_pct']:+.1f}%" if "tput_pct" in d else "n/a"
        lines.append(f"| {label} | {p99} | {tput} |")
    lines.append("")
    return "\n".join(lines)


def md_epoch_fairness_aggregate(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    agg = payload["aggregates"]["online_vs_static_compare"]
    rows = [
        ("HIGH", "online_vs_static_pct_high_mean_write_p99_us", "online_vs_static_pct_high_sum_ops_per_sec"),
        ("MID", "online_vs_static_pct_mid_mean_write_p99_us", "online_vs_static_pct_mid_sum_ops_per_sec"),
        ("LOW", "online_vs_static_pct_low_mean_write_p99_us", "online_vs_static_pct_low_sum_ops_per_sec"),
        ("ALL", "online_vs_static_pct_mean_write_p99_us", "online_vs_static_pct_sum_ops_per_sec"),
    ]
    lines = [
        "## Fairness: Epoch-level realistic_big_a (5-trial aggregate, online vs static)",
        "",
        "| Tier | P99 delta | Throughput delta |",
        "|---|---:|---:|",
    ]
    for label, p99_key, tput_key in rows:
        lines.append(f"| {label} | {agg[p99_key]['mean']:+.1f}% | {agg[tput_key]['mean']:+.1f}% |")
    lines.append("")
    return "\n".join(lines)


def md_file_section(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip() + "\n"


def md_static_biased(sb: dict) -> str:
    if "error" in sb:
        return ""
    lines = [
        "## Stronger Baseline: static_biased (demand2f trial a)",
        "",
        "| Policy | High P99 (us) | High P999 (us) | High write tput | Bytes/write | Overlap |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for pol in ["static", "static_biased", "online"]:
        if pol not in sb:
            continue
        p = sb[pol]
        lines.append(f"| {pol} | {p['high_p99_us']:.0f} | {p['high_p999_us']:.0f} | "
                     f"{p['high_write_tput']:.0f} | {p['compact_bytes_per_write']:.0f} | "
                     f"{p['mean_overlap']:.2f}/4 |")
    lines.append("")
    return "\n".join(lines)


def md_ablation(abl: dict) -> str:
    lines = [
        "## Score-Mode Ablation",
        "",
        "| Mode | High P99 | High P999 | High tput | Total tput | Bytes | B/W | Overlap | Max lag | Gate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for entry in abl.get("ablation", []):
        mode = entry["mode"]
        parts = [mode]
        for label in ["High P99", "High P999", "High write tput", "Total tput", "Compact bytes", "Bytes/write"]:
            v = entry.get(label, {})
            if isinstance(v, dict) and "mean" in v:
                parts.append(f"{v['mean']:+.1f}%")
            else:
                parts.append("n/a")
        ov = entry.get("overlap", {})
        if isinstance(ov, dict) and "mean" in ov:
            parts.append(f"{ov['mean']:.2f}/4")
        else:
            parts.append("n/a")
        lag = entry.get("max_lag", {})
        if isinstance(lag, dict) and "mean" in lag:
            parts.append(f"{lag['mean']:.0f}")
        else:
            parts.append("n/a")
        parts.append(str(entry.get("gate", "n/a")))
        lines.append("| " + " | ".join(parts) + " |")
    lines.append("")
    return "\n".join(lines)


def md_longer_run(lr: dict) -> str:
    if "error" in lr:
        return ""
    ovs = lr.get("online_vs_static", {})
    adapt = lr.get("adaptation", {})
    lines = [
        "## Longer-Run Confirmation (360s, 16 drift events)",
        "",
        f"| Metric | Value |",
        f"|---|---:|",
        f"| High P99 | {ovs.get('high_p99_pct', 0):+.1f}% |",
        f"| High P999 | {ovs.get('high_p999_pct', 0):+.1f}% |",
        f"| High write tput | {ovs.get('high_write_tput_pct', 0):+.1f}% |",
        f"| Total tput | {ovs.get('total_tput_pct', 0):+.1f}% |",
        f"| Bytes/write | {ovs.get('bytes_per_write_pct', 0):+.1f}% |",
        f"| Mean overlap | {adapt.get('mean_overlap', 0):.2f}/4 |",
        f"| Max lag | {adapt.get('max_lag', '?')} windows |",
        f"| Failed tenants | {lr.get('failed_tenants', '?')} |",
        "",
    ]
    return "\n".join(lines)


# ── realistic_big aggregate ─────────────────────────────────────────────

def aggregate_realistic_big() -> dict:
    """Aggregate realistic_big repeated trials if available."""
    trials = []
    for suffix in ["a", "b", "c", "d", "e"]:
        trial_name = f"realistic_big_{suffix}" if suffix != "a" else "realistic_big_a"
        analysis = load_realistic_analysis(trial_name)
        if analysis is None:
            continue
        s = analysis.get("summary", {})
        trials.append({
            "trial": trial_name,
            "high_p99_us": s.get("online_high_mean_write_p99_us"),
            "static_high_p99_us": s.get("static_high_mean_write_p99_us"),
            "high_tput": s.get("online_high_sum_ops_per_sec"),
            "static_high_tput": s.get("static_high_sum_ops_per_sec"),
            "total_tput": s.get("online_sum_ops_per_sec"),
            "static_total_tput": s.get("static_sum_ops_per_sec"),
            "compact_bytes": s.get("online_sum_compact_write_bytes"),
            "static_compact_bytes": s.get("static_sum_compact_write_bytes"),
            "pct_high_p99": s.get("online_vs_static_pct_high_mean_write_p99_us"),
            "pct_high_tput": s.get("online_vs_static_pct_high_sum_ops_per_sec"),
            "pct_total_tput": s.get("online_vs_static_pct_sum_ops_per_sec"),
            "pct_compact": s.get("online_vs_static_pct_sum_compact_write_bytes"),
        })

    if not trials:
        return {"n": 0}

    result = {"n": len(trials), "per_trial": trials}
    for metric in ["pct_high_p99", "pct_high_tput", "pct_total_tput", "pct_compact"]:
        vals = [t[metric] for t in trials if t[metric] is not None]
        if vals:
            result[f"agg_{metric}"] = summarize(vals)
    return result


def md_realistic_big(rb: dict) -> str:
    detailed = RESULTS / "paper_tables" / "realistic_big_a_aggregate.md"
    if detailed.exists():
        lines = [
            "## Main Result: Epoch-Level realistic_big_a (5 trials, online vs static)",
            "",
            "Detailed source: `remote-results/paper_tables/realistic_big_a_aggregate.md`.",
            "",
            "| Metric | Mean | Min | Max | Stdev | 95% CI |",
            "|---|---:|---:|---:|---:|---|",
        ]
        payload = json.loads((RESULTS / "paper_tables" / "realistic_big_a_aggregate.json").read_text(encoding="utf-8"))
        agg = payload["aggregates"]["online_vs_static_compare"]
        rows = [
            ("High write P99", "online_vs_static_pct_high_mean_write_p99_us"),
            ("High throughput", "online_vs_static_pct_high_sum_ops_per_sec"),
            ("Total throughput", "online_vs_static_pct_sum_ops_per_sec"),
            ("Compact bytes", "online_vs_static_pct_sum_compact_write_bytes"),
        ]
        for label, key in rows:
            s = agg[key]
            lines.append(
                f"| {label} | {s['mean']:+.1f}% | {s['min']:+.1f}% | {s['max']:+.1f}% | "
                f"{s['stdev']:.1f} | [{s['ci95_low']:+.1f}%, {s['ci95_high']:+.1f}%] |"
            )
        overlap = payload["aggregates"].get("online_overlap", {})
        if overlap:
            lines.append(
                f"| Mean high overlap | {overlap['mean']:.2f}/4 | {overlap['min']:.2f} | "
                f"{overlap['max']:.2f} | {overlap['stdev']:.2f} | - |"
            )
        lines.append("")
        return "\n".join(lines)

    n = rb.get("n", 0)
    if n == 0:
        return ""
    lines = [
        f"## Epoch-Level realistic_big ({n} trial(s), online vs static)",
        "",
    ]
    if n == 1:
        t = rb["per_trial"][0]
        lines.extend([
            "| Metric | Value |",
            "|---|---:|",
            f"| High write P99 | {t.get('pct_high_p99', 0):+.1f}% |" if t.get("pct_high_p99") else "",
            f"| High throughput | {t.get('pct_high_tput', 0):+.1f}% |" if t.get("pct_high_tput") else "",
            f"| Total throughput | {t.get('pct_total_tput', 0):+.1f}% |" if t.get("pct_total_tput") else "",
            f"| Compact bytes | {t.get('pct_compact', 0):+.1f}% |" if t.get("pct_compact") else "",
        ])
    else:
        lines.extend([
            "| Metric | Mean | Min | Max | Stdev |",
            "|---|---:|---:|---:|---:|",
        ])
        labels = {"agg_pct_high_p99": "High write P99", "agg_pct_high_tput": "High throughput",
                   "agg_pct_total_tput": "Total throughput", "agg_pct_compact": "Compact bytes"}
        for key, label in labels.items():
            s = rb.get(key, {})
            if s.get("n", 0) > 0:
                lines.append(f"| {label} | {s['mean']:+.1f}% | {s['min']:+.1f}% | {s['max']:+.1f}% | {s['stdev']:.1f} |")
    lines.append("")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=ROOT / "remote-results/paper_tables")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing existing paper-table artifacts.")
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    fail_if_missing_inputs()
    fail_if_outputs_exist(outdir, args.overwrite)
    outdir.mkdir(parents=True, exist_ok=True)

    # 1. Continuous demand2f aggregate
    cont_agg = aggregate_demand2f()
    (outdir / "main_continuous_demand2f.json").write_text(
        json.dumps(cont_agg, indent=2, sort_keys=True), encoding="utf-8")

    # 2. Continuous fairness
    fairness_results = []
    for trial in DEMAND2F_TRIALS:
        fr = fairness_for_trial(trial)
        if fr:
            fairness_results.append(fr)
    cont_fairness = aggregate_fairness(fairness_results)
    (outdir / "fairness_continuous_demand2f.json").write_text(
        json.dumps({"per_trial": fairness_results, "aggregate": cont_fairness}, indent=2, sort_keys=True),
        encoding="utf-8")

    # 3. Epoch-level realistic_big
    epoch_analysis = load_realistic_analysis("realistic_big_a")
    epoch_fair = epoch_fairness(epoch_analysis) if epoch_analysis else {}
    (outdir / "fairness_epoch_realistic_big.json").write_text(
        json.dumps(epoch_fair, indent=2, sort_keys=True), encoding="utf-8")

    # 4. Realistic_big repeated trial aggregate
    rb_agg = aggregate_realistic_big()
    (outdir / "aggregate_realistic_big.json").write_text(
        json.dumps(rb_agg, indent=2, sort_keys=True), encoding="utf-8")

    # 5. Ablation table
    abl = ablation_table()
    (outdir / "ablation_score_modes.json").write_text(
        json.dumps(abl, indent=2, sort_keys=True), encoding="utf-8")

    # 6. Longer run
    lr = longer_run_table()
    (outdir / "longer_run_confirmation.json").write_text(
        json.dumps(lr, indent=2, sort_keys=True), encoding="utf-8")

    # 7. Static_biased comparison
    sb = static_biased_table()
    (outdir / "static_biased_comparison.json").write_text(
        json.dumps(sb, indent=2, sort_keys=True), encoding="utf-8")

    # 8. Markdown report
    md = []
    md.append("# Paper Tables")
    md.append("")
    md.append(f"Generated: see git log for date.")
    md.append("")
    md.append(md_main_continuous(cont_agg))
    md.append(md_realistic_big(rb_agg))
    md.append(md_fairness_continuous(cont_fairness))
    if epoch_fair:
        repeated_epoch = RESULTS / "paper_tables" / "realistic_big_a_aggregate.json"
        if repeated_epoch.exists():
            md.append(md_epoch_fairness_aggregate(repeated_epoch))
        else:
            md.append(md_epoch_fairness(epoch_fair))
    read_cpu = md_read_cpu_collateral()
    if read_cpu:
        md.append(read_cpu)
    builtin = md_file_section(RESULTS / "paper_tables" / "realistic_big_builtin_aggregate.md")
    if builtin:
        md.append("## Built-In RocksDB Baseline: static_autotuned (5 trials, realistic_big_{a..e})")
        md.append("")
        md.append("Detailed source: `remote-results/paper_tables/realistic_big_builtin_aggregate.md`.")
        md.append("")
        md.append(builtin)
    if sb:
        md.append(md_static_biased(sb))
    md.append(md_ablation(abl))
    md.append(md_longer_run(lr))
    (outdir / "paper_tables.md").write_text("\n".join(md), encoding="utf-8")

    print(f"Paper tables written to {outdir}/")
    print(f"  - paper_tables.md (markdown report)")
    print(f"  - main_continuous_demand2f.json")
    print(f"  - fairness_continuous_demand2f.json")
    print(f"  - fairness_epoch_realistic_big.json")
    print(f"  - aggregate_realistic_big.json ({rb_agg.get('n', 0)} trials)")
    print(f"  - ablation_score_modes.json")
    print(f"  - longer_run_confirmation.json")
    print(f"  - static_biased_comparison.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
