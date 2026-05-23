#!/usr/bin/env python3
"""Compute static-actuatable write supply for Baleen candidate segments.

This is a trace-only diagnostic addendum. It reads the existing independent
candidate list plus the segment-locality addendum, rescans the already available
Baleen 0.1% trace sample, and writes new JSON/Markdown artifacts. It does not
prepare or run a RocksDB smoke.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import statistics
import sys
import tarfile
from pathlib import Path
from typing import Iterable

MB = 1024 * 1024
BALEEN_WRITE_OPS = {"3", "4", "6", "PUT_TEMP", "PUT_PERM", "PUT_NOT_INIT"}


def parse_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except ValueError:
            return default


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except ValueError:
        return default


def pct(values: Iterable[float], q: float) -> float | None:
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    pos = (q / 100.0) * (len(vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def mean(values: Iterable[float]) -> float:
    vals = list(values)
    return statistics.fmean(vals) if vals else 0.0


def median(values: Iterable[float]) -> float | None:
    vals = list(values)
    return float(statistics.median(vals)) if vals else None


def normalize_op(token: str) -> str:
    op = str(token).strip().upper()
    if op.endswith(".0"):
        op = op[:-2]
    return op


def read_header_from_lines(lines: Iterable[str], source: str) -> list[str]:
    required = {"block_id", "io_offset", "io_size", "op_time", "op_name", "user_name"}
    for raw in lines:
        line = raw.strip()
        if not line.startswith("#"):
            continue
        cols = line[1:].strip().split()
        if required.issubset(set(cols)):
            return cols
    raise ValueError(f"could not find Baleen schema in {source}")


class TraceReader:
    def __init__(self, root: Path | None, tar_path: Path | None, trace_pattern: str):
        self.root = root
        self.tar_path = tar_path
        self.trace_pattern = trace_pattern
        self._tar: tarfile.TarFile | None = None
        self._members_by_region: dict[str, dict[str, tarfile.TarInfo]] = {}
        if tar_path is not None:
            self._tar = tarfile.open(tar_path, "r:gz")
            for member in self._tar.getmembers():
                parts = Path(member.name).parts
                if len(parts) < 3:
                    continue
                region = parts[-2]
                name = parts[-1]
                if name == "full.header" or name == trace_pattern:
                    self._members_by_region.setdefault(region, {})[name] = member

    def close(self) -> None:
        if self._tar is not None:
            self._tar.close()

    def header(self, trace_id: str) -> list[str]:
        if self._tar is not None:
            member = self._members_by_region.get(trace_id, {}).get("full.header")
            if member is None:
                raise FileNotFoundError(f"missing {trace_id}/full.header in {self.tar_path}")
            fh = self._tar.extractfile(member)
            if fh is None:
                raise FileNotFoundError(f"could not read {member.name}")
            with fh:
                lines = (b.decode("utf-8", "replace") for b in fh)
                return read_header_from_lines(lines, member.name)
        trace_dir = self._find_trace_dir(trace_id)
        return read_header_from_lines(trace_dir.joinpath("full.header").read_text(errors="replace").splitlines(), str(trace_dir))

    def iter_rows(self, trace_id: str):
        if self._tar is not None:
            member = self._members_by_region.get(trace_id, {}).get(self.trace_pattern)
            if member is None:
                raise FileNotFoundError(f"missing {trace_id}/{self.trace_pattern} in {self.tar_path}")
            fh = self._tar.extractfile(member)
            if fh is None:
                raise FileNotFoundError(f"could not read {member.name}")
            with fh:
                for raw in fh:
                    line = raw.decode("utf-8", "replace").strip()
                    if line and not line.startswith("#"):
                        yield line.split()
            return
        trace_dir = self._find_trace_dir(trace_id)
        trace_file = trace_dir / self.trace_pattern
        if not trace_file.exists():
            raise FileNotFoundError(f"missing {trace_file}")
        with trace_file.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if line and not line.startswith("#"):
                    yield line.split()

    def source_description(self) -> str:
        if self.tar_path is not None:
            return str(self.tar_path)
        return str(self.root)

    def _find_trace_dir(self, trace_id: str) -> Path:
        if self.root is None:
            raise ValueError("trace root is not configured")
        matches = sorted(p for p in self.root.rglob(trace_id) if p.is_dir() and (p / "full.header").exists())
        if not matches:
            raise FileNotFoundError(f"could not find {trace_id} under {self.root}")
        return matches[-1]


def load_candidates(path: Path) -> tuple[dict, list[dict]]:
    payload = json.loads(path.read_text())
    candidates = payload.get("candidate_segments")
    if not isinstance(candidates, list):
        raise ValueError(f"{path} does not contain candidate_segments")
    return payload, candidates


def load_locality(path: Path) -> tuple[dict, dict[str, dict]]:
    payload = json.loads(path.read_text())
    rows = payload.get("segments")
    if not isinstance(rows, list):
        raise ValueError(f"{path} does not contain segments")
    return payload, {str(row["segment_id"]): row for row in rows}


def initialize_states(candidates: list[dict], window_sec: int) -> tuple[list[dict], dict[str, dict[int, list[int]]]]:
    states = []
    by_trace_window: dict[str, dict[int, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, cand in enumerate(candidates):
        selected = [str(u) for u in cand.get("selected_user_ids", [])]
        if not selected:
            raise ValueError(f"candidate {cand.get('segment_id', i)} missing selected_user_ids")
        start_window = int(cand.get("start_window", math.floor(float(cand["start_time"]) / window_sec)))
        end_window = int(cand.get("end_window", math.floor((float(cand["end_time"]) - 1e-9) / window_sec)))
        windows = end_window - start_window + 1
        if windows <= 0:
            raise ValueError(f"candidate {cand.get('segment_id', i)} has invalid window range")
        state = {
            "candidate": cand,
            "selected": selected,
            "selected_set": set(selected),
            "start_window": start_window,
            "end_window": end_window,
            "per_window_user_bytes": [collections.Counter() for _ in range(windows)],
        }
        states.append(state)
        for win in range(start_window, end_window + 1):
            by_trace_window[str(cand["trace_id"])][win].append(i)
    return states, by_trace_window


def scan_traces(reader: TraceReader, states: list[dict], by_trace_window: dict[str, dict[int, list[int]]], window_sec: int) -> dict:
    summary = {}
    for trace_id, windows in sorted(by_trace_window.items()):
        header = reader.header(trace_id)
        idx = {name: i for i, name in enumerate(header)}
        required = ["io_size", "op_time", "op_name", "user_name"]
        missing = [name for name in required if name not in idx]
        if missing:
            raise ValueError(f"{trace_id} missing required columns: {missing}")
        records = 0
        write_records = 0
        matched_writes = 0
        for parts in reader.iter_rows(trace_id):
            records += 1
            if len(parts) < len(header):
                continue
            op = normalize_op(parts[idx["op_name"]])
            if op not in BALEEN_WRITE_OPS:
                continue
            write_records += 1
            op_time = parse_float(parts[idx["op_time"]])
            win = int(math.floor(op_time / window_sec))
            state_indexes = windows.get(win)
            if not state_indexes:
                continue
            user = parts[idx["user_name"]]
            io_size = max(0, parse_int(parts[idx["io_size"]]))
            op_count = parse_int(parts[idx["op_count"]], 1) if "op_count" in idx else 1
            op_count = max(1, op_count)
            bytes_ = io_size * op_count
            for state_idx in state_indexes:
                state = states[state_idx]
                cand = state["candidate"]
                if user not in state["selected_set"]:
                    continue
                if not (float(cand["start_time"]) <= op_time < float(cand["end_time"])):
                    continue
                local_w = win - state["start_window"]
                if 0 <= local_w < len(state["per_window_user_bytes"]):
                    state["per_window_user_bytes"][local_w][user] += bytes_
                    matched_writes += 1
        summary[trace_id] = {
            "records_scanned": records,
            "write_records": write_records,
            "matched_candidate_selected_writes": matched_writes,
        }
        print(
            f"[static-actuatable] {trace_id} records={records:,} writes={write_records:,} "
            f"matched_selected_writes={matched_writes:,}",
            file=sys.stderr,
            flush=True,
        )
    return summary


def tier_assignment(
    selected: list[str],
    scaled_by_user: dict[str, float],
    high_count: int,
    low_count: int,
) -> dict[str, str]:
    order = {user: i for i, user in enumerate(selected)}
    ranked = sorted(selected, key=lambda user: (-scaled_by_user.get(user, 0.0), order[user]))
    high = set(ranked[:high_count])
    low = set(ranked[-low_count:]) if low_count > 0 else set()
    out = {}
    for user in selected:
        if user in high:
            out[user] = "high"
        elif user in low:
            out[user] = "low"
        else:
            out[user] = "mid"
    return out


def risk_label(row: dict, sustained_min_windows: int) -> str:
    margin = float(row["static_actuatable_mean_mbps"]) - 70.0
    if row["static_windows_geq70"] < 8 or margin < 10 or float(row["active_write_tenants_median"]) < 12:
        return "moderate: eligible but close to the static-actuatable supply gate"
    if row.get("capped_loss_ratio_p50") is not None and row["capped_loss_ratio_p50"] > 0.20:
        return "moderate: aggregate supply still loses material mass to per-tenant caps"
    if row["static_windows_geq70"] >= sustained_min_windows and margin >= 20:
        return "low-to-moderate: strongest trace-only static-actuatable candidate"
    return "moderate"


def compute_rows(
    states: list[dict],
    locality_by_segment: dict[str, dict],
    global_scale: float,
    window_sec: int,
    high_count: int,
    low_count: int,
    static_cap: float,
    high_cap: float,
    mid_cap: float,
    low_cap: float,
    sustained_min_windows: int,
) -> list[dict]:
    rows = []
    for state in states:
        cand = state["candidate"]
        segment_id = str(cand["segment_id"])
        selected = state["selected"]
        loc = locality_by_segment.get(segment_id, {})
        per_window_scaled = []
        active_counts = []
        static_act = []
        oracle_act = []
        aggregate_scaled = []
        capped_loss = []
        oracle_tiers = []
        for counter in state["per_window_user_bytes"]:
            scaled_by_user = {
                user: counter.get(user, 0.0) / window_sec / MB * global_scale
                for user in selected
            }
            per_window_scaled.append({user: scaled_by_user[user] for user in selected})
            active = sum(1 for v in scaled_by_user.values() if v > 0.0)
            active_counts.append(active)
            agg = sum(scaled_by_user.values())
            aggregate_scaled.append(agg)
            static_v = sum(min(static_cap, v) for v in scaled_by_user.values())
            static_act.append(static_v)
            tiers = tier_assignment(selected, scaled_by_user, high_count, low_count)
            oracle_tiers.append(tiers)
            cap_by_tier = {"high": high_cap, "mid": mid_cap, "low": low_cap}
            oracle_act.append(sum(min(cap_by_tier[tiers[user]], scaled_by_user[user]) for user in selected))
            capped_loss.append(None if agg <= 0.0 else 1.0 - static_v / agg)

        static_mean = mean(static_act)
        row = {
            "segment_id": segment_id,
            "trace_id": cand.get("trace_id"),
            "start_window": cand.get("start_window"),
            "end_window": cand.get("end_window"),
            "start_time": cand.get("start_time"),
            "end_time": cand.get("end_time"),
            "selected_user_ids": selected,
            "raw_mean_write_mbps": cand.get("raw_mean_write_mbps"),
            "scaled_mean_write_mbps": cand.get("scaled_mean_write_mbps"),
            "scaled_windows_geq70": cand.get("scaled_windows_geq70"),
            "segment_locality_score": loc.get("segment_locality_score"),
            "segment_locality_pass": bool(loc.get("segment_locality_pass", False)),
            "segment_locality_pass_reasons": loc.get("segment_locality_pass_reasons", []),
            "write_tenants_segment": loc.get("write_tenants", cand.get("n_write_tenants")),
            "per_window_scaled_tenant_write_mbps": per_window_scaled,
            "active_write_tenants_per_window": active_counts,
            "static_actuatable_write_mbps_per_window": static_act,
            "oracle_tiered_actuatable_write_mbps_per_window": oracle_act,
            "oracle_tiered_assignment_per_window": oracle_tiers,
            "aggregate_scaled_write_mbps_per_window": aggregate_scaled,
            "original_scaled_window_write_mbps": cand.get("scaled_window_write_mbps"),
            "capped_loss_ratio_per_window": capped_loss,
            "static_windows_geq70": sum(1 for v in static_act if v >= 70.0),
            "static_windows_geq60": sum(1 for v in static_act if v >= 60.0),
            "active_write_tenants_min": min(active_counts) if active_counts else None,
            "active_write_tenants_median": median(active_counts),
            "active_write_tenants_p95": pct(active_counts, 95),
            "static_actuatable_mean_mbps": static_mean,
            "static_actuatable_p50_mbps": pct(static_act, 50),
            "static_actuatable_p95_mbps": pct(static_act, 95),
            "oracle_tiered_actuatable_mean_mbps": mean(oracle_act),
            "aggregate_scaled_mean_write_mbps": mean(aggregate_scaled),
            "capped_loss_ratio_mean_nonzero": mean([v for v in capped_loss if v is not None]),
            "capped_loss_ratio_p50": pct([v for v in capped_loss if v is not None], 50),
            "static_smoke_eligible": False,
            "static_smoke_eligibility_reasons": [],
        }
        checks = [
            ("segment_locality_pass", row["segment_locality_pass"]),
            ("static_windows_geq70>=6/8", row["static_windows_geq70"] >= sustained_min_windows),
            ("median_active_write_tenants>=9", float(row["active_write_tenants_median"] or 0.0) >= 9.0),
            ("static_actuatable_mean_mbps>=70", row["static_actuatable_mean_mbps"] >= 70.0),
        ]
        row["static_smoke_eligible"] = all(ok for _, ok in checks)
        row["static_smoke_eligibility_reasons"] = [name for name, ok in checks if not ok]
        if row["static_smoke_eligible"]:
            row["expected_risk"] = risk_label(row, sustained_min_windows)
        rows.append(row)
    return rows


def top_recommendations(rows: list[dict], n: int) -> list[dict]:
    eligible = [row for row in rows if row["static_smoke_eligible"]]
    ranked = sorted(
        eligible,
        key=lambda row: (
            -row["static_windows_geq70"],
            -float(row["static_actuatable_mean_mbps"]),
            -float(row["active_write_tenants_median"] or 0.0),
            -float(row.get("segment_locality_score") or 0.0),
            str(row["trace_id"]),
            int(row["start_window"]),
        ),
    )
    return [
        {
            "segment_id": row["segment_id"],
            "trace_id": row["trace_id"],
            "start_time": row["start_time"],
            "end_time": row["end_time"],
            "selected_user_ids": row["selected_user_ids"],
            "static_actuatable_mean_mbps": row["static_actuatable_mean_mbps"],
            "static_actuatable_p50_mbps": row["static_actuatable_p50_mbps"],
            "static_actuatable_p95_mbps": row["static_actuatable_p95_mbps"],
            "static_windows_geq70": row["static_windows_geq70"],
            "static_windows_geq60": row["static_windows_geq60"],
            "active_write_tenants_median": row["active_write_tenants_median"],
            "segment_locality_score": row["segment_locality_score"],
            "expected_risk": row.get("expected_risk", "moderate"),
        }
        for row in ranked[:n]
    ]


def summarize(rows: list[dict], recommendations: list[dict]) -> dict:
    static_means = [row["static_actuatable_mean_mbps"] for row in rows]
    active_medians = [float(row["active_write_tenants_median"] or 0.0) for row in rows]
    eligible_count = len([row for row in rows if row["static_smoke_eligible"]])
    return {
        "segments_total": len(rows),
        "segment_locality_pass": sum(1 for row in rows if row["segment_locality_pass"]),
        "static_windows_geq70_ge6": sum(1 for row in rows if row["static_windows_geq70"] >= 6),
        "static_windows_geq60_ge6": sum(1 for row in rows if row["static_windows_geq60"] >= 6),
        "median_active_write_tenants_ge9": sum(1 for row in rows if float(row["active_write_tenants_median"] or 0.0) >= 9.0),
        "static_mean_actuatable_ge70": sum(1 for row in rows if row["static_actuatable_mean_mbps"] >= 70.0),
        "static_smoke_eligible_segments": eligible_count,
        "static_actuatable_mean_mbps_distribution": {
            "min": min(static_means) if static_means else None,
            "p50": pct(static_means, 50),
            "p95": pct(static_means, 95),
            "max": max(static_means) if static_means else None,
        },
        "active_write_tenants_median_distribution": {
            "min": min(active_medians) if active_medians else None,
            "p50": pct(active_medians, 50),
            "p95": pct(active_medians, 95),
            "max": max(active_medians) if active_medians else None,
        },
        "top_static_actuatable_segments": [
            {
                "segment_id": row["segment_id"],
                "trace_id": row["trace_id"],
                "static_actuatable_mean_mbps": row["static_actuatable_mean_mbps"],
                "static_windows_geq70": row["static_windows_geq70"],
                "active_write_tenants_median": row["active_write_tenants_median"],
                "scaled_mean_write_mbps": row["scaled_mean_write_mbps"],
                "segment_locality_score": row["segment_locality_score"],
                "static_smoke_eligible": row["static_smoke_eligible"],
                "static_smoke_eligibility_reasons": row["static_smoke_eligibility_reasons"],
            }
            for row in sorted(rows, key=lambda r: (-r["static_actuatable_mean_mbps"], str(r["trace_id"]), int(r["start_window"])))[:10]
        ],
        "recommendation": (
            "run_one_second_static_smoke_candidate_only_after_user_confirmation"
            if recommendations
            else "stop_baleen_static_policy_path"
        ),
        "next_step_recommendations": (
            [
                "Do not run a second Baleen static smoke from these 146 candidates.",
                "Treat the Baleen static-policy path as stopped: static-actuatable supply, not aggregate trace supply, is the blocker.",
                "Consider Tencent next, or run a harness-only synthetic all-mid diagnostic control before any new trace-family smoke.",
            ]
            if eligible_count == 0
            else [
                "A second Baleen static smoke is trace-only eligible, but should run only after explicit user confirmation.",
                "Keep high/mid/low budgets at 11/7/3 MB/s and preserve the 112 MB/s aggregate budget.",
            ]
        ),
        "recommended_segments": recommendations,
    }


def render_md(report: dict) -> str:
    s = report["summary"]
    lines = []
    lines.append("# Baleen Static-Actuatable Supply Addendum\n")
    lines.append("## Verdict\n")
    if s["static_smoke_eligible_segments"] > 0:
        first = s["recommended_segments"][0]
        lines.append(
            f"- Baleen still has {s['static_smoke_eligible_segments']} trace-only eligible static-smoke candidate(s)."
        )
        lines.append(f"- Top recommendation, pending user confirmation: `{first['segment_id']}`.")
    else:
        lines.append("- Baleen main line should stop for the static policy path.")
        lines.append("- Reason: static-actuatable supply is insufficient even when aggregate scaled trace supply passes Gate A.")
    lines.append("- This addendum does not run RocksDB, does not run n=5, and does not change the 112 MB/s budget contract.")
    lines.append("")
    lines.append("## Eligibility Counts\n")
    lines.append("| check | count |")
    lines.append("|---|---:|")
    lines.append(f"| total independent candidates | {s['segments_total']} |")
    lines.append(f"| segment locality pass | {s['segment_locality_pass']} |")
    lines.append(f"| static_windows_geq70 >= 6/8 | {s['static_windows_geq70_ge6']} |")
    lines.append(f"| static_windows_geq60 >= 6/8 | {s['static_windows_geq60_ge6']} |")
    lines.append(f"| median active write tenants >= 9 | {s['median_active_write_tenants_ge9']} |")
    lines.append(f"| static mean actuatable >= 70 MB/s | {s['static_mean_actuatable_ge70']} |")
    lines.append(f"| all new static eligibility checks pass | {s['static_smoke_eligible_segments']} |")
    lines.append("")
    dist = s["static_actuatable_mean_mbps_distribution"]
    active_dist = s["active_write_tenants_median_distribution"]
    lines.append("## Distributions\n")
    lines.append("| metric | min | p50 | p95 | max |")
    lines.append("|---|---:|---:|---:|---:|")
    lines.append(
        f"| static actuatable mean MB/s | {dist['min']:.3f} | {dist['p50']:.3f} | {dist['p95']:.3f} | {dist['max']:.3f} |"
    )
    lines.append(
        f"| median active write tenants | {active_dist['min']:.1f} | {active_dist['p50']:.1f} | {active_dist['p95']:.1f} | {active_dist['max']:.1f} |"
    )
    lines.append("")
    lines.append("## Top Static-Actuatable Segments\n")
    lines.append("| segment_id | static mean | static >=70 windows | median active tenants | scaled mean | locality | eligible | missing checks |")
    lines.append("|---|---:|---:|---:|---:|---:|---|---|")
    for row in s["top_static_actuatable_segments"]:
        missing = ", ".join(row["static_smoke_eligibility_reasons"]) or "-"
        lines.append(
            f"| `{row['segment_id']}` | {row['static_actuatable_mean_mbps']:.3f} "
            f"| {row['static_windows_geq70']} | {row['active_write_tenants_median']:.1f} "
            f"| {float(row['scaled_mean_write_mbps']):.3f} | {float(row['segment_locality_score'] or 0.0):.4f} "
            f"| {row['static_smoke_eligible']} | {missing} |"
        )
    if s["recommended_segments"]:
        lines.append("")
        lines.append("## Recommended Segments\n")
        lines.append("| segment_id | selected users | static mean/p50/p95 | static >=70 windows | median active tenants | locality | expected risk |")
        lines.append("|---|---|---:|---:|---:|---:|---|")
        for row in s["recommended_segments"]:
            users = ", ".join(row["selected_user_ids"])
            lines.append(
                f"| `{row['segment_id']}` | {users} "
                f"| {row['static_actuatable_mean_mbps']:.3f}/{row['static_actuatable_p50_mbps']:.3f}/{row['static_actuatable_p95_mbps']:.3f} "
                f"| {row['static_windows_geq70']} | {row['active_write_tenants_median']:.1f} "
                f"| {float(row['segment_locality_score'] or 0.0):.4f} | {row['expected_risk']} |"
            )
    lines.append("")
    lines.append("## Notes\n")
    lines.append("- Static actuatable supply is computed as `sum_tenants min(7.0, scaled_tenant_write_MBps)` per window.")
    lines.append("- Oracle-tiered actuatable supply uses trace-derived per-window tiers with caps high/mid/low = 11/7/3 MB/s.")
    lines.append("- Existing aggregate Gate-A fields are carried unchanged; the new gate only diagnoses whether that supply can be actuated by a static all-mid policy.")
    lines.append("- The previous Baleen static-smoke summary has a display-only per-window completion-ratio issue in zero-offered windows; total completion ratio and Gate-B verdict are unaffected.")
    lines.append("")
    lines.append("## Next Step Recommendation\n")
    for item in s["next_step_recommendations"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser()
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--baleen-root", type=Path)
    src.add_argument("--baleen-tar", type=Path)
    p.add_argument("--candidate-segments-json", type=Path, required=True)
    p.add_argument("--segment-locality-addendum-json", type=Path, required=True)
    p.add_argument("--out-json", type=Path, required=True)
    p.add_argument("--out-md", type=Path, required=True)
    p.add_argument("--trace-pattern", default="full_0_0.1.trace")
    p.add_argument("--window-sec", type=int, default=20)
    p.add_argument("--high-count", type=int, default=4)
    p.add_argument("--low-count", type=int, default=4)
    p.add_argument("--static-cap-mbps", type=float, default=7.0)
    p.add_argument("--high-cap-mbps", type=float, default=11.0)
    p.add_argument("--mid-cap-mbps", type=float, default=7.0)
    p.add_argument("--low-cap-mbps", type=float, default=3.0)
    p.add_argument("--sustained-min-windows", type=int, default=6)
    p.add_argument("--recommendation-count", type=int, default=3)
    p.add_argument("--allow-overwrite", action="store_true")
    args = p.parse_args()

    for out_path in [args.out_json, args.out_md]:
        if out_path.exists() and not args.allow_overwrite:
            print(f"refusing to overwrite existing artifact: {out_path}", file=sys.stderr)
            return 3

    candidate_payload, candidates = load_candidates(args.candidate_segments_json)
    locality_payload, locality_by_segment = load_locality(args.segment_locality_addendum_json)
    global_scale = float(candidate_payload.get("global_scale") or 1.0)
    states, by_trace_window = initialize_states(candidates, args.window_sec)
    reader = TraceReader(args.baleen_root, args.baleen_tar, args.trace_pattern)
    try:
        scan_summary = scan_traces(reader, states, by_trace_window, args.window_sec)
    finally:
        reader.close()
    rows = compute_rows(
        states,
        locality_by_segment,
        global_scale,
        args.window_sec,
        args.high_count,
        args.low_count,
        args.static_cap_mbps,
        args.high_cap_mbps,
        args.mid_cap_mbps,
        args.low_cap_mbps,
        args.sustained_min_windows,
    )
    recs = top_recommendations(rows, args.recommendation_count)
    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "inputs": {
            "candidate_segments_json": str(args.candidate_segments_json),
            "segment_locality_addendum_json": str(args.segment_locality_addendum_json),
            "trace_source": reader.source_description(),
            "trace_pattern": args.trace_pattern,
        },
        "config": {
            "window_sec": args.window_sec,
            "global_scale": global_scale,
            "static_actuatable_formula": "sum_tenants min(7.0, scaled_tenant_write_MBps)",
            "oracle_tiered_formula": "sum_tenants min(tier_cap, scaled_tenant_write_MBps) with trace-derived high/mid/low tiers",
            "high_count": args.high_count,
            "mid_count": max(0, 16 - args.high_count - args.low_count),
            "low_count": args.low_count,
            "caps_mbps": {
                "static_all_mid": args.static_cap_mbps,
                "high": args.high_cap_mbps,
                "mid": args.mid_cap_mbps,
                "low": args.low_cap_mbps,
            },
            "future_budget_contract_preserved": "high/mid/low = 11/7/3 MB/s, total 112 MB/s",
            "static_smoke_eligibility": {
                "segment_locality_pass": True,
                "static_windows_geq70": f">={args.sustained_min_windows}/8",
                "median_active_write_tenants": ">=9",
                "static_actuatable_mean_mbps": ">=70",
            },
            "unit_note": "Trace MB/s fields follow the prior Baleen audit convention: bytes / window_sec / 1024^2, then global_scale.",
        },
        "scan_summary": scan_summary,
        "locality_summary": locality_payload.get("summary", {}),
        "summary": summarize(rows, recs),
        "diagnostic_notes": [
            "This is a trace-only static-actuatable supply addendum; it does not run RocksDB.",
            "The prior Baleen static-smoke analyzer displayed non-null per-window completion ratios in zero-offered windows because it divided by max(1, offered_ops). That was a diagnostic display issue only; total completion_ratio and the Gate-B verdict are unchanged.",
        ],
        "segments": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2, sort_keys=True))
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(render_md(report))
    print(json.dumps({
        "out_json": str(args.out_json),
        "out_md": str(args.out_md),
        "segments": len(rows),
        "eligible": report["summary"]["static_smoke_eligible_segments"],
        "recommendation": report["summary"]["recommendation"],
        "top_recommendation": recs[0]["segment_id"] if recs else None,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
