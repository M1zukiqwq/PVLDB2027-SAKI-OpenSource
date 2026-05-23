# Rate-Limiter Coverage Microstudy

4 identical tenants, static policy, n=3 seeds per per-tenant budget; window 0 dropped. Numbers below are means across seeds with [min, max] in brackets. This sweep changes the per-tenant rate-limiter ceiling only; it is **not** a SAKI policy comparison and does not alter the 112 MB/s fixed-budget main results.

| Budget MB/s | n | Failed | Limiter actual MB/s | Compact MB/s | Flush MB/s | Background MB/s | Coverage | Write P99 ms | Pending p95 MiB |
|---:|---:|---:|---|---|---|---|---:|---:|---:|
| 1.0 | 3 | 0 |  1.00 [ 1.00, 1.00] |  0.69 [ 0.69, 0.69] |  0.32 [ 0.31, 0.33] |  1.00 [ 1.00, 1.01] | 0.99 [0.99,1.00] | 59.30 | 20.6 |
| 3.0 | 3 | 0 |  2.96 [ 2.96, 2.97] |  2.11 [ 2.07, 2.17] |  0.79 [ 0.78, 0.80] |  2.90 [ 2.85, 2.97] | 1.02 [1.00,1.04] | 22.32 | 12.3 |
| 6.0 | 3 | 0 |  5.70 [ 5.69, 5.70] |  4.33 [ 4.29, 4.36] |  1.36 [ 1.34, 1.37] |  5.69 [ 5.66, 5.71] | 1.00 [1.00,1.01] | 8.61 | 10.6 |
| 11.0 | 3 | 0 |  7.28 [ 7.24, 7.32] |  5.50 [ 5.38, 5.62] |  1.77 [ 1.75, 1.79] |  7.27 [ 7.17, 7.37] | 1.00 [0.99,1.01] | 5.76 | 10.9 |

## Monotonicity diagnostics

- Rate-limiter actual MB/s mean non-decreasing with budget: **True**
- Background (flush+compact) MB/s mean non-decreasing with budget: **True**
- Zero failed tenants across all sweep runs: **True**

## Negative control: --no-rocksdb-rate-limiter at 6 MB/s

| n | Failed | Limiter actual MB/s | Compact MB/s | Flush MB/s | Background MB/s | Write P99 ms |
|---:|---:|---|---|---|---|---:|
| 2 | 0 |  0.00 [ 0.00, 0.00] |  6.05 [ 5.92, 6.18] |  1.96 [ 1.93, 1.99] |  8.01 [ 7.85, 8.17] | 5.41 |

Negative control runs are a sanity check only (n=2); they are not a SAKI baseline.

## Interpretation

This validates that the runtime actuation path exercises RocksDB background maintenance I/O; it is not a SAKI policy comparison and does not alter the fixed-budget main experiments.
