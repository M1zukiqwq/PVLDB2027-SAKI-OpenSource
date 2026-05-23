# Release Manifest

## Included Claim Data

The `remote-results/` directory follows the paths expected by the checked-in
aggregation and figure scripts. Summary artifacts live under
`remote-results/paper_tables/`; selected raw trial files live directly under
`remote-results/`.

Raw result families included:

| Family | File pattern | Count |
|---|---|---:|
| Continuous main, ablation, longer run | `embedded_continuous_*embedded_demand2f_16t*.json` | 36 |
| Score-family coefficient ablation | `embedded_continuous_*coef_ablation*.json` | 45 |
| Continuous workload matrix | `embedded_continuous_*wm_*.json` | 60 |
| Window-length sweep | `embedded_continuous_*wl*.json` | 27 |
| Rate-limiter coverage microstudy | `embedded_continuous_static_rlcov*.json` | 14 |
| Epoch main and stronger baselines | `realistic_*realistic_big*.json` | 40 |
| Epoch sensitivity coverage | `realistic_*sens_*.json` | 52 |
| Aligned cgroup-v2 baseline | `cgroup_smoke_*realistic_cgroup_aligned*.json` | 35 |

The excluded continuous file is the old `embedded_continuous_adaptive_*`
diagnostic, which is not a paper claim source.

## Claim-To-Artifact Map

| Paper result | Summary artifact | Raw family |
|---|---|---|
| Epoch-Drift16 main result | `remote-results/paper_tables/realistic_big_a_aggregate.{json,md}` | `realistic_*realistic_big*.json` |
| Runtime-Drift16 main result | `remote-results/paper_tables/main_continuous_demand2f.json` | `embedded_continuous_*embedded_demand2f_16t*.json` |
| Frozen-skew baseline | `remote-results/paper_tables/realistic_big_static_biased_aggregate.{json,md}` | `realistic_*realistic_big*.json` |
| RocksDB static-autotuned baseline | `remote-results/paper_tables/realistic_big_builtin_aggregate.{json,md}` | `realistic_*realistic_big*.json` |
| Score-mode ablation | `remote-results/paper_tables/ablation_score_modes.json` | `embedded_continuous_*embedded_demand2f_16t*.json` |
| Ranking-margin replay | `remote-results/paper_tables/ranking_margin_robustness.{json,md}` | continuous online raw files |
| Coefficient ablation | `remote-results/paper_tables/coefficient_ablation.{json,md}` | `embedded_continuous_*coef_ablation*.json` |
| cgroup-v2 baseline | `remote-results/cgroup_aligned_aggregate.{json,md}` | `cgroup_smoke_*realistic_cgroup_aligned*.json` |
| Epoch sensitivity | `remote-results/paper_tables/sensitivity_aggregate.{json,md}` | `realistic_*sens_*.json` |
| Workload matrix | `remote-results/paper_tables/workload_matrix_aggregate.{json,md}` | `embedded_continuous_*wm_*.json` |
| Window-length sweep | `remote-results/paper_tables/window_length_sweep.{json,md}` | `embedded_continuous_*wl*.json` |
| Rate-limiter coverage | `remote-results/paper_tables/rate_limiter_coverage.{json,md}` | `embedded_continuous_static_rlcov*.json` |
| Collateral accounting | `fairness_continuous_demand2f.json`, `continuous_read_cpu_collateral.{json,md}`, `realistic_big_a_aggregate.{json,md}` | continuous and epoch main raw files |
| Public trace qualification | `public_trace_qualification.{json,md}`, `trace_family_audit/`, `baleen_static_smoke/`, `cachelib_external_v1b/` | compact audit summaries |

## Omitted Material

- Host logs and database directories.
- Compiled binaries and shared libraries.
- Backup files, bytecode caches, and editor/build intermediates.
- Private remote-host notes and credentials.
- Large external trace corpora; only derived audit summaries are included.
