#!/usr/bin/env python3
"""Audit public trace families before any RocksDB replay.

This phase-2 audit is intentionally trace-only: it parses the source trace,
computes byte-supply, drift, locality, and KV-submit-QPS diagnostics, and writes
reports. It does not create a formal RocksDB ``selected_segments.json`` and does
not run RocksDB.

Implemented family:
  * ``baleen`` / Meta Tectonic storage traces. Columns are bound dynamically from
    each trace directory's ``full.header``. Baleen op codes are:
      reads:  GET_TEMP=1, GET_PERM=2, GET_NOT_INIT=5
      writes: PUT_TEMP=3, PUT_PERM=4, PUT_NOT_INIT=6
    The parser accepts either the numeric code or the symbolic name.

Reserved:
  * ``tencent`` is kept as a command-line family name for a future schema, but
    this script currently exits with a clear unsupported-family error.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as _dt
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
MB = 1024 * 1024
DEFAULT_SEED = 20260519

BALEEN_READ_OPS = {
    "1": "GET_TEMP",
    "2": "GET_PERM",
    "5": "GET_NOT_INIT",
    "GET_TEMP": "GET_TEMP",
    "GET_PERM": "GET_PERM",
    "GET_NOT_INIT": "GET_NOT_INIT",
}
BALEEN_WRITE_OPS = {
    "3": "PUT_TEMP",
    "4": "PUT_PERM",
    "6": "PUT_NOT_INIT",
    "PUT_TEMP": "PUT_TEMP",
    "PUT_PERM": "PUT_PERM",
    "PUT_NOT_INIT": "PUT_NOT_INIT",
}

MAPPING_CHUNKS = {
    "request-as-one-put": None,
    "64KiB-chunk": 64 * 1024,
    "1KiB-chunk": 1024,
}


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    vals = sorted(values)
    if len(vals) == 1:
        return float(vals[0])
    rank = (len(vals) - 1) * (p / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(vals[lo])
    frac = rank - lo
    return float(vals[lo] * (1.0 - frac) + vals[hi] * frac)


def percentiles(values: list[float], ps: Iterable[float] = (50, 90, 95, 99)) -> dict:
    return {f"p{int(p)}": pct(values, p) for p in ps}


def safe_mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else float("nan")


def risk_for_per_thread_qps(per_thread_qps: float) -> str:
    # Conservative harness-side warning bands. These are diagnostics, not gates.
    if math.isnan(per_thread_qps):
        return "unknown"
    if per_thread_qps < 5_000:
        return "low"
    if per_thread_qps < 20_000:
        return "mid"
    return "high"


def normalize_baleen_op(token: str) -> tuple[str, str]:
    raw = str(token).strip()
    key = raw.upper()
    if key.endswith(".0"):
        key = key[:-2]
    if key in BALEEN_WRITE_OPS:
        return "write", BALEEN_WRITE_OPS[key]
    if key in BALEEN_READ_OPS:
        return "read", BALEEN_READ_OPS[key]
    return "other", raw


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


@dataclass
class CandidateSegment:
    segment_id: str
    trace_id: str
    start_window: int
    end_window: int
    start_time: float
    end_time: float
    n_tenants: int
    n_write_tenants: int
    selected_user_ids: list[str]
    per_window_high: list[list[str]]
    raw_window_write_mbps: list[float]
    raw_mean_write_mbps: float
    top4_drift_rate: float
    frozen_overlap: float
    high_set_changes: int
    top4_unique_users: int
    basic_gate_pass: bool
    basic_gate_reasons: list[str]
    scaled_window_write_mbps: list[float] = field(default_factory=list)
    scaled_mean_write_mbps: float = float("nan")
    scaled_windows_geq70: int = 0
    scaled_gate_pass: bool = False
    mapping_diagnostics: dict = field(default_factory=dict)
    qps_risk: str = "unknown"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class TraceStats:
    def __init__(
        self,
        trace_id: str,
        trace_dir: Path,
        header_columns: list[str],
        window_sec: int,
        value_size: int,
        offset_bucket_bytes: int,
    ):
        self.trace_id = trace_id
        self.trace_dir = trace_dir
        self.header_columns = header_columns
        self.window_sec = window_sec
        self.value_size = value_size
        self.offset_bucket_bytes = offset_bucket_bytes

        self.trace_files: list[Path] = []
        self.records_total = 0
        self.parse_errors = 0
        self.read_records = 0
        self.write_records = 0
        self.other_records = 0
        self.total_op_count = 0.0
        self.read_op_count = 0.0
        self.write_op_count = 0.0
        self.other_op_count = 0.0
        self.total_write_bytes = 0.0
        self.total_read_bytes = 0.0
        self.op_distribution_records: collections.Counter[str] = collections.Counter()
        self.op_distribution_ops: collections.Counter[str] = collections.Counter()

        self.min_op_time: Optional[float] = None
        self.max_op_time: Optional[float] = None
        self.min_window: Optional[int] = None
        self.max_window: Optional[int] = None
        self.min_second: Optional[int] = None
        self.max_second: Optional[int] = None

        self.users_all: set[str] = set()
        self.users_write: set[str] = set()
        self.tenant_write_bytes: collections.Counter[str] = collections.Counter()
        self.tenant_write_records: collections.Counter[str] = collections.Counter()

        self.write_sizes: list[float] = []
        self.window_tenant_write_bytes: dict[int, collections.Counter[str]] = {}
        self.window_tenant_all_ops: dict[int, collections.Counter[str]] = {}
        self.window_tenant_mapping_ops: dict[str, dict[int, collections.Counter[str]]] = {
            name: {} for name in MAPPING_CHUNKS
        }
        self.window_write_bytes: collections.Counter[int] = collections.Counter()
        self.second_write_bytes: collections.Counter[int] = collections.Counter()
        self.window_mapping_ops: dict[str, collections.Counter[int]] = {
            name: collections.Counter() for name in MAPPING_CHUNKS
        }
        self.mapping_total_ops: collections.Counter[str] = collections.Counter()

        self.write_block_ids: set[str] = set()
        self.write_block_record_count = 0
        self.block_write_bytes: collections.Counter[str] = collections.Counter()
        self.offset_bucket_write_bytes: collections.Counter[str] = collections.Counter()

    def add_trace_file(self, path: Path) -> None:
        self.trace_files.append(path)

    def add_record(
        self,
        op_time: float,
        op_kind: str,
        op_name: str,
        io_size: int,
        op_count: int,
        user_name: str,
        block_id: str,
        io_offset: int,
    ) -> None:
        self.records_total += 1
        op_count = max(1, op_count)
        io_size = max(0, io_size)
        self.total_op_count += op_count
        self.op_distribution_records[op_name] += 1
        self.op_distribution_ops[op_name] += op_count
        self.users_all.add(user_name)
        if self.min_op_time is None or op_time < self.min_op_time:
            self.min_op_time = op_time
        if self.max_op_time is None or op_time > self.max_op_time:
            self.max_op_time = op_time
        win = int(math.floor(op_time / self.window_sec))
        sec = int(math.floor(op_time))
        self.min_window = win if self.min_window is None else min(self.min_window, win)
        self.max_window = win if self.max_window is None else max(self.max_window, win)
        self.min_second = sec if self.min_second is None else min(self.min_second, sec)
        self.max_second = sec if self.max_second is None else max(self.max_second, sec)

        byte_count = io_size * op_count
        self.window_tenant_all_ops.setdefault(win, collections.Counter())[user_name] += op_count
        if op_kind == "write":
            self.write_records += 1
            self.write_op_count += op_count
            self.total_write_bytes += byte_count
            self.users_write.add(user_name)
            self.tenant_write_bytes[user_name] += byte_count
            self.tenant_write_records[user_name] += 1
            self.write_sizes.append(io_size)
            self.window_write_bytes[win] += byte_count
            self.second_write_bytes[sec] += byte_count
            self.window_tenant_write_bytes.setdefault(win, collections.Counter())[user_name] += byte_count
            self.write_block_ids.add(block_id)
            self.write_block_record_count += 1
            self.block_write_bytes[block_id] += byte_count
            self._add_offset_buckets(block_id, io_offset, io_size, op_count)

            for mapping_name, chunk_size in MAPPING_CHUNKS.items():
                if chunk_size is None:
                    kv_ops = op_count
                else:
                    kv_ops = op_count * max(1, math.ceil(io_size / chunk_size))
                self.mapping_total_ops[mapping_name] += kv_ops
                self.window_mapping_ops[mapping_name][win] += kv_ops
                self.window_tenant_mapping_ops[mapping_name].setdefault(win, collections.Counter())[user_name] += kv_ops
        elif op_kind == "read":
            self.read_records += 1
            self.read_op_count += op_count
            self.total_read_bytes += byte_count
        else:
            self.other_records += 1
            self.other_op_count += op_count

    def _add_offset_buckets(self, block_id: str, io_offset: int, io_size: int, op_count: int) -> None:
        if io_size <= 0:
            return
        bucket_size = self.offset_bucket_bytes
        pos = max(0, io_offset)
        remaining = io_size
        while remaining > 0:
            bucket_idx = pos // bucket_size
            bucket_end = (bucket_idx + 1) * bucket_size
            n = min(remaining, bucket_end - pos)
            self.offset_bucket_write_bytes[f"{block_id}:{bucket_idx}"] += n * op_count
            pos += n
            remaining -= n

    def duration_sec(self) -> float:
        if self.min_op_time is None or self.max_op_time is None:
            return 0.0
        return max(0.0, self.max_op_time - self.min_op_time)

    def window_range(self) -> range:
        if self.min_window is None or self.max_window is None:
            return range(0)
        return range(self.min_window, self.max_window + 1)

    def second_range(self) -> range:
        if self.min_second is None or self.max_second is None:
            return range(0)
        return range(self.min_second, self.max_second + 1)

    def window_write_mbps_values(self) -> list[float]:
        return [self.window_write_bytes[w] / self.window_sec / MB for w in self.window_range()]

    def second_write_mbps_values(self) -> list[float]:
        return [self.second_write_bytes[s] / MB for s in self.second_range()]

    def mapping_summary(self, threads: int) -> dict:
        duration = max(1e-9, self.duration_sec())
        out = {}
        for name in MAPPING_CHUNKS:
            total_ops = float(self.mapping_total_ops[name])
            mean_qps = total_ops / duration
            window_qps = [self.window_mapping_ops[name][w] / self.window_sec for w in self.window_range()]
            p95_qps = pct(window_qps, 95)
            per_thread_p95 = p95_qps / max(1, threads)
            out[name] = {
                "offered_qps_mean": mean_qps,
                "offered_qps_p95_20s": p95_qps,
                "logical_mbps_mean_at_value_size": mean_qps * self.value_size / MB,
                "logical_mbps_p95_20s_at_value_size": p95_qps * self.value_size / MB,
                "per_thread_qps_mean": mean_qps / max(1, threads),
                "per_thread_qps_p95_20s": per_thread_p95,
                "per_thread_qps_risk": risk_for_per_thread_qps(per_thread_p95),
            }
        return out

    def locality_summary(self) -> dict:
        block_share = top_fraction_share(self.block_write_bytes, 0.01)
        offset_share = top_fraction_share(self.offset_bucket_write_bytes, 0.01)
        unique_block_ratio = (
            len(self.write_block_ids) / self.write_block_record_count
            if self.write_block_record_count else float("nan")
        )
        return {
            "unique_write_blocks": len(self.write_block_ids),
            "write_block_records": self.write_block_record_count,
            "unique_block_ratio_records": unique_block_ratio,
            "block_bucket_count": len(self.block_write_bytes),
            "offset_bucket_bytes": self.offset_bucket_bytes,
            "offset_bucket_count": len(self.offset_bucket_write_bytes),
            "top1pct_block_write_byte_share": block_share,
            "top1pct_offset_bucket_write_byte_share": offset_share,
        }

    def to_summary(self, threads: int) -> dict:
        duration = self.duration_sec()
        top20 = [
            {"user_name": user, "write_mb": bytes_ / MB}
            for user, bytes_ in self.tenant_write_bytes.most_common(20)
        ]
        write_record_share = self.write_records / self.records_total if self.records_total else float("nan")
        write_op_share = self.write_op_count / self.total_op_count if self.total_op_count else float("nan")
        return {
            "trace_id": self.trace_id,
            "trace_dir": str(self.trace_dir),
            "trace_files": [
                {"path": str(p), "size_bytes": p.stat().st_size}
                for p in self.trace_files
            ],
            "header_columns": self.header_columns,
            "records_total": self.records_total,
            "parse_errors": self.parse_errors,
            "duration_sec": duration,
            "min_op_time": self.min_op_time,
            "max_op_time": self.max_op_time,
            "op_distribution_records": dict(self.op_distribution_records),
            "op_distribution_op_count": dict(self.op_distribution_ops),
            "write_records": self.write_records,
            "read_records": self.read_records,
            "other_records": self.other_records,
            "write_op_count": self.write_op_count,
            "read_op_count": self.read_op_count,
            "other_op_count": self.other_op_count,
            "write_record_share": write_record_share,
            "write_op_count_share": write_op_share,
            "total_write_mb": self.total_write_bytes / MB,
            "raw_write_mbps_over_duration": self.total_write_bytes / max(1e-9, duration) / MB,
            "write_size_bytes": percentiles(self.write_sizes, (50, 90, 99)),
            "unique_tenants_users": len(self.users_all),
            "unique_write_tenants_users": len(self.users_write),
            "per_tenant_write_mb_top20": top20,
            "global_write_mbps_20s": percentiles(self.window_write_mbps_values(), (50, 95, 99)),
            "global_write_mbps_1s_burst": percentiles(self.second_write_mbps_values(), (50, 95, 99)),
            "locality": self.locality_summary(),
            "mapping_diagnostics": self.mapping_summary(threads),
        }


def top_fraction_share(counter: collections.Counter, fraction: float) -> float:
    total = sum(counter.values())
    if total <= 0 or not counter:
        return float("nan")
    n = max(1, math.ceil(len(counter) * fraction))
    return sum(v for _, v in counter.most_common(n)) / total


def read_baleen_header(trace_dir: Path) -> list[str]:
    header_path = trace_dir / "full.header"
    if not header_path.exists():
        raise FileNotFoundError(f"missing Baleen full.header in {trace_dir}")
    required = {"block_id", "io_offset", "io_size", "op_time", "op_name", "user_name"}
    for raw in header_path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith("#"):
            continue
        body = line[1:].strip()
        cols = body.split()
        if required.issubset(set(cols)):
            return cols
    raise ValueError(f"could not find schema row with {sorted(required)} in {header_path}")


def find_baleen_trace_dirs(root: Path, regions: list[str]) -> list[Path]:
    found = []
    for region in regions:
        matches = sorted(p for p in root.rglob(region) if p.is_dir() and (p / "full.header").exists())
        if not matches:
            raise FileNotFoundError(f"could not find region {region} under {root}")
        if len(matches) > 1:
            # Prefer the deepest lexicographically-last match only if duplicates exist.
            matches = sorted(matches, key=lambda p: (len(p.parts), str(p)))
        found.append(matches[-1])
    return found


def iter_trace_lines(trace_file: Path):
    with trace_file.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            yield line.split()


def parse_baleen_trace_dir(
    trace_dir: Path,
    trace_id: str,
    trace_pattern: str,
    window_sec: int,
    value_size: int,
    offset_bucket_bytes: int,
    max_records: Optional[int],
    progress_every_records: int,
) -> TraceStats:
    header_cols = read_baleen_header(trace_dir)
    idx = {name: i for i, name in enumerate(header_cols)}
    required = ["block_id", "io_offset", "io_size", "op_time", "op_name", "user_name"]
    missing = [c for c in required if c not in idx]
    if missing:
        raise ValueError(f"{trace_dir} missing required Baleen columns: {missing}")

    stats = TraceStats(trace_id, trace_dir, header_cols, window_sec, value_size, offset_bucket_bytes)
    trace_files = sorted(trace_dir.glob(trace_pattern))
    if not trace_files:
        raise FileNotFoundError(f"no trace files matching {trace_pattern!r} in {trace_dir}")
    started = time.time()
    last_progress = 0
    for trace_file in trace_files:
        stats.add_trace_file(trace_file)
        for parts in iter_trace_lines(trace_file):
            if len(parts) < len(header_cols):
                stats.parse_errors += 1
                continue
            try:
                op_time = parse_float(parts[idx["op_time"]])
                op_kind, op_name = normalize_baleen_op(parts[idx["op_name"]])
                io_size = parse_int(parts[idx["io_size"]])
                io_offset = parse_int(parts[idx["io_offset"]])
                op_count = parse_int(parts[idx["op_count"]], 1) if "op_count" in idx else 1
                user_name = parts[idx["user_name"]]
                block_id = parts[idx["block_id"]]
            except Exception:
                stats.parse_errors += 1
                continue
            stats.add_record(op_time, op_kind, op_name, io_size, op_count, user_name, block_id, io_offset)
            if max_records is not None and stats.records_total >= max_records:
                break
            if progress_every_records and stats.records_total - last_progress >= progress_every_records:
                elapsed = time.time() - started
                print(
                    f"[progress] {trace_id} records={stats.records_total:,} "
                    f"writes={stats.write_records:,} elapsed={elapsed:.1f}s "
                    f"rate={stats.records_total / max(elapsed, 1e-9):,.0f} rec/s",
                    file=sys.stderr,
                    flush=True,
                )
                last_progress = stats.records_total
        if max_records is not None and stats.records_total >= max_records:
            break
    elapsed = time.time() - started
    print(
        f"[done] {trace_id} records={stats.records_total:,} writes={stats.write_records:,} "
        f"duration={stats.duration_sec():.1f}s elapsed={elapsed:.1f}s",
        file=sys.stderr,
        flush=True,
    )
    return stats


def build_basic_candidates(stats: TraceStats, args: argparse.Namespace) -> list[CandidateSegment]:
    if stats.min_window is None or stats.max_window is None:
        return []
    out: list[CandidateSegment] = []
    windows = args.windows_per_segment
    last_start = stats.max_window - windows + 1
    if last_start < stats.min_window:
        return []
    seg_ordinal = 0
    for start in range(stats.min_window, last_start + 1, args.segment_stride_windows):
        end = start + windows - 1
        tenant_ops: collections.Counter[str] = collections.Counter()
        tenant_totals: collections.Counter[str] = collections.Counter()
        for w in range(start, end + 1):
            tenant_ops.update(stats.window_tenant_all_ops.get(w, {}))
            tenant_totals.update(stats.window_tenant_write_bytes.get(w, {}))
        active_users = [u for u, ops in tenant_ops.items() if ops > 0]
        active_users.sort(key=lambda u: (-tenant_totals[u], -tenant_ops[u], u))
        selected = active_users[: args.tenant_count]
        write_active_users = [u for u, b in tenant_totals.items() if b > 0]
        per_window_high: list[list[str]] = []
        raw_window_mbps: list[float] = []
        for w in range(start, end + 1):
            win = stats.window_tenant_write_bytes.get(w, {})
            scored = [(u, win.get(u, 0.0)) for u in selected]
            scored.sort(key=lambda kv: (-kv[1], kv[0]))
            per_window_high.append([u for u, _ in scored[: args.high_count]])
            raw_window_mbps.append(sum(win.get(u, 0.0) for u in selected) / args.window_sec / MB)

        high_set_changes = 0
        drift_sum = 0.0
        for i in range(1, len(per_window_high)):
            prev = set(per_window_high[i - 1])
            cur = set(per_window_high[i])
            sym = len(prev.symmetric_difference(cur))
            drift_sum += sym / 2.0
            if sym > 0:
                high_set_changes += 1
        drift_rate = drift_sum / max(1, len(per_window_high) - 1)
        if per_window_high:
            first = set(per_window_high[0])
            overlaps = [len(first.intersection(set(h))) for h in per_window_high[1:]]
            frozen_overlap = safe_mean(overlaps)
        else:
            frozen_overlap = float("nan")
        top4_unique = len({u for h in per_window_high for u in h})
        raw_mean = safe_mean(raw_window_mbps)

        reasons = []
        if len(active_users) < args.tenant_count:
            reasons.append(f"n_tenants {len(active_users)}<{args.tenant_count}")
        if high_set_changes < args.high_set_min_changes:
            reasons.append(f"high_set_changes {high_set_changes}<{args.high_set_min_changes}")
        if not math.isnan(frozen_overlap) and frozen_overlap > args.frozen_overlap_max:
            reasons.append(f"frozen_overlap {frozen_overlap:.3f}>{args.frozen_overlap_max}")
        basic_pass = not reasons
        seg = CandidateSegment(
            segment_id=f"{stats.trace_id}_w{start}",
            trace_id=stats.trace_id,
            start_window=start,
            end_window=end,
            start_time=start * args.window_sec,
            end_time=(end + 1) * args.window_sec,
            n_tenants=len(active_users),
            n_write_tenants=len(write_active_users),
            selected_user_ids=selected,
            per_window_high=per_window_high,
            raw_window_write_mbps=raw_window_mbps,
            raw_mean_write_mbps=raw_mean,
            top4_drift_rate=drift_rate,
            frozen_overlap=frozen_overlap,
            high_set_changes=high_set_changes,
            top4_unique_users=top4_unique,
            basic_gate_pass=basic_pass,
            basic_gate_reasons=reasons,
        )
        if basic_pass:
            seg.mapping_diagnostics = mapping_for_segment(stats, seg, args)
            seg.qps_risk = seg.mapping_diagnostics["1KiB-chunk"]["per_thread_qps_risk"]
            out.append(seg)
        seg_ordinal += 1
    return out


def mapping_for_segment(stats: TraceStats, seg: CandidateSegment, args: argparse.Namespace) -> dict:
    selected = set(seg.selected_user_ids)
    out = {}
    for name in MAPPING_CHUNKS:
        per_window_ops = []
        for w in range(seg.start_window, seg.end_window + 1):
            win_counts = stats.window_tenant_mapping_ops[name].get(w, {})
            per_window_ops.append(sum(win_counts.get(u, 0.0) for u in selected))
        duration = args.windows_per_segment * args.window_sec
        total_ops = sum(per_window_ops)
        mean_qps = total_ops / max(1e-9, duration)
        per_window_qps = [v / args.window_sec for v in per_window_ops]
        p95_qps = pct(per_window_qps, 95)
        per_thread_p95 = p95_qps / max(1, args.threads)
        out[name] = {
            "offered_qps_mean": mean_qps,
            "offered_qps_p95_20s": p95_qps,
            "logical_mbps_mean_at_value_size": mean_qps * args.value_size / MB,
            "logical_mbps_p95_20s_at_value_size": p95_qps * args.value_size / MB,
            "per_thread_qps_mean": mean_qps / max(1, args.threads),
            "per_thread_qps_p95_20s": per_thread_p95,
            "per_thread_qps_risk": risk_for_per_thread_qps(per_thread_p95),
        }
    return out


def apply_scaled_gates(candidates: list[CandidateSegment], global_scale: float, args: argparse.Namespace) -> None:
    for seg in candidates:
        seg.scaled_window_write_mbps = [
            v * global_scale if not math.isnan(global_scale) else float("nan")
            for v in seg.raw_window_write_mbps
        ]
        seg.scaled_mean_write_mbps = safe_mean(seg.scaled_window_write_mbps)
        seg.scaled_windows_geq70 = sum(
            1 for v in seg.scaled_window_write_mbps
            if not math.isnan(v) and v >= args.scaled_sustained_mbps
        )
        seg.scaled_gate_pass = seg.scaled_windows_geq70 >= args.sustained_min_windows


def pick_independent(candidates: list[CandidateSegment], min_gap_windows: int) -> list[CandidateSegment]:
    selected: list[CandidateSegment] = []
    last_end_by_trace: dict[str, int] = {}
    for seg in sorted(candidates, key=lambda s: (s.trace_id, s.start_window)):
        last_end = last_end_by_trace.get(seg.trace_id)
        if last_end is not None and seg.start_window <= last_end + min_gap_windows:
            continue
        selected.append(seg)
        last_end_by_trace[seg.trace_id] = seg.end_window
    return selected


def combined_summary(stats_list: list[TraceStats], threads: int, value_size: int) -> dict:
    records = sum(s.records_total for s in stats_list)
    write_records = sum(s.write_records for s in stats_list)
    read_records = sum(s.read_records for s in stats_list)
    other_records = sum(s.other_records for s in stats_list)
    total_ops = sum(s.total_op_count for s in stats_list)
    write_ops = sum(s.write_op_count for s in stats_list)
    write_sizes: list[float] = []
    win_mbps: list[float] = []
    sec_mbps: list[float] = []
    op_records: collections.Counter[str] = collections.Counter()
    op_counts: collections.Counter[str] = collections.Counter()
    users = set()
    write_users = set()
    tenant_write: collections.Counter[str] = collections.Counter()
    block_bytes: collections.Counter[str] = collections.Counter()
    offset_bytes: collections.Counter[str] = collections.Counter()
    unique_blocks = set()
    block_record_count = 0
    mapping_total: collections.Counter[str] = collections.Counter()
    mapping_window_qps: dict[str, list[float]] = {name: [] for name in MAPPING_CHUNKS}
    duration_sum = 0.0
    total_write_bytes = 0.0
    for s in stats_list:
        duration_sum += s.duration_sec()
        total_write_bytes += s.total_write_bytes
        write_sizes.extend(s.write_sizes)
        win_mbps.extend(s.window_write_mbps_values())
        sec_mbps.extend(s.second_write_mbps_values())
        op_records.update(s.op_distribution_records)
        op_counts.update(s.op_distribution_ops)
        users.update(s.users_all)
        write_users.update(s.users_write)
        tenant_write.update(s.tenant_write_bytes)
        block_bytes.update({f"{s.trace_id}:{k}": v for k, v in s.block_write_bytes.items()})
        offset_bytes.update({f"{s.trace_id}:{k}": v for k, v in s.offset_bucket_write_bytes.items()})
        unique_blocks.update(f"{s.trace_id}:{b}" for b in s.write_block_ids)
        block_record_count += s.write_block_record_count
        mapping_total.update(s.mapping_total_ops)
        for name in MAPPING_CHUNKS:
            mapping_window_qps[name].extend([s.window_mapping_ops[name][w] / s.window_sec for w in s.window_range()])
    mapping = {}
    for name in MAPPING_CHUNKS:
        mean_qps = mapping_total[name] / max(1e-9, duration_sum)
        p95_qps = pct(mapping_window_qps[name], 95)
        per_thread_p95 = p95_qps / max(1, threads)
        mapping[name] = {
            "offered_qps_mean": mean_qps,
            "offered_qps_p95_20s": p95_qps,
            "logical_mbps_mean_at_value_size": mean_qps * value_size / MB,
            "logical_mbps_p95_20s_at_value_size": p95_qps * value_size / MB,
            "per_thread_qps_mean": mean_qps / max(1, threads),
            "per_thread_qps_p95_20s": per_thread_p95,
            "per_thread_qps_risk": risk_for_per_thread_qps(per_thread_p95),
        }
    return {
        "records_total": records,
        "duration_sec_sum_across_traces": duration_sum,
        "write_records": write_records,
        "read_records": read_records,
        "other_records": other_records,
        "write_record_share": write_records / records if records else float("nan"),
        "write_op_count_share": write_ops / total_ops if total_ops else float("nan"),
        "total_write_mb": total_write_bytes / MB,
        "raw_write_mbps_over_summed_duration": total_write_bytes / max(1e-9, duration_sum) / MB,
        "op_distribution_records": dict(op_records),
        "op_distribution_op_count": dict(op_counts),
        "write_size_bytes": percentiles(write_sizes, (50, 90, 99)),
        "unique_tenants_users": len(users),
        "unique_write_tenants_users": len(write_users),
        "per_tenant_write_mb_top20": [
            {"user_name": user, "write_mb": bytes_ / MB}
            for user, bytes_ in tenant_write.most_common(20)
        ],
        "global_write_mbps_20s": percentiles(win_mbps, (50, 95, 99)),
        "global_write_mbps_1s_burst": percentiles(sec_mbps, (50, 95, 99)),
        "locality": {
            "unique_write_blocks": len(unique_blocks),
            "write_block_records": block_record_count,
            "unique_block_ratio_records": (
                len(unique_blocks) / block_record_count if block_record_count else float("nan")
            ),
            "top1pct_block_write_byte_share": top_fraction_share(block_bytes, 0.01),
            "top1pct_offset_bucket_write_byte_share": top_fraction_share(offset_bytes, 0.01),
        },
        "mapping_diagnostics": mapping,
    }


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def file_inventory(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        if p.exists():
            out.append({"path": str(p), "size_bytes": p.stat().st_size, "sha256": sha256_file(p)})
    return out


def gate_a_verdict(
    trace_summaries: list[dict],
    combined: dict,
    basic_candidates: list[CandidateSegment],
    scaled_candidates: list[CandidateSegment],
    independent_candidates: list[CandidateSegment],
    args: argparse.Namespace,
) -> dict:
    max_users = max((t["unique_write_tenants_users"] for t in trace_summaries), default=0)
    locality_share = combined["locality"]["top1pct_offset_bucket_write_byte_share"]
    checks = {
        "tenant_axis_ge16": max_users >= args.tenant_count,
        "candidate_segments_ge5": len(independent_candidates) >= args.want_segments,
        "locality_top1pct_offset_bucket_ge5pct": (
            not math.isnan(locality_share) and locality_share >= args.locality_top1pct_min_share
        ),
    }
    return {
        "pass": bool(all(checks.values())),
        "checks": checks,
        "notes": [
            "Byte-write supply and KV submit-QPS risk are reported separately.",
            "Future RocksDB smoke/n=5 must keep high/mid/low budgets at 11/7/3 MB/s, total 112 MB/s.",
            "The 112 MB/s budget is not used as a trace-demand gate; trace demand is scaled only for audit diagnostics.",
        ],
        "thresholds": {
            "tenant_count_min": args.tenant_count,
            "candidate_segments_min": args.want_segments,
            "candidate_requires_n_tenants": args.tenant_count,
            "candidate_requires_frozen_overlap_le": args.frozen_overlap_max,
            "candidate_requires_high_set_changes_ge": args.high_set_min_changes,
            "global_scale_formula": "96 / median(raw_mean_write_MBps over basic candidate segments)",
            "scaled_sustained_mbps": args.scaled_sustained_mbps,
            "sustained_min_windows": args.sustained_min_windows,
            "locality_top1pct_offset_bucket_min_share": args.locality_top1pct_min_share,
        },
        "observed": {
            "max_unique_write_users_in_single_trace": max_users,
            "basic_candidate_segments": len(basic_candidates),
            "scaled_candidate_segments": len(scaled_candidates),
            "independent_candidate_segments": len(independent_candidates),
            "top1pct_offset_bucket_write_byte_share": locality_share,
        },
    }


def choose_smoke_candidate(independent: list[CandidateSegment]) -> Optional[CandidateSegment]:
    if not independent:
        return None
    risk_rank = {"low": 0, "mid": 1, "high": 2, "unknown": 3}
    return sorted(
        independent,
        key=lambda s: (
            risk_rank.get(s.qps_risk, 3),
            -s.scaled_windows_geq70,
            -s.raw_mean_write_mbps,
            s.trace_id,
            s.start_window,
        ),
    )[0]


def add_bytes_to_bucket_counter(
    counter: collections.Counter,
    prefix: tuple[str, ...],
    io_offset: int,
    io_size: int,
    op_count: int,
    bucket_size: int,
) -> None:
    if io_size <= 0:
        return
    pos = max(0, io_offset)
    remaining = io_size
    while remaining > 0:
        bucket_idx = pos // bucket_size
        bucket_end = (bucket_idx + 1) * bucket_size
        n = min(remaining, bucket_end - pos)
        counter[prefix + (str(bucket_idx),)] += n * op_count
        pos += n
        remaining -= n


def top_share_record(counter: collections.Counter, fraction: float = 0.01) -> dict:
    total = sum(counter.values())
    if total <= 0 or not counter:
        return {
            "share": float("nan"),
            "top_n": 0,
            "bucket_count": len(counter),
            "top_bytes": 0,
            "total_bytes": total,
        }
    top_n = max(1, math.ceil(len(counter) * fraction))
    top_items = counter.most_common(top_n)
    top_bytes = sum(v for _, v in top_items)
    return {
        "share": top_bytes / total,
        "top_n": top_n,
        "bucket_count": len(counter),
        "top_bytes": top_bytes,
        "total_bytes": total,
        "top_keys_preview": [
            {"key": "|".join(str(part) for part in key), "write_bytes": value}
            for key, value in top_items[:5]
        ],
    }


def parse_bucket_size(token: str) -> int:
    raw = token.strip().lower()
    if raw.endswith("mib"):
        return int(float(raw[:-3]) * MB)
    if raw.endswith("mb"):
        return int(float(raw[:-2]) * 1_000_000)
    if raw.endswith("kib"):
        return int(float(raw[:-3]) * 1024)
    if raw.endswith("kb"):
        return int(float(raw[:-2]) * 1000)
    return int(raw)


def load_candidate_segments(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    candidates = payload.get("candidate_segments")
    if not isinstance(candidates, list):
        raise ValueError(f"{path} does not contain candidate_segments list")
    return candidates


def run_segment_locality_addendum(args: argparse.Namespace) -> int:
    if args.family != "baleen":
        print("segment locality addendum currently supports --family baleen only", file=sys.stderr)
        return 2
    if args.candidate_segments_json is None:
        print("--candidate-segments-json is required with --segment-locality-addendum", file=sys.stderr)
        return 2
    if args.out_addendum_json is None or args.out_addendum_md is None:
        print("--out-addendum-json and --out-addendum-md are required", file=sys.stderr)
        return 2
    for out_path in [args.out_addendum_json, args.out_addendum_md]:
        if out_path.exists() and not args.allow_overwrite:
            print(f"refusing to overwrite existing addendum artifact: {out_path}", file=sys.stderr)
            return 3

    trace_dirs = list(args.trace_dir)
    if not trace_dirs:
        if args.baleen_root is None:
            print("either --trace-dir or --baleen-root is required for Baleen", file=sys.stderr)
            return 2
        trace_dirs = find_baleen_trace_dirs(args.baleen_root, args.regions)
    trace_dir_by_id = {p.name: p for p in trace_dirs}
    candidates = load_candidate_segments(args.candidate_segments_json)
    if not candidates:
        print("no candidate segments to audit", file=sys.stderr)
        return 2

    bucket_sizes = [parse_bucket_size(x) for x in args.locality_bucket_sizes]
    if not bucket_sizes:
        bucket_sizes = [MB, 4 * MB, 8 * MB]
    primary_bucket_size = bucket_sizes[0]

    segment_states = []
    window_to_segments_by_trace: dict[str, dict[int, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for i, cand in enumerate(candidates):
        selected = set(str(u) for u in cand.get("selected_user_ids", []))
        if not selected:
            raise ValueError(f"candidate {cand.get('segment_id', i)} missing selected_user_ids")
        trace_id = cand["trace_id"]
        start_window = int(cand.get("start_window", math.floor(float(cand["start_time"]) / args.window_sec)))
        end_window = int(cand.get("end_window", math.floor((float(cand["end_time"]) - 1e-9) / args.window_sec)))
        state = {
            "candidate": cand,
            "selected": selected,
            "write_bytes": 0,
            "write_records": 0,
            "write_op_count": 0,
            "write_tenants": set(),
            "block_keys": set(),
            "block_bytes": collections.Counter(),
            "offset_bucket_bytes": collections.Counter(),
            "block_offset_bucket_bytes": {size: collections.Counter() for size in bucket_sizes},
        }
        segment_states.append(state)
        for w in range(start_window, end_window + 1):
            window_to_segments_by_trace[trace_id][w].append(i)

    started = time.time()
    for trace_id, win_map in sorted(window_to_segments_by_trace.items()):
        trace_dir = trace_dir_by_id.get(trace_id)
        if trace_dir is None:
            raise FileNotFoundError(f"candidate trace_id {trace_id} not found in trace dirs {sorted(trace_dir_by_id)}")
        header_cols = read_baleen_header(trace_dir)
        idx = {name: i for i, name in enumerate(header_cols)}
        required = ["block_id", "io_offset", "io_size", "op_time", "op_name", "user_name"]
        missing = [name for name in required if name not in idx]
        if missing:
            raise ValueError(f"{trace_dir} missing required columns: {missing}")
        has_rs_shard = "rs_shard_id" in idx
        trace_files = sorted(trace_dir.glob(args.trace_pattern))
        if not trace_files:
            raise FileNotFoundError(f"no trace files matching {args.trace_pattern!r} in {trace_dir}")
        records = 0
        matched_writes = 0
        for trace_file in trace_files:
            for parts in iter_trace_lines(trace_file):
                records += 1
                if len(parts) < len(header_cols):
                    continue
                op_kind, _op_name = normalize_baleen_op(parts[idx["op_name"]])
                if op_kind != "write":
                    continue
                op_time = parse_float(parts[idx["op_time"]])
                win = int(math.floor(op_time / args.window_sec))
                seg_indexes = win_map.get(win)
                if not seg_indexes:
                    continue
                user_name = parts[idx["user_name"]]
                io_size = parse_int(parts[idx["io_size"]])
                io_offset = parse_int(parts[idx["io_offset"]])
                op_count = parse_int(parts[idx["op_count"]], 1) if "op_count" in idx else 1
                if op_count <= 0:
                    op_count = 1
                byte_count = max(0, io_size) * op_count
                block_id = parts[idx["block_id"]]
                rs_shard = parts[idx["rs_shard_id"]] if has_rs_shard else "_"
                block_key = (block_id, rs_shard) if has_rs_shard else (block_id,)
                for seg_idx in seg_indexes:
                    state = segment_states[seg_idx]
                    cand = state["candidate"]
                    if not (float(cand["start_time"]) <= op_time < float(cand["end_time"])):
                        continue
                    if user_name not in state["selected"]:
                        continue
                    matched_writes += 1
                    state["write_bytes"] += byte_count
                    state["write_records"] += 1
                    state["write_op_count"] += op_count
                    state["write_tenants"].add(user_name)
                    state["block_keys"].add(block_key)
                    state["block_bytes"][block_key] += byte_count
                    add_bytes_to_bucket_counter(
                        state["offset_bucket_bytes"],
                        block_key,
                        io_offset,
                        io_size,
                        op_count,
                        primary_bucket_size,
                    )
                    for bucket_size in bucket_sizes:
                        add_bytes_to_bucket_counter(
                            state["block_offset_bucket_bytes"][bucket_size],
                            block_key,
                            io_offset,
                            io_size,
                            op_count,
                            bucket_size,
                        )
        print(
            f"[addendum] {trace_id} records={records:,} matched_selected_writes={matched_writes:,}",
            file=sys.stderr,
            flush=True,
        )

    segment_results = []
    for state in segment_states:
        cand = state["candidate"]
        block_share = top_share_record(state["block_bytes"])
        offset_share = top_share_record(state["offset_bucket_bytes"])
        block_offset_shares = {
            f"{size // MB}MiB": top_share_record(counter)
            for size, counter in state["block_offset_bucket_bytes"].items()
        }
        candidate_metrics = {
            key: cand.get(key)
            for key in [
                "segment_id",
                "trace_id",
                "start_time",
                "end_time",
                "n_tenants",
                "n_write_tenants",
                "raw_mean_write_mbps",
                "scaled_mean_write_mbps",
                "scaled_windows_geq70",
                "top4_drift_rate",
                "frozen_overlap",
                "high_set_changes",
            ]
        }
        max_block_offset_share = max(
            (v["share"] for v in block_offset_shares.values() if not math.isnan(v["share"])),
            default=float("nan"),
        )
        locality_scores = [
            block_share["share"],
            offset_share["share"],
            max_block_offset_share,
        ]
        locality_score = max((v for v in locality_scores if not math.isnan(v)), default=float("nan"))
        pass_reasons = []
        if not math.isnan(block_share["share"]) and block_share["share"] >= args.segment_locality_threshold:
            pass_reasons.append("top1pct_block_share")
        if not math.isnan(offset_share["share"]) and offset_share["share"] >= args.segment_locality_threshold:
            pass_reasons.append("top1pct_offset_bucket_share")
        for name, row in block_offset_shares.items():
            if not math.isnan(row["share"]) and row["share"] >= args.segment_locality_threshold:
                pass_reasons.append(f"top1pct_block_offset_bucket_share_{name}")
        write_records = state["write_records"]
        unique_block_ratio = len(state["block_keys"]) / write_records if write_records else float("nan")
        row = {
            **candidate_metrics,
            "scope": "selected_user_ids_only",
            "selected_user_ids": cand.get("selected_user_ids", []),
            "write_tenant_ids": sorted(state["write_tenants"]),
            "write_tenants": len(state["write_tenants"]),
            "write_records": write_records,
            "write_op_count": state["write_op_count"],
            "write_bytes": state["write_bytes"],
            "write_mb": state["write_bytes"] / MB,
            "unique_write_blocks": len(state["block_keys"]),
            "unique_write_block_ratio": unique_block_ratio,
            "top1pct_block_write_byte_share": block_share["share"],
            "top1pct_block_write_byte_detail": block_share,
            "top1pct_offset_bucket_write_byte_share": offset_share["share"],
            "top1pct_offset_bucket_write_byte_detail": offset_share,
            "top1pct_block_offset_bucket_write_byte_share_by_bucket": {
                name: detail["share"] for name, detail in block_offset_shares.items()
            },
            "top1pct_block_offset_bucket_write_byte_detail_by_bucket": block_offset_shares,
            "segment_locality_score": locality_score,
            "segment_locality_pass": bool(pass_reasons),
            "segment_locality_pass_reasons": pass_reasons,
        }
        segment_results.append(row)

    passing = [r for r in segment_results if r["segment_locality_pass"]]
    top10 = sorted(
        segment_results,
        key=lambda r: (
            -safe_sort_float(r["segment_locality_score"]),
            -safe_sort_float(r.get("raw_mean_write_mbps")),
            r["trace_id"],
            r["start_time"],
        ),
    )[:10]
    addendum_pass = len(passing) >= args.want_segments
    recommended = top10[0] if addendum_pass and top10 and top10[0]["segment_locality_pass"] else None

    report = {
        "generated_at": _dt.datetime.now().astimezone().isoformat(),
        "input_candidate_segments_json": str(args.candidate_segments_json),
        "trace_root": str(args.baleen_root) if args.baleen_root else None,
        "config": {
            "family": args.family,
            "regions": args.regions,
            "trace_pattern": args.trace_pattern,
            "window_sec": args.window_sec,
            "scope": "selected_user_ids_only",
            "bucket_sizes": [f"{size // MB}MiB" for size in bucket_sizes],
            "offset_bucket_mode": "block_local_proxy_no_global_absolute_offset",
            "segment_locality_threshold": args.segment_locality_threshold,
            "want_segments": args.want_segments,
            "future_budget_contract": "high/mid/low = 11/7/3 MB/s, total 112 MB/s",
        },
        "summary": {
            "segments_total": len(segment_results),
            "segments_passing_segment_locality": len(passing),
            "addendum_pass": addendum_pass,
            "top10_strongest_segments": top10,
            "recommended_static_smoke_candidate": recommended,
            "recommendation": (
                "recommend_static_only_rocksdb_smoke_do_not_execute_in_this_turn"
                if addendum_pass
                else "baleen_mainline_stop_recommend_tencent_osca_hourly_trace_audit"
            ),
        },
        "segments": segment_results,
    }
    args.out_addendum_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_addendum_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_addendum_json.write_text(json.dumps(report, indent=2, default=json_default))
    args.out_addendum_md.write_text(render_segment_locality_addendum_md(report))
    elapsed = time.time() - started
    print(json.dumps({
        "out_json": str(args.out_addendum_json),
        "out_md": str(args.out_addendum_md),
        "segments_total": len(segment_results),
        "segments_passing_segment_locality": len(passing),
        "addendum_pass": addendum_pass,
        "elapsed_sec": elapsed,
    }, indent=2))
    return 0


def safe_sort_float(value) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("-inf")
    if math.isnan(f):
        return float("-inf")
    return f


def render_segment_locality_addendum_md(report: dict) -> str:
    summary = report["summary"]
    cfg = report["config"]
    lines = []
    lines.append("# Baleen 0.1% Segment Locality Addendum\n")
    lines.append(f"- generated_at: {report['generated_at']}")
    lines.append(f"- input_candidate_segments_json: `{report['input_candidate_segments_json']}`")
    lines.append(f"- scope: `{cfg['scope']}`")
    lines.append(f"- offset_bucket_mode: `{cfg['offset_bucket_mode']}`")
    lines.append(f"- bucket_sizes: {', '.join(cfg['bucket_sizes'])}")
    lines.append(f"- segment_locality_threshold: {cfg['segment_locality_threshold']}")
    lines.append(f"- future_budget_contract: {cfg['future_budget_contract']}")
    lines.append("")
    lines.append("## Verdict\n")
    lines.append(f"- segments_total: {summary['segments_total']}")
    lines.append(f"- segments_passing_segment_locality: {summary['segments_passing_segment_locality']}")
    lines.append(f"- addendum_pass: **{summary['addendum_pass']}**")
    lines.append(f"- recommendation: `{summary['recommendation']}`")
    rec = summary.get("recommended_static_smoke_candidate")
    if rec:
        lines.append(f"- recommended_smoke_segment_id: `{rec['segment_id']}`")
        lines.append(f"- recommended_selected_user_ids: {', '.join(rec['selected_user_ids'])}")
    lines.append("")
    lines.append("## Top 10 Locality Segments\n")
    lines.append("| rank | segment | trace | pass | score | block | offset 1MiB | block+offset 1/4/8MiB | unique block ratio | write MB | write recs | write tenants | scaled MB/s | users |")
    lines.append("|---:|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---|")
    for i, row in enumerate(summary["top10_strongest_segments"], 1):
        shares = row["top1pct_block_offset_bucket_write_byte_share_by_bucket"]
        share_text = "/".join(f"{shares.get(k, float('nan')):.4f}" for k in ["1MiB", "4MiB", "8MiB"])
        lines.append(
            f"| {i} | {row['segment_id']} | {row['trace_id']} | {row['segment_locality_pass']} "
            f"| {row['segment_locality_score']:.4f} "
            f"| {row['top1pct_block_write_byte_share']:.4f} "
            f"| {row['top1pct_offset_bucket_write_byte_share']:.4f} "
            f"| {share_text} "
            f"| {row['unique_write_block_ratio']:.4f} "
            f"| {row['write_mb']:.2f} | {row['write_records']} | {row['write_tenants']} "
            f"| {row['scaled_mean_write_mbps']:.2f} "
            f"| {', '.join(row['selected_user_ids'])} |"
        )
    lines.append("")
    lines.append("## Interpretation\n")
    if summary["addendum_pass"]:
        lines.append(
            "At least five independent candidate segments pass the unchanged 5% segment-level locality threshold. "
            "This supports recommending one static-only RocksDB smoke next, without changing the 112 MB/s budget contract."
        )
    else:
        lines.append(
            "Fewer than five independent candidate segments pass the unchanged 5% segment-level locality threshold. "
            "Baleen should stop on this mainline and the next trace-family audit should move to Tencent OSCA."
        )
    lines.append("")
    return "\n".join(lines)


def render_md(report: dict, candidates: list[dict]) -> str:
    cfg = report["config"]
    gate = report["gate_a"]
    lines = []
    lines.append("# Baleen 0.1% Trace-Family Audit\n")
    lines.append(f"- generated_at: {report['generated_at']}")
    lines.append(f"- verdict_gate_a: **{'pass' if gate['pass'] else 'fail'}**")
    lines.append(f"- family: {cfg['family']}")
    lines.append(f"- value_size: {cfg['value_size']} bytes")
    lines.append(f"- future_budget_contract: high/mid/low = 11/7/3 MB/s, total 112 MB/s")
    lines.append("")
    lines.append("## Input Files\n")
    for item in report.get("input_archives", []):
        lines.append(f"- archive: `{item['path']}` ({item['size_bytes']} bytes)")
    for tr in report["traces"]:
        sizes = ", ".join(f"{Path(f['path']).name}={f['size_bytes']}B" for f in tr["trace_files"])
        lines.append(f"- {tr['trace_id']}: `{tr['trace_dir']}`; {sizes}")
    lines.append("")
    lines.append("## Combined Supply Diagnostics\n")
    c = report["combined"]
    lines.append(f"- records_total: {c['records_total']}")
    lines.append(f"- duration_sec_sum_across_traces: {c['duration_sec_sum_across_traces']:.3f}")
    lines.append(f"- write_record_share: {c['write_record_share']:.4f}")
    lines.append(f"- write_op_count_share: {c['write_op_count_share']:.4f}")
    lines.append(f"- raw_write_mbps_over_summed_duration: {c['raw_write_mbps_over_summed_duration']:.4f}")
    ws = c["write_size_bytes"]
    lines.append(f"- write_size_bytes p50/p90/p99: {ws['p50']:.1f} / {ws['p90']:.1f} / {ws['p99']:.1f}")
    w20 = c["global_write_mbps_20s"]
    b1 = c["global_write_mbps_1s_burst"]
    lines.append(f"- 20s global write MB/s p50/p95/p99: {w20['p50']:.4f} / {w20['p95']:.4f} / {w20['p99']:.4f}")
    lines.append(f"- 1s burst write MB/s p50/p95/p99: {b1['p50']:.4f} / {b1['p95']:.4f} / {b1['p99']:.4f}")
    loc = c["locality"]
    lines.append(
        "- locality: unique_block_ratio="
        f"{loc['unique_block_ratio_records']:.4f}, "
        f"top1pct_offset_bucket_share={loc['top1pct_offset_bucket_write_byte_share']:.4f}, "
        f"top1pct_block_share={loc['top1pct_block_write_byte_share']:.4f}"
    )
    lines.append("")
    lines.append("## Mapping Diagnostics\n")
    lines.append("| mapping | offered QPS mean | offered QPS p95/20s | logical MB/s mean @1KiB | per-thread QPS p95 | risk |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for name, m in c["mapping_diagnostics"].items():
        lines.append(
            f"| {name} | {m['offered_qps_mean']:.2f} | {m['offered_qps_p95_20s']:.2f} "
            f"| {m['logical_mbps_mean_at_value_size']:.4f} "
            f"| {m['per_thread_qps_p95_20s']:.2f} | {m['per_thread_qps_risk']} |"
        )
    lines.append("")
    lines.append("## Candidate Segment Summary\n")
    cs = report["candidate_summary"]
    lines.append(f"- global_scale: {cs['global_scale']}")
    lines.append(f"- basic_candidate_segments: {cs['basic_candidate_segments']}")
    lines.append(f"- scaled_candidate_segments: {cs['scaled_candidate_segments']}")
    lines.append(f"- independent_candidate_segments: {cs['independent_candidate_segments']}")
    lines.append("")
    lines.append("## Gate A\n")
    for k, v in gate["checks"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    rec = report.get("recommended_static_smoke_candidate")
    if rec:
        lines.append("## Recommended Static-Only Smoke Candidate\n")
        lines.append(f"- segment_id: {rec['segment_id']}")
        lines.append(f"- trace_id: {rec['trace_id']}")
        lines.append(f"- start_time: {rec['start_time']}")
        lines.append(f"- end_time: {rec['end_time']}")
        lines.append(f"- raw_mean_write_mbps: {rec['raw_mean_write_mbps']:.4f}")
        lines.append(f"- scaled_mean_write_mbps: {rec['scaled_mean_write_mbps']:.4f}")
        lines.append(f"- scaled_windows_geq70: {rec['scaled_windows_geq70']}")
        lines.append(f"- qps_risk_1KiB_chunk: {rec['qps_risk']}")
        lines.append(f"- selected_user_ids: {', '.join(rec['selected_user_ids'])}")
        lines.append("")
    lines.append("## Candidate Preview\n")
    lines.append("| segment | trace | n_tenants | n_write | raw MB/s | scaled MB/s | geq70 | drift | frozen overlap | changes | qps risk |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for seg in candidates[:20]:
        lines.append(
            f"| {seg['segment_id']} | {seg['trace_id']} | {seg['n_tenants']} "
            f"| {seg.get('n_write_tenants')} | {seg['raw_mean_write_mbps']:.4f} | {seg['scaled_mean_write_mbps']:.2f} "
            f"| {seg['scaled_windows_geq70']} | {seg['top4_drift_rate']:.3f} "
            f"| {seg['frozen_overlap']:.3f} | {seg['high_set_changes']} | {seg['qps_risk']} |"
        )
    lines.append("")
    return "\n".join(lines)


def json_default(obj):
    if isinstance(obj, collections.Counter):
        return dict(obj)
    if isinstance(obj, set):
        return sorted(obj)
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--family", default="baleen", choices=["baleen", "tencent"])
    p.add_argument("--segment-locality-addendum", action="store_true",
                   help="Compute per-candidate segment locality from an existing candidate JSON.")
    p.add_argument("--baleen-root", type=Path, help="Root containing storage/.../Region*/full.header")
    p.add_argument("--trace-dir", action="append", type=Path, default=[],
                   help="Explicit Baleen trace directory. May be repeated.")
    p.add_argument("--regions", nargs="*", default=["Region7", "Region6", "Region5"])
    p.add_argument("--trace-pattern", default="full_0_0.1.trace")
    p.add_argument("--candidate-segments-json", type=Path)
    p.add_argument("--out-addendum-json", type=Path)
    p.add_argument("--out-addendum-md", type=Path)
    p.add_argument("--allow-overwrite", action="store_true")
    p.add_argument("--input-archive", action="append", type=Path, default=[])
    p.add_argument("--out-json", type=Path)
    p.add_argument("--out-md", type=Path)
    p.add_argument("--out-candidates-json", type=Path)
    p.add_argument("--window-sec", type=int, default=20)
    p.add_argument("--windows-per-segment", type=int, default=8)
    p.add_argument("--segment-stride-windows", type=int, default=1)
    p.add_argument("--tenant-count", type=int, default=16)
    p.add_argument("--high-count", type=int, default=4)
    p.add_argument("--low-count", type=int, default=4)
    p.add_argument("--want-segments", type=int, default=5)
    p.add_argument("--min-independent-gap-windows", type=int, default=8)
    p.add_argument("--high-set-min-changes", type=int, default=3)
    p.add_argument("--frozen-overlap-max", type=float, default=2.75)
    p.add_argument("--target-scale-mbps", type=float, default=96.0)
    p.add_argument("--scaled-sustained-mbps", type=float, default=70.0)
    p.add_argument("--sustained-min-windows", type=int, default=6)
    p.add_argument("--locality-top1pct-min-share", type=float, default=0.05)
    p.add_argument("--offset-bucket-bytes", type=int, default=1024 * 1024)
    p.add_argument("--locality-bucket-sizes", nargs="*", default=["1MiB", "4MiB", "8MiB"],
                   help="Bucket sizes for segment-level block+offset locality addendum.")
    p.add_argument("--segment-locality-threshold", type=float, default=0.05)
    p.add_argument("--value-size", type=int, default=1024)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--max-records-per-trace", type=int, default=None)
    p.add_argument("--progress-every-records", type=int, default=100_000)
    args = p.parse_args()
    if args.baleen_root is not None:
        args.baleen_root = args.baleen_root.expanduser().resolve()
    args.trace_dir = [p.expanduser().resolve() for p in args.trace_dir]
    if args.candidate_segments_json is not None:
        args.candidate_segments_json = args.candidate_segments_json.expanduser().resolve()
    if args.out_addendum_json is not None:
        args.out_addendum_json = args.out_addendum_json.expanduser().resolve()
    if args.out_addendum_md is not None:
        args.out_addendum_md = args.out_addendum_md.expanduser().resolve()
    args.input_archive = [p.expanduser().resolve() for p in args.input_archive]
    if args.out_json is not None:
        args.out_json = args.out_json.expanduser().resolve()
    if args.out_md is not None:
        args.out_md = args.out_md.expanduser().resolve()
    if args.out_candidates_json is not None:
        args.out_candidates_json = args.out_candidates_json.expanduser().resolve()

    if args.segment_locality_addendum:
        return run_segment_locality_addendum(args)

    if args.family == "tencent":
        print("Tencent schema is reserved but not implemented in this audit turn.", file=sys.stderr)
        return 2
    if args.family != "baleen":
        print(f"unsupported family: {args.family}", file=sys.stderr)
        return 2
    if args.out_json is None or args.out_md is None or args.out_candidates_json is None:
        print("--out-json, --out-md, and --out-candidates-json are required for the main audit", file=sys.stderr)
        return 2

    trace_dirs = list(args.trace_dir)
    if not trace_dirs:
        if args.baleen_root is None:
            print("either --trace-dir or --baleen-root is required for Baleen", file=sys.stderr)
            return 2
        trace_dirs = find_baleen_trace_dirs(args.baleen_root, args.regions)
    trace_stats: list[TraceStats] = []
    for trace_dir in trace_dirs:
        trace_id = trace_dir.name
        trace_stats.append(
            parse_baleen_trace_dir(
                trace_dir=trace_dir,
                trace_id=trace_id,
                trace_pattern=args.trace_pattern,
                window_sec=args.window_sec,
                value_size=args.value_size,
                offset_bucket_bytes=args.offset_bucket_bytes,
                max_records=args.max_records_per_trace,
                progress_every_records=args.progress_every_records,
            )
        )

    all_basic: list[CandidateSegment] = []
    for stats in trace_stats:
        all_basic.extend(build_basic_candidates(stats, args))
    raw_means = [s.raw_mean_write_mbps for s in all_basic if s.raw_mean_write_mbps > 0]
    median_raw = statistics.median(raw_means) if raw_means else float("nan")
    global_scale = args.target_scale_mbps / median_raw if raw_means and median_raw > 0 else float("nan")
    apply_scaled_gates(all_basic, global_scale, args)
    scaled_candidates = [s for s in all_basic if s.scaled_gate_pass]
    independent_candidates = pick_independent(scaled_candidates, args.min_independent_gap_windows)

    trace_summaries = [s.to_summary(args.threads) for s in trace_stats]
    combined = combined_summary(trace_stats, args.threads, args.value_size)
    gate = gate_a_verdict(
        trace_summaries,
        combined,
        all_basic,
        scaled_candidates,
        independent_candidates,
        args,
    )
    rec = choose_smoke_candidate(independent_candidates) if gate["pass"] else None

    git_sha = ""
    try:
        git_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        pass

    candidate_dicts = [s.to_dict() for s in independent_candidates]
    report = {
        "generated_at": _dt.datetime.now().astimezone().isoformat(),
        "config": {
            "family": args.family,
            "regions": args.regions,
            "trace_pattern": args.trace_pattern,
            "window_sec": args.window_sec,
            "windows_per_segment": args.windows_per_segment,
            "tenant_count": args.tenant_count,
            "high_count": args.high_count,
            "low_count": args.low_count,
            "value_size": args.value_size,
            "threads_for_qps_risk": args.threads,
            "offset_bucket_bytes": args.offset_bucket_bytes,
            "target_scale_mbps": args.target_scale_mbps,
            "scaled_sustained_mbps": args.scaled_sustained_mbps,
            "sustained_min_windows": args.sustained_min_windows,
            "min_independent_gap_windows": args.min_independent_gap_windows,
            "qps_risk_thresholds_per_thread": {"low_lt": 5000, "mid_lt": 20000, "high_ge": 20000},
            "git_sha": git_sha,
        },
        "budget_contract": {
            "future_high_budget_mbps": 11,
            "future_mid_budget_mbps": 7,
            "future_low_budget_mbps": 3,
            "future_total_budget_mbps": 112,
            "note": "Audit scale is diagnostic only and does not change the fixed 112 MB/s budget contract.",
        },
        "input_archives": file_inventory(args.input_archive),
        "traces": trace_summaries,
        "combined": combined,
        "candidate_summary": {
            "basic_candidate_segments": len(all_basic),
            "scaled_candidate_segments": len(scaled_candidates),
            "independent_candidate_segments": len(independent_candidates),
            "raw_mean_write_mbps_median_over_basic_candidates": median_raw,
            "global_scale": global_scale,
        },
        "gate_a": gate,
        "recommended_static_smoke_candidate": rec.to_dict() if rec else None,
        "candidate_segments_path": str(args.out_candidates_json),
    }

    candidates_payload = {
        "generated_at": report["generated_at"],
        "warning": "Audit candidates only; not a formal RocksDB selected_segments.json.",
        "global_scale": global_scale,
        "candidate_count": len(candidate_dicts),
        "candidate_segments": candidate_dicts,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_candidates_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2, default=json_default))
    args.out_candidates_json.write_text(json.dumps(candidates_payload, indent=2, default=json_default))
    args.out_md.write_text(render_md(report, candidate_dicts))
    print(json.dumps({
        "out_json": str(args.out_json),
        "out_md": str(args.out_md),
        "out_candidates_json": str(args.out_candidates_json),
        "gate_a_pass": gate["pass"],
        "independent_candidate_segments": len(independent_candidates),
        "global_scale": global_scale,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
