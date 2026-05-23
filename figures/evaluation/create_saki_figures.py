import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "figures" / "data"
FIG_DIR = ROOT / "figures" / "evaluation"
DATA_DIR.mkdir(parents=True, exist_ok=True)
FIG_DIR.mkdir(parents=True, exist_ok=True)

COLORS = {
    "static": "#0072B2",
    "online": "#D55E00",
    "static_biased": "#E69F00",
    "static_autotuned": "#CC79A7",
    "oracle_tiered": "#009E73",
    "pressure": "#CC79A7",
    "hybrid": "#E69F00",
    "high": "#D55E00",
    "mid": "#0072B2",
    "low": "#999999",
    "good": "#009E73",
    "bad": "#CC79A7",
}

T95 = {
    2: 12.706,
    3: 4.303,
    4: 3.182,
    5: 2.776,
    6: 2.571,
    7: 2.447,
    8: 2.365,
    9: 2.306,
    10: 2.262,
}

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "axes.labelsize": 8,
        "axes.titlesize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "savefig.dpi": 450,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.hashsalt": "saki-pvldb-2027",
    }
)


def save(fig, name):
    metadata = {
        "pdf": {"CreationDate": None, "ModDate": None, "Producer": "Matplotlib"},
        "png": {"Software": "Matplotlib"},
        "svg": {"Date": "2026-05-20T00:00:00"},
    }
    for ext in ("pdf", "png", "svg"):
        path = FIG_DIR / f"{name}.{ext}"
        fig.savefig(path, metadata=metadata[ext])
        if ext == "svg":
            path.write_text(
                "\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()) + "\n",
                encoding="utf-8",
            )
    plt.close(fig)


def ci95_half_width(stat):
    n = int(stat.get("n", 0))
    if n <= 1:
        return 0.0
    t = T95.get(n, 1.96)
    return round(t * float(stat.get("stdev", 0.0)) / math.sqrt(n), 1)


def fig3_continuous_summary():
    payload = json.loads((ROOT / "remote-results/paper_tables/main_continuous_demand2f.json").read_text())["aggregates"]
    rows = [
        (
            "High P99",
            -payload["online_vs_static_pct_high_write_p99_us"]["mean"],
            ci95_half_width(payload["online_vs_static_pct_high_write_p99_us"]),
        ),
        (
            "High P999",
            -payload["online_vs_static_pct_high_write_p999_us"]["mean"],
            ci95_half_width(payload["online_vs_static_pct_high_write_p999_us"]),
        ),
        (
            "High tput",
            payload["online_vs_static_pct_high_write_throughput"]["mean"],
            ci95_half_width(payload["online_vs_static_pct_high_write_throughput"]),
        ),
        (
            "Total tput",
            payload["online_vs_static_pct_total_throughput"]["mean"],
            ci95_half_width(payload["online_vs_static_pct_total_throughput"]),
        ),
        (
            "Bytes/write",
            -payload["online_vs_static_pct_compact_output_bytes_per_write"]["mean"],
            ci95_half_width(payload["online_vs_static_pct_compact_output_bytes_per_write"]),
        ),
    ]
    with (DATA_DIR / "fig3_continuous_summary.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["metric", "improvement_pct", "ci95_half_width_pp"])
        writer.writerows(rows)
    fig, ax = plt.subplots(figsize=(3.45, 2.35))
    y = list(range(len(rows)))
    vals = [r[1] for r in rows]
    errs = [r[2] for r in rows]
    colors = [COLORS["good"] if v >= 0 else COLORS["bad"] for v in vals]
    ax.barh(y, vals, xerr=errs, color=colors, alpha=0.85, capsize=2)
    ax.axvline(0, color="0.25", lw=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in rows])
    ax.invert_yaxis()
    ax.set_xlabel("Improvement over static (%)")
    ax.set_title("Continuous repeated aggregate")
    save(fig, "fig3_continuous_summary")


def fig4_score_ablation():
    payload = json.loads((ROOT / "remote-results/paper_tables/ablation_score_modes.json").read_text())["ablation"]
    metrics = ["High P99", "High P999", "High write tput", "Bytes/write"]
    modes = ["demand", "pressure", "hybrid"]
    rows = []
    for item in payload:
        for metric in metrics:
            mean = item[metric]["mean"]
            if metric in {"High P99", "High P999", "Bytes/write"}:
                mean = -mean
            rows.append({"mode": item["mode"], "metric": metric, "mean": mean})
        rows.append({"mode": item["mode"], "metric": "Overlap", "mean": item["overlap"]["mean"]})
        rows.append({"mode": item["mode"], "metric": "Max lag", "mean": item["max_lag"]["mean"]})
    with (DATA_DIR / "fig4_score_ablation.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["mode", "metric", "mean"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.25), constrained_layout=True)
    x = list(range(len(modes)))
    width = 0.18
    metric_colors = {
        "High P99": "#0072B2",
        "High P999": "#009E73",
        "High write tput": "#E69F00",
        "Bytes/write": "#CC79A7",
    }
    for j, metric in enumerate(metrics):
        vals = [next(r["mean"] for r in rows if r["mode"] == mode and r["metric"] == metric) for mode in modes]
        axes[0].bar(
            [i + (j - 1.5) * width for i in x],
            vals,
            width=width,
            label=metric,
            color=metric_colors[metric],
            alpha=0.85,
        )
    axes[0].axhline(0, color="0.35", lw=0.8)
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(["Demand", "Pressure", "Hybrid"])
    axes[0].set_ylabel("Improvement over static (%)")
    axes[0].set_title("(a) Performance (higher is better)")
    axes[0].legend(frameon=False, ncols=2, fontsize=6)
    for j, metric in enumerate(("Overlap", "Max lag")):
        vals = [next(r["mean"] for r in rows if r["mode"] == mode and r["metric"] == metric) for mode in modes]
        axes[1].bar([i + (j - 0.5) * 0.28 for i in x], vals, width=0.28, label=metric, color=["#009E73", "#CC79A7"][j])
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(["Demand", "Pressure", "Hybrid"])
    axes[1].set_ylabel("Windows or tenants")
    axes[1].set_title("(b) Adaptation checks")
    axes[1].legend(frameon=False)
    save(fig, "fig4_score_ablation")


def fig8_collateral_summary():
    continuous = json.loads((ROOT / "remote-results/paper_tables/fairness_continuous_demand2f.json").read_text())[
        "aggregate"
    ]
    epoch = json.loads((ROOT / "remote-results/paper_tables/realistic_big_a_aggregate.json").read_text())[
        "aggregates"
    ]["online_vs_static_compare"]
    epoch_keys = {
        "high": (
            "online_vs_static_pct_high_mean_write_p99_us",
            "online_vs_static_pct_high_sum_ops_per_sec",
        ),
        "mid": (
            "online_vs_static_pct_mid_mean_write_p99_us",
            "online_vs_static_pct_mid_sum_ops_per_sec",
        ),
        "low": (
            "online_vs_static_pct_low_mean_write_p99_us",
            "online_vs_static_pct_low_sum_ops_per_sec",
        ),
    }
    rows = []
    for tier in ("high", "mid", "low"):
        epoch_p99_key, epoch_tput_key = epoch_keys[tier]
        rows.extend(
            [
                {
                    "setting": "Continuous",
                    "tier": tier.upper(),
                    "metric": "P99",
                    "improvement_pct": -continuous[tier]["write_p99_pct"]["mean"],
                },
                {
                    "setting": "Continuous",
                    "tier": tier.upper(),
                    "metric": "P999",
                    "improvement_pct": -continuous[tier]["write_p999_pct"]["mean"],
                },
                {
                    "setting": "Continuous",
                    "tier": tier.upper(),
                    "metric": "Write tput",
                    "improvement_pct": continuous[tier]["write_throughput_pct"]["mean"],
                },
            ]
        )
        rows.extend(
            [
                {
                    "setting": "Epoch",
                    "tier": tier.upper(),
                    "metric": "P99",
                    "improvement_pct": -epoch[epoch_p99_key]["mean"],
                },
                {
                    "setting": "Epoch",
                    "tier": tier.upper(),
                    "metric": "Write tput",
                    "improvement_pct": epoch[epoch_tput_key]["mean"],
                },
            ]
        )

    with (DATA_DIR / "fig8_collateral_summary.csv").open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["setting", "tier", "metric", "improvement_pct"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(7.0, 2.2),
        constrained_layout=True,
        gridspec_kw={"width_ratios": [1.25, 0.92]},
    )
    tier_order = ["HIGH", "MID", "LOW"]
    panels = [
        ("Continuous", ["P99", "P999", "Write tput"], axes[0]),
        ("Epoch", ["P99", "Write tput"], axes[1]),
    ]
    cmap = plt.get_cmap("BrBG")
    vmin, vmax = -50, 50

    for title, metrics, ax in panels:
        matrix = []
        for tier in tier_order:
            matrix.append(
                [
                    next(
                        r["improvement_pct"]
                        for r in rows
                        if r["setting"] == title and r["tier"] == tier and r["metric"] == metric
                    )
                    for metric in metrics
                ]
            )
        image = ax.imshow(matrix, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
        ax.set_title(title)
        ax.set_xticks(range(len(metrics)))
        ax.set_xticklabels(metrics)
        ax.set_yticks(range(len(tier_order)))
        ax.set_yticklabels(tier_order)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_xticks([x - 0.5 for x in range(1, len(metrics))], minor=True)
        ax.set_yticks([y - 0.5 for y in range(1, len(tier_order))], minor=True)
        ax.grid(which="minor", color="white", linewidth=1.2)
        for y, tier in enumerate(tier_order):
            for x, metric in enumerate(metrics):
                val = matrix[y][x]
                text_color = "white" if abs(val) >= 28 else "black"
                ax.text(x, y, f"{val:+.1f}%", ha="center", va="center", color=text_color, fontsize=7)

    cbar = fig.colorbar(image, ax=axes, orientation="horizontal", fraction=0.08, pad=0.10)
    cbar.set_label("Improvement over static (%)")
    save(fig, "fig8_collateral_summary")


if __name__ == "__main__":
    fig3_continuous_summary()
    fig4_score_ablation()
    fig8_collateral_summary()
