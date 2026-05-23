# realistic_big_a Repeated-Trial Aggregate (n=5)

Trials: realistic_big_a, realistic_big_b, realistic_big_c, realistic_big_d, realistic_big_e

## Per-trial online vs static (percent change)

| Trial | High write P99 | High throughput | Total throughput | Compaction write bytes | Mean write P99 (all tiers) | Mid write P99 | Mid throughput | Low write P99 | Low throughput | Mean overlap |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| realistic_big_a | -17.24% | +39.36% | -3.32% | -20.83% | -16.01% | +0.70% | -11.00% | -0.69% | -2.71% | 3.14/4 |
| realistic_big_b | -8.63% | +34.22% | -4.65% | -18.43% | -7.33% | +11.15% | -13.14% | +2.19% | -3.91% | 3.14/4 |
| realistic_big_c | -9.62% | +37.91% | -1.30% | -20.70% | -9.30% | -4.84% | -5.40% | +1.63% | -1.46% | 3.14/4 |
| realistic_big_d | -16.87% | +42.12% | -1.31% | -24.26% | -15.58% | +4.53% | -7.37% | +3.48% | -1.16% | 3.14/4 |
| realistic_big_e | -6.24% | +36.71% | +2.05% | -20.88% | -6.00% | -2.71% | -8.52% | -4.17% | +3.25% | 3.14/4 |

## Aggregate online vs static (percent change)

| Metric | n | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| High write P99 | 5 | -11.72% | -17.24% | -6.24% | 5.03 | [-17.96%, -5.48%] |
| High throughput | 5 | +38.07% | +34.22% | +42.12% | 2.95 | [+34.40%, +41.73%] |
| Total throughput | 5 | -1.71% | -4.65% | +2.05% | 2.54 | [-4.85%, +1.44%] |
| Compaction write bytes | 5 | -21.02% | -24.26% | -18.43% | 2.08 | [-23.61%, -18.43%] |
| Mean write P99 (all tiers) | 5 | -10.84% | -16.01% | -6.00% | 4.67 | [-16.64%, -5.05%] |
| Mid write P99 | 5 | +1.77% | -4.84% | +11.15% | 6.34 | [-6.10%, +9.63%] |
| Mid throughput | 5 | -9.09% | -13.14% | -5.40% | 3.04 | [-12.86%, -5.31%] |
| Low write P99 | 5 | +0.49% | -4.17% | +3.48% | 3.01 | [-3.25%, +4.23%] |
| Low throughput | 5 | -1.20% | -3.91% | +3.25% | 2.72 | [-4.57%, +2.17%] |

Online high-set overlap after warmup: mean=3.14/4, min=3.14, max=3.14, stdev=0.00.
