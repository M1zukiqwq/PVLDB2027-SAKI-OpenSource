#!/usr/bin/env python3
"""Generate the paper-facing public trace qualification summary.

This script only reads existing CacheLib and Baleen diagnostic artifacts.  It
does not run RocksDB, download traces, change the budget contract, or edit the
paper.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


DEFAULT_CACHELIB = ROOT / "remote-results/cachelib_external_v1b/results/cachelib_external_v1_cachelib_v1b_fixhot_t4_n5_aggregate.json"
DEFAULT_BALEEN_AUDIT = ROOT / "remote-results/trace_family_audit/baleen_0.1_audit_report.json"
DEFAULT_BALEEN_STATIC = ROOT / "remote-results/trace_family_audit/baleen_0.1_static_actuatable_addendum.json"
DEFAULT_BALEEN_SMOKE = ROOT / "remote-results/baleen_static_smoke/results/baleen_static_smoke_Region6_w83982457_summary.json"
DEFAULT_OUT_JSON = ROOT / "remote-results/paper_tables/public_trace_qualification.json"
DEFAULT_OUT_MD = ROOT / "remote-results/paper_tables/public_trace_qualification.md"


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def rounded(value: float | int | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def mean_for(summary: dict[str, Any], metric: str, policy: str = "online") -> float:
    return float(summary[metric][policy]["mean"])


def minmax_for(summary: dict[str, Any], metric: str, policy: str = "online") -> tuple[float, float]:
    values = summary[metric][policy]["values"]
    return float(min(values)), float(max(values))


def build_summary(cachelib: dict[str, Any], baleen_audit: dict[str, Any], baleen_static: dict[str, Any], baleen_smoke: dict[str, Any]) -> dict[str, Any]:
    cache_summary = cachelib["all_selected_summary"]
    cache_offered_min, cache_offered_max = minmax_for(cache_summary, "offered_write_mbps")
    cache_completed_min, cache_completed_max = minmax_for(cache_summary, "completed_write_mbps")
    cache_compact_min, cache_compact_max = minmax_for(cache_summary, "mean_compact_output_mbps")
    cache_pending_min, cache_pending_max = minmax_for(cache_summary, "pending_compaction_bytes_p95_mib")

    baleen_segments = baleen_static["segments"]
    max_static_window = max(
        max(seg["static_actuatable_write_mbps_per_window"]) for seg in baleen_segments
    )
    max_oracle_window = max(
        max(seg["oracle_tiered_actuatable_write_mbps_per_window"]) for seg in baleen_segments
    )
    max_active_window = max(
        max(seg["active_write_tenants_per_window"]) for seg in baleen_segments
    )
    baleen_dist = baleen_static["summary"]["static_actuatable_mean_mbps_distribution"]
    baleen_active_dist = baleen_static["summary"]["active_write_tenants_median_distribution"]

    cachelib_row = {
        "trace_family": "Meta CacheLib kvcache/202401",
        "trace_role": "cache-front-end key-value request stream",
        "qualification_status": "negative_smoke_only",
        "candidate_screen": "5 selected drifting 16-tenant segments",
        "rocksdb_status": "n=5 trace-shaped replay ran; stress-pass subset n=0",
        "stress_gate": "mean compact >=60 MB/s or pending p95 >=32 MiB",
        "stress_pass_segments": cachelib["n_stress"],
        "offered_write_mbps_mean": rounded(mean_for(cache_summary, "offered_write_mbps")),
        "offered_write_mbps_range": [rounded(cache_offered_min), rounded(cache_offered_max)],
        "completed_write_mbps_mean": rounded(mean_for(cache_summary, "completed_write_mbps")),
        "completed_write_mbps_range": [rounded(cache_completed_min), rounded(cache_completed_max)],
        "completion_ratio_mean": rounded(mean_for(cache_summary, "completion_ratio"), 4),
        "compact_output_mbps_mean": rounded(mean_for(cache_summary, "mean_compact_output_mbps")),
        "compact_output_mbps_range": [rounded(cache_compact_min), rounded(cache_compact_max)],
        "pending_p95_mib_mean": rounded(mean_for(cache_summary, "pending_compaction_bytes_p95_mib")),
        "pending_p95_mib_range": [rounded(cache_pending_min), rounded(cache_pending_max)],
        "tracking_signal": {
            "online_overlap_after_warmup": rounded(mean_for(cache_summary, "overlap_after_warmup_mean"), 2),
            "static_biased_overlap_after_warmup": rounded(cache_summary["overlap_after_warmup_mean"]["static_biased"]["mean"], 2),
            "online_max_lag": rounded(mean_for(cache_summary, "max_lag_after_warmup"), 2),
            "static_biased_max_lag": rounded(cache_summary["max_lag_after_warmup"]["static_biased"]["mean"], 2),
        },
        "stop_reason": "failed pre-registered engine-stress gate after replay; cache request stream did not become sustained RocksDB compaction pressure",
        "paper_use": "trace-shaped plumbing and tracking sanity check, not production-trace validation",
    }

    baleen_row = {
        "trace_family": "Meta Tectonic/Baleen storage_0.1 Region5-7",
        "trace_role": "storage/flash-cache block trace with user axis",
        "qualification_status": "stop_before_second_smoke",
        "candidate_screen": (
            f'{baleen_audit["candidate_summary"]["independent_candidate_segments"]} independent '
            "segments after aggregate supply/drift screen"
        ),
        "rocksdb_status": "one static-only diagnostic smoke failed Gate B; no n=5 and no second smoke recommended",
        "stress_gate": "static all-mid actuatable mean >=70 MB/s and >=6/8 windows >=70 MB/s",
        "stress_pass_segments": baleen_static["summary"]["static_smoke_eligible_segments"],
        "aggregate_scaled_candidates": baleen_audit["candidate_summary"]["scaled_candidate_segments"],
        "segment_locality_pass": baleen_static["summary"]["segment_locality_pass"],
        "static_actuatable_mean_mbps_distribution": {
            "min": rounded(baleen_dist["min"]),
            "p50": rounded(baleen_dist["p50"]),
            "p95": rounded(baleen_dist["p95"]),
            "max": rounded(baleen_dist["max"]),
        },
        "median_active_write_tenants_distribution": {
            "min": rounded(baleen_active_dist["min"], 1),
            "p50": rounded(baleen_active_dist["p50"], 1),
            "p95": rounded(baleen_active_dist["p95"], 1),
            "max": rounded(baleen_active_dist["max"], 1),
        },
        "max_single_window_static_actuatable_mbps": rounded(max_static_window),
        "max_single_window_oracle_tiered_actuatable_mbps": rounded(max_oracle_window),
        "max_active_write_tenants_any_window": int(max_active_window),
        "diagnostic_smoke": {
            "segment_id": "Region6_w83982457",
            "offered_write_mbps": rounded(baleen_smoke["metrics"]["offered_write_mb_s"]),
            "rate_limiter_actual_mbps": rounded(baleen_smoke["metrics"]["rate_limiter_actual_mb_s"]),
            "completed_logical_write_mbps": rounded(baleen_smoke["metrics"]["completed_logical_write_mb_s"]),
            "compact_output_mbps": rounded(baleen_smoke["metrics"]["compact_output_mb_s"]),
            "pending_p95_mib": rounded(baleen_smoke["metrics"]["pending_compaction_bytes_p95_mib"]),
            "completion_ratio": rounded(baleen_smoke["metrics"]["completion_ratio"], 4),
        },
        "stop_reason": "aggregate scaled trace supply is concentrated in too few active tenant/windows to use the fixed per-tenant caps",
        "paper_use": "trace-family qualification negative, not controller comparison",
    }

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "budget_contract": "high/mid/low = 11/7/3 MB/s; 16-tenant aggregate = 112 MB/s",
        "verdict": "No current public trace result should be promoted to production-trace validation.",
        "recommendation": (
            "Keep the controlled RocksDB workload matrix as the main externality extension; "
            "report CacheLib and Baleen as qualification negatives."
        ),
        "rows": [cachelib_row, baleen_row],
        "source_artifacts": [
            str(DEFAULT_CACHELIB.relative_to(ROOT)),
            str(DEFAULT_BALEEN_AUDIT.relative_to(ROOT)),
            str(DEFAULT_BALEEN_STATIC.relative_to(ROOT)),
            str(DEFAULT_BALEEN_SMOKE.relative_to(ROOT)),
        ],
    }


def fmt_range(values: list[float | None]) -> str:
    return f"{values[0]}--{values[1]}"


def write_markdown(report: dict[str, Any], out_path: Path) -> None:
    cache, baleen = report["rows"]
    lines = [
        "# Public Trace Qualification Summary",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- budget_contract: `{report['budget_contract']}`",
        f"- verdict: **{report['verdict']}**",
        "",
        "## Qualification Table",
        "",
        "| Trace family | Screen status | RocksDB status | Key diagnostic | Paper use |",
        "|---|---|---|---|---|",
        (
            f"| {cache['trace_family']} | {cache['candidate_screen']} | "
            f"{cache['rocksdb_status']} | offered {fmt_range(cache['offered_write_mbps_range'])} MB/s; "
            f"completed {fmt_range(cache['completed_write_mbps_range'])} MB/s; "
            f"compact {fmt_range(cache['compact_output_mbps_range'])} MB/s; "
            f"pending p95 {fmt_range(cache['pending_p95_mib_range'])} MiB | {cache['paper_use']} |"
        ),
        (
            f"| {baleen['trace_family']} | {baleen['candidate_screen']}; "
            f"{baleen['segment_locality_pass']}/146 pass segment locality | "
            f"{baleen['rocksdb_status']} | static-actuatable mean p50/p95/max "
            f"{baleen['static_actuatable_mean_mbps_distribution']['p50']}/"
            f"{baleen['static_actuatable_mean_mbps_distribution']['p95']}/"
            f"{baleen['static_actuatable_mean_mbps_distribution']['max']} MB/s; "
            f"median active tenants p50/p95/max "
            f"{baleen['median_active_write_tenants_distribution']['p50']}/"
            f"{baleen['median_active_write_tenants_distribution']['p95']}/"
            f"{baleen['median_active_write_tenants_distribution']['max']} | {baleen['paper_use']} |"
        ),
        "",
        "## Interpretation",
        "",
        "- CacheLib validates the replay and tracking plumbing, but all five selected segments fail the engine-stress gate.",
        "- Baleen has aggregate scaled supply, but 0/146 independent candidates have enough static-actuatable supply under the fixed 7 MB/s all-mid per-tenant cap.",
        "- Neither result changes the 112 MB/s budget contract or supports a production-trace performance claim.",
        "",
        "## Sources",
        "",
    ]
    lines.extend(f"- `{path}`" for path in report["source_artifacts"])
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cachelib", type=Path, default=DEFAULT_CACHELIB)
    parser.add_argument("--baleen-audit", type=Path, default=DEFAULT_BALEEN_AUDIT)
    parser.add_argument("--baleen-static", type=Path, default=DEFAULT_BALEEN_STATIC)
    parser.add_argument("--baleen-smoke", type=Path, default=DEFAULT_BALEEN_SMOKE)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    parser.add_argument("--out-md", type=Path, default=DEFAULT_OUT_MD)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    for out_path in (args.out_json, args.out_md):
        if out_path.exists() and not args.overwrite:
            raise SystemExit(f"{out_path} exists; pass --overwrite to replace it")

    report = build_summary(
        load_json(args.cachelib),
        load_json(args.baleen_audit),
        load_json(args.baleen_static),
        load_json(args.baleen_smoke),
    )

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(report, args.out_md)


if __name__ == "__main__":
    main()
