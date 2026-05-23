#!/usr/bin/env python3
"""Verify the SAKI public artifact layout and paper data wiring."""
from __future__ import annotations

import csv
import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def require(path: str) -> Path:
    p = ROOT / path
    if not p.exists():
        fail(f"missing required path: {path}")
    return p


def read_json(path: str) -> dict:
    return json.loads(require(path).read_text(encoding="utf-8"))


def assert_round(value: float, expected: float, digits: int, label: str) -> None:
    got = round(float(value), digits)
    if got != expected:
        fail(f"{label}: rounded value {got} != expected {expected}")


def assert_equal(value, expected, label: str) -> None:
    if value != expected:
        fail(f"{label}: {value!r} != {expected!r}")


def check_required_paths() -> None:
    for path in [
        "LICENSE",
        "README.md",
        "MANIFEST.md",
        "paper/saki_main.pdf",
        "paper/saki_main.tex",
        "paper/saki_refs.bib",
        "chapters/07_Evaluation.tex",
        "figures/data-manifest.md",
        "figures/evaluation/create_saki_figures.py",
        "figures/evaluation/fig3_continuous_summary.pdf",
        "figures/evaluation/fig4_score_ablation.pdf",
        "figures/evaluation/fig8_collateral_summary.pdf",
        "remote/continuous_kv_harness.cc",
        "remote/io_throttle.c",
        "remote/run_embedded_continuous.py",
        "remote/aggregate_workload_matrix.py",
        "remote/aggregate_window_length_sweep.py",
        "remote-results/paper_tables/main_continuous_demand2f.json",
        "remote-results/paper_tables/realistic_big_a_aggregate.json",
        "remote-results/paper_tables/workload_matrix_aggregate.json",
        "remote-results/paper_tables/window_length_sweep.json",
        "remote-results/paper_tables/rate_limiter_coverage.json",
        "remote-results/cgroup_aligned_aggregate.json",
    ]:
        require(path)


def names_matching(names: set[str], pattern: str, excluded: set[str] | None = None) -> set[str]:
    excluded = excluded or set()
    return {n for n in names if fnmatch.fnmatch(n, pattern) and n not in excluded}


def check_raw_coverage() -> None:
    rr = require("remote-results")
    names = {p.name for p in rr.iterdir() if p.is_file() and p.suffix == ".json"}
    excluded = {"embedded_continuous_adaptive_embedded_demand2f_16t.json"}
    categories = [
        ("continuous_demand2f", "embedded_continuous_*embedded_demand2f_16t*.json", 36, excluded),
        ("coefficient_ablation", "embedded_continuous_*coef_ablation*.json", 45, set()),
        ("workload_matrix", "embedded_continuous_*wm_*.json", 60, set()),
        ("window_length", "embedded_continuous_*wl*.json", 27, set()),
        ("rate_limiter_coverage", "embedded_continuous_static_rlcov*.json", 14, set()),
        ("epoch_realistic_big", "realistic_*realistic_big*.json", 40, set()),
        ("epoch_sensitivity", "realistic_*sens_*.json", 52, set()),
        ("cgroup_aligned", "cgroup_smoke_*realistic_cgroup_aligned*.json", 35, set()),
    ]
    matched: set[str] = set()
    for label, pattern, expected, ex in categories:
        hits = names_matching(names, pattern, ex)
        assert_equal(len(hits), expected, f"raw family count {label}")
        matched |= hits
    if excluded & names:
        fail("old adaptive continuous diagnostic should not be included")
    allowed_extra = {"cgroup_aligned_aggregate.json"}
    extras = sorted(n for n in names if n not in matched and n not in allowed_extra)
    if extras:
        fail(f"unexpected top-level remote-results JSON files: {extras[:10]}")


def check_headline_values() -> None:
    epoch = read_json("remote-results/paper_tables/realistic_big_a_aggregate.json")
    epoch_cmp = epoch["aggregates"]["online_vs_static_compare"]
    assert_round(epoch_cmp["online_vs_static_pct_high_mean_write_p99_us"]["mean"], -11.7, 1, "epoch high P99")
    assert_round(epoch_cmp["online_vs_static_pct_high_sum_ops_per_sec"]["mean"], 38.1, 1, "epoch high throughput")
    assert_round(epoch_cmp["online_vs_static_pct_sum_ops_per_sec"]["mean"], -1.7, 1, "epoch total throughput")
    assert_round(epoch_cmp["online_vs_static_pct_sum_compact_write_bytes"]["mean"], -21.0, 1, "epoch compact bytes")
    assert_round(epoch["aggregates"]["online_high_overlap_after_warmup"]["mean"], 3.14, 2, "epoch overlap")

    cont = read_json("remote-results/paper_tables/main_continuous_demand2f.json")
    cag = cont["aggregates"]
    assert_equal(cag["trial_count"], 5, "continuous trial count")
    assert_equal(cag["failed_tenants_total"], 0, "continuous failed tenants")
    assert_round(cag["online_vs_static_pct_high_write_p99_us"]["mean"], -27.0, 1, "continuous high P99")
    assert_round(cag["online_vs_static_pct_high_write_p999_us"]["mean"], -47.1, 1, "continuous high P999")
    assert_round(cag["online_vs_static_pct_high_write_throughput"]["mean"], 26.1, 1, "continuous high throughput")
    assert_round(cag["online_vs_static_pct_total_throughput"]["mean"], 4.3, 1, "continuous total throughput")
    assert_round(cag["online_vs_static_pct_compact_output_bytes"]["mean"], -1.9, 1, "continuous compact bytes")
    assert_round(cag["online_vs_static_pct_compact_output_bytes_per_write"]["mean"], -11.9, 1, "continuous bytes/write")
    assert_round(cag["mean_high_overlap_after_warmup"]["mean"], 3.00, 2, "continuous overlap")

    ablation = read_json("remote-results/paper_tables/ablation_score_modes.json")["ablation"]
    modes = {row["mode"]: row for row in ablation}
    assert_equal(set(modes), {"demand", "pressure", "hybrid"}, "score modes")
    assert_round(modes["pressure"]["overlap"]["mean"], 1.86, 2, "pressure overlap")
    assert_round(modes["pressure"]["max_lag"]["mean"], 5.0, 1, "pressure max lag")
    assert_round(modes["hybrid"]["overlap"]["mean"], 2.29, 2, "hybrid overlap")
    assert_round(modes["hybrid"]["max_lag"]["mean"], 2.0, 1, "hybrid max lag")

    coef = read_json("remote-results/paper_tables/coefficient_ablation.json")
    assert_equal(len(coef["labels"]), 5, "coefficient ablation label count")
    for row in coef["labels"]:
        stats = row["stats"]
        assert_equal(row["n"], 3, f"{row['label']} seed count")
        assert_equal(stats["max_failed_static"], 0, f"{row['label']} failed static")
        assert_equal(stats["max_failed_online"], 0, f"{row['label']} failed online")

    wm = read_json("remote-results/paper_tables/workload_matrix_aggregate.json")
    assert_equal([v["name"] for v in wm["variants"]], ["wm_value4k", "wm_readheavy", "wm_driftfast", "wm_8t_2high"], "workload variants")
    for v in wm["variants"]:
        assert_equal(v["n"], 5, f"{v['name']} n")
        assert_equal(v["indicators"]["max_failed_static"], 0, f"{v['name']} failed static")
        assert_equal(v["indicators"]["max_failed_online"], 0, f"{v['name']} failed online")

    wl = read_json("remote-results/paper_tables/window_length_sweep.json")
    for window in ("10", "20", "40"):
        assert_equal(wl["windows"][window]["n"], 3, f"window {window} n")
        ind = wl["windows"][window]["indicators"]
        assert_equal(ind["max_failed_static"], 0, f"window {window} failed static")
        assert_equal(ind["max_failed_online"], 0, f"window {window} failed online")
    assert_round(wl["windows"]["20"]["stats"]["high_p99"]["mean"], -26.9, 1, "window 20 high P99")

    cgroup = read_json("remote-results/cgroup_aligned_aggregate.json")
    cgo = cgroup["aggregates"]["cgroup_online"]["vs_cgroup_equal"]
    assert_round(cgo["pct_high_sum_ops_per_sec"]["mean"], 12.5, 1, "cgroup online high throughput")
    assert_round(cgo["pct_high_mean_write_p99_us"]["mean"], -4.4, 1, "cgroup online high P99")
    assert_round(cgo["pct_sum_compact_write_bytes"]["mean"], 3.1, 1, "cgroup online compact bytes")

    rl = read_json("remote-results/paper_tables/rate_limiter_coverage.json")
    assert_equal([row["budget_mbps"] for row in rl["sweep"]], [1.0, 3.0, 6.0, 11.0], "rate limiter budgets")
    for row in rl["sweep"]:
        agg = row["aggregate"]
        assert_equal(agg["failed_tenants_total"], 0, f"rlcov {row['budget_mbps']} failed tenants")
        cov = float(agg["coverage_ratio_mean"])
        if not 0.98 <= cov <= 1.03:
            fail(f"rlcov {row['budget_mbps']} coverage ratio {cov} outside expected range")

    longrun = read_json("remote-results/paper_tables/longer_run_confirmation.json")
    assert_equal(longrun["failed_tenants"], 0, "longer-run failed tenants")
    assert_round(longrun["online_vs_static"]["high_p99_pct"], -13.9, 1, "longer-run high P99")
    assert_round(longrun["online_vs_static"]["high_write_tput_pct"], 11.6, 1, "longer-run high throughput")

    public_trace = read_json("remote-results/paper_tables/public_trace_qualification.json")
    assert_equal(public_trace["verdict"], "No current public trace result should be promoted to production-trace validation.", "public trace verdict")
    assert_equal(len(public_trace["rows"]), 2, "public trace row count")
    assert_equal(sum(int(r["stress_pass_segments"]) for r in public_trace["rows"]), 0, "public trace stress-pass total")


def check_figures() -> None:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run([sys.executable, "figures/evaluation/create_saki_figures.py"], cwd=ROOT, check=True, env=env)
    for path in [
        "figures/data/fig3_continuous_summary.csv",
        "figures/data/fig4_score_ablation.csv",
        "figures/data/fig8_collateral_summary.csv",
        "figures/evaluation/fig3_continuous_summary.pdf",
        "figures/evaluation/fig4_score_ablation.pdf",
        "figures/evaluation/fig8_collateral_summary.pdf",
    ]:
        p = require(path)
        if p.stat().st_size <= 0:
            fail(f"empty generated artifact: {path}")
    with require("figures/data/fig3_continuous_summary.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert_equal([r["metric"] for r in rows], ["High P99", "High P999", "High tput", "Total tput", "Bytes/write"], "fig3 metrics")


def check_python_compiles() -> None:
    py_files = sorted((ROOT / "remote").glob("*.py"))
    py_files.append(ROOT / "figures/evaluation/create_saki_figures.py")
    py_files.append(ROOT / "tools/verify_artifact.py")
    for path in py_files:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path.relative_to(ROOT)), "exec")


def check_sanitized() -> None:
    bad_substrings = [
        "/home/" + "tianqc",
        "10." + "181.8.145",
        "/tmp/" + "PVLDB2027-SAKI",
        "/private/tmp/" + "PVLDB2027-SAKI",
        "kv_compaction_" + "debt_exp",
        "claude" + ".md",
    ]
    text_suffixes = {".c", ".cc", ".csv", ".json", ".md", ".py", ".tex", ".bib", ".cls", ".bst", ".txt"}
    self_path = Path(__file__).resolve()
    for path in ROOT.rglob("*"):
        if path.is_dir():
            if path == ROOT / ".git":
                continue
            if ".git" in path.parts:
                continue
            if path.name == "__pycache__":
                fail(f"unexpected directory in release: {path.relative_to(ROOT)}")
            continue
        if path.resolve() == self_path:
            continue
        if path.name.endswith(".pyc") or ".bak_" in path.name:
            fail(f"unexpected generated/backup file: {path.relative_to(ROOT)}")
        if path.suffix not in text_suffixes and path.name not in {"LICENSE", "README.md", "MANIFEST.md", "requirements.txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for needle in bad_substrings:
            if needle in text:
                fail(f"sensitive/private string {needle!r} found in {path.relative_to(ROOT)}")


def main() -> int:
    check_required_paths()
    check_raw_coverage()
    check_headline_values()
    check_python_compiles()
    check_figures()
    check_sanitized()
    print("OK: SAKI artifact data, scripts, figures, and sanitization checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
