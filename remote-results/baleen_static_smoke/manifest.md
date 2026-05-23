# Baleen Static-Only Smoke Manifest

This directory contains the single Gate-B diagnostic smoke for Baleen segment
`Region6_w83982457`. It does not contain an n=5 run and does not change the
112 MB/s budget contract.

## Verdict

- recommend_n5: `false`
- reason_if_no_n5: `engine_stress_insufficient;completion_ratio_insufficient`
- engine_stress_pass: `false`
- completion_pass: `false`
- correctness_pass: `true`

## Gate-B Metrics

| metric | value |
|---|---:|
| offered_write_mb_s | 89.127 |
| rate_limiter_actual_mb_s | 12.707 |
| completed_logical_write_mb_s | 1.099 |
| compact_output_mb_s | 11.275 |
| pending_compaction_bytes_p95_mib | 10.417 |
| completion_ratio | 0.0123 |
| failed_tenants | 0 |

## Segment And Budget

- segment_id: `Region6_w83982457`
- trace_id: `Region6`
- selected users:
  `312948048, 312948042, 312948050, 312948044, 312948053, 312948047, 312948057, 312948071, 312948041, 312948054, 312948039, 312948043, 312948051, 312948056, 312948045, 312948055`
- budget contract: high/mid/low = `11/7/3 MB/s`, aggregate `112 MB/s`
- policy: `static`

## Remote Command

```bash
ssh <remote-host> 'set -euo pipefail; cd <remote-root>; TAG=baleen_static_smoke_v1 SEGMENT_ID=Region6_w83982457 THREADS=4 PREFILL_THREADS=8 TIMEOUT=900 bash scripts/run_baleen_static_smoke.sh'
```

## Artifact Map

Remote:

- `results/baleen_static_smoke/Region6_w83982457_schedule.json`
- `results/baleen_static_smoke/baleen_static_smoke_Region6_w83982457_summary.json`
- `results/baleen_static_smoke/baleen_static_smoke_Region6_w83982457_summary.md`
- `results/embedded_continuous_static_baleen_static_smoke_v1_segRegion6_w83982457_static.json`
- `logs/baleen_static_smoke/baleen_static_smoke_Region6_w83982457.log`
- `logs/embedded_continuous_static_baleen_static_smoke_v1_segRegion6_w83982457_static/`

Local:

- `remote-results/baleen_static_smoke/results/Region6_w83982457_schedule.json`
- `remote-results/baleen_static_smoke/results/baleen_static_smoke_Region6_w83982457_summary.json`
- `remote-results/baleen_static_smoke/results/baleen_static_smoke_Region6_w83982457_summary.md`
- `remote-results/baleen_static_smoke/results/embedded_continuous_static_baleen_static_smoke_v1_segRegion6_w83982457_static.json`
- `remote-results/baleen_static_smoke/logs/baleen_static_smoke_Region6_w83982457.log`
- `remote-results/baleen_static_smoke/logs/embedded_continuous_static_baleen_static_smoke_v1_segRegion6_w83982457_static/`

## Script Changes Used

- Added `remote/prepare_baleen_smoke_schedule.py`.
- Added `remote/analyze_baleen_static_smoke.py`.
- Added `remote/run_baleen_static_smoke.sh`.
- Reused `remote/audit_trace_family.py` and existing `remote/run_embedded_continuous.py`.

The paper source files were not edited for this smoke.
