# Baleen 0.1% Trace-Family Audit

- generated_at: 2026-05-19T12:26:55.753840+08:00
- verdict_gate_a: **fail**
- family: baleen
- value_size: 1024 bytes
- future_budget_contract: high/mid/low = 11/7/3 MB/s, total 112 MB/s

## Input Files

- archive: `data/baleen_storage_0.1/storage_0.1.tar.gz` (17081636 bytes)
- Region7: `data/baleen_storage_0.1/storage/20230325/Region7`; full_0_0.1.trace=19951760B
- Region6: `data/baleen_storage_0.1/storage/20230325/Region6`; full_0_0.1.trace=18210254B
- Region5: `data/baleen_storage_0.1/storage/20230325/Region5`; full_0_0.1.trace=16187893B

## Combined Supply Diagnostics

- records_total: 753531
- duration_sec_sum_across_traces: 1792993.000
- write_record_share: 0.1227
- write_op_count_share: 0.1162
- raw_write_mbps_over_summed_duration: 0.3204
- write_size_bytes p50/p90/p99: 7576114.0 / 8388608.0 / 25165824.0
- 20s global write MB/s p50/p95/p99: 0.2324 / 1.1425 / 1.6008
- 1s burst write MB/s p50/p95/p99: 0.0000 / 0.0000 / 8.0000
- locality: unique_block_ratio=0.9364, top1pct_offset_bucket_share=0.0212, top1pct_block_share=0.0369

## Mapping Diagnostics

| mapping | offered QPS mean | offered QPS p95/20s | logical MB/s mean @1KiB | per-thread QPS p95 | risk |
|---|---:|---:|---:|---:|---|
| request-as-one-put | 0.05 | 0.15 | 0.0001 | 0.04 | low |
| 64KiB-chunk | 5.14 | 18.35 | 0.0050 | 4.59 | low |
| 1KiB-chunk | 328.13 | 1169.97 | 0.3204 | 292.49 | low |

## Candidate Segment Summary

- global_scale: 198.2226995589032
- basic_candidate_segments: 964
- scaled_candidate_segments: 297
- independent_candidate_segments: 146

## Gate A

- tenant_axis_ge16: True
- candidate_segments_ge5: True
- locality_top1pct_offset_bucket_ge5pct: False

## Candidate Preview

| segment | trace | n_tenants | n_write | raw MB/s | scaled MB/s | geq70 | drift | frozen overlap | changes | qps risk |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Region5_w83962157 | Region5 | 16 | 5 | 0.3833 | 75.98 | 6 | 1.000 | 2.286 | 6 | low |
| Region5_w83966774 | Region5 | 16 | 7 | 0.6702 | 132.85 | 6 | 2.000 | 2.286 | 6 | low |
| Region5_w83967684 | Region5 | 16 | 8 | 0.5435 | 107.74 | 6 | 1.857 | 2.143 | 6 | low |
| Region5_w83968407 | Region5 | 17 | 8 | 0.5953 | 118.00 | 6 | 2.000 | 2.286 | 7 | low |
| Region5_w83969498 | Region5 | 16 | 6 | 0.5312 | 105.29 | 6 | 1.429 | 2.571 | 6 | low |
| Region5_w83969944 | Region5 | 16 | 6 | 0.4032 | 79.93 | 6 | 1.286 | 2.714 | 6 | low |
| Region5_w83970988 | Region5 | 16 | 7 | 0.6247 | 123.84 | 7 | 1.571 | 2.429 | 5 | low |
| Region5_w83971636 | Region5 | 16 | 9 | 0.4828 | 95.71 | 6 | 1.714 | 2.571 | 7 | low |
| Region5_w83973886 | Region5 | 16 | 6 | 0.4211 | 83.48 | 6 | 1.571 | 2.571 | 7 | low |
| Region5_w83975089 | Region5 | 16 | 9 | 0.5614 | 111.28 | 7 | 2.000 | 1.571 | 7 | low |
| Region5_w83976306 | Region5 | 16 | 7 | 0.3628 | 71.92 | 6 | 1.286 | 2.429 | 7 | low |
| Region5_w83979058 | Region5 | 16 | 6 | 0.4871 | 96.56 | 6 | 0.857 | 2.143 | 5 | low |
| Region5_w83979594 | Region5 | 17 | 5 | 0.4669 | 92.54 | 6 | 1.857 | 2.714 | 7 | low |
| Region5_w83980284 | Region5 | 16 | 6 | 0.6376 | 126.39 | 7 | 1.714 | 2.571 | 7 | low |
| Region5_w83980530 | Region5 | 16 | 6 | 0.4801 | 95.17 | 6 | 1.714 | 2.286 | 6 | low |
| Region5_w83981405 | Region5 | 16 | 6 | 0.4609 | 91.37 | 6 | 1.429 | 2.143 | 7 | low |
| Region5_w83983562 | Region5 | 16 | 7 | 0.5826 | 115.49 | 6 | 1.714 | 2.286 | 6 | low |
| Region5_w83983686 | Region5 | 16 | 8 | 0.6980 | 138.36 | 6 | 2.143 | 2.714 | 7 | low |
| Region5_w83984754 | Region5 | 16 | 9 | 0.5856 | 116.07 | 6 | 2.000 | 1.429 | 7 | low |
| Region5_w83986745 | Region5 | 16 | 4 | 0.4689 | 92.95 | 6 | 1.000 | 2.714 | 6 | low |
