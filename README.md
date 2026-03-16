# Classes2Test + AgoneTest
This repository contains:

- The Classes2Test dataset (focal class ↔ test class mappings)
- The AgoneTest benchmarking framework (LLM prompts, runners, plotting)
- The exact outputs used in the paper (CSV summaries + per‑sample JSONs)

## Quick Links

- Results (macro averages): `output/output_agone_mean.csv`
- Results (per class): `output/output_agone_classes.csv`
- Raw records (by project): `output/<PROJECT_ID>/...`
- Dataset (ground truth): `dataset/`
- framework code: `AgoneTest/`

## Repository Layout

- `dataset/`: JSON files mapping each focal class to its corresponding test class/cases. Example: `dataset/100021742/100021742_19.json`.
- `output/`: Reproducibility artifacts and summaries from our runs:
  - `output/output_agone_mean.csv`: macro‑averaged metrics by generator/prompt.
  - `output/output_agone_classes.csv`: per‑class metrics and smells.
  - `output/<PROJECT_ID>/...`: per‑sample JSONs organized by project ID.
- `output.zip`: a zipped archive of the `output/output_agone_mean.csv` and `output/output_agone_classes.csv` for reviewers convenience.
- `AgoneTest/`: benchmark scripts and utilities (prompt sets, execution manager, plotting).

## Results At A Glance

Open the aggregate CSV to inspect macro metrics by generator and prompt technique:

- `output/output_agone_mean.csv`
  - Columns include: `Generator(LLM)`, `Prompt_Technique`, `Compilation`, `Branch_Coverage%`, `Line_Coverage%`, `Method_Coverage%`, `Mutation_Score%`, and test‑smell rates.

Per‑class metrics (useful for detailed analyses or slicing by project/class):

- `output/output_agone_classes.csv`
  - Columns include: `Generator(LLM)`, `Prompt_Technique`, `Compilation`, `Project_ID`, `Class_Under_Test`, coverage/mutation metrics, and per‑smell indicators.

## Dataset Format (Classes2Test)

- Structure: `dataset/<PROJECT_ID>/<PROJECT_ID>_<N>.json`
- Each JSON encodes one focal class, its test class, and at least one test case with code context and metadata.
- Top‑level keys:
  - `focal_class`: identifier, file path, fields, and methods present in the class under test.
  - `test_class`: identifier, file path, and fields for the paired test class.
  - `test_case`: concrete test method metadata and body (identifier, signature, body, invocations).

## Running the framework AgoneTest

Prerequisites:

- Python 3.10+
- Java JDKs (see `AgoneTest/envExample` for versions/paths)
- API keys for any LLMs you plan to run (optional if using only non‑LLM baselines)

Setup:

- Create a `.env` from `AgoneTest/envExample` and set the `JAVA_DIRECTORY`, `JAVA_HOME_*`, and any API keys you intend to use.
- Install Python deps: `pip install -r AgoneTest/requirements.txt`

Run AgoneTest:

- Interactive mode: `python AgoneTest/agone_test.py`
  - Select project(s), choose whether to re‑run existing results, and whether to apply error‑correction.
  - Outputs write into `output/` and include:
    - `output/output_agone_classes.csv`
    - `output/output_agone_mean.csv` 


## How To Use This Repo

- Want the data only? Browse `dataset/` and the per‑sample JSONs in `output/<PROJECT_ID>/`.
- Want the headline results? Open `output/output_agone_mean.csv`.
- Want fine‑grained analysis? Use `output/output_agone_classes.csv` and the plotting scripts in `AgoneTest/`.
- Want to reproduce? Configure `.env`, install deps, and run `AgoneTest/agone_test.py`.

## Notes

- The dataset builds upon the Methods2Test corpus and extends it to class‑level mappings suitable for test generation and evaluation.
- The AgoneTest framework is made available for research. A commercial version may be developed in the future.
