# Baleen Static-Only RocksDB Smoke

- recommend_n5: **False**
- reason_if_no_n5: `engine_stress_insufficient;completion_ratio_insufficient`
- segment_id: `Region6_w83982457`
- trace_id: `Region6`
- budget_contract_preserved: True
- selected_users: 312948048, 312948042, 312948050, 312948044, 312948053, 312948047, 312948057, 312948071, 312948041, 312948054, 312948039, 312948043, 312948051, 312948056, 312948045, 312948055

## Gate B Metrics

| metric | value | pass criterion |
|---|---:|---|
| offered_write_mb_s | 89.127 | diagnostic |
| rate_limiter_actual_mb_s | 12.707 | diagnostic |
| completed_logical_write_mb_s | 1.099 | diagnostic |
| compact_output_mb_s | 11.275 | >= 60 OR pending p95 >= 32 MiB |
| pending_compaction_bytes_p95_mib | 10.417 | >= 32 MiB OR compact >= 60 |
| completion_ratio | 0.0123 | >= 0.5 |
| failed_tenants | 0 | == 0 |

## Pass Flags

- engine_stress_pass: False
- completion_pass: False
- correctness_pass: True

## Per-Tenant Completed MB/s

| tenant | completed | offered | completion | limiter | compact | exit |
|---|---:|---:|---:|---:|---:|---:|
| tenant0 | 0.184 | 35.877 | 0.0051 | 1.742 | 1.557 | 0 |
| tenant1 | 0.292 | 17.543 | 0.0166 | 3.747 | 3.701 | 0 |
| tenant10 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
| tenant11 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
| tenant12 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
| tenant13 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
| tenant14 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
| tenant15 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
| tenant2 | 0.184 | 11.877 | 0.0155 | 1.990 | 1.805 | 0 |
| tenant3 | 0.180 | 11.877 | 0.0152 | 1.959 | 1.203 | 0 |
| tenant4 | 0.183 | 11.877 | 0.0154 | 0.982 | 0.602 | 0 |
| tenant5 | 0.000 | 0.000 | 55.0000 | 1.016 | 1.203 | 0 |
| tenant6 | 0.076 | 0.076 | 1.0009 | 1.272 | 1.203 | 0 |
| tenant7 | 0.000 | 0.000 | 36.0000 | 0.000 | 0.000 | 0 |
| tenant8 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
| tenant9 | 0.000 | 0.000 | 0.0000 | 0.000 | 0.000 | 0 |
