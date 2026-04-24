# Agone Pipeline + Classes2Test

This repository contains the Agone pipeline code plus the `Classes2Test` benchmark inputs used to drive it.

The repository is intentionally kept source-focused. Large generated working copies, run outputs, logs, and local paper-writing assets are treated as local artifacts and are not meant to be committed.

## Repository Layout

- `Agone_Test/`: pipeline code, prompts, runners, analysis helpers, and tests.
- `Classes2Test/`: benchmark JSON inputs mapping focal classes to corresponding test classes and cases.
- `tools/`: bundled local tooling used by the pipeline.
- `repos/`: local source-repository clones used as pipeline inputs. Recreated locally and ignored by Git.
- `compiledrepos/`: mutable worker copies created during execution. Generated locally and ignored by Git.
- `output/`: run outputs, metrics, logs, and worker artifacts. Generated locally and ignored by Git.

## Dataset Format

Each file in `Classes2Test/` is one focal-class/test-class mapping.

- Naming convention: `<PROJECT_ID>_<N>.json`
- Top-level sections:
  - `focal_class`
  - `test_class`
  - `test_case`

## Running the Pipeline

Prerequisites:

- Python 3.10+
- Java JDKs configured in `.env`
- Any API keys required for the agents you want to run

Setup:

```bash
pip install -r Agone_Test/requirements.txt
```

- Copy `Agone_Test/envExample` to `.env` and fill in the required Java paths and optional API keys.
- Prepare local source repos under `repos/` using the extraction and cloning helpers in `Agone_Test/extract.py`.

Run:

```bash
python Agone_Test/agone_test.py
```

During execution, the pipeline creates:

- `compiledrepos/worker_<id>/...` for mutable worker copies
- `output/worker_<id>/...` for logs, CSVs, diagnostics, and per-sample artifacts

These directories are intentionally excluded from version control.

## Notes

- `Classes2Test/` is the versioned benchmark input.
- `repos/`, `compiledrepos/`, and `output/` are runtime state.
- `Agone_Test/PIPELINE_WHITEBOX.md` documents the execution flow and worker-safe path model.
