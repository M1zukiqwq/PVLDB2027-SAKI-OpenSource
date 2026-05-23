#!/usr/bin/env python3
"""Run the local workload matrix that strengthens external validity.

One-axis-at-a-time perturbations around `embedded_demand2f_16t`.  Same per-tenant
budgets, same paper Saki policy (`online --online-score-mode demand`).  This
script delegates each (policy, trial) run to the existing
`run_embedded_continuous.py` and then calls `analyze_embedded_continuous.py`.

It is intentionally a thin driver: no new score modes, no main-result changes,
no aggregate-budget changes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


# -- Anchor (must match scripts/run_demand2f.sh) ----------------------------
ANCHOR_ARGS: dict[str, str | int | float] = {
    "tenant_count": 16,
    "duration_sec": 160,
    "window_sec": 20,
    "num_keys": 80_000,
    "prefill_keys": 80_000,
    "threads": 2,
    "prefill_threads": 2,
    "value_size": 1024,
    "write_buffer_size": 2_097_152,
    "max_write_buffer_number": 3,
    "l0_compact_trigger": 2,
    "l0_slowdown_trigger": 5,
    "l0_stop_trigger": 9,
    "target_file_size_base": 2_097_152,
    "max_background_jobs": 2,
    "high_count": 4,
    "low_count": 4,
    "high_budget": 11_000_000,
    "mid_budget": 6_000_000,
    "low_budget": 1_000_000,
    "high_write_qps": 2400,
    "mid_write_qps": 1200,
    "low_write_qps": 100,
    "high_read_qps": 700,
    "mid_read_qps": 1400,
    "low_read_qps": 1800,
    "high_hot_frac": 0.18,
    "mid_hot_frac": 0.45,
    "low_hot_frac": 0.70,
    "initial_hot_center": 1.5,
    "drift_tenants": 8,
    "timeout": 1500,
}


# -- Variant overrides ------------------------------------------------------
# Each variant overrides at most one axis. high_count+low_count stays 2x low_count
# so the static fair-share aggregate equals tenant_count * mid_budget.
VARIANTS: dict[str, dict[str, object]] = {
    # A: value-size / compaction pressure
    "wm_value4k": {
        "value_size": 4096,
        "num_keys": 24_000,
        "prefill_keys": 24_000,
        "write_buffer_size": 4_194_304,
        "target_file_size_base": 4_194_304,
    },
    # B: read mix
    "wm_readheavy": {
        "high_read_qps": 2100,
        "mid_read_qps": 4200,
        "low_read_qps": 5400,
    },
    # C: drift speed
    "wm_driftfast": {
        "drift_tenants": 12,
    },
    # D: tenant scale (smaller cluster; same per-tenant budgets => aggregate scales linearly)
    "wm_8t_2high": {
        "tenant_count": 8,
        "high_count": 2,
        "low_count": 2,
        "drift_tenants": 4,
        "initial_hot_center": 0.75,
    },
}


# Trial seeds: a..e for full n=5, screening uses {a, b}.
ALL_SEEDS = ["a", "b", "c", "d", "e"]
SCREEN_SEEDS = ["a", "b"]


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_script(root: Path, name: str) -> Path:
    for base in (root / "remote", root / "scripts", Path(__file__).resolve().parent):
        path = base / name
        if path.exists():
            return path
    return root / "remote" / name


def trial_name(variant: str, seed: str) -> str:
    return f"{variant}_{seed}"


def raw_output_path(root: Path, policy: str, trial: str) -> Path:
    return root / "results" / f"embedded_continuous_{policy}_{trial}.json"


def analysis_output_path(root: Path, trial: str) -> Path:
    return root / "results" / f"embedded_continuous_analysis_{trial}.json"


def unlink_if_exists(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def clear_analysis_output(root: Path, trial: str) -> bool:
    return unlink_if_exists(analysis_output_path(root, trial))


def build_args(variant: str, seed: str) -> dict[str, object]:
    args = dict(ANCHOR_ARGS)
    args.update(VARIANTS[variant])
    args["trial"] = trial_name(variant, seed)
    return args


def cli_flags(args: dict[str, object]) -> list[str]:
    flags: list[str] = []
    for key, val in args.items():
        flags.append(f"--{key.replace('_', '-')}")
        flags.append(str(val))
    return flags


def run_one(
    runner: Path,
    root: Path,
    policy: str,
    variant: str,
    seed: str,
    log_dir: Path,
    online_score_mode: str = "demand",
    extra_env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    args = build_args(variant, seed)
    cmd = [sys.executable, str(runner), "--root", str(root), "--policy", policy] + cli_flags(args)
    if policy == "online":
        cmd += ["--online-score-mode", online_score_mode]
    log = log_dir / f"{policy}_{trial_name(variant, seed)}.log"
    rec: dict[str, object] = {
        "policy": policy,
        "variant": variant,
        "seed": seed,
        "trial": trial_name(variant, seed),
        "log": str(log),
        "cmd": cmd,
        "output": str(raw_output_path(root, policy, trial_name(variant, seed))),
    }
    if dry_run:
        rec["dry_run"] = True
        return rec
    log_dir.mkdir(parents=True, exist_ok=True)
    rec["removed_stale_output"] = unlink_if_exists(raw_output_path(root, policy, trial_name(variant, seed)))
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    start = time.time()
    with log.open("w", encoding="utf-8") as f:
        f.write("COMMAND: " + " ".join(cmd) + "\n")
        f.flush()
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, env=env)
        runtime = time.time() - start
        f.write(f"\nEXIT_CODE: {proc.returncode}\nRUNTIME_SEC: {runtime:.3f}\n")
    rec["exit_code"] = proc.returncode
    rec["runtime_sec"] = runtime
    return rec


def run_analysis(
    analyzer: Path,
    root: Path,
    variant: str,
    seed: str,
    log_dir: Path,
    policies: list[str],
    dry_run: bool = False,
) -> dict[str, object]:
    trial = trial_name(variant, seed)
    cmd = [sys.executable, str(analyzer), "--root", str(root), "--trial", trial, "--policies", *policies]
    log = log_dir / f"analysis_{trial}.log"
    rec: dict[str, object] = {
        "variant": variant,
        "seed": seed,
        "trial": trial,
        "cmd": cmd,
        "log": str(log),
        "output": str(analysis_output_path(root, trial)),
    }
    if dry_run:
        rec["dry_run"] = True
        return rec
    rec["removed_stale_output"] = unlink_if_exists(analysis_output_path(root, trial))
    start = time.time()
    with log.open("w", encoding="utf-8") as f:
        f.write("COMMAND: " + " ".join(cmd) + "\n")
        f.flush()
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
        runtime = time.time() - start
        f.write(f"\nEXIT_CODE: {proc.returncode}\nRUNTIME_SEC: {runtime:.3f}\n")
    rec["exit_code"] = proc.returncode
    rec["runtime_sec"] = runtime
    return rec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root(),
        help="Experiment workdir containing results/. Defaults to this repository root.",
    )
    parser.add_argument(
        "--variants",
        nargs="+",
        default=list(VARIANTS.keys()),
        help="Variant names to run.",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        default=SCREEN_SEEDS,
        help="Trial seeds. Default: a b (n=2 screening).",
    )
    parser.add_argument(
        "--policies",
        nargs="+",
        default=["static", "online"],
        choices=["static", "online", "oracle_tiered", "static_biased", "unlimited"],
    )
    parser.add_argument(
        "--mode",
        choices=["screen", "promote", "custom"],
        default="screen",
        help="screen: 2 seeds (a,b). promote: 3 extra seeds (c,d,e). custom: use --seeds as-is.",
    )
    parser.add_argument("--manifest", type=Path, help="Write run manifest JSON here.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.root = args.root.expanduser().resolve()

    if args.mode == "screen":
        seeds = SCREEN_SEEDS
    elif args.mode == "promote":
        seeds = [s for s in ALL_SEEDS if s not in SCREEN_SEEDS]
    else:
        seeds = args.seeds

    unknown = [v for v in args.variants if v not in VARIANTS]
    if unknown:
        print(f"unknown variants: {unknown}", file=sys.stderr)
        return 2

    runner = resolve_script(args.root, "run_embedded_continuous.py")
    analyzer = resolve_script(args.root, "analyze_embedded_continuous.py")
    log_dir = args.root / "logs" / "workload_matrix"
    if not args.dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "anchor": ANCHOR_ARGS,
        "variants": VARIANTS,
        "mode": args.mode,
        "policies": args.policies,
        "seeds": seeds,
        "runs": [],
        "analyses": [],
        "skipped": [],
    }

    total_units = len(args.variants) * len(seeds) * len(args.policies)
    print(f"[workload_matrix] {total_units} runs queued: variants={args.variants} seeds={seeds} policies={args.policies}")
    had_failure = False
    unit = 0
    for variant in args.variants:
        for seed in seeds:
            seed_failed = False
            for policy in args.policies:
                unit += 1
                t0 = time.time()
                print(f"[workload_matrix] ({unit}/{total_units}) START {policy} {variant}_{seed} @ {time.strftime('%H:%M:%S')}")
                rec = run_one(runner, args.root, policy, variant, seed, log_dir, dry_run=args.dry_run)
                manifest["runs"].append(rec)
                if not args.dry_run:
                    rc = rec.get("exit_code")
                    dt = rec.get("runtime_sec", 0.0)
                    print(f"[workload_matrix] ({unit}/{total_units}) END   {policy} {variant}_{seed} rc={rc} dt={dt:.1f}s")
                    if rc != 0:
                        print(f"[workload_matrix] FAILED; see {rec['log']}", file=sys.stderr)
                        seed_failed = True
                        had_failure = True
            if seed_failed:
                clear_analysis_output(args.root, trial_name(variant, seed))
                manifest["skipped"].append({
                    "variant": variant,
                    "seed": seed,
                    "trial": trial_name(variant, seed),
                    "reason": "one or more policy runs failed; analysis skipped to avoid stale or partial data",
                })
                continue
            # After both policies for this (variant, seed) -> analyze.
            arec = run_analysis(analyzer, args.root, variant, seed, log_dir, args.policies, dry_run=args.dry_run)
            manifest["analyses"].append(arec)
            if not args.dry_run:
                rc = arec.get("exit_code")
                print(f"[workload_matrix] ANALYZE {variant}_{seed} rc={rc}")
                if rc != 0:
                    had_failure = True

    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"[workload_matrix] manifest -> {args.manifest}")
    return 1 if had_failure else 0


if __name__ == "__main__":
    sys.exit(main())
