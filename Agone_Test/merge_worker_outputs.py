import json
from pathlib import Path

import pandas as pd


def _read_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def main():
    workspace_root = Path(__file__).resolve().parent.parent
    output_root = workspace_root / "output"
    worker_dirs = sorted([path for path in output_root.glob("worker_*") if path.is_dir()])

    if not worker_dirs:
        print("No worker output directories found under output/worker_*")
        return

    classes_frames = []
    merged_project_info = {}
    merged_output_frames = []
    merged_mutation_records = []
    merged_mutation_backups = []

    for worker_dir in worker_dirs:
        worker_name = worker_dir.name

        classes_path = worker_dir / "classes.csv"
        if classes_path.exists():
            try:
                classes_df = pd.read_csv(classes_path)
                classes_df["Worker"] = worker_name
                classes_frames.append(classes_df)
            except Exception:
                pass

        project_info_path = worker_dir / "project_info.json"
        project_info = _read_json(project_info_path, {})
        if isinstance(project_info, dict):
            merged_project_info.update(project_info)

        for output_csv_path in worker_dir.rglob("*_Output.csv"):
            try:
                output_df = pd.read_csv(output_csv_path)
                output_df["Worker"] = worker_name
                output_df["Source_File"] = str(output_csv_path.relative_to(output_root)).replace("\\", "/")
                merged_output_frames.append(output_df)
            except Exception:
                continue

        for mutation_path in worker_dir.rglob("focal_mutations.json"):
            mutation_payload = _read_json(mutation_path, [])
            if not isinstance(mutation_payload, list):
                continue
            project_id = mutation_path.parent.name
            for record in mutation_payload:
                if isinstance(record, dict):
                    next_record = dict(record)
                    next_record.setdefault("worker", worker_name)
                    next_record.setdefault("project", project_id)
                    merged_mutation_records.append(next_record)

        for backup_path in worker_dir.rglob("focal_mutation_backups.json"):
            backup_payload = _read_json(backup_path, {})
            if not isinstance(backup_payload, dict):
                continue
            merged_mutation_backups.append(
                {
                    "worker": worker_name,
                    "project": backup_path.parent.name,
                    "backups": backup_payload,
                }
            )

    if classes_frames:
        merged_classes = pd.concat(classes_frames, ignore_index=True)
        merged_classes.to_csv(output_root / "classes.csv", index=False)
        print(f"Merged classes.csv rows: {len(merged_classes)}")

    if merged_output_frames:
        merged_outputs = pd.concat(merged_output_frames, ignore_index=True)
        merged_outputs.to_csv(output_root / "all_workers_Output.csv", index=False)
        print(f"Merged *_Output.csv rows: {len(merged_outputs)}")

    if merged_project_info:
        (output_root / "project_info.json").write_text(
            json.dumps(merged_project_info, indent=2),
            encoding="utf-8",
        )
        print(f"Merged project_info entries: {len(merged_project_info)}")

    (output_root / "focal_mutations.json").write_text(
        json.dumps(merged_mutation_records, indent=2),
        encoding="utf-8",
    )
    (output_root / "focal_mutation_backups.json").write_text(
        json.dumps(merged_mutation_backups, indent=2),
        encoding="utf-8",
    )
    print(f"Merged focal_mutations records: {len(merged_mutation_records)}")
    print(f"Merged focal_mutation_backups entries: {len(merged_mutation_backups)}")


if __name__ == "__main__":
    main()
