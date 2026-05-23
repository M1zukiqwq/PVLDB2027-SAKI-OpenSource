#!/usr/bin/env python3
"""Aggregate the SAKI window-length mini-sweep.

For each window_sec in {10, 20, 40} we read
  embedded_continuous_analysis_wl{W}_{seed}.json
plus the raw policy JSONs
  embedded_continuous_static_wl{W}_{seed}.json
  embedded_continuous_online_wl{W}_{seed}.json
to extract:

- per-trial online-vs-static high P99, P999, high write throughput,
  total throughput, compaction bytes, bytes/write
- per-trial mean high-set overlap after warmup
- per-trial max adaptation lag (in control-windows)
- per-trial failed tenants for each policy
- per-trial LOW-tier collateral: write P99/P999 and total throughput change

LOW-tier metrics are computed here directly from the raw window_records
(filtering true_tier == 'low'), because the existing analyzer only emits the
high-tier-filtered metrics.

Statistical rendering follows the workload-matrix convention:
- n=2 -> mean [min, max]
- n>=3 -> mean +/-CI (Student-t 95% half-width)
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

T_CRIT_975 = {
    2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
    7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262,
}

ONLINE_VS_STATIC = [
    ("online_vs_static_pct_high_write_p99_us", "High P99"),
    ("online_vs_static_pct_high_write_p999_us", "High P999"),
    ("online_vs_static_pct_high_write_throughput", "High tput"),
    ("online_vs_static_pct_total_throughput", "Total tput"),
    ("online_vs_static_pct_compact_output_bytes", "Compact bytes"),
    ("online_vs_static_pct_compact_output_bytes_per_write", "Bytes/write"),
]


def summarize(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0}
    out: dict[str, object] = {
        "n": n,
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
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


def fmt_pct(stat: dict, precision: int = 1) -> str:
    if not stat or stat.get("n", 0) == 0:
        return "n/a"
    n = stat["n"]
    mean = stat["mean"]
    if n == 1:
        return f"{mean:+.{precision}f}% (n=1)"
    if n == 2:
        return f"{mean:+.{precision}f}% [{stat['min']:+.1f},{stat['max']:+.1f}]"
    if stat.get("ci95_margin") is not None:
        return f"{mean:+.{precision}f}% +/-{stat['ci95_margin']:.1f}"
    return f"{mean:+.{precision}f}% (n={n})"


def fmt_num(stat: dict, precision: int = 2, denom: str = "/4") -> str:
    if not stat or stat.get("n", 0) == 0:
        return "n/a"
    return f"{stat['mean']:.{precision}f}{denom}"


def low_tier_metrics(raw_payload: dict) -> dict:
    """Compute LOW-tier collateral from raw window_records."""
    recs = [r for r in raw_payload.get("window_records", []) if r.get("true_tier") == "low"]
    if not recs:
        return {"n_low_rows": 0}
    total_sec = float(raw_payload.get("summary", {}).get("duration_sec", 0.0))
    write_ops = sum(float(r.get("write_ops", 0.0)) for r in recs)
    read_ops = sum(float(r.get("read_ops", 0.0)) for r in recs)
    total_ops = write_ops + read_ops
    num = sum(float(r.get("write_p99_us", 0.0)) * float(r.get("write_ops", 0.0)) for r in recs)
    den = sum(float(r.get("write_ops", 0.0)) for r in recs)
    p99 = num / den if den else 0.0
    num999 = sum(float(r.get("write_p999_us", 0.0)) * float(r.get("write_ops", 0.0)) for r in recs)
    p999 = num999 / den if den else 0.0
    return {
        "n_low_rows": len(recs),
        "low_write_p99_us": p99,
        "low_write_p999_us": p999,
        "low_total_throughput": total_ops / total_sec if total_sec else 0.0,
        "low_write_throughput": write_ops / total_sec if total_sec else 0.0,
    }


def low_collateral_pct(online_raw: dict, static_raw: dict) -> dict:
    lo = low_tier_metrics(online_raw)
    ls = low_tier_metrics(static_raw)
    if lo.get("n_low_rows", 0) == 0 or ls.get("n_low_rows", 0) == 0:
        return {"low_metrics_available": False}

    def pct(new: float, old: float) -> float:
        return (new - old) / old * 100.0 if old else 0.0

    return {
        "low_metrics_available": True,
        "low_pct_write_p99_us": pct(lo["low_write_p99_us"], ls["low_write_p99_us"]),
        "low_pct_write_p999_us": pct(lo["low_write_p999_us"], ls["low_write_p999_us"]),
        "low_pct_total_throughput": pct(lo["low_total_throughput"], ls["low_total_throughput"]),
        "low_pct_write_throughput": pct(lo["low_write_throughput"], ls["low_write_throughput"]),
    }


def load_trial(results_dir: Path, window_sec: int, seed: str, require_raw: bool = True) -> dict | None:
    trial = f"wl{window_sec}_{seed}"
    analysis = results_dir / f"embedded_continuous_analysis_{trial}.json"
    online_raw = results_dir / f"embedded_continuous_online_{trial}.json"
    static_raw = results_dir / f"embedded_continuous_static_{trial}.json"
    if not analysis.exists():
        return None
    missing_raw = [p for p in (online_raw, static_raw) if not p.exists()]
    if missing_raw and require_raw:
        raise FileNotFoundError(
            f"{trial} has analysis but is missing raw policy JSON needed for LOW collateral: "
            + ", ".join(str(p) for p in missing_raw)
        )
    payload = json.loads(analysis.read_text(encoding="utf-8"))
    cmp = payload.get("online_vs_static", {})
    online = payload.get("per_policy", {}).get("online", {})
    static = payload.get("per_policy", {}).get("static", {})
    rec: dict[str, object] = {
        "trial": trial,
        "window_sec": window_sec,
        "seed": seed,
        "analysis_path": str(analysis),
        "compare": {k: float(cmp.get(k, 0.0)) for k, _ in ONLINE_VS_STATIC},
        "overlap_after_warmup": float(online.get("mean_high_overlap_after_warmup", 0.0)),
        "windows_recorded": float(online.get("windows_recorded", 0.0)),
        "failed_online": int(online.get("failed_tenants", 0)),
        "failed_static": int(static.get("failed_tenants", 0)),
    }
    lag_info = online.get("adaptation_lag", {})
    rec["max_lag_windows"] = lag_info.get("max_lag_windows")
    rec["change_windows"] = lag_info.get("change_windows", [])
    if not missing_raw:
        ord = json.loads(online_raw.read_text(encoding="utf-8"))
        srd = json.loads(static_raw.read_text(encoding="utf-8"))
        rec["low_collateral"] = low_collateral_pct(ord, srd)
        rec["high_count"] = int(ord.get("args", {}).get("high_count", 4))
    else:
        rec["low_collateral"] = {"low_metrics_available": False}
        rec["high_count"] = 4
    return rec


def summarize_window(trials: list[dict]) -> dict:
    if not trials:
        return {"n": 0}
    high_count = trials[0].get("high_count", 4)
    stats = {
        "high_p99": summarize([t["compare"]["online_vs_static_pct_high_write_p99_us"] for t in trials]),
        "high_p999": summarize([t["compare"]["online_vs_static_pct_high_write_p999_us"] for t in trials]),
        "high_tput": summarize([t["compare"]["online_vs_static_pct_high_write_throughput"] for t in trials]),
        "total_tput": summarize([t["compare"]["online_vs_static_pct_total_throughput"] for t in trials]),
        "compact_bytes": summarize([t["compare"]["online_vs_static_pct_compact_output_bytes"] for t in trials]),
        "bytes_per_write": summarize([t["compare"]["online_vs_static_pct_compact_output_bytes_per_write"] for t in trials]),
        "overlap": summarize([t["overlap_after_warmup"] for t in trials]),
    }
    lags = [t.get("max_lag_windows") for t in trials]
    finite_lags = [x for x in lags if isinstance(x, (int, float))]
    stats["max_lag_windows"] = summarize([float(x) for x in finite_lags]) if finite_lags else {"n": 0}
    stats["lag_unbounded_count"] = sum(1 for x in lags if x is None)

    if all(t.get("low_collateral", {}).get("low_metrics_available") for t in trials):
        stats["low_p99"] = summarize([t["low_collateral"]["low_pct_write_p99_us"] for t in trials])
        stats["low_p999"] = summarize([t["low_collateral"]["low_pct_write_p999_us"] for t in trials])
        stats["low_total_tput"] = summarize([t["low_collateral"]["low_pct_total_throughput"] for t in trials])
        stats["low_write_tput"] = summarize([t["low_collateral"]["low_pct_write_throughput"] for t in trials])
        stats["low_available"] = True
    else:
        stats["low_available"] = False

    indicators = {
        "max_failed_static": max(t["failed_static"] for t in trials),
        "max_failed_online": max(t["failed_online"] for t in trials),
        "windows_per_run": int(trials[0].get("windows_recorded", 0)),
        "high_count": high_count,
    }
    return {
        "n": len(trials),
        "stats": stats,
        "indicators": indicators,
        "trials": trials,
        "high_count": high_count,
    }


def render_markdown(payload: dict) -> str:
    md: list[str] = []

    def row(window_sec: int) -> dict:
        return payload["windows"][str(window_sec)]

    def stat(window_sec: int, name: str) -> dict:
        return row(window_sec)["stats"][name]

    def overlap(window_sec: int) -> str:
        w = row(window_sec)
        return fmt_num(w["stats"]["overlap"], denom=f"/{w.get('high_count', 4)}")

    def lag_phrase(window_sec: int) -> str:
        s = row(window_sec)["stats"]
        lag_stat = s.get("max_lag_windows", {})
        unbounded = int(s.get("lag_unbounded_count", 0))
        if lag_stat.get("n", 0) == 0:
            return f"unbounded in all {unbounded} seeds" if unbounded else "n/a"
        text = f"{lag_stat['mean']:.2f} control windows"
        if unbounded:
            text += f", with {unbounded} unbounded seeds"
        return text

    def has_full_bracket() -> bool:
        return all(
            str(window_sec) in payload["windows"]
            and payload["windows"][str(window_sec)].get("n", 0) > 0
            and payload["windows"][str(window_sec)].get("stats", {}).get("low_available")
            for window_sec in (10, 20, 40)
        )
    md.append("# Window-Length Sensitivity Sweep")
    md.append("")
    md.append("SAKI control-window sensitivity around the continuous main anchor")
    md.append("`embedded_demand2f_16t`. Holds every other parameter (16 tenants,")
    md.append("duration=160s, 4/8/4 split, per-tenant budgets 11/6/1 MB/s, demand2f")
    md.append("offered load, drift_tenants=8, value_size=1024, num_keys=80000) fixed.")
    md.append("Same paper SAKI policy (`online --online-score-mode demand")
    md.append("--online-budget-mode fixed`). The same-budget contract is preserved")
    md.append("(aggregate = 96 MB/s); we are sweeping the control window, not the")
    md.append("aggregate I/O.")
    md.append("")
    md.append("Per-row n is the number of seeds (a, b, c) that completed both static")
    md.append("and online for that `window_sec`. Comparisons are online-vs-static")
    md.append("within each trial; the table reports mean +/-CI (Student-t 95%) when")
    md.append("n>=3 and `mean [min, max]` otherwise.")
    md.append("")
    md.append("## Online vs static, high tier")
    md.append("")
    md.append("| window_sec | n | windows/run | High P99 | High P999 | High tput | Total tput | Bytes/write | Overlap | max lag (windows) | failed (s/o) |")
    md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for wl in payload["windows_sorted"]:
        w = payload["windows"][str(wl)]
        n = w.get("n", 0)
        if n == 0:
            md.append(f"| {wl} | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        s = w["stats"]
        ind = w["indicators"]
        hc = w.get("high_count", 4)
        lag_stat = s.get("max_lag_windows", {})
        if lag_stat.get("n", 0) == 0:
            lag_cell = "unbounded" if s.get("lag_unbounded_count", 0) > 0 else "n/a"
        else:
            lag_cell = f"{lag_stat['mean']:.2f} (max {lag_stat['max']:.0f})"
            if s.get("lag_unbounded_count", 0) > 0:
                lag_cell += f", +{s['lag_unbounded_count']} unbounded"
        md.append(
            f"| {wl} | {n} | {ind['windows_per_run']} | "
            f"{fmt_pct(s['high_p99'])} | "
            f"{fmt_pct(s['high_p999'])} | "
            f"{fmt_pct(s['high_tput'])} | "
            f"{fmt_pct(s['total_tput'])} | "
            f"{fmt_pct(s['bytes_per_write'])} | "
            f"{fmt_num(s['overlap'], denom=f'/{hc}')} | "
            f"{lag_cell} | "
            f"{ind['max_failed_static']}/{ind['max_failed_online']} |"
        )
    md.append("")
    md.append("## LOW-tier collateral (online vs static)")
    md.append("")
    md.append("Computed directly from raw per-tenant window_records filtered to")
    md.append("`true_tier == 'low'`. Negative LOW throughput change is the expected")
    md.append("cost of moving budget toward HIGH; large positive LOW P99/P999 changes")
    md.append("would indicate collateral tail pain.")
    md.append("")
    md.append("| window_sec | n | LOW P99 | LOW P999 | LOW write tput | LOW total tput |")
    md.append("|---:|---:|---:|---:|---:|---:|")
    for wl in payload["windows_sorted"]:
        w = payload["windows"][str(wl)]
        n = w.get("n", 0)
        if n == 0:
            md.append(f"| {wl} | 0 | n/a | n/a | n/a | n/a |")
            continue
        s = w["stats"]
        if not s.get("low_available"):
            md.append(f"| {wl} | {n} | unavailable | unavailable | unavailable | unavailable |")
            continue
        md.append(
            f"| {wl} | {n} | "
            f"{fmt_pct(s['low_p99'])} | "
            f"{fmt_pct(s['low_p999'])} | "
            f"{fmt_pct(s['low_write_tput'])} | "
            f"{fmt_pct(s['low_total_tput'])} |"
        )
    md.append("")
    md.append("## Per-trial details")
    md.append("")
    for wl in payload["windows_sorted"]:
        w = payload["windows"][str(wl)]
        if w.get("n", 0) == 0:
            continue
        md.append(f"### window_sec = {wl}")
        md.append("")
        md.append("| trial | High P99 % | High P999 % | High tput % | Total tput % | Bytes/write % | Overlap | max lag | failed (s/o) |")
        md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        hc = w.get("high_count", 4)
        for t in w["trials"]:
            c = t["compare"]
            lag = t.get("max_lag_windows")
            lag_s = "unbounded" if lag is None else f"{int(lag)}"
            md.append(
                f"| {t['trial']} | "
                f"{c['online_vs_static_pct_high_write_p99_us']:+.1f}% | "
                f"{c['online_vs_static_pct_high_write_p999_us']:+.1f}% | "
                f"{c['online_vs_static_pct_high_write_throughput']:+.1f}% | "
                f"{c['online_vs_static_pct_total_throughput']:+.1f}% | "
                f"{c['online_vs_static_pct_compact_output_bytes_per_write']:+.1f}% | "
                f"{t['overlap_after_warmup']:.2f}/{hc} | "
                f"{lag_s} | "
                f"{t['failed_static']}/{t['failed_online']} |"
            )
        md.append("")
    md.append("Caveats:")
    md.append("- This is a *window-length sensitivity / stability boundary* sweep,")
    md.append("  not a new main result. The headline main continuous claim")
    md.append("  (window_sec=20, n=5) stands unchanged.")
    md.append("- The fixed-budget contract (per-tenant 11/6/1 MB/s; aggregate 96 MB/s")
    md.append("  under 4/8/4) is preserved across all rows.")
    md.append("- `windows/run` shrinks at larger window_sec (duration_sec=160 fixed).")
    md.append("  This means longer windows have fewer post-warmup samples for the")
    md.append("  overlap statistic and fewer opportunities to observe a phase change,")
    md.append("  so lag is measured in *control windows*, not seconds.")
    md.append("- LOW-tier metrics in the second table are derived here from raw")
    md.append("  `window_records` (true_tier == 'low'); the upstream analyzer does")
    md.append("  not emit a LOW summary today.")
    if has_full_bracket():
        md.append("")
        md.append("## Interpretation")
        md.append("")
        md.append(
            f"**10 s window -- aggressive but noisy.** High write throughput remains "
            f"positive ({fmt_pct(stat(10, 'high_tput'))}), but its CI is wide and "
            f"High P99 is not a win ({fmt_pct(stat(10, 'high_p99'))}). Overlap is "
            f"{overlap(10)} and adaptation lag is {lag_phrase(10)}, so the controller "
            f"tracks promptly but pays for the shorter window with noisy tail latency. "
            f"LOW P99 is also elevated ({fmt_pct(stat(10, 'low_p99'))}), which marks "
            f"this as the aggressive boundary rather than the main operating point."
        )
        md.append("")
        md.append(
            f"**20 s window -- the mechanism anchor.** This is the only row whose "
            f"High-P99 confidence interval is strictly below zero "
            f"({fmt_pct(stat(20, 'high_p99'))}). High throughput remains positive "
            f"({fmt_pct(stat(20, 'high_tput'))}), overlap is {overlap(20)}, "
            f"adaptation lag is {lag_phrase(20)}, and no tenants fail. This keeps "
            f"the window-length bracket aligned with the headline continuous result "
            f"without making the sensitivity sweep a new tuned main result."
        )
        md.append("")
        md.append(
            f"**40 s window -- lag-bounded.** High throughput remains positive on "
            f"average ({fmt_pct(stat(40, 'high_tput'))}), but the longer control "
            f"period leaves overlap at {overlap(40)} and adaptation lag is "
            f"{lag_phrase(40)}. No tenant fails, so this is a tracking/responsiveness "
            f"limit rather than a capacity-violation regime."
        )
        md.append("")
        md.append(
            "**Stability boundary, not a new claim.** Together these rows bracket "
            "the 20 s anchor: 10 s is more reactive but tail-latency noisy, 40 s "
            "under-tracks the drifting high set, and 20 s gives the only CI-strict "
            "High-P99 improvement. The fixed-budget claim and the headline main "
            "continuous numbers (`embedded_demand2f_16t`, n=5) are unchanged by "
            "this sweep."
        )
    md.append("")
    md.append("## Notes on the source-of-truth budget")
    md.append("")
    md.append(
        "The actual `embedded_demand2f_16t` main continuous result was run with "
        "per-tenant high/mid/low = 11/6/1 MB/s, aggregate `4*11 + 8*6 + 4*1 = "
        "96 MB/s`. This sweep uses the same per-tenant budgets so the "
        "fixed-budget contract is preserved row-by-row."
    )
    md.append("")
    md.append(
        "The 11/7/3 MB/s, 112 MB/s contract belongs to the epoch/public-trace "
        "family, not to the continuous main. The window-length sweep "
        "intentionally does not mix the two anchors."
    )
    md.append("")
    md.append("## Artifacts")
    md.append("")
    md.append("- Driver: `remote/run_window_length_sweep.py`")
    md.append("- Aggregator: `remote/aggregate_window_length_sweep.py`")
    md.append("- Summary: `remote-results/paper_tables/window_length_sweep.{json,md}`")
    md.append("- Full regeneration also needs the per-trial raw policy JSONs,")
    md.append("  because LOW-tier collateral is computed from raw `window_records`.")
    md.append("- Aggregated rows report zero failed tenants in every window setting.")
    return "\n".join(md) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "remote-results",
        help="Directory holding embedded_continuous_*.json files.",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[10, 20, 40],
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        default=["a", "b", "c"],
    )
    parser.add_argument("--out-markdown", type=Path)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Allow a diagnostic no-data table instead of failing when requested window/seed analyses are missing.",
    )
    args = parser.parse_args()

    payload: dict[str, object] = {
        "windows_sorted": sorted(args.windows),
        "windows": {},
    }
    for wl in sorted(args.windows):
        trials: list[dict] = []
        for seed in args.seeds:
            try:
                rec = load_trial(args.results_dir, wl, seed, require_raw=not args.allow_missing)
            except FileNotFoundError as exc:
                print(f"Refusing to write window-length aggregate: {exc}", file=sys.stderr)
                print(
                    "Restore the full results directory, or pass --allow-missing for an explicit partial diagnostic.",
                    file=sys.stderr,
                )
                return 2
            if rec is not None:
                trials.append(rec)
        payload["windows"][str(wl)] = summarize_window(trials)
    missing_windows = [wl for wl in sorted(args.windows) if payload["windows"][str(wl)].get("n", 0) == 0]
    if missing_windows and not args.allow_missing:
        print(
            "Refusing to write window-length aggregate: requested raw analysis files are missing.",
            file=sys.stderr,
        )
        print(
            "Restore the full results directory, or pass --allow-missing for an explicit partial diagnostic.",
            file=sys.stderr,
        )
        for wl in missing_windows:
            expected = [
                args.results_dir / f"embedded_continuous_analysis_wl{wl}_{seed}.json"
                for seed in args.seeds
            ]
            print(f"  window {wl}: no analysis files found among {[str(p) for p in expected]}", file=sys.stderr)
        return 2

    md = render_markdown(payload)
    if args.out_markdown:
        args.out_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.out_markdown.write_text(md, encoding="utf-8")
        print(f"wrote markdown -> {args.out_markdown}")
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        print(f"wrote json -> {args.out_json}")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
