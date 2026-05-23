#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path


def tenant_name(i: int) -> str:
    return f"tenant{i}"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_sha_for(path: Path) -> str:
    env_sha = os.environ.get("GIT_SHA")
    if env_sha:
        return env_sha
    try:
        return subprocess.check_output(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        for candidate in [path / "GIT_SHA", path / "remote" / "GIT_SHA", path / "scripts" / "GIT_SHA"]:
            try:
                value = candidate.read_text().strip()
            except OSError:
                continue
            if value:
                return value
        return "unknown"


def script_dir(root: Path) -> Path:
    for candidate in (root / "remote", root / "scripts", Path(__file__).resolve().parent):
        if (candidate / "continuous_kv_harness.cc").exists():
            return candidate
    return root / "remote"


def load_cachelib_schedule(args: argparse.Namespace) -> None:
    """Load a frozen segment from ``selected_segments.json`` and stash a per-
    window, per-tenant replay payload on ``args``.

    Side-effects:
      * args.cachelib_payload[(tenant_index, window)] -> {true_tier,
        write_qps, read_qps, hot_frac, trace_usecase}
      * args.cachelib_per_window_tiers[window] -> {"high":[...], "mid":[...], "low":[...]}
      * args.cachelib_meta -> reproducibility metadata for the trial JSON.
      * Overrides args.tenant_count / num_keys / prefill_keys / window_sec /
        windows / duration_sec / high_count / low_count to match the segment.

    The controller still sees only previous-window observed metrics
    (write_qps_target is recorded into metrics.csv by the harness so it stays
    in the observed-signal envelope; true tier is *not* surfaced to the
    controller). Window 0 is reported as a warmup window in the metadata so
    downstream analyzers can exclude it from performance/overlap metrics.
    """
    if not args.cachelib_schedule:
        return
    schedule_path = Path(args.cachelib_schedule)
    payload = json.loads(schedule_path.read_text())
    segs = payload.get("selected_segments") or []
    if not segs:
        raise SystemExit(f"--cachelib-schedule {schedule_path} has no selected_segments")
    target = str(args.cachelib_segment_id) if args.cachelib_segment_id is not None else None
    seg = None
    if target is not None:
        for s in segs:
            if str(s.get("segment_index")) == target:
                seg = s
                break
        if seg is None:
            raise SystemExit(
                f"segment_index={target} not in {schedule_path}; "
                f"available={[s.get('segment_index') for s in segs]}"
            )
    else:
        seg = segs[0]
    cfg = payload.get("config", {})
    agg = payload.get("aggregate", {})
    global_scale = float(agg.get("global_scale", 1.0))
    window_sec = int(cfg.get("window_sec", 20))
    windows = int(cfg.get("windows_per_segment", 8))
    tenant_count = int(cfg.get("tenant_count", 16))
    high_count = int(cfg.get("high_count", 4))
    low_count = int(cfg.get("low_count", 4))
    tenant_ids = list(seg["tenant_ids"])
    if len(tenant_ids) < tenant_count:
        raise SystemExit(
            f"segment {seg.get('segment_index')} has only {len(tenant_ids)} tenants "
            f"but tenant_count={tenant_count}"
        )
    tenant_ids = tenant_ids[:tenant_count]
    detail = seg.get("detail") or {}
    keyspace = int(detail.get("_segment_keyspace_global", 80000))

    tenant_index_of = {tid: i for i, tid in enumerate(tenant_ids)}
    per_window_tiers: list[dict[str, list[str]]] = []
    cachelib_payload: dict[tuple[int, int], dict[str, object]] = {}
    for w in range(windows):
        rows = []
        for tid in tenant_ids:
            d = detail.get(tid)
            if d is None:
                raise SystemExit(f"segment detail missing for tenant {tid}")
            wb = float(d["per_window_write_bytes"][w])
            ro = float(d["per_window_read_ops"][w])
            us = int(d["per_window_unique_sample_size"][w])
            tops = int(d["per_window_total_ops"][w])
            unique_ratio = min(1.0, us / max(1, tops))
            hot_frac = max(0.15, min(0.85, 0.15 + 0.70 * unique_ratio))
            write_qps = global_scale * wb / 1024.0 / float(window_sec)
            read_qps = global_scale * ro / float(window_sec)
            rows.append({
                "trace_usecase": tid,
                "tenant_index": tenant_index_of[tid],
                "write_bytes": wb,
                "write_qps": write_qps,
                "read_qps": read_qps,
                "hot_frac": hot_frac,
            })
        # Pick true high / true low by current-window write_bytes.
        ranked = sorted(rows, key=lambda r: r["write_bytes"], reverse=True)
        high_idx = {r["tenant_index"] for r in ranked[:high_count]}
        low_idx = {r["tenant_index"] for r in ranked[-low_count:]}
        tiers_w = {"high": [], "mid": [], "low": []}
        for r in rows:
            idx = r["tenant_index"]
            name = tenant_name(idx)
            if idx in high_idx:
                tier = "high"
            elif idx in low_idx:
                tier = "low"
            else:
                tier = "mid"
            tiers_w[tier].append(name)
            cachelib_payload[(idx, w)] = {
                "true_tier": tier,
                "write_qps": r["write_qps"],
                "read_qps": r["read_qps"],
                "hot_frac": r["hot_frac"],
                "trace_usecase": r["trace_usecase"],
            }
        for k in tiers_w:
            tiers_w[k] = sorted(tiers_w[k])
        per_window_tiers.append(tiers_w)

    args.tenant_count = tenant_count
    args.high_count = high_count
    args.low_count = low_count
    args.num_keys = keyspace
    args.prefill_keys = keyspace
    args.value_size = int(cfg.get("value_size", args.value_size))
    args.window_sec = window_sec
    args.duration_sec = windows * window_sec
    args.windows = windows
    args.cachelib_payload = cachelib_payload
    args.cachelib_per_window_tiers = per_window_tiers
    mid_count = max(0, tenant_count - high_count - low_count)
    budget_contract = {
        "high": int(args.high_budget),
        "mid": int(args.mid_budget),
        "low": int(args.low_budget),
        "aggregate": int(
            high_count * args.high_budget
            + mid_count * args.mid_budget
            + low_count * args.low_budget
        ),
        "units": "bytes_per_second",
    }
    args.cachelib_meta = {
        "cachelib_external_version": "v1b_hotness_mapping_fix",
        "git_sha": git_sha_for(args.root),
        "selected_segments_sha256": file_sha256(schedule_path),
        "rocksdb_version": "8.9.1",
        "budget_contract": budget_contract,
        "hot_frac_semantics": "fraction_of_keyspace_range",
        "hotness_mapping": "hot_frac=clamp(0.15+0.70*unique_ratio,0.15,0.85)",
        "segment_index": seg.get("segment_index"),
        "segment_id": seg.get("segment_index"),
        "segment_start_op_time": seg.get("start_op_time"),
        "segment_end_op_time": seg.get("end_op_time"),
        "segment_day_indices": seg.get("day_indices"),
        "segment_keyspace_global": keyspace,
        "global_scale": global_scale,
        "trace_dataset": cfg.get("trace_dataset", "Meta CacheLib kvcache/202401"),
        "trace_files": cfg.get("trace_files", []),
        "trace_files_sha256": cfg.get("trace_files_sha256", {}),
        "value_size": args.value_size,
        "tenant_count": tenant_count,
        "window_sec": window_sec,
        "windows_per_segment": windows,
        "warmup_windows": [0],
        "tenant_ids": tenant_ids,
        "selected_segments_path": str(schedule_path),
    }


def tenant_rates(
    args: argparse.Namespace, tenant_index: int, window: int, true_tier: str
) -> tuple[float, float, float]:
    payload = getattr(args, "cachelib_payload", None)
    if payload is not None:
        row = payload.get((tenant_index, window))
        if row is not None:
            return (
                float(row["write_qps"]),
                float(row["read_qps"]),
                float(row["hot_frac"]),
            )
    return tier_rates(args, true_tier)


def circular_distance(a: float, b: float, n: int) -> float:
    d = abs(a - b) % n
    return min(d, n - d)


def clamp(x: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, x))


def bounded_step(old: float, target: float, step: float) -> float:
    return old + clamp(target - old, -step, step)


def workload_tiers(args: argparse.Namespace, window: int) -> dict[str, list[str]]:
    cl_tiers = getattr(args, "cachelib_per_window_tiers", None)
    if cl_tiers is not None:
        idx = max(0, min(window, len(cl_tiers) - 1))
        return {k: list(v) for k, v in cl_tiers[idx].items()}
    if args.windows <= 1:
        center = args.initial_hot_center
    else:
        frac = (window + 0.5) / args.windows
        center = args.initial_hot_center + args.drift_tenants * frac
    scores = []
    for i in range(args.tenant_count):
        dist = circular_distance(float(i), center, args.tenant_count)
        score = math.cos((2.0 * math.pi * dist) / args.tenant_count)
        scores.append((score, i))
    ranked = [i for _, i in sorted(scores, reverse=True)]
    high = sorted(tenant_name(i) for i in ranked[: args.high_count])
    low = sorted(tenant_name(i) for i in ranked[-args.low_count :])
    high_set = set(high)
    low_set = set(low)
    mid = sorted(tenant_name(i) for i in range(args.tenant_count) if tenant_name(i) not in high_set | low_set)
    return {"high": high, "mid": mid, "low": low}


def tenant_tier(name: str, tiers: dict[str, list[str]]) -> str:
    if name in tiers["high"]:
        return "high"
    if name in tiers["low"]:
        return "low"
    return "mid"


def tier_budget(args: argparse.Namespace, tier: str) -> int:
    if tier == "high":
        return args.high_budget
    if tier == "low":
        return args.low_budget
    return args.mid_budget


def tier_rates(args: argparse.Namespace, tier: str) -> tuple[float, float, float]:
    if tier == "high":
        return args.high_write_qps, args.high_read_qps, args.high_hot_frac
    if tier == "low":
        return args.low_write_qps, args.low_read_qps, args.low_hot_frac
    return args.mid_write_qps, args.mid_read_qps, args.mid_hot_frac


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_control(
    args: argparse.Namespace,
    path: Path,
    budget_path: Path,
    tenant_index: int,
    true_tier: str,
    assigned_tier: str,
    budget: int,
    window: int = 0,
    stop: int = 0,
) -> None:
    write_qps, read_qps, hot_frac = tenant_rates(args, tenant_index, window, true_tier)
    hot_keys = max(1, int(args.num_keys * hot_frac))
    # Move the hot range slowly inside each tenant so the run is not a single
    # fixed hot spot, but keep it deterministic for reproducibility.
    hot_start = int((tenant_index * 9973 + args.window_sec * 31) % max(1, args.num_keys))
    write_atomic(
        path,
        "\n".join(
            [
                f"write_qps={write_qps:.3f}",
                f"read_qps={read_qps:.3f}",
                f"hot_start={hot_start}",
                f"hot_keys={hot_keys}",
                f"true_tier={true_tier}",
                f"assigned_tier={assigned_tier}",
                f"budget={budget}",
                f"stop={stop}",
                "",
            ]
        ),
    )
    write_atomic(budget_path, f"{budget}\n")


def validate_budget(args: argparse.Namespace, allocation: dict[str, dict[str, object]]) -> None:
    if args.policy == "unlimited":
        return
    got = sum(int(v["budget"]) for v in allocation.values())
    expected = args.tenant_count * args.mid_budget
    if got != expected:
        raise ValueError(f"budget changed: got {got}, expected {expected}")


def rebalance_budget_exact(
    args: argparse.Namespace,
    allocation: dict[str, dict[str, object]],
) -> dict[str, object] | None:
    if args.policy == "unlimited":
        return None
    expected = int(args.tenant_count) * int(args.mid_budget)
    got = sum(int(v["budget"]) for v in allocation.values())
    diff = expected - got
    if diff == 0:
        return None

    mid_names = sorted(
        name for name, row in allocation.items()
        if str(row.get("assigned_tier")) == "mid"
    )
    names = mid_names or sorted(allocation)
    if not names:
        raise ValueError("cannot rebalance an empty allocation")

    step = 1 if diff > 0 else -1
    q, r = divmod(abs(diff), len(names))
    adjustments: dict[str, int] = {}
    for idx, name in enumerate(names):
        adj = step * (q + (1 if idx < r else 0))
        if adj == 0:
            continue
        new_budget = int(allocation[name]["budget"]) + adj
        if new_budget < 0:
            raise ValueError(f"budget rebalance would make {name} negative")
        allocation[name] = dict(allocation[name])
        allocation[name]["budget"] = new_budget
        adjustments[name] = adj

    after = sum(int(v["budget"]) for v in allocation.values())
    if after != expected:
        raise ValueError(f"budget rebalance failed: got {after}, expected {expected}")
    return {
        "target_total_budget": expected,
        "total_budget_before_rebalance": got,
        "total_budget_after_rebalance": after,
        "adjustment_bytes": diff,
        "adjustment_scope": "mid" if mid_names else "all",
        "per_tenant_adjustments": adjustments,
    }


def _row_float(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key, "0") or 0.0)
    except ValueError:
        return 0.0


def pressure_score_pressure(args: argparse.Namespace, row: dict[str, str]) -> float:
    write_qps = _row_float(row, "write_ops") / max(1, args.window_sec)
    read_qps = _row_float(row, "read_ops") / max(1, args.window_sec)
    compact_mb = _row_float(row, "compact_output_bytes") / (1024.0 * 1024.0)
    pending_mb = _row_float(row, "pending_compaction_bytes") / (1024.0 * 1024.0)
    l0 = _row_float(row, "l0_files")
    tail_ms = min(_row_float(row, "write_p99_us") / 1000.0, 80.0) + min(
        _row_float(row, "write_p999_us") / 5000.0, 40.0
    )
    return 0.55 * tail_ms + 1.05 * write_qps - 0.02 * read_qps + 0.14 * compact_mb + 0.12 * pending_mb + 5.0 * l0


def demand_score_terms(args: argparse.Namespace, row: dict[str, str]) -> dict[str, float]:
    window_sec = max(1, args.window_sec)
    write_qps_target = _row_float(row, "write_qps_target")
    completed_write_qps = _row_float(row, "write_ops") / window_sec
    completion_gap = max(0.0, write_qps_target - completed_write_qps)
    compact_mb = _row_float(row, "compact_output_bytes") / (1024.0 * 1024.0)
    pending_mb = _row_float(row, "pending_compaction_bytes") / (1024.0 * 1024.0)
    l0 = min(_row_float(row, "l0_files"), 6.0)
    tail_ms = min(_row_float(row, "write_p99_us") / 1000.0, 80.0) + min(
        _row_float(row, "write_p999_us") / 5000.0, 40.0
    )
    anchor_coeff = float(getattr(args, "score_anchor", 6.00))
    residual_scale = float(getattr(args, "score_residual_scale", 1.0))
    drop_residual = bool(getattr(args, "score_drop_residual", False))
    if drop_residual:
        residual_scale = 0.0
    anchor = anchor_coeff * write_qps_target / 1000.0
    gap_term = residual_scale * 0.60 * completion_gap / 1000.0
    tail_term = residual_scale * 0.05 * tail_ms
    compact_term = residual_scale * 0.02 * compact_mb
    pending_term = residual_scale * 0.02 * pending_mb
    l0_term = residual_scale * 0.30 * l0
    residual = gap_term + tail_term + compact_term + pending_term + l0_term
    return {
        "write_qps_target": write_qps_target,
        "completed_write_qps": completed_write_qps,
        "completion_gap": completion_gap,
        "anchor": anchor,
        "gap_term": gap_term,
        "tail_term": tail_term,
        "compact_term": compact_term,
        "pending_term": pending_term,
        "l0_term": l0_term,
        "residual": residual,
        "score": anchor + residual,
        "residual_pressure": clamp(residual / max(anchor + 1.0, 1e-9), 0.0, 2.0),
    }


def pressure_score_demand(args: argparse.Namespace, row: dict[str, str]) -> float:
    terms = demand_score_terms(args, row)
    # write_qps_target dominates because it is the only signal that uniquely
    # identifies foreground demand. Tail/compact contributions are deliberately
    # small: any tenant losing budget can show 2-second tails and huge backlog
    # while not being demand-driven, so over-weighting them mis-ranks low/mid
    # tenants experiencing transient compaction storms.
    return terms["score"]


def update_adaptive_demand_v1_state(
    args: argparse.Namespace,
    prev_metrics: dict[str, dict[str, str]],
    state: dict[str, object],
) -> tuple[dict[str, dict[str, float]], dict[str, object]]:
    terms_by_name = {
        name: demand_score_terms(args, prev_metrics.get(name, {}))
        for name in (tenant_name(i) for i in range(args.tenant_count))
    }
    pressures = [terms["residual_pressure"] for terms in terms_by_name.values()]
    residual_pressure = sum(pressures) / len(pressures) if pressures else 0.55
    prev_ema = float(state.get("adaptive_demand_v1_ema_residual_pressure", 0.55))
    ema = 0.80 * prev_ema + 0.20 * residual_pressure
    alpha_target = clamp(1.0 + 0.30 * (0.55 - ema), 0.75, 1.25)
    prev_alpha = float(state.get("adaptive_demand_v1_alpha", 1.0))
    alpha = bounded_step(prev_alpha, alpha_target, 0.05)
    state["adaptive_demand_v1_ema_residual_pressure"] = ema
    state["adaptive_demand_v1_alpha_target"] = alpha_target
    state["adaptive_demand_v1_alpha"] = alpha
    diagnostics = {
        "score_mode": "adaptive_demand_v1",
        "formula": "anchor + alpha * residual; anchor is fixed demand's write_qps_target term",
        "alpha": alpha,
        "alpha_target": alpha_target,
        "alpha_prev": prev_alpha,
        "alpha_min": 0.75,
        "alpha_max": 1.25,
        "alpha_step_limit": 0.05,
        "ema_residual_pressure": ema,
        "ema_gain": 0.20,
        "residual_pressure_mean": residual_pressure,
    }
    return terms_by_name, diagnostics


def update_adaptive_ranklag_v1_state(
    args: argparse.Namespace,
    prev_metrics: dict[str, dict[str, str]],
    state: dict[str, object],
) -> tuple[dict[str, dict[str, float]], dict[str, object]]:
    """Adaptive SAKI coefficients driven by observed rank drift and assignment miss.

    Inputs are observation-only; no `true_tier` / phase label is consulted.

    Per just-observed window w:
      R_D(w) = top-H tenants by observed write_qps_target.
      A_H(w) = high set actually assigned during window w (from prior decision).
      miss(w)  = 1 - |A_H(w) ∩ R_D(w)| / H
      drift(w) = 1 - |R_D(w) ∩ R_D(w-1)| / H
      raw_u(w) = max(0, 0.6 * drift(w) + 0.4 * miss(w) - 0.15)
      u(w)     = 0.7 * u(w-1) + 0.3 * raw_u(w)
      alpha_D  = clip(6.00 * (1 + 0.25 * u), 6.00, 7.50)
      lambda_P = clip(1.00 - 0.15 * u, 0.85, 1.00)
    When no signal yet, u=0 ⇒ alpha_D=6.00, lambda_P=1.00 ⇒ exact fixed SAKI.
    """
    names = [tenant_name(i) for i in range(args.tenant_count)]
    H = max(1, int(args.high_count))
    terms_by_name = {
        name: demand_score_terms(args, prev_metrics.get(name, {}))
        for name in names
    }

    if prev_metrics:
        ranked_by_demand = sorted(
            names,
            key=lambda n: terms_by_name[n]["write_qps_target"],
            reverse=True,
        )
        rd_now = set(ranked_by_demand[:H])
    else:
        rd_now = set()

    rd_prev = set(state.get("prev_rd", []))
    a_h_prev = set(state.get("prev_assigned_high", []))

    if prev_metrics and rd_prev:
        drift = 1.0 - len(rd_now & rd_prev) / H
    else:
        drift = 0.0
    if prev_metrics and a_h_prev:
        miss = 1.0 - len(a_h_prev & rd_now) / H
    else:
        miss = 0.0

    raw_u = max(0.0, 0.6 * drift + 0.4 * miss - 0.15)
    prev_u = float(state.get("u", 0.0))
    u = 0.7 * prev_u + 0.3 * raw_u

    alpha_D = clamp(6.00 * (1.0 + 0.25 * u), 6.00, 7.50)
    lambda_P = clamp(1.00 - 0.15 * u, 0.85, 1.00)

    state["u_prev"] = prev_u
    state["u"] = u
    state["raw_u"] = raw_u
    state["alpha_D"] = alpha_D
    state["lambda_P"] = lambda_P
    state["miss"] = miss
    state["drift"] = drift
    state["prev_rd"] = sorted(rd_now)

    diagnostics = {
        "score_mode": "adaptive_ranklag_v1",
        "formula": "alpha_D * write_qps_target/1000 + lambda_P * (0.60*G/1000 + 0.05*T + 0.02*B + 0.02*P + 0.30*L)",
        "u": u,
        "u_prev": prev_u,
        "raw_u": raw_u,
        "alpha_D": alpha_D,
        "lambda_P": lambda_P,
        "alpha_D_bounds": [6.00, 7.50],
        "lambda_P_bounds": [0.85, 1.00],
        "miss": miss,
        "drift": drift,
        "H": H,
        "ewma_gain": 0.30,
        "deadband": 0.15,
        "R_D_now": sorted(rd_now),
        "R_D_prev": sorted(rd_prev),
        "A_H_prev": sorted(a_h_prev),
        "tenant_scores_pre": {
            name: {
                "anchor": terms_by_name[name]["anchor"],
                "residual": terms_by_name[name]["residual"],
                "write_qps_target": terms_by_name[name]["write_qps_target"],
                "completion_gap": terms_by_name[name]["completion_gap"],
            }
            for name in names
        },
    }
    return terms_by_name, diagnostics


def debt_safe_v1_terms(
    args: argparse.Namespace,
    prev_metrics: dict[str, dict[str, str]],
    adaptive_state: dict[str, object],
) -> tuple[dict[str, dict[str, float]], dict[str, object]]:
    """Compute observation-only debt risk used to avoid unsafe low placement.

    The fixed demand score remains the anchor. This mode does not change
    budget levels and does not use true_tier, phase labels, or future workload.
    It only marks tenants that should not be demoted to low in the next window
    if safer low candidates exist.
    """
    names = [tenant_name(i) for i in range(args.tenant_count)]
    window_sec = max(1, int(args.window_sec))
    pending_scale = 32.0 * 1024.0 * 1024.0
    prev_p99_ema_raw = adaptive_state.get("debt_safe_v1_p99_ema", {})
    prev_p999_ema_raw = adaptive_state.get("debt_safe_v1_p999_ema", {})
    prev_p99_ema: dict[str, float] = {
        str(k): float(v) for k, v in prev_p99_ema_raw.items()
    } if isinstance(prev_p99_ema_raw, dict) else {}
    prev_p999_ema: dict[str, float] = {
        str(k): float(v) for k, v in prev_p999_ema_raw.items()
    } if isinstance(prev_p999_ema_raw, dict) else {}

    prev_assigned_high = set(str(x) for x in adaptive_state.get("debt_safe_v1_prev_assigned_high", []))
    protected_ttl_raw = adaptive_state.get("debt_safe_v1_protected_ttl", {})
    protected_ttl: dict[str, int] = {
        str(k): int(v) for k, v in protected_ttl_raw.items()
    } if isinstance(protected_ttl_raw, dict) else {}

    terms: dict[str, dict[str, float]] = {}
    p99_ema_next: dict[str, float] = dict(prev_p99_ema)
    p999_ema_next: dict[str, float] = dict(prev_p999_ema)
    new_protected_ttl: dict[str, int] = {}

    for name in names:
        row = prev_metrics.get(name, {})
        demand_terms = demand_score_terms(args, row)
        demand = demand_terms["write_qps_target"]
        completed = _row_float(row, "write_ops") / window_sec
        gap_norm = max(0.0, demand - completed) / max(demand, 1.0)
        pending_norm = min(_row_float(row, "pending_compaction_bytes") / pending_scale, 2.0)
        l0_norm = min(_row_float(row, "l0_files") / 6.0, 2.0)
        p99 = max(_row_float(row, "write_p99_us"), 1.0)
        p999 = max(_row_float(row, "write_p999_us"), 1.0)
        p99_base = max(prev_p99_ema.get(name, p99), 1.0)
        p999_base = max(prev_p999_ema.get(name, p999), 1.0)
        p99_rel = clamp(max(0.0, p99 / p99_base - 1.0), 0.0, 2.0)
        p999_rel = clamp(max(0.0, p999 / p999_base - 1.0), 0.0, 2.0)
        debt_risk = clamp(
            0.35 * gap_norm
            + 0.25 * pending_norm
            + 0.15 * l0_norm
            + 0.15 * p99_rel
            + 0.10 * p999_rel,
            0.0,
            2.0,
        )
        old_ttl = max(0, protected_ttl.get(name, 0) - 1)
        just_high = name in prev_assigned_high
        risky = debt_risk >= 0.75 or p999_rel >= 1.00 or pending_norm >= 1.00
        protected = risky or old_ttl > 0
        if just_high and (debt_risk >= 0.55 or pending_norm >= 0.75 or p999_rel >= 0.75):
            protected = True
            old_ttl = max(old_ttl, 1)
        if protected:
            new_protected_ttl[name] = old_ttl
        terms[name] = {
            "demand_score": demand_terms["score"],
            "write_qps_target": demand,
            "completed_write_qps": completed,
            "gap_norm": gap_norm,
            "pending_norm": pending_norm,
            "l0_norm": l0_norm,
            "p99_us": p99,
            "p999_us": p999,
            "p99_rel": p99_rel,
            "p999_rel": p999_rel,
            "debt_risk": debt_risk,
            "protected": 1.0 if protected else 0.0,
            "ttl": float(old_ttl),
        }
        p99_ema_next[name] = p99 if name not in prev_p99_ema else 0.85 * prev_p99_ema[name] + 0.15 * p99
        p999_ema_next[name] = p999 if name not in prev_p999_ema else 0.85 * prev_p999_ema[name] + 0.15 * p999

    adaptive_state["debt_safe_v1_p99_ema"] = p99_ema_next
    adaptive_state["debt_safe_v1_p999_ema"] = p999_ema_next
    adaptive_state["debt_safe_v1_protected_ttl"] = new_protected_ttl

    diagnostics = {
        "score_mode": "adaptive_debt_safe_v1",
        "formula": "fixed demand ranking for high; low candidates exclude tenants with observed debt_risk/tail protection when possible",
        "pending_scale_bytes": pending_scale,
        "risk_threshold": 0.75,
        "p999_rel_threshold": 1.00,
        "pending_norm_threshold": 1.00,
        "post_high_debt_threshold": 0.55,
        "protected_ttl": new_protected_ttl,
        "prev_assigned_high": sorted(prev_assigned_high),
    }
    return terms, diagnostics


def demotion_guard_v1_terms(
    args: argparse.Namespace,
    prev_metrics: dict[str, dict[str, str]],
    adaptive_state: dict[str, object],
) -> tuple[dict[str, dict[str, float]], dict[str, object]]:
    """Compute conservative post-high demotion risk.

    This mode only considers one kind of intervention: if fixed demand would
    move a previously high tenant directly to low while persistent debt signals
    are present, keep it out of low for one window if a safe nearby low
    replacement exists.
    """
    names = [tenant_name(i) for i in range(args.tenant_count)]
    window_sec = max(1, int(args.window_sec))
    pending_scale = 32.0 * 1024.0 * 1024.0
    prev_p99_ema_raw = adaptive_state.get("demotion_guard_v1_p99_ema", {})
    prev_p999_ema_raw = adaptive_state.get("demotion_guard_v1_p999_ema", {})
    prev_p99_ema: dict[str, float] = {
        str(k): float(v) for k, v in prev_p99_ema_raw.items()
    } if isinstance(prev_p99_ema_raw, dict) else {}
    prev_p999_ema: dict[str, float] = {
        str(k): float(v) for k, v in prev_p999_ema_raw.items()
    } if isinstance(prev_p999_ema_raw, dict) else {}
    prev_streak_raw = adaptive_state.get("demotion_guard_v1_risky_streak", {})
    prev_streak: dict[str, int] = {
        str(k): int(v) for k, v in prev_streak_raw.items()
    } if isinstance(prev_streak_raw, dict) else {}

    terms: dict[str, dict[str, float]] = {}
    p99_ema_next: dict[str, float] = dict(prev_p99_ema)
    p999_ema_next: dict[str, float] = dict(prev_p999_ema)
    streak_next: dict[str, int] = {}

    for name in names:
        row = prev_metrics.get(name, {})
        demand_terms = demand_score_terms(args, row)
        demand = demand_terms["write_qps_target"]
        completed = _row_float(row, "write_ops") / window_sec
        gap_norm = max(0.0, demand - completed) / max(demand, 1.0)
        pending_norm = min(_row_float(row, "pending_compaction_bytes") / pending_scale, 2.0)
        l0_norm = min(_row_float(row, "l0_files") / 6.0, 2.0)
        p99 = max(_row_float(row, "write_p99_us"), 1.0)
        p999 = max(_row_float(row, "write_p999_us"), 1.0)
        p99_base = max(prev_p99_ema.get(name, p99), 1.0)
        p999_base = max(prev_p999_ema.get(name, p999), 1.0)
        p99_rel = clamp(max(0.0, p99 / p99_base - 1.0), 0.0, 2.0)
        p999_rel = clamp(max(0.0, p999 / p999_base - 1.0), 0.0, 2.0)
        debt_risk = clamp(
            0.35 * gap_norm
            + 0.25 * pending_norm
            + 0.15 * l0_norm
            + 0.15 * p99_rel
            + 0.10 * p999_rel,
            0.0,
            2.0,
        )
        risky_now = (
            (debt_risk >= 0.55 and (pending_norm >= 0.35 or gap_norm >= 0.45))
            or (pending_norm >= 0.75 and gap_norm >= 0.25)
            or (p999_rel >= 1.25 and pending_norm >= 0.25)
        )
        streak = prev_streak.get(name, 0) + 1 if risky_now else 0
        if streak:
            streak_next[name] = streak
        terms[name] = {
            "demand_score": demand_terms["score"],
            "write_qps_target": demand,
            "completed_write_qps": completed,
            "gap_norm": gap_norm,
            "pending_norm": pending_norm,
            "l0_norm": l0_norm,
            "p99_us": p99,
            "p999_us": p999,
            "p99_rel": p99_rel,
            "p999_rel": p999_rel,
            "debt_risk": debt_risk,
            "risky_now": 1.0 if risky_now else 0.0,
            "risky_streak": float(streak),
        }
        p99_ema_next[name] = p99 if name not in prev_p99_ema else 0.85 * prev_p99_ema[name] + 0.15 * p99
        p999_ema_next[name] = p999 if name not in prev_p999_ema else 0.85 * prev_p999_ema[name] + 0.15 * p999

    adaptive_state["demotion_guard_v1_p99_ema"] = p99_ema_next
    adaptive_state["demotion_guard_v1_p999_ema"] = p999_ema_next
    adaptive_state["demotion_guard_v1_risky_streak"] = streak_next

    diagnostics = {
        "score_mode": "adaptive_demotion_guard_v1",
        "formula": "fixed-demand high/low except at most one protected prev-high to low demotion when persistent risk and safe replacement are present",
        "pending_scale_bytes": pending_scale,
        "persistent_streak_required": 2,
        "max_low_replacements_per_window": 1,
        "replacement_margin": 0.20,
        "replacement_max_gap_norm": 0.55,
        "replacement_max_pending_norm": 0.55,
        "replacement_max_p999_us": 80000.0,
        "replacement_window_extra_candidates": 5,
    }
    return terms, diagnostics


def update_adaptive_spread_v1_state(
    args: argparse.Namespace,
    prev_metrics: dict[str, dict[str, str]],
    prev_assigned_high: list[str],
    prev_assigned_low: list[str],
    state: dict[str, object],
) -> tuple[int, int, int, dict[str, object]]:
    """Compute adaptive Δ(w) using only observed previous-window signals.

    Placement (which tenant is high/mid/low) is decided elsewhere via the fixed
    demand score. This function only chooses the spread Δ(w) between tiers,
    i.e. reallocation intensity, and returns integer-byte per-tier budgets that
    keep the total budget conserved.

    Conservation:
      H == L (symmetric default in current configs):
          B_H = B_M0 + Δ(w), B_L = B_M0 - Δ(w), B_M = B_M0
          ⇒ conservation is exact in integer bytes/sec.
      H != L:
          extra = H * Δ(w), low_delta = extra / L
          B_H = B_M0 + Δ(w), B_L = B_M0 - low_delta, B_M = B_M0
          ⇒ integer rounding may leave |error| ≤ a few bytes/sec; we then
          shrink Δ if it would drive B_L under a safety floor.

    Inputs are observation-only. No true_tier, phase label, or future workload
    is read.
    """
    B_H0 = int(args.high_budget)
    B_M0 = int(args.mid_budget)
    B_L0 = int(args.low_budget)
    Delta0 = float(B_H0 - B_M0)
    H = max(1, int(args.high_count))
    L = max(1, int(args.low_count))
    N = int(args.tenant_count)
    M = N - H - L

    diagnostics: dict[str, object] = {
        "mode": "adaptive_spread_v1",
        "delta0": Delta0,
        "B_H0": B_H0,
        "B_M0": B_M0,
        "B_L0": B_L0,
        "H": H,
        "L": L,
        "M": M,
        "N": N,
        "rho": 0.25,
        "delta_floor": 0.75 * Delta0,
        "delta_ceil": 1.50 * Delta0,
        "prev_assigned_high": list(prev_assigned_high),
        "prev_assigned_low": list(prev_assigned_low),
    }

    have_signal = bool(prev_metrics) and bool(prev_assigned_high)
    prev_u = float(state.get("spread_u", 0.0))

    if not have_signal:
        u = prev_u
        raw_u = 0.0
        high_gap_norm = 0.0
        high_tail_norm = 0.0
        low_collateral_norm = 0.0
        spread_multiplier_pre_clip = 1.0
        warmup = True
    else:
        window_sec = max(1, int(args.window_sec))

        def _signals(name: str) -> tuple[float, float]:
            row = prev_metrics.get(name, {})
            wqt = max(1.0, _row_float(row, "write_qps_target"))
            wcompleted = _row_float(row, "write_ops") / window_sec
            gap = max(0.0, wqt - wcompleted)
            gap_norm = gap / wqt
            p99 = _row_float(row, "write_p99_us")
            tail_norm = min(p99 / 20000.0, 2.0)
            return gap_norm, tail_norm

        high_gaps: list[float] = []
        high_tails: list[float] = []
        for name in prev_assigned_high:
            g, t = _signals(name)
            high_gaps.append(g)
            high_tails.append(t)
        low_tails: list[float] = []
        for name in prev_assigned_low:
            _, t = _signals(name)
            low_tails.append(t)

        high_gap_norm = sum(high_gaps) / max(1, len(high_gaps))
        high_tail_norm = sum(high_tails) / max(1, len(high_tails))
        low_collateral_norm = sum(low_tails) / max(1, len(low_tails)) if low_tails else 0.0

        raw_u = high_gap_norm + 0.25 * high_tail_norm - 0.5 * low_collateral_norm
        u = 0.7 * prev_u + 0.3 * raw_u
        spread_multiplier_pre_clip = 1.0 + 0.25 * u
        warmup = False

    spread_multiplier = clamp(spread_multiplier_pre_clip, 0.75, 1.50)
    delta_target_unclipped = Delta0 * spread_multiplier
    delta_target = clamp(delta_target_unclipped, 0.75 * Delta0, 1.50 * Delta0)

    safe_floor = max(1, int(B_L0 * 0.5))
    floor_triggered = False

    if H == L:
        # Symmetric: conservation is automatic at integer precision.
        B_H_float = B_M0 + delta_target
        B_L_float = B_M0 - delta_target
        if B_L_float < safe_floor:
            new_delta = float(B_M0 - safe_floor)
            delta_target = max(0.0, new_delta)
            B_H_float = B_M0 + delta_target
            B_L_float = B_M0 - delta_target
            floor_triggered = True
        B_M_float = float(B_M0)
    else:
        # Asymmetric: redistribute extra from highs evenly across lows.
        extra = H * delta_target
        low_delta = extra / L
        B_H_float = B_M0 + delta_target
        B_L_float = B_M0 - low_delta
        B_M_float = float(B_M0)
        if B_L_float < safe_floor:
            max_low_delta = float(B_M0 - safe_floor)
            max_extra = max_low_delta * L
            delta_target = max(0.0, max_extra / H)
            B_H_float = B_M0 + delta_target
            B_L_float = B_M0 - (delta_target * H / L)
            floor_triggered = True

    B_H_int = int(round(B_H_float))
    B_M_int = int(round(B_M_float))
    B_L_int = int(round(B_L_float))

    total_target = N * B_M0
    total_before = H * B_H_int + M * B_M_int + L * B_L_int
    delta_total = total_before - total_target

    # Absorb integer rounding error in the mid bucket if it exists. If not,
    # nudge low. For H == L this branch is a no-op because total_before is
    # already exact.
    if delta_total != 0 and M > 0:
        per_mid_adj = delta_total // M
        leftover = delta_total - per_mid_adj * M
        B_M_int = B_M_int - per_mid_adj
        if leftover != 0 and L > 0:
            B_L_int = B_L_int - leftover

    total_after = H * B_H_int + M * B_M_int + L * B_L_int
    conservation_error = total_after - total_target

    state["spread_u"] = u
    state["spread_u_prev"] = prev_u
    state["spread_raw_u"] = raw_u

    diagnostics.update(
        {
            "warmup": bool(warmup),
            "raw_u": raw_u,
            "u": u,
            "u_prev": prev_u,
            "high_gap_norm": high_gap_norm,
            "high_tail_norm": high_tail_norm,
            "low_collateral_norm": low_collateral_norm,
            "spread_multiplier_pre_clip": spread_multiplier_pre_clip,
            "spread_multiplier": spread_multiplier,
            "spread_delta": delta_target,
            "delta_target_unclipped": delta_target_unclipped,
            "floor_triggered": bool(floor_triggered),
            "high_budget": B_H_int,
            "mid_budget": B_M_int,
            "low_budget": B_L_int,
            "total_budget_before": total_before,
            "total_budget_after": total_after,
            "total_budget_target": total_target,
            "conservation_error": conservation_error,
        }
    )
    return B_H_int, B_M_int, B_L_int, diagnostics


def update_adaptive_spread_v2_state(
    args: argparse.Namespace,
    prev_metrics: dict[str, dict[str, str]],
    prev_assigned_high: list[str],
    prev_assigned_low: list[str],
    state: dict[str, object],
) -> tuple[int, int, int, dict[str, object]]:
    """Compute conservative adaptive spread v2 from previous-window signals.

    Placement is unchanged and is decided by the fixed demand score elsewhere.
    This function only changes spread intensity. All signals come from the
    previous completed window and the previously assigned high/low sets.
    """
    B_H0 = int(args.high_budget)
    B_M0 = int(args.mid_budget)
    B_L0 = int(args.low_budget)
    Delta0 = float(B_H0 - B_M0)
    H = max(1, int(args.high_count))
    L = max(1, int(args.low_count))
    N = int(args.tenant_count)
    M = N - H - L

    diagnostics: dict[str, object] = {
        "mode": "adaptive_spread_v2",
        "version": "adaptive_spread_v2",
        "delta0": Delta0,
        "B_H0": B_H0,
        "B_M0": B_M0,
        "B_L0": B_L0,
        "H": H,
        "L": L,
        "M": M,
        "N": N,
        "rho": 0.15,
        "delta_floor": 0.90 * Delta0,
        "delta_ceil": 1.15 * Delta0,
        "low_budget_floor": 0.80 * B_L0,
        "prev_assigned_high": list(prev_assigned_high),
        "prev_assigned_low": list(prev_assigned_low),
        "previous_high_set_used": list(prev_assigned_high),
        "previous_low_set_used": list(prev_assigned_low),
    }

    prev_u = float(state.get("spread_v2_u", 0.0))
    have_metrics = bool(prev_metrics)
    have_signal = have_metrics and bool(prev_assigned_high)
    window_sec = max(1, int(args.window_sec))

    low_p99_ema_raw = state.get("spread_v2_low_p99_ema", {})
    low_p99_ema: dict[str, float] = {
        str(k): float(v) for k, v in low_p99_ema_raw.items()
    } if isinstance(low_p99_ema_raw, dict) else {}
    total_tput_ema_prev = state.get("spread_v2_total_tput_ema")
    total_tput_ema_value = float(total_tput_ema_prev) if total_tput_ema_prev is not None else None

    def _signals(name: str) -> tuple[float, float, float]:
        row = prev_metrics.get(name, {})
        demand = _row_float(row, "write_qps_target")
        completed = _row_float(row, "write_ops") / window_sec
        gap_norm = max(0.0, demand - completed) / max(demand, 1.0)
        p99 = _row_float(row, "write_p99_us")
        tail_norm = min(p99 / 20000.0, 2.0)
        return gap_norm, tail_norm, p99

    current_total_tput = 0.0
    if have_metrics:
        current_total_tput = sum(
            (_row_float(row, "write_ops") + _row_float(row, "read_ops")) / window_sec
            for row in prev_metrics.values()
        )

    high_gap_norm = 0.0
    high_tail_norm = 0.0
    low_tail_norm = 0.0
    low_tail_rel = 0.0
    total_tput_drop = 0.0
    raw_u = 0.0
    warmup = not have_signal

    low_p99_observed: dict[str, float] = {}
    if have_signal:
        high_gaps: list[float] = []
        high_tails: list[float] = []
        for name in prev_assigned_high:
            gap_norm, tail_norm, _ = _signals(name)
            high_gaps.append(gap_norm)
            high_tails.append(tail_norm)

        low_tails: list[float] = []
        low_rels: list[float] = []
        for name in prev_assigned_low:
            _, tail_norm, p99 = _signals(name)
            low_tails.append(tail_norm)
            low_p99_observed[name] = p99
            baseline = low_p99_ema.get(name)
            if baseline is None:
                baseline = max(p99, 1.0)
            low_rels.append(max(0.0, p99 / max(baseline, 1.0) - 1.0))

        high_gap_norm = sum(high_gaps) / max(1, len(high_gaps))
        high_tail_norm = sum(high_tails) / max(1, len(high_tails))
        low_tail_norm = sum(low_tails) / max(1, len(low_tails)) if low_tails else 0.0
        low_tail_rel = sum(low_rels) / max(1, len(low_rels)) if low_rels else 0.0
        if total_tput_ema_value is not None and current_total_tput > 0.0:
            total_tput_drop = max(0.0, total_tput_ema_value / max(current_total_tput, 1.0) - 1.0)
        raw_u = high_gap_norm + 0.15 * high_tail_norm - low_tail_rel - 0.50 * total_tput_drop
        u = 0.75 * prev_u + 0.25 * raw_u
    else:
        u = prev_u

    # Update observation-only EMAs after computing this window's signal.
    if have_metrics and current_total_tput > 0.0:
        if total_tput_ema_value is None:
            state["spread_v2_total_tput_ema"] = current_total_tput
        else:
            state["spread_v2_total_tput_ema"] = 0.75 * total_tput_ema_value + 0.25 * current_total_tput
    if prev_assigned_low:
        for name in prev_assigned_low:
            p99 = low_p99_observed.get(name)
            if p99 is None:
                _, _, p99 = _signals(name)
            p99 = max(p99, 1.0)
            old = low_p99_ema.get(name)
            low_p99_ema[name] = p99 if old is None else 0.75 * old + 0.25 * p99
        state["spread_v2_low_p99_ema"] = low_p99_ema

    spread_multiplier_pre_clip = 1.0 + 0.15 * u
    spread_multiplier_formula = clamp(spread_multiplier_pre_clip, 0.90, 1.15)
    delta_target_unclipped = Delta0 * spread_multiplier_formula
    delta_before_floor = clamp(delta_target_unclipped, 0.90 * Delta0, 1.15 * Delta0)

    low_budget_floor = float(B_L0) * 0.80
    floor_hit = False
    if H == L:
        max_delta_by_floor = max(0.0, float(B_M0) - low_budget_floor)
    else:
        max_low_delta = max(0.0, float(B_M0) - low_budget_floor)
        max_delta_by_floor = max_low_delta * L / H
    delta_target = delta_before_floor
    if delta_target > max_delta_by_floor:
        delta_target = max_delta_by_floor
        floor_hit = True

    if H == L:
        B_H_float = B_M0 + delta_target
        B_L_float = B_M0 - delta_target
        B_M_float = float(B_M0)
    else:
        extra = H * delta_target
        low_delta = extra / L
        B_H_float = B_M0 + delta_target
        B_L_float = B_M0 - low_delta
        B_M_float = float(B_M0)

    B_H_int = int(round(B_H_float))
    B_M_int = int(round(B_M_float))
    B_L_int = int(round(B_L_float))

    total_target = N * B_M0
    total_before = H * B_H_int + M * B_M_int + L * B_L_int
    delta_total = total_before - total_target
    if delta_total != 0 and M > 0:
        per_mid_adj = delta_total // M
        leftover = delta_total - per_mid_adj * M
        B_M_int = B_M_int - per_mid_adj
        if leftover != 0 and L > 0:
            B_L_int = B_L_int - leftover

    total_after = H * B_H_int + M * B_M_int + L * B_L_int
    conservation_error = total_after - total_target
    applied_multiplier = delta_target / Delta0 if Delta0 else 1.0
    ceiling_hit = spread_multiplier_pre_clip > 1.15 or delta_target_unclipped > 1.15 * Delta0
    spread_floor_hit = spread_multiplier_pre_clip < 0.90 or delta_target_unclipped < 0.90 * Delta0

    state["spread_v2_u"] = u
    state["spread_v2_u_prev"] = prev_u
    state["spread_v2_raw_u"] = raw_u

    diagnostics.update(
        {
            "warmup": bool(warmup),
            "raw_u": raw_u,
            "u": u,
            "u_prev": prev_u,
            "high_gap_norm": high_gap_norm,
            "high_tail_norm": high_tail_norm,
            "low_tail_norm": low_tail_norm,
            "low_tail_rel": low_tail_rel,
            "total_tput_drop": total_tput_drop,
            "current_total_tput": current_total_tput,
            "total_tput_ema_prev": total_tput_ema_value,
            "total_tput_ema": state.get("spread_v2_total_tput_ema"),
            "low_p99_ema": low_p99_ema,
            "spread_multiplier_pre_clip": spread_multiplier_pre_clip,
            "spread_multiplier_formula": spread_multiplier_formula,
            "spread_multiplier": applied_multiplier,
            "delta": delta_target,
            "spread_delta": delta_target,
            "delta_target_unclipped": delta_target_unclipped,
            "delta_before_floor": delta_before_floor,
            "floor_hit": bool(floor_hit),
            "floor_triggered": bool(floor_hit),
            "ceiling_hit": bool(ceiling_hit),
            "spread_floor_hit": bool(spread_floor_hit),
            "high_budget": B_H_int,
            "mid_budget": B_M_int,
            "low_budget": B_L_int,
            "total_budget_before": total_before,
            "total_budget_after": total_after,
            "total_budget_target": total_target,
            "conservation_error": conservation_error,
        }
    )
    return B_H_int, B_M_int, B_L_int, diagnostics


def update_adaptive_spread_v3_state(
    args: argparse.Namespace,
    prev_metrics: dict[str, dict[str, str]],
    prev_assigned_high: list[str],
    prev_assigned_low: list[str],
    state: dict[str, object],
) -> tuple[int, int, int, dict[str, object]]:
    """Compute final conservative adaptive spread v3 from prior-window signals.

    Placement is unchanged and is decided by the fixed demand score elsewhere.
    This controller only changes the budget spread intensity. Inputs are
    previous-window observations over the previously assigned high/low sets;
    it does not read true_tier, phase labels, or future workload state.
    """
    B_H0 = int(args.high_budget)
    B_M0 = int(args.mid_budget)
    B_L0 = int(args.low_budget)
    Delta0 = float(B_H0 - B_M0)
    H = max(1, int(args.high_count))
    L = max(1, int(args.low_count))
    N = int(args.tenant_count)
    M = N - H - L

    diagnostics: dict[str, object] = {
        "mode": "adaptive_spread_v3",
        "version": "adaptive_spread_v3",
        "delta0": Delta0,
        "B_H0": B_H0,
        "B_M0": B_M0,
        "B_L0": B_L0,
        "H": H,
        "L": L,
        "M": M,
        "N": N,
        "rho": 0.15,
        "spread_multiplier_floor": 0.97,
        "spread_multiplier_ceil": 1.05,
        "spread_multiplier_slew_limit": 0.02,
        "low_budget_floor": 0.90 * B_L0,
        "prev_assigned_high": list(prev_assigned_high),
        "prev_assigned_low": list(prev_assigned_low),
        "previous_high_set_used": list(prev_assigned_high),
        "previous_low_set_used": list(prev_assigned_low),
    }

    prev_u = float(state.get("spread_v3_u", 0.0))
    prev_multiplier = float(state.get("spread_multiplier", 1.0))
    prev_multiplier = clamp(prev_multiplier, 0.97, 1.05)
    have_metrics = bool(prev_metrics)
    have_signal = have_metrics and bool(prev_assigned_high)
    window_sec = max(1, int(args.window_sec))

    low_p99_ema_raw = state.get("spread_v3_low_p99_ema", {})
    low_p99_ema: dict[str, float] = {
        str(k): float(v) for k, v in low_p99_ema_raw.items()
    } if isinstance(low_p99_ema_raw, dict) else {}
    total_tput_ema_prev = state.get("spread_v3_total_tput_ema")
    total_tput_ema_value = float(total_tput_ema_prev) if total_tput_ema_prev is not None else None
    high_p999_ema_prev = state.get("spread_v3_high_p999_ema")
    high_p999_ema_value = float(high_p999_ema_prev) if high_p999_ema_prev is not None else None

    def _signals(name: str) -> tuple[float, float, float, float, float]:
        row = prev_metrics.get(name, {})
        demand = _row_float(row, "write_qps_target")
        completed = _row_float(row, "write_ops") / window_sec
        gap_norm = max(0.0, demand - completed) / max(demand, 1.0)
        p99 = _row_float(row, "write_p99_us")
        p999 = _row_float(row, "write_p999_us")
        p99_norm = min(p99 / 20000.0, 2.0)
        p999_norm = min(p999 / 60000.0, 2.0)
        return gap_norm, p99_norm, p999_norm, p99, p999

    current_total_tput = 0.0
    if have_metrics:
        current_total_tput = sum(
            (_row_float(row, "write_ops") + _row_float(row, "read_ops")) / window_sec
            for row in prev_metrics.values()
        )

    high_gap_norm = 0.0
    high_p99_norm = 0.0
    high_p999_norm = 0.0
    current_high_p999 = 0.0
    low_tail_rel_raw = 0.0
    low_tail_rel = 0.0
    total_tput_drop = 0.0
    total_tput_ema_reliable = total_tput_ema_value is not None
    high_p999_ema_for_guard = high_p999_ema_value
    high_p999_rel = 0.0
    high_p999_rel_clipped = 0.0
    high_p999_guard_active = False
    raw_u_unclipped = 0.0
    raw_u = 0.0
    u = prev_u
    target_multiplier_unclipped = 1.0
    target_multiplier = 1.0
    warmup = not have_signal

    low_p99_observed: dict[str, float] = {}
    if have_signal:
        high_gaps: list[float] = []
        high_p99s: list[float] = []
        high_p999s: list[float] = []
        high_p999_us: list[float] = []
        for name in prev_assigned_high:
            gap_norm, p99_norm, p999_norm, _, p999 = _signals(name)
            high_gaps.append(gap_norm)
            high_p99s.append(p99_norm)
            high_p999s.append(p999_norm)
            high_p999_us.append(p999)

        high_gap_norm = sum(high_gaps) / max(1, len(high_gaps))
        high_p99_norm = sum(high_p99s) / max(1, len(high_p99s))
        high_p999_norm = sum(high_p999s) / max(1, len(high_p999s))
        current_high_p999 = sum(high_p999_us) / max(1, len(high_p999_us))

        if high_p999_ema_for_guard is None:
            high_p999_ema_for_guard = max(current_high_p999, 1.0)
        high_p999_rel = max(0.0, current_high_p999 / max(high_p999_ema_for_guard, 1.0) - 1.0)
        high_p999_rel_clipped = clamp(high_p999_rel, 0.0, 2.0)
        high_p999_guard_active = high_p999_rel_clipped > 0.50

        low_rels: list[float] = []
        for name in prev_assigned_low:
            _, _, _, p99, _ = _signals(name)
            low_p99_observed[name] = p99
            baseline = low_p99_ema.get(name)
            if baseline is None:
                baseline = max(p99, 1.0)
            low_rels.append(max(0.0, p99 / max(baseline, 1.0) - 1.0))
        low_tail_rel_raw = sum(low_rels) / max(1, len(low_rels)) if low_rels else 0.0
        low_tail_rel = clamp(low_tail_rel_raw, 0.0, 2.0)

        if total_tput_ema_reliable and current_total_tput > 0.0:
            total_tput_drop = clamp(
                max(0.0, float(total_tput_ema_value) / max(current_total_tput, 1.0) - 1.0),
                0.0,
                1.0,
            )

        if high_p999_guard_active:
            raw_u_unclipped = 0.0
            raw_u = 0.0
            u = clamp(0.85 * prev_u, -1.0, 1.0)
            target_multiplier = 1.0
            target_multiplier_unclipped = 1.0
        else:
            raw_u_unclipped = (
                0.60 * high_gap_norm
                + 0.10 * high_p99_norm
                + 0.10 * high_p999_norm
                - 0.70 * low_tail_rel
                - 0.50 * total_tput_drop
            )
            raw_u = clamp(raw_u_unclipped, -1.0, 1.0)
            u = clamp(0.85 * prev_u + 0.15 * raw_u, -1.0, 1.0)
            target_multiplier_unclipped = 1.0 + 0.05 * u
            target_multiplier = clamp(target_multiplier_unclipped, 0.97, 1.05)

    if high_p999_guard_active:
        spread_multiplier_pre_floor = prev_multiplier + clamp(1.0 - prev_multiplier, -0.02, 0.02)
    else:
        spread_multiplier_pre_floor = prev_multiplier + clamp(target_multiplier - prev_multiplier, -0.02, 0.02)
        spread_multiplier_pre_floor = clamp(spread_multiplier_pre_floor, 0.97, 1.05)

    spread_multiplier_pre_floor = clamp(spread_multiplier_pre_floor, 0.97, 1.05)
    multiplier_delta_before_floor = spread_multiplier_pre_floor - prev_multiplier
    delta_target = Delta0 * spread_multiplier_pre_floor

    low_budget_floor = float(B_L0) * 0.90
    floor_hit = False
    if H == L:
        max_delta_by_floor = max(0.0, float(B_M0) - low_budget_floor)
    else:
        max_low_delta = max(0.0, float(B_M0) - low_budget_floor)
        max_delta_by_floor = max_low_delta * L / H
    if delta_target > max_delta_by_floor:
        delta_target = max_delta_by_floor
        floor_hit = True

    applied_multiplier = delta_target / Delta0 if Delta0 else 1.0
    multiplier_delta_applied = applied_multiplier - prev_multiplier

    if H == L:
        B_H_float = B_M0 + delta_target
        B_L_float = B_M0 - delta_target
        B_M_float = float(B_M0)
    else:
        extra = H * delta_target
        low_delta = extra / L
        B_H_float = B_M0 + delta_target
        B_L_float = B_M0 - low_delta
        B_M_float = float(B_M0)

    B_H_int = int(round(B_H_float))
    B_M_int = int(round(B_M_float))
    B_L_int = int(round(B_L_float))

    total_target = N * B_M0
    total_before = H * B_H_int + M * B_M_int + L * B_L_int
    delta_total = total_before - total_target
    if delta_total != 0 and M > 0:
        per_mid_adj = delta_total // M
        leftover = delta_total - per_mid_adj * M
        B_M_int = B_M_int - per_mid_adj
        if leftover != 0 and L > 0:
            B_L_int = B_L_int - leftover

    total_after = H * B_H_int + M * B_M_int + L * B_L_int
    conservation_error = total_after - total_target

    # Update observation-only EMAs after this window's control computation.
    if have_metrics and current_total_tput > 0.0:
        if total_tput_ema_value is None:
            state["spread_v3_total_tput_ema"] = current_total_tput
        else:
            state["spread_v3_total_tput_ema"] = 0.85 * total_tput_ema_value + 0.15 * current_total_tput
    if have_signal:
        if high_p999_ema_value is None:
            state["spread_v3_high_p999_ema"] = max(current_high_p999, 1.0)
        else:
            state["spread_v3_high_p999_ema"] = 0.85 * high_p999_ema_value + 0.15 * current_high_p999
    if prev_assigned_low:
        for name in prev_assigned_low:
            p99 = low_p99_observed.get(name)
            if p99 is None:
                _, _, _, p99, _ = _signals(name)
            p99 = max(p99, 1.0)
            old = low_p99_ema.get(name)
            low_p99_ema[name] = p99 if old is None else 0.85 * old + 0.15 * p99
        state["spread_v3_low_p99_ema"] = low_p99_ema

    state["spread_v3_u"] = u
    state["spread_v3_u_prev"] = prev_u
    state["spread_v3_raw_u"] = raw_u
    state["spread_multiplier"] = applied_multiplier

    ceiling_hit = target_multiplier_unclipped > 1.05 or spread_multiplier_pre_floor > 1.05
    spread_floor_hit = target_multiplier_unclipped < 0.97

    diagnostics.update(
        {
            "warmup": bool(warmup),
            "raw_u_unclipped": raw_u_unclipped,
            "raw_u": raw_u,
            "u": u,
            "u_prev": prev_u,
            "high_gap_norm": high_gap_norm,
            "high_p99_norm": high_p99_norm,
            "high_p999_norm": high_p999_norm,
            "high_tail_norm": high_p99_norm,
            "current_high_p999": current_high_p999,
            "low_tail_rel_raw": low_tail_rel_raw,
            "low_tail_rel": low_tail_rel,
            "total_tput_drop": total_tput_drop,
            "current_total_tput": current_total_tput,
            "total_tput_ema_reliable": bool(total_tput_ema_reliable),
            "total_tput_ema_prev": total_tput_ema_value,
            "total_tput_ema": state.get("spread_v3_total_tput_ema"),
            "low_p99_ema": low_p99_ema,
            "high_p999_ema": high_p999_ema_for_guard if high_p999_ema_for_guard is not None else 0.0,
            "high_p999_ema_after": state.get("spread_v3_high_p999_ema"),
            "high_p999_rel": high_p999_rel,
            "high_p999_rel_clipped": high_p999_rel_clipped,
            "high_p999_guard_active": bool(high_p999_guard_active),
            "target_multiplier_unclipped": target_multiplier_unclipped,
            "target_multiplier": target_multiplier,
            "prev_multiplier": prev_multiplier,
            "spread_multiplier_pre_floor": spread_multiplier_pre_floor,
            "multiplier_delta_before_floor": multiplier_delta_before_floor,
            "multiplier_delta_applied": multiplier_delta_applied,
            "spread_multiplier": applied_multiplier,
            "delta": delta_target,
            "spread_delta": delta_target,
            "delta_before_floor": Delta0 * spread_multiplier_pre_floor,
            "floor_hit": bool(floor_hit),
            "floor_triggered": bool(floor_hit),
            "ceiling_hit": bool(ceiling_hit),
            "spread_floor_hit": bool(spread_floor_hit),
            "high_budget": B_H_int,
            "mid_budget": B_M_int,
            "low_budget": B_L_int,
            "total_budget_before": total_before,
            "total_budget_after": total_after,
            "total_budget_target": total_target,
            "conservation_error": conservation_error,
        }
    )
    return B_H_int, B_M_int, B_L_int, diagnostics


def pressure_score_hybrid(args: argparse.Namespace, row: dict[str, str]) -> float:
    window_sec = max(1, args.window_sec)
    write_qps_target = _row_float(row, "write_qps_target")
    completed_write_qps = _row_float(row, "write_ops") / window_sec
    completion_gap = max(0.0, write_qps_target - completed_write_qps)
    compact_mb = _row_float(row, "compact_output_bytes") / (1024.0 * 1024.0)
    pending_mb = _row_float(row, "pending_compaction_bytes") / (1024.0 * 1024.0)
    l0 = min(_row_float(row, "l0_files"), 6.0)
    tail_ms = min(_row_float(row, "write_p99_us") / 1000.0, 80.0) + min(
        _row_float(row, "write_p999_us") / 5000.0, 40.0
    )
    return (
        3.00 * write_qps_target / 1000.0
        + 0.50 * completion_gap / 1000.0
        + 2.00 * completed_write_qps / 1000.0
        + 0.10 * tail_ms
        + 0.04 * compact_mb
        + 0.04 * pending_mb
        + 0.60 * l0
    )


def pressure_score(args: argparse.Namespace, row: dict[str, str]) -> float:
    mode = getattr(args, "online_score_mode", "pressure")
    if mode == "demand":
        return pressure_score_demand(args, row)
    if mode == "adaptive_demand_v1":
        return pressure_score_demand(args, row)
    if mode == "adaptive_ranklag_v1":
        return pressure_score_demand(args, row)
    if mode == "adaptive_debt_safe_v1":
        return pressure_score_demand(args, row)
    if mode == "adaptive_demotion_guard_v1":
        return pressure_score_demand(args, row)
    if mode == "hybrid":
        return pressure_score_hybrid(args, row)
    return pressure_score_pressure(args, row)


def online_score_mode_allocation(
    args: argparse.Namespace,
    names: list[str],
    prev_metrics: dict[str, dict[str, str]],
    adaptive_state: dict[str, object] | None,
    spread_budgets: dict[str, int] | None = None,
) -> tuple[dict[str, dict[str, object]], dict[str, object] | None]:
    diagnostics: dict[str, object] | None = None
    mode = getattr(args, "online_score_mode", "pressure")
    if mode == "adaptive_demand_v1":
        if adaptive_state is None:
            adaptive_state = {}
        terms_by_name, diagnostics = update_adaptive_demand_v1_state(args, prev_metrics, adaptive_state)
        alpha = float(diagnostics["alpha"])
        scores = {
            name: terms["anchor"] + alpha * terms["residual"]
            for name, terms in terms_by_name.items()
        }
        diagnostics["tenant_scores"] = {
            name: {
                "anchor": terms_by_name[name]["anchor"],
                "residual": terms_by_name[name]["residual"],
                "residual_pressure": terms_by_name[name]["residual_pressure"],
                "score": scores[name],
                "write_qps_target": terms_by_name[name]["write_qps_target"],
                "completion_gap": terms_by_name[name]["completion_gap"],
            }
            for name in names
        }
    elif mode == "adaptive_ranklag_v1":
        if adaptive_state is None:
            adaptive_state = {}
        terms_by_name, diagnostics = update_adaptive_ranklag_v1_state(args, prev_metrics, adaptive_state)
        alpha_D = float(diagnostics["alpha_D"])
        lambda_P = float(diagnostics["lambda_P"])
        scores = {
            name: alpha_D * (terms["write_qps_target"] / 1000.0)
                  + lambda_P * terms["residual"]
            for name, terms in terms_by_name.items()
        }
        diagnostics["tenant_scores"] = {
            name: {
                "D_over_1000": terms_by_name[name]["write_qps_target"] / 1000.0,
                "residual": terms_by_name[name]["residual"],
                "score": scores[name],
                "write_qps_target": terms_by_name[name]["write_qps_target"],
                "completion_gap": terms_by_name[name]["completion_gap"],
            }
            for name in names
        }
    elif mode == "adaptive_debt_safe_v1":
        if adaptive_state is None:
            adaptive_state = {}
        terms_by_name, diagnostics = debt_safe_v1_terms(args, prev_metrics, adaptive_state)
        scores = {name: terms["demand_score"] for name, terms in terms_by_name.items()}
        ranked = sorted(names, key=lambda name: scores[name], reverse=True)
        high = set(ranked[: args.high_count])
        low_needed = int(args.low_count)
        low_ranked = [name for name in reversed(ranked) if name not in high]
        protected = {
            name for name in low_ranked
            if bool(terms_by_name[name].get("protected", 0.0))
        }
        low = [name for name in low_ranked if name not in protected][:low_needed]
        fallback_used: list[str] = []
        if len(low) < low_needed:
            already = set(low)
            fallback = sorted(
                (name for name in low_ranked if name not in already),
                key=lambda name: (terms_by_name[name]["debt_risk"], scores[name], name),
            )
            fallback_used = fallback[: low_needed - len(low)]
            low.extend(fallback_used)
        low_set = set(low)
        out = {}
        for name in names:
            assigned = "high" if name in high else ("low" if name in low_set else "mid")
            if spread_budgets is not None:
                budget = int(spread_budgets[assigned])
            else:
                budget = tier_budget(args, assigned)
            out[name] = {"assigned_tier": assigned, "budget": budget}
        diagnostics["ranked"] = ranked
        diagnostics["assigned_high"] = sorted(high)
        diagnostics["assigned_low"] = sorted(low_set)
        diagnostics["base_low_by_demand"] = low_ranked[:low_needed]
        diagnostics["protected_low_candidates"] = sorted(protected)
        diagnostics["fallback_low_used"] = sorted(fallback_used)
        diagnostics["low_changed_from_fixed_demand"] = sorted(set(low_ranked[:low_needed]) ^ low_set)
        diagnostics["tenant_scores"] = {
            name: {
                "score": scores[name],
                "write_qps_target": terms_by_name[name]["write_qps_target"],
                "gap_norm": terms_by_name[name]["gap_norm"],
                "pending_norm": terms_by_name[name]["pending_norm"],
                "l0_norm": terms_by_name[name]["l0_norm"],
                "p99_rel": terms_by_name[name]["p99_rel"],
                "p999_rel": terms_by_name[name]["p999_rel"],
                "debt_risk": terms_by_name[name]["debt_risk"],
                "protected": bool(terms_by_name[name]["protected"]),
            }
            for name in names
        }
        adaptive_state["debt_safe_v1_prev_assigned_high"] = sorted(high)
        return out, diagnostics
    elif mode == "adaptive_demotion_guard_v1":
        if adaptive_state is None:
            adaptive_state = {}
        terms_by_name, diagnostics = demotion_guard_v1_terms(args, prev_metrics, adaptive_state)
        scores = {name: terms["demand_score"] for name, terms in terms_by_name.items()}
        ranked = sorted(names, key=lambda name: scores[name], reverse=True)
        high = set(ranked[: args.high_count])
        low_needed = int(args.low_count)
        low_ranked = [name for name in reversed(ranked) if name not in high]
        base_low = low_ranked[:low_needed]
        low = list(base_low)
        prev_assigned_high = set(str(x) for x in adaptive_state.get("demotion_guard_v1_prev_assigned_high", []))
        recent_high_ttl_raw = adaptive_state.get("demotion_guard_v1_recent_high_ttl", {})
        recent_high_ttl: dict[str, int] = {
            str(k): int(v) for k, v in recent_high_ttl_raw.items()
        } if isinstance(recent_high_ttl_raw, dict) else {}

        protectable = [
            name for name in base_low
            if recent_high_ttl.get(name, 0) > 0 and terms_by_name[name]["risky_streak"] >= 2.0
        ]
        protectable = sorted(
            protectable,
            key=lambda name: (terms_by_name[name]["debt_risk"], terms_by_name[name]["pending_norm"]),
            reverse=True,
        )
        protected_tenant = protectable[0] if protectable else None
        replacement_tenant = None
        skipped_reasons: list[str] = []
        if protected_tenant is not None:
            protected_risk = terms_by_name[protected_tenant]["debt_risk"]
            candidate_pool = [
                name for name in low_ranked[low_needed : low_needed + 5]
                if name not in high and name not in low
            ]
            for cand in candidate_pool:
                cand_terms = terms_by_name[cand]
                safe = (
                    protected_risk - cand_terms["debt_risk"] >= 0.20
                    and cand_terms["gap_norm"] <= 0.55
                    and cand_terms["pending_norm"] <= 0.55
                    and cand_terms["p999_us"] <= 80000.0
                )
                if safe:
                    replacement_tenant = cand
                    break
                skipped_reasons.append(
                    f"{cand}:risk={cand_terms['debt_risk']:.3f},gap={cand_terms['gap_norm']:.3f},pending={cand_terms['pending_norm']:.3f},p999={cand_terms['p999_us']:.0f}"
                )
            if replacement_tenant is not None:
                low.remove(protected_tenant)
                low.append(replacement_tenant)

        low_set = set(low)
        out = {}
        for name in names:
            assigned = "high" if name in high else ("low" if name in low_set else "mid")
            if spread_budgets is not None:
                budget = int(spread_budgets[assigned])
            else:
                budget = tier_budget(args, assigned)
            out[name] = {"assigned_tier": assigned, "budget": budget}

        diagnostics["ranked"] = ranked
        diagnostics["assigned_high"] = sorted(high)
        diagnostics["assigned_low"] = sorted(low_set)
        diagnostics["base_low_by_demand"] = list(base_low)
        diagnostics["prev_assigned_high"] = sorted(prev_assigned_high)
        diagnostics["recent_high_ttl_before"] = dict(sorted(recent_high_ttl.items()))
        diagnostics["protectable_low_candidates"] = protectable
        diagnostics["protected_tenant"] = protected_tenant
        diagnostics["replacement_tenant"] = replacement_tenant
        diagnostics["replacement_skipped_reasons"] = skipped_reasons
        diagnostics["low_changed_from_fixed_demand"] = sorted(set(base_low) ^ low_set)
        diagnostics["tenant_scores"] = {
            name: {
                "score": scores[name],
                "write_qps_target": terms_by_name[name]["write_qps_target"],
                "gap_norm": terms_by_name[name]["gap_norm"],
                "pending_norm": terms_by_name[name]["pending_norm"],
                "p999_us": terms_by_name[name]["p999_us"],
                "p99_rel": terms_by_name[name]["p99_rel"],
                "p999_rel": terms_by_name[name]["p999_rel"],
                "debt_risk": terms_by_name[name]["debt_risk"],
                "risky_now": bool(terms_by_name[name]["risky_now"]),
                "risky_streak": int(terms_by_name[name]["risky_streak"]),
            }
            for name in names
        }
        next_recent_high_ttl: dict[str, int] = {}
        for name, ttl in recent_high_ttl.items():
            if ttl > 1 and name not in high:
                next_recent_high_ttl[name] = ttl - 1
        for name in high:
            next_recent_high_ttl[name] = 3
        adaptive_state["demotion_guard_v1_recent_high_ttl"] = next_recent_high_ttl
        adaptive_state["demotion_guard_v1_prev_assigned_high"] = sorted(high)
        return out, diagnostics
    else:
        scores = {name: pressure_score(args, prev_metrics.get(name, {})) for name in names}
    ranked = sorted(names, key=lambda name: scores[name], reverse=True)
    high = set(ranked[: args.high_count])
    low = set(ranked[-args.low_count :])
    out = {}
    for name in names:
        assigned = "high" if name in high else ("low" if name in low else "mid")
        if spread_budgets is not None:
            budget = int(spread_budgets[assigned])
        else:
            budget = tier_budget(args, assigned)
        out[name] = {"assigned_tier": assigned, "budget": budget}
    if diagnostics is not None:
        diagnostics["ranked"] = ranked
        diagnostics["assigned_high"] = sorted(high)
        diagnostics["assigned_low"] = sorted(low)
    if mode == "adaptive_ranklag_v1" and adaptive_state is not None:
        adaptive_state["prev_assigned_high"] = sorted(high)
    return out, diagnostics


def decide_allocation(
    args: argparse.Namespace,
    window: int,
    tiers: dict[str, list[str]],
    prev_metrics: dict[str, dict[str, str]],
    adaptive_state: dict[str, object] | None = None,
    return_diagnostics: bool = False,
) -> dict[str, dict[str, object]] | tuple[dict[str, dict[str, object]], dict[str, object] | None]:
    names = [tenant_name(i) for i in range(args.tenant_count)]
    if args.policy == "static":
        out = {name: {"assigned_tier": "mid", "budget": args.mid_budget} for name in names}
        return (out, None) if return_diagnostics else out
    if args.policy == "unlimited":
        out = {name: {"assigned_tier": "unlimited", "budget": 0} for name in names}
        return (out, None) if return_diagnostics else out
    if args.policy == "oracle_tiered":
        out = {
            name: {"assigned_tier": tenant_tier(name, tiers), "budget": tier_budget(args, tenant_tier(name, tiers))}
            for name in names
        }
        return (out, None) if return_diagnostics else out
    if args.policy == "oracle_drain":
        hot: set[str] = set(tiers["high"])
        for lookback in range(1, args.oracle_drain_windows + 1):
            if window - lookback >= 0:
                hot.update(workload_tiers(args, window - lookback)["high"])
        # Keep the aggregate budget equal to static fair share. With the
        # symmetric default budgets, each high-budget tenant requires one
        # low-budget tenant. For asymmetric budgets, round up conservatively.
        high_extra = max(0, args.high_budget - args.mid_budget)
        low_savings = max(1, args.mid_budget - args.low_budget)
        low_needed = min(len(names) - len(hot), math.ceil(len(hot) * high_extra / low_savings))
        candidates = [name for name in tiers["low"] if name not in hot]
        candidates += [name for name in names if name not in hot and name not in candidates]
        low = set(candidates[:low_needed])
        out = {}
        for name in names:
            assigned = "high" if name in hot else ("low" if name in low else "mid")
            out[name] = {"assigned_tier": assigned, "budget": tier_budget(args, assigned)}
        return (out, None) if return_diagnostics else out
    if args.policy == "static_biased":
        tiers_w0 = workload_tiers(args, 0)
        out = {
            name: {"assigned_tier": tenant_tier(name, tiers_w0), "budget": tier_budget(args, tenant_tier(name, tiers_w0))}
            for name in names
        }
        return (out, None) if return_diagnostics else out
    if args.policy != "online":
        raise ValueError(args.policy)
    if window == 0 or not prev_metrics:
        out = {name: {"assigned_tier": "mid", "budget": args.mid_budget} for name in names}
        return (out, None) if return_diagnostics else out
    budget_mode = getattr(args, "online_budget_mode", "fixed")
    spread_budgets: dict[str, int] | None = None
    adaptive_budget_diagnostics: dict[str, object] | None = None
    if budget_mode in {"adaptive_spread_v1", "adaptive_spread_v2", "adaptive_spread_v3"}:
        if adaptive_state is None:
            adaptive_state = {}
        prev_assigned_high = list(adaptive_state.get("spread_prev_assigned_high", []))
        prev_assigned_low = list(adaptive_state.get("spread_prev_assigned_low", []))
        if budget_mode == "adaptive_spread_v1":
            B_H, B_M, B_L, adaptive_budget_diagnostics = update_adaptive_spread_v1_state(
                args, prev_metrics, prev_assigned_high, prev_assigned_low, adaptive_state
            )
        else:
            if budget_mode == "adaptive_spread_v2":
                B_H, B_M, B_L, adaptive_budget_diagnostics = update_adaptive_spread_v2_state(
                    args, prev_metrics, prev_assigned_high, prev_assigned_low, adaptive_state
                )
            else:
                B_H, B_M, B_L, adaptive_budget_diagnostics = update_adaptive_spread_v3_state(
                    args, prev_metrics, prev_assigned_high, prev_assigned_low, adaptive_state
                )
        spread_budgets = {"high": B_H, "mid": B_M, "low": B_L}
    out, diagnostics = online_score_mode_allocation(
        args, names, prev_metrics, adaptive_state, spread_budgets=spread_budgets
    )
    if budget_mode in {"adaptive_spread_v1", "adaptive_spread_v2", "adaptive_spread_v3"}:
        rebalance_diag = rebalance_budget_exact(args, out)
        if rebalance_diag is not None:
            if diagnostics is None:
                diagnostics = {}
            diagnostics["budget_rebalance"] = rebalance_diag
    if budget_mode in {"adaptive_spread_v1", "adaptive_spread_v2", "adaptive_spread_v3"} and adaptive_state is not None:
        adaptive_state["spread_prev_assigned_high"] = sorted(
            name for name, row in out.items() if row.get("assigned_tier") == "high"
        )
        adaptive_state["spread_prev_assigned_low"] = sorted(
            name for name, row in out.items() if row.get("assigned_tier") == "low"
        )
    if adaptive_budget_diagnostics is not None:
        if diagnostics is None:
            diagnostics = {}
        diagnostics["adaptive_budget_diagnostics"] = adaptive_budget_diagnostics
    return (out, diagnostics) if return_diagnostics else out


def _resolve_path_override(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    return Path(text).expanduser().resolve()


def _multiarch_names() -> list[str]:
    names: list[str] = []
    for value in (
        os.environ.get("DEB_HOST_MULTIARCH"),
        sysconfig.get_config_var("MULTIARCH"),
        "x86_64-linux-gnu",
        "aarch64-linux-gnu",
        "arm-linux-gnueabihf",
    ):
        if value and value not in names:
            names.append(str(value))
    return names


def resolve_rocksdb_include_dir(args: argparse.Namespace) -> Path:
    override = _resolve_path_override(getattr(args, "rocksdb_include_dir", None))
    env_override = _resolve_path_override(os.environ.get("ROCKSDB_INCLUDE_DIR"))
    candidates = [
        p for p in (
            override,
            env_override,
            args.root / "opt/debroot/usr/include",
            args.root / "opt/rocksdb/include",
            Path("/usr/local/include"),
            Path("/usr/include"),
        )
        if p is not None
    ]
    return next((p for p in candidates if p.exists()), candidates[0])


def resolve_rocksdb_lib_dir(args: argparse.Namespace) -> Path:
    override = _resolve_path_override(getattr(args, "rocksdb_lib_dir", None))
    env_override = _resolve_path_override(os.environ.get("ROCKSDB_LIB_DIR"))
    deb_usr = args.root / "opt/debroot/usr"
    candidates: list[Path] = [p for p in (override, env_override) if p is not None]
    candidates.extend(deb_usr / "lib" / name for name in _multiarch_names())
    candidates.extend([
        deb_usr / "lib",
        args.root / "opt/rocksdb/lib",
        Path("/usr/local/lib"),
    ])
    candidates.extend(Path("/usr/lib") / name for name in _multiarch_names())
    candidates.append(Path("/usr/lib"))
    return next((p for p in candidates if p.exists()), candidates[0])


def compile_harness(args: argparse.Namespace) -> Path:
    scripts = script_dir(args.root)
    src = scripts / "continuous_kv_harness.cc"
    binary = args.root / "build" / "continuous_kv_harness"
    if binary.exists() and binary.stat().st_mtime >= src.stat().st_mtime:
        return binary
    binary.parent.mkdir(parents=True, exist_ok=True)
    include = resolve_rocksdb_include_dir(args)
    lib = resolve_rocksdb_lib_dir(args)
    cmd = [
        args.cxx,
        "-O2",
        "-std=c++17",
        "-Wall",
        "-Wextra",
        "-I",
        str(include),
        str(src),
        "-L",
        str(lib),
        f"-Wl,-rpath,{lib}",
        "-lrocksdb",
        "-lpthread",
        "-ldl",
        "-lsnappy",
        "-lz",
        "-lbz2",
        "-llz4",
        "-lzstd",
        "-o",
        str(binary),
    ]
    subprocess.run(cmd, check=True)
    return binary


def compile_throttle(args: argparse.Namespace) -> Path:
    if sys.platform != "linux":
        raise SystemExit("--use-ldpreload-throttle requires Linux LD_PRELOAD and /proc/self/fd")
    scripts = script_dir(args.root)
    src = scripts / "io_throttle.c"
    so = args.root / "build" / "libio_throttle.so"
    if so.exists() and so.stat().st_mtime >= src.stat().st_mtime:
        return so
    so.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([args.cc, "-O2", "-Wall", "-shared", "-fPIC", str(src), "-o", str(so), "-ldl", "-pthread"], check=True)
    return so


def env_base(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    lib = resolve_rocksdb_lib_dir(args)
    if lib.exists():
        env["LD_LIBRARY_PATH"] = str(lib) + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    return env


def read_last_csv(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.DictReader(f))
            return rows[-1] if rows else None
    except OSError:
        return None


def read_window_csv(path: Path, window: int) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            rows = [row for row in csv.DictReader(f) if int(row.get("window", -1)) == window]
            return rows[-1] if rows else None
    except (OSError, ValueError):
        return None


def read_all_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def run_prefill(args: argparse.Namespace, binary: Path, env: dict[str, str], data_root: Path, log_root: Path) -> list[dict[str, object]]:
    rows = []
    for i in range(args.tenant_count):
        name = tenant_name(i)
        db = data_root / name
        db.mkdir(parents=True, exist_ok=True)
        log = log_root / f"prefill_{name}.log"
        cmd = [
            str(binary),
            "--mode=prefill",
            f"--db={db}",
            f"--tenant={name}",
            f"--prefill-keys={args.prefill_keys}",
            f"--num-keys={args.num_keys}",
            f"--prefill-threads={args.prefill_threads}",
            f"--value-size={args.value_size}",
            f"--write-buffer-size={args.write_buffer_size}",
            f"--max-write-buffer-number={args.max_write_buffer_number}",
            f"--target-file-size-base={args.target_file_size_base}",
            f"--max-background-jobs={args.max_background_jobs}",
        ]
        start = time.time()
        with log.open("w", encoding="utf-8", errors="replace") as f:
            f.write("COMMAND: " + " ".join(cmd) + "\n")
            f.flush()
            proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env, timeout=args.timeout)
            runtime = time.time() - start
            f.write(f"\nEXIT_CODE: {proc.returncode}\nRUNTIME_SEC: {runtime:.3f}\n")
        rows.append({"tenant": name, "exit_code": proc.returncode, "runtime_sec": runtime, "log": str(log)})
        if proc.returncode != 0:
            raise RuntimeError(f"prefill failed for {name}, see {log}")
    return rows


def launch_tenant(
    args: argparse.Namespace,
    binary: Path,
    env0: dict[str, str],
    throttle_so: Path | None,
    i: int,
    data_root: Path,
    log_root: Path,
    run_root: Path,
) -> tuple[subprocess.Popen, object, list[str]]:
    name = tenant_name(i)
    db = data_root / name
    control = run_root / f"{name}.control"
    budget = run_root / f"{name}.budget"
    metrics = log_root / f"{name}_metrics.csv"
    log = log_root / f"work_{name}.log"
    cmd = [
        str(binary),
        "--mode=workload",
        f"--db={db}",
        f"--tenant={name}",
        f"--control-file={control}",
        f"--metrics-file={metrics}",
        f"--duration-sec={args.duration_sec}",
        f"--window-sec={args.window_sec}",
        f"--threads={args.threads}",
        f"--num-keys={args.num_keys}",
        f"--value-size={args.value_size}",
        f"--write-buffer-size={args.write_buffer_size}",
        f"--max-write-buffer-number={args.max_write_buffer_number}",
        f"--l0-compact-trigger={args.l0_compact_trigger}",
        f"--l0-slowdown-trigger={args.l0_slowdown_trigger}",
        f"--l0-stop-trigger={args.l0_stop_trigger}",
        f"--target-file-size-base={args.target_file_size_base}",
        f"--max-background-jobs={args.max_background_jobs}",
        f"--control-refresh-ms={args.control_refresh_ms}",
        f"--use-rocksdb-rate-limiter={1 if args.use_rocksdb_rate_limiter else 0}",
    ]
    env = env0.copy()
    if throttle_so is not None:
        env["LD_PRELOAD"] = str(throttle_so) + (os.pathsep + env["LD_PRELOAD"] if env.get("LD_PRELOAD") else "")
        env["THROTTLE_ROOT"] = str(db)
        env["THROTTLE_BUDGET_FILE"] = str(budget)
        env["THROTTLE_LOG"] = str(log_root / f"{name}_throttle.csv")
        env["THROTTLE_TENANT"] = name
        env["THROTTLE_REFRESH_MS"] = str(args.throttle_refresh_ms)
        env["THROTTLE_LOG_MS"] = str(args.throttle_log_ms)
    handle = log.open("w", encoding="utf-8", errors="replace")
    handle.write("COMMAND: " + " ".join(cmd) + "\n")
    handle.flush()
    proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, env=env)
    return proc, handle, cmd


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cxx", default=os.environ.get("CXX", "g++"), help="C++ compiler for continuous_kv_harness.cc.")
    parser.add_argument("--cc", default=os.environ.get("CC", "gcc"), help="C compiler for optional io_throttle.c.")
    parser.add_argument("--rocksdb-include-dir", type=Path, default=None, help="Override RocksDB include directory; also accepts ROCKSDB_INCLUDE_DIR.")
    parser.add_argument("--rocksdb-lib-dir", type=Path, default=None, help="Override RocksDB library directory; also accepts ROCKSDB_LIB_DIR.")
    parser.add_argument("--policy", choices=["static", "oracle_tiered", "oracle_drain", "online", "unlimited", "static_biased"], required=True)
    parser.add_argument("--trial", default="embedded_continuous_a")
    parser.add_argument("--tenant-count", type=int, default=16)
    parser.add_argument("--duration-sec", type=int, default=360)
    parser.add_argument("--window-sec", type=int, default=30)
    parser.add_argument("--num-keys", type=int, default=160_000)
    parser.add_argument("--prefill-keys", type=int, default=160_000)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--prefill-threads", type=int, default=2)
    parser.add_argument("--value-size", type=int, default=1024)
    parser.add_argument("--write-buffer-size", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--max-write-buffer-number", type=int, default=3)
    parser.add_argument("--l0-compact-trigger", type=int, default=2)
    parser.add_argument("--l0-slowdown-trigger", type=int, default=5)
    parser.add_argument("--l0-stop-trigger", type=int, default=9)
    parser.add_argument("--target-file-size-base", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--max-background-jobs", type=int, default=2)
    parser.add_argument("--high-count", type=int, default=4)
    parser.add_argument("--low-count", type=int, default=4)
    parser.add_argument("--oracle-drain-windows", type=int, default=1)
    parser.add_argument("--high-budget", type=int, default=11_000_000)
    parser.add_argument("--mid-budget", type=int, default=7_000_000)
    parser.add_argument("--low-budget", type=int, default=3_000_000)
    parser.add_argument("--high-write-qps", type=float, default=2800.0)
    parser.add_argument("--mid-write-qps", type=float, default=1200.0)
    parser.add_argument("--low-write-qps", type=float, default=250.0)
    parser.add_argument("--high-read-qps", type=float, default=900.0)
    parser.add_argument("--mid-read-qps", type=float, default=1600.0)
    parser.add_argument("--low-read-qps", type=float, default=2200.0)
    parser.add_argument("--high-hot-frac", type=float, default=0.30)
    parser.add_argument("--mid-hot-frac", type=float, default=0.45)
    parser.add_argument("--low-hot-frac", type=float, default=0.70)
    parser.add_argument("--initial-hot-center", type=float, default=1.5)
    parser.add_argument("--drift-tenants", type=float, default=0.0)
    parser.add_argument("--control-refresh-ms", type=int, default=200)
    parser.add_argument("--metrics-wait-sec", type=float, default=2.0)
    parser.add_argument("--throttle-refresh-ms", type=int, default=200)
    parser.add_argument("--throttle-log-ms", type=int, default=1000)
    parser.add_argument("--use-ldpreload-throttle", action="store_true")
    parser.add_argument("--no-rocksdb-rate-limiter", action="store_true")
    parser.add_argument(
        "--online-score-mode",
        choices=[
            "pressure",
            "demand",
            "hybrid",
            "adaptive_demand_v1",
            "adaptive_ranklag_v1",
            "adaptive_debt_safe_v1",
            "adaptive_demotion_guard_v1",
        ],
        default="pressure",
        help="Online classifier scoring mode. pressure: completed-write+tail+l0 (legacy). demand: observed offered write_qps_target + completion gap. hybrid: blend of demand and completion. adaptive_demand_v1: demand anchor with bounded EMA residual calibration. adaptive_ranklag_v1: bounded adaptation of alpha_D and lambda_P driven by observed rank drift and assignment miss. adaptive_debt_safe_v1: fixed-demand high placement with fixed budgets, but low placement avoids observed debt/tail-risk tenants when possible. adaptive_demotion_guard_v1: fixed-demand high/low except at most one safe low replacement to prevent persistent risky high-to-low demotion.",
    )
    parser.add_argument(
        "--online-budget-mode",
        choices=["fixed", "adaptive_spread_v1", "adaptive_spread_v2", "adaptive_spread_v3"],
        default="fixed",
        help="Online budget intensity mode. fixed: per-tier budgets stay at --high-budget/--mid-budget/--low-budget. adaptive_spread_v1: original optional spread controller, unchanged. adaptive_spread_v2: conservative spread controller using previous-window high pressure, relative low-tail collateral, and total-throughput EMA; spread_multiplier in [0.90, 1.15], with B_L >= 0.80*B_L0. adaptive_spread_v3: final very conservative controller with spread_multiplier in [0.97, 1.05], ±0.02 per-window slew, high-P999 guard toward fixed, and B_L >= 0.90*B_L0. Placement (which tenant is high/mid/low) is unchanged.",
    )
    parser.add_argument(
        "--score-anchor",
        type=float,
        default=6.00,
        help="Demand-mode anchor coefficient applied to write_qps_target/1000. "
             "Default 6.00 matches the paper formula; ablation only.",
    )
    parser.add_argument(
        "--score-residual-scale",
        type=float,
        default=1.0,
        help="Uniform scale on the five demand-mode residual terms "
             "(gap, tail, compact, pending, L0). Default 1.0 matches the paper; ablation only.",
    )
    parser.add_argument(
        "--score-drop-residual",
        action="store_true",
        help="Drop the residual block entirely (anchor-only score). "
             "Equivalent to --score-residual-scale 0.0; ablation only.",
    )
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--keep-data", action="store_true")
    parser.add_argument(
        "--cachelib-schedule",
        type=Path,
        default=None,
        help="Optional path to a selected_segments.json frozen schedule produced by "
             "prepare_cachelib_trace.py. When set, the runner replays the per-window "
             "per-tenant offered demand directly from the frozen schedule and overrides "
             "tenant_count / num_keys / prefill_keys / window_sec / windows / "
             "duration_sec / high_count / low_count to match the segment.",
    )
    parser.add_argument(
        "--cachelib-segment-id",
        default=None,
        help="Segment index to replay from --cachelib-schedule. Defaults to the first "
             "selected segment.",
    )
    args = parser.parse_args()
    args.root = args.root.expanduser().resolve()
    args.rocksdb_include_dir = _resolve_path_override(args.rocksdb_include_dir)
    args.rocksdb_lib_dir = _resolve_path_override(args.rocksdb_lib_dir)
    if args.cachelib_schedule is not None:
        args.cachelib_schedule = args.cachelib_schedule.expanduser().resolve()

    args.cachelib_payload = None
    args.cachelib_per_window_tiers = None
    args.cachelib_meta = None
    if args.cachelib_schedule:
        load_cachelib_schedule(args)
    else:
        args.windows = int(math.ceil(args.duration_sec / args.window_sec))
    args.use_rocksdb_rate_limiter = (not args.no_rocksdb_rate_limiter) and args.policy != "unlimited"
    if args.drift_tenants <= 0:
        args.drift_tenants = max(1.0, args.tenant_count / 2.0)

    result_out = args.root / "results" / f"embedded_continuous_{args.policy}_{args.trial}.json"
    result_out.parent.mkdir(parents=True, exist_ok=True)
    if result_out.exists():
        result_out.unlink()

    data_root = args.root / "data" / f"embedded_continuous_{args.policy}_{args.trial}"
    log_root = args.root / "logs" / f"embedded_continuous_{args.policy}_{args.trial}"
    run_root = args.root / "run" / f"embedded_continuous_{args.policy}_{args.trial}"
    for path in [log_root, run_root]:
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    if data_root.exists() and not args.keep_data:
        shutil.rmtree(data_root)
    data_root.mkdir(parents=True, exist_ok=True)

    binary = compile_harness(args)
    throttle_so = None
    if args.policy != "unlimited" and args.use_ldpreload_throttle:
        throttle_so = compile_throttle(args)
    env = env_base(args)

    prefill = []
    if not args.keep_data:
        prefill = run_prefill(args, binary, env, data_root, log_root)

    tiers0 = workload_tiers(args, 0)
    alloc0 = decide_allocation(args, 0, tiers0, {})
    validate_budget(args, alloc0)
    for i in range(args.tenant_count):
        name = tenant_name(i)
        true_tier = tenant_tier(name, tiers0)
        assigned = str(alloc0[name]["assigned_tier"])
        budget = int(alloc0[name]["budget"])
        write_control(args, run_root / f"{name}.control", run_root / f"{name}.budget", i, true_tier, assigned, budget, window=0)

    procs: dict[str, subprocess.Popen] = {}
    handles: dict[str, object] = {}
    commands: dict[str, list[str]] = {}
    starts: dict[str, float] = {}
    for i in range(args.tenant_count):
        name = tenant_name(i)
        proc, handle, cmd = launch_tenant(args, binary, env, throttle_so, i, data_root, log_root, run_root)
        procs[name] = proc
        handles[name] = handle
        commands[name] = cmd
        starts[name] = time.time()

    allocation_history = []
    prev_metrics: dict[str, dict[str, str]] = {}
    adaptive_state: dict[str, object] = {}
    start_mono = time.monotonic()
    for window in range(args.windows):
        tiers = workload_tiers(args, window)
        allocation, allocation_diagnostics = decide_allocation(
            args,
            window,
            tiers,
            prev_metrics,
            adaptive_state=adaptive_state,
            return_diagnostics=True,
        )
        validate_budget(args, allocation)
        allocation_entry = {
            "window": window,
            "elapsed_start_sec": window * args.window_sec,
            "elapsed_end_sec": min(args.duration_sec, (window + 1) * args.window_sec),
            "tiers": tiers,
            "allocation": allocation,
        }
        if allocation_diagnostics is not None:
            allocation_entry["online_score_diagnostics"] = allocation_diagnostics
            adaptive_budget_diag = allocation_diagnostics.get("adaptive_budget_diagnostics") if isinstance(allocation_diagnostics, dict) else None
            if adaptive_budget_diag is not None:
                allocation_entry["adaptive_budget_diagnostics"] = adaptive_budget_diag
        allocation_history.append(allocation_entry)
        for i in range(args.tenant_count):
            name = tenant_name(i)
            true_tier = tenant_tier(name, tiers)
            assigned = str(allocation[name]["assigned_tier"])
            budget = int(allocation[name]["budget"])
            write_control(args, run_root / f"{name}.control", run_root / f"{name}.budget", i, true_tier, assigned, budget, window=window)

        target = start_mono + min(args.duration_sec, (window + 1) * args.window_sec)
        while time.monotonic() < target:
            if all(p.poll() is not None for p in procs.values()):
                break
            time.sleep(min(1.0, max(0.0, target - time.monotonic())))

        metrics = {}
        deadline = time.monotonic() + args.metrics_wait_sec
        while time.monotonic() <= deadline:
            metrics = {}
            for i in range(args.tenant_count):
                name = tenant_name(i)
                row = read_window_csv(log_root / f"{name}_metrics.csv", window)
                if row is not None:
                    metrics[name] = row
            if len(metrics) == args.tenant_count:
                break
            time.sleep(0.05)
        prev_metrics = metrics

    final_tiers = workload_tiers(args, args.windows - 1)
    for i in range(args.tenant_count):
        name = tenant_name(i)
        true_tier = tenant_tier(name, final_tiers)
        alloc = allocation_history[-1]["allocation"][name] if allocation_history else {"assigned_tier": "mid", "budget": args.mid_budget}
        write_control(
            args,
            run_root / f"{name}.control",
            run_root / f"{name}.budget",
            i,
            true_tier,
            str(alloc["assigned_tier"]),
            int(alloc["budget"]),
            stop=1,
        )

    final_rows = []
    for i in range(args.tenant_count):
        name = tenant_name(i)
        proc = procs[name]
        handle = handles[name]
        try:
            code = proc.wait(timeout=max(1, args.timeout))
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
        handle.write(f"\nEXIT_CODE: {code}\nRUNTIME_SEC: {time.time() - starts[name]:.3f}\n")
        handle.close()
        final_rows.append(
            {
                "tenant": name,
                "exit_code": code,
                "runtime_sec": time.time() - starts[name],
                "log": str(log_root / f"work_{name}.log"),
                "metrics": str(log_root / f"{name}_metrics.csv"),
                "throttle": str(log_root / f"{name}_throttle.csv"),
            }
        )

    window_records = []
    for i in range(args.tenant_count):
        name = tenant_name(i)
        for row in read_all_csv(log_root / f"{name}_metrics.csv"):
            row["tenant_index"] = i
            row["process_exit_code"] = next((r["exit_code"] for r in final_rows if r["tenant"] == name), None)
            window_records.append(row)

    failed_tenants = sum(1 for r in final_rows if int(r["exit_code"]) != 0)
    summary = {
        "policy": args.policy,
        "trial": args.trial,
        "tenant_count": args.tenant_count,
        "duration_sec": args.duration_sec,
        "window_sec": args.window_sec,
        "failed_tenants": failed_tenants,
        "windows_recorded": len({int(r["window"]) for r in window_records}) if window_records else 0,
        "sum_compact_output_bytes": sum(float(r.get("compact_output_bytes", 0.0)) for r in window_records),
        "sum_write_ops": sum(float(r.get("write_ops", 0.0)) for r in window_records),
        "sum_read_ops": sum(float(r.get("read_ops", 0.0)) for r in window_records),
    }
    args_dump: dict = {}
    for k, v in vars(args).items():
        if k in {"cachelib_payload", "cachelib_per_window_tiers"}:
            continue  # large; redundant with cachelib_meta + allocation_history.
        args_dump[k] = str(v) if isinstance(v, Path) else v
    payload = {
        "args": args_dump,
        "cachelib_external_metadata": args.cachelib_meta,
        "summary": summary,
        "prefill": prefill,
        "commands": commands,
        "allocation_history": allocation_history,
        "final_rows": final_rows,
        "window_records": window_records,
        "notes": {
            "continuous_processes": "Each tenant is one long-running embedded RocksDB process during workload.",
            "runtime_workload_control": "Runner updates per-tenant control files; tenant processes reread write/read QPS and hot range while running.",
            "runtime_budget_control": "By default, the tenant harness calls RocksDB RateLimiter::SetBytesPerSecond while the process runs; optional LD_PRELOAD DB-file write throttling can be enabled for actuation diagnostics.",
            "fixed_rocksdb_knob": "max_background_jobs is fixed at process launch and is not claimed to change at runtime.",
            "window_latency": "Per-window write/read P99 and P999 are measured inside the tenant harness, not inferred from whole-run db_bench output.",
        },
    }
    result_out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if failed_tenants else 0


if __name__ == "__main__":
    raise SystemExit(main())
