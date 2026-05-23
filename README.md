# SAKI Paper Artifact Release

This directory is the public-release bundle for the SAKI PVLDB submission.
It contains the finished manuscript PDF, LaTeX source, experiment and
aggregation source, figure-generation scripts, generated figures, paper-facing
summary tables, and the selected per-trial raw JSON files needed to audit the
paper claims.

## Layout

- `paper/`: finished PDF and LaTeX root/source dependencies.
- `chapters/`: manuscript sections referenced by `paper/saki_main.tex`.
- `figures/`: figure script, generated CSV data, and generated PDF/PNG/SVG
  figures used by the paper.
- `remote/`: experiment drivers, analyzers, aggregation scripts, C++ harness,
  and cgroup utility source.
- `remote-results/`: paper summaries plus selected raw per-trial JSON data.
- `tools/verify_artifact.py`: smoke checker for layout, data coverage, paper
  headline values, and figure regeneration.

The raw JSON selection is intentionally narrow: it covers the experiment
families named in the manuscript and the scripts under `remote/`. Host logs,
database directories, compiled binaries, backup files, private operator notes,
and external trace corpora are not included.

## Quick Check

From this directory:

```bash
python3 -m venv /tmp/saki-artifact-venv
. /tmp/saki-artifact-venv/bin/activate
python -m pip install -r requirements.txt
python tools/verify_artifact.py
```

The verifier recompiles the Python scripts, regenerates the paper figures from
`remote-results/paper_tables/*.json`, checks the expected raw-result family
counts, and compares key JSON values with the rounded manuscript numbers.

## Reproduction Environment

The artifact has two intended use modes. The quick check above is a lightweight
claim-audit path: it needs Python and the locked plotting dependencies in
`requirements.txt`, but it does not need RocksDB, Linux cgroups, sudo access, or
the omitted database/log directories. The virtual environment avoids installing
into a system-managed Python. It was verified with Python 3.14.3 and Matplotlib
3.10.9; Python 3.10 or newer is expected to work with the pinned dependency set.

The paper build requires a TeX installation with `latexmk`, `pdflatex`,
`bibtex`, and the standard ACM/AMS/font packages used by `acmart`. The ACM class
and bibliography style files used by the submission are vendored under
`paper/`.

Full experiment reruns are host-dependent and storage-intensive. The reported
trials were run on one Linux machine with 80 logical cores, Ubuntu kernel 6.17,
and a local SSD, with each RocksDB tenant writing to its own data directory on
the same device. Runtime harness builds need `gcc`, `g++` with C++17 support,
and RocksDB 8.9.1 headers/libraries, either from the same Ubuntu `.deb` archives
used for the paper or under `opt/debroot/usr` / `opt/rocksdb`. The cgroup-v2
baseline additionally needs a Linux cgroup-v2 setup and the permissions normally
required to create and throttle cgroups. Full reruns create fresh `build/`,
`data/`, `logs/`, `results/`, and `run/` trees; those host-local artifacts are
not part of this compact public release.

## Rebuilding Figures

```bash
python3 figures/evaluation/create_saki_figures.py
```

Figure data provenance is in `figures/data-manifest.md`.

## Paper Build

```bash
cd paper
SOURCE_DATE_EPOCH=1779235200 latexmk -pdf -interaction=nonstopmode -halt-on-error saki_main.tex
```

The checked-in `paper/saki_main.pdf` is the submission-facing output.

## License

Unless a file states otherwise, this release is provided under Apache-2.0.
The ACM/PVLDB template files and any external trace references remain subject
to their upstream terms.
