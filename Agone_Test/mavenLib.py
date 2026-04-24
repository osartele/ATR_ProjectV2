import re
import xml.etree.ElementTree as ET
import os
import subprocess
import time
import json

import pandas as pd
import javalang

import errorCorrection
import focal_mutator
import project_structure_analyzer as psa
import utils
import sys
from path_context import get_path_context

try:
    import psutil
except Exception:
    psutil = None

df_chance = pd.DataFrame(
    columns=[
        'Test_Class',
        'Test_Path',
        'Generator(LLM/EVOSUITE)',
        'Prompt_Technique',
        'Chance',
        'Total_Prompt_Tokens',
        'Total_Completion_Tokens',
        'Iterations_to_Pass',
        'High_Signal',
        'Signal_Reason',
    ]
)

PATH_CONTEXT = get_path_context()


def _worker_output_path(*parts):
    base = PATH_CONTEXT.get_output_path()
    return os.path.join(base, *[str(part) for part in parts])


def _worker_project_output_path(project_id, *parts):
    base = PATH_CONTEXT.get_project_output_path(project_id)
    return os.path.join(base, *[str(part) for part in parts])

MUTATION_RETRY_PRIORITIES = [
    ("logical", "NEGATE_CONDITIONALS", focal_mutator.apply_logical_mutation),
    ("signature", "MATH_PRIMITIVE_RETURNS", focal_mutator.apply_signature_mutation),
    ("exception", "ASSERTION_ERROR_FALLBACK", focal_mutator.apply_exception_mutation),
]

FAILURE_SIGNAL_PATTERNS = [
    re.compile(
        r"java\.lang\.AssertionError:\s*expected:<[^>]*>,\s*but was:<[^>]*>",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"Wanted but not invoked:\s*.+", re.IGNORECASE),
    re.compile(
        r"Arguments are different!\s*Wanted:\s*.+?\s*Actual:\s*.+",
        re.IGNORECASE | re.DOTALL,
    ),
]

COMPILATION_SIGNAL_PATTERN = re.compile(
    r"\[ERROR\]\s+.*?\.java:\[\d+,\d+\]\s+.+",
    re.IGNORECASE,
)

SUREFIRE_FAILURE_PATTERNS = [
    re.compile(r"\bThere are test failures\b", re.IGNORECASE),
    re.compile(r"<<<\s*FAILURE!"),
    re.compile(r"<<<\s*ERROR!"),
    re.compile(
        r"\[ERROR\]\s+Tests run:\s*\d+,\s*Failures:\s*[1-9]\d*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\[ERROR\]\s+Tests run:\s*\d+,\s*Failures:\s*\d+,\s*Errors:\s*[1-9]\d*",
        re.IGNORECASE,
    ),
]

CONTEXT_BOOT_FAILURE_PATTERNS = [
    re.compile(r"Failed to load ApplicationContext", re.IGNORECASE),
    re.compile(r"BeanCreationException", re.IGNORECASE),
    re.compile(r"UnsatisfiedDependencyException", re.IGNORECASE),
    re.compile(r"Error creating bean with name", re.IGNORECASE),
]


def _normalize_method_name(method_name):
    if method_name is None:
        return None
    normalized = str(method_name).strip()
    if not normalized or normalized.lower() == "nan":
        return None
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", normalized) is None:
        return None
    return normalized


def _normalize_compiled_path(project_id, raw_path):
    if raw_path is None:
        return None
    return PATH_CONTEXT.to_worker_compiled_path(project_id, raw_path)


def _extract_ast_method_pair(test_path, focal_path, preferred_test_method=None, preferred_focal_method=None):
    if not test_path or not focal_path:
        return None, None
    if not os.path.isfile(test_path) or not os.path.isfile(focal_path):
        return None, None

    try:
        mapping = psa.map_test_to_focal_methods(test_path, focal_path) or {}
    except Exception:
        return None, None

    preferred_test = _normalize_method_name(preferred_test_method)
    preferred_focal = _normalize_method_name(preferred_focal_method)
    normalized_mapping = {}
    for test_method, focal_methods in mapping.items():
        normalized_test = _normalize_method_name(test_method)
        if normalized_test is None:
            continue
        normalized_focal_methods = []
        for focal_method in focal_methods or []:
            normalized_focal = _normalize_method_name(focal_method)
            if normalized_focal:
                normalized_focal_methods.append(normalized_focal)
        normalized_focal_methods = list(dict.fromkeys(normalized_focal_methods))
        if normalized_focal_methods:
            normalized_mapping[normalized_test] = normalized_focal_methods

    if not normalized_mapping:
        return None, None

    if preferred_test in normalized_mapping:
        candidate_focal_methods = normalized_mapping[preferred_test]
        if preferred_focal in candidate_focal_methods:
            return preferred_test, preferred_focal
        return preferred_test, candidate_focal_methods[0]

    if preferred_focal:
        for candidate_test in sorted(normalized_mapping.keys()):
            if preferred_focal in normalized_mapping[candidate_test]:
                return candidate_test, preferred_focal

    selected_test = sorted(normalized_mapping.keys())[0]
    selected_focal = normalized_mapping[selected_test][0]
    return selected_test, selected_focal


def _infer_target_focal_parameter_count(test_path, target_test_method, target_focal_method):
    normalized_test_method = _normalize_method_name(target_test_method)
    normalized_focal_method = _normalize_method_name(target_focal_method)
    if (
        normalized_test_method is None
        or normalized_focal_method is None
        or not test_path
        or not os.path.isfile(test_path)
    ):
        return None

    try:
        with open(test_path, "r", encoding="utf-8", errors="replace") as test_file:
            test_source = test_file.read()
        parsed_tree = javalang.parse.parse(test_source)
    except Exception:
        return None

    invocation_argument_counts = []
    for class_declaration in getattr(parsed_tree, "types", []) or []:
        if not isinstance(class_declaration, javalang.tree.ClassDeclaration):
            continue
        for method_declaration in class_declaration.methods:
            if method_declaration.name != normalized_test_method:
                continue
            for _, invocation in method_declaration.filter(javalang.tree.MethodInvocation):
                if getattr(invocation, "member", None) != normalized_focal_method:
                    continue
                invocation_argument_counts.append(
                    len(getattr(invocation, "arguments", []) or [])
                )

    if not invocation_argument_counts:
        return None

    # Prefer the most frequent arity if multiple invocations are present.
    frequency_by_count = {}
    for argument_count in invocation_argument_counts:
        frequency_by_count[argument_count] = frequency_by_count.get(argument_count, 0) + 1
    return max(frequency_by_count, key=lambda count: (frequency_by_count[count], -count))


def _extract_ast_scope_from_dataframe(project_dataframe):
    project_df = project_dataframe.copy()
    for _, row in project_df.iterrows():
        project_id = row.get("Project")
        test_path = _normalize_compiled_path(project_id, row.get("Test_Path"))
        focal_path = _normalize_compiled_path(project_id, row.get("Focal_Path"))
        preferred_test = row.get("AST_Test_Method") or row.get("Test_Case")
        preferred_focal = row.get("AST_Focal_Method") or row.get("Focal_Method")
        ast_test_method, ast_focal_method = _extract_ast_method_pair(
            test_path,
            focal_path,
            preferred_test_method=preferred_test,
            preferred_focal_method=preferred_focal,
        )
        if ast_test_method and ast_focal_method:
            return ast_test_method, ast_focal_method
    return None, None


def _extract_method_names_from_java_file(java_file_path):
    if not java_file_path or not os.path.isfile(java_file_path):
        return []
    try:
        with open(java_file_path, "r", encoding="utf-8", errors="ignore") as source_file:
            source_code = source_file.read()
        tree = javalang.parse.parse(source_code)
    except Exception:
        return []

    for type_declaration in getattr(tree, "types", []):
        if isinstance(type_declaration, javalang.tree.ClassDeclaration):
            return [method.name for method in getattr(type_declaration, "methods", [])]
    return []


def _resolve_primary_focal_path(project_dataframe):
    project_df = project_dataframe.copy()
    for _, row in project_df.iterrows():
        focal_path = _normalize_compiled_path(row.get("Project"), row.get("Focal_Path"))
        if focal_path and os.path.isfile(focal_path):
            return focal_path
    return None


def _build_excluded_methods_for_target(project_dataframe, target_focal_method):
    normalized_target_focal_method = _normalize_method_name(target_focal_method)
    if normalized_target_focal_method is None:
        return []
    focal_path = _resolve_primary_focal_path(project_dataframe)
    method_names = _extract_method_names_from_java_file(focal_path)
    return sorted(
        {
            method_name
            for method_name in method_names
            if _normalize_method_name(method_name) and method_name != normalized_target_focal_method
        }
    )


def record_tracking_metrics(
    test_class,
    test_path,
    generator,
    technique,
    chance,
    prompt_tokens,
    completion_tokens,
    iterations_to_pass,
    high_signal="-",
    signal_reason="-",
):
    global df_chance
    df_chance = pd.concat(
        [
            df_chance,
            pd.DataFrame(
                [
                    {
                        'Test_Class': test_class,
                        'Test_Path': test_path,
                        'Generator(LLM/EVOSUITE)': generator,
                        'Prompt_Technique': technique,
                        'Chance': chance,
                        'Total_Prompt_Tokens': prompt_tokens,
                        'Total_Completion_Tokens': completion_tokens,
                        'Iterations_to_Pass': iterations_to_pass,
                        'High_Signal': high_signal,
                        'Signal_Reason': signal_reason,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )


def _get_int_run_setting(setting_name, default_value):
    try:
        return int(utils._get_run_setting(setting_name, default_value))
    except (TypeError, ValueError, AttributeError):
        return int(default_value)


def _get_text_run_setting(setting_name, default_value):
    try:
        setting_value = utils._get_run_setting(setting_name, default_value)
    except AttributeError:
        setting_value = default_value
    return str(setting_value).strip().lower()


def _log_flow_event(path, message):
    print(message)
    try:
        _append_maven_diagnostic(_resolve_maven_diagnostic_log_path(path), message)
    except Exception:
        pass


def _merge_failure_log_context(existing_failure_log, additional_context):
    existing_text = str(existing_failure_log or "").strip()
    additional_text = str(additional_context or "").strip()
    if not additional_text:
        return existing_text

    style_context_block = f"[Iterative Style-Lock Rejection]\n{additional_text}"
    if style_context_block in existing_text:
        return existing_text
    if not existing_text:
        return style_context_block
    return f"{existing_text}\n\n{style_context_block}"


def _resolve_scope_paths_from_dataframe(project_dataframe):
    project_df = project_dataframe.copy()
    if project_df.empty:
        return None, None, None
    row = project_df.iloc[0]
    project_id = row.get("Project")
    focal_path = _normalize_compiled_path(project_id, row.get("Focal_Path"))
    test_path = _normalize_compiled_path(project_id, row.get("Test_Path"))
    return (
        str(project_id).strip() if project_id is not None else None,
        focal_path,
        test_path,
    )


def _load_json_file(file_path, default_value):
    if not os.path.exists(file_path):
        return default_value
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default_value


def _write_json_file(file_path, payload):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8", errors="replace") as handle:
        json.dump(payload, handle, indent=2)


def _load_mutation_backup_content(project_id, focal_path):
    if project_id is None or not focal_path:
        return None
    backups_path = _worker_project_output_path(project_id, "focal_mutation_backups.json")
    backup_map = _load_json_file(backups_path, {})
    if not isinstance(backup_map, dict):
        return None
    normalized_focal_path = str(focal_path).replace("\\", "/")
    if normalized_focal_path in backup_map:
        return backup_map.get(normalized_focal_path)
    for backup_path, backup_content in backup_map.items():
        if str(backup_path).replace("\\", "/") == normalized_focal_path:
            return backup_content
    return None


def _restore_focal_from_backup(project_id, focal_path):
    backup_content = _load_mutation_backup_content(project_id, focal_path)
    if backup_content is None:
        return False, "no focal backup content found"
    try:
        with open(focal_path, "w", encoding="utf-8", errors="replace") as focal_file:
            focal_file.write(str(backup_content))
    except OSError as error:
        return False, f"failed to restore focal backup: {error}"
    return True, ""


def _focal_mutations_file_path(project_id):
    return _worker_project_output_path(project_id, "focal_mutations.json")


def _mutation_attempt_key(mutation_type, mutation_record=None):
    if isinstance(mutation_record, dict):
        explicit_attempt_key = str(mutation_record.get("mutation_attempt_key", "")).strip()
        if explicit_attempt_key:
            return explicit_attempt_key

    normalized_type = str(mutation_type or "").strip()
    if not normalized_type:
        return None

    if normalized_type == "logical" and isinstance(mutation_record, dict):
        try:
            candidate_index = int(mutation_record.get("mutation_candidate_index"))
            return f"logical:{candidate_index}"
        except (TypeError, ValueError):
            return "logical"

    if normalized_type != "exception":
        return normalized_type

    injection_scope = None
    if isinstance(mutation_record, dict):
        scope_candidate = mutation_record.get("injection_scope") or mutation_record.get("mutation_variant")
        injection_scope = str(scope_candidate or "").strip().lower()
    if injection_scope in {"catch_block", "method_entry"}:
        return f"exception:{injection_scope}"
    return "exception"


def _mutation_family_key(mutation_key):
    normalized_key = str(mutation_key or "").strip().lower()
    if not normalized_key:
        return None
    return normalized_key.split(":", 1)[0]


def _ordered_mutation_retry_priorities(preferred_first_family=None):
    priorities = list(MUTATION_RETRY_PRIORITIES or [])
    preferred_family = _mutation_family_key(preferred_first_family)
    if preferred_family is None:
        return priorities

    prioritized = []
    remaining = []
    for priority in priorities:
        mutation_type = priority[0] if priority else None
        if _mutation_family_key(mutation_type) == preferred_family:
            prioritized.append(priority)
        else:
            remaining.append(priority)
    return prioritized + remaining


def _current_mutation_type_for_focal(
    project_id,
    focal_path,
    target_method_name=None,
    target_parameter_count=None,
):
    if project_id is None or not focal_path:
        return None
    records = _load_json_file(_focal_mutations_file_path(project_id), [])
    if not isinstance(records, list):
        return None
    normalized_focal_path = str(focal_path).replace("\\", "/")
    for record in reversed(records):
        if not isinstance(record, dict):
            continue
        if str(record.get("focal_path", "")).replace("\\", "/") == normalized_focal_path:
            if target_method_name:
                record_method = _normalize_method_name(record.get("method_name"))
                if record_method != _normalize_method_name(target_method_name):
                    continue
            if target_parameter_count is not None:
                try:
                    record_parameter_count = int(record.get("method_parameter_count"))
                except (TypeError, ValueError):
                    continue
                if record_parameter_count != target_parameter_count:
                    continue
            mutation_key = _mutation_attempt_key(record.get("mutation_type"), mutation_record=record)
            if mutation_key:
                return mutation_key
    return None


def _upsert_focal_mutation_record(project_id, focal_path, test_path, mutation_record):
    if project_id is None or not focal_path:
        return

    records_path = _focal_mutations_file_path(project_id)
    mutation_records = _load_json_file(records_path, [])
    if not isinstance(mutation_records, list):
        mutation_records = []

    normalized_focal_path = str(focal_path).replace("\\", "/")
    normalized_test_path = str(test_path).replace("\\", "/") if test_path else None
    filtered_records = []
    for record in mutation_records:
        if not isinstance(record, dict):
            continue
        record_focal_path = str(record.get("focal_path", "")).replace("\\", "/")
        record_test_path = str(record.get("test_path", "")).replace("\\", "/")
        if record_focal_path == normalized_focal_path and (
            normalized_test_path is None or record_test_path == normalized_test_path
        ):
            continue
        filtered_records.append(record)

    next_record = dict(mutation_record or {})
    next_record["focal_path"] = normalized_focal_path
    if normalized_test_path is not None:
        next_record["test_path"] = normalized_test_path
    filtered_records.append(next_record)
    _write_json_file(records_path, filtered_records)


def _apply_prioritized_retry_mutation(
    focal_path,
    target_focal_method,
    attempted_mutation_types,
    target_focal_parameter_count=None,
    preferred_first_family=None,
):
    normalized_attempted_keys = {
        str(item).strip()
        for item in (attempted_mutation_types or set())
        if str(item).strip()
    }
    attempted_keys = normalized_attempted_keys
    if isinstance(attempted_mutation_types, set):
        attempted_mutation_types.clear()
        attempted_mutation_types.update(normalized_attempted_keys)
        attempted_keys = attempted_mutation_types

    def _register_attempt(attempt_key):
        normalized_key = str(attempt_key or "").strip()
        if not normalized_key:
            return
        attempted_keys.add(normalized_key)

    failed_attempts = []
    priorities = _ordered_mutation_retry_priorities(preferred_first_family=preferred_first_family)
    for mutation_type, mutation_class, mutation_function in priorities:
        mutation_family = _mutation_family_key(mutation_type)
        if mutation_family == "logical":
            max_logical_variants = 12
            for candidate_index in range(max_logical_variants):
                variant_key = f"logical:{candidate_index}"
                if variant_key in attempted_keys:
                    continue
                try:
                    mutation_result = mutation_function(
                        focal_path,
                        target_method=target_focal_method,
                        target_parameter_count=target_focal_parameter_count,
                        preferred_candidate_index=candidate_index,
                    )
                    mutation_result = dict(mutation_result or {})
                    mutation_result["mutation_priority_class"] = mutation_class
                    mutation_result["mutation_attempt_key"] = variant_key
                    mutation_result.setdefault("mutation_candidate_index", candidate_index)
                    _register_attempt(variant_key)
                    return mutation_result, mutation_type, ""
                except ValueError as error:
                    error_text = str(error or "")
                    if "No logical mutation candidate at index" in error_text:
                        _register_attempt(variant_key)
                        if candidate_index == 0:
                            failed_attempts.append(f"{mutation_type}: {error}")
                        break
                    _register_attempt(variant_key)
                    failed_attempts.append(f"{variant_key}: {error}")
            continue

        if mutation_family == "exception":
            exception_variant_keys = ["exception:catch_block", "exception:method_entry"]
            for variant_key in exception_variant_keys:
                if variant_key in attempted_keys:
                    continue
                preferred_scope = variant_key.split(":", 1)[1]
                try:
                    mutation_result = mutation_function(
                        focal_path,
                        target_method=target_focal_method,
                        target_parameter_count=target_focal_parameter_count,
                        preferred_injection_scope=preferred_scope,
                    )
                    mutation_result = dict(mutation_result or {})
                    mutation_result["mutation_priority_class"] = mutation_class
                    mutation_result["mutation_attempt_key"] = variant_key
                    _register_attempt(variant_key)
                    return mutation_result, mutation_type, ""
                except ValueError as error:
                    _register_attempt(variant_key)
                    failed_attempts.append(f"{variant_key}: {error}")
            continue

        mutation_variant_key = str(mutation_type or mutation_family or "").strip()
        if not mutation_variant_key or mutation_variant_key in attempted_keys:
            continue
        try:
            mutation_result = mutation_function(
                focal_path,
                target_method=target_focal_method,
                target_parameter_count=target_focal_parameter_count,
            )
            mutation_result = dict(mutation_result or {})
            mutation_result["mutation_priority_class"] = mutation_class
            mutation_result["mutation_attempt_key"] = mutation_variant_key
            _register_attempt(mutation_variant_key)
            return mutation_result, mutation_type, ""
        except ValueError as error:
            _register_attempt(mutation_variant_key)
            failed_attempts.append(f"{mutation_variant_key}: {error}")
            continue

    if failed_attempts:
        return None, None, "; ".join(failed_attempts)
    return None, None, "no prioritized mutation strategies remaining for retry"


def _normalize_failure_signal_line(signal_line, max_chars=500):
    normalized_line = re.sub(r"\s+", " ", str(signal_line or "")).strip()
    if not normalized_line:
        return ""
    if len(normalized_line) > max_chars:
        return normalized_line[:max_chars].rstrip() + "..."
    return normalized_line


def _is_context_bootstrap_failure(result_payload):
    if isinstance(result_payload, dict):
        failure_text = "\n".join(
            [
                str(result_payload.get("error_text", "") or ""),
                str(result_payload.get("failure_log", "") or ""),
            ]
        )
    else:
        failure_text = str(result_payload or "")
    if not failure_text.strip():
        return False
    return any(pattern.search(failure_text) for pattern in CONTEXT_BOOT_FAILURE_PATTERNS)


def _extract_concise_failure_signal(failure_log_text):
    failure_text = str(failure_log_text or "")
    if not failure_text.strip():
        return ""

    for pattern in FAILURE_SIGNAL_PATTERNS:
        match = pattern.search(failure_text)
        if match:
            return _normalize_failure_signal_line(match.group(0))

    if "compilation error" in failure_text.lower() or "compilation failure" in failure_text.lower():
        compilation_match = COMPILATION_SIGNAL_PATTERN.search(failure_text)
        if compilation_match:
            return _normalize_failure_signal_line(compilation_match.group(0))

    for line in failure_text.splitlines():
        normalized_line = line.strip()
        if normalized_line.startswith("java.lang."):
            return _normalize_failure_signal_line(normalized_line)
        if "Wanted but not invoked" in normalized_line:
            return _normalize_failure_signal_line(normalized_line)
        if "Arguments are different!" in normalized_line:
            return _normalize_failure_signal_line(normalized_line)
    return ""


def _append_iterative_retry_guidance(failure_log_text):
    base_failure_text = str(failure_log_text or "").strip()
    concise_signal = _extract_concise_failure_signal(base_failure_text)
    if not concise_signal:
        return base_failure_text

    guidance_text = (
        f"Your previous patch resulted in: {concise_signal}. "
        "Adjust the surgical repair to satisfy the existing mock expectations."
    )
    guidance_block = f"[Iterative Retry Guidance]\n{guidance_text}"
    if guidance_block in base_failure_text:
        return base_failure_text
    if not base_failure_text:
        return guidance_block
    return f"{base_failure_text}\n\n{guidance_block}"


def verify_mutation_is_live(
    project,
    maven_execution_path,
    scoped_dataframe,
    system,
    ast_test_method=None,
    ast_focal_method=None,
    focal_path=None,
    test_path=None,
):
    project_id_from_scope, scoped_focal_path, scoped_test_path = _resolve_scope_paths_from_dataframe(scoped_dataframe)
    project_id = str(project).strip() if project is not None else project_id_from_scope
    if project_id is None:
        project_id = project_id_from_scope

    resolved_focal_path = focal_path or scoped_focal_path
    resolved_test_path = test_path or scoped_test_path
    normalized_ast_test_method = _normalize_method_name(ast_test_method)
    normalized_ast_focal_method = _normalize_method_name(ast_focal_method)
    target_focal_parameter_count = _infer_target_focal_parameter_count(
        resolved_test_path,
        normalized_ast_test_method,
        normalized_ast_focal_method,
    )
    if (
        normalized_ast_test_method is not None
        and normalized_ast_focal_method is not None
        and target_focal_parameter_count is not None
    ):
        _log_flow_event(
            maven_execution_path,
            f"[MutationLiveGate] Target overload resolved: "
            f"{normalized_ast_focal_method}/{target_focal_parameter_count} from {normalized_ast_test_method}.",
        )

    if normalized_ast_focal_method is None:
        baseline_result = run_maven_baseline_stage(
            maven_execution_path,
            scoped_dataframe,
            system,
            ast_test_method=normalized_ast_test_method,
            ast_focal_method=normalized_ast_focal_method,
        )
        return {
            "is_live": False,
            "baseline_result": baseline_result,
            "high_signal": 0,
            "signal_reason": "missing_ast_focal_method",
            "attempts_used": 0,
            "attempted_mutation_types": [],
        }

    baseline_result = run_maven_baseline_stage(
        maven_execution_path,
        scoped_dataframe,
        system,
        ast_test_method=normalized_ast_test_method,
        ast_focal_method=normalized_ast_focal_method,
    )
    context_unsafe_active_detected = False
    if not baseline_result.get("ok"):
        if not _is_context_bootstrap_failure(baseline_result):
            return {
                "is_live": True,
                "baseline_result": baseline_result,
                "high_signal": 1,
                "signal_reason": "active_mutation",
                "attempts_used": 0,
                "attempted_mutation_types": [],
            }
        context_unsafe_active_detected = True
        _log_flow_event(
            maven_execution_path,
            "[MutationLiveGate] Initial mutation failed at Spring/context bootstrap; "
            "searching for context-safe active mutation.",
        )

    if not resolved_focal_path or not os.path.isfile(resolved_focal_path):
        return {
            "is_live": False,
            "baseline_result": baseline_result,
            "high_signal": 0,
            "signal_reason": "missing_focal_path_for_retry",
            "attempts_used": 0,
            "attempted_mutation_types": [],
        }

    retries_executed = 0
    attempted_mutation_types = set()
    current_mutation_key = _current_mutation_type_for_focal(
        project_id,
        resolved_focal_path,
        target_method_name=normalized_ast_focal_method,
        target_parameter_count=target_focal_parameter_count,
    )
    if current_mutation_key:
        attempted_mutation_types.add(current_mutation_key)
    preferred_family = _mutation_family_key(current_mutation_key)

    _log_flow_event(
        maven_execution_path,
        f"[MutationLiveGate] Quiet mutation detected for focal={resolved_focal_path}; "
        "starting prioritized retries with exhaustive family traversal.",
    )
    while True:
        retry_number = retries_executed + 1
        restored, restore_reason = _restore_focal_from_backup(project_id, resolved_focal_path)
        if not restored:
            _log_flow_event(
                maven_execution_path,
                f"[MutationLiveGate] Retry {retry_number} aborted: {restore_reason}.",
            )
            break

        mutation_result, mutation_type, mutation_error = _apply_prioritized_retry_mutation(
            resolved_focal_path,
            normalized_ast_focal_method,
            attempted_mutation_types,
            target_focal_parameter_count=target_focal_parameter_count,
            preferred_first_family=preferred_family,
        )
        if mutation_result is None or mutation_type is None:
            _log_flow_event(
                maven_execution_path,
                f"[MutationLiveGate] Retry {retry_number} did not apply a new mutation: {mutation_error}",
            )
            break
        retries_executed = retry_number

        mutation_attempt_key = mutation_result.get("mutation_attempt_key") or _mutation_attempt_key(
            mutation_type,
            mutation_record=mutation_result,
        )
        if mutation_attempt_key:
            attempted_mutation_types.add(str(mutation_attempt_key).strip())
        mutation_result["focal_path"] = str(resolved_focal_path).replace("\\", "/")
        mutation_result["test_path"] = str(resolved_test_path).replace("\\", "/") if resolved_test_path else ""
        _upsert_focal_mutation_record(project_id, resolved_focal_path, resolved_test_path, mutation_result)
        _log_flow_event(
            maven_execution_path,
            f"[MutationLiveGate] Retry {retry_number} applied mutation type={mutation_type} "
            f"variant={mutation_result.get('mutation_attempt_key', mutation_result.get('injection_scope', '-'))} "
            f"class={mutation_result.get('mutation_priority_class', '-')}.",
        )

        baseline_result = run_maven_baseline_stage(
            maven_execution_path,
            scoped_dataframe,
            system,
            ast_test_method=normalized_ast_test_method,
            ast_focal_method=normalized_ast_focal_method,
        )
        if not baseline_result.get("ok"):
            if _is_context_bootstrap_failure(baseline_result):
                context_unsafe_active_detected = True
                _log_flow_event(
                    maven_execution_path,
                    f"[MutationLiveGate] Retry {retry_number} caused context/bootstrap failure; "
                    "continuing search for context-safe active mutation.",
                )
                continue
            _log_flow_event(
                maven_execution_path,
                f"[MutationLiveGate] Retry {retry_number} activated a live mutation.",
            )
            return {
                "is_live": True,
                "baseline_result": baseline_result,
                "high_signal": 1,
                "signal_reason": "active_mutation",
                "attempts_used": retry_number,
                "attempted_mutation_types": sorted(attempted_mutation_types),
            }

    total_attempts = 1 + retries_executed
    if context_unsafe_active_detected:
        _log_flow_event(
            maven_execution_path,
            f"[MutationLiveGate] No context-safe active mutation found after {total_attempts} attempts "
            f"for focal={resolved_focal_path}.",
        )
        return {
            "is_live": False,
            "baseline_result": baseline_result,
            "high_signal": 0,
            "signal_reason": "no_context_safe_active_mutant",
            "attempts_used": retries_executed,
            "attempted_mutation_types": sorted(attempted_mutation_types),
        }

    quiet_reason = f"quiet_mutation_after_{total_attempts}_attempts"
    _log_flow_event(
        maven_execution_path,
        f"[MutationLiveGate] Mutation remained quiet after {total_attempts} attempts for focal={resolved_focal_path}.",
    )
    return {
        "is_live": False,
        "baseline_result": baseline_result,
        "high_signal": 0,
        "signal_reason": quiet_reason,
        "attempts_used": retries_executed,
        "attempted_mutation_types": sorted(attempted_mutation_types),
    }


def _persist_generated_response_artifact(
    project,
    test_type,
    technique,
    test_class_name,
    generated_test_content,
    suffix=None,
):
    if generated_test_content is None:
        return None
    output_directory = PATH_CONTEXT.get_project_output_path(project)
    os.makedirs(output_directory, exist_ok=True)
    safe_suffix = f"_{suffix}" if suffix else ""
    artifact_path = os.path.join(
        output_directory,
        f"response_{test_type}_{technique}_{test_class_name}{safe_suffix}.java",
    )
    with open(artifact_path, "w", encoding="utf-8") as artifact_file:
        artifact_file.write(generated_test_content)
    print(f"File generated at: {artifact_path}")
    return artifact_path


def _build_targeted_maven_args(project_dataframe, include_am=True, ast_test_method=None):
    project_df = project_dataframe.copy()

    module_args = []
    if 'Module' in project_df.columns:
        modules = []
        for module in project_df['Module'].dropna().tolist():
            module_value = str(module).strip()
            if module_value and module_value.lower() != 'nan':
                modules.append(module_value.replace('\\', '/'))
        modules = list(dict.fromkeys(modules))
        if modules:
            module_args = ['-pl', ','.join(modules)]
            if include_am:
                module_args.append('-am')

    test_classes = []
    if 'Test_Class' in project_df.columns:
        test_classes = [
            str(test_class).strip()
            for test_class in project_df['Test_Class'].dropna().tolist()
            if str(test_class).strip() and str(test_class).strip().lower() != 'nan'
        ]
    if not test_classes:
        test_paths = project_df['Test_Path'].dropna().tolist()
        test_classes = [
            test_path.split('test/java/')[1].replace('/', '.').replace('.java', '')
            for test_path in test_paths
            if 'test/java/' in str(test_path)
        ]

    test_classes = list(dict.fromkeys(test_classes))
    normalized_ast_test_method = _normalize_method_name(ast_test_method)
    if test_classes and normalized_ast_test_method:
        scoped_test_classes = [
            f"{test_class}#{normalized_ast_test_method}" for test_class in test_classes
        ]
        test_arg = f"-Dtest={','.join(scoped_test_classes)}"
    else:
        test_arg = f"-Dtest={','.join(test_classes)}" if test_classes else None
    return module_args, test_arg


def _build_pitest_filter_args(project_dataframe, ast_focal_method=None):
    project_df = project_dataframe.copy()

    target_tests = []
    if 'Test_Path' in project_df.columns:
        for test_path in project_df['Test_Path'].dropna().tolist():
            normalized_test_path = str(test_path).replace('\\', '/')
            if 'test/java/' in normalized_test_path:
                target_tests.append(
                    normalized_test_path.split('test/java/')[1].replace('/', '.').replace('.java', '')
                )
    if not target_tests and 'Test_Class' in project_df.columns:
        target_tests = [
            str(test_class).strip()
            for test_class in project_df['Test_Class'].dropna().tolist()
            if str(test_class).strip() and str(test_class).strip().lower() != 'nan'
        ]

    target_classes = []
    if 'Focal_Path' in project_df.columns:
        for focal_path in project_df['Focal_Path'].dropna().tolist():
            normalized_focal_path = str(focal_path).replace('\\', '/')
            if 'java/' in normalized_focal_path:
                target_classes.append(
                    normalized_focal_path.split('java/')[1].replace('/', '.').replace('.java', '')
                )
    if not target_classes and 'Focal_Class' in project_df.columns:
        target_classes = [
            str(focal_class).strip()
            for focal_class in project_df['Focal_Class'].dropna().tolist()
            if str(focal_class).strip() and str(focal_class).strip().lower() != 'nan'
        ]

    target_tests = list(dict.fromkeys(target_tests))
    target_classes = list(dict.fromkeys(target_classes))

    pitest_args = []
    if target_tests:
        pitest_args.append(f"-DtargetTests={','.join(target_tests)}")
    if target_classes:
        pitest_args.append(f"-DtargetClasses={','.join(target_classes)}")
    return target_tests, target_classes, pitest_args


def _build_jacoco_include_patterns(project_dataframe):
    project_df = project_dataframe.copy()
    include_patterns = []
    if 'Focal_Path' not in project_df.columns:
        return include_patterns

    for focal_path in project_df['Focal_Path'].dropna().tolist():
        normalized_focal_path = str(focal_path).replace('\\', '/')
        class_path = None
        for marker in ('src/main/java/', 'src/test/java/', 'java/'):
            if marker in normalized_focal_path:
                class_path = normalized_focal_path.split(marker, 1)[1]
                break
        if class_path is None or not class_path.endswith('.java'):
            continue
        include_patterns.append(class_path.replace('.java', '*'))

    return list(dict.fromkeys(include_patterns))


def _build_maven_subprocess_env():
    maven_env = os.environ.copy()
    memory_caps = "-Xmx2g -XX:MaxMetaspaceSize=512m"
    existing_opts = str(maven_env.get("MAVEN_OPTS", "")).strip()
    if existing_opts:
        if memory_caps not in existing_opts:
            maven_env["MAVEN_OPTS"] = f"{existing_opts} {memory_caps}".strip()
    else:
        maven_env["MAVEN_OPTS"] = memory_caps
    return maven_env


def _resolve_maven_diagnostic_log_path(path):
    project_id = PATH_CONTEXT.extract_project_id(path)
    if project_id:
        output_directory = PATH_CONTEXT.get_project_output_path(project_id)
    else:
        output_directory = PATH_CONTEXT.get_output_path()
    os.makedirs(output_directory, exist_ok=True)
    return os.path.join(output_directory, "maven_smoke_diagnostics.log")


def _append_maven_diagnostic(log_path, message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8", errors="replace") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


def _read_maven_diagnostic_tail(log_path, max_chars=8000):
    if not os.path.exists(log_path):
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as log_file:
            return log_file.read()[-max_chars:]
    except Exception:
        return ""


def _read_maven_new_output(log_path, start_offset):
    if not os.path.exists(log_path):
        return "", start_offset
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as diagnostic_log:
            diagnostic_log.seek(start_offset)
            new_text = diagnostic_log.read()
            new_offset = diagnostic_log.tell()
            return new_text, new_offset
    except Exception:
        return "", start_offset


def _resolve_failure_log_path(path):
    project_id = PATH_CONTEXT.extract_project_id(path)
    if project_id:
        output_directory = PATH_CONTEXT.get_project_output_path(project_id)
    else:
        output_directory = PATH_CONTEXT.get_output_path()
    os.makedirs(output_directory, exist_ok=True)
    return os.path.join(output_directory, "latest_failure_log.txt")


def _persist_failure_log(path, failure_log_text):
    failure_log_path = _resolve_failure_log_path(path)
    if not failure_log_text or not str(failure_log_text).strip():
        try:
            if os.path.exists(failure_log_path):
                os.remove(failure_log_path)
        except OSError:
            pass
        return failure_log_path

    with open(failure_log_path, "w", encoding="utf-8", errors="replace") as failure_log_file:
        failure_log_file.write(str(failure_log_text).strip())
    return failure_log_path


def _extract_target_test_class_names(project_dataframe):
    test_classes = []
    if "Test_Class" in project_dataframe.columns:
        for test_class in project_dataframe["Test_Class"].dropna().tolist():
            normalized_test_class = str(test_class).strip()
            if normalized_test_class and normalized_test_class.lower() != "nan":
                test_classes.append(normalized_test_class)

    if not test_classes and "Test_Path" in project_dataframe.columns:
        for test_path in project_dataframe["Test_Path"].dropna().tolist():
            normalized_test_path = str(test_path).replace("\\", "/")
            if "test/java/" in normalized_test_path:
                test_classes.append(
                    normalized_test_path.split("test/java/")[1].replace("/", ".").replace(".java", "")
                )

    return list(dict.fromkeys(test_classes))


def _extract_surefire_failure_log(path, project_dataframe):
    module_roots = [os.path.abspath(path)]
    if "Module" in project_dataframe.columns:
        for module in project_dataframe["Module"].dropna().tolist():
            module_value = str(module).strip().replace("\\", "/")
            if module_value and module_value.lower() != "nan":
                module_roots.append(os.path.abspath(os.path.join(path, module_value)))
    module_roots = list(dict.fromkeys(module_roots))

    test_classes = _extract_target_test_class_names(project_dataframe)
    expected_report_names = {f"{test_class.split('.')[-1]}.txt" for test_class in test_classes}
    expected_report_names = {report_name for report_name in expected_report_names if report_name}

    report_chunks = []
    for module_root in module_roots:
        surefire_dir = os.path.join(module_root, "target", "surefire-reports")
        if not os.path.isdir(surefire_dir):
            continue

        available_reports = [
            report_name
            for report_name in os.listdir(surefire_dir)
            if report_name.endswith(".txt")
        ]
        prioritized_reports = [
            report_name for report_name in available_reports if report_name in expected_report_names
        ]
        if not prioritized_reports:
            prioritized_reports = sorted(
                available_reports,
                key=lambda report_name: os.path.getmtime(os.path.join(surefire_dir, report_name)),
                reverse=True,
            )[:3]

        for report_name in prioritized_reports:
            report_path = os.path.join(surefire_dir, report_name)
            try:
                with open(report_path, "r", encoding="utf-8", errors="replace") as report_file:
                    report_content = report_file.read().strip()
            except Exception:
                continue
            if report_content:
                report_chunks.append(f"[Surefire Report] {report_path}\n{report_content}")

    return "\n\n".join(report_chunks).strip()


def _build_failure_log_text(path, project_dataframe, stdout_text, stderr_text, max_chars=20000):
    surefire_failure_log = _extract_surefire_failure_log(path, project_dataframe)
    combined_output = ((stdout_text or "") + "\n" + (stderr_text or "")).strip()
    if len(combined_output) > max_chars:
        combined_output = combined_output[-max_chars:]

    sections = []
    if surefire_failure_log:
        sections.append(surefire_failure_log)
    if combined_output:
        sections.append(f"[Maven Output]\n{combined_output}")
    return "\n\n".join(sections).strip()


def _is_compile_failure_output(stdout_text, stderr_text):
    combined_output = ((stdout_text or "") + "\n" + (stderr_text or "")).lower()
    compile_markers = [
        "compilation error",
        "compilation failure",
        "failed to execute goal org.apache.maven.plugins:maven-compiler-plugin",
    ]
    return any(marker in combined_output for marker in compile_markers)


def _has_explicit_test_failure_output(stdout_text, stderr_text):
    combined_output = (stdout_text or "") + "\n" + (stderr_text or "")
    if not combined_output.strip():
        return False
    for pattern in SUREFIRE_FAILURE_PATTERNS:
        if pattern.search(combined_output):
            return True
    return False


def _stage_succeeded(result):
    stdout_text = result.stdout or ""
    stderr_text = result.stderr or ""
    if result.returncode == 0:
        return True
    if 'BUILD FAILURE' in stdout_text or 'BUILD FAILURE' in stderr_text:
        return False
    return 'BUILD SUCCESS' in stdout_text or 'BUILD SUCCESS' in stderr_text


def _prepare_maven_stage_context(path, project_dataframe, system, ast_test_method=None, ast_focal_method=None):
    project_df = project_dataframe.copy()
    build_timeout_seconds = utils.get_subprocess_timeout_seconds("build_timeout_seconds", 900)
    smoke_mode = os.getenv("AGONE_SMOKE_TEST", "0").strip().lower() in {"1", "true", "yes", "on"}
    diagnostic_log_path = _resolve_maven_diagnostic_log_path(path)

    resolved_ast_test_method = _normalize_method_name(ast_test_method)
    resolved_ast_focal_method = _normalize_method_name(ast_focal_method)
    if resolved_ast_test_method is None or resolved_ast_focal_method is None:
        derived_ast_test_method, derived_ast_focal_method = _extract_ast_scope_from_dataframe(project_df)
        if resolved_ast_test_method is None:
            resolved_ast_test_method = derived_ast_test_method
        if resolved_ast_focal_method is None:
            resolved_ast_focal_method = derived_ast_focal_method

    excluded_mutation_methods = _build_excluded_methods_for_target(
        project_df,
        resolved_ast_focal_method,
    )

    baseline_module_args, test_classes = _build_targeted_maven_args(
        project_df,
        include_am=False,
        ast_test_method=resolved_ast_test_method,
    )
    pit_module_args, _ = _build_targeted_maven_args(project_df, include_am=False)
    target_tests, target_classes, pitest_filter_args = _build_pitest_filter_args(
        project_df,
        ast_focal_method=resolved_ast_focal_method,
    )
    maven_env = _build_maven_subprocess_env()

    if not target_tests or not target_classes:
        missing_filters = []
        if not target_tests:
            missing_filters.append("targetTests")
        if not target_classes:
            missing_filters.append("targetClasses")
        missing_filters_text = ",".join(missing_filters)
        error_message = f"Refusing to run PIT without targeted filters ({missing_filters_text})."
        return {
            "ok": False,
            "error_message": error_message,
            "project_df": project_df,
            "diagnostic_log_path": diagnostic_log_path,
            "smoke_mode": smoke_mode,
        }

    common_flags = ['-Drat.skip=true', '-DfailIfNoTests=false', '-Dcheckstyle.skip=true']
    pitest_runtime_flags = [
        *pitest_filter_args,
        *([f"-DincludedTestMethods={resolved_ast_test_method}"] if resolved_ast_test_method else []),
        *([f"-Dpitest.includedMethods={resolved_ast_focal_method}"] if resolved_ast_focal_method else []),
        *([f"-DexcludedMethods={','.join(excluded_mutation_methods)}"] if excluded_mutation_methods else []),
        '-DfailWhenNoMutations=false',
        '-Dthreads=1',
        '-DtimeoutConstant=120000',
        '-DtimeoutFactor=4.0',
        '-DparseSurefireArgLine=false',
        '-Dfeatures=-auto_threads',
    ]
    maven_executable = 'mvn.cmd' if system == 'Windows' else 'mvn'
    offline_enabled = os.getenv("AGONE_MAVEN_OFFLINE", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    offline_flags = ['-o'] if offline_enabled else []
    baseline_command = [
        maven_executable,
        *offline_flags,
        '-B',
        *baseline_module_args,
        *([test_classes] if test_classes else []),
        *common_flags,
        'jacoco:prepare-agent',
        'test',
        'jacoco:report',
    ]
    pit_command = [
        maven_executable,
        *offline_flags,
        '-B',
        *pit_module_args,
        *pitest_runtime_flags,
        *common_flags,
        'org.pitest:pitest-maven:mutationCoverage',
    ]

    def run_stage(command, stage_name):
        if smoke_mode:
            return _run_maven_smoke_command(
                command,
                path,
                maven_env,
                build_timeout_seconds,
                diagnostic_log_path,
                command_label=stage_name,
            )
        return subprocess.run(
            command,
            cwd=path,
            capture_output=True,
            text=True,
            timeout=build_timeout_seconds,
            env=maven_env,
        )

    return {
        "ok": True,
        "project_df": project_df,
        "build_timeout_seconds": build_timeout_seconds,
        "smoke_mode": smoke_mode,
        "diagnostic_log_path": diagnostic_log_path,
        "resolved_ast_test_method": resolved_ast_test_method,
        "resolved_ast_focal_method": resolved_ast_focal_method,
        "excluded_mutation_methods": excluded_mutation_methods,
        "baseline_module_args": baseline_module_args,
        "pit_module_args": pit_module_args,
        "test_classes": test_classes,
        "target_tests": target_tests,
        "target_classes": target_classes,
        "baseline_command": baseline_command,
        "pit_command": pit_command,
        "run_stage": run_stage,
    }


def _log_stage_context(context):
    smoke_mode = context["smoke_mode"]
    diagnostic_log_path = context["diagnostic_log_path"]

    if context["test_classes"] is not None:
        print(f"Test classes: {context['test_classes']}")
    if context["baseline_module_args"]:
        print(f"Maven baseline module selection: {' '.join(context['baseline_module_args'])}")
    if context["pit_module_args"]:
        print(f"Maven PIT module selection: {' '.join(context['pit_module_args'])}")
    print(
        "PITest filters: "
        f"-DtargetTests={','.join(context['target_tests']) if context['target_tests'] else '<none>'} "
        f"-DtargetClasses={','.join(context['target_classes']) if context['target_classes'] else '<none>'}"
    )
    if context["resolved_ast_test_method"]:
        print(f"AST test method scope: {context['resolved_ast_test_method']}")
    if context["resolved_ast_focal_method"]:
        print(f"AST focal method scope: {context['resolved_ast_focal_method']}")
    if context["excluded_mutation_methods"]:
        print(f"PITest excluded focal methods: {','.join(context['excluded_mutation_methods'])}")

    subprocess.check_call(['java', '-version'])
    print(f"Maven baseline command: {' '.join(context['baseline_command'])}")
    print(f"Maven PIT command: {' '.join(context['pit_command'])}")

    if smoke_mode:
        print("Precision Maven mode enabled for smoke execution.")
        print(f"Maven smoke diagnostics log: {os.path.abspath(diagnostic_log_path)}")
        _append_maven_diagnostic(
            diagnostic_log_path,
            f"Smoke execution started. baseline_module_args={context['baseline_module_args']} "
            f"pit_module_args={context['pit_module_args']} test_classes={context['test_classes']} "
            f"target_tests={context['target_tests']} target_classes={context['target_classes']} "
            f"ast_test_method={context['resolved_ast_test_method']} ast_focal_method={context['resolved_ast_focal_method']} "
            f"excluded_mutation_methods={context['excluded_mutation_methods']} "
            f"timeout={context['build_timeout_seconds']}s",
        )
        _append_maven_diagnostic(
            diagnostic_log_path,
            f"Maven baseline command: {' '.join(context['baseline_command'])}",
        )
        _append_maven_diagnostic(
            diagnostic_log_path,
            f"Maven PIT command: {' '.join(context['pit_command'])}",
        )


def run_maven_baseline_stage(path, project_dataframe, system, ast_test_method=None, ast_focal_method=None):
    context = _prepare_maven_stage_context(
        path,
        project_dataframe,
        system,
        ast_test_method=ast_test_method,
        ast_focal_method=ast_focal_method,
    )
    if not context["ok"]:
        if context["smoke_mode"]:
            _append_maven_diagnostic(context["diagnostic_log_path"], context["error_message"])
        return {
            "ok": False,
            "error_text": context["error_message"],
            "failure_log": context["error_message"],
            "is_compile_failure": False,
        }

    _persist_failure_log(path, "")
    _log_stage_context(context)
    baseline_result = context["run_stage"](context["baseline_command"], "baseline-test")
    explicit_test_failure = _has_explicit_test_failure_output(
        baseline_result.stdout or "",
        baseline_result.stderr or "",
    )
    if _stage_succeeded(baseline_result) and not explicit_test_failure:
        _persist_failure_log(path, "")
        return {
            "ok": True,
            "error_text": None,
            "failure_log": "",
            "is_compile_failure": False,
        }
    if explicit_test_failure and context["smoke_mode"]:
        _append_maven_diagnostic(
            context["diagnostic_log_path"],
            "Detected explicit Surefire failure markers despite successful Maven exit code.",
        )

    failure_log_text = _build_failure_log_text(
        path,
        context["project_df"],
        baseline_result.stdout or "",
        baseline_result.stderr or "",
    )
    persisted_failure_log_path = _persist_failure_log(path, failure_log_text)
    if context["smoke_mode"]:
        _append_maven_diagnostic(
            context["diagnostic_log_path"],
            f"Baseline failure log captured at {persisted_failure_log_path}",
        )
    error_text = errorCorrection.extract_errors(
        baseline_result.stdout or "",
        baseline_result.stderr or "",
    )
    if explicit_test_failure and (not error_text or not str(error_text).strip()):
        error_text = "Surefire reported failing tests."
    return {
        "ok": False,
        "error_text": error_text,
        "failure_log": failure_log_text,
        "is_compile_failure": _is_compile_failure_output(
            baseline_result.stdout or "",
            baseline_result.stderr or "",
        ),
    }


def run_maven_pit_stage(path, project_dataframe, system, ast_test_method=None, ast_focal_method=None):
    context = _prepare_maven_stage_context(
        path,
        project_dataframe,
        system,
        ast_test_method=ast_test_method,
        ast_focal_method=ast_focal_method,
    )
    if not context["ok"]:
        if context["smoke_mode"]:
            _append_maven_diagnostic(context["diagnostic_log_path"], context["error_message"])
        return {"ok": False, "error_text": context["error_message"]}

    _log_stage_context(context)
    pit_result = context["run_stage"](context["pit_command"], "pit-targeted")
    if _stage_succeeded(pit_result):
        return {"ok": True, "error_text": None}

    error_text = errorCorrection.extract_errors(pit_result.stdout or "", pit_result.stderr or "")
    return {"ok": False, "error_text": error_text}


def _detect_maven_stage_from_line(line):
    normalized_line = line.lower()
    if "jacoco:prepare-agent" in normalized_line:
        return "jacoco-prepare-agent"
    if "jacoco:report" in normalized_line:
        return "jacoco-report"
    if "pitest" in normalized_line and "mutationcoverage" in normalized_line:
        return "pitest"
    if (
        "surefire" in normalized_line and ":test" in normalized_line
    ) or "t e s t s" in normalized_line:
        return "test"
    return None


def _log_maven_stage_transitions(chunk_text, seen_stages, diagnostic_log_path):
    for line in chunk_text.splitlines():
        stage_name = _detect_maven_stage_from_line(line)
        if stage_name is None or stage_name in seen_stages:
            continue
        seen_stages.add(stage_name)
        print(f"Maven smoke stage reached: {stage_name}")
        _append_maven_diagnostic(
            diagnostic_log_path,
            f"Observed Maven stage marker for '{stage_name}': {line.strip()}",
        )


def _terminate_process_tree(process):
    if process is None:
        return
    pid = getattr(process, "pid", None)
    if pid is None:
        return
    terminated = False
    if psutil is not None:
        try:
            root_process = psutil.Process(pid)
            children = root_process.children(recursive=True)
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    continue
            psutil.wait_procs(children, timeout=5)
            for child in children:
                try:
                    if child.is_running():
                        child.kill()
                except psutil.NoSuchProcess:
                    continue
            if root_process.is_running():
                root_process.terminate()
                try:
                    root_process.wait(timeout=5)
                except psutil.TimeoutExpired:
                    root_process.kill()
            terminated = True
        except Exception:
            terminated = False

    if not terminated:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                process.terminate()
                process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _run_maven_smoke_command(command, path, maven_env, build_timeout_seconds, diagnostic_log_path, command_label="lifecycle"):
    print(f"Maven smoke {command_label} command: {' '.join(command)}")
    _append_maven_diagnostic(
        diagnostic_log_path,
        f"Starting smoke {command_label} command={' '.join(command)} cwd={path}",
    )

    start_time = time.monotonic()
    if os.path.exists(diagnostic_log_path):
        file_offset = os.path.getsize(diagnostic_log_path)
    else:
        file_offset = 0
    seen_stages = set()
    combined_output = []

    with open(diagnostic_log_path, "a", encoding="utf-8", errors="replace") as diagnostic_log:
        diagnostic_log.write(f"\n===== MAVEN {command_label.upper()} START =====\n")
        process = subprocess.Popen(
            command,
            cwd=path,
            stdout=diagnostic_log,
            stderr=subprocess.STDOUT,
            text=True,
            env=maven_env,
        )

    try:
        while True:
            chunk_text, file_offset = _read_maven_new_output(diagnostic_log_path, file_offset)
            if chunk_text:
                combined_output.append(chunk_text)
                _log_maven_stage_transitions(chunk_text, seen_stages, diagnostic_log_path)

            return_code = process.poll()
            if return_code is not None:
                break

            elapsed = time.monotonic() - start_time
            if elapsed > build_timeout_seconds:
                _terminate_process_tree(process)
                raise subprocess.TimeoutExpired(command, build_timeout_seconds)

            time.sleep(1)
    finally:
        chunk_text, file_offset = _read_maven_new_output(diagnostic_log_path, file_offset)
        if chunk_text:
            combined_output.append(chunk_text)
            _log_maven_stage_transitions(chunk_text, seen_stages, diagnostic_log_path)
        with open(diagnostic_log_path, "a", encoding="utf-8", errors="replace") as diagnostic_log:
            diagnostic_log.write(
                f"\n===== MAVEN {command_label.upper()} END returncode={process.poll()} =====\n"
            )

    elapsed = time.monotonic() - start_time
    print(f"Maven smoke {command_label} finished in {elapsed:.2f}s with returncode={process.returncode}")
    _append_maven_diagnostic(
        diagnostic_log_path,
        f"Finished smoke {command_label} in {elapsed:.2f}s with returncode={process.returncode}",
    )

    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout="".join(combined_output),
        stderr="",
    )


def search_modules_pom(project_path, project_dataframe, project_id):
    """
    Searches all the modules where are stored pom.xml files in association with the classes specified in the the dataframe.

        Parameters:
                    project_path: the path of the proejct
                    project_dataframe (Dataframe): the dataframe containing all the focal classes and test classes
                    project_id: the ID of the project
        Returns:
                    modules (List): the list of the modules found               
    """
    project_df = project_dataframe.copy()
    modules = []
    for index, row in project_df.iterrows():
            location = row['Test_Path'].replace(f'repos/{project_id}/', '').replace(f"{row['Test_Class']}.java", '')
            file_name = f"/{row['Test_Class']}.java"
            while not os.path.isfile(f"{project_path}/{location}/pom.xml"):
                location = os.path.dirname(location)
                if location == '':
                    break
            if location != '':
                modules.append(location)
    # remove duplicates
    modules = list(dict.fromkeys(modules)) 
    return modules


def version_as_variable(version, root):
    """
    Given a version expressed as a variable, this function returns the corresponding version as a number. 
    This function works only for Maven Projects. 
        Parameters:
                    version (String): the version represented by a variable (e.g. '${jre.version}')
                    root (Element): root of the pom file
        Returns:
                    :the number corresponding to the variable (e.g. '${jre.version}'  -> 1.8), "None" if the function did not find it
    """
    pattern = r"\${(.*?)}" 
    # I only obtain the content within square brackets. For example, ${jre.version} becomes jre.version
    matches = re.findall(pattern, version)
    if matches:
        result = matches[0]
        version = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}' + result)
        if version is not None:
            return version.text
        else:
            return None
    else:
        return None
    


def extract_maven_version(path):
    """
    Extracts Maven version from the given project.  
        Parameters:
                    path: the path of the project or of the module
        Returns:
                    compiler_version: the Maven version expressed as a numeric value, 'None' if the function did not find it
    """
    compiler_version = None
    tree = ET.parse(os.path.join(path, 'pom.xml'))
    root = tree.getroot()
    find_compiler = False
    compiler_version_find = root.find('{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}mvn.version')
    if compiler_version_find is not None:
        compiler_version_find = compiler_version_find.text
        if compiler_version_find.startswith('$'):
            compiler_version_find = version_as_variable(compiler_version_find, root)
        if utils.check_version(compiler_version_find)==True:
            find_compiler = True
            compiler_version = compiler_version_find
    if find_compiler == False:
        compiler_version_find = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}maven.version')
        if compiler_version_find is not None:
            compiler_version_find = compiler_version_find.text
            if compiler_version_find.startswith('$'):
                compiler_version_find = version_as_variable(compiler_version_find, root)
            if utils.check_version(compiler_version_find)==True:
                find_compiler = True
                compiler_version = compiler_version_find
    if compiler_version is None:
        compiler_version = '3.8.1'
    return compiler_version



def extract_test_and_java_version_maven(path):
    """
    Extracts Java version and JUnit or TestNG version from the given project. 
        Parameters:
                    path: the path of the project or of the module
        Returns:
                    java_version: if the function finds the Java version, it returns a numeric value, otherwise, it returns 'None'
                    junit_version: if the function finds the Junit version, it returns a numeric value, otherwise, it returns 'None'
                    testng_version: if the function finds the TestNG version, it returns a numeric value, otherwise, it returns 'None'
    """
    java_version = None
    junit_version = None
    testng_version = None
    # Define the namespace
    ns = {'mvn': 'http://maven.apache.org/POM/4.0.0'}
    tree = ET.parse(os.path.join(path, 'pom.xml'))
    root = tree.getroot()
    dependencies_root=root.findall('mvn:dependencies/mvn:dependency', ns)
    dependencies_management=root.findall('mvn:dependencyManagement/mvn:dependencies/mvn:dependency', ns)
    all_dependencies=dependencies_root+dependencies_management
    findTest = False # True if I found the Junit/TestNG version, false otherwhise
    for dependency in all_dependencies:
        # Extract junit or testNG version
        group_id = dependency.find('mvn:groupId', ns)
        artifact_id = dependency.find('mvn:artifactId', ns)
        version = dependency.find('mvn:version', ns)
        # If the groupId and artifactId match 'org.testng' and 'testng' respectively
        if group_id is not None and artifact_id is not None and version is not None:
            if group_id.text.__contains__('testng'):
                    version_text = version.text
                    if version_text.startswith('$'):
                        version_text = version_as_variable(version_text, root)
                    if utils.check_version(version_text)==True:
                        testng_version = version_text
                        findTest = True
                        break
            elif group_id.text.__contains__('junit'):
                version_text = version.text
                if version_text.startswith('$'):
                    version_text = version_as_variable(version_text, root)
                if utils.check_version(version_text)==True:
                    junit_version = version_text
                    findTest = True
                    break
    if findTest==False:
        test_version = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}junit5.version')
        if test_version is not None:
                test_version_text = test_version.text
                if test_version_text.startswith("$"):
                    test_version_text = version_as_variable(test_version_text, root)
                if utils.check_version(test_version_text)==True:
                    junit_version = test_version_text
                    findTest = True
    if findTest==False:
        test_version = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}junit4.version')
        if test_version is not None:
            test_version_text = test_version.text
            if test_version_text.startswith("$"):
                test_version_text = version_as_variable(test_version_text, root)
            if utils.check_version(test_version_text)==True:
                junit_version = test_version_text
                findTest = True
    if findTest==False:
        test_version = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}junit.version')
        if test_version is not None:
            test_version_text = test_version.text
            if test_version_text.startswith("$"):
                test_version_text = version_as_variable(test_version_text, root)
            if utils.check_version(test_version_text)==True:
                junit_version = test_version_text
                findTest = True
    if findTest==False:
        test_version = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}version.junit')
        if test_version is not None:
            test_version_text = test_version.text
            if test_version_text.startswith("$"):
                test_version_text = version_as_variable(test_version_text, root)
            if utils.check_version(test_version_text)==True:
                junit_version = test_version_text
                findTest = True
    if findTest==False:
        test_version = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}testng.version')
        if test_version is not None:
            test_version_text = test_version.text
            if test_version_text.startswith("$"):
                test_version_text = version_as_variable(test_version_text, root)
            if utils.check_version(test_version_text)==True:
                testng_version = test_version_text
                findTest = True
    if findTest==False:
        test_version = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}version.testng')
        if test_version is not None:
            test_version_text = test_version.text
            if test_version_text.startswith("$"):
                test_version_text = version_as_variable(test_version_text, root)
            if utils.check_version(test_version_text)==True:
                testng_version = test_version_text
                findTest = True
        
        
        
    findJava = False # True if I found the java version, false otherwhise
    # Extract Java version
    java_version_find = root.find(
        '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}maven.compiler.source')
    if java_version_find is not None:
        java_version_find_text = java_version_find.text
        if java_version_find_text.startswith("$"):
           java_version_find_text = version_as_variable(java_version_find_text, root)
        if utils.check_version(java_version_find_text)==True:
            java_version = java_version_find_text
            findJava = True

    if findJava == False:
        java_version_find = root.find(
            '{http://maven.apache.org/POM/4.0.0}properties/{h ttp://maven.apache.org/POM/4.0.0}javaVersion')
        if java_version_find is not None:
            java_version_find_text = java_version_find.text
            if java_version_find_text.startswith("$"):
                java_version_find_text = version_as_variable(java_version_find_text, root)
            if utils.check_version(java_version_find_text)==True:
                java_version = java_version_find_text
                findJava = True  

    if findJava == False:
        java_version_find = root.find(
            '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}java.version')
        if java_version_find is not None:
            java_version_find_text = java_version_find.text
            if java_version_find_text.startswith("$"):
                java_version_find_text = version_as_variable(java_version_find_text, root)
            if utils.check_version(java_version_find_text)==True:
                java_version = java_version_find_text
                findJava = True

    if findJava == False:
        java_version_find = root.find(
            '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}maven.compiler.source')
        if java_version_find is not None:
            java_version_find = root.find(
                './/{http://maven.apache.org/POM/4.0.0}plugin'
                '[{http://maven.apache.org/POM/4.0.0}artifactId="maven-compiler-plugin"]'
                '/{http://maven.apache.org/POM/4.0.0}configuration/'
                '{http://maven.apache.org/POM/4.0.0}release')
            if java_version_find is not None:
                java_version_find_text = java_version.text
                if java_version_find_text.startswith("$"):
                    java_version_find_text = version_as_variable(java_version_find_text, root)
                if utils.check_version(java_version_find_text)==True:
                    java_version = java_version_find_text
                    findJava = True

    if findJava == False:
        with open(os.path.join(path, 'pom.xml'), 'r') as f:
            pom_content = f.read()
            java_version_find = re.search(r'<source>(.+?)</source>', pom_content, re.DOTALL)
            if java_version is None:
                java_version_find = re.search(r'<javaVersion>(.+?)</javaVersion>', pom_content, re.DOTALL)
            if java_version is None:
                java_version_find = re.search(r'<release>(.+?)</release>', pom_content, re.DOTALL)
            if java_version_find is not None:
                java_version_find_text = java_version_find.group(1)
                if java_version_find_text.startswith("$"):
                    java_version_find_text = version_as_variable(java_version_find_text, root)
                if utils.check_version(java_version_find_text)==True:
                    java_version = java_version_find_text
                    findJava = True

    if findJava == False:
        java_version_find = root.find(
            '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}maven.compiler.release')
        if java_version_find is not None:
            java_version_find_text = java_version_find.text
            if java_version_find_text.startswith("$"):
                java_version_find_text = version_as_variable(java_version_find_text, root)
            if utils.check_version(java_version_find_text)==True:
                java_version = java_version_find_text
                findJava = True

    if findJava == False:
        java_version_find = root.find(
            '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}javaVersion')
        if java_version is not None:
            java_version = java_version_find.text
        if java_version is None:
            with open(os.path.join(path, 'pom.xml'), 'r') as f:
                pom_content = f.read()
            java_version = re.search(r'<source>(.+?)</source>', pom_content, re.DOTALL)
            if java_version is not None:
                findJava = True
                java_version = java_version.group(1)

    if findJava == False:
        java_version_find = root.find(
            '{http://maven.apache.org/POM/4.0.0}properties/{http://maven.apache.org/POM/4.0.0}version.java')
        if java_version_find is not None:
            java_version_find_text = java_version_find.text
            if java_version_find_text.startswith("$"):
                java_version_find_text = version_as_variable(java_version_find_text, root)
            if utils.check_version(java_version_find_text)==True:
                java_version = java_version_find_text
                findJava = True

    # Use Java version 1.8 if no version is found
    if java_version is None:
        java_version = '1.8'

    # Set JUnit version to 4 if neither JUnit nor TestNG version is found
    if junit_version is None and testng_version is None:
        junit_version = '4'

    return java_version, junit_version, testng_version






def edit_pom_file(path, project_dataframe, junit_version, testng_version, ast_focal_method=None, ast_test_method=None):
    """
    Edits the pom.xml file of the given Maven project to add the Jacoco and PITest dependencies.
        Parameters:
                    path: the path of the Maven project or of the module
                    project_dataframe (Dataframe): the dataframe containing all the focal classes and test classes that are to be executed with Pitest 
                    junit_version: the JUnit version of the Maven project, 'None' if the project does not implement the JUnit framework
                    testng_version: the TestNG version of the Maven project, 'None' if the project does not implement the TestNG framework

        Returns:
                    tree_old (ElementTree): the content of the pom.xml file before the edit, 'None' if an error occurred
    """
    project_df = project_dataframe.copy()
    target_tests, target_classes, _ = _build_pitest_filter_args(
        project_df,
        ast_focal_method=ast_focal_method,
    )
    normalized_ast_test_method = _normalize_method_name(ast_test_method)
    normalized_ast_focal_method = _normalize_method_name(ast_focal_method)
    if normalized_ast_test_method is None or normalized_ast_focal_method is None:
        derived_ast_test_method, derived_ast_focal_method = _extract_ast_scope_from_dataframe(project_df)
        if normalized_ast_test_method is None:
            normalized_ast_test_method = derived_ast_test_method
        if normalized_ast_focal_method is None:
            normalized_ast_focal_method = derived_ast_focal_method
    excluded_methods = _build_excluded_methods_for_target(project_df, normalized_ast_focal_method)
    jacoco_include_patterns = _build_jacoco_include_patterns(project_df)
    # Parse the 'pom.xml' file
    ET.register_namespace('', 'http://maven.apache.org/POM/4.0.0')
    try:
        pom_path = os.path.join(path, 'pom.xml')
        tree_old = ET.parse(pom_path)
        tree_new = ET.parse(pom_path)
        root = tree_new.getroot()
    except Exception as e:
        print(e)
        return None
    ns = {'maven': 'http://maven.apache.org/POM/4.0.0'}
    # Check if there is only one 'build' section
    if len(root.findall('maven:build', ns)) == 1:
        build = root.find('maven:build', ns)
    else:
        # Add a 'build' section
        build = ET.SubElement(root, 'build')

    # Check if there is only one 'plugins' section
    if len(build.findall('maven:plugins', ns)) == 1:
        plugins = build.find('maven:plugins', ns)
    else:
        # Add a 'plugins' section
        plugins = ET.SubElement(build, 'plugins')

    # Check se tra i plugin c'è già pitest e jacoco
    for plugin in plugins.findall('maven:plugin', ns):
        group_id = plugin.find('maven:groupId', ns)
        artifact_id = plugin.find('maven:artifactId', ns)
        if group_id is None or artifact_id is None:
            continue
        if group_id.text == 'org.pitest':
            # Remove the 'pitest' plugin
            plugins.remove(plugin)
        if group_id.text == 'org.jacoco':
            # Remove the 'jacoco' plugin
            plugins.remove(plugin)
        if artifact_id.text == 'maven-surefire-plugin':
            plugins.remove(plugin)


    # Add the 'jacoco', 'pitest' and 'maven-surefire-plugin' plugins
    pitest_plugin = ET.SubElement(plugins, 'plugin')
    pitest_group_id = ET.SubElement(pitest_plugin, 'groupId')
    pitest_group_id.text = 'org.pitest'
    pitest_artifact_id = ET.SubElement(pitest_plugin, 'artifactId')
    pitest_artifact_id.text = 'pitest-maven'
    pitest_version = ET.SubElement(pitest_plugin, 'version')
    pitest_version.text = '1.16.0'




    # Add the pitest dependencies for junit 5
    if junit_version is not None:
        if junit_version.startswith('5'):
            pitest_dependencies = ET.SubElement(pitest_plugin, 'dependencies')
            pitest_dependency = ET.SubElement(pitest_dependencies, 'dependency')
            pitest_dependency_group_id = ET.SubElement(pitest_dependency, 'groupId')
            pitest_dependency_group_id.text = 'org.pitest'
            pitest_dependency_artifact_id = ET.SubElement(pitest_dependency, 'artifactId')
            pitest_dependency_artifact_id.text = 'pitest-junit5-plugin'
            pitest_dependency_version = ET.SubElement(pitest_dependency, 'version')
            pitest_dependency_version.text = '1.2.1'

    # Add the pitest dependencies for testng
    if testng_version is not None:
            pitest_dependencies = ET.SubElement(pitest_plugin, 'dependencies')
            pitest_dependency = ET.SubElement(pitest_dependencies, 'dependency')
            pitest_dependency_group_id = ET.SubElement(pitest_dependency, 'groupId')
            pitest_dependency_group_id.text = 'org.pitest'
            pitest_dependency_artifact_id = ET.SubElement(pitest_dependency, 'artifactId')
            pitest_dependency_artifact_id.text = 'pitest-testng-plugin'
            pitest_dependency_version = ET.SubElement(pitest_dependency, 'version')
            pitest_dependency_version.text = '1.0.0'
        



    pitest_configuration = ET.SubElement(pitest_plugin, 'configuration')
    pitest_skip = ET.SubElement(pitest_configuration, 'skip')
    pitest_skip.text = 'false'
    pitest_output_formats = ET.SubElement(pitest_configuration, 'outputFormats')
    pitest_output_format = ET.SubElement(pitest_output_formats, 'outputFormat')
    pitest_output_format.text = 'CSV'
    pitest_export_line_coverage = ET.SubElement(pitest_configuration, 'exportLineCoverage')
    pitest_export_line_coverage.text = 'true'
    pitest_timestamped_reports = ET.SubElement(pitest_configuration, 'timestampedReports')
    pitest_timestamped_reports.text = 'false'
    pitest_fail_when_no_mutations = ET.SubElement(pitest_configuration, 'failWhenNoMutations')
    pitest_fail_when_no_mutations.text = 'false'
    pitest_features = ET.SubElement(pitest_configuration, 'features')
    pitest_feature = ET.SubElement(pitest_features, 'feature')
    pitest_feature.text = '+CLASSLIMIT(limit[42])'
    pitest_feature2 = ET.SubElement(pitest_features, 'feature')
    pitest_feature2.text = '-auto_threads'
    pitest_threads = ET.SubElement(pitest_configuration, 'threads')
    pitest_threads.text = '1'
    pitest_timeout_const = ET.SubElement(pitest_configuration, 'timeoutConstant')
    pitest_timeout_const.text = '120000'
    pitest_timeout_factor = ET.SubElement(pitest_configuration, 'timeoutFactor')
    pitest_timeout_factor.text = '4.0'
    pitest_parse_surefire_arg_line = ET.SubElement(pitest_configuration, 'parseSurefireArgLine')
    pitest_parse_surefire_arg_line.text = 'false'
    pitest_target_tests = ET.SubElement(pitest_configuration, 'targetTests')
    for test in target_tests:
        ET.SubElement(pitest_target_tests, 'param').text = test
    pitest_target_classes = ET.SubElement(pitest_configuration, 'targetClasses')
    for target_class in target_classes:
        ET.SubElement(pitest_target_classes, 'param').text = target_class
    if normalized_ast_test_method:
        pitest_included_test_methods = ET.SubElement(pitest_configuration, 'includedTestMethods')
        ET.SubElement(pitest_included_test_methods, 'param').text = normalized_ast_test_method
    if excluded_methods:
        pitest_excluded_methods = ET.SubElement(pitest_configuration, 'excludedMethods')
        for excluded_method in excluded_methods:
            ET.SubElement(pitest_excluded_methods, 'param').text = excluded_method

    surefire_plugin = ET.SubElement(plugins, 'plugin')
    surefire_group_id = ET.SubElement(surefire_plugin, 'groupId')
    surefire_group_id.text = 'org.apache.maven.plugins'
    surefire_artifact_id = ET.SubElement(surefire_plugin, 'artifactId')
    surefire_artifact_id.text = 'maven-surefire-plugin'
    surefire_version = ET.SubElement(surefire_plugin, 'version')
    surefire_version.text = '2.22.2'
    surefire_configuration = ET.SubElement(surefire_plugin, 'configuration')
    surefire_arg_line = ET.SubElement(surefire_configuration, 'argLine')
    surefire_arg_line.text = '--illegal-access=permit'
    surefire_test_failure_ignore = ET.SubElement(surefire_configuration, 'testFailureIgnore')
    surefire_test_failure_ignore.text = 'true'
    surefire_fork_count = ET.SubElement(surefire_configuration, 'forkCount')
    surefire_fork_count.text = '2'
    surefire_reuse_forks = ET.SubElement(surefire_configuration, 'reuseForks')
    surefire_reuse_forks.text = 'true'
    surefire_arg_line2 = ET.SubElement(surefire_configuration, 'argLine')
    surefire_arg_line2.text = '${surefireArgLine}'


    jacoco_plugin = ET.SubElement(plugins, 'plugin')
    jacoco_group_id = ET.SubElement(jacoco_plugin, 'groupId')
    jacoco_group_id.text = 'org.jacoco'
    jacoco_artifact_id = ET.SubElement(jacoco_plugin, 'artifactId')
    jacoco_artifact_id.text = 'jacoco-maven-plugin'
    jacoco_version = ET.SubElement(jacoco_plugin, 'version')
    jacoco_version.text = '0.8.7'
    
     
    jacoco_configuration = ET.SubElement(jacoco_plugin, 'configuration')
    jacoco_property = ET.SubElement(jacoco_configuration, 'propertyName')
    jacoco_property.text = 'surefireArgLine'
    jacoco_skip = ET.SubElement(jacoco_configuration, 'skip')
    jacoco_skip.text = 'false'
    jacoco_data_file = ET.SubElement(jacoco_configuration, 'dataFile')
    jacoco_data_file.text = '${project.build.directory}/jacoco.exec'
    jacoco_output_file = ET.SubElement(jacoco_configuration, 'output')
    jacoco_output_file.text = 'file'
    if jacoco_include_patterns:
        jacoco_includes = ET.SubElement(jacoco_configuration, 'includes')
        for include_pattern in jacoco_include_patterns:
            jacoco_include = ET.SubElement(jacoco_includes, 'include')
            jacoco_include.text = include_pattern
    jacoco_formats_file = ET.SubElement(jacoco_configuration, 'formats')
    jacoco_format_file = ET.SubElement(jacoco_formats_file, 'format')
    jacoco_format_file.text = 'CSV'

    jacoco_executions = ET.SubElement(jacoco_plugin, 'executions')
    jacoco_execution = ET.SubElement(jacoco_executions, 'execution')
    jacoco_id = ET.SubElement(jacoco_execution, 'id')
    jacoco_id.text = 'jacoco-initialize'
    jacoco_goals = ET.SubElement(jacoco_execution, 'goals')
    jacoco_goal = ET.SubElement(jacoco_goals, 'goal')
    jacoco_goal.text = 'prepare-agent'
    jacoco_execution2 = ET.SubElement(jacoco_executions, 'execution')
    jacoco_id2 = ET.SubElement(jacoco_execution2, 'id')
    jacoco_id2.text = 'jacoco-site'
    jacoco_phase = ET.SubElement(jacoco_execution2, 'phase')
    jacoco_phase.text = 'test'
    jacoco_goals2 = ET.SubElement(jacoco_execution2, 'goals')
    jacoco_goal2 = ET.SubElement(jacoco_goals2, 'goal')
    jacoco_goal2.text = 'report'


    # Write the changes to the 'pom.xml' file
    tree_new.write(pom_path)
    return tree_old



def run_maven_test_command(path, project_dataframe, system, ast_test_method=None, ast_focal_method=None):
    """
    Runs the package command for the given Maven project. 
    It generates the Jacoco and PITest reports. 
        Parameters:
                    path: the path of the project or of the module
                    project_dataframe (Dataframe): the dataframe that contains the focal classes and test classes that are to be executed by mvn
                    system (string): the current OS (Windows, Linux, etc..)  
        Returns:
                    :'True' if the project has been compiled successfully, 'False' if the project has been compiled with errors
    """
    try:
        baseline_result = run_maven_baseline_stage(
            path,
            project_dataframe,
            system,
            ast_test_method=ast_test_method,
            ast_focal_method=ast_focal_method,
        )
        if not baseline_result.get("ok"):
            errori = baseline_result.get("error_text")
            print("\n--------------------")
            print(errori)
            print("\n--------------------")
            return False, errori

        pit_result = run_maven_pit_stage(
            path,
            project_dataframe,
            system,
            ast_test_method=ast_test_method,
            ast_focal_method=ast_focal_method,
        )
        if pit_result.get("ok"):
            return True, None

        errori = pit_result.get("error_text")
        print("\n--------------------")
        print(errori)
        print("\n--------------------")
        return False, errori
    except subprocess.TimeoutExpired:
        diagnostic_log_path = _resolve_maven_diagnostic_log_path(path)
        diagnostic_tail = _read_maven_diagnostic_tail(diagnostic_log_path)
        timed_out_error = errorCorrection.extract_errors(diagnostic_tail, "")
        return False, timed_out_error
    except Exception as e:
        print(e)
        return False, None


def _execute_iterative_healing_flow(
    project,
    test_type,
    technique,
    name_focal_class,
    name_test_class,
    focal_class,
    focal_path,
    test_path,
    testing_framework,
    java_version,
    has_mockito,
    project_structure,
    project_dependencies,
    package_test_class,
    scoped_dataframe,
    maven_execution_path,
    system,
    ast_test_method,
    ast_focal_method,
):
    total_prompt_tokens = 0
    total_completion_tokens = 0
    iterations_to_pass = 0

    normalized_ast_test_method = _normalize_method_name(ast_test_method)
    normalized_ast_focal_method = _normalize_method_name(ast_focal_method)
    if normalized_ast_test_method is None:
        _log_flow_event(
            maven_execution_path,
            f"[IterativeHealing] Missing AST target test method for {name_test_class}; skipping repair flow.",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "high_signal": 0,
            "signal_reason": "missing_ast_test_method",
        }
    if normalized_ast_focal_method is None:
        _log_flow_event(
            maven_execution_path,
            f"[IterativeHealing] Missing AST focal method for {name_test_class}; failing fast to avoid unscoped PIT.",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "high_signal": 0,
            "signal_reason": "missing_ast_focal_method",
        }

    mutation_live_result = verify_mutation_is_live(
        project,
        maven_execution_path,
        scoped_dataframe,
        system,
        ast_test_method=normalized_ast_test_method,
        ast_focal_method=normalized_ast_focal_method,
        focal_path=focal_path,
        test_path=test_path,
    )
    high_signal = mutation_live_result.get("high_signal", 0)
    signal_reason = mutation_live_result.get("signal_reason", "-")
    baseline_result = mutation_live_result.get("baseline_result", {})
    is_live_mutation = bool(mutation_live_result.get("is_live"))

    if not is_live_mutation:
        if str(signal_reason).startswith("quiet_mutation_after_"):
            _log_flow_event(
                maven_execution_path,
                f"[IterativeHealing] Skipping Codex/PIT for {name_test_class}: {signal_reason}.",
            )
            return {
                "success": True,
                "last_execution": True,
                "chance": 0,
                "iterations_to_pass": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "high_signal": high_signal,
                "signal_reason": signal_reason,
            }
        if str(signal_reason) == "no_context_safe_active_mutant":
            _log_flow_event(
                maven_execution_path,
                f"[IterativeHealing] Skipping Codex/PIT for {name_test_class}: {signal_reason}.",
            )
            return {
                "success": True,
                "last_execution": True,
                "chance": 0,
                "iterations_to_pass": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "high_signal": high_signal,
                "signal_reason": signal_reason,
            }
        _log_flow_event(
            maven_execution_path,
            f"[IterativeHealing] Mutation-live verification failed for {name_test_class}: {signal_reason}",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "high_signal": high_signal,
            "signal_reason": signal_reason,
        }

    max_retries = _get_int_run_setting("iterative_max_retries", 3)
    if max_retries < 1:
        max_retries = 1
    latest_failure_log = baseline_result.get("failure_log", "")
    _log_flow_event(
        maven_execution_path,
        f"[IterativeHealing] Baseline failed for {name_test_class}; starting retry loop (max_retries={max_retries}).",
    )
    for retry_number in range(1, max_retries + 1):
        _log_flow_event(
            maven_execution_path,
            f"[IterativeHealing] Retry {retry_number}/{max_retries} for {name_test_class} on focal {name_focal_class}.",
        )
        retry_failure_context = _append_iterative_retry_guidance(latest_failure_log)
        _persist_failure_log(maven_execution_path, retry_failure_context)
        try:
            with open(focal_path, "r", encoding="utf-8", errors="replace") as focal_file:
                focal_class_snapshot = focal_file.read()
        except Exception:
            focal_class_snapshot = focal_class
        generated_test_content, _, usage_metadata = utils.generate_test_with_codex(
            test_type,
            technique,
            focal_class_snapshot,
            focal_path,
            testing_framework,
            java_version,
            has_mockito,
            test_path,
            name_test_class,
            project_structure,
            project_dependencies,
            package_test_class,
            output_contract="mapped_method",
            target_test_method=normalized_ast_test_method,
            target_focal_method=normalized_ast_focal_method,
            failure_log_override=retry_failure_context,
        )
        total_prompt_tokens += usage_metadata.get("prompt_tokens", 0)
        total_completion_tokens += usage_metadata.get("completion_tokens", 0)
        if generated_test_content is None:
            rejection_reason = str(usage_metadata.get("rejection_reason", "") or "").strip()
            if rejection_reason:
                latest_failure_log = _merge_failure_log_context(retry_failure_context, rejection_reason)
                _persist_failure_log(maven_execution_path, latest_failure_log)
                _log_flow_event(
                    maven_execution_path,
                    f"[IterativeHealing] Retry {retry_number} style-lock rejection captured: {rejection_reason}",
                )
            _log_flow_event(
                maven_execution_path,
                f"[IterativeHealing] Codex method patch generation failed on retry {retry_number} for {name_test_class}.",
            )
            continue

        _persist_generated_response_artifact(
            project,
            test_type,
            technique,
            name_test_class,
            generated_test_content,
            suffix=f"iterative_retry_{retry_number}",
        )

        baseline_result = run_maven_baseline_stage(
            maven_execution_path,
            scoped_dataframe,
            system,
            ast_test_method=normalized_ast_test_method,
            ast_focal_method=normalized_ast_focal_method,
        )
        if not baseline_result.get("ok"):
            latest_failure_log = baseline_result.get("failure_log", latest_failure_log)
            concise_signal = _extract_concise_failure_signal(latest_failure_log)
            if concise_signal:
                _log_flow_event(
                    maven_execution_path,
                    f"[IterativeHealing] Retry {retry_number} failure signal: {concise_signal}",
                )
            continue

        _log_flow_event(
            maven_execution_path,
            f"[IterativeHealing] Baseline passed on retry {retry_number} for {name_test_class}; running targeted PIT.",
        )
        pit_result = run_maven_pit_stage(
            maven_execution_path,
            scoped_dataframe,
            system,
            ast_test_method=normalized_ast_test_method,
            ast_focal_method=normalized_ast_focal_method,
        )
        if pit_result.get("ok"):
            iterations_to_pass = retry_number
            return {
                "success": True,
                "last_execution": True,
                "chance": retry_number,
                "iterations_to_pass": iterations_to_pass,
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "high_signal": high_signal,
                "signal_reason": signal_reason,
            }
        _log_flow_event(
            maven_execution_path,
            f"[IterativeHealing] PIT failed after retry {retry_number} for {name_test_class}: {pit_result.get('error_text')}",
        )
        iterations_to_pass = retry_number
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": iterations_to_pass,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "high_signal": high_signal,
            "signal_reason": signal_reason,
        }

    _log_flow_event(
        maven_execution_path,
        f"[IterativeHealing] Exhausted retries for {name_test_class} without baseline success.",
    )
    return {
        "success": False,
        "last_execution": False,
        "chance": 6,
        "iterations_to_pass": max_retries,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "high_signal": high_signal,
        "signal_reason": signal_reason,
    }


def _execute_regenerative_sync_flow(
    project,
    test_type,
    technique,
    name_focal_class,
    name_test_class,
    focal_class,
    focal_path,
    test_path,
    testing_framework,
    java_version,
    has_mockito,
    project_structure,
    project_dependencies,
    package_test_class,
    scoped_dataframe,
    maven_execution_path,
    system,
    ast_test_method,
    ast_focal_method,
):
    total_prompt_tokens = 0
    total_completion_tokens = 0
    normalized_ast_test_method = _normalize_method_name(ast_test_method)
    normalized_ast_focal_method = _normalize_method_name(ast_focal_method)
    if normalized_ast_focal_method is None:
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] Missing AST focal method for {name_test_class}; failing fast to avoid unscoped PIT.",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "high_signal": 0,
            "signal_reason": "missing_ast_focal_method",
        }

    def run_generation_pass(pass_label, failure_log_text):
        compile_failure_context = ""
        if str(pass_label).startswith("compile_retry_"):
            compile_failure_context = str(failure_log_text or "").strip()
        try:
            with open(focal_path, "r", encoding="utf-8", errors="replace") as focal_file:
                focal_class_snapshot = focal_file.read()
        except Exception:
            focal_class_snapshot = focal_class
        generated_test_content, _, usage_metadata = utils.generate_test_with_codex(
            test_type,
            technique,
            focal_class_snapshot,
            focal_path,
            testing_framework,
            java_version,
            has_mockito,
            test_path,
            name_test_class,
            project_structure,
            project_dependencies,
            package_test_class,
            output_contract="mapped_method_additive",
            target_test_method=normalized_ast_test_method,
            target_focal_method=normalized_ast_focal_method,
            failure_log_override=failure_log_text,
            compile_failure_log_override=compile_failure_context,
        )
        return generated_test_content, usage_metadata

    mutation_live_result = verify_mutation_is_live(
        project,
        maven_execution_path,
        scoped_dataframe,
        system,
        ast_test_method=normalized_ast_test_method,
        ast_focal_method=normalized_ast_focal_method,
        focal_path=focal_path,
        test_path=test_path,
    )
    high_signal = mutation_live_result.get("high_signal", 0)
    signal_reason = mutation_live_result.get("signal_reason", "-")
    baseline_preflight_result = mutation_live_result.get("baseline_result", {})
    is_live_mutation = bool(mutation_live_result.get("is_live"))
    latest_failure_log = baseline_preflight_result.get("failure_log", "")
    if not is_live_mutation:
        if str(signal_reason).startswith("quiet_mutation_after_"):
            _log_flow_event(
                maven_execution_path,
                f"[RegenerativeSync] Skipping Codex/PIT for {name_test_class}: {signal_reason}.",
            )
            return {
                "success": True,
                "last_execution": True,
                "chance": 0,
                "iterations_to_pass": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "high_signal": high_signal,
                "signal_reason": signal_reason,
            }
        if str(signal_reason) == "no_context_safe_active_mutant":
            _log_flow_event(
                maven_execution_path,
                f"[RegenerativeSync] Skipping Codex/PIT for {name_test_class}: {signal_reason}.",
            )
            return {
                "success": True,
                "last_execution": True,
                "chance": 0,
                "iterations_to_pass": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "high_signal": high_signal,
                "signal_reason": signal_reason,
            }
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] Mutation-live verification failed for {name_test_class}: {signal_reason}",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "high_signal": high_signal,
            "signal_reason": signal_reason,
        }

    _log_flow_event(
        maven_execution_path,
        f"[RegenerativeSync] Starting primary generation pass for {name_test_class} on focal {name_focal_class}.",
    )
    generated_test_content, usage_metadata = run_generation_pass("initial", latest_failure_log)
    total_prompt_tokens += usage_metadata.get("prompt_tokens", 0)
    total_completion_tokens += usage_metadata.get("completion_tokens", 0)
    if generated_test_content is None:
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] Primary generation failed for {name_test_class}.",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "high_signal": high_signal,
            "signal_reason": signal_reason,
        }

    _persist_generated_response_artifact(
        project,
        test_type,
        technique,
        name_test_class,
        generated_test_content,
        suffix="regen_pass_0",
    )

    baseline_result = run_maven_baseline_stage(
        maven_execution_path,
        scoped_dataframe,
        system,
        ast_test_method=normalized_ast_test_method,
        ast_focal_method=normalized_ast_focal_method,
    )
    if baseline_result.get("ok"):
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] Baseline passed on first pass for {name_test_class}; running targeted PIT.",
        )
        pit_result = run_maven_pit_stage(
            maven_execution_path,
            scoped_dataframe,
            system,
            ast_test_method=normalized_ast_test_method,
            ast_focal_method=normalized_ast_focal_method,
        )
        if pit_result.get("ok"):
            return {
                "success": True,
                "last_execution": True,
                "chance": 0,
                "iterations_to_pass": 0,
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "high_signal": high_signal,
                "signal_reason": signal_reason,
            }
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] PIT failed after first pass for {name_test_class}: {pit_result.get('error_text')}",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "high_signal": high_signal,
            "signal_reason": signal_reason,
        }

    retry_policy = _get_text_run_setting("regenerative_retry_on", "compilation_only")
    compile_retry_max = _get_int_run_setting("regenerative_compile_retry_max", 1)
    if compile_retry_max < 0:
        compile_retry_max = 0
    should_retry_compile = (
        retry_policy == "compilation_only" and baseline_result.get("is_compile_failure")
    )
    if not should_retry_compile or compile_retry_max == 0:
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] Baseline failed for {name_test_class} without eligible compile-only retry.",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": 0,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "high_signal": high_signal,
            "signal_reason": signal_reason,
        }

    latest_failure_log = baseline_result.get("failure_log", latest_failure_log)
    compile_retry_max = min(compile_retry_max, 1)
    for compile_retry_number in range(1, compile_retry_max + 1):
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] Compile retry {compile_retry_number}/{compile_retry_max} for {name_test_class}.",
        )
        generated_retry_content, retry_usage = run_generation_pass(
            f"compile_retry_{compile_retry_number}",
            latest_failure_log,
        )
        total_prompt_tokens += retry_usage.get("prompt_tokens", 0)
        total_completion_tokens += retry_usage.get("completion_tokens", 0)
        if generated_retry_content is None:
            _log_flow_event(
                maven_execution_path,
                f"[RegenerativeSync] Generation failed on compile retry {compile_retry_number} for {name_test_class}.",
            )
            continue

        _persist_generated_response_artifact(
            project,
            test_type,
            technique,
            name_test_class,
            generated_retry_content,
            suffix=f"regen_compile_retry_{compile_retry_number}",
        )

        baseline_retry_result = run_maven_baseline_stage(
            maven_execution_path,
            scoped_dataframe,
            system,
            ast_test_method=normalized_ast_test_method,
            ast_focal_method=normalized_ast_focal_method,
        )
        if not baseline_retry_result.get("ok"):
            latest_failure_log = baseline_retry_result.get("failure_log", latest_failure_log)
            continue

        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] Baseline passed after compile retry {compile_retry_number}; running targeted PIT.",
        )
        pit_result = run_maven_pit_stage(
            maven_execution_path,
            scoped_dataframe,
            system,
            ast_test_method=normalized_ast_test_method,
            ast_focal_method=normalized_ast_focal_method,
        )
        if pit_result.get("ok"):
            return {
                "success": True,
                "last_execution": True,
                "chance": 1,
                "iterations_to_pass": compile_retry_number,
                "total_prompt_tokens": total_prompt_tokens,
                "total_completion_tokens": total_completion_tokens,
                "high_signal": high_signal,
                "signal_reason": signal_reason,
            }
        _log_flow_event(
            maven_execution_path,
            f"[RegenerativeSync] PIT failed after compile retry for {name_test_class}: {pit_result.get('error_text')}",
        )
        return {
            "success": False,
            "last_execution": False,
            "chance": 6,
            "iterations_to_pass": compile_retry_number,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "high_signal": high_signal,
            "signal_reason": signal_reason,
        }

    _log_flow_event(
        maven_execution_path,
        f"[RegenerativeSync] Compile retry exhausted for {name_test_class}.",
    )
    return {
        "success": False,
        "last_execution": False,
        "chance": 6,
        "iterations_to_pass": compile_retry_max,
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "high_signal": high_signal,
        "signal_reason": signal_reason,
    }




def run_evosuite_generation_maven(path, focal_path, system):
    """
    Given a focal class of a Maven project, it runs EvoSuite to generate the corresponding test class.
        Parameters:
                path: the path of the project or of the module
                focal_path: the path of the focal class 
                system (string): the current OS (Windows, Linux, etc..)  

        Returns:
                :'True' if the generation has been executed correctly, 'False' otherwise
       
    """
    try:
        build_timeout_seconds = utils.get_subprocess_timeout_seconds("build_timeout_seconds", 900)
        name_class_to_test = focal_path.split('java/')[1].replace('.java', '').replace('/', '.')
        if system == 'Windows':
            result_mvn = subprocess.run(['mvn.cmd', '-B', 'clean'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
            result_generate = subprocess.run(['mvn.cmd', '-B', 'evosuite:generate', '-DtimeInMinutesPerClass=1', f'-Dcuts={name_class_to_test}', '-DuseSandbox=false', '-Duse_separate_classloader=false'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
            result_export = subprocess.run(['mvn.cmd', '-B', 'evosuite:export'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
        else:
            result_mvn = subprocess.run(['mvn', '-B', 'clean'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
            result_generate = subprocess.run(['mvn', '-B', 'evosuite:generate', '-DtimeInMinutesPerClass=1', f'-Dcuts={name_class_to_test}', '-DuseSandbox=false', '-Duse_separate_classloader=false'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
            result_export = subprocess.run(['mvn', '-B', 'evosuite:export'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
        if (
            (result_mvn.returncode == 0 or 'BUILD SUCCESS' in result_mvn.stdout or 'BUILD SUCCESS' in result_mvn.stderr)
            and (result_generate.returncode == 0 or 'BUILD SUCCESS' in result_generate.stdout or 'BUILD SUCCESS' in result_generate.stderr)
            and (result_export.returncode == 0 or 'BUILD SUCCESS' in result_export.stdout or 'BUILD SUCCESS' in result_export.stderr)
        ):
            return True
        else:
            return False
    except subprocess.TimeoutExpired as e:
        print(f"EvoSuite Maven generation timed out after {build_timeout_seconds} seconds: {e}")
        return False
    except Exception as e:
        print(e)
        return False
    


def add_evosuite_pom(path):
    """
    Adds the evosuite dependency and the evosuite plugin (version 1.0.6) to the pom.xml file of the given project.
        Parameters:
                    path: the path of the project or of the module
        Returns:
                    tree_old (ElementTree): the content of the pom.xml file before the edit, 'None' if an error occurred
    """
    
    ET.register_namespace('', 'http://maven.apache.org/POM/4.0.0')
    try:
        pom_path = os.path.join(path, 'pom.xml')
        tree_old = ET.parse(pom_path)
        tree_new = ET.parse(pom_path)
        root = tree_new.getroot()
    except Exception as e:
        print(e)
        return None
    ns = {'maven': 'http://maven.apache.org/POM/4.0.0'}


    if len(root.findall('maven:build', ns)) == 1:
        build = root.find('maven:build', ns)
    else:
        # Add a 'build' section
        build = ET.SubElement(root, 'build')

    # Check if there is only one 'plugins' section
    if len(build.findall('maven:plugins', ns)) == 1:
        plugins = build.find('maven:plugins', ns)
    else:
        # Add a 'plugins' section
        plugins = ET.SubElement(build, 'plugins')


    all_plugins_sections = root.findall('maven:build/maven:plugins', ns)
    for plugins_section in all_plugins_sections:
        for plugin in plugins_section.findall('maven:plugin', ns):
            group_id = plugin.find('maven:groupId', ns)
            artifact_id = plugin.find('maven:artifactId', ns)
            if group_id is None or artifact_id is None:
                continue
            if group_id.text == 'org.evosuite.plugins':
                # Remove the 'evosuite' plugin
                plugins.remove(plugin)
            if artifact_id.text == 'maven-surefire-plugin':
                plugins.remove(plugin)

    # add surefire plugin
    surefire_plugin = ET.SubElement(plugins, 'plugin')
    surefire_group_id = ET.SubElement(surefire_plugin, 'groupId')
    surefire_group_id.text = 'org.apache.maven.plugins'
    surefire_artifact_id = ET.SubElement(surefire_plugin, 'artifactId')
    surefire_artifact_id.text = 'maven-surefire-plugin'
    surefire_version = ET.SubElement(surefire_plugin, 'version')
    surefire_version.text = '2.17'
    surefire_configuration = ET.SubElement(surefire_plugin, 'configuration')
    surefire_properties = ET.SubElement(surefire_configuration, 'properties')
    surefire_property = ET.SubElement(surefire_properties, 'property')
    surefire_name = ET.SubElement(surefire_property, 'name')
    surefire_name.text = 'listener'
    surefire_value = ET.SubElement(surefire_property, 'value')
    surefire_value.text = 'org.evosuite.runtime.InitializingListener'
    surefire_arg_line = ET.SubElement(surefire_configuration, 'argLine')
    surefire_arg_line.text = '--illegal-access=permit'
    surefire_test_failure_ignore = ET.SubElement(surefire_configuration, 'testFailureIgnore')
    surefire_test_failure_ignore.text = 'true'
    surefire_fork_count = ET.SubElement(surefire_configuration, 'forkCount')
    surefire_fork_count.text = '2'
    surefire_reuse_forks = ET.SubElement(surefire_configuration, 'reuseForks')
    surefire_reuse_forks.text = 'False'
    surefire_arg_line2 = ET.SubElement(surefire_configuration, 'argLine')
    surefire_arg_line2.text = '${surefireArgLine}'


    # Add the evosuite plugin
    evosuite_plugin = ET.SubElement(plugins, 'plugin')
    evosuite_group_id = ET.SubElement(evosuite_plugin, 'groupId')
    evosuite_group_id.text = 'org.evosuite.plugins'
    evosuite_artifact_id = ET.SubElement(evosuite_plugin, 'artifactId')
    evosuite_artifact_id.text = 'evosuite-maven-plugin'
    evosuite_version = ET.SubElement(evosuite_plugin, 'version')
    evosuite_version.text = '1.0.6'
    evosuite_executions = ET.SubElement(evosuite_plugin, 'executions')
    evosuite_execution = ET.SubElement(evosuite_executions, 'execution')
    evosuite_execution_id = ET.SubElement(evosuite_execution, 'id')
    evosuite_execution_id.text = 'generate-tests'
    evosuite_execution_phase = ET.SubElement(evosuite_execution, 'phase')
    evosuite_execution_phase.text = 'none'
    evosuite_goals = ET.SubElement(evosuite_execution, 'goals')
    evosuite_goal = ET.SubElement(evosuite_goals, 'goal')
    evosuite_goal.text = 'generate'


    evosuite_dependencies = ET.SubElement(evosuite_plugin, 'dependencies')
    evosuite_dependency = ET.SubElement(evosuite_dependencies, 'dependency')
    evosuite_dependency_group_id = ET.SubElement(evosuite_dependency, 'groupId')
    evosuite_dependency_group_id.text = 'org.evosuite'
    evosuite_dependency_artifact_id = ET.SubElement(evosuite_dependency, 'artifactId')
    evosuite_dependency_artifact_id.text = 'evosuite-standalone-runtime'
    evosuite_dependency_version = ET.SubElement(evosuite_dependency, 'version')
    evosuite_dependency_version.text = '1.0.6'
    evosuite_dependency_scope = ET.SubElement(evosuite_dependency, 'scope')
    evosuite_dependency_scope.text = 'compile'

    # Check if there is only one 'dependencies' section
    if len(root.findall('maven:dependencies', ns)) == 1:
        evosuite_dependencies_root = root.find('maven:dependencies', ns)
    else:
        # Add a 'dependencies' section
        evosuite_dependencies_root = ET.SubElement(root, 'dependencies')

    evosuite_dependency_root = ET.SubElement(evosuite_dependencies_root, 'dependency')
    evosuite_dependency_group_id_root = ET.SubElement(evosuite_dependency_root, 'groupId')
    evosuite_dependency_group_id_root.text = 'org.evosuite'
    evosuite_dependency_artifact_id_root = ET.SubElement(evosuite_dependency_root, 'artifactId')
    evosuite_dependency_artifact_id_root.text = 'evosuite-standalone-runtime'
    evosuite_dependency_version_root = ET.SubElement(evosuite_dependency_root, 'version')
    evosuite_dependency_version_root.text = '1.0.6'
    evosuite_dependency_scope_root = ET.SubElement(evosuite_dependency_root, 'scope')
    evosuite_dependency_scope_root.text = 'compile'

    # Write the changes to the 'pom.xml' file
    tree_new.write(pom_path)
    return tree_old



def process_maven_project(project, test_types, techniques, project_path, project_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, correct, project_structure, project_dependencies):
    """
    It processes the given Maven project with the given test types and techniques.
    Parameters:
                project: the ID of the project.
                test_types (List): the list of test types to execute.
                techniques (List): the list of prompt techniques (for the AI test types) to execute.
                project_path: the path of the project. 
                project_df: the dataframe that contains all the focal/test classes of the project.
                compilter_version: the Maven version of the given project.
                java_version: the Java version of the given project.
                junit_version: the Junit version of the given project.
                testng_version: the testNG version of the given project.
                has_mockito: the string that will be used to specify to the API whether the AI test types can use the Mockito framework or not.
                system (String): the current OS (Windows, Linux, etc...)
    Returns:
                0(int) if the process failed.
    """
    swtich_to_next_project = False
    os.makedirs(PATH_CONTEXT.get_project_output_path(project), exist_ok=True)
    project_ast_test_method, project_ast_focal_method = _extract_ast_scope_from_dataframe(project_df)
    # add jacoco and pitest dependecies to pom.xml
    original_pom = edit_pom_file(
        project_path,
        project_df,
        junit_version,
        testng_version,
        ast_focal_method=project_ast_focal_method,
        ast_test_method=project_ast_test_method,
    )
    if original_pom is None:
        print("An errore occured while trying to edit the pom file")
        return 0 # Switch to the next project
    for i, test_type in enumerate(test_types):
        output_path_failed = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}.failed") # Indicates that the test type failed due to an error during the execution of the script.
        output_path_failed_maven = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}.mavenfailed")  # Indicates that all the test classes of the test type failed during the maven execution.
        swtich_to_next_test_type = False
        print('\n----')
        print(f"STARTING '{test_type}' test type\n")
        if test_type == "human":
            # configure the test smell detector
            csv_path_input_test_smell = utils.configure_test_smell_detector(project_df, project)
            # Run the Maven package command
            print("--//loading maven execution..//")
            if run_maven_test_command(
                project_path,
                project_df,
                system,
                ast_test_method=project_ast_test_method,
                ast_focal_method=project_ast_focal_method,
            )[0]==True:
                print(f"[INFO] Package command completed for {project}\n")
            else:
                print(f"Package command failed for {project}. Switch to next project...\n")
                original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                swtich_to_next_project = True
                break # analyze the next project

            # Run the test smell detector
            path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None)
            if path_csv_result_test_smell is None:
                print("An error occured while trying to run the test smell detector")
            else:
                print("The test smell detector ended successfully")  
            # Retrieve Code Coverage and Cyclomatic Complexity on test classes
            utils.snapshot_coverage_reports(
                project_path,
                project_df,
                project,
                'Maven',
                test_type,
                None,
            )
            measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(
                project_path,
                project_df,
                project,
                'Maven',
                test_type=test_type,
                technique=None,
            )
            if measures_df is None:
                print(f"Switch to next project because edit_pom_xml() failed for the project {project}")
                original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                swtich_to_next_project = True
                break # Switch to next project
            output_csv_path = utils.generate_output_csv_test_type(project, test_type, None, measures_df, path_csv_result_test_smell)
            if output_csv_path is None:
                print("An errore occured while trying to save the test type csv file!")
            else:
                print(f"DataFrame saved to {output_csv_path}")

        elif test_type == 'evosuite':
            project_df_evosuite = project_df.copy() # dataframe for the evosuite test type
            pom_before_evosuite = add_evosuite_pom(project_path)
            if pom_before_evosuite is None:
                try:
                    with open(output_path_failed, 'w') as file:
                        pass
                except Exception as e:
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                continue # Switch to next test type
            dictionary_for_restore = {} # dictionary that contains test_path as keys and the respective 'human_test_class' as values. This dictionary is used to restore the test classes to the human version.
            for index, row in project_df_evosuite.iterrows(): # iterate over each test class and focal class
                name_focal_class = row['Focal_Class']
                name_test_class = row['Test_Class']
                test_path = _normalize_compiled_path(project, row['Test_Path'])
                focal_path = _normalize_compiled_path(project, row['Focal_Path'])
                last_execution = None # outcome of the last maven execution, True = Build Success, False = Build Failure
                current_module = utils.find_module_class(project, test_path)
                try:
                    with open(test_path, 'r') as test_file_read:
                        human_test_class = test_file_read.read() # save the human version of the test class
                        dictionary_for_restore[test_path] =  human_test_class
                    os.remove(test_path)

                except Exception as e:
                    print(f"An error occured while trying to open and read the test_path: {e}")
                    try:
                        pom_before_evosuite.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    swtich_to_next_test_type = True # Switch to next test type
                    break
                
                
                print("--//loading evosuite generation..//")
                if run_evosuite_generation_maven(project_path, focal_path, system) == False: # if error while running evosuite
                    print ("An error accored while trying to run the evosuite generation")
                    try:
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        pom_before_evosuite.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    swtich_to_next_test_type = True
                    break # Switch to next test type
                else:
                    print(f"Evosuite generation performed correctly for the '{name_focal_class}' class")

                evosuite_test_path = test_path.replace(f'{name_test_class}.java', f'{name_focal_class}_ESTest.java')
                try:
                    if not os.path.exists(evosuite_test_path):
                        project_df_evosuite = project_df_evosuite[project_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Maven execution
                        utils.remove_evosuite_scaffolding_files(list(test_path))
                        utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Maven execution
                        utils.remove_dot_evosuite_dir(project, current_module)
                        continue # Switch to next focal class/test class
                    with open(evosuite_test_path, 'r') as evosuite_file:
                        evosuite_content = evosuite_file.read()
                    evosuite_content = evosuite_content.replace(f'public class {name_focal_class}_ESTest', f'public class {name_test_class}') 
                    evosuite_content = evosuite_content.replace('separateClassLoader = true', 'separateClassLoader = false') # when setting separateClassLoader to false, JaCoCo can correctly calculate code coverage
                    with open(test_path, 'w') as test_file:
                        test_file.write(evosuite_content)
                    os.remove(evosuite_test_path)
                except Exception as e:
                    print(f"An error occured while trying to copy the evosuite class test: {e}")
                    try:
                        pom_before_evosuite.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_dot_evosuite_dir(project, current_module)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_dot_evosuite_dir(project, current_module)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    swtich_to_next_test_type = True
                    break # Switch to next test type
                print("--//loading maven execution..//")
                if run_maven_test_command(
                    project_path,
                    project_df,
                    system,
                    ast_test_method=project_ast_test_method,
                    ast_focal_method=project_ast_focal_method,
                )[0]==False: # if error while running maven
                    print(f"Package command failed for project: {project}, test type: {test_type}\n")
                    project_df_evosuite = project_df_evosuite[project_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Maven execution
                    utils.remove_evosuite_scaffolding_files(list(test_path))
                    utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Maven execution
                    utils.remove_dot_evosuite_dir(project, current_module)
                    last_execution = False
                    continue # Switch to next focal class/test class
                else:
                    last_execution = True
                    print(f"Package command completed for {project}\n")

                utils.remove_dot_evosuite_dir(project, current_module)
                
            if swtich_to_next_test_type == True:
                continue

                
                    
            if project_df_evosuite.empty: # If all the test classes provided by Evosuite failed during Maven execution
                try:
                    with open(output_path_failed_maven, 'w') as file:
                        pass
                except Exception as e:
                    original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                    utils.write_files(dictionary_for_restore)
                    utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                    print(f'An error occured while trying to open output_path_failed_maven: {e}')
                    sys.exit(1)
            else: # if at least one of the test classes provided by Evosuite runned succesfully during Maven execution
                # configure the test smell detector
                csv_path_input_test_smell = utils.configure_test_smell_detector(project_df_evosuite, project)
                # Run the test smell detector
                path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None)
                if path_csv_result_test_smell is None:
                    print("An error occured while trying to run the test smell detector")
                else:
                    print("The test smell detector ended successfully")  
                if last_execution == False: # if last maven execution outcome is False, then I run one more time maven
                    print("--//loading maven execution..//")
                    if run_maven_test_command(
                        project_path,
                        project_df,
                        system,
                        ast_test_method=project_ast_test_method,
                        ast_focal_method=project_ast_focal_method,
                    )[0]==False: # if error while running maven
                        print('An error occured while trying to execute the final version of test classes.\n')
                        try:
                            pom_before_evosuite.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        continue # Switch to next test type
                # Retrieve Code Coverage and Cyclomatic Complexity on test classes
                utils.snapshot_coverage_reports(
                    project_path,
                    project_df_evosuite,
                    project,
                    'Maven',
                    test_type,
                    None,
                )
                measures_df  = utils.retrieve_code_coverage_and_cyclomatic_complexity(
                    project_path,
                    project_df_evosuite,
                    project,
                    'Maven',
                    test_type=test_type,
                    technique=None,
                )
                if measures_df is not None:
                    output_csv_path = utils.generate_output_csv_test_type(project, test_type, None, measures_df, path_csv_result_test_smell)
                    if output_csv_path is None:
                        print("An errore occured while trying to save the test type csv file!")
                    else:
                        print(f"DataFrame saved to {output_csv_path}")
                else:
                    print(f"An occured while trying to retrieve data coverage fo the project {project}")
                    try:
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                
            utils.write_files(dictionary_for_restore)
            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
            pom_before_evosuite.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                    



        else:
            # Iterate over each technique
            for j, technique in enumerate(techniques):
                global df_chance
                output_path_failed = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}_{technique}.failed") # Indicates that a test type/technique failed due to an error during the execution of AgonTest.py or during a call to the API
                output_path_failed_maven = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}_{technique}.mavenfailed") # Indicates that all the test classes of the test type failed during the maven execution.

                restart_technique = False 
                print(f"\nProcessing test_type: {test_type}, technique: {technique}")
                project_df_technique = project_df.copy() # dataframe of the current test type and technique
                dictionary_for_restore = {} # dictionary that contains test_path as keys and the respective 'human_test_class' as values. This dictionary is used to restore the test classes to the human version.
                for index, row in project_df_technique.iterrows(): # iterate over each test class and focal class
                    name_focal_class = row['Focal_Class']
                    name_test_class = row['Test_Class']
                    test_path = _normalize_compiled_path(project, row['Test_Path'])
                    focal_path = _normalize_compiled_path(project, row['Focal_Path'])
                    last_execution = None # outcome of the last maven execution, True = Build Success, False = Build Failure
                    testing_framework = None
                    if junit_version is not None:
                        testing_framework = 'Junit version ' + junit_version
                    elif testng_version is not None:
                        testing_framework = 'testNG version ' + testng_version
                    try:
                        with open(focal_path, 'r') as focal_file:
                            focal_class = focal_file.read()
                    except Exception as e:
                        print(f"An error occured while trying to open and read the focal class: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                    

                    try:
                        with open(test_path, 'r', encoding='utf-8') as test_file_read:
                            human_test_class = test_file_read.read()
                            dictionary_for_restore[test_path] = human_test_class
                    except Exception as e:
                        print(f"An error occured while trying to open and read the test_path: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(project_path, "pom.xml"))
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True
                        break

                    ast_test_method, ast_focal_method = _extract_ast_method_pair(
                        test_path,
                        focal_path,
                        preferred_test_method=row.get("Test_Case"),
                        preferred_focal_method=row.get("Focal_Method"),
                    )
                    scoped_project_df = pd.DataFrame([row]).copy()
                    if ast_test_method:
                        scoped_project_df.loc[:, "AST_Test_Method"] = ast_test_method
                    if ast_focal_method:
                        scoped_project_df.loc[:, "AST_Focal_Method"] = ast_focal_method

                    print(f"\nDispatching Codex CLI for test_type: {test_type}, technique: {technique}, focal class: {name_focal_class}")
                    package_test_class = utils.find_package(test_path)

                    if technique == "iterative-healing":
                        flow_result = _execute_iterative_healing_flow(
                            project,
                            test_type,
                            technique,
                            name_focal_class,
                            name_test_class,
                            focal_class,
                            focal_path,
                            test_path,
                            testing_framework,
                            java_version,
                            has_mockito,
                            project_structure,
                            project_dependencies,
                            package_test_class,
                            scoped_project_df,
                            project_path,
                            system,
                            ast_test_method,
                            ast_focal_method,
                        )
                        last_execution = flow_result["last_execution"]
                        record_tracking_metrics(
                            name_test_class,
                            test_path,
                            test_type,
                            technique,
                            flow_result["chance"],
                            flow_result["total_prompt_tokens"],
                            flow_result["total_completion_tokens"],
                            flow_result["iterations_to_pass"],
                            flow_result.get("high_signal", "-"),
                            flow_result.get("signal_reason", "-"),
                        )
                        if flow_result["success"]:
                            print(f"Package command completed for {project}\n")
                        else:
                            print(f"Package command failed for project: {project}, test type: {test_type}, technique: {technique}\n")
                        continue

                    if technique == "regenerative-sync":
                        flow_result = _execute_regenerative_sync_flow(
                            project,
                            test_type,
                            technique,
                            name_focal_class,
                            name_test_class,
                            focal_class,
                            focal_path,
                            test_path,
                            testing_framework,
                            java_version,
                            has_mockito,
                            project_structure,
                            project_dependencies,
                            package_test_class,
                            scoped_project_df,
                            project_path,
                            system,
                            ast_test_method,
                            ast_focal_method,
                        )
                        last_execution = flow_result["last_execution"]
                        record_tracking_metrics(
                            name_test_class,
                            test_path,
                            test_type,
                            technique,
                            flow_result["chance"],
                            flow_result["total_prompt_tokens"],
                            flow_result["total_completion_tokens"],
                            flow_result["iterations_to_pass"],
                            flow_result.get("high_signal", "-"),
                            flow_result.get("signal_reason", "-"),
                        )
                        if flow_result["success"]:
                            print(f"Package command completed for {project}\n")
                        else:
                            print(f"Package command failed for project: {project}, test type: {test_type}, technique: {technique}\n")
                        continue

                    generated_test_content, messages, usage_metadata = utils.generate_test_with_codex(
                        test_type,
                        technique,
                        focal_class,
                        focal_path,
                        testing_framework,
                        java_version,
                        has_mockito,
                        test_path,
                        name_test_class,
                        project_structure,
                        project_dependencies,
                        package_test_class,
                    )
                    total_prompt_tokens = usage_metadata.get("prompt_tokens", 0)
                    total_completion_tokens = usage_metadata.get("completion_tokens", 0)
                    print(f"Codex CLI execution completed with test_type: {test_type}, technique: {technique}, focal class: {name_focal_class}")
                    if generated_test_content is None:
                        print(f"ERROR: Codex CLI did not produce a valid test class for {name_test_class}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                    
                    try:
                        with open(test_path, 'w') as test_file_write:
                            test_file_write.write(generated_test_content)
                        _persist_generated_response_artifact(
                            project,
                            test_type,
                            technique,
                            name_test_class,
                            generated_test_content,
                        )
                    except Exception as e:
                        print(f"An error occured while trying to open and read the test_path: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break


                    # Run the Maven package command
                    print("--//loading maven execution..//")
                    esito, errori = run_maven_test_command(
                        project_path,
                        scoped_project_df,
                        system,
                        ast_test_method=ast_test_method,
                        ast_focal_method=ast_focal_method,
                    )
                    if not esito and correct:  # Se il test Maven fallisce
                        chance_result = False
                        iterations_to_pass = 0
                        for num_chance in range(1, 5):
                            if chance_result:
                                break
                            chance_result, errori, correction_usage = errorCorrection.correct_errors(
                                project,
                                test_type,
                                technique,
                                test_path,
                                focal_path,
                                project_path,
                                scoped_project_df,
                                system,
                                messages,
                                errori,
                                dictionary_for_restore,
                                num_chance,
                                "Maven",
                                ast_test_method=ast_test_method,
                                ast_focal_method=ast_focal_method,
                            )
                            total_prompt_tokens += correction_usage.get("prompt_tokens", 0)
                            total_completion_tokens += correction_usage.get("completion_tokens", 0)
                            iterations_to_pass = num_chance
                            if chance_result:
                                last_execution = True
                                record_tracking_metrics(name_test_class, test_path, test_type, technique, num_chance, total_prompt_tokens, total_completion_tokens, iterations_to_pass)
                        errorCorrection.save_conversation_to_json(
                            messages,
                            name_test_class,
                            _worker_project_output_path(project, "codex_conversations"),
                        )
                        if not chance_result:
                            record_tracking_metrics(name_test_class, test_path, test_type, technique, 6, total_prompt_tokens, total_completion_tokens, iterations_to_pass)
                    elif not esito and not correct:
                        print(f"Package command failed for project: {project}, test type: {test_type}, technique: {technique}\n")
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 6, total_prompt_tokens, total_completion_tokens, 0)
                    elif esito:
                        last_execution = True
                        print(f"Package command completed for {project}\n")
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 0, total_prompt_tokens, total_completion_tokens, 0)

                if restart_technique == True:
                    continue

                    
                if project_df_technique.empty: # If all the test classes provided by the API failed during Maven execution
                    try:
                        with open(output_path_failed_maven, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed_maven: {e}')
                        sys.exit()
                else: # if at least one of the test classes provided by the API runned succesfully during Maven execution
                    # configure the test smell detector
                    csv_path_input_test_smell = utils.configure_test_smell_detector(project_df_technique, project)
                    # Run the test smell detector
                    path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, technique)
                    if path_csv_result_test_smell is None:
                        print("An error occured while trying to run the test smell detector")
                    else:
                        print("The test smell detector ended successfully")  
                    if last_execution == False: # if last maven execution outcome is False, then I run one more time maven
                        print("--//loading maven execution..//")
                        if run_maven_test_command(
                            project_path,
                            project_df,
                            system,
                            ast_test_method=project_ast_test_method,
                            ast_focal_method=project_ast_focal_method,
                        )[0]==False: # if error while running maven
                            print(
                                f"[{technique}] Final execution failed. "
                                "Recording mavenfailed marker for CSV failure row.\n"
                            )
                            try:
                                utils.write_files(dictionary_for_restore)
                                if os.path.exists(output_path_failed):
                                    os.remove(output_path_failed)
                                with open(output_path_failed_maven, 'w') as file:
                                    pass
                            except Exception as e:
                                original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                                utils.write_files(dictionary_for_restore)
                                print(f'An error occured while trying to open output_path_failed_maven: {e}')
                                sys.exit(1)
                            continue  # switch to next technique      
                    # Retrieve Code Coverage and Cyclomatic Complexity on test classes                            
                    utils.snapshot_coverage_reports(
                        project_path,
                        project_df_technique,
                        project,
                        'Maven',
                        test_type,
                        technique,
                    )
                    measures_df  = utils.retrieve_code_coverage_and_cyclomatic_complexity(
                        project_path,
                        project_df_technique,
                        project,
                        'Maven',
                        test_type=test_type,
                        technique=technique,
                    )
                    if measures_df is not None:
                        output_csv_path = utils.generate_output_csv_test_type(project, test_type, technique, measures_df, path_csv_result_test_smell)
                        if output_csv_path is None:
                            print("An errore occured while trying to save the test type csv file!")
                        else:
                            print(f"DataFrame saved to {output_csv_path}")
                    else:
                        print(f"An occured while trying to retrieve data coverage of the project {project}")
                        try:
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                    utils.write_files(dictionary_for_restore)
    if swtich_to_next_project == True:
        return 0
    original_pom.write(os.path.join(project_path, "pom.xml")) # restore pom to the original version



def process_maven_module(project, module, test_types, techniques, path, project_path, module_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, project_structure, project_dependencies, correct=False):
    """
    It processes the given Maven module with the given test types and techniques.
    Parameters:
                project: the ID of the project.
                module: the name of the module.
                test_types (List): the list of test types to execute.
                techniques (List): the list of prompt techniques (for the AI test types) to execute.
                path: the path of the module. 
                projecty_path: the path of the project.
                module_df: the dataframe that contains all the focal/test classes of the module.
                compilter_version: the Maven version of the given project.
                java_version: the Java version of the given project.
                junit_version: the Junit version of the given project.
                testng_version: the testNG version of the given project.
                has_mockito: the string that will be used to specify to the API whether the AI test types can use the Mockito framework or not.
                system (String): the current OS (Windows, Linux, etc...)
    Returns:
                0(int) if the process failed.
    """
    os.makedirs(PATH_CONTEXT.get_project_output_path(project), exist_ok=True)
    module_ast_test_method, module_ast_focal_method = _extract_ast_scope_from_dataframe(module_df)
    # add jacoco and pitest dependecies to pom.xml
    original_pom = edit_pom_file(
        path,
        module_df,
        junit_version,
        testng_version,
        ast_focal_method=module_ast_focal_method,
        ast_test_method=module_ast_test_method,
    )
    if original_pom is None:
        print("An errore occured while trying to edit the pom file")
        return 0
    for test_type in test_types:
        output_path_failed = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}.failed") # Indicates that the test type failed due to an error during the execution of the script.
        output_path_failed_maven = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}.mavenfailed")  # Indicates that all the test classes of the test type failed during the maven execution.

        swtich_to_next_test_type = False
        print('\n----')
        print(f"STARTING {test_type} test typ\n")
        if test_type == "human":
            # configure the test smell detector
            csv_path_input_test_smell = utils.configure_test_smell_detector(module_df, project)
            # Run the Maven package command
            print("--//loading maven execution..//")
            if run_maven_test_command(
                path,
                module_df,
                system,
                ast_test_method=module_ast_test_method,
                ast_focal_method=module_ast_focal_method,
            )[0]==True:
                print(f"Package command completed for {project}_{module}\n")
            else:
                print(f"Package command failed for {project}_{module}. Switch to next project/module...\n")
                original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                return 0 # analyze the next module

            # Run the test smell detector
            path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None, module)
            if path_csv_result_test_smell is None:
                print("An error occured while trying to run the test smell detector")
            else:
                print("The test smell detector ended successfully")  
            # Retrieve Code Coverage and Cyclomatic Complexity on test classes
            utils.snapshot_coverage_reports(
                PATH_CONTEXT.get_compiled_repo_path(project),
                module_df,
                project,
                'Maven',
                test_type,
                None,
                module,
            )
            measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(
                PATH_CONTEXT.get_compiled_repo_path(project),
                module_df,
                project,
                'Maven',
                module,
                test_type=test_type,
                technique=None,
            )
            if measures_df is None:
                print(f"Switch to next project/module because edit_pom_xml() failed for the project {project}_{module}")
                original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                return 0
            output_csv_path = utils.generate_output_csv_test_type(project, test_type, None, measures_df, path_csv_result_test_smell, module)
            if output_csv_path is None:
                print("An errore occured while trying to save the test type csv file!")
            else:
                print(f"DataFrame saved to {output_csv_path}")

        elif test_type == 'evosuite':
            module_df_evosuite = module_df.copy() # dataframe for the evosuite test type
            pom_before_evosuite = add_evosuite_pom(path)
            if pom_before_evosuite is None:
                try:
                    with open(output_path_failed, 'w') as file:
                        pass
                except Exception as e:
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                continue # Switch to next test type
            dictionary_for_restore = {} # dictionary that contains test_path as keys and the respective 'human_test_class' as values. This dictionary is used to restore the test classes to the human version.
            for index, row in module_df_evosuite.iterrows(): # iterate over each test class and focal class
                name_focal_class = row['Focal_Class']
                name_test_class = row['Test_Class']
                test_path = _normalize_compiled_path(project, row['Test_Path'])
                focal_path = _normalize_compiled_path(project, row['Focal_Path'])
                last_execution = None # outcome of the last maven execution, True = Build Success, False = Build Failure
                try:
                    with open(test_path, 'r') as test_file_read:
                        human_test_class = test_file_read.read() # save the human version of the test class
                        dictionary_for_restore[test_path] =  human_test_class   
                    os.remove(test_path)      
                except Exception as e:
                    print(f"An error occured while trying to open and read the test_path: {e}")
                    try:
                        pom_before_evosuite.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e: 
                        original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    swtich_to_next_test_type = True # Switch to next test type
                    break
                
                print("--//loading evosuite generation..//")
                if run_evosuite_generation_maven(path, focal_path, system) == False: # if error while running evosuite
                    print ("An error accored while trying to run the evosuite generation")
                    try:
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        pom_before_evosuite.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    swtich_to_next_test_type = True
                    break # Switch to next test type
                else:
                    print(f"Evosuite generation performed correctly for the {name_focal_class} class")

                evosuite_test_path = test_path.replace(f'{name_test_class}.java', f'{name_focal_class}_ESTest.java')
                try:
                    if not os.path.exists(evosuite_test_path):
                        project_df_evosuite = project_df_evosuite[project_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Maven execution
                        utils.remove_evosuite_scaffolding_files(list(test_path))
                        utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Maven execution
                        utils.remove_dot_evosuite_dir(project, module)
                        continue # Switch to next focal class/test class
                    with open(evosuite_test_path, 'r') as evosuite_file:
                        evosuite_content = evosuite_file.read()
                    evosuite_content = evosuite_content.replace(f'public class {name_focal_class}_ESTest', f'public class {name_test_class}') 
                    evosuite_content = evosuite_content.replace('separateClassLoader = true', 'separateClassLoader = false') # when setting separateClassLoader to false, JaCoCo can correctly calculate code coverage
                    with open(test_path, 'w') as test_file:
                        test_file.write(evosuite_content)
                    os.remove(evosuite_test_path)
                except Exception as e:
                    print(f"An error occured while trying to copy the evosuite class test: {e}")
                    try:
                        pom_before_evosuite.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_dot_evosuite_dir(project, module)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_dot_evosuite_dir(project, module)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    swtich_to_next_test_type = True
                    break # Switch to next test type
                print("--//loading maven execution..//")
                if run_maven_test_command(
                    path,
                    module_df,
                    system,
                    ast_test_method=module_ast_test_method,
                    ast_focal_method=module_ast_focal_method,
                )[0]==False: # if error while running maven
                    print(f"Package command failed for project: {project}_{module}, test type: {test_type}\n")
                    module_df_evosuite = module_df_evosuite[module_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Maven execution
                    utils.remove_evosuite_scaffolding_files(list(test_path))
                    utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Maven execution
                    utils.remove_dot_evosuite_dir(project, module)
                    last_execution = False
                    continue # Switch to next focal class/test class
                else:
                    last_execution = True
                    print(f"Package command completed for {project}_{module}\n")

                utils.remove_dot_evosuite_dir(project, module)
                
            if swtich_to_next_test_type == True:
                continue

                
                    
            if module_df_evosuite.empty: # If all the test classes provided by Evosuite failed during Maven execution
                try:
                    with open(output_path_failed_maven, 'w') as file:
                        pass
                except Exception as e:
                    original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                    utils.write_files(dictionary_for_restore)
                    utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                    print(f'An error occured while trying to open output_path_failed_maven: {e}')
                    sys.exit(1)
            else: # if at least one of the test classes provided by Evosuite runned succesfully during Maven execution
                # configure the test smell detector
                csv_path_input_test_smell = utils.configure_test_smell_detector(module_df_evosuite, project)
                # Run the test smell detector
                path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None, module)
                if path_csv_result_test_smell is None:
                    print("An error occured while trying to run the test smell detector")
                else:
                    print("The test smell detector ended successfully")  
                if last_execution == False: # if last maven execution outcome is False, then I run one more time maven
                    print("--//loading maven execution..//")
                    if run_maven_test_command(
                        path,
                        module_df,
                        system,
                        ast_test_method=module_ast_test_method,
                        ast_focal_method=module_ast_focal_method,
                    )[0]==False: # if error while running maven
                        print('An error occured while trying to execute the final version of test classes.\n')
                        try:
                            pom_before_evosuite.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        continue # Switch to next test type
                # Retrieve Code Coverage and Cyclomatic Complexity on test classes
                utils.snapshot_coverage_reports(
                    PATH_CONTEXT.get_compiled_repo_path(project),
                    module_df_evosuite,
                    project,
                    'Maven',
                    test_type,
                    None,
                    module,
                )
                measures_df  = utils.retrieve_code_coverage_and_cyclomatic_complexity(
                    PATH_CONTEXT.get_compiled_repo_path(project),
                    module_df_evosuite,
                    project,
                    'Maven',
                    module,
                    test_type=test_type,
                    technique=None,
                )
                if measures_df is not None:
                    output_csv_path = utils.generate_output_csv_test_type(project, test_type, None, measures_df, path_csv_result_test_smell, module)
                    if output_csv_path is None:
                        print("An errore occured while trying to save the test type csv file!")
                    else:
                        print(f"DataFrame saved to {output_csv_path}")
                else:
                    print(f"An occured while trying to retrieve data coverage of the project {project}_{module}")
                    try:
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                
            utils.write_files(dictionary_for_restore)
            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
            pom_before_evosuite.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                    



        else:
            # Iterate over each technique
            for technique in techniques:  
                output_path_failed = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}_{technique}.failed") # Indicates that the test type/technique failed due to an error during the execution of AgonTest.py or during a call to the API
                output_path_failed_maven = _worker_project_output_path(project, f"TestClasses_{project}_{test_type}_{technique}.mavenfailed")  # Indicates that all the test classes of the test type failed during the maven execution.
                restart_technique = False 
                print(f"\nProcessing test_type: {test_type}, technique: {technique}")
                module_df_technique = module_df.copy() # dataframe of the current test type and technique
                dictionary_for_restore = {} # dictionary that contains test_path as keys and the respective 'human_test_class' as values. This dictionary is used to restore the test classes to the human version.
                for index, row in module_df_technique.iterrows(): # iterate over each test class and focal class
                    name_focal_class = row['Focal_Class']
                    name_test_class = row['Test_Class']
                    focal_path = _normalize_compiled_path(project, row['Focal_Path'])
                    test_path = _normalize_compiled_path(project, row['Test_Path'])
                    last_execution = None # outcome of the last maven execution, True = Build Success, False = Build Failure
                    testing_framework = None
                    if junit_version is not None:
                        testing_framework = 'Junit version ' + junit_version
                    elif testng_version is not None:
                        testing_framework = 'testNG version ' + testng_version
                    try:
                        with open(focal_path, 'r') as focal_file:
                            focal_class = focal_file.read()
                    except Exception as e:
                        print(f"An error occured while trying to open and read the focal class: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True #Switch to next technique
                        break 
                    
                    
                    try:
                        with open(test_path, 'r', encoding='utf-8') as test_file_read:
                            human_test_class = test_file_read.read()
                            dictionary_for_restore[test_path] = human_test_class
                    except Exception as e:
                        print(f"An error occured while trying to open and read the test_path: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(path, "pom.xml"))
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True
                        break

                    ast_test_method, ast_focal_method = _extract_ast_method_pair(
                        test_path,
                        focal_path,
                        preferred_test_method=row.get("Test_Case"),
                        preferred_focal_method=row.get("Focal_Method"),
                    )
                    scoped_module_df = pd.DataFrame([row]).copy()
                    if ast_test_method:
                        scoped_module_df.loc[:, "AST_Test_Method"] = ast_test_method
                    if ast_focal_method:
                        scoped_module_df.loc[:, "AST_Focal_Method"] = ast_focal_method

                    print(f"\nDispatching Codex CLI for test_type: {test_type}, technique: {technique}, focal class: {name_focal_class}")
                    package_test_class = utils.find_package(test_path)

                    if technique == "iterative-healing":
                        flow_result = _execute_iterative_healing_flow(
                            project,
                            test_type,
                            technique,
                            name_focal_class,
                            name_test_class,
                            focal_class,
                            focal_path,
                            test_path,
                            testing_framework,
                            java_version,
                            has_mockito,
                            project_structure,
                            project_dependencies,
                            package_test_class,
                            scoped_module_df,
                            path,
                            system,
                            ast_test_method,
                            ast_focal_method,
                        )
                        last_execution = flow_result["last_execution"]
                        record_tracking_metrics(
                            name_test_class,
                            test_path,
                            test_type,
                            technique,
                            flow_result["chance"],
                            flow_result["total_prompt_tokens"],
                            flow_result["total_completion_tokens"],
                            flow_result["iterations_to_pass"],
                            flow_result.get("high_signal", "-"),
                            flow_result.get("signal_reason", "-"),
                        )
                        if not flow_result["success"]:
                            print(f"Package command failed for project: {project}_{module}, test type: {test_type}, technique: {technique}\n")
                            module_df_technique = module_df_technique[module_df_technique['Test_Path'] != row['Test_Path']]
                            utils.write_file(test_path, human_test_class)
                            last_execution = False
                            continue
                        print(f"Package command completed for {project}_{module}\n")
                        continue

                    if technique == "regenerative-sync":
                        flow_result = _execute_regenerative_sync_flow(
                            project,
                            test_type,
                            technique,
                            name_focal_class,
                            name_test_class,
                            focal_class,
                            focal_path,
                            test_path,
                            testing_framework,
                            java_version,
                            has_mockito,
                            project_structure,
                            project_dependencies,
                            package_test_class,
                            scoped_module_df,
                            path,
                            system,
                            ast_test_method,
                            ast_focal_method,
                        )
                        last_execution = flow_result["last_execution"]
                        record_tracking_metrics(
                            name_test_class,
                            test_path,
                            test_type,
                            technique,
                            flow_result["chance"],
                            flow_result["total_prompt_tokens"],
                            flow_result["total_completion_tokens"],
                            flow_result["iterations_to_pass"],
                            flow_result.get("high_signal", "-"),
                            flow_result.get("signal_reason", "-"),
                        )
                        if not flow_result["success"]:
                            print(f"Package command failed for project: {project}_{module}, test type: {test_type}, technique: {technique}\n")
                            module_df_technique = module_df_technique[module_df_technique['Test_Path'] != row['Test_Path']]
                            utils.write_file(test_path, human_test_class)
                            last_execution = False
                            continue
                        print(f"Package command completed for {project}_{module}\n")
                        continue

                    generated_test_content, messages, usage_metadata = utils.generate_test_with_codex(
                        test_type,
                        technique,
                        focal_class,
                        focal_path,
                        testing_framework,
                        java_version,
                        has_mockito,
                        test_path,
                        name_test_class,
                        project_structure,
                        project_dependencies,
                        package_test_class,
                    )
                    total_prompt_tokens = usage_metadata.get("prompt_tokens", 0)
                    total_completion_tokens = usage_metadata.get("completion_tokens", 0)
                    print(f"Codex CLI execution completed with test_type: {test_type}, technique: {technique}, focal class: {name_focal_class}")
                    if generated_test_content is None:
                        print(f"ERROR: Codex CLI did not produce a valid test class for {name_test_class}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                    
                    try:
                        with open(test_path, 'w') as test_file_write:
                            test_file_write.write(generated_test_content)
                        _persist_generated_response_artifact(
                            project,
                            test_type,
                            technique,
                            name_test_class,
                            generated_test_content,
                        )
                    except Exception as e:
                        print(f"An error occured while trying to open and read the test_path: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                                

                    # Run the Maven package command
                    print("--//loading maven execution..//")
                    esito, errori = run_maven_test_command(
                        path,
                        scoped_module_df,
                        system,
                        ast_test_method=ast_test_method,
                        ast_focal_method=ast_focal_method,
                    )
                    if not esito and correct:
                        chance_result = False
                        iterations_to_pass = 0
                        for num_chance in range(1, 5):
                            if chance_result:
                                break
                            chance_result, errori, correction_usage = errorCorrection.correct_errors(
                                project,
                                test_type,
                                technique,
                                test_path,
                                focal_path,
                                path,
                                scoped_module_df,
                                system,
                                messages,
                                errori,
                                dictionary_for_restore,
                                num_chance,
                                "Maven",
                                ast_test_method=ast_test_method,
                                ast_focal_method=ast_focal_method,
                            )
                            total_prompt_tokens += correction_usage.get("prompt_tokens", 0)
                            total_completion_tokens += correction_usage.get("completion_tokens", 0)
                            iterations_to_pass = num_chance
                            if chance_result:
                                last_execution = True
                                record_tracking_metrics(name_test_class, test_path, test_type, technique, num_chance, total_prompt_tokens, total_completion_tokens, iterations_to_pass)
                        if not chance_result:
                            print(f"Package command failed for project: {project}_{module}, test type: {test_type}, technique: {technique}\n")
                            module_df_technique = module_df_technique[module_df_technique['Test_Path'] != row['Test_Path']]
                            utils.write_file(test_path, human_test_class)
                            record_tracking_metrics(name_test_class, test_path, test_type, technique, 6, total_prompt_tokens, total_completion_tokens, iterations_to_pass)
                            last_execution = False
                            continue
                    elif not esito:
                        print(f"Package command failed for project: {project}_{module}, test type: {test_type}, technique: {technique}\n")
                        module_df_technique = module_df_technique [module_df_technique['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Maven execution
                        utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Maven execution
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 6, total_prompt_tokens, total_completion_tokens, 0)
                        last_execution = False
                        continue
                    else:
                        last_execution = True
                        print(f"Package command completed for {project}_{module}\n")
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 0, total_prompt_tokens, total_completion_tokens, 0)

                if restart_technique == True:
                    continue

                    
                if module_df_technique.empty: # If all the test classes provided by the API failed during Maven execution
                    try:
                        with open(output_path_failed_maven, 'w') as file:
                            pass
                    except Exception as e:
                        original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed_maven: {e}')
                        sys.exit()
                else: # if at least one of the test classes provided by the API runned succesfully during Maven execution
                    # configure the test smell detector
                    csv_path_input_test_smell = utils.configure_test_smell_detector(module_df_technique, project)
                    # Run the test smell detector
                    path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, technique, module)
                    if path_csv_result_test_smell is None:
                        print("An error occured while trying to run the test smell detector")
                    else:
                        print("The test smell detector ended successfully")  
                    if last_execution == False: # if last maven execution outcome is False, then I run one more time maven
                        print("--//loading maven execution..//")
                        if run_maven_test_command(
                            path,
                            module_df,
                            system,
                            ast_test_method=module_ast_test_method,
                            ast_focal_method=module_ast_focal_method,
                        )[0]==False: # if error while running maven
                            print(
                                f"[{technique}] Final execution failed. "
                                "Recording mavenfailed marker for CSV failure row.\n"
                            )
                            try:
                                utils.write_files(dictionary_for_restore)
                                if os.path.exists(output_path_failed):
                                    os.remove(output_path_failed)
                                with open(output_path_failed_maven, 'w') as file:
                                    pass
                            except Exception as e:
                                original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                                utils.write_files(dictionary_for_restore)
                                print(f'An error occured while trying to open output_path_failed_maven: {e}')
                                sys.exit(1)
                            continue  # switch to next technique      
                    # Retrieve Code Coverage and Cyclomatic Complexity on test classes
                    utils.snapshot_coverage_reports(
                        project_path,
                        module_df_technique,
                        project,
                        'Maven',
                        test_type,
                        technique,
                        module,
                    )
                    measures_df  = utils.retrieve_code_coverage_and_cyclomatic_complexity(
                        project_path,
                        module_df_technique,
                        project,
                        'Maven',
                        module,
                        test_type=test_type,
                        technique=technique,
                    )
                    if measures_df is not None:
                        output_csv_path = utils.generate_output_csv_test_type(project, test_type, technique, measures_df, path_csv_result_test_smell, module)
                        if output_csv_path is None:
                            print("An errore occured while trying to save the test type csv file!")
                        else:
                            print(f"DataFrame saved to {output_csv_path}")
                    else:
                        print(f"An errore occured while trying to retrieve data coverage of the project {project}_{module}")
                        try:
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            original_pom.write(os.path.join(path, "pom.xml")) # restore pom to previous version
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                    utils.write_files(dictionary_for_restore)
    original_pom.write(os.path.join(path, "pom.xml")) # restore pom to the original version 
