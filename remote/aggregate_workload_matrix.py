#!/usr/bin/env python3
"""Aggregate the local workload matrix.

For each variant we read every available
`embedded_continuous_analysis_<variant>_<seed>.json` file plus the matching
online raw JSON used to recover high_count.  Statistical rendering follows the
existing pre-committed rules from `aggregate_sensitivity.py`:

- n=1: ``mean (n=1)``
- n=2: ``mean [min, max]`` -- screening only, no CI/stdev
- n>=3: ``mean +/-CI`` (Student-t 95% half-width)

Promotion policy for this matrix: every variant is promoted to n=5 unless it
hits ``capacity-boundary`` (failed tenants in either policy), so that boundary
outcomes carry the same statistical weight as wins.  This avoids the
"selective rigor" anti-pattern where only winners get CIs.

Verdict labels describe the *actual* outcome at the aggregated n, not a
pre-commit-time promotion decision:

- ``capacity-boundary``   any failed tenants (static or online)
- ``controller-boundary`` overlap < 2.5/4 -- controller could not track the
                          high set in this workload
- ``ci-strict-win``       n>=3 AND high P99 95% CI upper bound < 0
                          AND high write throughput 95% CI lower bound > 0
- ``ci-strict-partial``   n>=3 AND exactly one of high P99 / high tput is
                          CI-strict in the expected direction
- ``ci-crosses-zero``     n>=3 AND neither high P99 nor high tput CI is
                          separated from zero
- ``directional-positive`` n<=2 AND mean high P99 <= -2% AND mean high tput
                          >= +5%
- ``directional-mixed``   n<=2 otherwise

No claim of statistical significance is made for n<=2.
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

# Student-t 0.975 critical values, df = n-1.
T_CRIT_975 = {
    2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571,
    7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262,
}

ONLINE_VS_STATIC = [
    ("online_vs_static_pct_high_write_p99_us", "High write P99"),
    ("online_vs_static_pct_high_write_p999_us", "High write P999"),
    ("online_vs_static_pct_high_write_throughput", "High write tput"),
    ("online_vs_static_pct_total_throughput", "Total tput"),
    ("online_vs_static_pct_compact_output_bytes", "Compact bytes"),
    ("online_vs_static_pct_compact_output_bytes_per_write", "Bytes/write"),
]

PROMOTE_RULE = {
    "high_p99_max_pct": -2.0,
    "high_tput_min_pct": 5.0,
    # Overlap threshold expressed as a fraction of high_count so it transfers
    # across variants with different high-set sizes. 0.625 == 2.5/4 (anchor).
    "overlap_fraction_min": 0.625,
}


def summarize(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0}
    out: dict[str, object] = {
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


def fmt_overlap(stat: dict, high_count: int = 4) -> str:
    if not stat or stat.get("n", 0) == 0:
        return "n/a"
    return f"{stat['mean']:.2f}/{high_count}"


def load_analysis(path: str) -> dict:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    cmp = payload.get("online_vs_static", {})
    online = payload.get("per_policy", {}).get("online", {})
    static = payload.get("per_policy", {}).get("static", {})
    trial = os.path.basename(path).replace("embedded_continuous_analysis_", "").replace(".json", "")
    # The analyzer drops args.high_count, so look it up from the raw policy file.
    raw_path = os.path.join(os.path.dirname(path), f"embedded_continuous_online_{trial}.json")
    if not os.path.exists(raw_path):
        raise FileNotFoundError(
            f"missing raw online JSON needed for high_count/overlap denominator: {raw_path}"
        )
    raw = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    high_count = int(raw.get("args", {}).get("high_count", 4))
    overlap_mean = float(online.get("mean_high_overlap_after_warmup", 0.0))
    return {
        "path": path,
        "trial": trial,
        "compare": {k: float(cmp.get(k, 0.0)) for k, _ in ONLINE_VS_STATIC},
        "overlap_after_warmup": overlap_mean,
        "overlap_fraction": overlap_mean / high_count if high_count > 0 else 0.0,
        "high_count": high_count,
        "failed_online": int(online.get("failed_tenants", 0)),
        "failed_static": int(static.get("failed_tenants", 0)),
    }


def classify(stats: dict, indicators: dict) -> str:
    if indicators["max_failed_static"] > 0 or indicators["max_failed_online"] > 0:
        return "capacity-boundary"
    if stats["overlap_fraction"]["mean"] < PROMOTE_RULE["overlap_fraction_min"]:
        return "controller-boundary"
    p99 = stats["high_p99"]
    tput = stats["high_tput"]
    n = p99.get("n", 0)
    if n >= 3 and p99.get("ci95_high") is not None and tput.get("ci95_low") is not None:
        p99_strict = p99["ci95_high"] < 0
        tput_strict = tput["ci95_low"] > 0
        if p99_strict and tput_strict:
            return "ci-strict-win"
        if p99_strict or tput_strict:
            return "ci-strict-partial"
        return "ci-crosses-zero"
    if (
        p99["mean"] <= PROMOTE_RULE["high_p99_max_pct"]
        and tput["mean"] >= PROMOTE_RULE["high_tput_min_pct"]
    ):
        return "directional-positive"
    return "directional-mixed"


def render_markdown(payload: dict) -> str:
    md: list[str] = []
    md.append("# Workload Matrix Aggregate")
    md.append("")
    md.append("One-axis-at-a-time perturbations around the continuous main result")
    md.append("`embedded_demand2f_16t`. Same paper Saki policy (`online` with")
    md.append("`--online-score-mode demand`). Same per-tenant budgets; tenant_count")
    md.append("scaling preserves the static fair-share aggregate `tenant_count * mid_budget`.")
    md.append("")
    md.append("| variant | n | High P99 | High P999 | High tput | Total tput | Bytes/write | Overlap | failed (s/o) | verdict |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|:--|")
    for v in payload["variants"]:
        n = v.get("n", 0)
        if n == 0:
            md.append(f"| {v['name']} | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | no-data |")
            continue
        s = v["stats"]
        ind = v["indicators"]
        md.append(
            f"| {v['name']} | {n} | "
            f"{fmt_pct(s['high_p99'])} | "
            f"{fmt_pct(s['high_p999'])} | "
            f"{fmt_pct(s['high_tput'])} | "
            f"{fmt_pct(s['total_tput'])} | "
            f"{fmt_pct(s['bytes_per_write'])} | "
            f"{fmt_overlap(s['overlap'], v.get('high_count', 4))} | "
            f"{int(ind['max_failed_static'])}/{int(ind['max_failed_online'])} | "
            f"{v['verdict']} |"
        )
    md.append("")
    md.append("Statistical rendering (pre-committed; do not relax for matrix rows):")
    md.append("- n=2 cells show `mean [min, max]`. Direction is the only claim.")
    md.append("- n>=3 cells show `mean +/-CI` (Student-t 95% half-width).")
    md.append("")
    md.append("Promotion policy: every variant is promoted to n=5 except those flagged")
    md.append("`capacity-boundary`, so boundary outcomes carry the same statistical")
    md.append("weight as wins. This avoids selectively pricing rigor only for variants")
    md.append("that already look favorable in screening.")
    md.append("")
    md.append("Verdict labels (describe the actual aggregated outcome, not a promotion decision):")
    md.append("- `capacity-boundary`: any failed tenants in either policy. Workload exceeds the same-budget contract; not promoted to n=5.")
    md.append("- `controller-boundary`: overlap < 2.5/4. Controller could not track the high set in this workload.")
    md.append("- `ci-strict-win`: n>=3, high P99 95% CI upper bound < 0 AND high tput 95% CI lower bound > 0.")
    md.append("- `ci-strict-partial`: n>=3, exactly one of high P99 / high tput is CI-strict in the expected direction.")
    md.append("- `ci-crosses-zero`: n>=3, neither high P99 nor high tput CI is separated from zero.")
    md.append("- `directional-positive`: n<=2, mean high P99 <= -2% AND mean high tput >= +5%.")
    md.append("- `directional-mixed`: n<=2 otherwise. Used only during screening; promoted to n=5 afterwards.")
    return "\n".join(md) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=ROOT / "remote-results",
        help="Directory holding embedded_continuous_analysis_*.json files.",
    )
    parser.add_argument(
        "--variant",
        action="append",
        required=True,
        help="Variant name; reads analysis JSONs that begin with this prefix and an underscore seed. Repeatable.",
    )
    parser.add_argument("--out-markdown", type=Path)
    parser.add_argument("--out-json", type=Path)
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Allow a diagnostic no-data table instead of failing when requested variants have no raw analysis files.",
    )
    args = parser.parse_args()

    payload: dict[str, object] = {"variants": []}
    missing: list[tuple[str, str]] = []
    for name in args.variant:
        pattern = str(args.results_dir / f"embedded_continuous_analysis_{name}_*.json")
        paths = sorted(glob.glob(pattern))
        if not paths:
            missing.append((name, pattern))
            payload["variants"].append({"name": name, "pattern": pattern, "n": 0, "verdict": "no-data"})
            continue
        try:
            per_trial = [load_analysis(p) for p in paths]
        except FileNotFoundError as exc:
            if args.allow_missing:
                print(f"warning: {exc}", file=sys.stderr)
                payload["variants"].append({
                    "name": name,
                    "pattern": pattern,
                    "n": 0,
                    "verdict": "no-data",
                    "missing_input": str(exc),
                })
                continue
            else:
                print(f"Refusing to write workload-matrix aggregate: {exc}", file=sys.stderr)
                print(
                    "Restore the full results directory, or pass --allow-missing for an explicit partial diagnostic.",
                    file=sys.stderr,
                )
                return 2
        stats = {
            "high_p99": summarize([t["compare"]["online_vs_static_pct_high_write_p99_us"] for t in per_trial]),
            "high_p999": summarize([t["compare"]["online_vs_static_pct_high_write_p999_us"] for t in per_trial]),
            "high_tput": summarize([t["compare"]["online_vs_static_pct_high_write_throughput"] for t in per_trial]),
            "total_tput": summarize([t["compare"]["online_vs_static_pct_total_throughput"] for t in per_trial]),
            "compact_bytes": summarize([t["compare"]["online_vs_static_pct_compact_output_bytes"] for t in per_trial]),
            "bytes_per_write": summarize([t["compare"]["online_vs_static_pct_compact_output_bytes_per_write"] for t in per_trial]),
            "overlap": summarize([t["overlap_after_warmup"] for t in per_trial]),
            "overlap_fraction": summarize([t["overlap_fraction"] for t in per_trial]),
        }
        indicators = {
            "max_failed_static": max((t["failed_static"] for t in per_trial), default=0),
            "max_failed_online": max((t["failed_online"] for t in per_trial), default=0),
        }
        high_count = per_trial[0]["high_count"] if per_trial else 4
        verdict = classify(stats, indicators)
        payload["variants"].append({
            "name": name,
            "pattern": pattern,
            "n": len(per_trial),
            "trials": per_trial,
            "stats": stats,
            "indicators": indicators,
            "high_count": high_count,
            "verdict": verdict,
        })

    payload["promote_rule"] = PROMOTE_RULE
    if missing and not args.allow_missing:
        print(
            "Refusing to write workload-matrix aggregate: requested raw analysis files are missing.",
            file=sys.stderr,
        )
        print(
            "Restore the full results directory, or pass --allow-missing for an explicit partial diagnostic.",
            file=sys.stderr,
        )
        for name, pattern in missing:
            print(f"  missing variant {name}: {pattern}", file=sys.stderr)
        return 2
    md = render_markdown(payload)
    if args.out_markdown:
        args.out_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.out_markdown.write_text(md, encoding="utf-8")
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
