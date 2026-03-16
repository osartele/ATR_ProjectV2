import json
import os
import re

import gradleLib
import mavenLib
import project_structure_analyzer as psa
import utils


RUNTIME_FAILURE_PATTERN = re.compile(
    r"((?:org\.opentest4j\.[^\n]+|java\.lang\.(?:AssertionError|[A-Za-z]+Exception)[^\n]*|"
    r"junit\.framework\.AssertionFailedError[^\n]*|AssertionFailedError[^\n]*)"
    r"(?:\n(?:\s+at .+|Caused by:.+|\s*\.\.\. .+|\[ERROR\].+|[A-Za-z0-9_.:$]+: .+|expected:.*|but was:.*))*)",
    re.MULTILINE,
)


def _normalize_generated_class(result):
    if result is None:
        return None
    match = re.search(r"```(?:java)?\s*(.*?)```", result, re.DOTALL)
    if match:
        return match.group(1).strip()
    return result.strip()


def _normalize_project_test_path(project, test_path):
    if test_path.startswith("compiledrepos/"):
        return test_path
    if test_path.startswith("repos/"):
        return test_path.replace("repos/", "compiledrepos/")
    return f"compiledrepos/{project}/" + test_path


def _resolve_focal_path(project, row):
    focal_path = row.get("Focal_Path")
    if focal_path is None:
        return None
    if focal_path.startswith("compiledrepos/"):
        return focal_path
    if focal_path.startswith("repos/"):
        return focal_path.replace("repos/", "compiledrepos/")
    return f"compiledrepos/{project}/" + focal_path


def _is_runtime_failure(errors):
    if not errors:
        return False
    lowered_errors = errors.lower()
    return (
        "runtime failure" in lowered_errors
        or "assertionerror" in lowered_errors
        or "assertionfailederror" in lowered_errors
        or "the test compiled, but failed at runtime" in lowered_errors
    )


def _extract_referenced_test_methods(errors, test_path):
    if not errors:
        return []

    test_class_name = os.path.splitext(os.path.basename(test_path))[0]
    method_pattern = re.compile(
        rf"{re.escape(test_class_name)}\.([A-Za-z_][A-Za-z0-9_]*)\(",
    )
    referenced_methods = []
    for method_name in method_pattern.findall(errors):
        if method_name not in referenced_methods:
            referenced_methods.append(method_name)
    return referenced_methods


def _format_ast_repair_context(ast_context):
    if not ast_context:
        return ""

    context_sections = []
    invocations_by_test = ast_context.get("invocations_by_test", {})
    if invocations_by_test:
        formatted_invocations = []
        for test_method_name, invocation_entries in invocations_by_test.items():
            call_sites = []
            for invocation_entry in invocation_entries:
                invocation_line = invocation_entry.get("source_line") or invocation_entry.get("invocation")
                matched_methods = ", ".join(invocation_entry.get("matched_focal_methods", [])) or "unknown focal method"
                line_number = invocation_entry.get("line")
                if line_number is not None:
                    call_sites.append(
                        f"line {line_number}: {invocation_line} -> {matched_methods}"
                    )
                else:
                    call_sites.append(f"{invocation_line} -> {matched_methods}")
            formatted_invocations.append(
                f"JUnit test method {test_method_name}:\n" + "\n".join(call_sites)
            )
        context_sections.append(
            "Relevant focal invocation call sites from JUnit tests:\n"
            + "\n\n".join(formatted_invocations)
        )
        context_sections.append(
            "AST-mapped JUnit test to focal method mapping:\n"
            + json.dumps(ast_context.get("mapping", {}), indent=2)
        )

    mapped_focal_method_sources = ast_context.get("mapped_focal_method_sources", {})
    if mapped_focal_method_sources:
        formatted_focal_methods = []
        for focal_method_name, source_blocks in mapped_focal_method_sources.items():
            for source_block in source_blocks:
                formatted_focal_methods.append(
                    f"Focal method {focal_method_name}:\n{source_block}"
                )
        context_sections.append(
            "Relevant focal method implementations:\n" + "\n\n".join(formatted_focal_methods)
        )

    return "\n\n".join(context_sections)


def _extract_focal_method_logic(project, test_path, project_df, errors=""):
    normalized_test_path = _normalize_project_test_path(project, test_path)

    target_row = None
    for _, row in project_df.iterrows():
        row_test_path = row.get("Test_Path")
        if row_test_path is None:
            continue
        candidate_test_path = _normalize_project_test_path(project, str(row_test_path))
        if candidate_test_path == normalized_test_path:
            target_row = row
            break

    if target_row is None:
        return ""

    focal_path = _resolve_focal_path(project, target_row)
    if focal_path is None or not os.path.isfile(focal_path):
        return ""

    selected_test_methods = _extract_referenced_test_methods(errors, normalized_test_path)
    try:
        ast_context = psa.build_ast_prompt_context(
            normalized_test_path,
            focal_path,
            selected_test_methods=selected_test_methods,
        )
    except Exception:
        return ""
    if not ast_context.get("invocations_by_test") and selected_test_methods:
        try:
            ast_context = psa.build_ast_prompt_context(normalized_test_path, focal_path)
        except Exception:
            return ""

    return _format_ast_repair_context(ast_context)


def correct_errors(
    project,
    test_type,
    technique,
    test_path,
    focal_path,
    project_path,
    project_df,
    system,
    messages,
    errori,
    dictionary_for_restore,
    chance,
    type_project,
    ast_test_method=None,
    ast_focal_method=None,
):
    print(f"Package command failed for project: {project}, test type: {test_type}, technique: {technique}\n")

    failed_dir = "./failed_classes"
    if not os.path.exists(failed_dir):
        os.makedirs(failed_dir)

    save_class(test_path, "_failed", failed_dir)
    print(f"{chance} CHANCE")

    focal_method_logic = _extract_focal_method_logic(project, test_path, project_df, errori)
    corrected_class, success, usage_metadata = conversation(
        test_type,
        errori,
        messages,
        chance,
        test_path,
        focal_path,
        focal_method_logic=focal_method_logic,
    )
    save_class(test_path, "_processed", failed_dir)

    if success:
        with open(test_path, "w", encoding="utf-8") as test_file_write:
            test_file_write.write(corrected_class)

        if type_project == "Maven":
            esito, errori = mavenLib.run_maven_test_command(
                project_path,
                project_df,
                system,
                ast_test_method=ast_test_method,
                ast_focal_method=ast_focal_method,
            )
        else:
            esito, errori = gradleLib.run_gradle_test_command(project_path, project_df, system)

        if esito:
            print("Test passed with the corrected class.")
            save_class(test_path, "_corrected", failed_dir)
            print(
                "Classe di test corretta salvata in: "
                + os.path.join(failed_dir, os.path.basename(test_path).replace(".java", "_corrected.java"))
            )
            print(f"Package command completed for {project}\n")
            return True, None, usage_metadata

        print("Test failed again with the corrected class.\n")
        print(
            "Ripristino della classe originale a causa del fallimento del test Maven con la classe corretta, "
            f"nel seguente path di salvataggio: {test_path}\n"
        )
        restore_original_class(test_path, dictionary_for_restore)
        return False, errori, usage_metadata

    print("Conversation did not return a successful correction.")
    return False, None, usage_metadata


def conversation(test_type, errors, messages, num_chance, test_path, focal_path, focal_method_logic=""):
    """
    Repairs the existing test class through the Codex CLI based on compilation
    failures or runtime test failures.
    """
    if _is_runtime_failure(errors):
        prompt_instruction = conversation_healing_messages(
            errors,
            focal_method_logic,
            num_chance,
            test_path,
            focal_path,
        )
    else:
        prompt_instruction = conversation_messages(
            errors,
            num_chance,
            test_path,
            focal_path,
            focal_method_logic,
        )

    messages.append({"role": "user", "content": prompt_instruction})
    execution_result = utils.run_codex_agent(prompt_instruction, [test_path, focal_path])
    execution_summary = utils._summarize_codex_execution(execution_result)
    messages.append({"role": "assistant", "content": execution_summary})
    if os.getenv("AGONE_SMOKE_TEST", "0").strip().lower() in {"1", "true", "yes", "on"} or not execution_result.get("success"):
        print(f"Codex repair summary:\n{execution_summary}")
    usage_metadata = execution_result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0})

    if not execution_result.get("success"):
        return None, False, usage_metadata

    try:
        with open(test_path, "r", encoding="utf-8") as corrected_test_file:
            corrected_class = corrected_test_file.read()
    except Exception:
        return None, False, usage_metadata

    corrected_class = _normalize_generated_class(corrected_class)
    if corrected_class is None:
        return None, False, usage_metadata

    utils.write_file(test_path, corrected_class)
    return corrected_class, True, usage_metadata


def conversation_messages(errors, num_chance, test_path, focal_path, focal_method_logic=""):
    if num_chance == 1:
        return (
            f"The Java test file at {test_path} no longer compiles against the mutated focal class at {focal_path}.\n\n"
            "Analyze the compilation errors below and repair the test file in place.\n"
            "Preserve the package declaration and class name, keep working logic intact, and do not modify the focal class.\n"
            "Use the AST-mapped JUnit-to-focal context below as your primary context.\n"
            "Respond by saving valid Java code directly into the test file.\n\n"
            "Relevant AST-mapped context:\n"
            f"{focal_method_logic or 'AST-mapped context unavailable.'}\n\n"
            f"Compilation errors:\n{errors}"
        )

    return (
        f"The previous repair attempt for {test_path} did not compile.\n\n"
        "Review the remaining errors, keep the existing structure where possible, and finish repairing the test file in place.\n"
        "Use the AST-mapped JUnit-to-focal context below as your primary context. Do not modify the focal class.\n\n"
        "Relevant AST-mapped context:\n"
        f"{focal_method_logic or 'AST-mapped context unavailable.'}\n\n"
        f"Compilation errors:\n{errors}"
    )


def conversation_healing_messages(errors, focal_method_logic, num_chance, test_path, focal_path):
    if num_chance == 1:
        return (
            f"The test {os.path.basename(test_path)} failed with this assertion trace:\n{errors}\n\n"
            f"Analyze the modified focal class {focal_path} and repair the assertions in the test file in place.\n\n"
            "Use this AST-mapped JUnit-to-focal context as your primary context:\n"
            f"{focal_method_logic or 'Focal method source unavailable.'}\n\n"
            "Do not modify the focal class. Save the corrected Java test code directly to disk."
        )

    return (
        f"The previous runtime repair for {os.path.basename(test_path)} did not pass.\n\n"
        "Re-check the failing assertion logic against the focal method behavior and update the test file in place.\n\n"
        f"Assertion trace:\n{errors}\n\n"
        "Use this AST-mapped JUnit-to-focal context as your primary context:\n"
        f"{focal_method_logic or 'Focal method source unavailable.'}\n\n"
        "Save only valid Java test code to the existing file."
    )


def save_class(test_path, suffix, save_dir):
    filename = os.path.basename(test_path).replace(".java", f"{suffix}.java")
    save_path = os.path.join(save_dir, filename)

    with open(test_path, "r", encoding="utf-8") as test_file:
        content = test_file.read()
    with open(save_path, "w", encoding="utf-8") as file:
        file.write(content)

    return save_path


def restore_original_class(test_path, dictionary_for_restore):
    with open(test_path, "w", encoding="utf-8") as test_file_write:
        test_file_write.write(dictionary_for_restore[test_path])


def _extract_runtime_failure(log_text, tool_name):
    runtime_match = RUNTIME_FAILURE_PATTERN.search(log_text or "")
    if runtime_match:
        stack_trace = runtime_match.group(1).strip()
        return f"The test compiled, but failed at runtime with this stack trace:\n{stack_trace}"

    runtime_summary = re.search(
        r"(Tests run: .*?(?:Failures|Errors): .*?)(?:\n|$)([\s\S]*?)(?=\n\[INFO\]|\nBUILD |\Z)",
        log_text or "",
        re.DOTALL,
    )
    if runtime_summary:
        summary = runtime_summary.group(0).strip()
        if "Failures:" in summary or "Errors:" in summary:
            return f"The test compiled, but failed at runtime with this stack trace:\n{summary}"

    return None


def extract_errors(stdout: str, stderr: str):
    combined_output = "\n".join(part for part in [stdout, stderr] if part)

    error_pattern = re.compile(r"\[ERROR\] COMPILATION ERROR :(.*?)(?=\[INFO\]|\Z)", re.DOTALL)
    errors = error_pattern.findall(combined_output)
    if errors:
        cleaned_errors = errors[0].strip()
        cleaned_lines = [
            line.replace("[INFO] -------------------------------------------------------------", "")
            .replace("[ERROR]", "")
            .strip()
            for line in cleaned_errors.splitlines()
            if line.strip()
        ]
        formatted_errors = "The following compilation errors were encountered during the Maven build:\n"
        formatted_errors += "\n- " + "\n- ".join(cleaned_lines)
        return formatted_errors

    runtime_errors = _extract_runtime_failure(combined_output, "Maven")
    if runtime_errors is not None:
        return runtime_errors

    cleaned_stderr = [
        line.strip() for line in (stderr or "").splitlines() if line.strip() and "[ERROR]" in line
    ]
    if cleaned_stderr:
        formatted_errors = "The following errors were encountered during the Maven build (from stderr):\n"
        formatted_errors += "\n- " + "\n- ".join(cleaned_stderr)
        return formatted_errors

    error_lines = []
    capturing = False
    for line in (stdout or "").splitlines():
        if "[ERROR]" in line:
            capturing = True
            error_lines.append(line.replace("[ERROR]", "").strip())
        elif capturing:
            if "[INFO]" in line:
                capturing = False
            else:
                error_lines.append(line.strip())

    if error_lines:
        formatted_errors = "The following general errors were encountered during the Maven build:\n"
        formatted_errors += "\n- " + "\n- ".join(error_lines)
        return formatted_errors
    return "No compilation errors or general issues found in the Maven output."


def extract_gradle_errors(stdout: str, stderr: str):
    combined_output = "\n".join(part for part in [stdout, stderr] if part)

    runtime_errors = _extract_runtime_failure(combined_output, "Gradle")
    if runtime_errors is not None:
        return runtime_errors

    error_pattern = re.compile(r"> Task :(.*?):.*?FAILED", re.DOTALL)
    errors = error_pattern.findall(combined_output)
    if errors:
        formatted_errors = "The following task errors were encountered during the Gradle build:\n"
        formatted_errors += "\n- " + "\n- ".join(errors)
        return formatted_errors

    cleaned_stderr = [
        line.strip()
        for line in (stderr or "").splitlines()
        if line.strip() and ("[ERROR]" in line or "FAILED" in line)
    ]
    if cleaned_stderr:
        formatted_errors = "The following errors were encountered during the Gradle build (from stderr):\n"
        formatted_errors += "\n- " + "\n- ".join(cleaned_stderr)
        return formatted_errors

    error_lines = []
    capturing = False
    for line in (stdout or "").splitlines():
        if "[ERROR]" in line or "FAILED" in line:
            capturing = True
            error_lines.append(line.replace("[ERROR]", "").strip())
        elif capturing:
            if "[INFO]" in line:
                capturing = False
            else:
                error_lines.append(line.strip())

    if error_lines:
        formatted_errors = "The following general errors were encountered during the Gradle build:\n"
        formatted_errors += "\n- " + "\n- ".join(error_lines)
        return formatted_errors
    return "No compilation errors or general issues found in the Gradle output."


def save_conversation_to_json(messages, class_name, save_path="."):
    file_name = f"conversation_{class_name}.json"
    full_path = os.path.join(save_path, file_name)

    conversation_data = []
    for i, message in enumerate(messages):
        if isinstance(message, dict):
            role = message.get("role", "unknown").capitalize()
            content = message.get("content", "")
        else:
            role = "Unknown"
            content = str(message)

        conversation_data.append(
            {
                "messaggio_numero": i + 1,
                "ruolo": role,
                "contenuto": content,
            }
        )

    os.makedirs(save_path, exist_ok=True)

    with open(full_path, "w", encoding="utf-8") as file:
        json.dump(conversation_data, file, indent=4, ensure_ascii=False)

    return full_path
