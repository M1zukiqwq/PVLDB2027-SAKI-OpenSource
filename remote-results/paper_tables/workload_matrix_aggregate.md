# Workload Matrix Aggregate

One-axis-at-a-time perturbations around the continuous main result
`embedded_demand2f_16t`. Same paper Saki policy (`online` with
`--online-score-mode demand`). Same per-tenant budgets; tenant_count
scaling preserves the static fair-share aggregate `tenant_count * mid_budget`.

| variant | n | High P99 | High P999 | High tput | Total tput | Bytes/write | Overlap | failed (s/o) | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|:--|
| wm_value4k | 5 | -28.6% +/-16.2 | -7.1% +/-20.6 | +20.1% +/-4.7 | +0.6% +/-1.5 | -9.0% +/-3.0 | 3.00/4 | 0/0 | ci-strict-win |
| wm_readheavy | 5 | -15.4% +/-17.5 | -38.4% +/-27.6 | +30.4% +/-7.6 | -0.2% +/-4.0 | -10.2% +/-5.8 | 3.00/4 | 0/0 | ci-strict-partial |
| wm_driftfast | 5 | -17.6% +/-10.1 | -1.1% +/-46.6 | +20.5% +/-10.3 | +4.4% +/-3.4 | -3.3% +/-4.4 | 2.57/4 | 0/0 | ci-strict-win |
| wm_8t_2high | 5 | -42.2% +/-12.7 | -52.6% +/-16.9 | +28.7% +/-5.8 | +7.0% +/-1.9 | -11.3% +/-3.4 | 1.57/2 | 0/0 | ci-strict-win |

Statistical rendering (pre-committed; do not relax for matrix rows):
- n=2 cells show `mean [min, max]`. Direction is the only claim.
- n>=3 cells show `mean +/-CI` (Student-t 95% half-width).

Promotion policy: every variant is promoted to n=5 except those flagged
`capacity-boundary`, so boundary outcomes carry the same statistical
weight as wins. This avoids selectively pricing rigor only for variants
that already look favorable in screening.

Verdict labels (describe the actual aggregated outcome, not a promotion decision):
- `capacity-boundary`: any failed tenants in either policy. Workload exceeds the same-budget contract; not promoted to n=5.
- `controller-boundary`: overlap < 2.5/4. Controller could not track the high set in this workload.
- `ci-strict-win`: n>=3, high P99 95% CI upper bound < 0 AND high tput 95% CI lower bound > 0.
- `ci-strict-partial`: n>=3, exactly one of high P99 / high tput is CI-strict in the expected direction.
- `ci-crosses-zero`: n>=3, neither high P99 nor high tput CI is separated from zero.
- `directional-positive`: n<=2, mean high P99 <= -2% AND mean high tput >= +5%.
- `directional-mixed`: n<=2 otherwise. Used only during screening; promoted to n=5 afterwards.
