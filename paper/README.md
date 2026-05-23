# Paper Source

This directory contains the PVLDB-style LaTeX source for the SAKI manuscript.
The root file is `saki_main.tex`; section files live under `../chapters/`.

## Files

- `saki_main.tex`: manuscript root
- `saki_refs.bib`: bibliography
- `acmart.cls`, `ACM-Reference-Format.bst`: local template dependencies

## Compile

From the project root:

```bash
cd paper
SOURCE_DATE_EPOCH=1779235200 latexmk -pdf -interaction=nonstopmode -halt-on-error saki_main.tex
```

If the local environment lacks TeX packages, install them in the usual system
or user-local way before compiling.

## Source Data

The manuscript text and tables are based on the curated summaries under:

- `remote-results/paper_tables/paper_tables.md`
- `remote-results/paper_tables/realistic_big_a_aggregate.md`
- `remote-results/paper_tables/*.json`

This release bundle also includes the selected paper-facing raw JSON files
directly under `../remote-results/`, using the same layout expected by the
aggregation scripts in `../remote/`.

## Important

The draft already reflects the two main result families:

1. Epoch-level `Epoch-Drift16`, reported as a 5-trial aggregate with 95% CI
   from raw trial ids `realistic_big_a` through `realistic_big_e`.
2. Continuous `Runtime-Drift16`, reported as a 5-trial aggregate with runtime
   `RateLimiter::SetBytesPerSecond()` actuation from raw artifacts named
   `embedded_demand2f_16t_*`.

The full remote host directory is not included. Host logs, database
directories, backups, compiled binaries, and private operator notes are
omitted; the included raw JSON files are restricted to the paper-referenced
experiment families.

Submission-facing workload names are intentionally separated from raw artifact
ids. The manuscript uses `Epoch-Drift16`, `Runtime-Drift16`, `Value-4K`,
`Read-3x`, `Fast-Drift`, and `Tenant-8`; raw result filenames keep the older
trial ids for reproducibility.

See `../MANIFEST.md` for the full claim-to-artifact map.
