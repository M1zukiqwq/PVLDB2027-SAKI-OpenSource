# Coefficient-Robustness Ablation Aggregate

Anchored at the published continuous main result
`embedded_demand2f_16t` (16 tenants, 4/8/4 split, per-tenant budgets
11/6/1 MB/s = 96 MB/s aggregate, 20s window, 8 windows, demand-mode online).
Only the demand-score coefficients are perturbed; everything else is held.
Each ablation seed is paired with the published paper-static seed of the
matching trial (seed 1 -> demand2f_16t, seed 2 -> _b, seed 3 -> _c), so the
% deltas are per-seed paired with that static.

Cells show `mean [min, max]` across n=3 seeds; we do not quote a 95% CI at n=3
for the ablation (the published main result uses n=5 for that claim).

| label | High P99 % | High tput % | Total tput % | Bytes/write % | Overlap | Max lag (w) | Mean Jaccard vs SAKI |
|---|---:|---:|---:|---:|---:|---:|---:|
| anchor_half | -30.3% [-38.1,-23.6] | +22.4% [+18.9,+27.1] | +5.7% [+2.5,+8.5] | -14.0% [-16.3,-11.4] | 2.90/4 | 1 | 0.94 [0.88,1.00] |
| anchor_double | -27.5% [-42.4,-18.2] | +23.7% [+22.2,+26.2] | +3.3% [+0.8,+4.5] | -12.1% [-17.3,-9.2] | 3.00/4 | 0 | 1.00 [1.00,1.00] |
| residual_half | -30.0% [-39.8,-16.7] | +24.3% [+21.3,+28.2] | +3.8% [+1.2,+5.7] | -14.1% [-17.9,-11.9] | 3.00/4 | 0 | 1.00 [1.00,1.00] |
| residual_double | -35.8% [-49.6,-25.7] | +25.0% [+20.9,+27.1] | +5.8% [+4.9,+6.3] | -13.7% [-17.2,-8.2] | 2.86/4 | 1 | 0.94 [0.89,1.00] |
| anchor_only | -31.9% [-40.2,-26.2] | +21.5% [+12.6,+26.0] | +4.1% [-0.6,+8.3] | -13.6% [-18.5,-6.7] | 3.00/4 | 0 | 1.00 [1.00,1.00] |
| **fixed SAKI (n=5, paper main)*** | -27.0% | +26.1% | +4.3% | -11.9% | 3.00/4 | 0 | 1.00 (self) |

`*` reference row reproduced from `main_continuous_demand2f.json` (n=5).
It is NOT a same-batch rerun; values frozen at paper-main runtime.

Each ablation cell is per-seed paired with the published paper-static seed.
Per-seed raw analysis JSONs are listed in the companion JSON file.
