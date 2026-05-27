# static_autotuned mechanism check (Agent 2)

Generated from remote `${SAKI_RUN_ROOT}/results` at `20260527T013545`. New CSV: `${SAKI_RUN_ROOT}/results/static_autotuned_mechanism_agent2_20260527T013545_epoch_tier.csv`.

## Data coverage

- Raw JSONs used: `realistic_static_realistic_big_[a-e].json` and `realistic_static_autotuned_realistic_big_[a-e].json`.
- `workload` has per-tenant/epoch fields including: `epoch`, `tenant`, `tier`, `assigned_tier`, `rate_limit`, `bg_jobs`, `ops_per_sec`, `runtime_sec`, `write_p99_us`, `get_p99_us`, `compact_read_bytes`, `compact_write_bytes`, `flush_write_bytes`, `stall_*`, and log path.
- `alloc_history` has only configured assignment: `assigned_tier`, `bg_jobs`, `rate_limit`.
- JSON does not expose actual limiter bytes, effective dynamic ceiling, or RocksDB autotune internal rate. Search for actual/effective/auto limiter fields returned: `[]`.
- Logs confirm autotune command flag in `640/640` static_autotuned tenant-epoch logs; static has `0/640`. Logs expose `rocksdb.number.rate_limiter.drains`, not limiter bytes.

## Config invariance

- `static`: assigned_tiers=[('mid',)], rates=[(7000000,)], jobs=[(2,)], assigned-high overlap mean=0.00, max=0
- `static_autotuned`: assigned_tiers=[('mid',)], rates=[(7000000,)], jobs=[(2,)], assigned-high overlap mean=0.00, max=0

Both policies keep all 16 tenants at `assigned_tier=mid`, `rate_limit=7,000,000 B/s`, `bg_jobs=2` in every epoch/trial; there is no cross-tenant promotion of true high tenants.

## Aggregate result

- High-tier write P99: static `30988.2 us`, static_autotuned `39923.1 us`, mean per-trial delta `28.8%`.
- High-tier throughput: static `89,637 ops/s-sum`, static_autotuned `71,996 ops/s-sum`, mean per-trial delta `-19.7%`.
- Mid throughput delta `-17.3%`, low throughput delta `1.9%`, total throughput delta `-1.8%`.
- Total compact-write bytes delta `-0.2%`, so the regression is not explained by substantially more compaction work.

## High-tier epoch means

| epoch | ops delta | write P99 delta | runtime delta | compaction write delta | drains delta |
|---:|---:|---:|---:|---:|---:|
| 0 | -19.4% | 7.8% | 21.8% | 0.7% | 69.3% |
| 1 | -20.2% | 17.8% | 24.4% | 0.1% | 60.3% |
| 2 | -19.5% | 24.1% | 19.3% | -1.4% | 60.5% |
| 3 | -19.0% | 39.3% | 21.8% | -1.3% | 56.4% |
| 4 | -18.9% | 26.7% | 20.3% | -1.6% | 55.2% |
| 5 | -20.3% | 42.5% | 24.9% | 1.5% | 59.9% |
| 6 | -20.2% | 34.5% | 21.7% | -1.1% | 58.9% |
| 7 | -19.8% | 36.5% | 22.2% | 1.4% | 56.9% |

Across high tenant-epochs, static_autotuned shows average ops delta `-19.7%`, P99 delta `28.6%`, runtime delta `22.0%`, compact-write delta `-0.2%`, and drain-count delta `59.7%`.

## Mechanism

The available evidence supports a local-control explanation, not a workload-shift explanation. RocksDB auto-tuning is enabled per DB instance while every tenant keeps the same 7 MB/s nominal limiter and 2 background jobs. Because this local autotuner has no global view, it cannot move unused budget from low/mid tenants to the currently high tenants and it never changes the cross-tenant assignment; the assigned-high overlap is exactly zero. Under the same ceiling, the autotuned runs show lower high-tier ops/sec, longer high-tier runtimes, higher rate-limiter drain counts, and higher write P99 while total compact-write bytes stay essentially unchanged. Thus the observed harm is consistent with local smoothing/throttling reducing effective per-instance service during pressure, while SAKI improves by changing assignment/budget across tenants.

## Figure feasibility

Can plot per-tenant time series for `ops_per_sec`, `write_p99_us`, `compact_write_bytes`, configured `rate_limit`, `assigned_tier`, `tier`, and log-derived drain count over 8 epochs. Cannot plot actual limiter bytes or effective autotuned rate without a new instrumentation run.

## Need for extra experiment

No extra experiment is needed for the paper mechanism paragraph if framed as above. A supplemental experiment is only needed if we want to claim measured effective limiter bytes/rate; the current data do not contain that signal.
