import json
import os
import sys
import random
import re
import shutil
import subprocess
import time
import pandas as pd
import numpy as np
from pathlib import Path
import utils
import mavenLib
import gradleLib
import warnings
from execution_manager import ExecutionManager
import project_structure_analyzer as psa
import project_dependencies_analyzer as pda
import focal_mutator
from path_context import get_path_context, remove_path_force
from dotenv import load_dotenv

warnings.simplefilter(action='ignore', category=FutureWarning)

load_dotenv()

ExecutionManager.initialize()
supported_test_types = ExecutionManager.get_agents_list()
supported_techniques = ExecutionManager.get_prompts_list()
SMOKE_TEST_CONTEXT = None
PATH_CONTEXT = get_path_context()


def _worker_output_path(*parts):
    base = PATH_CONTEXT.get_output_path()
    return os.path.join(base, *[str(part) for part in parts])


def _worker_project_output_path(project, *parts):
    base = PATH_CONTEXT.get_project_output_path(project)
    return os.path.join(base, *[str(part) for part in parts])


def _worker_compiled_repo_path(project):
    return PATH_CONTEXT.get_compiled_repo_path(project)


def _detect_system():
    if os.name == "nt":
        return "Windows"
    if sys.platform == "darwin":
        return "Darwin"
    return "Linux"


def _is_smoke_test_mode():
    return os.getenv("AGONE_SMOKE_TEST", "0").strip().lower() in {"1", "true", "yes", "on"}


def _default_smoke_test_json():
    return Path(__file__).resolve().parent.parent / "Classes2Test" / "42949039_429.json"


def _get_smoke_test_target_json():
    env_target_json = os.getenv("AGONE_SMOKE_TARGET_JSON")
    if env_target_json:
        return env_target_json
    return str(_default_smoke_test_json())


def _find_nearest_build_directory(project_root, class_relative_path):
    project_root_path = Path(project_root).resolve()
    current_directory = (project_root_path / Path(class_relative_path).parent).resolve()

    while True:
        if (current_directory / "pom.xml").is_file():
            return current_directory, "Maven"
        if (current_directory / "build.gradle").is_file() or (current_directory / "build.gradle.kts").is_file():
            return current_directory, "Gradle"
        if current_directory == project_root_path:
            break
        current_directory = current_directory.parent

    if (project_root_path / "pom.xml").is_file():
        return project_root_path, "Maven"
    if (project_root_path / "build.gradle").is_file() or (project_root_path / "build.gradle.kts").is_file():
        return project_root_path, "Gradle"
    return project_root_path, None


def _materialize_smoke_repo(source_repo_path, compiled_repo_path):
    def _is_materialized(source_path, compiled_path):
        if not compiled_path.exists():
            return False
        try:
            if compiled_path.resolve() == source_path.resolve():
                return False
        except OSError:
            return False

        source_pom = source_path / "pom.xml"
        if source_pom.is_file() and not (compiled_path / "pom.xml").is_file():
            return False

        source_entries = [
            entry.name
            for entry in source_path.iterdir()
            if entry.name not in {".git", "target", "build", ".gradle"}
        ]
        source_entries = sorted(source_entries)
        if not source_entries:
            return compiled_path.exists()

        for entry_name in source_entries[:5]:
            if not (compiled_path / entry_name).exists():
                return False
        return True

    if _is_materialized(source_repo_path, compiled_repo_path):
        return "existing"
    if compiled_repo_path.exists() or compiled_repo_path.is_symlink():
        if not remove_path_force(compiled_repo_path):
            raise OSError(f"Unable to clean worker repository path before copy: {compiled_repo_path}")
        time.sleep(1)

    compiled_repo_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            source_repo_path,
            compiled_repo_path,
            ignore=shutil.ignore_patterns(".git", "target", "build", ".gradle"),
            copy_function=shutil.copyfile,
        )
    except FileExistsError:
        if not remove_path_force(compiled_repo_path):
            raise
        shutil.copytree(
            source_repo_path,
            compiled_repo_path,
            ignore=shutil.ignore_patterns(".git", "target", "build", ".gradle"),
            copy_function=shutil.copyfile,
        )
    return "copy"


def _build_smoke_project_info(project_id, project_root, module_relative_path, project_type):
    module_root = project_root / module_relative_path if module_relative_path else project_root

    if project_type == "Maven":
        root_compiler_version = mavenLib.extract_maven_version(str(project_root)) if (project_root / "pom.xml").is_file() else None
        if (project_root / "pom.xml").is_file():
            root_java_version, root_junit_version, root_testng_version = mavenLib.extract_test_and_java_version_maven(str(project_root))
        else:
            root_java_version, root_junit_version, root_testng_version = (None, None, None)
        module_compiler_version = mavenLib.extract_maven_version(str(module_root)) if (module_root / "pom.xml").is_file() else root_compiler_version
        if (module_root / "pom.xml").is_file():
            module_java_version, module_junit_version, module_testng_version = mavenLib.extract_test_and_java_version_maven(str(module_root))
        else:
            module_java_version, module_junit_version, module_testng_version = (root_java_version, root_junit_version, root_testng_version)
    elif project_type == "Gradle":
        root_compiler_version = gradleLib.extract_gradle_version_from_gradle_wrapper(str(project_id))
        if root_compiler_version is None:
            root_java_version, root_junit_version, root_testng_version, root_compiler_version = gradleLib.extract_info_build_gradle(str(project_root), True)
        else:
            root_java_version, root_junit_version, root_testng_version = gradleLib.extract_info_build_gradle(str(project_root), False)
        module_compiler_version = root_compiler_version
        if (module_root / "build.gradle").is_file() or (module_root / "build.gradle.kts").is_file():
            module_java_version, module_junit_version, module_testng_version, detected_module_compiler = gradleLib.extract_info_build_gradle(str(module_root), True)
            if detected_module_compiler is not None:
                module_compiler_version = detected_module_compiler
        else:
            module_java_version, module_junit_version, module_testng_version = (root_java_version, root_junit_version, root_testng_version)
    else:
        raise ValueError(f"Unsupported smoke test project type: {project_type}")

    if root_java_version is None:
        root_java_version = module_java_version
    if root_junit_version is None:
        root_junit_version = module_junit_version
    if root_testng_version is None:
        root_testng_version = module_testng_version
    if root_compiler_version is None:
        root_compiler_version = module_compiler_version

    project_info = {
        str(project_id): {
            "java_version": root_java_version,
            "testng_version": root_testng_version,
            "junit_version": root_junit_version,
            "type": project_type,
            "version": root_compiler_version,
        }
    }

    if module_relative_path:
        if module_java_version is None:
            module_java_version = root_java_version
        if module_junit_version is None:
            module_junit_version = root_junit_version
        if module_testng_version is None:
            module_testng_version = root_testng_version
        if module_compiler_version is None:
            module_compiler_version = root_compiler_version
        project_info[str(project_id)]["modules"] = [module_relative_path]
        project_info[f"{project_id}_{module_relative_path}"] = {
            "java_version": module_java_version,
            "testng_version": module_testng_version,
            "junit_version": module_junit_version,
            "type": project_type,
            "version": module_compiler_version,
        }

    return project_info


def _prepare_smoke_test_environment(target_json_path=None, refresh_compiled_repo=True):
    target_json = Path(target_json_path or _default_smoke_test_json()).resolve()
    if not target_json.is_file():
        raise FileNotFoundError(f"Smoke test target JSON not found: {target_json}")

    with open(target_json, "r", encoding="utf-8") as target_file:
        record = json.load(target_file)

    project_id = str(record.get("repository", {}).get("repo_id"))
    focal_class = record.get("focal_class", {}).get("identifier")
    test_class = record.get("test_class", {}).get("identifier")
    focal_file = record.get("focal_class", {}).get("file")
    test_file = record.get("test_class", {}).get("file")
    preferred_focal_method = record.get("focal_method", {}).get("identifier")
    preferred_test_case = record.get("test_case", {}).get("identifier")

    if not all([project_id, focal_class, test_class, focal_file, test_file]):
        raise ValueError(f"Incomplete smoke test record in {target_json}")

    agone_root = Path(__file__).resolve().parent
    repo_root = (agone_root.parent / "repos" / project_id).resolve()
    if not repo_root.exists():
        raise FileNotFoundError(f"Repository for smoke test not found: {repo_root}")

    test_file_path = (repo_root / test_file).resolve()
    focal_file_path = (repo_root / focal_file).resolve()
    ast_mapping = psa.map_test_to_focal_methods(str(test_file_path), str(focal_file_path)) or {}
    normalized_mapping = _normalize_ast_mapping(ast_mapping)
    normalized_preferred_test_case = _normalize_method_identifier(preferred_test_case)
    normalized_preferred_focal_method = _normalize_method_identifier(preferred_focal_method)

    if normalized_preferred_test_case and normalized_preferred_focal_method:
        predicted_focal_methods = normalized_mapping.get(normalized_preferred_test_case, [])
        if normalized_preferred_focal_method not in predicted_focal_methods:
            raise ValueError(
                "Smoke target filtered out before pipeline: AST mapping does not confirm "
                f"{normalized_preferred_test_case} -> {normalized_preferred_focal_method}. "
                f"Predicted mapping: {normalized_mapping}"
            )
        test_case = normalized_preferred_test_case
        focal_method = normalized_preferred_focal_method
    else:
        test_case, focal_method = mavenLib._extract_ast_method_pair(
            str(test_file_path),
            str(focal_file_path),
            preferred_test_method=preferred_test_case,
            preferred_focal_method=preferred_focal_method,
        )
    if not test_case or not focal_method:
        raise ValueError(
            "Unable to derive smoke target method pair from AST mapping for "
            f"{test_file} -> {focal_file}. Mapping: {normalized_mapping}"
        )

    build_root, project_type = _find_nearest_build_directory(repo_root, focal_file)
    if project_type is None:
        raise ValueError(f"Unable to detect Maven/Gradle project type for smoke target: {target_json}")

    module_relative_path = os.path.relpath(build_root, repo_root)
    if module_relative_path == ".":
        module_relative_path = None

    output_root = Path(PATH_CONTEXT.get_output_path())
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / project_id).mkdir(parents=True, exist_ok=True)

    classes_df = pd.DataFrame(
        [
            {
                "Project": int(project_id),
                "Focal_Class": focal_class,
                "Test_Class": test_class,
                "Focal_Path": f"repos/{project_id}/{focal_file}",
                "Test_Path": f"repos/{project_id}/{test_file}",
                "Module": module_relative_path,
                "Focal_Method": focal_method,
                "Test_Case": test_case,
                "AST_Focal_Method": focal_method,
                "AST_Test_Method": test_case,
            }
        ]
    )
    classes_df.to_csv(output_root / "classes.csv", index=False)

    repo_materialization = "existing"
    if refresh_compiled_repo:
        compiled_repo_root = Path(PATH_CONTEXT.get_compiled_repo_path(project_id))
        repo_materialization = _materialize_smoke_repo(repo_root, compiled_repo_root)

    project_info = _build_smoke_project_info(project_id, repo_root, module_relative_path, project_type)
    with open(output_root / "project_info.json", "w", encoding="utf-8") as project_info_file:
        json.dump(project_info, project_info_file, indent=2)

    smoke_target = {
        "project": project_id,
        "project_type": project_type,
        "module": module_relative_path,
        "focal_class": focal_class,
        "test_class": test_class,
        "focal_method": focal_method,
        "test_case": test_case,
        "ast_mapping": normalized_mapping,
        "target_json": str(target_json),
        "target_name": f"{test_class}::{test_case} -> {focal_class}.{focal_method}",
        "repo_materialization": repo_materialization,
    }

    with open(output_root / project_id / "smoke_target.json", "w", encoding="utf-8") as smoke_target_file:
        json.dump(smoke_target, smoke_target_file, indent=2)

    return smoke_target


def _get_smoke_test_context():
    global SMOKE_TEST_CONTEXT
    if not _is_smoke_test_mode():
        return None
    if SMOKE_TEST_CONTEXT is None:
        SMOKE_TEST_CONTEXT = _prepare_smoke_test_environment(_get_smoke_test_target_json())
    return SMOKE_TEST_CONTEXT


def _normalize_compiled_path(project, raw_path):
    normalized = PATH_CONTEXT.to_worker_compiled_path(project, raw_path)
    return normalized if normalized is not None else str(raw_path)


def _normalize_method_identifier(method_name):
    if method_name is None:
        return None
    if pd.isna(method_name):
        return None
    normalized = str(method_name).strip()
    if not normalized or normalized == "-" or normalized.lower() == "nan":
        return None
    normalized = normalized.split("(", 1)[0].strip()
    return mavenLib._normalize_method_name(normalized)


def _normalize_ast_mapping(ast_mapping):
    normalized_mapping = {}
    for test_method_name, focal_method_names in (ast_mapping or {}).items():
        normalized_test_method = _normalize_method_identifier(test_method_name)
        if normalized_test_method is None:
            continue
        normalized_focal_methods = []
        for focal_method_name in focal_method_names or []:
            normalized_focal_method = _normalize_method_identifier(focal_method_name)
            if normalized_focal_method is not None:
                normalized_focal_methods.append(normalized_focal_method)
        if normalized_focal_methods:
            normalized_mapping[normalized_test_method] = list(dict.fromkeys(normalized_focal_methods))
    return normalized_mapping


def _write_ast_filter_rejections(project, scope_label, rejected_rows):
    if not rejected_rows:
        return

    output_path = _worker_project_output_path(project, f"ast_filter_rejections_{scope_label}.csv")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    rejection_df = pd.DataFrame(rejected_rows)
    if os.path.exists(output_path):
        try:
            existing_df = pd.read_csv(output_path)
            rejection_df = pd.concat([existing_df, rejection_df], ignore_index=True)
        except Exception:
            pass
    rejection_df.to_csv(output_path, index=False)


def _filter_rows_by_ast_verified_mapping(project_df, project, scope_label="project"):
    if project_df is None or project_df.empty:
        return project_df

    mapping_cache = {}
    rejected_rows = []
    kept_indices = []

    for index, row in project_df.iterrows():
        row_project = row.get("Project", project)
        expected_test_method = _normalize_method_identifier(row.get("AST_Test_Method"))
        if expected_test_method is None:
            expected_test_method = _normalize_method_identifier(row.get("Test_Case"))
        expected_focal_method = _normalize_method_identifier(row.get("AST_Focal_Method"))
        if expected_focal_method is None:
            expected_focal_method = _normalize_method_identifier(row.get("Focal_Method"))

        test_path = _normalize_compiled_path(row_project, row.get("Test_Path"))
        focal_path = _normalize_compiled_path(row_project, row.get("Focal_Path"))
        mapping_key = (str(test_path), str(focal_path))
        predicted_focal_methods = []
        reject_reason = None

        if not (os.path.isfile(test_path) and os.path.isfile(focal_path)):
            reject_reason = "missing_test_or_focal_file"
        elif expected_test_method is None or expected_focal_method is None:
            reject_reason = "missing_expected_test_or_focal_method"
        else:
            if mapping_key not in mapping_cache:
                try:
                    ast_mapping = psa.map_test_to_focal_methods(test_path, focal_path)
                    mapping_cache[mapping_key] = _normalize_ast_mapping(ast_mapping)
                except Exception as ast_error:
                    mapping_cache[mapping_key] = {"__ast_error__": str(ast_error)}
            normalized_mapping = mapping_cache[mapping_key]
            if "__ast_error__" in normalized_mapping:
                reject_reason = f"ast_mapping_error:{normalized_mapping['__ast_error__']}"
            else:
                predicted_focal_methods = normalized_mapping.get(expected_test_method, [])
                if expected_focal_method not in predicted_focal_methods:
                    if expected_test_method not in normalized_mapping:
                        reject_reason = f"ast_missing_test_method:{expected_test_method}"
                    else:
                        reject_reason = (
                            f"ast_focal_mismatch:expected={expected_focal_method};"
                            f"predicted={','.join(predicted_focal_methods) if predicted_focal_methods else '-'}"
                        )

        if reject_reason is None:
            kept_indices.append(index)
            continue

        rejected_rows.append(
            {
                "Project": row_project,
                "Scope": scope_label,
                "Test_Class": row.get("Test_Class"),
                "Focal_Class": row.get("Focal_Class"),
                "Test_Path": row.get("Test_Path"),
                "Focal_Path": row.get("Focal_Path"),
                "Expected_Test_Method": expected_test_method or "-",
                "Expected_Focal_Method": expected_focal_method or "-",
                "Predicted_Focal_Methods": ",".join(predicted_focal_methods) if predicted_focal_methods else "-",
                "Reason": reject_reason,
            }
        )

    if rejected_rows:
        print(
            f"AST pre-filter dropped {len(rejected_rows)} row(s) for project {project} "
            f"(scope={scope_label})."
        )
        for rejected_row in rejected_rows[:3]:
            print(
                "  - "
                f"{rejected_row['Test_Class']}::{rejected_row['Expected_Test_Method']} -> "
                f"{rejected_row['Expected_Focal_Method']} [{rejected_row['Reason']}]"
            )
        _write_ast_filter_rejections(project, scope_label, rejected_rows)

    filtered_df = project_df.loc[kept_indices].copy()
    filtered_df = filtered_df.reset_index(drop=True)
    return filtered_df


def _split_human_baseline(test_types):
    baseline_test_types = ["human"] if "human" in test_types else []
    follow_up_test_types = [test_type for test_type in test_types if test_type != "human"]
    return baseline_test_types, follow_up_test_types


def apply_focal_mutations(project, project_dataframe):
    rng = random.Random(str(project))
    mutation_records = []
    mutated_focal_paths = set()
    focal_backups = {}

    for _, row in project_dataframe.iterrows():
        focal_path = _normalize_compiled_path(project, row["Focal_Path"])
        test_path = _normalize_compiled_path(project, row["Test_Path"])
        if focal_path in mutated_focal_paths or not os.path.isfile(focal_path):
            continue

        target_method = None
        if os.path.isfile(test_path):
            try:
                mapping = psa.map_test_to_focal_methods(test_path, focal_path)
                mapped_methods = sorted({method for methods in mapping.values() for method in methods})
                if mapped_methods:
                    target_method = rng.choice(mapped_methods)
            except Exception:
                target_method = None

        try:
            with open(focal_path, "r", encoding="utf-8") as focal_file:
                focal_backups[focal_path] = focal_file.read()
            mutation_result = focal_mutator.apply_random_mutation(focal_path, target_method=target_method, rng=rng)
            mutation_result["focal_path"] = focal_path
            mutation_result["test_path"] = test_path
            mutation_records.append(mutation_result)
            mutated_focal_paths.add(focal_path)
            print(f"Applied {mutation_result['mutation_type']} mutation to {focal_path}")
        except Exception as e:
            print(f"Skipping mutation for {focal_path}: {e}")

    if mutation_records:
        output_dir = _worker_project_output_path(project)
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "focal_mutations.json"), "w", encoding="utf-8") as mutation_file:
            json.dump(mutation_records, mutation_file, indent=2)
        with open(os.path.join(output_dir, "focal_mutation_backups.json"), "w", encoding="utf-8") as backup_file:
            json.dump(focal_backups, backup_file, indent=2)

    return mutation_records


def restore_focal_mutations(project):
    backup_path = _worker_project_output_path(project, "focal_mutation_backups.json")
    if not os.path.exists(backup_path):
        return
    try:
        with open(backup_path, "r", encoding="utf-8") as backup_file:
            focal_backups = json.load(backup_file)
    except (OSError, json.JSONDecodeError):
        return

    for focal_path, focal_content in focal_backups.items():
        pristine_content = _load_pristine_focal_content_from_git(focal_path) or focal_content
        restore_path = focal_path
        restore_directory = os.path.dirname(restore_path)
        if restore_directory and not os.path.exists(restore_directory):
            continue
        try:
            with open(restore_path, "w", encoding="utf-8") as focal_file:
                focal_file.write(pristine_content)
        except OSError as e:
            print(f"Unable to restore focal file {restore_path}: {e}")


def _load_pristine_focal_content_from_git(focal_path):
    normalized_path = str(focal_path).replace("\\", "/")
    match = re.search(
        r"(?:^|/)(?:compiledrepos(?:/worker_[A-Za-z0-9_-]+)?|repos)/(\d+)/(.*)",
        normalized_path,
    )
    if match is None:
        return None

    project = match.group(1)
    relative_repo_path = match.group(2)
    repo_root = os.path.abspath(os.path.join("repos", project))
    if not os.path.isdir(repo_root):
        return None

    git_command = [
        "git",
        "-c",
        f"safe.directory={repo_root.replace(os.sep, '/')}",
        "-C",
        repo_root,
        "show",
        f"HEAD:{relative_repo_path}",
    ]

    try:
        result = subprocess.run(
            git_command,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except Exception:
        return None

    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def restore_focal_mutations_for_human_baseline(project, baseline_test_types):
    if not baseline_test_types:
        return
    print(f"Restoring focal backups before the human baseline for project {project}")
    restore_focal_mutations(project)

def select_projects_to_process():
    """
    Does the intersection of three sets: projects in the classes.csv file, projects in the project_info.json file, and projects in the compiledrepos directory.
    Returns:
        projects_to_process (Set): the projects resulted from the intersection of the three sets, 'None' if an error occurred
    """
    # Read the classes.csv file into a DataFrame
    classes_csv_path = _worker_output_path("classes.csv")
    try:
        df = pd.read_csv(classes_csv_path)
        projects_in_csv = [str(project) for project in df['Project'].unique().tolist()]
    except Exception as e:
        print(f"Error reading classes.csv: {e}")
        return None

    compiled_root = PATH_CONTEXT.get_compiled_root()
    if os.path.exists(compiled_root):
        projects_in_compiledrepos = [
            entry for entry in os.listdir(compiled_root)
            if str(entry).isdigit()
        ]
    else:
        return None

    project_info_path = _worker_output_path("project_info.json")
    try:
        with open(project_info_path, "r", encoding="utf-8") as project_info_file:
            project_info_data = json.load(project_info_file)
            # Get the list of projects in project_info.json
            projects_in_project_info = list(project_info_data.keys())
    except Exception as e:
        print(f"Error reading project_info.json: {e}")
        return None

    projects_to_process = set(projects_in_csv) & set(projects_in_compiledrepos) & set(projects_in_project_info)
    return projects_to_process

def generate_files(test_types, techniques, execution_override, correct, specific_project=None):
    """
    Starts the AgoneTest.py script.
    It generates the following files: output_agone_classes.csv, output_agone_projects.csv, output_agone_mean.csv, output_agone_mean_filtered.csv, and output_agone_info.txt.
    Parameters:
        test_types (List): the list of test types to execute.
        techniques (List): the list of prompt techniques (for the AI test types) to execute.
        execution_override (bool): True if the user wants to re-execute the projects that have already been processed, False otherwise.
        specific_project (optional): the ID of the project to execute (if the function has to execute only one specific project).
    """
    print("Starting to generate files")
    PATH_CONTEXT.ensure_worker_directories()
    smoke_test_context = _get_smoke_test_context()
    projects_to_process = select_projects_to_process()
    java_directory = os.getenv("JAVA_DIRECTORY")
    if projects_to_process is None:
        print("An error occurred")
        sys.exit(1)
    if smoke_test_context is not None:
        projects_to_process = [str(smoke_test_context["project"])]
    # False if the project selected by the user was not found, True otherwise
    flag_find = False
    # Extract all versions from project_info.json
    project_info_path = _worker_output_path("project_info.json")
    try:
        with open(project_info_path, "r", encoding="utf-8") as project_info_file:
            project_info_data = json.load(project_info_file)
    except Exception as e:
        print(f"Error opening project_info.json: {e}")
        sys.exit(1)

    compatible_projects_evosuite, number_projects_evosuite = calculate_number_projects_evosuite_compatibility(projects_to_process, project_info_data)
    print(f"\nAt least {number_projects_evosuite} projects are compatible for evosuite execution!\n")

    output_agone_classes_path = _worker_output_path("output_agone_classes.csv")
    all_test_types = test_types.copy()
    all_techniques = techniques.copy()
    # Iterate over each project
    for project in projects_to_process:
        if specific_project is not None and project != specific_project:
            continue
        if smoke_test_context is not None and str(project) != str(smoke_test_context["project"]):
            continue
        print(f"\n\n\n-------------------------------------------------------------------")
        if smoke_test_context is not None:
            print(f"SMOKE TEST MODE: Running N=1 for target: {smoke_test_context['target_name']}")
        print(f"PROCESSING PROJECT: '{project}'")
        project_path = _worker_compiled_repo_path(project)
        project_structure_path = os.path.join(project_path, "project_structure.json")
        project_dependencies_path = os.path.join(project_path, "project_dependencies.json")
        if not os.path.isfile(project_structure_path):
            psa.save_project_structure(project_path)
        if not os.path.isfile(project_dependencies_path):
            pda.save_project_dependencies(project_path)
        project_structure = psa.get_structure(project_path)
        project_dependencies = pda.get_structure(project_path)
        flag_find = True

        if not execution_override:
            test_types = set()
            techniques = set()
            for all_test_type in all_test_types:
                if all_test_type == 'human' or all_test_type == 'evosuite':
                    if not verify_if_project_test_type_has_already_been_executed(
                        project,
                        all_test_type,
                        _worker_output_path("output_agone_projects.csv"),
                        None,
                    ):
                        test_types.add(all_test_type)
                else:
                    for all_technique in all_techniques:
                        if not verify_if_project_test_type_has_already_been_executed(
                            project,
                            all_test_type,
                            _worker_output_path("output_agone_projects.csv"),
                            all_technique,
                        ):
                            test_types.add(all_test_type)
                            techniques.add(all_technique)
            clean_previous_execution_files_project(project)
            test_types = list(test_types)
            techniques = list(techniques)
            # Move the 'human' test type to the first position
            if 'human' in test_types:
                test_types.remove('human')
                test_types.insert(0, 'human')
            elif test_types:
                test_types.insert(0, 'human')
            if 'evosuite' in test_types:
                test_types.remove('evosuite')
                test_types.insert(1, 'evosuite')
        else:
            clean_previous_execution_files_project(project)
            test_types = all_test_types
            techniques = all_techniques

        try:
            modules = project_info_data.get(project, {}).get('modules')
            if smoke_test_context is not None:
                modules = None
            if modules:
                for module in modules:
                    name = project + '_' + module
                    type_project = project_info_data.get(name, {}).get('type')
                    compiler_version = project_info_data.get(name, {}).get('version')
                    java_version = project_info_data.get(name, {}).get('java_version')
                    junit_version = project_info_data.get(name, {}).get('junit_version')
                    testng_version = project_info_data.get(name, {}).get('testng_version')
                    result_df_process_module = process_module(module, project, project_path, java_version, junit_version, testng_version, compiler_version, type_project, test_types, techniques, project_structure, project_dependencies, correct)
                    if result_df_process_module is not None:
                        if generate_output_agone_files(output_agone_classes_path, result_df_process_module, all_test_types, all_techniques):
                            print(f"Output agone files updated!")
                        else:
                            print(f"Error generating output CSV files for module '{module}'")
                    else:
                        print(f"Error processing output CSV files for module '{module}'")
                continue

            type_project = project_info_data.get(project, {}).get('type')
            compiler_version = project_info_data.get(project, {}).get('version')
            java_version = project_info_data.get(project, {}).get('java_version')
            junit_version = project_info_data.get(project, {}).get('junit_version')
            testng_version = project_info_data.get(project, {}).get('testng_version')
            has_mockito = utils.verify_mockito(type_project, project_path)

            utils.set_java_home(java_directory, java_version, system)
            # Read the classes.csv file into a DataFrame
            df = pd.read_csv(_worker_output_path("classes.csv"))
            project_df = df[df['Project'].isin([int(project)])] # I get only the rows of the current project
            project_df = utils.remove_missing_files_from_dataframe(project_df)
            project_df = _filter_rows_by_ast_verified_mapping(project_df, project, scope_label="project")
            if project_df.empty:
                print(f"Skipping project {project}: no AST-verified samples remain after pre-filtering.")
                continue
            baseline_test_types, follow_up_test_types = _split_human_baseline(test_types)
            restore_focal_mutations_for_human_baseline(project, baseline_test_types)
            execution_groups = []
            if baseline_test_types:
                execution_groups.append(baseline_test_types)
            if follow_up_test_types:
                execution_groups.append(follow_up_test_types)
            if not execution_groups:
                execution_groups.append(test_types)

            if type_project == 'Maven':
                print(f"\n{project} is a Maven project")
                project_failed = False
                for index, current_test_types in enumerate(execution_groups):
                    if mavenLib.process_maven_project(project, current_test_types, techniques, project_path, project_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, correct, project_structure, project_dependencies) == 0:
                        project_failed = True
                        break
                    if index == 0 and baseline_test_types and follow_up_test_types:
                        apply_focal_mutations(project, project_df)
                if project_failed:
                    continue
            elif type_project == 'Gradle':
                print(f"\n{project} is a Gradle project")
                project_failed = False
                for index, current_test_types in enumerate(execution_groups):
                    if gradleLib.process_gradle_project(project, current_test_types, techniques, project_path, project_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, project_structure, project_dependencies, correct) == 0:
                        project_failed = True
                        break
                    if index == 0 and baseline_test_types and follow_up_test_types:
                        apply_focal_mutations(project, project_df)
                if project_failed:
                    continue

            result_generate_output_df_project, output_csv_path = utils.generate_output_csv_project(project, project_df, test_types, techniques)
            if result_generate_output_df_project is not None:
                print(f'{output_csv_path} saved correctly')
                if generate_output_agone_files(output_agone_classes_path, result_generate_output_df_project, all_test_types, all_techniques):
                    print(f"Output agone files updated!")
                else:
                    print(f"Error generating output CSV files for project '{project}'")
            else:
                print(f"Error processing output CSV files for project '{project}'")

            path_to_input_file = _worker_project_output_path(project, "pathToInputFile.csv")
            if os.path.exists(path_to_input_file):
                try:
                    os.remove(path_to_input_file)
                except Exception as e:
                    print(f"Error deleting pathToInputFile.csv: {e}")
        finally:
            if smoke_test_context is not None:
                restore_focal_mutations(project)

    if not flag_find:
        print(f"Project '{specific_project}' not found")
        return

def generate_output_agone_files(output_agone_classes_path, new_output_agone_classes_df, test_types_user, techniques_user):
    """
    This function generates the output_agone_classes, output_agone_projects, output_agone_mean and output_agone_mean_filtered CSV files. It also generates the output_agone_info.txt file. 
    Parameters:
                output_agone_classes_path: the path of the CSV file containing the information about all the classes processed.
                new_output_agone_classes_df: the dataframe containing updated information about the classes of a new project or module, related to code coverage, mutation coverage, and the number of test smells.
                test_types_user (List): the list containing all the test types that the user wants to execute.
                techniques_user (List): the list containing all the techniques that the user wants to execute.
    Returns:
                : True if all the generations have been executed successfully, False otherwise.
    """
    # Initialize the output_agone_classes dataframe.
    output_agone_classes_df = pd.DataFrame()
    # Retrieve the previous output_agone_classes dataframe if it exists.
    # Concat the previous dataframe with the new one in the output_agone_class_df.
    if os.path.exists(output_agone_classes_path):
        try:
            old_dataframe_output_agone = pd.read_csv(output_agone_classes_path)
            if not new_output_agone_classes_df.empty and not old_dataframe_output_agone.empty:
                output_agone_classes_df = pd.concat([old_dataframe_output_agone, new_output_agone_classes_df], ignore_index=True)
            elif new_output_agone_classes_df.empty and not old_dataframe_output_agone.empty:
                output_agone_classes_df = old_dataframe_output_agone
            else:
                output_agone_classes_df = new_output_agone_classes_df
        except Exception as e:
            try:
                output_agone_classes_df = new_output_agone_classes_df
            except Exception as e:
               print(e)
               return False
    else:
        try:
            output_agone_classes_df = new_output_agone_classes_df
        except Exception as e:
            return False
    # Remove the duplicated rows
    output_agone_classes_df.drop_duplicates(subset={'ID_Focal_Class', 'Generator(LLM/EVOSUITE)', 'Prompt_Technique'}, keep='last', inplace=True)
    output_agone_classes_df.to_csv(output_agone_classes_path, index=False, na_rep="-")
    if (generate_output_agone_projects(output_agone_classes_df) == False):
        return False
    if (generate_output_agone_mean(supported_test_types, supported_techniques, output_agone_classes_df)==False):
        return False
    if (generate_output_agone_info(supported_test_types, supported_techniques, supported_test_types, supported_techniques)==False):
        return False
    if (generate_output_agone_mean_filtered(supported_test_types, supported_techniques) == False):
        return False
    if (generate_lists_projects_classes_filtered(supported_test_types, supported_techniques) == False):
        return False
    return True

def check_project_has_all_test_types(project, test_types, techniques, output_agone_projects_df):
    """
    This function checks if the given project has been executed with all the given test types and techniques.
    Parameters:
                project: the ID of the project to execute
                test_types (List): the list of test types to execute.
                techniques (List): the list of prompt techniques (for the AI test types) to execute. 
                output_agone_projects_df = The DataFrame containing information about the processed projects
    Returns:
                : True if the project has been executed with all the given test types and techniques, False otherwise.
    """
    for test_type in test_types:
            if test_type != 'human' and test_type != 'evosuite': # if AI test type
                for technique in techniques:
                    exists = ((output_agone_projects_df['Project'] == project) & (output_agone_projects_df['Generator(LLM/EVOSUITE)'] == test_type) & (output_agone_projects_df['Prompt_Technique'] == technique)).any()
                    if exists == False:
                        return False      
            else:
                exists = ((output_agone_projects_df['Project'] == project) & (output_agone_projects_df['Generator(LLM/EVOSUITE)'] == test_type)).any()
                if exists == False:
                    return False
    return True

def check_class_has_all_test_types(java_class, test_types, techniques, output_agone_classes_df):
    """
    This function checks if the given java focal class has been executed with all the given test types and techniques.
    By 'executed', it means that the compilation either failed or was successful.
    Parameters:
                java_class (String): string that specifies both the ID of the project and the name of the Java focal clas (format: IDproject_focalClassName).
                test_types (List): the list of test types.
                techniques (List): the list of prompt techniques (for the AI test types).
                output_agone_classes_df (Dataframe  )= dataframe containing information about processed classes.
    Returns:
                : True if the java focal class has been executed with all the given test types and techniques, False otherwise.
    """
    for test_type in test_types:
            if test_type != 'human' and test_type != 'evosuite': # if AI test type
                for technique in techniques:
                    exists = ((output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type) & (output_agone_classes_df['Prompt_Technique'] == technique)).any()
                    if exists == False:
                        return False      
            else:
                exists = ((output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type)).any()
                if exists == False:
                    return False
    return True

def check_class_has_all_compilation_test_types(java_class, test_types, techniques, output_agone_classes_df):
    """
    This function checks if the given java focal class has been executed with all the given test types and techniques.
    By 'executed', it is meant only that the compilation was successful.
    Parameters:
                java_class (String): string that specifies both the ID of the project and the name of the Java focal clas (format: IDproject_focalClassName).
                test_types (List): the list of test types.
                techniques (List): the list of prompt techniques (for the AI test types).
                output_agone_classes_df (Dataframe  )= dataframe containing information about processed classes.
    Returns:
                : True if the java focal class has been executed with all the given test types and techniques, False otherwise.
    """
    for test_type in test_types:
            if test_type != 'human' and test_type != 'evosuite': # if AI test type
                for technique in techniques:
                    exists = ((output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type) & (output_agone_classes_df['Prompt_Technique'] == technique)).any()
                    if exists == False:
                        return False   
                    row = output_agone_classes_df[
                        (output_agone_classes_df['ID_Focal_Class'] == java_class) & 
                        (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type) & 
                        (output_agone_classes_df['Prompt_Technique'] == technique)]
                    if not row.empty:
                        if row.iloc[0]['Compilation'] == 1 or row.iloc[0]['Compilation'] == '1':
                            return True
                    return False
            else:
                exists = ((output_agone_classes_df['ID_Focal_Class'] == java_class) & (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type)).any()
                if exists == False:
                    return False
                row = output_agone_classes_df[
                    (output_agone_classes_df['ID_Focal_Class'] == java_class) & 
                    (output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type)]
                if not row.empty:
                    if row.iloc[0]['Compilation'] == 1 or row.iloc[0]['Compilation'] == '1':
                        return True
                return False
    return True

def generate_lists_projects_classes_filtered(test_types, techniques):
    """
    This function generates CSV files containing only the projects and classes that were executed with the given test types and techniques.
    Parameters:
                test_types (List): the list of test types.
                techniques (List): the list of prompt techniques (for the AI test types).
    Returns:
                (bool): True if the generation has been executed successfully, False if an error occurred.

    """
    initial_check = False
    output_agone_projects_path = _worker_output_path("output_agone_projects.csv")
    output_agone_classes_path = _worker_output_path("output_agone_classes.csv")
    if os.path.exists(output_agone_projects_path) and os.path.exists(output_agone_classes_path):
        output_agone_projects_df = pd.read_csv(output_agone_projects_path)
        output_agone_classes_df = pd.read_csv(output_agone_classes_path)
        if not output_agone_projects_df.empty and not output_agone_projects_df.empty:
            initial_check = True
    if initial_check == False:
        return False
    projects = output_agone_projects_df['Project'].unique()
    projects_set = set()
    classes_set = set()
    java_classes =  output_agone_classes_df['ID_Focal_Class'].unique()
    for project in projects:
        if (check_project_has_all_test_types(project, test_types, techniques, output_agone_projects_df) == True):
            if (check_project_cyclomatic_complexity_and_loc(project, output_agone_projects_df) == True):
                projects_set.add(project)
    for java_class in java_classes:
        if (check_class_has_all_test_types(java_class, test_types, techniques, output_agone_classes_df) == True):
            if (check_class_cyclomatic_complexity_and_loc(java_class, output_agone_classes_df) == True):
                classes_set.add(java_class)
    projects_list_df = pd.DataFrame(list(projects_set),columns=['Project'])
    classes_list_df = pd.DataFrame(list(classes_set), columns=['ID_Focal_Class'])
    try:
        projects_list_path = _worker_output_path("output_agone_projects_filtered.csv")
        classes_list_path = _worker_output_path("output_agone_classes_filtered.csv")

        projects_list_df.to_csv(projects_list_path, index=False)
        classes_list_df.to_csv(classes_list_path, index=False)
        return True
    except Exception as e:
        return False

def check_class_cyclomatic_complexity_and_loc(java_class, output_agone_classes_df):
    """
    This function checks if the given dataframe contains information about the cyclomatic complexity and the loc of the given java class.
    Parameters:
                java_class (String): string that specifies both the ID of the project and the name of the Java focal clas (format: IDproject_focalClassName).
                output_agone_classes_df (Dataframe  )= dataframe containing information about processed classes.
    Returns:
                : True if the given dataframe contains information about the cyclomatic complexity and the loc of the given java class, False otherwise.
    """
    try:
        class_row = output_agone_classes_df[output_agone_classes_df['ID_Focal_Class'] == java_class].iloc[0]
        if class_row['Cyclomatic_Complexity_Focal_Class'] is not None and class_row['Cyclomatic_Complexity_Focal_Class'] != '-' and not pd.isna(class_row['Cyclomatic_Complexity_Focal_Class']):
            if class_row['Lines_Of_Code_Focal_Class'] is not None and class_row['Lines_Of_Code_Focal_Class'] != '-' and not pd.isna(class_row['Lines_Of_Code_Focal_Class']):
                return True
        return False
    except Exception as e:
        return False

def generate_output_agone_mean_filtered(test_types, techniques):
    """
    This function generates the 'output_agone_mean_filtered.csv' file.
    It includes information about all the given test types and techniques, considering only the classes that have been correctly processed with all the specified test types and techniques and for which data on cyclomatic complexity and LOC have been reported.
    The information refers to the mean values of code coverage, strong mutation coverage and number of test smells.
    Parameters:
                test_types (List): the list of test types.
                techniques (List): the list of prompt techniques (for the AI test types).
    Returns:
                (bool): True if the generation has been executed successfully, False if an error occurred.

    """
    output_agone_classes_path = _worker_output_path("output_agone_classes.csv")
    output_agone_classes_df = pd.read_csv(output_agone_classes_path)
    if output_agone_classes_df is None or output_agone_classes_df.empty:
        return True
    output_agone_mean_filtered_path = _worker_output_path("output_agone_mean_filtered.csv")
    java_classes =  output_agone_classes_df['ID_Focal_Class'].unique()
    # All the Java classes that have been executed correctly with all test types (compilation failed inclued), and for which the cyclomatic complexity and LOC are known.
    java_classes_filtered = set()
    # All the Java classes that have been executed correctly with all test types (compilation failed not inclued), and for which the cyclomatic complexity and LOC are known.
    java_classes_filtered_compilation = set()
    for java_class in java_classes:
        if (check_class_has_all_test_types(java_class, test_types, techniques, output_agone_classes_df) == True):
            if (check_class_cyclomatic_complexity_and_loc(java_class, output_agone_classes_df) == True):
                java_classes_filtered.add(java_class)
    for java_class in java_classes_filtered:
        if (check_class_has_all_compilation_test_types(java_class, test_types, techniques, output_agone_classes_df) == True):
               java_classes_filtered_compilation.add(java_class)
        
    output_agone_classes_filtered_df = output_agone_classes_df[output_agone_classes_df['ID_Focal_Class'].isin(java_classes_filtered)].copy()
    output_agone_classes_filtered_df_compilation = output_agone_classes_df[output_agone_classes_df['ID_Focal_Class'].isin(java_classes_filtered_compilation)].copy()

    df_output = pd.DataFrame(columns=[
        'Test_type', 'Prompt_Technique', 'Compilation%', 'Branch_Coverage%',
        'Line_Coverage%', 'Method_Coverage%', 'Mutation_Coverage%',
        'NumberOfMethods', 'Assertion Roulette', 'Conditional Test Logic',
        'Constructor Initialization', 'Default Test', 'EmptyTest',
        'Exception Catching Throwing', 'General Fixture', 'Mystery Guest',
        'Print Statement', 'Redundant Assertion', 'Sensitive Equality',
        'Verbose Test', 'Sleepy Test', 'Eager Test', 'Lazy Test',
        'Duplicate Assert', 'Unknown Test', 'IgnoredTest', 'Resource Optimism',
        'Magic Number Test', 'Dependent Test'
    ])
    for test_type in test_types:
        if test_type == 'human' or test_type == 'evosuite':
            percentage_completition = calculate_compilation(test_type, None, output_agone_classes_filtered_df)
            percentage_branch_coverage = calculate_coverage_mean(test_type, None, 'Branch', output_agone_classes_filtered_df_compilation)
            percentage_line_coverage = calculate_coverage_mean(test_type, None, 'Line', output_agone_classes_filtered_df_compilation)
            percentage_method_coverage = calculate_coverage_mean(test_type, None, 'Method', output_agone_classes_filtered_df_compilation)
            percentage_mutation_coverage = calculate_coverage_mean(test_type, None, 'Mutation', output_agone_classes_filtered_df_compilation)
            test_smell_mean = calculate_test_smell_mean(test_type, None, output_agone_classes_filtered_df_compilation)

            if percentage_completition is None:
                percentage_completition = 'None'
            if percentage_branch_coverage is None:
                percentage_branch_coverage = 'None'
            if percentage_line_coverage is None:
                percentage_line_coverage = 'None'
            if percentage_method_coverage is None:
                percentage_method_coverage = 'None'
            if percentage_mutation_coverage is None:
                percentage_mutation_coverage = 'None'
            if test_smell_mean is None:
                test_smell_mean = 'None'
            test_smell_mean = list(test_smell_mean.values())
            data = [test_type, '-', percentage_completition, percentage_branch_coverage, percentage_line_coverage, percentage_method_coverage, percentage_mutation_coverage]
            data.extend(test_smell_mean)
            df_output.loc[len(df_output)] = data

        else: # if the test type is an AI test type
            for technique in techniques:
                    percentage_completition = calculate_compilation(test_type, technique, output_agone_classes_filtered_df)
                    percentage_branch_coverage = calculate_coverage_mean(test_type, technique, 'Branch', output_agone_classes_filtered_df_compilation)
                    percentage_line_coverage = calculate_coverage_mean(test_type, technique, 'Line', output_agone_classes_filtered_df_compilation)
                    percentage_method_coverage = calculate_coverage_mean(test_type, technique, 'Method', output_agone_classes_filtered_df_compilation)
                    percentage_mutation_coverage = calculate_coverage_mean(test_type, technique, 'Mutation', output_agone_classes_filtered_df_compilation)
                    test_smell_mean = calculate_test_smell_mean(test_type, technique, output_agone_classes_filtered_df_compilation)
                    
                    if percentage_completition is None:
                        percentage_completition = 'None'
                    if percentage_branch_coverage is None:
                        percentage_branch_coverage = 'None'
                    if percentage_line_coverage is None:
                        percentage_line_coverage = 'None'
                    if percentage_method_coverage is None:
                        percentage_method_coverage = 'None'
                    if percentage_mutation_coverage is None:
                        percentage_mutation_coverage = 'None'
                    if test_smell_mean is None:
                        test_smell_mean = 'None'
                    test_smell_mean = list(test_smell_mean.values())
                    data = [test_type, technique, percentage_completition, percentage_branch_coverage, percentage_line_coverage, percentage_method_coverage, percentage_mutation_coverage]
                    data.extend(test_smell_mean)
                    df_output.loc[len(df_output)] = data    
    try:
        df_output.to_csv(output_agone_mean_filtered_path, index=False, na_rep="-")  # Set index=False to exclude row indices in the CSV file
        return True
    except Exception as e:
        return False

def check_project_cyclomatic_complexity_and_loc(project, output_agone_projects_df):
    """
    This function checks if the given dataframe contains information about the cyclomatic complexity and the loc of the given project.
    Parameters:
                project: the ID of the project.
                output_agone_projects_df (F) = The DataFrame containing information about the processed projects.
    Returns:
                : True if the given dataframe contains information about the cyclomatic complexity and the loc of the given project, False otherwise.
    """
    try:
        project_row = output_agone_projects_df[output_agone_projects_df['Project'] == project].iloc[0]
        if project_row['Cyclomatic_Complexity'] is not None and project_row['Cyclomatic_Complexity'] != '-' and not pd.isna(project_row['Cyclomatic_Complexity']):
            if project_row['Lines_Of_Code'] is not None and project_row['Lines_Of_Code'] != '-' and not pd.isna(project_row['Lines_Of_Code']):
                return True
        return False
    except Exception as e:
        return False

def generate_output_agone_info(test_types, techniques, test_types_user, techniques_user):
    """
    This function generates the 'output_agone_info.txt' file. 
    It contains information about the projects that have been processed.
    The information regards the number of projects that have been processed with each test type and technique.
    Parameters:
                test_types (List): the list of all the test types processed.
                techniques (List): the list of all the prompt techniques (for the AI test types) processed.
                test_types_user (List): the list of test types selected by the user.
                techniques_user (List): the list of techniques selected by the user.
    Returns:
                : True if the generation has been executed successfully, False otherwise.
    """
    output_agone_projects_path = _worker_output_path("output_agone_projects.csv")
    output_agone_classes_path = _worker_output_path("output_agone_classes.csv")
    output_agone_info_path = _worker_output_path("output_agone_info.txt")
    try:
        output_agone_projects_df = pd.read_csv(output_agone_projects_path)
        output_agone_classes_df = pd.read_csv(output_agone_classes_path)
        with open(output_agone_info_path, "w") as file:
            file.write("\nPROJECTS\n")
            projects = output_agone_projects_df['Project'].unique()
            num_of_projects_all = len(projects)
            file.write("Total number of projects processed: " + str(num_of_projects_all))
            for test_type in test_types:
                filtered_df_test_type = output_agone_projects_df[output_agone_projects_df['Generator(LLM/EVOSUITE)'] == test_type]
                num_of_projects_test_type = len(filtered_df_test_type['Project'].unique())
                file.write("\nTotal number of projects processed with " + test_type + ": " + str(num_of_projects_test_type))
                if test_type != 'human' and test_type != 'evosuite':  # if AI test type
                    for technique in techniques:
                        filtered_df_technique = filtered_df_test_type[filtered_df_test_type['Prompt_Technique'] == technique]
                        num_of_projects_technique = len(filtered_df_technique['Project'].unique())
                        file.write("\nTotal number of projects processed with " + test_type + " " + technique + ": " + str(num_of_projects_technique))
            num_of_projects_all_test_types = 0
            for project in projects:
                if (check_project_has_all_test_types(project, test_types_user, techniques_user, output_agone_projects_df)) == True:
                    num_of_projects_all_test_types = num_of_projects_all_test_types + 1
            file.write("\nTotal number of projects that have been processed with all the test types and techniques selected by the user: " + str(num_of_projects_all_test_types))
            file.write("\n-------------------------------\n")
            file.write("\nCLASSES\n")
            java_classes = output_agone_classes_df['ID_Focal_Class'].unique()
            num_of_classes_all = len(java_classes)
            file.write("Total number of classes processed: " + str(num_of_classes_all))
            for test_type in test_types:
                filtered_df_test_type = output_agone_classes_df[output_agone_classes_df['Generator(LLM/EVOSUITE)'] == test_type]
                num_of_classes_test_type = len(filtered_df_test_type['ID_Focal_Class'].unique())
                file.write("\nTotal number of classes processed with " + test_type + ": " + str(num_of_classes_test_type))
                if test_type != 'human' and test_type != 'evosuite':  # if AI test type
                    for technique in techniques:
                        filtered_df_technique = filtered_df_test_type[filtered_df_test_type['Prompt_Technique'] == technique]
                        num_of_classes_technique = len(filtered_df_technique['ID_Focal_Class'].unique())
                        file.write("\nTotal number of classes processed with " + test_type + " " + technique + ": " + str(num_of_classes_technique))
            num_of_classes_all_test_types = 0
            for java_class in java_classes:
                if (check_class_has_all_test_types(java_class, test_types_user, techniques_user, output_agone_classes_df)) == True:
                    num_of_classes_all_test_types = num_of_classes_all_test_types + 1
            file.write("\nTotal number of classes that have been processed with all the test types and techniques selected by the user: " + str(num_of_classes_all_test_types))
    except Exception as e:
        return False
    return True

def calculate_compilation(test_type, technique, df):
    """
    This function calculates the percentage of occurrences of '1' values in the compilation column of the given gdataframe.
    Particolarly, it calculates the percentage value for the specified test type and technique.
    Parameters:
                test_type: the type of test for which to calculate the compilation percentage.
                technique: the technique associated with the AI test type, 'None' if the test type is non AI-related.
                df: the dataframe containing information about processed classes.
    Returns:
                percentage_compilation: the percentage of occurrences of '1' values in the compilation column of the dataframe. 'None' if there is no data in the dataframe.
    """
    df_type = df[df['Generator(LLM/EVOSUITE)'] == test_type]
    if technique is not None:
        df_type = df_type[df_type['Prompt_Technique'] == technique]
    counts_type = df_type['Compilation'].value_counts()
    # Convert counts to DataFrame
    counts_type = pd.DataFrame(counts_type)
    if counts_type.empty:
        return None
    # Reset index to make 'Compilation' a column
    counts_type.reset_index(inplace=True)
    # Rename the columns
    counts_type.columns = ['Compilation', 'Count']
    # Calculate the total count of 'Compilation' values where it's either 1 or 0
    total_count = counts_type['Count'].sum()
    # Calculate the count for 'Compilation' value 1
    if '1' in counts_type['Compilation'].values:
        count_compilation_1 = counts_type[counts_type['Compilation'] == '1']['Count'].values[0]
    elif 1 in counts_type['Compilation'].values:
        count_compilation_1 = counts_type[counts_type['Compilation'] == 1]['Count'].values[0]
    else:
        count_compilation_1 = 0
    # Calculate the percentage of 'Compilation' value 1
    percentage_compilation = round((count_compilation_1 / total_count) * 100, 2)
    return percentage_compilation

def calculate_coverage_mean(test_type, technique, criterion, df):
    """
    This function calculates the mean value of the given coverage criterion in the specified dataframe. 
    Particolarly, it calculates the mean for the given test type and technique.
    Parameters:
                test_type: the test type for which to calculate the mean.
                technique: the technique associated with the AI test type, 'None' if the test type is not AI-related.
                criterion: the coverage criterion for which to calculate the mean.
                df: the dataframe containing information about processed classes.

    Returns:
                mean: the mean value of the given coverage criterion in the specified dataframe. 'None' if there is no data about the specified criterion coverage in the dataframe.
    """
    df_type = df[df['Generator(LLM/EVOSUITE)'] == test_type]
    if technique is not None:
        df_type = df_type[df_type['Prompt_Technique'] == technique]        
    df_type.reset_index(drop=True, inplace=True)
    coverage_values = df_type[f'{criterion}_Coverage'].astype(str).tolist()
    sum_coverage = 0
    item_coverage = 0
    for coverage_value in coverage_values:
        if coverage_value is not None and coverage_value != '-' and coverage_value != 'nan' and not pd.isna(coverage_value):
            sum_coverage = sum_coverage + float(coverage_value)
            item_coverage = item_coverage + 1
    if item_coverage == 0:
        return None
    else:
        mean = round(sum_coverage/item_coverage, 2)
        return mean

def calculate_test_smell_mean(test_type, technique, df):
    """
    This function calculates the mean value of the number of test smell occurrences for the given test type, technique, and dataframe.
    Parameters:
                test_type (String): the test type.
                technique (String): the technique.
                df (DataFrame): the dataframe containing the data.
    Returns:
                dict: A dictionary with the mean values for each test smell.
    """
    df_type = df[df['Generator(LLM/EVOSUITE)'] == test_type]
    if technique is not None:
        df_type = df_type[df_type['Prompt_Technique'] == technique]
    df_type.reset_index(drop=True, inplace=True)

    test_smell_columns = [
        'NumberOfMethods', 'Assertion Roulette', 'Conditional Test Logic',
        'Constructor Initialization', 'Default Test', 'EmptyTest',
        'Exception Catching Throwing', 'General Fixture', 'Mystery Guest',
        'Print Statement', 'Redundant Assertion', 'Sensitive Equality',
        'Verbose Test', 'Sleepy Test', 'Eager Test', 'Lazy Test',
        'Duplicate Assert', 'Unknown Test', 'IgnoredTest', 'Resource Optimism',
        'Magic Number Test', 'Dependent Test'
    ]

    test_smell_means = {}
    for column in test_smell_columns:
        values = df_type[column].astype(str).tolist()
        sum_values = 0
        item_count = 0
        for value in values:
            try:
                sum_values += float(value)
                item_count += 1
            except ValueError:
                continue
        if item_count == 0:
            test_smell_means[column] = None
        else:
            test_smell_means[column] = round(sum_values / item_count, 2)

    return test_smell_means

def generate_output_agone_projects(output_agone_classes_df):
    """
    This function generates the 'output_agone_projects.csv' file.
    This file contains data for each project, including mean code coverage, mean strong mutation coverage, total lines of code of the focal classes, mean cyclomatic complexity of the focal classes, and compilation rate for each test type.
    Parameters:
                output_agone_classes_df (Dataframe): dataframe containing data for each test class and each test type, including code coverage, mutation coverage, lines of code of the corresponding focal class, cyclomatic complextity of the corresponding focal class, and compilation rate.
    Returns:
                (bool): True if the generation has been executed successfully, False if an error occurred.
    """
    output_agone_projects_path = _worker_output_path("output_agone_projects.csv")
    if output_agone_classes_df is None or output_agone_classes_df.empty:
        empty_df = pd.DataFrame()
        empty_df.to_csv(output_agone_projects_path, index=False, na_rep="-")  
        return True
    id_focal_classes = output_agone_classes_df['ID_Focal_Class'].unique()
    # Convert the ndarray to a list and convert integers to strings
    id_focal_classes = [str(project) for project in  id_focal_classes.tolist()]
    projects = []
    for id_focal_class in id_focal_classes:
        projects.append(id_focal_class.split('_')[0])
    projects = list(set(projects))
   
    output_agone_projects_df = pd.DataFrame(columns=['Project', 'Cyclomatic_Complexity', 'Lines_Of_Code', 'Generator(LLM/EVOSUITE)', 'Prompt_Technique', 'Compilation%', 'Branch_Coverage%', 'Line_Coverage%', 'Method_Coverage%', 'Mutation_Coverage%',
        'NumberOfMethods', 'Assertion Roulette', 'Conditional Test Logic',
        'Constructor Initialization', 'Default Test', 'EmptyTest',
        'Exception Catching Throwing', 'General Fixture', 'Mystery Guest',
        'Print Statement', 'Redundant Assertion', 'Sensitive Equality',
        'Verbose Test', 'Sleepy Test', 'Eager Test', 'Lazy Test',
        'Duplicate Assert', 'Unknown Test', 'IgnoredTest', 'Resource Optimism',
        'Magic Number Test', 'Dependent Test'])
    for project in projects:
        classes_rows_project = output_agone_classes_df[output_agone_classes_df['ID_Focal_Class'].str.contains(project)]
        test_types = classes_rows_project['Generator(LLM/EVOSUITE)'].unique()

        classes_human_rows_project = classes_rows_project[classes_rows_project['Generator(LLM/EVOSUITE)'] == 'human'].copy()
        classes_human_rows_project['Cyclomatic_Complexity_Focal_Class'] = classes_human_rows_project['Cyclomatic_Complexity_Focal_Class'].replace({'-': np.nan, '-0': np.nan}, regex=True)
        classes_human_rows_project['Cyclomatic_Complexity_Focal_Class'] = pd.to_numeric(classes_human_rows_project['Cyclomatic_Complexity_Focal_Class'], errors='coerce')

        classes_human_rows_project['Lines_Of_Code_Focal_Class'] = classes_human_rows_project['Lines_Of_Code_Focal_Class'].replace({'-': np.nan, '-0': np.nan}, regex=True)
        classes_human_rows_project['Lines_Of_Code_Focal_Class'] = pd.to_numeric(classes_human_rows_project['Lines_Of_Code_Focal_Class'], errors='coerce')
        if classes_human_rows_project['Lines_Of_Code_Focal_Class'].isna().all():
            total_lines_of_code = '-'
        else:
            total_lines_of_code = classes_human_rows_project['Lines_Of_Code_Focal_Class'].sum()
        
        cyclomatic_complexity_values = classes_human_rows_project['Cyclomatic_Complexity_Focal_Class'] 
        sum_cyclomatic_complexity = 0
        item_cyclomatic_complexity = 0
        for cyclomatic_complexity in  cyclomatic_complexity_values:
            if cyclomatic_complexity is not None and cyclomatic_complexity != '-' and cyclomatic_complexity != 'nan' and not np.isnan(cyclomatic_complexity):
                sum_cyclomatic_complexity = sum_cyclomatic_complexity + float(cyclomatic_complexity)
                item_cyclomatic_complexity = item_cyclomatic_complexity + 1
        if item_cyclomatic_complexity == 0:
            cyclomatic_complexity_mean = '-'
        else:
            cyclomatic_complexity_mean = round(sum_cyclomatic_complexity/ item_cyclomatic_complexity, 2)

        for test_type in test_types:
            if test_type == 'human' or test_type == 'evosuite':
                percentage_completition = calculate_compilation(test_type, None, classes_rows_project)
                percentage_branch_coverage = calculate_coverage_mean(test_type, None, 'Branch', classes_rows_project)
                percentage_line_coverage = calculate_coverage_mean(test_type, None, 'Line', classes_rows_project)
                percentage_method_coverage = calculate_coverage_mean(test_type, None, 'Method', classes_rows_project)
                percentage_mutation_coverage = calculate_coverage_mean(test_type, None, 'Mutation', classes_rows_project)
                test_smell_mean = calculate_test_smell_mean(test_type, None, classes_rows_project)
                if percentage_completition is None:
                    percentage_completition = '-'
                if percentage_branch_coverage is None:
                    percentage_branch_coverage = '-'
                if percentage_line_coverage is None:
                    percentage_line_coverage = '-'
                if percentage_method_coverage is None:
                    percentage_method_coverage = '-'
                if test_smell_mean is None:
                    test_smell_mean = '-'
                # transform test_smell_mean into a list
                test_smell_mean = list(test_smell_mean.values())
                output_agone_project_data = [project, cyclomatic_complexity_mean, total_lines_of_code, test_type, '-',  percentage_completition, percentage_branch_coverage, percentage_line_coverage, percentage_method_coverage, percentage_mutation_coverage]
                output_agone_project_data.extend(test_smell_mean)
                output_agone_projects_df.loc[len(output_agone_projects_df)] = output_agone_project_data

            else: # in other words, the test type is an AI test type
                classes_test_type_rows_project = classes_rows_project[classes_rows_project['Generator(LLM/EVOSUITE)'] == test_type]
                techniques = classes_test_type_rows_project['Prompt_Technique'].unique()
                for technique in techniques:
                    percentage_completition = calculate_compilation(test_type, technique, classes_rows_project)
                    percentage_branch_coverage = calculate_coverage_mean(test_type, technique, 'Branch', classes_rows_project)
                    percentage_line_coverage = calculate_coverage_mean(test_type, technique, 'Line', classes_rows_project)
                    percentage_method_coverage = calculate_coverage_mean(test_type, technique, 'Method', classes_rows_project)
                    percentage_mutation_coverage = calculate_coverage_mean(test_type, technique, 'Mutation', classes_rows_project)
                    test_smell_mean = calculate_test_smell_mean(test_type, technique, classes_rows_project)
                    if percentage_completition is None:
                        percentage_completition = '-'
                    if percentage_branch_coverage is None:
                        percentage_branch_coverage = '-'
                    if percentage_line_coverage is None:
                        percentage_line_coverage = '-'
                    if percentage_method_coverage is None:
                        percentage_method_coverage = '-'
                    if test_smell_mean is None:
                        test_smell_mean = '-'
                    test_smell_mean = list(test_smell_mean.values())
                    output_agone_project_data = [project, cyclomatic_complexity_mean, total_lines_of_code, test_type, technique, percentage_completition, percentage_branch_coverage, percentage_line_coverage, percentage_method_coverage, percentage_mutation_coverage]
                    output_agone_project_data.extend(test_smell_mean)
                    output_agone_projects_df.loc[len(output_agone_projects_df)] = output_agone_project_data
    try:
        output_agone_projects_df.to_csv(output_agone_projects_path, index=False, na_rep="-")  # Set index=False to exclude row indices in the CSV file
        return True
    except Exception as e:
        return False

def generate_output_agone_mean(test_types, techniques, output_agone_classes_df):
    """
    This function generates the 'output_agone_mean.csv' file. 
    It contains information about all the given test types and techniques for all the projects in the dataframe.
    The information refers to the mean values of code coverage, strong mutation coverage and the number of test smells.
    Parameters:
                test_types (List): the list of test types to process.
                techniques (List): the list of prompt techniques (for the AI test types) to process. 
                output_agone_classes_df (Dataframe): the dataframe containing information about processed classes.
    Returns:
                (bool): True if the generation has been executed successfully, False if an error occurred.

    """
    output_agone_mean_path = _worker_output_path("output_agone_mean.csv")

    if output_agone_classes_df is None or output_agone_classes_df.empty:
        empty_df = pd.DataFrame()
        empty_df.to_csv(output_agone_mean_path, index=False, na_rep="-")  
        return True
    
    df_output = pd.DataFrame(columns=['Test_type', 'Prompt_Technique', 'Compilation%', 'Branch_Coverage%', 'Line_Coverage%', 'Method_Coverage%', 'Mutation_Coverage%',
        'NumberOfMethods', 'Assertion Roulette', 'Conditional Test Logic',
        'Constructor Initialization', 'Default Test', 'EmptyTest',
        'Exception Catching Throwing', 'General Fixture', 'Mystery Guest',
        'Print Statement', 'Redundant Assertion', 'Sensitive Equality',
        'Verbose Test', 'Sleepy Test', 'Eager Test', 'Lazy Test',
        'Duplicate Assert', 'Unknown Test', 'IgnoredTest', 'Resource Optimism',
        'Magic Number Test', 'Dependent Test'])
    for test_type in test_types:
        if test_type == 'human' or test_type == 'evosuite':
            percentage_completition = calculate_compilation(test_type, None, output_agone_classes_df)
            percentage_branch_coverage = calculate_coverage_mean(test_type, None, 'Branch', output_agone_classes_df)
            percentage_line_coverage = calculate_coverage_mean(test_type, None, 'Line', output_agone_classes_df)
            percentage_method_coverage = calculate_coverage_mean(test_type, None, 'Method', output_agone_classes_df)
            percentage_mutation_coverage = calculate_coverage_mean(test_type, None, 'Mutation', output_agone_classes_df)
            test_smell_mean = calculate_test_smell_mean(test_type, None, output_agone_classes_df)

            if percentage_completition is None:
                percentage_completition = '-'
            if percentage_branch_coverage is None:
                percentage_branch_coverage = '-'
            if percentage_line_coverage is None:
                percentage_line_coverage = '-'
            if percentage_method_coverage is None:
                percentage_method_coverage = '-'
            if percentage_mutation_coverage is None:
                percentage_mutation_coverage = '-'
            if test_smell_mean is None:
                test_smell_mean = '-'
            test_smell_mean = list(test_smell_mean.values())
            data = [test_type, '-', percentage_completition, percentage_branch_coverage, percentage_line_coverage, percentage_method_coverage, percentage_mutation_coverage]
            data.extend(test_smell_mean)
            df_output.loc[len(df_output)] = data

        else: # if the test type is an AI test type
            for technique in techniques:
                    percentage_completition = calculate_compilation(test_type, technique, output_agone_classes_df)
                    percentage_branch_coverage = calculate_coverage_mean(test_type, technique, 'Branch', output_agone_classes_df)
                    percentage_line_coverage = calculate_coverage_mean(test_type, technique, 'Line', output_agone_classes_df)
                    percentage_method_coverage = calculate_coverage_mean(test_type, technique, 'Method', output_agone_classes_df)
                    percentage_mutation_coverage = calculate_coverage_mean(test_type, technique, 'Mutation', output_agone_classes_df)
                    test_smell_mean = calculate_test_smell_mean(test_type, technique, output_agone_classes_df)
                    
                    if percentage_completition is None:
                        percentage_completition = '-'
                    if percentage_branch_coverage is None:
                        percentage_branch_coverage = '-'
                    if percentage_line_coverage is None:
                        percentage_line_coverage = '-'
                    if percentage_method_coverage is None:
                        percentage_method_coverage = '-'
                    if percentage_mutation_coverage is None:
                        percentage_mutation_coverage = '-'
                    if test_smell_mean is None:
                        test_smell_mean = '-'
                    test_smell_mean = list(test_smell_mean.values())
                    data = [test_type, technique, percentage_completition, percentage_branch_coverage, percentage_line_coverage, percentage_method_coverage, percentage_mutation_coverage]
                    data.extend(test_smell_mean)
                    df_output.loc[len(df_output)] = data    
    try:
        df_output.to_csv(output_agone_mean_path, index=False, na_rep="-")  # Set index=False to exclude row indices in the CSV file
        return True
    except Exception as e:
        return False

def ask_user_project_to_execute():
    """
    Asks the user if they want to execute all projects or a specific project.
    Returns:
            project_input_user: the ID of the project selected by the user (if the user wants to execute only one specific project), 'None' if the user wants to execute all projects
    """
    repeat_error = True
    while (repeat_error == True):
        print("1. A specific project")
        print("2. All projects")
        choice = input("What do you want to do? ")
        #choice = '2'
        if choice == '1':
            project_input_user = input("Insert the project id: ")
            repeat_error = False
        elif choice == '2':
            project_input_user = None
            repeat_error = False
        else: 
            print("You entered an invalid value")
    return project_input_user

def process_module(module, project, project_path, java_version, junit_version, testng_version, compiler_version, type_project, test_types, techniques, project_structure, project_dependencies, correct=False):
    """
    Process a single module of a project.
    Parameters:
                module: the name of the module
                project: the ID of the project 
                project_path: the path of the project
                java_version: the version of Java detected in the project
                junit_version: the version of JUnit detected in the project
                testng_version: the version of testNG detected in the project
                compiler_version: the version of Maven/Gradle detected in the project
                type_project: if the project is Maven project or a Gradle project
                test_types: all the test types
                techniques: all the prompt techniques associated with the AI test types
    Returns:
                output_classes_dataframe (Dataframe): the dataframe that includes all the measures about all the test types applied in the module (code coverage, mutation coverage and number of test smells)
                :None if an error occured
                    
    """
    print('\n----')
    java_directory = os.getenv("JAVA_DIRECTORY")
    print(f"Processing project: {project}, module: {module}")
    path = os.path.join(project_path, module)
    has_mockito = utils.verify_mockito(type_project, path)
    utils.set_java_home(java_directory, java_version, system)
    # Read the classes.csv file into a DataFrame
    df = pd.read_csv(_worker_output_path("classes.csv"))
    project_df = df[df['Project'].isin([int(project)])] # I get only the rows of the current project
    module_df = project_df[project_df['Module'].isin([module])] # I get only the rows of the current module
    module_df = utils.remove_missing_files_from_dataframe(module_df)
    module_scope_label = f"module_{str(module).replace('/', '_').replace('\\\\', '_')}"
    module_df = _filter_rows_by_ast_verified_mapping(module_df, project, scope_label=module_scope_label)
    if module_df.empty:
        print(f"Skipping module '{module}' for project {project}: no AST-verified samples remain after pre-filtering.")
        return pd.DataFrame()
    baseline_test_types, follow_up_test_types = _split_human_baseline(test_types)
    restore_focal_mutations_for_human_baseline(project, baseline_test_types)
    execution_groups = []
    if baseline_test_types:
        execution_groups.append(baseline_test_types)
    if follow_up_test_types:
        execution_groups.append(follow_up_test_types)
    if not execution_groups:
        execution_groups.append(test_types)

    if type_project == 'Maven':
        print(f"\n{project}_{module} is a Maven project")
        for index, current_test_types in enumerate(execution_groups):
            if mavenLib.process_maven_module(project, module, current_test_types, techniques, path, project_path, module_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, project_structure, project_dependencies, correct) == 0:
                return None
            if index == 0 and baseline_test_types and follow_up_test_types:
                apply_focal_mutations(project, module_df)
    elif type_project == 'Gradle':
        print(f"\n{project}_{module} is a Gradle project")
        for index, current_test_types in enumerate(execution_groups):
            if gradleLib.process_gradle_module(project, module, current_test_types, techniques, path, module_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, project_structure, project_dependencies, correct) == 0:
                return None
            if index == 0 and baseline_test_types and follow_up_test_types:
                apply_focal_mutations(project, module_df)
    result_generate_output_csv_project, output_csv_path = utils.generate_output_csv_project(project, module_df, test_types, techniques, module) 
    path_to_input_file = _worker_project_output_path(project, "pathToInputFile.csv")
    if os.path.exists(path_to_input_file):
        try:
            os.remove(path_to_input_file)
        except Exception as e:
            print("An error occured while trying to delete pathToInputFile.csv") 
    if result_generate_output_csv_project  is not None:
        print(f'{output_csv_path} saved correctly')
        return result_generate_output_csv_project
    else:
        return None

def ask_user_execution_override():
    """
    Asks the user if they want to execute the projects that have already been executed as well.
    Returns:
                execution_ovveride (bool): True if the user want to execute the project that have already been executed as weel, False otherwise.
    """
    while(True):
        choice = input("Execution override function: Do you want to re-execute the projects (with the relative test types) that have already been processed? (Y/N)  ")
        #choice = 'N'
        if choice == 'Y':
            return True
        elif choice == 'N':
            return False
        else:
            print("You entered an invalid value")

def verify_if_project_test_type_has_already_been_executed(project, test_type, output_agone_projects_path, technique=None):
    """
    Verifies whether the given project has already been executed with the specified test type and technique.
    Parameters:
                project: the ID of the project.
                test type: the test type associated with the project.
                output_agone_projects_path: the path of the dataframe containing information about the projects processed.
                technique (optional): the technique associated with the given AI test type.
    Returns:
                execution (bool): True if the project has already been executed, False otherwise.
    """
    check_df = False
    # check if the project/test type has alrady been processed in output_agone_projects dataframe
    while(True):
        if os.path.exists(output_agone_projects_path):
            try:
                output_agone_projects_df = pd.read_csv(output_agone_projects_path)
                if output_agone_projects_df.empty:
                    check_df = False
                    break
                project = int(project)

                if technique is None: # if the test type is not AI-related
                    exists = ((output_agone_projects_df['Project'] == project) & (output_agone_projects_df['Generator(LLM/EVOSUITE)'] == test_type)).any()
                    if exists == True:
                        check_df = True
                    else:
                        check_df = False
                    break
                      
                else: # if the test type is AI-related
                    exists = ((output_agone_projects_df['Project'] == project ) & (output_agone_projects_df['Generator(LLM/EVOSUITE)'] == test_type) & (output_agone_projects_df['Prompt_Technique'] == technique)).any()
                if exists == True:
                    check_df = True
                else:
                    check_df = False
                break
                      
            except Exception as e:
                print(e)
                check_df = False
                break
    return check_df

def ask_user_clean_all():
    """
    Asks the user if they want to clean up all previous executions. 
    If they confirm, it deletes all the output/project CSV files and resets the `output_agone_classes`, `output_agone_projects`, and `output_agone_mean` files to empty DataFrames.
    Returns:
               clean_up (bool): True if the user want to clean up all previous executions, False otherwise.
    """
    while(True):
        choice = input("Reset function: Do you want to clean up all the previous executions? (Y/N)  ")
        #choice = 'N'
        if choice == 'Y':
            worker_output_root = _worker_output_path()
            if not os.path.isdir(worker_output_root):
                return True
            for project in os.listdir(worker_output_root):
                project_path = os.path.join(worker_output_root, project)
                if os.path.isfile(project_path):
                    continue
                print(f"Cleaning '{project}'")
                for filename in os.listdir(project_path):
                    if filename.endswith('.csv'):
                        os.remove(os.path.join(project_path, filename))
            empty_df = pd.DataFrame()
            output_agone_classes_path = _worker_output_path("output_agone_classes.csv")
            output_agone_projects_path = _worker_output_path("output_agone_projects.csv")
            output_agone_mean_path = _worker_output_path("output_agone_mean.csv")
            output_agone_mean_filtered_path = _worker_output_path("output_agone_mean_filtered.csv")
            output_agone_info_path = _worker_output_path("output_agone_info.txt")
            if os.path.exists(output_agone_classes_path):
                empty_df.to_csv(output_agone_classes_path)
            if os.path.exists(output_agone_projects_path):
                empty_df.to_csv(output_agone_projects_path)
            if os.path.exists(output_agone_mean_path):
                empty_df.to_csv(output_agone_mean_path)
            if os.path.exists(output_agone_mean_filtered_path):
                empty_df.to_csv(output_agone_mean_filtered_path)
            if os.path.exists(output_agone_info_path):
                with open(output_agone_info_path, 'w') as file:
                    file.write('')
            return True
        elif choice == 'N':
            return False
        else:
            print("You entered an invalid value")

def calculate_number_projects_evosuite_compatibility(projects_to_process, project_info_data):
    """
    It calculates and returns the number of projects that are compatible with EvoSuite.
    Parameters:
            projects_to_process (list): The list of projects to be analyzed.
            project_info_data (dict): The JSON-formatted data containing information about the projects.
    Returns:
            number_of_projects (int): The number of projects that are compatible with EvoSuite.
            compatible_projects (list): list of projects that are compatible with EvoSuite execution.

    """
    number_of_project = 0
    compatible_projetcs = set()
    for project in projects_to_process:
        java_version = project_info_data.get(project, {}).get('java_version')
        junit_version = project_info_data.get(project, {}).get('junit_version')
        if junit_version is not None and java_version is not None:
            if junit_version.startswith('4') and ((java_version == '5' or java_version == '1.5') or (java_version == '6' or java_version == '1.6') or (java_version == '7' or java_version == '1.7') or (java_version == '8' or java_version == '1.8')): 
                number_of_project = number_of_project +1
                compatible_projetcs.add(project)
    compatible_projetcs = list(compatible_projetcs)
    return compatible_projetcs, number_of_project

def clean_previous_execution_files_project(project):
    """
    Removes the previous execution files of the given project.
    Parameters:
            project: the ID of the project.
    """
    project_path = _worker_project_output_path(project)
    if os.path.isfile(project_path):
        for filename in os.listdir(project_path):
            if filename.endswith('.csv') and filename is not f"{project}_Output.csv":
                os.remove(os.path.join(project_path, filename))

def ask_to_correct():
    """
    Asks the user if they want to correct the class that not compile.
    Returns:
            True or False.
    """
    while (True):
        choice = input(
            "Do you want to correct the class that eventualy not compile? (Y/N)  ")
        if choice == 'Y':
            return True
        elif choice == 'N':
            return False
        else:
            print("You entered an invalid value")

# Get the system: Windows, Linux or Darwin
system = _detect_system()

def main():
    print("WELCOME TO AGONE TEST!")
    test_types = ExecutionManager.get_agents_list()
    techniques = ExecutionManager.get_prompts_list()
    smoke_test_context = _get_smoke_test_context()
    if smoke_test_context is not None:
        project_user = str(smoke_test_context["project"])
        correct = True
        execution_override = True
        print(f"SMOKE TEST MODE: Running N=1 for target: {smoke_test_context['target_name']}")
        print("SMOKE TEST MODE: Auto-selecting repair mode and execution override.")
    else:
        project_user = ask_user_project_to_execute()
        correct = ask_to_correct()
        if ask_user_clean_all():
            execution_override = True
        else:
            execution_override = ask_user_execution_override()
    generate_files(test_types, techniques, execution_override, correct, project_user)
    print("File processing completed!")

if __name__ == "__main__":
    if not utils.is_admin(system):
        print("This script is running without administrator privileges!")
        print("Please re-run the script with administrator privileges to avoid errors during execution!")
    main()
