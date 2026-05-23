# Sensitivity-Checks Aggregate

Four-point local screening around the `realistic_big` main
configuration (drift slow/fast, pressure low/high). Value-size
sensitivity is deferred and only appears if its result files exist.
These checks are not a full workload survey; they test whether the
main effect survives local perturbations around the main configuration.
The faster-drift point is used to expose the controller boundary,
not as a failure case to hide. The fast-drift variant stresses
early adaptation and then measures recovery once the high set reaches
its terminal stage; it is not a pure 'high drift frequency' setting.

| variant | n | high P99 | high tput | total tput | compact bytes | mid tput | overlap | static stall p99 (us, max) | failed | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--|
| baseline | 5 | -11.7% +/-6.2 | +38.1% +/-3.7 | -1.7% +/-3.1 | -21.0% +/-2.6 | -9.1% +/-3.8 | 3.14/4 | 0 | 0/0 | baseline |
| drift_slow | 2 | -7.7% [-14.7,-0.7] | +37.6% [+34.3,+40.9] | +1.1% [+0.5,+1.7] | -21.3% [-22.8,-19.8] | -7.8% [-8.4,-7.2] | 3.43/4 | 21063 | 0/0 | promote |
| drift_fast | 5 | -6.9% +/-5.0 | +33.9% +/-3.0 | -5.0% +/-4.4 | -19.0% +/-2.6 | -8.5% +/-3.3 | 3.14/4 | 21300 | 0/0 | promote |
| press_low | 2 | -10.0% [-11.6,-8.5] | +33.0% [+30.8,+35.2] | -0.4% [-2.9,+2.0] | -19.0% [-22.5,-15.5] | -9.2% [-15.1,-3.3] | 3.14/4 | 19028 | 0/0 | promote |
| press_high | 5 | -12.9% +/-4.0 | +33.9% +/-1.7 | +0.1% +/-2.6 | -17.6% +/-0.8 | -4.1% +/-6.9 | 3.14/4 | 22981 | 0/0 | promote |

Rendering rules (pre-committed; no post-hoc selection):
- n=2 cells show `mean [min, max]`; no CI/stdev is rendered. Direction
  is the only claim supported by two screening trials.
- n>=3 cells show `mean +/-CI` (Student-t 95% half-width).
- The verdict column is an *engineering* filter that decides which
  variants merit promotion to a five-trial run with confidence
  intervals. It is not a statistical acceptance test.

Verdict labels:
- `promote`: high P99 <= -2% AND high tput >= +5% AND overlap >= 2.5/4 AND not capacity-boundary.
- `partial`: direction correct on >=2 of {high P99, high tput, compact bytes} but borderline.
- `controller-boundary`: high-set overlap < 2.5/4. Controller could not track the high set at this drift speed; reported honestly, not hidden.
- `capacity-boundary`: static failed tenants > 0 OR static-high stall P99 > 2x baseline static-high stall P99. Reported as a capacity/saturation observation, not as evidence of controller regression.
- `no-promote`: none of the headline metrics moves in the expected direction.

Baseline static-high stall P99 mean (across realistic_big_a..e): 20981 us. Capacity-boundary threshold: 41962 us (2x baseline).

Promotion-target selection for `static_autotuned` (pre-committed):
- Eligible set: variants with verdict `promote`.
- Sort key: axis priority (pressure before drift), then lexicographic
  variant name. This rule does NOT depend on observed effect size.
- **Selected target for static_autotuned**: `press_high`.

Wording for the paper:
> These checks are not a full workload survey; they test whether the
> main effect survives local perturbations around the main
> configuration. The faster-drift point is used to expose the
> controller boundary, not as a failure case to hide.
