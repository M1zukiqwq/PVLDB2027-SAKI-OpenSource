# realistic_big static_biased Repeated-Trial Aggregate (n=5)

Trials: realistic_big_a, realistic_big_b, realistic_big_c, realistic_big_d, realistic_big_e

## Per-trial Static Biased Vs Static (percent change)

| Trial | High write P99 | High throughput | Total throughput | Compaction write bytes | Mid write P99 | Mid throughput | Low write P99 | Low throughput |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| realistic_big_a | -4.86% | +15.03% | +5.57% | -8.57% | -0.08% | -12.63% | -18.35% | +9.60% |
| realistic_big_b | -7.01% | +18.84% | -8.32% | -10.90% | -4.59% | -10.64% | -0.15% | -8.51% |
| realistic_big_c | -5.47% | +18.18% | -8.17% | -9.94% | +2.64% | -11.20% | +1.82% | -8.23% |
| realistic_big_d | -8.50% | +18.63% | -6.60% | -10.21% | +14.21% | -10.89% | +2.94% | -6.36% |
| realistic_big_e | -4.39% | +16.17% | -3.22% | -9.87% | -4.56% | -4.58% | -1.99% | -3.42% |

## Aggregate Static Biased Vs Static

| Metric | n | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| High write P99 | 5 | -6.05% | -8.50% | -4.39% | 1.69 | [-8.14%, -3.95%] |
| High throughput | 5 | +17.37% | +15.03% | +18.84% | 1.68 | [+15.28%, +19.46%] |
| Total throughput | 5 | -4.15% | -8.32% | +5.57% | 5.81 | [-11.35%, +3.06%] |
| Compaction write bytes | 5 | -9.90% | -10.90% | -8.57% | 0.85 | [-10.95%, -8.85%] |
| Mid write P99 | 5 | +1.52% | -4.59% | +14.21% | 7.73 | [-8.07%, +11.12%] |
| Mid throughput | 5 | -9.99% | -12.63% | -4.58% | 3.12 | [-13.86%, -6.12%] |
| Low write P99 | 5 | -3.15% | -18.35% | +2.94% | 8.71 | [-13.96%, +7.66%] |
| Low throughput | 5 | -3.39% | -8.51% | +9.60% | 7.54 | [-12.74%, +5.97%] |

## Per-trial Online Vs Static Biased (percent change)

| Trial | High write P99 | High throughput | Total throughput | Compaction write bytes | Mid write P99 | Mid throughput | Low write P99 | Low throughput |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| realistic_big_a | -13.00% | +21.15% | -8.42% | -13.40% | +0.78% | +1.86% | +21.64% | -11.23% |
| realistic_big_b | -1.74% | +12.94% | +4.00% | -8.45% | +16.49% | -2.79% | +2.35% | +5.03% |
| realistic_big_c | -4.39% | +16.69% | +7.48% | -11.95% | -7.29% | +6.52% | -0.19% | +7.37% |
| realistic_big_d | -9.15% | +19.81% | +5.66% | -15.65% | -8.47% | +3.95% | +0.53% | +5.56% |
| realistic_big_e | -1.93% | +17.68% | +5.44% | -12.21% | +1.94% | -4.13% | -2.22% | +6.91% |

## Aggregate Online Vs Static Biased

| Metric | n | Mean | Min | Max | Stdev | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| High write P99 | 5 | -6.04% | -13.00% | -1.74% | 4.91 | [-12.14%, +0.05%] |
| High throughput | 5 | +17.65% | +12.94% | +21.15% | 3.16 | [+13.73%, +21.58%] |
| Total throughput | 5 | +2.83% | -8.42% | +7.48% | 6.41 | [-5.12%, +10.79%] |
| Compaction write bytes | 5 | -12.33% | -15.65% | -8.45% | 2.61 | [-15.58%, -9.08%] |
| Mid write P99 | 5 | +0.69% | -8.47% | +16.49% | 9.99 | [-11.70%, +13.09%] |
| Mid throughput | 5 | +1.08% | -4.13% | +6.52% | 4.49 | [-4.49%, +6.65%] |
| Low write P99 | 5 | +4.42% | -2.22% | +21.64% | 9.76 | [-7.70%, +16.54%] |
| Low throughput | 5 | +2.73% | -11.23% | +7.37% | 7.86 | [-7.03%, +12.49%] |

## Overlap after warmup

- static_biased: mean=1.14/4, min=1.14, max=1.14, stdev=0.00
- online: mean=3.14/4, min=3.14, max=3.14, stdev=0.00

