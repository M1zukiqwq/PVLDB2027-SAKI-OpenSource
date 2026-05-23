# aligned cgroup baseline aggregate (n=5)

Trials: realistic_cgroup_aligned_a, realistic_cgroup_aligned_b, realistic_cgroup_aligned_c, realistic_cgroup_aligned_d, realistic_cgroup_aligned_e

Workload: 16 tenants, 8 epochs, num-keys=240000, writes-high=100000, high-count=4, low-count=4 (matches `realistic_big_*` scale).

All numbers are aggregate-level (sum/mean across tenants per trial) 
then mean/CI across trials. Negative P99 / compaction-bytes deltas are improvements.

## Aggregate vs `rocksdb_static`

| Policy | Failed (total) | High P99 mean | High P99 95%CI | High tput mean | High tput 95%CI | Total tput mean | Total tput 95%CI | Compact bytes mean | Compact bytes 95%CI | Overlap mean |
|---|---:|---:|---|---:|---|---:|---|---:|---|---:|
| rocksdb_static | 0 | +0.00% | [+0.00, +0.00] | +0.00% | [+0.00, +0.00] | +0.00% | [+0.00, +0.00] | +0.00% | [+0.00, +0.00] | 0.00/4 |
| rocksdb_online | 0 | +9.25% | [+1.40, +17.11] | +22.59% | [+20.84, +24.34] | -1.16% | [-7.13, +4.81] | -14.88% | [-15.86, -13.90] | 3.00/4 |
| cgroup_equal | 0 | +22.53% | [+18.52, +26.53] | +4.12% | [+1.58, +6.65] | +4.94% | [-0.75, +10.64] | +0.80% | [-1.48, +3.08] | 0.00/4 |
| cgroup_online | 0 | +17.02% | [+10.39, +23.66] | +17.07% | [+13.83, +20.31] | +6.12% | [+2.92, +9.32] | +3.93% | [+1.83, +6.04] | 3.00/4 |
| cgroup_static_biased | 0 | +16.90% | [+10.53, +23.27] | +14.09% | [+11.50, +16.68] | +6.68% | [+4.05, +9.31] | +0.16% | [-1.87, +2.19] | 0.86/4 |
| cgroup_oracle_tiered | 0 | +11.37% | [+7.14, +15.60] | +20.13% | [+17.27, +22.99] | +8.47% | [+5.09, +11.84] | +2.80% | [+0.91, +4.70] | 4.00/4 |

## Aggregate vs `cgroup_equal` (same-mechanism)

| Policy | Failed (total) | High P99 mean | High P99 95%CI | High tput mean | High tput 95%CI | Total tput mean | Total tput 95%CI | Compact bytes mean | Compact bytes 95%CI | Overlap mean |
|---|---:|---:|---|---:|---|---:|---|---:|---|---:|
| cgroup_equal | 0 | +0.00% | [+0.00, +0.00] | +0.00% | [+0.00, +0.00] | +0.00% | [+0.00, +0.00] | +0.00% | [+0.00, +0.00] | 0.00/4 |
| cgroup_online | 0 | -4.42% | [-11.28, +2.45] | +12.45% | [+9.65, +15.25] | +1.20% | [-1.91, +4.31] | +3.12% | [+1.68, +4.55] | 3.00/4 |
| cgroup_static_biased | 0 | -4.50% | [-11.65, +2.66] | +9.60% | [+6.78, +12.42] | +1.81% | [-4.34, +7.96] | -0.61% | [-3.61, +2.40] | 0.86/4 |
| cgroup_oracle_tiered | 0 | -9.07% | [-12.98, -5.16] | +15.41% | [+11.80, +19.02] | +3.49% | [-2.18, +9.17] | +2.02% | [-1.33, +5.37] | 4.00/4 |

## Per-trial details

### realistic_cgroup_aligned_a

| Policy | Failed | High P99 vs static | High tput vs static | Total tput vs static | Compact bytes vs static | Overlap |
|---|---:|---:|---:|---:|---:|---:|
| rocksdb_static | 0 | +0.00% | +0.00% | +0.00% | +0.00% | 0.00/4 |
| rocksdb_online | 0 | +6.85% | +22.11% | +5.58% | -13.64% | 3.00/4 |
| cgroup_equal | 0 | +23.06% | +2.72% | +9.72% | +2.65% | 0.00/4 |
| cgroup_online | 0 | +22.88% | +13.29% | +9.12% | +6.61% | 3.00/4 |
| cgroup_static_biased | 0 | +21.87% | +11.84% | +8.23% | +1.09% | 0.86/4 |
| cgroup_oracle_tiered | 0 | +14.76% | +16.70% | +9.04% | +3.83% | 4.00/4 |

### realistic_cgroup_aligned_b

| Policy | Failed | High P99 vs static | High tput vs static | Total tput vs static | Compact bytes vs static | Overlap |
|---|---:|---:|---:|---:|---:|---:|
| rocksdb_static | 0 | +0.00% | +0.00% | +0.00% | +0.00% | 0.00/4 |
| rocksdb_online | 0 | +12.22% | +23.39% | -1.56% | -14.73% | 3.00/4 |
| cgroup_equal | 0 | +24.58% | +7.31% | +2.74% | -1.90% | 0.00/4 |
| cgroup_online | 0 | +17.23% | +18.99% | +3.12% | +2.47% | 3.00/4 |
| cgroup_static_biased | 0 | +19.57% | +14.92% | +3.37% | +0.32% | 0.86/4 |
| cgroup_oracle_tiered | 0 | +15.24% | +19.40% | +4.07% | +4.08% | 4.00/4 |

### realistic_cgroup_aligned_c

| Policy | Failed | High P99 vs static | High tput vs static | Total tput vs static | Compact bytes vs static | Overlap |
|---|---:|---:|---:|---:|---:|---:|
| rocksdb_static | 0 | +0.00% | +0.00% | +0.00% | +0.00% | 0.00/4 |
| rocksdb_online | 0 | +18.10% | +20.42% | +1.29% | -15.72% | 3.00/4 |
| cgroup_equal | 0 | +25.19% | +3.08% | +9.58% | +1.71% | 0.00/4 |
| cgroup_online | 0 | +16.80% | +15.36% | +8.25% | +4.57% | 3.00/4 |
| cgroup_static_biased | 0 | +11.13% | +16.79% | +5.73% | -2.56% | 0.86/4 |
| cgroup_oracle_tiered | 0 | +7.72% | +22.05% | +11.10% | +0.35% | 4.00/4 |

### realistic_cgroup_aligned_d

| Policy | Failed | High P99 vs static | High tput vs static | Total tput vs static | Compact bytes vs static | Overlap |
|---|---:|---:|---:|---:|---:|---:|
| rocksdb_static | 0 | +0.00% | +0.00% | +0.00% | +0.00% | 0.00/4 |
| rocksdb_online | 0 | +1.15% | +22.95% | -4.47% | -14.95% | 3.00/4 |
| cgroup_equal | 0 | +22.76% | +2.48% | +3.48% | +1.76% | 0.00/4 |
| cgroup_online | 0 | +8.50% | +18.85% | +6.02% | +3.20% | 3.00/4 |
| cgroup_static_biased | 0 | +11.58% | +12.12% | +7.88% | +1.69% | 0.86/4 |
| cgroup_oracle_tiered | 0 | +9.85% | +20.07% | +8.01% | +3.42% | 4.00/4 |

### realistic_cgroup_aligned_e

| Policy | Failed | High P99 vs static | High tput vs static | Total tput vs static | Compact bytes vs static | Overlap |
|---|---:|---:|---:|---:|---:|---:|
| rocksdb_static | 0 | +0.00% | +0.00% | +0.00% | +0.00% | 0.00/4 |
| rocksdb_online | 0 | +7.94% | +24.09% | -6.64% | -15.35% | 3.00/4 |
| cgroup_equal | 0 | +17.04% | +5.00% | -0.80% | -0.22% | 0.00/4 |
| cgroup_online | 0 | +19.71% | +18.85% | +4.10% | +2.82% | 3.00/4 |
| cgroup_static_biased | 0 | +20.36% | +14.80% | +8.18% | +0.26% | 0.86/4 |
| cgroup_oracle_tiered | 0 | +9.27% | +22.42% | +10.13% | +2.34% | 4.00/4 |

