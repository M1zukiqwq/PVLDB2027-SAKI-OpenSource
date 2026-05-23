# Paper Tables

Generated: see git log for date.

## Main Result: Continuous demand2f (5 trials, online vs static)

| Metric | Mean | Min | Max | Stdev | 95% CI
|---|---:|---:|---:|---:|---:|
| High write P99 | -27.0% | -39.2% | -12.0% | 10.1 | ±12.5pp |
| High write P999 | -47.1% | -54.0% | -39.3% | 7.1 | ±8.8pp |
| High write throughput | +26.1% | +21.5% | +28.2% | 2.8 | ±3.5pp |
| Total throughput | +4.3% | +1.3% | +6.8% | 2.6 | ±3.2pp |
| Compact bytes | -1.9% | -5.8% | -0.1% | 2.3 | ±2.9pp |
| Bytes/write | -11.9% | -13.5% | -9.1% | 1.9 | ±2.3pp |
| Mean high overlap | 3.00/4 | 3.00 | 3.00 | 0.00 | - |
| Gate pass | 5/5 | - | - | - | - |
| Failed tenants | 0 | - | - | - | - |

## Main Result: Epoch-Level realistic_big_a (5 trials, online vs static)

Detailed source: `remote-results/paper_tables/realistic_big_a_aggregate.md`.

| Metric | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---|
| High write P99 | -11.7% | -17.2% | -6.2% | 5.0 | [-18.0%, -5.5%] |
| High throughput | +38.1% | +34.2% | +42.1% | 3.0 | [+34.4%, +41.7%] |
| Total throughput | -1.7% | -4.7% | +2.1% | 2.5 | [-4.9%, +1.4%] |
| Compact bytes | -21.0% | -24.3% | -18.4% | 2.1 | [-23.6%, -18.4%] |

## Fairness / Collateral Damage: Continuous demand2f (5 trials, online vs static)

| Tier | P99 | P999 | Write tput | Total tput | Bytes | Bytes/write |
|---|---:|---:|---:|---:|---:|---:|
| HIGH | -27.0% | -47.1% | +26.1% | +26.3% | +26.3% | +0.1% |
| MID | -17.8% | -4.5% | +1.3% | +1.3% | -5.5% | -6.7% |
| LOW | +1.4% | +2.6% | -3.0% | -4.0% | -35.3% | -33.2% |
| ALL | -21.2% | -29.2% | +11.5% | +4.3% | -1.9% | -11.9% |

## Fairness: Epoch-level realistic_big_a (5-trial aggregate, online vs static)

| Tier | P99 delta | Throughput delta |
|---|---:|---:|
| HIGH | -11.7% | +38.1% |
| MID | +1.8% | -9.1% |
| LOW | +0.5% | -1.2% |
| ALL | -10.8% | -1.7% |

## Continuous Read and CPU Collateral

Five embedded_demand2f_16t trials, online vs static. Negative latency and CPU/write values are improvements; positive read-throughput values are improvements.

| Tier | CPU/write | Read P99 | Read P999 | Read tput | Static read P99 (us) | Online read P99 (us) |
|---|---:|---:|---:|---:|---:|---:|
| HIGH | -1.6% | -4.5% | -2.2% | +26.9% | 135.9 | 129.7 |
| MID | -8.0% | +1.4% | -15.7% | +1.3% | 146.6 | 148.6 |
| LOW | -33.5% | +13.7% | +12.4% | -4.1% | 82.2 | 93.4 |
| ALL | -12.8% | +7.0% | -10.6% | +0.5% | 108.5 | 116.1 |

## Built-In RocksDB Baseline: static_autotuned (5 trials, realistic_big_{a..e})

Detailed source: `remote-results/paper_tables/realistic_big_builtin_aggregate.md`.

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

## Stronger Baseline: static_biased (demand2f trial a)

| Policy | High P99 (us) | High P999 (us) | High write tput | Bytes/write | Overlap |
|---|---:|---:|---:|---:|---:|
| static | 19806 | 50225 | 2474 | 4683 | 0.00/4 |
| static_biased | 10108 | 31383 | 2090 | 4778 | 0.86/4 |
| online | 12034 | 23344 | 3169 | 4258 | 3.00/4 |

## Score-Mode Ablation

| Mode | High P99 | High P999 | High tput | Total tput | Bytes | B/W | Overlap | Max lag | Gate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| demand | -27.0% | -47.1% | +26.1% | +4.3% | -1.9% | -11.9% | 3.00/4 | 0 | 5/5 |
| pressure | -14.1% | -7.1% | +9.8% | +3.1% | -0.6% | -7.7% | 1.86/4 | 5 | fail |
| hybrid | -35.2% | -66.6% | +15.6% | +9.2% | +0.5% | -10.2% | 2.29/4 | 2 | fail |

## Ranking-Margin Robustness

Read-only replay over the published continuous main online raw files
(5 trials, 35 post-warmup control decisions). Each row reconstructs a control
decision from the previous window's observations; no RocksDB run is launched by
this analysis.

- Reference replay matches recorded online high-budget set: 35/35
- Reference top-H true-high overlap: 3.00/4
- Reference boundary margin: mean 5.30, min 0.39, max 8.24

| Score instance | Exact top-H vs reference | Mean Jaccard vs reference | Mean true-high overlap |
|---|---:|---:|---:|
| demand only (6D) | 35/35 | 1.00 [1.00,1.00] | 3.00/4 |
| anchor x0.5 | 31/35 | 0.95 [0.33,1.00] | 2.86/4 |
| anchor x2 | 35/35 | 1.00 [1.00,1.00] | 3.00/4 |
| residual x0.5 | 35/35 | 1.00 [1.00,1.00] | 3.00/4 |
| residual x2 | 31/35 | 0.95 [0.33,1.00] | 2.86/4 |
| pressure-only negative control | 10/35 | 0.61 [0.14,1.00] | 2.03/4 |

## Longer-Run Confirmation (360s, 16 drift events)

| Metric | Value |
|---|---:|
| High P99 | -13.9% |
| High P999 | -19.6% |
| High write tput | +11.6% |
| Total tput | -0.7% |
| Bytes/write | -3.4% |
| Mean overlap | 3.00/4 |
| Max lag | 1 windows |
| Failed tenants | 0 |
