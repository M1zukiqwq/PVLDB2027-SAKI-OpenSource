# Public Trace Qualification Summary

- generated_at: `2026-05-19T06:27:53.515873+00:00`
- budget_contract: `high/mid/low = 11/7/3 MB/s; 16-tenant aggregate = 112 MB/s`
- verdict: **No current public trace result should be promoted to production-trace validation.**

## Qualification Table

| Trace family | Screen status | RocksDB status | Key diagnostic | Paper use |
|---|---|---|---|---|
| Meta CacheLib kvcache/202401 | 5 selected drifting 16-tenant segments | n=5 trace-shaped replay ran; stress-pass subset n=0 | offered 110.418--142.779 MB/s; completed 6.713--7.346 MB/s; compact 29.048--30.64 MB/s; pending p95 21.183--24.254 MiB | trace-shaped plumbing and tracking sanity check, not production-trace validation |
| Meta Tectonic/Baleen storage_0.1 Region5-7 | 146 independent segments after aggregate supply/drift screen; 146/146 pass segment locality | one static-only diagnostic smoke failed Gate B; no n=5 and no second smoke recommended | static-actuatable mean p50/p95/max 11.325/15.66/20.588 MB/s; median active tenants p50/p95/max 1.5/2.5/3.0 | trace-family qualification negative, not controller comparison |

## Interpretation

- CacheLib validates the replay and tracking plumbing, but all five selected segments fail the engine-stress gate.
- Baleen has aggregate scaled supply, but 0/146 independent candidates have enough static-actuatable supply under the fixed 7 MB/s all-mid per-tenant cap.
- Neither result changes the 112 MB/s budget contract or supports a production-trace performance claim.

## Sources

- `remote-results/cachelib_external_v1b/results/cachelib_external_v1_cachelib_v1b_fixhot_t4_n5_aggregate.json`
- `remote-results/trace_family_audit/baleen_0.1_audit_report.json`
- `remote-results/trace_family_audit/baleen_0.1_static_actuatable_addendum.json`
- `remote-results/baleen_static_smoke/results/baleen_static_smoke_Region6_w83982457_summary.json`
