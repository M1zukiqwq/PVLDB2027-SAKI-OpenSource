#!/usr/bin/env python3
"""SAKI continuous window-length mini-sweep.

Reviewer-facing sensitivity sweep around the continuous main result
`embedded_demand2f_16t`. Holds every per-tenant offered-demand and budget
constant -- so the fixed-budget contract is preserved -- and varies only
`window_sec`, the SAKI control window. The point is to bracket the chosen
20-second anchor and report what happens at 10s (more agile, more noisy)
and 40s (slower, larger adaptation lag), not to tune a new main result.

This driver mirrors `run_workload_matrix.py`. It delegates each (policy, trial)
run to `run_embedded_continuous.py` and analysis to `analyze_embedded_continuous.py`.

The anchor below intentionally matches the per-tenant offered demand and
per-tenant budgets recorded in the existing main continuous runs
(`remote-results/embedded_continuous_online_embedded_demand2f_16t.json`):
high/mid/low budget = 11/6/1 MB/s and offered demand 2400/1200/100 writes/s.
The aggregate budget is therefore 4*11 + 8*6 + 4*1 = 96 MB/s. The 11/7/3
public-trace audit contract is a different epoch/public-trace anchor
(112 MB/s aggregate); we do not silently mix the two budgets here.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


# -- Anchor (matches actual main continuous run; see module docstring) -------
ANCHOR_ARGS: dict[str, object] = {
    "tenant_count": 16,
    "duration_sec": 160,
    # window_sec is the swept axis (overridden per run)
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

DEFAULT_WINDOWS = [10, 20, 40]
DEFAULT_SEEDS = ["a", "b", "c"]
DEFAULT_POLICIES = ["static", "online"]


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def resolve_script(root: Path, name: str) -> Path:
    for base in (root / "remote", root / "scripts", Path(__file__).resolve().parent):
        path = base / name
        if path.exists():
            return path
    return root / "remote" / name


def trial_name(window_sec: int, seed: str) -> str:
    return f"wl{window_sec}_{seed}"


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


def clear_trial_outputs(root: Path, trial: str, policies: list[str]) -> dict[str, bool]:
    removed = {
        f"raw_{policy}": unlink_if_exists(raw_output_path(root, policy, trial))
        for policy in policies
    }
    removed["analysis"] = unlink_if_exists(analysis_output_path(root, trial))
    return removed


def build_args(window_sec: int, seed: str) -> dict[str, object]:
    args = dict(ANCHOR_ARGS)
    args["window_sec"] = window_sec
    args["trial"] = trial_name(window_sec, seed)
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
    window_sec: int,
    seed: str,
    log_dir: Path,
    online_score_mode: str = "demand",
    online_budget_mode: str = "fixed",
    dry_run: bool = False,
) -> dict[str, object]:
    args = build_args(window_sec, seed)
    cmd = [sys.executable, str(runner), "--root", str(root), "--policy", policy] + cli_flags(args)
    if policy == "online":
        cmd += [
            "--online-score-mode", online_score_mode,
            "--online-budget-mode", online_budget_mode,
        ]
    log = log_dir / f"{policy}_{trial_name(window_sec, seed)}.log"
    rec: dict[str, object] = {
        "policy": policy,
        "window_sec": window_sec,
        "seed": seed,
        "trial": trial_name(window_sec, seed),
        "log": str(log),
        "cmd": cmd,
        "output": str(raw_output_path(root, policy, trial_name(window_sec, seed))),
    }
    if dry_run:
        rec["dry_run"] = True
        return rec
    log_dir.mkdir(parents=True, exist_ok=True)
    rec["removed_stale_output"] = unlink_if_exists(raw_output_path(root, policy, trial_name(window_sec, seed)))
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


def run_analysis(
    analyzer: Path,
    root: Path,
    window_sec: int,
    seed: str,
    log_dir: Path,
    policies: list[str],
    dry_run: bool = False,
) -> dict[str, object]:
    trial = trial_name(window_sec, seed)
    cmd = [sys.executable, str(analyzer), "--root", str(root), "--trial", trial, "--policies", *policies]
    log = log_dir / f"analysis_{trial}.log"
    rec: dict[str, object] = {
        "window_sec": window_sec,
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root(),
        help="Experiment workdir containing results/. Defaults to this repository root.",
    )
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=DEFAULT_WINDOWS,
        help="window_sec values to sweep. Default: 10 20 40.",
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        default=DEFAULT_SEEDS,
        help="Trial seeds. Default: a b c (n=3).",
    )
    parser.add_argument(
        "--policies",
        nargs="+",
        default=DEFAULT_POLICIES,
        choices=["static", "online"],
    )
    parser.add_argument("--manifest", type=Path, help="Write run manifest JSON here.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--stop-window-on-failure",
        action="store_true",
        default=True,
        help="If a run fails for a given window_sec, skip remaining seeds for that window.",
    )
    args = parser.parse_args()
    args.root = args.root.expanduser().resolve()

    runner = resolve_script(args.root, "run_embedded_continuous.py")
    analyzer = resolve_script(args.root, "analyze_embedded_continuous.py")
    log_dir = args.root / "logs" / "window_length_sweep"
    if not args.dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "anchor": ANCHOR_ARGS,
        "windows": args.windows,
        "seeds": args.seeds,
        "policies": args.policies,
        "runs": [],
        "analyses": [],
        "skipped": [],
    }

    total_units = len(args.windows) * len(args.seeds) * len(args.policies)
    print(
        f"[window_length_sweep] {total_units} runs queued: "
        f"windows={args.windows} seeds={args.seeds} policies={args.policies}"
    )

    failed_windows: set[int] = set()
    had_failure = False
    unit = 0
    for window_sec in args.windows:
        for seed in args.seeds:
            if window_sec in failed_windows:
                trial = trial_name(window_sec, seed)
                manifest["skipped"].append({
                    "window_sec": window_sec, "seed": seed,
                    "trial": trial,
                    "reason": "earlier seed failed for this window_sec",
                    "removed_stale_outputs": clear_trial_outputs(args.root, trial, args.policies)
                    if not args.dry_run
                    else {},
                })
                print(
                    f"[window_length_sweep] SKIP wl{window_sec}_{seed} "
                    f"(earlier failure in this window_sec)"
                )
                continue
            seed_failed = False
            for policy in args.policies:
                unit += 1
                print(
                    f"[window_length_sweep] ({unit}/{total_units}) START "
                    f"{policy} wl{window_sec}_{seed} @ {time.strftime('%H:%M:%S')}"
                )
                rec = run_one(runner, args.root, policy, window_sec, seed, log_dir, dry_run=args.dry_run)
                manifest["runs"].append(rec)
                if not args.dry_run:
                    rc = rec.get("exit_code")
                    dt = rec.get("runtime_sec", 0.0)
                    print(
                        f"[window_length_sweep] ({unit}/{total_units}) END   "
                        f"{policy} wl{window_sec}_{seed} rc={rc} dt={dt:.1f}s"
                    )
                    if rc != 0:
                        print(
                            f"[window_length_sweep] FAILED; see {rec['log']}",
                            file=sys.stderr,
                        )
                        seed_failed = True
                        had_failure = True
                        if args.stop_window_on_failure:
                            failed_windows.add(window_sec)
                        break
            if seed_failed:
                trial = trial_name(window_sec, seed)
                manifest["skipped"].append({
                    "window_sec": window_sec,
                    "seed": seed,
                    "trial": trial,
                    "reason": "one or more policy runs failed; analysis skipped to avoid stale or partial data",
                    "removed_stale_outputs": clear_trial_outputs(args.root, trial, args.policies)
                    if not args.dry_run
                    else {},
                })
                continue
            arec = run_analysis(analyzer, args.root, window_sec, seed, log_dir, args.policies, dry_run=args.dry_run)
            manifest["analyses"].append(arec)
            if not args.dry_run:
                rc = arec.get("exit_code")
                print(f"[window_length_sweep] ANALYZE wl{window_sec}_{seed} rc={rc}")
                if rc != 0:
                    had_failure = True

    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        print(f"[window_length_sweep] manifest -> {args.manifest}")
    return 1 if had_failure else 0


if __name__ == "__main__":
    sys.exit(main())
