# CacheLib External-Validity n=5 Aggregate

- tag: cachelib_v1b_fixhot_t4_n5
- schedule: <remote-root>/results/cachelib_trace_qualification/selected_segments.json
- policies: static, static_biased, online
- engine-stress gates: >= 60.0 MB/s compact OR >= 32.0 MiB pending p95
- segments: [3501, 6858, 7475, 13360, 20695]

## Headline verdict: **smoke_supplement_only_n_stress=0**


## Per-segment engine-stress

| segment | engine_stress_pass | policy | compact MB/s | pending p95 MiB | offered MB/s | completed MB/s | completion | limiter MB/s | write amp | L0 p95 | overlap | max_lag |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 3501 | no | static | 29.61 | 23.68 | 110.42 | 6.73 | 0.061 | 37.20 | 4.40 | 0.0 | 0.00 | 6 |
| 3501 | no | static_biased | 29.41 | 22.99 | 110.42 | 6.94 | 0.063 | 37.66 | 4.24 | 0.0 | 2.43 | 6 |
| 3501 | no | online | 30.57 | 23.12 | 110.42 | 6.87 | 0.062 | 37.39 | 4.45 | 0.0 | 2.57 | 6 |
| 6858 | no | static | 29.12 | 24.97 | 129.59 | 6.61 | 0.051 | 36.89 | 4.40 | 0.0 | 0.00 | 6 |
| 6858 | no | static_biased | 30.06 | 25.35 | 129.59 | 6.77 | 0.052 | 37.72 | 4.44 | 0.0 | 2.14 | 6 |
| 6858 | no | online | 29.47 | 25.10 | 129.59 | 7.06 | 0.054 | 37.98 | 4.17 | 0.0 | 2.86 | 1 |
| 7475 | no | static | 29.15 | 24.35 | 118.66 | 6.41 | 0.054 | 36.94 | 4.55 | 0.0 | 0.00 | 6 |
| 7475 | no | static_biased | 31.05 | 23.53 | 118.66 | 6.85 | 0.058 | 37.66 | 4.53 | 0.0 | 2.29 | 6 |
| 7475 | no | online | 30.64 | 24.95 | 118.66 | 6.71 | 0.057 | 37.46 | 4.56 | 0.0 | 2.71 | 3 |
| 13360 | no | static | 28.72 | 25.06 | 127.63 | 7.00 | 0.055 | 37.32 | 4.10 | 0.0 | 0.00 | 6 |
| 13360 | no | static_biased | 30.60 | 24.34 | 127.63 | 7.21 | 0.056 | 37.67 | 4.24 | 0.0 | 2.57 | 3 |
| 13360 | no | online | 29.31 | 24.26 | 127.63 | 7.35 | 0.058 | 37.10 | 3.99 | 0.0 | 3.29 | 1 |
| 20695 | no | static | 28.72 | 23.91 | 142.78 | 6.84 | 0.048 | 36.88 | 4.20 | 0.0 | 0.00 | 6 |
| 20695 | no | static_biased | 30.95 | 23.55 | 142.78 | 7.25 | 0.051 | 38.04 | 4.27 | 0.0 | 2.29 | 6 |
| 20695 | no | online | 29.05 | 23.58 | 142.78 | 6.91 | 0.048 | 37.16 | 4.21 | 0.0 | 2.29 | 1 |

## All-selected n=5

| metric | static n | static mean | sb n | sb mean | online n | online mean | online vs sb pct (CI 95) |
|---|---:|---:|---:|---:|---:|---:|---|
| high_p99_us | 5 | 35183.68571428572 | 5 | 30319.728571428575 | 5 | 29721.192857142858 | -1.85% [-15.04, +11.34] (n=5) |
| high_p999_us | 5 | 904906.5071428571 | 5 | 979322.9571428571 | 5 | 1071670.8857142858 | +10.65% [-6.07, +27.37] (n=5) |
| high_write_ops_total | 5 | 394996.6 | 5 | 427711.2 | 5 | 430824.4 | +0.88% [-8.66, +10.42] (n=5) |
| total_write_ops_mean_per_window | 5 | 131256.7142857143 | 5 | 136838.74285714288 | 5 | 136324.57142857142 | -0.34% [-4.67, +4.00] (n=5) |
| low_p99_us | 5 | 145461.82857142857 | 5 | 201203.47857142857 | 5 | 127721.0142857143 | -30.06% [-87.57, +27.45] (n=5) |
| mean_compact_output_mbps | 5 | 29.06467852 | 5 | 30.41303043857143 | 5 | 29.80993735714286 | -1.93% [-6.64, +2.78] (n=5) |
| max_pending_p95_mib | 5 | 24.395279932022095 | 5 | 23.952130842208863 | 5 | 24.20149850845337 | +1.08% [-2.42, +4.59] (n=5) |
| bytes_per_write | 5 | 4433.4112555231895 | 5 | 4447.533134060448 | 5 | 4379.360546710319 | -1.52% [-7.33, +4.28] (n=5) |
| offered_write_mbps | 5 | 125.81374581028572 | 5 | 125.81374581028572 | 5 | 125.81374581028572 | +0.00% [+0.00, +0.00] (n=5) |
| completed_write_mbps | 5 | 6.720343771428571 | 5 | 7.006143634285715 | 5 | 6.979818057142857 | -0.34% [-4.67, +4.00] (n=5) |
| completion_ratio | 5 | 0.05376358506424008 | 5 | 0.05603634976182888 | 5 | 0.055846238838289056 | -0.34% [-4.67, +4.00] (n=5) |
| rate_limiter_actual_mbps | 5 | 37.046357685714284 | 5 | 37.75057579571428 | 5 | 37.41808083857143 | -0.88% [-2.28, +0.52] (n=5) |
| compaction_write_amp | 5 | 4.329503179221865 | 5 | 4.343294076230906 | 5 | 4.276719283896796 | -1.52% [-7.33, +4.28] (n=5) |
| pending_compaction_bytes_mean_mib | 5 | 12.474290362426213 | 5 | 12.063660417284286 | 5 | 12.497700314862389 | +3.85% [-0.82, +8.52] (n=5) |
| pending_compaction_bytes_p95_mib | 5 | 22.919634180068968 | 5 | 22.72268785476684 | 5 | 22.877637672424306 | +0.65% [-0.66, +1.96] (n=5) |
| pending_compaction_bytes_max_mib | 5 | 25.519073486328125 | 5 | 24.616330337524413 | 5 | 24.649909591674806 | +0.24% [-3.92, +4.39] (n=5) |
| l0_files_mean | 5 | 0.0 | 5 | 0.0 | 5 | 0.0 | — |
| l0_files_p95 | 5 | 0.0 | 5 | 0.0 | 5 | 0.0 | — |
| l0_files_max | 5 | 0.0 | 5 | 0.0 | 5 | 0.0 | — |
| overlap_after_warmup_mean | 5 | 0.0 | 5 | 2.3428571428571425 | 5 | 2.742857142857143 | +17.15% [-0.39, +34.69] (n=5) |
| max_lag_after_warmup | 5 | 6.0 | 5 | 5.4 | 5 | 2.4 | -56.67% [-99.57, -13.76] (n=5) |

## Stress-pass subset

| metric | static n | static mean | sb n | sb mean | online n | online mean | online vs sb pct (CI 95) |
|---|---:|---:|---:|---:|---:|---:|---|
| high_p99_us | 0 | nan | 0 | nan | 0 | nan | — |
| high_p999_us | 0 | nan | 0 | nan | 0 | nan | — |
| high_write_ops_total | 0 | nan | 0 | nan | 0 | nan | — |
| total_write_ops_mean_per_window | 0 | nan | 0 | nan | 0 | nan | — |
| low_p99_us | 0 | nan | 0 | nan | 0 | nan | — |
| mean_compact_output_mbps | 0 | nan | 0 | nan | 0 | nan | — |
| max_pending_p95_mib | 0 | nan | 0 | nan | 0 | nan | — |
| bytes_per_write | 0 | nan | 0 | nan | 0 | nan | — |
| offered_write_mbps | 0 | nan | 0 | nan | 0 | nan | — |
| completed_write_mbps | 0 | nan | 0 | nan | 0 | nan | — |
| completion_ratio | 0 | nan | 0 | nan | 0 | nan | — |
| rate_limiter_actual_mbps | 0 | nan | 0 | nan | 0 | nan | — |
| compaction_write_amp | 0 | nan | 0 | nan | 0 | nan | — |
| pending_compaction_bytes_mean_mib | 0 | nan | 0 | nan | 0 | nan | — |
| pending_compaction_bytes_p95_mib | 0 | nan | 0 | nan | 0 | nan | — |
| pending_compaction_bytes_max_mib | 0 | nan | 0 | nan | 0 | nan | — |
| l0_files_mean | 0 | nan | 0 | nan | 0 | nan | — |
| l0_files_p95 | 0 | nan | 0 | nan | 0 | nan | — |
| l0_files_max | 0 | nan | 0 | nan | 0 | nan | — |
| overlap_after_warmup_mean | 0 | nan | 0 | nan | 0 | nan | — |
| max_lag_after_warmup | 0 | nan | 0 | nan | 0 | nan | — |

## Acceptance Gate (plan §Acceptance Gate, vs static_biased on stress-pass subset)

- acceptance_pass: **False**
- high_p999_no_strict_regression: True
- total_tput_no_strict_regression_3pct: True
- bytes_per_write_no_strict_regression: True
- benefit_at_least_one: False
- adaptation_overlap_improves: False
