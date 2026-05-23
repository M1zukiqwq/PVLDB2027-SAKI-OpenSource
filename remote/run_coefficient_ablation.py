#!/usr/bin/env python3
"""Coefficient-robustness ablation for the demand-mode online controller.

Anchors at the published `embedded_demand2f_16t` configuration (16 tenants,
4/8/4 split, per-tenant budgets 11/6/1 MB/s, 96 MB/s aggregate, 20s window,
8 windows). Only the demand-score coefficients are perturbed.

For each (label, seed) the script:
  1. removes stale online raw / analysis files for that trial id,
  2. ensures a static raw file exists for the trial (created by symlinking the
     already-published paper-static seed: seed 1 -> embedded_demand2f_16t,
     seed 2 -> embedded_demand2f_16t_b, seed 3 -> embedded_demand2f_16t_c),
  3. runs `online --online-score-mode demand` with the perturbed coefficients,
  4. runs `analyze_embedded_continuous.py --policies static online`.

If any (policy run, analysis) returns rc!=0 or a failed tenant, the script
returns rc=1 and stops; it does not silently skip.

Output:
  - logs/coefficient_ablation/<policy>_<trial>.log
  - results/embedded_continuous_online_<trial>.json
  - results/embedded_continuous_static_<trial>.json (symlink)
  - results/embedded_continuous_analysis_<trial>.json
  - <manifest.json> if --manifest is given

This script does NOT touch the demand2f main-result raw files; it only reads
the published static ones via symlinks.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


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


# Perturbation table: (label, anchor, residual_scale, drop_residual).
LABELS: list[dict[str, object]] = [
    {"label": "anchor_half",     "anchor": 3.00,  "residual_scale": 1.0, "drop_residual": False},
    {"label": "anchor_double",   "anchor": 12.00, "residual_scale": 1.0, "drop_residual": False},
    {"label": "residual_half",   "anchor": 6.00,  "residual_scale": 0.5, "drop_residual": False},
    {"label": "residual_double", "anchor": 6.00,  "residual_scale": 2.0, "drop_residual": False},
    {"label": "anchor_only",     "anchor": 6.00,  "residual_scale": 1.0, "drop_residual": True},
]


# seed N -> paper-static trial id whose raw file already exists.
SEED_TO_STATIC_TRIAL: dict[int, str] = {
    1: "embedded_demand2f_16t",
    2: "embedded_demand2f_16t_b",
    3: "embedded_demand2f_16t_c",
}


def default_root() -> Path:
    # On remote this script lives in <root>/scripts/ or <root>/remote/.
    return Path(__file__).resolve().parents[1]


def resolve_runner(root: Path) -> Path:
    for base in (root / "scripts", root / "remote", Path(__file__).resolve().parent):
        path = base / "run_embedded_continuous.py"
        if path.exists():
            return path
    return root / "remote" / "run_embedded_continuous.py"


def resolve_analyzer(root: Path) -> Path:
    for base in (root / "scripts", root / "remote", Path(__file__).resolve().parent):
        path = base / "analyze_embedded_continuous.py"
        if path.exists():
            return path
    return root / "remote" / "analyze_embedded_continuous.py"


def trial_name(label: str, seed: int) -> str:
    return f"coef_ablation_{label}_seed{seed}"


def raw_path(root: Path, policy: str, trial: str) -> Path:
    return root / "results" / f"embedded_continuous_{policy}_{trial}.json"


def analysis_path(root: Path, trial: str) -> Path:
    return root / "results" / f"embedded_continuous_analysis_{trial}.json"


def static_donor_path(root: Path, seed: int) -> Path:
    return root / "results" / f"embedded_continuous_static_{SEED_TO_STATIC_TRIAL[seed]}.json"


def unlink_if_exists(path: Path) -> bool:
    try:
        # Use lstat so that symlinks are removed correctly even if dangling.
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def ensure_static_link(root: Path, seed: int, trial: str) -> dict[str, object]:
    donor = static_donor_path(root, seed)
    target = raw_path(root, "static", trial)
    rec: dict[str, object] = {
        "kind": "static_link",
        "seed": seed,
        "trial": trial,
        "donor": str(donor),
        "target": str(target),
    }
    if not donor.exists():
        rec["error"] = f"donor static raw not found: {donor}"
        return rec
    # Always refresh the link to point at the donor, in case the donor moved.
    if target.exists() or target.is_symlink():
        target.unlink()
    try:
        os.symlink(donor.name, target)  # relative symlink within results/
        rec["created_symlink"] = True
    except (OSError, NotImplementedError) as exc:
        shutil.copy2(donor, target)
        rec["copied_static_raw"] = True
        rec["symlink_fallback_reason"] = str(exc)
    return rec


def cli_flags(args: dict[str, object]) -> list[str]:
    flags: list[str] = []
    for key, val in args.items():
        flags.append(f"--{key.replace('_', '-')}")
        flags.append(str(val))
    return flags


def run_online(runner: Path, root: Path, label_cfg: dict[str, object], seed: int,
               log_dir: Path, dry_run: bool = False) -> dict[str, object]:
    trial = trial_name(str(label_cfg["label"]), seed)
    args = dict(ANCHOR_ARGS)
    args["trial"] = trial
    cmd = [sys.executable, str(runner), "--root", str(root),
           "--policy", "online",
           "--online-score-mode", "demand",
           "--score-anchor", str(label_cfg["anchor"]),
           "--score-residual-scale", str(label_cfg["residual_scale"])]
    if bool(label_cfg["drop_residual"]):
        cmd.append("--score-drop-residual")
    cmd += cli_flags(args)
    log = log_dir / f"online_{trial}.log"
    rec: dict[str, object] = {
        "kind": "online_run",
        "label": label_cfg["label"],
        "anchor": label_cfg["anchor"],
        "residual_scale": label_cfg["residual_scale"],
        "drop_residual": bool(label_cfg["drop_residual"]),
        "seed": seed,
        "trial": trial,
        "log": str(log),
        "cmd": cmd,
        "output": str(raw_path(root, "online", trial)),
    }
    if dry_run:
        rec["dry_run"] = True
        return rec
    log_dir.mkdir(parents=True, exist_ok=True)
    rec["removed_stale_online_raw"] = unlink_if_exists(raw_path(root, "online", trial))
    rec["removed_stale_analysis"] = unlink_if_exists(analysis_path(root, trial))
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


def run_analysis(analyzer: Path, root: Path, trial: str, log_dir: Path,
                 dry_run: bool = False) -> dict[str, object]:
    cmd = [sys.executable, str(analyzer), "--root", str(root), "--trial", trial,
           "--policies", "static", "online"]
    log = log_dir / f"analysis_{trial}.log"
    rec: dict[str, object] = {
        "kind": "analysis",
        "trial": trial,
        "log": str(log),
        "cmd": cmd,
        "output": str(analysis_path(root, trial)),
    }
    if dry_run:
        rec["dry_run"] = True
        return rec
    log_dir.mkdir(parents=True, exist_ok=True)
    rec["removed_stale_analysis"] = unlink_if_exists(analysis_path(root, trial))
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


def failed_tenants_from_analysis(root: Path, trial: str) -> int | None:
    path = analysis_path(root, trial)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        per = data.get("per_policy", {})
        out = 0
        for pol, m in per.items():
            ft = int(float(m.get("failed_tenants", 0)))
            out = max(out, ft)
        return out
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--labels", nargs="+",
                        default=[c["label"] for c in LABELS],
                        help="Subset of labels to run.")
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    args.root = args.root.expanduser().resolve()

    label_lookup = {c["label"]: c for c in LABELS}
    unknown = [l for l in args.labels if l not in label_lookup]
    if unknown:
        print(f"[coef_ablation] unknown labels: {unknown}", file=sys.stderr)
        return 2
    bad_seeds = [s for s in args.seeds if s not in SEED_TO_STATIC_TRIAL]
    if bad_seeds:
        print(f"[coef_ablation] unsupported seeds (need static donor): {bad_seeds}", file=sys.stderr)
        return 2

    runner = resolve_runner(args.root)
    analyzer = resolve_analyzer(args.root)
    log_dir = args.root / "logs" / "coefficient_ablation"
    if not args.dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "anchor": ANCHOR_ARGS,
        "labels": LABELS,
        "seeds": args.seeds,
        "seed_to_static_trial": SEED_TO_STATIC_TRIAL,
        "runner": str(runner),
        "analyzer": str(analyzer),
        "records": [],
    }

    total = len(args.labels) * len(args.seeds)
    print(f"[coef_ablation] queued {total} online runs ({len(args.labels)} labels x {len(args.seeds)} seeds)")
    unit = 0
    for label in args.labels:
        cfg = label_lookup[label]
        for seed in args.seeds:
            unit += 1
            trial = trial_name(label, seed)
            print(f"[coef_ablation] ({unit}/{total}) START {trial} @ {time.strftime('%H:%M:%S')}")
            link_rec = ensure_static_link(args.root, seed, trial)
            manifest["records"].append(link_rec)
            if not args.dry_run and "error" in link_rec:
                print(f"[coef_ablation] FAILED at static-donor symlink: {link_rec['error']}", file=sys.stderr)
                if args.manifest:
                    args.manifest.parent.mkdir(parents=True, exist_ok=True)
                    args.manifest.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
                return 1
            online_rec = run_online(runner, args.root, cfg, seed, log_dir, dry_run=args.dry_run)
            manifest["records"].append(online_rec)
            if not args.dry_run:
                rc = online_rec.get("exit_code")
                dt = online_rec.get("runtime_sec", 0.0)
                print(f"[coef_ablation] ({unit}/{total}) END   online {trial} rc={rc} dt={dt:.1f}s")
                if rc != 0:
                    print(f"[coef_ablation] FAILED online run; see {online_rec['log']}", file=sys.stderr)
                    if args.manifest:
                        args.manifest.parent.mkdir(parents=True, exist_ok=True)
                        args.manifest.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
                    return 1
            ana_rec = run_analysis(analyzer, args.root, trial, log_dir, dry_run=args.dry_run)
            manifest["records"].append(ana_rec)
            if not args.dry_run:
                rc = ana_rec.get("exit_code")
                print(f"[coef_ablation] ANALYZE {trial} rc={rc}")
                if rc != 0:
                    print(f"[coef_ablation] FAILED analysis; see {ana_rec['log']}", file=sys.stderr)
                    if args.manifest:
                        args.manifest.parent.mkdir(parents=True, exist_ok=True)
                        args.manifest.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
                    return 1
                ft = failed_tenants_from_analysis(args.root, trial)
                if ft is not None and ft > 0:
                    print(f"[coef_ablation] FAILED tenants in {trial}: {ft}", file=sys.stderr)
                    manifest["records"].append({"kind": "fatal", "trial": trial, "failed_tenants": ft})
                    if args.manifest:
                        args.manifest.parent.mkdir(parents=True, exist_ok=True)
                        args.manifest.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
                    return 1
                ana_rec["failed_tenants"] = ft

    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        print(f"[coef_ablation] manifest -> {args.manifest}")
    print("[coef_ablation] DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
