#!/usr/bin/env python3
"""Prepare one Baleen candidate segment for the existing RocksDB replay harness.

This is a narrow bridge from the trace audit artifacts to
``run_embedded_continuous.py``. It writes a CacheLib-compatible schedule JSON
because the runner already has a frozen selected-segment replay path. The
payload is explicitly labelled as Baleen/Tectonic in metadata and is intended
only for a single static-only Gate-B smoke.

It does not run RocksDB and refuses to overwrite its output unless explicitly
requested.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import sys
from pathlib import Path

MB = 1024 * 1024

BALEEN_READ_OPS = {"1", "2", "5", "GET_TEMP", "GET_PERM", "GET_NOT_INIT"}
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


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_baleen_header(trace_dir: Path) -> list[str]:
    header = trace_dir / "full.header"
    required = {"block_id", "io_offset", "io_size", "op_time", "op_name", "user_name"}
    for raw in header.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        cols = line[1:].strip().split()
        if required.issubset(set(cols)):
            return cols
    raise ValueError(f"could not find Baleen schema in {header}")


def iter_trace_lines(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            yield line.split()


def find_trace_dir(root: Path, trace_id: str) -> Path:
    matches = sorted(p for p in root.rglob(trace_id) if p.is_dir() and (p / "full.header").exists())
    if not matches:
        raise FileNotFoundError(f"could not find trace dir {trace_id!r} under {root}")
    return matches[-1]


def load_candidate(path: Path, segment_id: str) -> tuple[dict, dict]:
    payload = json.loads(path.read_text())
    for cand in payload.get("candidate_segments", []):
        if str(cand.get("segment_id")) == segment_id:
            return payload, cand
    raise SystemExit(f"segment_id {segment_id!r} not found in {path}")


def key_for_row(parts: list[str], idx: dict[str, int], bucket_bytes: int) -> str:
    block_id = parts[idx["block_id"]]
    rs = parts[idx["rs_shard_id"]] if "rs_shard_id" in idx else "_"
    off = parse_int(parts[idx["io_offset"]])
    return f"{block_id}:{rs}:{off // bucket_bytes}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--baleen-root", type=Path, required=True)
    p.add_argument("--candidate-segments-json", type=Path, required=True)
    p.add_argument("--segment-id", required=True)
    p.add_argument("--trace-pattern", default="full_0_0.1.trace")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--value-size", type=int, default=1024)
    p.add_argument("--tenant-count", type=int, default=16)
    p.add_argument("--high-count", type=int, default=4)
    p.add_argument("--low-count", type=int, default=4)
    p.add_argument("--window-sec", type=int, default=20)
    p.add_argument("--keyspace-floor", type=int, default=80000)
    p.add_argument("--keyspace-ceiling", type=int, default=240000)
    p.add_argument("--keyspace-multiplier", type=float, default=1.5)
    p.add_argument("--unique-key-bucket-bytes", type=int, default=MB)
    p.add_argument("--allow-overwrite", action="store_true")
    args = p.parse_args()

    if args.out.exists() and not args.allow_overwrite:
        print(f"refusing to overwrite {args.out}", file=sys.stderr)
        return 3

    candidate_payload, cand = load_candidate(args.candidate_segments_json, args.segment_id)
    selected = [str(x) for x in cand["selected_user_ids"][: args.tenant_count]]
    if len(selected) != args.tenant_count:
        raise SystemExit(f"expected {args.tenant_count} selected users, got {len(selected)}")
    trace_id = str(cand["trace_id"])
    trace_dir = find_trace_dir(args.baleen_root, trace_id)
    header = read_baleen_header(trace_dir)
    idx = {name: i for i, name in enumerate(header)}
    start = float(cand["start_time"])
    end = float(cand["end_time"])
    start_window = int(cand["start_window"])
    end_window = int(cand["end_window"])
    windows = end_window - start_window + 1
    selected_set = set(selected)

    per_tenant = {
        user: {
            "per_window_write_bytes": [0.0] * windows,
            "per_window_read_ops": [0.0] * windows,
            "per_window_unique_sample_size": [0] * windows,
            "per_window_total_ops": [0] * windows,
            "per_window_saturated": [False] * windows,
            "_unique_sets": [set() for _ in range(windows)],
        }
        for user in selected
    }
    trace_files = sorted(trace_dir.glob(args.trace_pattern))
    if not trace_files:
        raise FileNotFoundError(f"no {args.trace_pattern!r} in {trace_dir}")
    scanned_records = 0
    matched_records = 0
    matched_writes = 0
    for trace_file in trace_files:
        for parts in iter_trace_lines(trace_file):
            scanned_records += 1
            if len(parts) < len(header):
                continue
            op_time = parse_float(parts[idx["op_time"]])
            if not (start <= op_time < end):
                continue
            user = parts[idx["user_name"]]
            if user not in selected_set:
                continue
            w = int(math.floor(op_time / args.window_sec)) - start_window
            if w < 0 or w >= windows:
                continue
            op_count = parse_int(parts[idx["op_count"]], 1) if "op_count" in idx else 1
            op_count = max(1, op_count)
            op = parts[idx["op_name"]].upper()
            if op.endswith(".0"):
                op = op[:-2]
            io_size = max(0, parse_int(parts[idx["io_size"]]))
            row = per_tenant[user]
            row["per_window_total_ops"][w] += op_count
            row["_unique_sets"][w].add(key_for_row(parts, idx, args.unique_key_bucket_bytes))
            matched_records += 1
            if op in BALEEN_WRITE_OPS:
                row["per_window_write_bytes"][w] += io_size * op_count
                matched_writes += 1
            elif op in BALEEN_READ_OPS:
                row["per_window_read_ops"][w] += op_count

    max_unique = 0
    for row in per_tenant.values():
        for w, keys in enumerate(row["_unique_sets"]):
            n = len(keys)
            row["per_window_unique_sample_size"][w] = n
            max_unique = max(max_unique, n)
        del row["_unique_sets"]
    keyspace = max(
        args.keyspace_floor,
        min(args.keyspace_ceiling, int(math.ceil(args.keyspace_multiplier * max(1, max_unique)))),
    )

    global_scale = float(candidate_payload.get("global_scale") or 1.0)
    raw_means = [
        float(s.get("raw_mean_write_mbps", 0.0))
        for s in candidate_payload.get("candidate_segments", [])
        if float(s.get("raw_mean_write_mbps", 0.0)) > 0
    ]
    aggregate = {
        "global_scale": global_scale,
        "raw_mean_write_mbps_median": sorted(raw_means)[len(raw_means) // 2] if raw_means else None,
        "candidate_count": len(candidate_payload.get("candidate_segments", [])),
        "source_candidate_segments_json": str(args.candidate_segments_json),
    }
    detail = {user: per_tenant[user] for user in selected}
    detail["_segment_keyspace_global"] = keyspace
    trace_sha = {}
    for path in trace_files:
        trace_sha[path.name] = {"size_bytes": path.stat().st_size, "sha256": file_sha256(path)}

    out = {
        "config": {
            "trace_dataset": "Meta Tectonic/Baleen storage_0.1",
            "trace_family": "baleen",
            "trace_files": [str(p) for p in trace_files],
            "trace_files_sha256": trace_sha,
            "window_sec": args.window_sec,
            "windows_per_segment": windows,
            "tenant_count": args.tenant_count,
            "high_count": args.high_count,
            "low_count": args.low_count,
            "value_size": args.value_size,
            "target_aggregate_mbps": 96.0,
            "budget_contract_note": "Future RocksDB smoke uses high/mid/low 11/7/3 MB/s, total 112 MB/s.",
            "unique_key_proxy": f"block_id:rs_shard_id:floor(io_offset/{args.unique_key_bucket_bytes})",
        },
        "aggregate": aggregate,
        "selected_segments": [
            {
                "segment_index": cand["segment_id"],
                "segment_id": cand["segment_id"],
                "trace_id": trace_id,
                "start_window": start_window,
                "end_window": end_window,
                "start_op_time": start,
                "end_op_time": end,
                "start_time": start,
                "end_time": end,
                "tenant_ids": selected,
                "selected_user_ids": selected,
                "per_window_high": cand.get("per_window_high", []),
                "drift_rate": cand.get("top4_drift_rate"),
                "top4_drift_rate": cand.get("top4_drift_rate"),
                "frozen_overlap_mean": cand.get("frozen_overlap"),
                "frozen_overlap": cand.get("frozen_overlap"),
                "high_set_changes": cand.get("high_set_changes"),
                "raw_mean_write_mbps": cand.get("raw_mean_write_mbps"),
                "raw_window_write_mbps": cand.get("raw_window_write_mbps"),
                "scaled_mean_write_mbps": cand.get("scaled_mean_write_mbps"),
                "scaled_window_write_mbps": cand.get("scaled_window_write_mbps"),
                "scaled_windows_geq70": cand.get("scaled_windows_geq70"),
                "n_tenants": cand.get("n_tenants"),
                "n_write_tenants": cand.get("n_write_tenants"),
                "detail": detail,
            }
        ],
        "preparation_summary": {
            "scanned_records": scanned_records,
            "matched_selected_records": matched_records,
            "matched_selected_write_records": matched_writes,
            "segment_keyspace_global": keyspace,
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps({
        "out": str(args.out),
        "segment_id": cand["segment_id"],
        "trace_id": trace_id,
        "selected_users": selected,
        "matched_selected_records": matched_records,
        "matched_selected_write_records": matched_writes,
        "segment_keyspace_global": keyspace,
        "global_scale": global_scale,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
