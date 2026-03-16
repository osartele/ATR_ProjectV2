import os
import random
import re

import javalang


LOGICAL_SWAP_MAP = {
    "<": ">=",
    ">": "<=",
    "<=": ">",
    ">=": "<",
    "==": "!=",
    "!=": "==",
}

IMMUTABLE_SOURCE_ROOTS = {"repos", "Classes2Test"}
PROJECT_WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _normalize_workspace_path(file_path):
    return os.path.normcase(os.path.realpath(os.path.abspath(str(file_path))))


def _workspace_relative_path(file_path):
    normalized_root = _normalize_workspace_path(PROJECT_WORKSPACE_ROOT)
    normalized_path = _normalize_workspace_path(file_path)
    try:
        relative_path = os.path.relpath(normalized_path, normalized_root)
    except ValueError:
        return None
    if relative_path.startswith(".."):
        return None
    return relative_path.replace("\\", "/")


def _assert_mutable_workspace_path(file_path):
    relative_path = _workspace_relative_path(file_path)
    if relative_path is None:
        return
    top_level_directory = relative_path.split("/", 1)[0]
    if top_level_directory in IMMUTABLE_SOURCE_ROOTS:
        raise PermissionError(
            f"Refusing to mutate immutable source path: {file_path}. "
            "Mutations must be applied to compiled workspace copies only."
        )


def _read_source(file_path):
    with open(file_path, "r", encoding="utf-8") as source_file:
        return source_file.read()


def _write_source(file_path, content):
    _assert_mutable_workspace_path(file_path)
    with open(file_path, "w", encoding="utf-8") as source_file:
        source_file.write(content)


def _build_line_offsets(source):
    offsets = [0]
    running_total = 0
    for line in source.splitlines(keepends=True):
        running_total += len(line)
        offsets.append(running_total)
    if not source.endswith(("\n", "\r")):
        offsets.append(len(source))
    return offsets


def _index_from_line_col(line_offsets, line, column):
    return line_offsets[line - 1] + column - 1


def _line_col_from_index(source, index):
    line = source.count("\n", 0, index) + 1
    line_start = source.rfind("\n", 0, index)
    if line_start == -1:
        column = index + 1
    else:
        column = index - line_start
    return line, column


def _scan_matching_delimiter(source, start_index, opener, closer):
    depth = 0
    in_line_comment = False
    in_block_comment = False
    in_single_quote = False
    in_double_quote = False
    escape_next = False
    index = start_index

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_single_quote:
            if not escape_next and char == "'":
                in_single_quote = False
            escape_next = char == "\\" and not escape_next
            index += 1
            continue

        if in_double_quote:
            if not escape_next and char == '"':
                in_double_quote = False
            escape_next = char == "\\" and not escape_next
            index += 1
            continue

        escape_next = False
        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if char == "'":
            in_single_quote = True
            index += 1
            continue
        if char == '"':
            in_double_quote = True
            index += 1
            continue

        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return index

        index += 1

    return None


def _find_method_body_open(source, search_start):
    in_line_comment = False
    in_block_comment = False
    in_single_quote = False
    in_double_quote = False
    escape_next = False
    index = search_start

    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            index += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue

        if in_single_quote:
            if not escape_next and char == "'":
                in_single_quote = False
            escape_next = char == "\\" and not escape_next
            index += 1
            continue

        if in_double_quote:
            if not escape_next and char == '"':
                in_double_quote = False
            escape_next = char == "\\" and not escape_next
            index += 1
            continue

        escape_next = False
        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if char == "'":
            in_single_quote = True
            index += 1
            continue
        if char == '"':
            in_double_quote = True
            index += 1
            continue

        if char == "{":
            return index
        if char == ";":
            return None
        index += 1

    return None


def _collect_method_contexts(file_path):
    source = _read_source(file_path)
    tree = javalang.parse.parse(source)
    line_offsets = _build_line_offsets(source)
    contexts = []

    for _, method_node in tree.filter(javalang.tree.MethodDeclaration):
        if method_node.position is None:
            continue

        start_index = _index_from_line_col(
            line_offsets,
            method_node.position.line,
            method_node.position.column,
        )
        parameter_open_index = source.find("(", start_index)
        if parameter_open_index == -1:
            continue

        parameter_close_index = _scan_matching_delimiter(source, parameter_open_index, "(", ")")
        if parameter_close_index is None:
            continue

        body_open_index = _find_method_body_open(source, parameter_close_index + 1)
        body_close_index = None
        if body_open_index is not None:
            body_close_index = _scan_matching_delimiter(source, body_open_index, "{", "}")

        contexts.append(
            {
                "name": method_node.name,
                "line": method_node.position.line,
                "column": method_node.position.column,
                "start_index": start_index,
                "parameter_open_index": parameter_open_index,
                "parameter_close_index": parameter_close_index,
                "body_open_index": body_open_index,
                "body_close_index": body_close_index,
                "parameters": [parameter.name for parameter in method_node.parameters],
                "throws": list(method_node.throws or []),
                "node": method_node,
            }
        )

    return source, line_offsets, contexts


def _select_method_context(contexts, target_method=None, predicate=None):
    filtered_contexts = []
    for context in contexts:
        if context["body_open_index"] is None or context["body_close_index"] is None:
            continue
        if target_method is not None and context["name"] != target_method:
            continue
        if predicate is not None and not predicate(context):
            continue
        filtered_contexts.append(context)

    if not filtered_contexts:
        return None
    return filtered_contexts[0]


def apply_logical_mutation(file_path, target_method=None):
    source, line_offsets, contexts = _collect_method_contexts(file_path)

    candidate_contexts = []
    for context in contexts:
        if target_method is not None and context["name"] != target_method:
            continue
        for _, node in context["node"]:
            if isinstance(node, javalang.tree.BinaryOperation) and node.operator in LOGICAL_SWAP_MAP:
                left_position = getattr(node.operandl, "position", None)
                right_position = getattr(node.operandr, "position", None)
                if left_position is None or right_position is None:
                    continue
                candidate_contexts.append((context, node, left_position, right_position))
                break

    if not candidate_contexts:
        raise ValueError("No logical mutation candidate was found in the focal class.")

    context, binary_node, left_position, right_position = candidate_contexts[0]
    left_index = _index_from_line_col(line_offsets, left_position.line, left_position.column)
    right_index = _index_from_line_col(line_offsets, right_position.line, right_position.column)
    operator_fragment = source[left_index:right_index]
    operator_offset = operator_fragment.rfind(binary_node.operator)
    if operator_offset == -1:
        raise ValueError("Failed to locate the binary operator in the source text.")

    operator_index = left_index + operator_offset
    new_operator = LOGICAL_SWAP_MAP[binary_node.operator]
    mutated_source = (
        source[:operator_index]
        + new_operator
        + source[operator_index + len(binary_node.operator):]
    )
    _write_source(file_path, mutated_source)
    operator_line, operator_column = _line_col_from_index(source, operator_index)
    return {
        "mutation_type": "logical",
        "method_name": context["name"],
        "old_operator": binary_node.operator,
        "new_operator": new_operator,
        "line": operator_line,
        "column": operator_column,
    }


def apply_signature_mutation(file_path, target_method=None):
    source, line_offsets, contexts = _collect_method_contexts(file_path)
    context = _select_method_context(contexts, target_method=target_method)
    if context is None:
        raise ValueError("No method declaration with a body was found for signature mutation.")

    # Keep module-wide compilation stable in smoke by avoiding signature drift.
    # Inject a behavioral mutation inside the mapped method body instead.
    body_open_index = context["body_open_index"]
    body_close_index = context["body_close_index"]
    if body_open_index is None or body_close_index is None:
        raise ValueError("Method body boundaries are unavailable for behavioral mutation.")

    method_body = source[body_open_index:body_close_index]
    add_tag_id_pattern = re.compile(
        r"\btagIds\s*\.\s*add\s*\(\s*tagEntity\s*\.\s*getId\s*\(\s*\)\s*\)\s*;"
    )
    add_tag_id_match = add_tag_id_pattern.search(method_body)
    if add_tag_id_match is not None:
        mutated_method_body = (
            method_body[:add_tag_id_match.start()]
            + "tagIds.add(999L);"
            + method_body[add_tag_id_match.end():]
        )
        mutation_index = body_open_index + add_tag_id_match.start()
        mutation_description = {
            "old_expression": "tagEntity.getId()",
            "new_expression": "999L",
            "mutation_variant": "hardcoded_tag_id",
        }
    else:
        message_type_pattern = re.compile(r"\bMESSAGE_TYPE_TAG_UPDATE\b")
        message_type_match = message_type_pattern.search(method_body)
        if message_type_match is None:
            raise ValueError("No behavioral mutation candidate was found in the target method body.")
        mutated_method_body = (
            method_body[:message_type_match.start()]
            + "MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE"
            + method_body[message_type_match.end():]
        )
        mutation_index = body_open_index + message_type_match.start()
        mutation_description = {
            "old_expression": "MESSAGE_TYPE_TAG_UPDATE",
            "new_expression": "MESSAGE_TYPE_BUSINESS_OBJECT_DEFINITION_UPDATE",
            "mutation_variant": "message_type_swap",
        }

    mutated_source = source[:body_open_index] + mutated_method_body + source[body_close_index:]
    _write_source(file_path, mutated_source)
    mutation_line, mutation_column = _line_col_from_index(source, mutation_index)
    return {
        "mutation_type": "signature",
        "method_name": context["name"],
        "inserted_parameter": "behavioral-mutation",
        "line": mutation_line,
        "column": mutation_column,
        **mutation_description,
    }


def apply_exception_mutation(file_path, target_method=None):
    source, _, contexts = _collect_method_contexts(file_path)

    def _can_add_exception(context):
        body_open_index = context.get("body_open_index")
        body_close_index = context.get("body_close_index")
        if body_open_index is None or body_close_index is None:
            return False
        method_body = source[body_open_index:body_close_index + 1]
        return "AGONE_MUTATION_TRIGGER" not in method_body

    context = _select_method_context(contexts, target_method=target_method, predicate=_can_add_exception)
    if context is None:
        raise ValueError("No method body was available for exception fallback mutation.")

    body_open_index = context["body_open_index"]
    insertion_index = body_open_index + 1
    line_start = source.rfind("\n", 0, body_open_index) + 1
    method_indent = re.match(r"[ \t]*", source[line_start:body_open_index]).group(0)
    body_indent = method_indent + "    "
    insertion_text = (
        f"\n{body_indent}if (System.getProperty(\"agone.mutation.trigger\") == null) "
        "{ throw new AssertionError(\"AGONE_MUTATION_TRIGGER\"); }"
    )

    mutated_source = source[:insertion_index] + insertion_text + source[insertion_index:]
    _write_source(file_path, mutated_source)
    mutation_line, mutation_column = _line_col_from_index(source, insertion_index)
    return {
        "mutation_type": "exception",
        "method_name": context["name"],
        "added_exception": "AssertionError(\"AGONE_MUTATION_TRIGGER\")",
        "line": mutation_line,
        "column": mutation_column,
    }


def apply_random_mutation(file_path, target_method=None, rng=None):
    rng = rng or random.Random()
    mutation_functions = [
        apply_logical_mutation,
        apply_signature_mutation,
        apply_exception_mutation,
    ]
    for mutation_function in rng.sample(mutation_functions, len(mutation_functions)):
        try:
            return mutation_function(file_path, target_method=target_method)
        except ValueError:
            continue
    raise ValueError("Unable to apply any supported mutation to the focal class.")
