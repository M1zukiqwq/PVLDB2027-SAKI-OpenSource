# Window-Length Sensitivity Sweep

SAKI control-window sensitivity around the continuous main anchor
`embedded_demand2f_16t`. Holds every other parameter (16 tenants,
duration=160s, 4/8/4 split, per-tenant budgets 11/6/1 MB/s, demand2f
offered load, drift_tenants=8, value_size=1024, num_keys=80000) fixed.
Same paper SAKI policy (`online --online-score-mode demand
--online-budget-mode fixed`). The same-budget contract is preserved
(aggregate = 96 MB/s); we are sweeping the control window, not the
aggregate I/O.

Per-row n is the number of seeds (a, b, c) that completed both static
and online for that `window_sec`. Comparisons are online-vs-static
within each trial; the table reports mean +/-CI (Student-t 95%) when
n>=3 and `mean [min, max]` otherwise.

## Online vs static, high tier

| window_sec | n | windows/run | High P99 | High P999 | High tput | Total tput | Bytes/write | Overlap | max lag (windows) | failed (s/o) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 10 | 3 | 16 | +6.8% +/-39.2 | +18.2% +/-87.0 | +23.4% +/-58.1 | +0.5% +/-28.8 | -8.0% +/-26.6 | 3.47/4 | 0.00 (max 0) | 0/0 |
| 20 | 3 | 8 | -26.9% +/-14.0 | -58.6% +/-19.7 | +16.1% +/-8.7 | +3.0% +/-0.8 | -9.1% +/-19.6 | 3.00/4 | 0.00 (max 0) | 0/0 |
| 40 | 3 | 4 | -14.4% +/-44.8 | -34.9% +/-18.0 | +14.5% +/-19.8 | +6.4% +/-8.8 | -9.4% +/-10.3 | 2.00/4 | unbounded | 0/0 |

## LOW-tier collateral (online vs static)

Computed directly from raw per-tenant window_records filtered to
`true_tier == 'low'`. Negative LOW throughput change is the expected
cost of moving budget toward HIGH; large positive LOW P99/P999 changes
would indicate collateral tail pain.

| window_sec | n | LOW P99 | LOW P999 | LOW write tput | LOW total tput |
|---:|---:|---:|---:|---:|---:|
| 10 | 3 | +110.5% +/-367.8 | -38.5% +/-37.9 | -8.5% +/-32.1 | -7.9% +/-34.8 |
| 20 | 3 | +24.6% +/-63.6 | -18.5% +/-197.8 | -2.1% +/-8.6 | -2.3% +/-5.0 |
| 40 | 3 | -36.3% +/-84.2 | +587.6% +/-1665.6 | +5.3% +/-3.5 | +5.0% +/-3.9 |

## Per-trial details

### window_sec = 10

| trial | High P99 % | High P999 % | High tput % | Total tput % | Bytes/write % | Overlap | max lag | failed (s/o) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| wl10_a | +24.8% | -2.1% | +48.4% | +11.4% | +3.2% | 3.47/4 | 0 | 0/0 |
| wl10_b | +0.3% | -2.0% | +19.7% | +1.7% | -9.0% | 3.47/4 | 0 | 0/0 |
| wl10_c | -4.7% | +58.6% | +2.1% | -11.7% | -18.2% | 3.47/4 | 0 | 0/0 |

### window_sec = 20

| trial | High P99 % | High P999 % | High tput % | Total tput % | Bytes/write % | Overlap | max lag | failed (s/o) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| wl20_a | -22.4% | -61.5% | +19.3% | +3.0% | -16.8% | 3.00/4 | 0 | 0/0 |
| wl20_b | -25.0% | -49.7% | +16.6% | +3.3% | -9.3% | 3.00/4 | 0 | 0/0 |
| wl20_c | -33.2% | -64.7% | +12.3% | +2.7% | -1.1% | 3.00/4 | 0 | 0/0 |

### window_sec = 40

| trial | High P99 % | High P999 % | High tput % | Total tput % | Bytes/write % | Overlap | max lag | failed (s/o) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| wl40_a | -23.5% | -39.3% | +22.4% | +9.9% | -13.5% | 2.00/4 | unbounded | 0/0 |
| wl40_b | +6.4% | -26.6% | +6.4% | +2.8% | -5.2% | 2.00/4 | unbounded | 0/0 |
| wl40_c | -26.0% | -38.9% | +14.7% | +6.5% | -9.4% | 2.00/4 | unbounded | 0/0 |

Caveats:
- This is a *window-length sensitivity / stability boundary* sweep,
  not a new main result. The headline main continuous claim
  (window_sec=20, n=5) stands unchanged.
- The fixed-budget contract (per-tenant 11/6/1 MB/s; aggregate 96 MB/s
  under 4/8/4) is preserved across all rows.
- `windows/run` shrinks at larger window_sec (duration_sec=160 fixed).
  This means longer windows have fewer post-warmup samples for the
  overlap statistic and fewer opportunities to observe a phase change,
  so lag is measured in *control windows*, not seconds.
- LOW-tier metrics in the second table are derived here from raw
  `window_records` (true_tier == 'low'); the upstream analyzer does
  not emit a LOW summary today.

## Interpretation

**10 s window -- aggressive but noisy.** High write throughput remains positive (+23.4% +/-58.1), but its CI is wide and High P99 is not a win (+6.8% +/-39.2). Overlap is 3.47/4 and adaptation lag is 0.00 control windows, so the controller tracks promptly but pays for the shorter window with noisy tail latency. LOW P99 is also elevated (+110.5% +/-367.8), which marks this as the aggressive boundary rather than the main operating point.

**20 s window -- the mechanism anchor.** This is the only row whose High-P99 confidence interval is strictly below zero (-26.9% +/-14.0). High throughput remains positive (+16.1% +/-8.7), overlap is 3.00/4, adaptation lag is 0.00 control windows, and no tenants fail. This keeps the window-length bracket aligned with the headline continuous result without making the sensitivity sweep a new tuned main result.

**40 s window -- lag-bounded.** High throughput remains positive on average (+14.5% +/-19.8), but the longer control period leaves overlap at 2.00/4 and adaptation lag is unbounded in all 3 seeds. No tenant fails, so this is a tracking/responsiveness limit rather than a capacity-violation regime.

**Stability boundary, not a new claim.** Together these rows bracket the 20 s anchor: 10 s is more reactive but tail-latency noisy, 40 s under-tracks the drifting high set, and 20 s gives the only CI-strict High-P99 improvement. The fixed-budget claim and the headline main continuous numbers (`embedded_demand2f_16t`, n=5) are unchanged by this sweep.

## Notes on the source-of-truth budget

The actual `embedded_demand2f_16t` main continuous result was run with per-tenant high/mid/low = 11/6/1 MB/s, aggregate `4*11 + 8*6 + 4*1 = 96 MB/s`. This sweep uses the same per-tenant budgets so the fixed-budget contract is preserved row-by-row.

The 11/7/3 MB/s, 112 MB/s contract belongs to the epoch/public-trace family, not to the continuous main. The window-length sweep intentionally does not mix the two anchors.

## Artifacts

- Driver: `remote/run_window_length_sweep.py`
- Aggregator: `remote/aggregate_window_length_sweep.py`
- Summary: `remote-results/paper_tables/window_length_sweep.{json,md}`
- Full regeneration also needs the per-trial raw policy JSONs,
  because LOW-tier collateral is computed from raw `window_records`.
- Aggregated rows report zero failed tenants in every window setting.
