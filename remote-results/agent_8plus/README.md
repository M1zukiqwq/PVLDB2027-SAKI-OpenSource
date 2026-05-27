# 8+ Evidence Push Supplemental Results

This directory contains additional raw and summarized results collected after
the paper-facing artifact snapshot. The 32/64-tenant files are scale stresses,
not headline performance evidence.

## 32/64-Tenant Scale Stress

These runs test whether the external controller remains live and continues to
track a larger tenant inventory. They should not be interpreted as scale-out
performance claims: tenant counts and aggregate background budget scale, while
the host CPU and storage device remain fixed, so CPU scheduling and resource
contention can dominate measured tails.

| Scale | Repeats | Failed tenants | Mean overlap | High write tput | Total tput | High P99 | High P999 | Bytes/write |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 32 tenants | 2 | 0 | 6/8 | +18.1% | +4.7% | +114.9% | +86.3% | +12.3% |
| 64 tenants | 1 | 0 | 12/16 | +6.7% | +6.4% | +79.0% | +12.3% | +23.5% |

Use these results as larger-inventory liveness/tracking evidence only. A
scalability claim would require more repetitions and host telemetry such as
CPU utilization, run queue length, context switches, iowait, and per-tenant
resource saturation.
