#!/usr/bin/env python3
"""Aggregate the coefficient-robustness ablation across seeds.

For every label produced by `run_coefficient_ablation.py` we read the per-seed
`embedded_continuous_analysis_coef_ablation_<label>_seed<N>.json` analysis file
and the matching online raw JSON. Each trial is already paired with the paper
static (via symlink in `run_coefficient_ablation.py`), so the `online_vs_static`
fields in the analysis JSON are per-seed paired deltas.

We aggregate (n=3 per label) and report:
  * High write P99 % (mean, range, paired)
  * High write throughput % (mean, range)
  * Total throughput %
  * Bytes/write %
  * Mean high-set overlap (out of high_count)
  * Max adaptation lag in windows (worst across seeds)
  * Top-4 Jaccard vs original SAKI online decisions, window-averaged then
    seed-averaged.

For Top-4 Jaccard we load the SAKI baseline online raw for the same seed:
  seed 1 -> embedded_continuous_online_embedded_demand2f_16t.json
  seed 2 -> embedded_continuous_online_embedded_demand2f_16t_b.json
  seed 3 -> embedded_continuous_online_embedded_demand2f_16t_c.json

Each window contributes Jaccard(assigned_high_ablation, assigned_high_baseline)
computed over the 4-element high sets; we skip window 0 (warmup, no signal).

We also emit a reference row taken verbatim from the existing 5-trial main
result `main_continuous_demand2f.json` so the table makes the comparison
explicit, marked with a star to flag that it is not a same-batch rerun.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


LABEL_ORDER = [
    "anchor_half",
    "anchor_double",
    "residual_half",
    "residual_double",
    "anchor_only",
]


SEED_TO_BASELINE_TRIAL = {
    1: "embedded_demand2f_16t",
    2: "embedded_demand2f_16t_b",
    3: "embedded_demand2f_16t_c",
}


def analysis_path(results_dir: Path, label: str, seed: int) -> Path:
    return results_dir / f"embedded_continuous_analysis_coef_ablation_{label}_seed{seed}.json"


def online_raw_path(results_dir: Path, label: str, seed: int) -> Path:
    return results_dir / f"embedded_continuous_online_coef_ablation_{label}_seed{seed}.json"


def baseline_online_raw_path(results_dir: Path, seed: int) -> Path:
    return results_dir / f"embedded_continuous_online_{SEED_TO_BASELINE_TRIAL[seed]}.json"


def assigned_high_per_window(raw: dict) -> list[tuple[int, frozenset[str]]]:
    out: list[tuple[int, frozenset[str]]] = []
    for entry in raw.get("allocation_history", []):
        w = int(entry.get("window", -1))
        names = [
            name
            for name, alloc in entry.get("allocation", {}).items()
            if alloc.get("assigned_tier") == "high"
        ]
        out.append((w, frozenset(names)))
    out.sort(key=lambda t: t[0])
    return out


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def per_label_jaccard(results_dir: Path, label: str, seed: int) -> dict[str, float]:
    abl_raw_path = online_raw_path(results_dir, label, seed)
    base_raw_path = baseline_online_raw_path(results_dir, seed)
    abl = json.loads(abl_raw_path.read_text(encoding="utf-8"))
    base = json.loads(base_raw_path.read_text(encoding="utf-8"))
    abl_windows = dict(assigned_high_per_window(abl))
    base_windows = dict(assigned_high_per_window(base))
    common = sorted(set(abl_windows) & set(base_windows))
    # Skip window 0 (no prior-window signal yet; both are seed/cold decisions).
    common = [w for w in common if w >= 1]
    j_values = [jaccard(abl_windows[w], base_windows[w]) for w in common]
    return {
        "windows_compared": len(j_values),
        "mean_jaccard": statistics.fmean(j_values) if j_values else 0.0,
        "min_jaccard": min(j_values) if j_values else 0.0,
    }


def load_seed(results_dir: Path, label: str, seed: int) -> dict[str, object]:
    apath = analysis_path(results_dir, label, seed)
    if not apath.exists():
        raise FileNotFoundError(f"missing analysis: {apath}")
    data = json.loads(apath.read_text(encoding="utf-8"))
    per = data.get("per_policy", {})
    online = per.get("online", {})
    static = per.get("static", {})
    cmp = data.get("online_vs_static", {})
    lag_info = online.get("adaptation_lag", {})
    raw_path = online_raw_path(results_dir, label, seed)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    high_count = int(raw.get("args", {}).get("high_count", 4))
    j = per_label_jaccard(results_dir, label, seed)
    return {
        "label": label,
        "seed": seed,
        "high_count": high_count,
        "failed_online": int(online.get("failed_tenants", 0)),
        "failed_static": int(static.get("failed_tenants", 0)),
        "high_p99_pct": float(cmp.get("online_vs_static_pct_high_write_p99_us", 0.0)),
        "high_p999_pct": float(cmp.get("online_vs_static_pct_high_write_p999_us", 0.0)),
        "high_tput_pct": float(cmp.get("online_vs_static_pct_high_write_throughput", 0.0)),
        "total_tput_pct": float(cmp.get("online_vs_static_pct_total_throughput", 0.0)),
        "bytes_per_write_pct": float(cmp.get("online_vs_static_pct_compact_output_bytes_per_write", 0.0)),
        "compact_bytes_pct": float(cmp.get("online_vs_static_pct_compact_output_bytes", 0.0)),
        "overlap_after_warmup": float(online.get("mean_high_overlap_after_warmup", 0.0)),
        "max_lag_windows": lag_info.get("max_lag_windows"),
        "lags_windows": lag_info.get("lags_windows", []),
        "windows_compared_for_jaccard": j["windows_compared"],
        "mean_jaccard_vs_saki": j["mean_jaccard"],
        "min_jaccard_vs_saki": j["min_jaccard"],
        "raw_path": str(raw_path),
        "analysis_path": str(apath),
    }


def aggregate(per_seed: list[dict]) -> dict[str, object]:
    def vals(key):
        return [float(s[key]) for s in per_seed]

    def stat(values):
        if not values:
            return {"n": 0}
        return {
            "n": len(values),
            "mean": statistics.fmean(values),
            "min": min(values),
            "max": max(values),
            "range": max(values) - min(values),
        }

    lags = [s["max_lag_windows"] for s in per_seed]
    finite_lags = [int(l) for l in lags if l is not None]
    return {
        "high_p99_pct": stat(vals("high_p99_pct")),
        "high_p999_pct": stat(vals("high_p999_pct")),
        "high_tput_pct": stat(vals("high_tput_pct")),
        "total_tput_pct": stat(vals("total_tput_pct")),
        "bytes_per_write_pct": stat(vals("bytes_per_write_pct")),
        "compact_bytes_pct": stat(vals("compact_bytes_pct")),
        "overlap_after_warmup": stat(vals("overlap_after_warmup")),
        "mean_jaccard_vs_saki": stat(vals("mean_jaccard_vs_saki")),
        "min_jaccard_vs_saki": stat(vals("min_jaccard_vs_saki")),
        "max_lag_seedwise": max(finite_lags) if finite_lags else None,
        "max_failed_static": max(int(s["failed_static"]) for s in per_seed) if per_seed else 0,
        "max_failed_online": max(int(s["failed_online"]) for s in per_seed) if per_seed else 0,
        "high_count": int(per_seed[0]["high_count"]) if per_seed else 4,
    }


def fmt_pct(stat_dict: dict, precision: int = 1) -> str:
    if stat_dict.get("n", 0) == 0:
        return "n/a"
    mean = stat_dict["mean"]
    lo = stat_dict.get("min", mean)
    hi = stat_dict.get("max", mean)
    return f"{mean:+.{precision}f}% [{lo:+.1f},{hi:+.1f}]"


def fmt_overlap(stat_dict: dict, high_count: int) -> str:
    if stat_dict.get("n", 0) == 0:
        return "n/a"
    return f"{stat_dict['mean']:.2f}/{high_count}"


def fmt_jaccard(stat_dict: dict) -> str:
    if stat_dict.get("n", 0) == 0:
        return "n/a"
    return f"{stat_dict['mean']:.2f} [{stat_dict['min']:.2f},{stat_dict['max']:.2f}]"


def reference_row(main_continuous_path: Path) -> dict[str, object] | None:
    if not main_continuous_path.exists():
        return None
    data = json.loads(main_continuous_path.read_text(encoding="utf-8"))
    # The main file has slightly different keys; mine the headline numbers.
    main = data.get("online_vs_static", {}) or data.get("means", {}) or data
    # Look for keys with the expected suffix.
    def f(key):
        return float(main.get(key, data.get(key, 0.0)) or 0.0)
    return {
        "label": "fixed SAKI (n=5, paper main)*",
        "high_p99_pct_text": main.get("high_write_p99_pct_text", ""),
        "high_tput_pct_text": main.get("high_write_throughput_pct_text", ""),
        "data_keys": list(main.keys()),
        "raw_path": str(main_continuous_path),
        "raw_payload": data,
    }


def render_markdown(payload: dict, ref: dict | None) -> str:
    md: list[str] = []
    md.append("# Coefficient-Robustness Ablation Aggregate")
    md.append("")
    md.append("Anchored at the published continuous main result")
    md.append("`embedded_demand2f_16t` (16 tenants, 4/8/4 split, per-tenant budgets")
    md.append("11/6/1 MB/s = 96 MB/s aggregate, 20s window, 8 windows, demand-mode online).")
    md.append("Only the demand-score coefficients are perturbed; everything else is held.")
    md.append("Each ablation seed is paired with the published paper-static seed of the")
    md.append("matching trial (seed 1 -> demand2f_16t, seed 2 -> _b, seed 3 -> _c), so the")
    md.append("% deltas are per-seed paired with that static.")
    md.append("")
    md.append("Cells show `mean [min, max]` across n=3 seeds; we do not quote a 95% CI at n=3")
    md.append("for the ablation (the published main result uses n=5 for that claim).")
    md.append("")
    md.append("| label | High P99 % | High tput % | Total tput % | Bytes/write % | Overlap | Max lag (w) | Mean Jaccard vs SAKI |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for entry in payload["labels"]:
        if entry.get("n", 0) == 0:
            md.append(f"| {entry['label']} | no-data | no-data | no-data | no-data | n/a | n/a | n/a |")
            continue
        s = entry["stats"]
        hc = s["high_count"]
        max_lag = s["max_lag_seedwise"]
        max_lag_str = "n/a" if max_lag is None else str(max_lag)
        md.append(
            f"| {entry['label']} | "
            f"{fmt_pct(s['high_p99_pct'])} | "
            f"{fmt_pct(s['high_tput_pct'])} | "
            f"{fmt_pct(s['total_tput_pct'])} | "
            f"{fmt_pct(s['bytes_per_write_pct'])} | "
            f"{fmt_overlap(s['overlap_after_warmup'], hc)} | "
            f"{max_lag_str} | "
            f"{fmt_jaccard(s['mean_jaccard_vs_saki'])} |"
        )
    if ref is not None and ref.get("ref_high_p99") is not None:
        md.append(
            f"| **{ref['label']}** | "
            f"{ref['ref_high_p99']} | "
            f"{ref['ref_high_tput']} | "
            f"{ref['ref_total_tput']} | "
            f"{ref['ref_bytes_per_write']} | "
            f"{ref['ref_overlap']} | "
            f"{ref['ref_max_lag']} | "
            f"1.00 (self) |"
        )
        md.append("")
        md.append("`*` reference row reproduced from `main_continuous_demand2f.json` (n=5).")
        md.append("It is NOT a same-batch rerun; values frozen at paper-main runtime.")
    md.append("")
    md.append("Each ablation cell is per-seed paired with the published paper-static seed.")
    md.append("Per-seed raw analysis JSONs are listed in the companion JSON file.")
    return "\n".join(md) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--main-continuous", type=Path,
                        default=ROOT / "remote-results/paper_tables/main_continuous_demand2f.json")
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--labels", nargs="+", default=LABEL_ORDER)
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-markdown", type=Path, required=True)
    args = parser.parse_args()

    payload: dict[str, object] = {"labels": []}
    for label in args.labels:
        per_seed: list[dict] = []
        missing = []
        for seed in args.seeds:
            try:
                per_seed.append(load_seed(args.results_dir, label, seed))
            except FileNotFoundError as e:
                missing.append(str(e))
        if not per_seed:
            payload["labels"].append({"label": label, "n": 0, "missing": missing})
            continue
        agg = aggregate(per_seed)
        payload["labels"].append({
            "label": label,
            "n": len(per_seed),
            "seeds": [s["seed"] for s in per_seed],
            "per_seed": per_seed,
            "stats": agg,
            "missing": missing,
        })

    ref_render: dict[str, object] = {"label": "fixed SAKI (n=5, paper main)*"}
    if args.main_continuous.exists():
        ref_data = json.loads(args.main_continuous.read_text(encoding="utf-8"))
        payload["main_continuous_ref"] = ref_data
        agg = ref_data.get("aggregates", {})

        def agg_mean(key: str) -> float | None:
            v = agg.get(key, {})
            if isinstance(v, dict) and isinstance(v.get("mean"), (int, float)):
                return float(v["mean"])
            return None

        def agg_max(key: str) -> float | None:
            v = agg.get(key, {})
            if isinstance(v, dict) and isinstance(v.get("max"), (int, float)):
                return float(v["max"])
            return None

        def fmtp(v: float | None) -> str:
            return "see JSON" if v is None else f"{v:+.1f}%"

        ref_render["ref_high_p99"] = fmtp(agg_mean("online_vs_static_pct_high_write_p99_us"))
        ref_render["ref_high_tput"] = fmtp(agg_mean("online_vs_static_pct_high_write_throughput"))
        ref_render["ref_total_tput"] = fmtp(agg_mean("online_vs_static_pct_total_throughput"))
        ref_render["ref_bytes_per_write"] = fmtp(agg_mean("online_vs_static_pct_compact_output_bytes_per_write"))
        overlap_mean = agg_mean("mean_high_overlap_after_warmup")
        ref_render["ref_overlap"] = "see JSON" if overlap_mean is None else f"{overlap_mean:.2f}/4"
        max_lag = agg_max("adaptation_max_lag")
        ref_render["ref_max_lag"] = "see JSON" if max_lag is None else str(int(max_lag))
    md = render_markdown(payload, ref_render)
    args.out_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.out_markdown.write_text(md, encoding="utf-8")
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
