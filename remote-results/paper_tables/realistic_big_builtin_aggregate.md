# realistic_big static_autotuned (built-in) Repeated-Trial Aggregate (n=5)

Built-in baseline policy: same per-tenant max_background_jobs and 
rate_limiter_bytes_per_sec ceilings as `static` (2 jobs, 7 MB/s), plus 
`--rate_limiter_auto_tuned=1`. Aggregate budget identical to all other 
baselines (32 jobs, 112 MB/s). Each tenant locally auto-tunes inside 
`[rate / 20, rate]`; cross-tenant info is never shared.

Trials: realistic_big_a, realistic_big_b, realistic_big_c, realistic_big_d, realistic_big_e

## Per-trial Builtin Vs Static (percent change)

| Trial | High write P99 | High throughput | Total throughput | Compaction write bytes | Mid write P99 | Mid throughput | Low write P99 | Low throughput |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| realistic_big_a | +30.09% | -20.86% | +6.18% | +0.62% | +1.51% | -19.32% | -17.59% | +12.97% |
| realistic_big_b | +31.43% | -20.18% | -5.12% | +1.59% | +2.25% | -18.59% | -1.26% | -2.07% |
| realistic_big_c | +24.35% | -17.08% | -3.11% | -3.66% | -6.37% | -12.93% | +3.53% | -0.90% |
| realistic_big_d | +27.75% | -20.13% | -3.44% | +0.72% | +9.78% | -18.98% | -0.86% | -0.03% |
| realistic_big_e | +30.60% | -20.12% | -3.44% | -0.42% | -1.25% | -16.87% | -0.81% | -0.43% |

## Aggregate Builtin Vs Static

| Metric | n | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| High write P99 | 5 | +28.84% | +24.35% | +31.43% | 2.86 | [+25.29%, +32.40%] |
| High throughput | 5 | -19.67% | -20.86% | -17.08% | 1.48 | [-21.51%, -17.83%] |
| Total throughput | 5 | -1.79% | -5.12% | +6.18% | 4.52 | [-7.40%, +3.83%] |
| Compaction write bytes | 5 | -0.23% | -3.66% | +1.59% | 2.04 | [-2.77%, +2.31%] |
| Mid write P99 | 5 | +1.18% | -6.37% | +9.78% | 5.88 | [-6.11%, +8.48%] |
| Mid throughput | 5 | -17.34% | -19.32% | -12.93% | 2.64 | [-20.61%, -14.07%] |
| Low write P99 | 5 | -3.40% | -17.59% | +3.53% | 8.17 | [-13.54%, +6.75%] |
| Low throughput | 5 | +1.91% | -2.07% | +12.97% | 6.23 | [-5.83%, +9.64%] |

## Per-trial Online Vs Builtin (percent change)

| Trial | High write P99 | High throughput | Total throughput | Compaction write bytes | Mid write P99 | Mid throughput | Low write P99 | Low throughput |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| realistic_big_a | -36.38% | +76.09% | -8.94% | -21.31% | -0.80% | +10.32% | +20.51% | -13.88% |
| realistic_big_b | -30.48% | +68.14% | +0.49% | -19.71% | +8.70% | +6.69% | +3.49% | -1.87% |
| realistic_big_c | -27.32% | +66.31% | +1.87% | -17.69% | +1.64% | +8.65% | -1.84% | -0.57% |
| realistic_big_d | -34.93% | +77.94% | +2.21% | -24.80% | -4.78% | +14.33% | +4.38% | -1.14% |
| realistic_big_e | -28.21% | +71.14% | +5.68% | -20.54% | -1.48% | +10.05% | -3.39% | +3.69% |

## Aggregate Online Vs Builtin

| Metric | n | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| High write P99 | 5 | -31.46% | -36.38% | -27.32% | 4.03 | [-36.47%, -26.46%] |
| High throughput | 5 | +71.93% | +66.31% | +77.94% | 5.00 | [+65.72%, +78.13%] |
| Total throughput | 5 | +0.26% | -8.94% | +5.68% | 5.49 | [-6.55%, +7.08%] |
| Compaction write bytes | 5 | -20.81% | -24.80% | -17.69% | 2.61 | [-24.05%, -17.57%] |
| Mid write P99 | 5 | +0.66% | -4.78% | +8.70% | 5.05 | [-5.61%, +6.92%] |
| Mid throughput | 5 | +10.01% | +6.69% | +14.33% | 2.81 | [+6.52%, +13.49%] |
| Low write P99 | 5 | +4.63% | -3.39% | +20.51% | 9.48 | [-7.14%, +16.40%] |
| Low throughput | 5 | -2.75% | -13.88% | +3.69% | 6.59 | [-10.93%, +5.42%] |

## Per-trial Static Biased Vs Builtin (percent change)

| Trial | High write P99 | High throughput | Total throughput | Compaction write bytes |
|---:|---:|---:|---:|---:|
| realistic_big_a | -26.87% | +45.35% | -0.57% | -9.14% |
| realistic_big_b | -29.25% | +48.88% | -3.37% | -12.29% |
| realistic_big_c | -23.98% | +42.52% | -5.22% | -6.52% |
| realistic_big_d | -28.37% | +48.53% | -3.27% | -10.85% |
| realistic_big_e | -26.79% | +45.43% | +0.23% | -9.49% |

## Aggregate Static Biased Vs Builtin

| Metric | n | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| High write P99 | 5 | -27.05% | -29.25% | -23.98% | 2.01 | [-29.54%, -24.56%] |
| High throughput | 5 | +46.14% | +42.52% | +48.88% | 2.62 | [+42.89%, +49.39%] |
| Total throughput | 5 | -2.44% | -5.22% | +0.23% | 2.23 | [-5.21%, +0.33%] |
| Compaction write bytes | 5 | -9.66% | -12.29% | -6.52% | 2.15 | [-12.33%, -6.99%] |

## Per-trial Online Vs Static (percent change)

| Trial | High write P99 | High throughput | Total throughput | Compaction write bytes |
|---:|---:|---:|---:|---:|
| realistic_big_a | -17.24% | +39.36% | -3.32% | -20.83% |
| realistic_big_b | -8.63% | +34.22% | -4.65% | -18.43% |
| realistic_big_c | -9.62% | +37.91% | -1.30% | -20.70% |
| realistic_big_d | -16.87% | +42.12% | -1.31% | -24.26% |
| realistic_big_e | -6.24% | +36.71% | +2.05% | -20.88% |

## Aggregate Online Vs Static

| Metric | n | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| High write P99 | 5 | -11.72% | -17.24% | -6.24% | 5.03 | [-17.96%, -5.48%] |
| High throughput | 5 | +38.07% | +34.22% | +42.12% | 2.95 | [+34.40%, +41.73%] |
| Total throughput | 5 | -1.71% | -4.65% | +2.05% | 2.54 | [-4.85%, +1.44%] |
| Compaction write bytes | 5 | -21.02% | -24.26% | -18.43% | 2.08 | [-23.61%, -18.43%] |

## Overlap after warmup

- static_autotuned (built-in): mean=0.00/4, min=0.00, max=0.00, stdev=0.00
- online: mean=3.14/4, min=3.14, max=3.14, stdev=0.00

## Failed tenants per trial

| Policy | Sum failed tenants across trials |
|---|---:|
| static | 0 |
| static_autotuned | 0 |
| online | 0 |

