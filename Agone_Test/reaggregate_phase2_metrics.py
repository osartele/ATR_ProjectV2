import argparse
import json
from pathlib import Path

import pandas as pd


BASE_OUTPUT_COLUMNS = [
    "ID_Focal_Class",
    "Cyclomatic_Complexity_Focal_Class",
    "Lines_Of_Code_Focal_Class",
    "Generator(LLM/EVOSUITE)",
    "Prompt_Technique",
    "Branch_Coverage",
    "Line_Coverage",
    "Method_Coverage",
    "Compilation",
    "Mutation_Coverage",
    "Post_Repair_Mutation_Coverage",
    "Mutation_Applied",
    "NumberOfMethods",
    "Assertion Roulette",
    "Conditional Test Logic",
    "Constructor Initialization",
    "Default Test",
    "EmptyTest",
    "Exception Catching Throwing",
    "General Fixture",
    "Mystery Guest",
    "Print Statement",
    "Redundant Assertion",
    "Sensitive Equality",
    "Verbose Test",
    "Sleepy Test",
    "Eager Test",
    "Lazy Test",
    "Duplicate Assert",
    "Unknown Test",
    "IgnoredTest",
    "Resource Optimism",
    "Magic Number Test",
    "Dependent Test",
    "Chance",
    "Total_Prompt_Tokens",
    "Total_Completion_Tokens",
    "Iterations_to_Pass",
    "High_Signal",
    "Signal_Reason",
]

PAIR_COLUMNS_RQ2 = [
    "Sample_Key",
    "human_compilation",
    "iterative_compilation",
    "regenerative_compilation",
    "human_signal_reason",
    "iterative_signal_reason",
    "regenerative_signal_reason",
    "human_Line_Coverage",
    "iterative_Line_Coverage",
    "regenerative_Line_Coverage",
    "iterative_delta_Line_Coverage",
    "regenerative_delta_Line_Coverage",
    "iterative_abs_delta_Line_Coverage",
    "regenerative_abs_delta_Line_Coverage",
    "human_Branch_Coverage",
    "iterative_Branch_Coverage",
    "regenerative_Branch_Coverage",
    "iterative_delta_Branch_Coverage",
    "regenerative_delta_Branch_Coverage",
    "iterative_abs_delta_Branch_Coverage",
    "regenerative_abs_delta_Branch_Coverage",
    "human_Method_Coverage",
    "iterative_Method_Coverage",
    "regenerative_Method_Coverage",
    "iterative_delta_Method_Coverage",
    "regenerative_delta_Method_Coverage",
    "iterative_abs_delta_Method_Coverage",
    "regenerative_abs_delta_Method_Coverage",
]

PAIR_COLUMNS_RQ3 = [
    "Sample_Key",
    "human_mutation_coverage",
    "iterative_mutation_coverage",
    "iterative_delta_mutation",
    "iterative_abs_delta_mutation",
    "iterative_compilation",
    "regenerative_mutation_coverage",
    "regenerative_delta_mutation",
    "regenerative_abs_delta_mutation",
    "regenerative_compilation",
    "human_signal_reason",
    "iterative_signal_reason",
    "regenerative_signal_reason",
]

EXCLUSION_PREFIXES = (
    "no_context_safe_active_mutant",
    "quiet_mutation_after_",
    "skipped_ast",
    "failed",
    "missing_output",
    "missing_prompt_row",
    "no_progress",
)

EXPECTED_PAIRS = [
    ("human", "-"),
    ("codex-cli", "iterative-healing"),
    ("codex-cli", "regenerative-sync"),
]

SMELL_COLUMNS = [
    "Assertion Roulette",
    "Conditional Test Logic",
    "Constructor Initialization",
    "Default Test",
    "EmptyTest",
    "Exception Catching Throwing",
    "General Fixture",
    "Mystery Guest",
    "Print Statement",
    "Redundant Assertion",
    "Sensitive Equality",
    "Verbose Test",
    "Sleepy Test",
    "Eager Test",
    "Lazy Test",
    "Duplicate Assert",
    "Unknown Test",
    "IgnoredTest",
    "Resource Optimism",
    "Magic Number Test",
    "Dependent Test",
]


def to_numeric(value, default=0.0):
    converted = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(converted):
        return default
    return float(converted)


def to_reason(value):
    if value is None:
        return "-"
    text = str(value).strip()
    if text == "" or text.lower() == "nan":
        return "-"
    return text


def is_exclusion_reason(reason):
    text = "" if reason is None else str(reason).strip()
    return text.startswith(EXCLUSION_PREFIXES)


def parse_mutation_family(text):
    if text is None:
        return "unknown"
    value = str(text).strip()
    if value == "" or value.lower() == "nan" or value == "-":
        return "unknown"
    return value.split(":", 1)[0].strip().lower()


def status_to_reason(status):
    if status is None:
        return "missing_output"
    value = str(status).strip().upper()
    if value == "SKIPPED_AST":
        return "skipped_ast"
    if value == "FAILED_NO_ARTIFACT":
        return "failed_no_artifact"
    if value == "FAILED_NO_ROWS":
        return "failed_no_rows"
    if value == "FAILED":
        return "failed"
    if value == "OK":
        return "missing_prompt_row"
    return value.lower() if value else "missing_output"


def read_sample_row(sample_dir):
    sample_row_path = sample_dir / "sample_row.csv"
    if not sample_row_path.exists():
        return {}
    try:
        sample_row_df = pd.read_csv(sample_row_path)
    except Exception:
        return {}
    if sample_row_df.empty:
        return {}
    row = sample_row_df.iloc[0].to_dict()
    return {str(key): row.get(key) for key in row.keys()}


def build_id_focal(sample_key, sample_row, fallback):
    if fallback:
        text = str(fallback).strip()
        if text and text.lower() != "nan":
            return text
    project = sample_row.get("Project")
    focal_class = sample_row.get("Focal_Class")
    focal_method = sample_row.get("Focal_Method")
    if project is None or focal_class is None or focal_method is None:
        return sample_key
    project = str(project).strip()
    focal_class = str(focal_class).strip()
    focal_method = str(focal_method).strip()
    if not project or not focal_class or not focal_method:
        return sample_key
    return f"{project}_{focal_class}::{focal_method}"


def latest_status_lookup(output_root, workers):
    lookup = {}
    for worker in workers:
        progress_path = output_root / worker / "progress.csv"
        if not progress_path.exists():
            continue
        try:
            progress_df = pd.read_csv(progress_path)
        except Exception:
            continue
        if progress_df.empty:
            continue
        progress_df = progress_df.copy()
        progress_df["_Timestamp"] = pd.to_datetime(progress_df.get("Timestamp"), errors="coerce")
        progress_df = progress_df.sort_values(by=["_Timestamp"], kind="stable")
        for _, row in progress_df.iterrows():
            key = str(row.get("Sample_Key", "")).strip()
            if not key:
                continue
            lookup[(worker, key)] = str(row.get("Status", "")).strip()
    return lookup


def make_placeholder_row(sample_key, sample_row, fallback_id_focal, generator, technique, reason):
    id_focal = build_id_focal(sample_key=sample_key, sample_row=sample_row, fallback=fallback_id_focal)
    row = {
        "ID_Focal_Class": id_focal,
        "Cyclomatic_Complexity_Focal_Class": "-",
        "Lines_Of_Code_Focal_Class": "-",
        "Generator(LLM/EVOSUITE)": generator,
        "Prompt_Technique": technique,
        "Branch_Coverage": 0.0,
        "Line_Coverage": 0.0,
        "Method_Coverage": 0.0,
        "Compilation": 0,
        "Mutation_Coverage": 0.0,
        "Post_Repair_Mutation_Coverage": 0.0,
        "Mutation_Applied": "-",
        "NumberOfMethods": 0,
        "Chance": "-",
        "Total_Prompt_Tokens": 0,
        "Total_Completion_Tokens": 0,
        "Iterations_to_Pass": 0,
        "High_Signal": 0,
        "Signal_Reason": reason,
    }
    for smell_col in SMELL_COLUMNS:
        row[smell_col] = 0
    return row


def select_row(sample_df, generator, technique):
    generator_col = sample_df["Generator(LLM/EVOSUITE)"].astype(str).str.strip().str.lower()
    technique_col = sample_df["Prompt_Technique"].astype(str).str.strip().str.lower()
    mask = (generator_col == generator) & (technique_col == technique)
    rows = sample_df.loc[mask]
    if rows.empty and generator == "codex-cli":
        mask = generator_col.str.contains("codex", na=False) & (technique_col == technique)
        rows = sample_df.loc[mask]
    if rows.empty:
        return None
    return rows.iloc[0]


def build_aggregate(output_root, workers):
    status_lookup = latest_status_lookup(output_root=output_root, workers=workers)
    frames = []
    for worker in workers:
        samples_root = output_root / worker / "samples"
        if not samples_root.exists():
            continue

        for sample_dir in sorted([path for path in samples_root.iterdir() if path.is_dir()]):
            sample_key = sample_dir.name
            sample_row = read_sample_row(sample_dir)
            output_files = sorted(
                sample_dir.glob("*_Output.csv"),
                key=lambda p: p.stat().st_mtime,
            )
            sample_df = pd.DataFrame()
            if output_files:
                latest_output = output_files[-1]
                try:
                    sample_df = pd.read_csv(latest_output)
                except Exception:
                    sample_df = pd.DataFrame()
            else:
                latest_output = None

            if sample_df.empty:
                sample_df = pd.DataFrame(columns=BASE_OUTPUT_COLUMNS)

            for column in BASE_OUTPUT_COLUMNS:
                if column not in sample_df.columns:
                    sample_df[column] = pd.NA

            existing_pairs = set(
                (
                    str(row.get("Generator(LLM/EVOSUITE)", "")).strip().lower(),
                    str(row.get("Prompt_Technique", "")).strip().lower(),
                )
                for _, row in sample_df.iterrows()
            )

            fallback_id = sample_df["ID_Focal_Class"].iloc[0] if not sample_df.empty else None
            placeholder_reason = status_to_reason(status_lookup.get((worker, sample_key)))
            placeholder_rows = []
            for generator, technique in EXPECTED_PAIRS:
                if (generator, technique) in existing_pairs:
                    continue
                placeholder_rows.append(
                    make_placeholder_row(
                        sample_key=sample_key,
                        sample_row=sample_row,
                        fallback_id_focal=fallback_id,
                        generator=generator,
                        technique=technique,
                        reason=placeholder_reason,
                    )
                )
            if placeholder_rows:
                placeholder_df = pd.DataFrame(placeholder_rows)
                if sample_df.empty:
                    sample_df = placeholder_df
                else:
                    sample_df = pd.concat([sample_df, placeholder_df], ignore_index=True)

            sample_df["Sample_Key"] = sample_key
            sample_df["Worker"] = worker
            if latest_output is None:
                sample_df["_Source_File"] = f"{worker}/samples/{sample_key}/(synthetic)"
            else:
                sample_df["_Source_File"] = str(latest_output.relative_to(output_root)).replace("\\", "/")
            frames.append(sample_df)

    if not frames:
        raise RuntimeError("No per-sample *_Output.csv files found for selected workers.")

    aggregate_df = pd.concat(frames, ignore_index=True)

    for column in BASE_OUTPUT_COLUMNS:
        if column not in aggregate_df.columns:
            aggregate_df[column] = pd.NA
    for column in ("Sample_Key", "Worker"):
        if column not in aggregate_df.columns:
            aggregate_df[column] = pd.NA

    sort_key = (
        aggregate_df["Generator(LLM/EVOSUITE)"].astype(str).str.lower().map(
            {
                "human": 0,
                "codex-cli": 1,
            }
        ).fillna(9)
    )
    sort_technique = (
        aggregate_df["Prompt_Technique"].astype(str).str.lower().map(
            {
                "-": 0,
                "iterative-healing": 1,
                "regenerative-sync": 2,
            }
        ).fillna(9)
    )
    aggregate_df = aggregate_df.assign(_sort_key=sort_key, _sort_technique=sort_technique)
    aggregate_df = aggregate_df.sort_values(
        by=["Sample_Key", "_sort_key", "_sort_technique", "_Source_File"],
        kind="stable",
    )
    aggregate_df = aggregate_df.drop_duplicates(
        subset=["Sample_Key", "Generator(LLM/EVOSUITE)", "Prompt_Technique"],
        keep="last",
    )
    aggregate_df = aggregate_df.sort_values(
        by=["Sample_Key", "_sort_key", "_sort_technique"],
        kind="stable",
    )

    final_columns = BASE_OUTPUT_COLUMNS + ["Sample_Key", "Worker"]
    aggregate_df = aggregate_df[final_columns].reset_index(drop=True)
    return aggregate_df


def build_pair_tables(aggregate_df):
    rq2_records = []
    family_by_sample = {}

    for sample_key, sample_df in aggregate_df.groupby("Sample_Key", sort=True):
        human_row = select_row(sample_df, "human", "-")
        iterative_row = select_row(sample_df, "codex-cli", "iterative-healing")
        regenerative_row = select_row(sample_df, "codex-cli", "regenerative-sync")

        mutation_family = "unknown"
        for row in (human_row, iterative_row, regenerative_row):
            if row is not None:
                mutation_family = parse_mutation_family(row.get("Mutation_Applied"))
                if mutation_family != "unknown":
                    break
        family_by_sample[sample_key] = mutation_family

        human_line = to_numeric(human_row.get("Line_Coverage"), 0.0) if human_row is not None else 0.0
        iterative_line = to_numeric(iterative_row.get("Line_Coverage"), 0.0) if iterative_row is not None else 0.0
        regenerative_line = to_numeric(regenerative_row.get("Line_Coverage"), 0.0) if regenerative_row is not None else 0.0

        human_branch = to_numeric(human_row.get("Branch_Coverage"), 0.0) if human_row is not None else 0.0
        iterative_branch = to_numeric(iterative_row.get("Branch_Coverage"), 0.0) if iterative_row is not None else 0.0
        regenerative_branch = to_numeric(regenerative_row.get("Branch_Coverage"), 0.0) if regenerative_row is not None else 0.0

        human_method = to_numeric(human_row.get("Method_Coverage"), 0.0) if human_row is not None else 0.0
        iterative_method = to_numeric(iterative_row.get("Method_Coverage"), 0.0) if iterative_row is not None else 0.0
        regenerative_method = to_numeric(regenerative_row.get("Method_Coverage"), 0.0) if regenerative_row is not None else 0.0

        record = {
            "Sample_Key": sample_key,
            "human_compilation": int(to_numeric(human_row.get("Compilation"), 0.0)) if human_row is not None else 0,
            "iterative_compilation": int(to_numeric(iterative_row.get("Compilation"), 0.0)) if iterative_row is not None else 0,
            "regenerative_compilation": int(to_numeric(regenerative_row.get("Compilation"), 0.0)) if regenerative_row is not None else 0,
            "human_signal_reason": to_reason(human_row.get("Signal_Reason")) if human_row is not None else "-",
            "iterative_signal_reason": to_reason(iterative_row.get("Signal_Reason")) if iterative_row is not None else "-",
            "regenerative_signal_reason": to_reason(regenerative_row.get("Signal_Reason")) if regenerative_row is not None else "-",
            "human_Line_Coverage": human_line,
            "iterative_Line_Coverage": iterative_line,
            "regenerative_Line_Coverage": regenerative_line,
            "iterative_delta_Line_Coverage": iterative_line - human_line,
            "regenerative_delta_Line_Coverage": regenerative_line - human_line,
            "iterative_abs_delta_Line_Coverage": abs(iterative_line - human_line),
            "regenerative_abs_delta_Line_Coverage": abs(regenerative_line - human_line),
            "human_Branch_Coverage": human_branch,
            "iterative_Branch_Coverage": iterative_branch,
            "regenerative_Branch_Coverage": regenerative_branch,
            "iterative_delta_Branch_Coverage": iterative_branch - human_branch,
            "regenerative_delta_Branch_Coverage": regenerative_branch - human_branch,
            "iterative_abs_delta_Branch_Coverage": abs(iterative_branch - human_branch),
            "regenerative_abs_delta_Branch_Coverage": abs(regenerative_branch - human_branch),
            "human_Method_Coverage": human_method,
            "iterative_Method_Coverage": iterative_method,
            "regenerative_Method_Coverage": regenerative_method,
            "iterative_delta_Method_Coverage": iterative_method - human_method,
            "regenerative_delta_Method_Coverage": regenerative_method - human_method,
            "iterative_abs_delta_Method_Coverage": abs(iterative_method - human_method),
            "regenerative_abs_delta_Method_Coverage": abs(regenerative_method - human_method),
            "human_mutation_coverage": to_numeric(human_row.get("Mutation_Coverage"), 0.0) if human_row is not None else 0.0,
            "iterative_mutation_coverage": to_numeric(iterative_row.get("Mutation_Coverage"), 0.0) if iterative_row is not None else 0.0,
            "regenerative_mutation_coverage": to_numeric(regenerative_row.get("Mutation_Coverage"), 0.0) if regenerative_row is not None else 0.0,
            "Mutation_Family": mutation_family,
        }
        record["iterative_delta_mutation"] = record["iterative_mutation_coverage"] - record["human_mutation_coverage"]
        record["regenerative_delta_mutation"] = record["regenerative_mutation_coverage"] - record["human_mutation_coverage"]
        record["iterative_abs_delta_mutation"] = abs(record["iterative_delta_mutation"])
        record["regenerative_abs_delta_mutation"] = abs(record["regenerative_delta_mutation"])

        rq2_records.append(record)

    paired_df = pd.DataFrame(rq2_records)
    if paired_df.empty:
        raise RuntimeError("No paired sample rows could be generated from aggregate data.")

    rq2_df = paired_df[PAIR_COLUMNS_RQ2].copy()
    rq3_df = paired_df[PAIR_COLUMNS_RQ3].copy()
    return paired_df, rq2_df, rq3_df


def build_filtered_df(paired_df):
    excluded_mask = paired_df["iterative_signal_reason"].apply(is_exclusion_reason) | paired_df[
        "regenerative_signal_reason"
    ].apply(is_exclusion_reason)
    return paired_df.loc[~excluded_mask].copy(), excluded_mask


def build_mutation_family_distribution(paired_df, filtered_keys):
    all_samples = paired_df.copy()
    all_samples["is_scored"] = all_samples["Sample_Key"].isin(set(filtered_keys))
    all_samples["is_context_unsafe"] = (
        all_samples["iterative_signal_reason"].apply(lambda r: str(r).startswith("no_context_safe_active_mutant"))
        | all_samples["regenerative_signal_reason"].apply(lambda r: str(r).startswith("no_context_safe_active_mutant"))
    )
    all_samples["is_quiet"] = (
        all_samples["iterative_signal_reason"].apply(lambda r: str(r).startswith("quiet_mutation_after_"))
        | all_samples["regenerative_signal_reason"].apply(lambda r: str(r).startswith("quiet_mutation_after_"))
    )

    total_scored = int(all_samples["is_scored"].sum())
    rows = []
    for family, family_df in all_samples.groupby("Mutation_Family", sort=True):
        total = len(family_df)
        scored = int(family_df["is_scored"].sum())
        excluded = total - scored
        excluded_df = family_df.loc[~family_df["is_scored"]]
        excluded_context = int(excluded_df["is_context_unsafe"].sum())
        excluded_quiet = int(excluded_df["is_quiet"].sum())
        rows.append(
            {
                "Mutation_Family": family,
                "Total_Samples": total,
                "Scored_Samples": scored,
                "Excluded_Samples": excluded,
                "Excluded_Context_Unsafe": excluded_context,
                "Excluded_Quiet": excluded_quiet,
                "Scored_Rate_within_Family_pct": round((scored / total) * 100, 1) if total else 0.0,
                "Share_of_All_Scored_pct": round((scored / total_scored) * 100, 1) if total_scored else 0.0,
            }
        )

    summary_row = {
        "Mutation_Family": "ALL",
        "Total_Samples": len(all_samples),
        "Scored_Samples": total_scored,
        "Excluded_Samples": len(all_samples) - total_scored,
        "Excluded_Context_Unsafe": int((~all_samples["is_scored"] & all_samples["is_context_unsafe"]).sum()),
        "Excluded_Quiet": int((~all_samples["is_scored"] & all_samples["is_quiet"]).sum()),
        "Scored_Rate_within_Family_pct": round((total_scored / len(all_samples)) * 100, 1) if len(all_samples) else 0.0,
        "Share_of_All_Scored_pct": 100.0 if total_scored else 0.0,
    }

    distribution_df = pd.DataFrame(rows)
    if not distribution_df.empty:
        distribution_df = distribution_df.sort_values("Mutation_Family", kind="stable")
    distribution_df = pd.concat([distribution_df, pd.DataFrame([summary_row])], ignore_index=True)
    return distribution_df


def count_exclusion_reasons(rq2_df, column_name):
    reasons = rq2_df[column_name].apply(to_reason)
    reasons = reasons[reasons.apply(is_exclusion_reason)]
    counts = reasons.value_counts()
    return {reason: int(count) for reason, count in counts.items()}


def build_summary_json(filtered_rq2_df, rq2_df):
    def rq2_strategy(prefix):
        comp_col = f"{prefix}_compilation"
        line_col = f"{prefix}_delta_Line_Coverage"
        branch_col = f"{prefix}_delta_Branch_Coverage"
        method_col = f"{prefix}_delta_Method_Coverage"

        total = len(filtered_rq2_df)
        compiled = int((filtered_rq2_df[comp_col] == 1).sum())
        return {
            "compilation_rate_pct": (compiled / total) * 100 if total else 0.0,
            "compiled": compiled,
            "total_scored": total,
            "mean_delta_line": filtered_rq2_df[line_col].mean() if total else 0.0,
            "mad_line": filtered_rq2_df[line_col].abs().mean() if total else 0.0,
            "line_ge_0": int((filtered_rq2_df[line_col] >= 0).sum()) if total else 0,
            "mean_delta_branch": filtered_rq2_df[branch_col].mean() if total else 0.0,
            "mad_branch": filtered_rq2_df[branch_col].abs().mean() if total else 0.0,
            "branch_ge_0": int((filtered_rq2_df[branch_col] >= 0).sum()) if total else 0,
            "mean_delta_method": filtered_rq2_df[method_col].mean() if total else 0.0,
            "mad_method": filtered_rq2_df[method_col].abs().mean() if total else 0.0,
            "method_ge_0": int((filtered_rq2_df[method_col] >= 0).sum()) if total else 0,
        }

    def rq3_strategy(prefix):
        comp_col = f"{prefix}_compilation"
        mut_col = f"{prefix}_delta_mutation"
        total = len(filtered_rq2_df)
        compiled = int((filtered_rq2_df[comp_col] == 1).sum())
        return {
            "mean_delta_mutation": filtered_rq2_df[mut_col].mean() if total else 0.0,
            "median_delta_mutation": filtered_rq2_df[mut_col].median() if total else 0.0,
            "mad_mutation": filtered_rq2_df[mut_col].abs().mean() if total else 0.0,
            "mutation_ge_0": int((filtered_rq2_df[mut_col] >= 0).sum()) if total else 0,
            "compiled": compiled,
            "total_scored": total,
        }

    total_samples = len(rq2_df)
    scored_samples = len(filtered_rq2_df)
    excluded_samples = total_samples - scored_samples

    summary = {
        "counts": {
            "total_samples": total_samples,
            "scored_samples": scored_samples,
            "excluded_samples": excluded_samples,
        },
        "exclusions": {
            "iterative_reasons": count_exclusion_reasons(rq2_df, "iterative_signal_reason"),
            "regenerative_reasons": count_exclusion_reasons(rq2_df, "regenerative_signal_reason"),
        },
        "rq2": {
            "iterative": rq2_strategy("iterative"),
            "regenerative": rq2_strategy("regenerative"),
        },
        "rq3": {
            "iterative": rq3_strategy("iterative"),
            "regenerative": rq3_strategy("regenerative"),
        },
    }
    return summary


def main():
    parser = argparse.ArgumentParser(description="Rebuild phase2 aggregate and RQ2/RQ3 metrics from worker outputs.")
    parser.add_argument(
        "--workspace-root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Workspace root containing output/ and Classes2Test/",
    )
    parser.add_argument(
        "--workers",
        nargs="+",
        default=["worker_workerRun2a", "worker_workerRun2b"],
        help="Worker output directories (under output/) to aggregate",
    )
    args = parser.parse_args()

    workspace_root = Path(args.workspace_root).resolve()
    output_root = workspace_root / "output"
    phase2_root = output_root / "phase2_metrics"
    phase2_root.mkdir(parents=True, exist_ok=True)

    aggregate_df = build_aggregate(output_root=output_root, workers=args.workers)
    aggregate_path = phase2_root / "workerRun2_aggregate_output.csv"
    aggregate_df.to_csv(aggregate_path, index=False)

    paired_df, rq2_df, rq3_df = build_pair_tables(aggregate_df)
    filtered_paired_df, excluded_mask = build_filtered_df(paired_df)
    filtered_rq2_df = rq2_df.loc[filtered_paired_df.index].copy()
    filtered_rq3_df = rq3_df.loc[filtered_paired_df.index].copy()

    rq2_path = phase2_root / "rq2_coverage_deviation_vs_human.csv"
    rq2_filtered_path = phase2_root / "rq2_coverage_deviation_vs_human_filtered.csv"
    rq3_path = phase2_root / "rq3_mutation_comparison_vs_human.csv"
    rq3_filtered_path = phase2_root / "rq3_mutation_comparison_vs_human_filtered.csv"
    distribution_path = phase2_root / "mutation_family_distribution_workerRun2.csv"
    summary_path = phase2_root / "rq2_rq3_summary_workerRun2.json"

    rq2_df.to_csv(rq2_path, index=False)
    filtered_rq2_df.to_csv(rq2_filtered_path, index=False)
    rq3_df.to_csv(rq3_path, index=False)
    filtered_rq3_df.to_csv(rq3_filtered_path, index=False)

    filtered_keys = set(filtered_paired_df["Sample_Key"].tolist())
    distribution_df = build_mutation_family_distribution(paired_df=paired_df, filtered_keys=filtered_keys)
    distribution_df.to_csv(distribution_path, index=False)

    summary = build_summary_json(filtered_rq2_df=filtered_paired_df, rq2_df=paired_df)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    total_samples = len(rq2_df)
    scored_samples = len(filtered_rq2_df)
    excluded_samples = int(excluded_mask.sum())
    print(f"Aggregate rows: {len(aggregate_df)}")
    print(f"Samples in paired table: {total_samples}")
    print(f"Scored samples: {scored_samples}")
    print(f"Excluded samples: {excluded_samples}")
    print(f"Wrote: {aggregate_path}")
    print(f"Wrote: {rq2_path}")
    print(f"Wrote: {rq2_filtered_path}")
    print(f"Wrote: {rq3_path}")
    print(f"Wrote: {rq3_filtered_path}")
    print(f"Wrote: {distribution_path}")
    print(f"Wrote: {summary_path}")


if __name__ == "__main__":
    main()
