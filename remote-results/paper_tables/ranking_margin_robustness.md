# Ranking-Margin Robustness

Read-only replay over the published continuous main online raw files.
Each row reconstructs a post-warmup control decision from the previous
window's observations; no RocksDB run is launched by this analysis.

## Summary

- Trials: 5
- Post-warmup control decisions: 35
- Reference replay matches recorded online high-budget set: 35/35
- Reference top-H true-high overlap: 3.00/4
- Reference boundary margin: mean 5.30, min 0.39, max 8.24

## Counterfactual Ranking Family

| score instance | exact top-H vs reference | mean Jaccard vs reference | mean true-high overlap |
|---|---:|---:|---:|
| demand only (6D) | 35/35 | 1.00 [1.00,1.00] | 3.00/4 |
| anchor x0.5 | 31/35 | 0.95 [0.33,1.00] | 2.86/4 |
| anchor x2 | 35/35 | 1.00 [1.00,1.00] | 3.00/4 |
| residual x0.5 | 35/35 | 1.00 [1.00,1.00] | 3.00/4 |
| residual x2 | 31/35 | 0.95 [0.33,1.00] | 2.86/4 |
| pressure-only negative control | 10/35 | 0.61 [0.14,1.00] | 2.03/4 |

Interpretation: the demand term alone reproduces the reference top-H
assignment on all replayed decisions. Moderate coefficient perturbations
that preserve demand dominance either leave the assignment unchanged or
retain high Jaccard with the reference. The pressure-only negative control
is much less aligned, matching the runtime score-mode ablation.
