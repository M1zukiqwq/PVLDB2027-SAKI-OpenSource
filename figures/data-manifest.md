# Figure Data Manifest

## Available Real Data

- `remote-results/paper_tables/paper_tables.md`: summary tables for main results, fairness, ablation, stronger baseline, longer-run confirmation.
- `remote-results/paper_tables/realistic_big_a_aggregate.md`: repeated epoch-level aggregate.
- `remote-results/paper_tables/*.json`: machine-readable summaries for continuous, fairness, ablation, and longer-run results.

## Generated Figures

| Figure | Data file | Real/mock | Source | Script | Outputs |
|---|---|---|---|---|---|
| Figure: Continuous repeated aggregate | `figures/data/fig3_continuous_summary.csv` | Real | `remote-results/paper_tables/main_continuous_demand2f.json` | `figures/evaluation/create_saki_figures.py` | `figures/evaluation/fig3_continuous_summary.{pdf,png,svg}` |
| Figure: Score-mode ablation | `figures/data/fig4_score_ablation.csv` | Real | `remote-results/paper_tables/ablation_score_modes.json` | `figures/evaluation/create_saki_figures.py` | `figures/evaluation/fig4_score_ablation.{pdf,png,svg}` |
| Figure: Collateral summary | `figures/data/fig8_collateral_summary.csv` | Real | `fairness_continuous_demand2f.json`, `realistic_big_a_aggregate.json` | `figures/evaluation/create_saki_figures.py` | `figures/evaluation/fig8_collateral_summary.{pdf,png,svg}` |

## Boundary

No mock figure data is used for manuscript claims. If a planning figure is needed later, its file must start with `mock_` or `synthetic_` and the manuscript text must not present it as a real result.

The public artifact keeps only figures included by the manuscript. Additional
exploratory or superseded figures are omitted from this submission repository.
