# Baleen Static-Actuatable Supply Addendum

## Verdict

- Baleen main line should stop for the static policy path.
- Reason: static-actuatable supply is insufficient even when aggregate scaled trace supply passes Gate A.
- This addendum does not run RocksDB, does not run n=5, and does not change the 112 MB/s budget contract.

## Eligibility Counts

| check | count |
|---|---:|
| total independent candidates | 146 |
| segment locality pass | 146 |
| static_windows_geq70 >= 6/8 | 0 |
| static_windows_geq60 >= 6/8 | 0 |
| median active write tenants >= 9 | 0 |
| static mean actuatable >= 70 MB/s | 0 |
| all new static eligibility checks pass | 0 |

## Distributions

| metric | min | p50 | p95 | max |
|---|---:|---:|---:|---:|
| static actuatable mean MB/s | 7.000 | 11.325 | 15.660 | 20.588 |
| median active write tenants | 1.0 | 1.5 | 2.5 | 3.0 |

## Top Static-Actuatable Segments

| segment_id | static mean | static >=70 windows | median active tenants | scaled mean | locality | eligible | missing checks |
|---|---:|---:|---:|---:|---:|---|---|
| `Region7_w83984401` | 20.588 | 0 | 2.5 | 202.227 | 0.1470 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83970657` | 18.387 | 0 | 2.5 | 226.603 | 0.1313 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83986934` | 18.375 | 0 | 3.0 | 219.868 | 0.1356 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83983626` | 16.625 | 0 | 2.0 | 184.167 | 0.0544 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83975164` | 16.557 | 0 | 2.5 | 155.341 | 0.0638 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83985165` | 16.506 | 0 | 2.0 | 159.429 | 0.1889 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83966917` | 15.750 | 0 | 3.0 | 133.259 | 0.0744 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83968181` | 15.750 | 0 | 2.5 | 170.210 | 0.1280 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83961735` | 15.391 | 0 | 3.0 | 190.570 | 0.1089 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |
| `Region7_w83987087` | 15.027 | 0 | 2.0 | 151.801 | 0.1105 | False | static_windows_geq70>=6/8, median_active_write_tenants>=9, static_actuatable_mean_mbps>=70 |

## Notes

- Static actuatable supply is computed as `sum_tenants min(7.0, scaled_tenant_write_MBps)` per window.
- Oracle-tiered actuatable supply uses trace-derived per-window tiers with caps high/mid/low = 11/7/3 MB/s.
- Existing aggregate Gate-A fields are carried unchanged; the new gate only diagnoses whether that supply can be actuated by a static all-mid policy.
- The previous Baleen static-smoke summary has a display-only per-window completion-ratio issue in zero-offered windows; total completion ratio and Gate-B verdict are unaffected.

## Next Step Recommendation

- Do not run a second Baleen static smoke from these 146 candidates.
- Treat the Baleen static-policy path as stopped: static-actuatable supply, not aggregate trace supply, is the blocker.
- Consider Tencent next, or run a harness-only synthetic all-mid diagnostic control before any new trace-family smoke.
