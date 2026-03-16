import json
import os
import re
from glob import iglob

import javalang


TEST_ANNOTATIONS = {
    "Test",
    "ParameterizedTest",
    "RepeatedTest",
    "TestFactory",
    "TestTemplate",
}


def _get_structure_json(rootdir):
    structure = {}

    for root, dirs, files in os.walk(rootdir):
        if "test" in root.replace(rootdir, "").split(os.sep):
            continue

        java_files = [file for file in files if file.endswith(".java")]
        if not java_files:
            continue

        package = root.replace(rootdir, "").replace(os.sep, ".").strip(".")
        package_structure = structure

        if package:
            for part in package.split("."):
                package_structure = package_structure.setdefault(part, {})

        for file in java_files:
            package_structure[file] = None

    return structure


def _read_java_source(java_path):
    with open(java_path, "r", encoding="utf-8") as java_file:
        return java_file.read()


def _parse_java_source(source_code, source_label="<memory>"):
    try:
        return javalang.parse.parse(source_code)
    except (javalang.parser.JavaSyntaxError, TypeError, IndexError, StopIteration) as exc:
        raise ValueError(f"Unable to parse Java source from {source_label}: {exc}") from exc


def _annotation_name(annotation):
    return annotation.name.split(".")[-1]


def _is_test_method(method_declaration):
    annotations = getattr(method_declaration, "annotations", []) or []
    return any(_annotation_name(annotation) in TEST_ANNOTATIONS for annotation in annotations)


def _type_to_string(type_node):
    if type_node is None:
        return "void"

    if isinstance(type_node, str):
        return type_node

    name = getattr(type_node, "name", str(type_node))
    sub_type = getattr(type_node, "sub_type", None)
    if sub_type is not None:
        name = f"{name}.{_type_to_string(sub_type)}"

    arguments = []
    for argument in getattr(type_node, "arguments", []) or []:
        if hasattr(argument, "type") and argument.type is not None:
            arguments.append(_type_to_string(argument.type))
        elif hasattr(argument, "pattern_type") and argument.pattern_type is not None:
            arguments.append(_type_to_string(argument.pattern_type))
        else:
            arguments.append("?")
    if arguments:
        name = f"{name}<{', '.join(arguments)}>"

    dimensions = "[]" * len(getattr(type_node, "dimensions", []) or [])
    return f"{name}{dimensions}"


def _parameter_to_dict(parameter):
    parameter_type = _type_to_string(parameter.type)
    if getattr(parameter, "varargs", False):
        parameter_type = f"{parameter_type}..."
    return {
        "name": parameter.name,
        "type": parameter_type,
    }


def _modifiers_to_string(modifiers):
    if not modifiers:
        return ""
    return " ".join(sorted(modifiers))


def _build_method_signature(method_declaration):
    parameters = [_parameter_to_dict(parameter) for parameter in method_declaration.parameters]
    parameters_signature = ", ".join(
        f"{parameter['type']} {parameter['name']}" for parameter in parameters
    )
    return_type = _type_to_string(getattr(method_declaration, "return_type", None))
    visibility = _modifiers_to_string(method_declaration.modifiers)
    signature = f"{return_type} {method_declaration.name}({parameters_signature})"
    full_signature = f"{visibility} {signature}".strip()

    return {
        "identifier": method_declaration.name,
        "parameters": f"({parameters_signature})",
        "modifiers": visibility,
        "return": return_type,
        "signature": signature,
        "full_signature": full_signature,
        "class_method_signature": None,
        "testcase": _is_test_method(method_declaration),
        "constructor": False,
        "parameter_count": len(parameters),
        "parameters_list": parameters,
    }


def _build_constructor_signature(constructor_declaration):
    parameters = [_parameter_to_dict(parameter) for parameter in constructor_declaration.parameters]
    parameters_signature = ", ".join(
        f"{parameter['type']} {parameter['name']}" for parameter in parameters
    )
    visibility = _modifiers_to_string(constructor_declaration.modifiers)
    signature = f"{constructor_declaration.name}({parameters_signature})"
    full_signature = f"{visibility} {signature}".strip()

    return {
        "identifier": constructor_declaration.name,
        "parameters": f"({parameters_signature})",
        "modifiers": visibility,
        "return": None,
        "signature": signature,
        "full_signature": full_signature,
        "class_method_signature": None,
        "testcase": False,
        "constructor": True,
        "parameter_count": len(parameters),
        "parameters_list": parameters,
    }


def _find_class_bounds(source_code, class_declaration):
    source_lines = source_code.splitlines()
    if class_declaration.position is None:
        return 1, len(source_lines)

    start_line = class_declaration.position.line
    opening_brace_seen = False
    brace_depth = 0
    for line_index in range(start_line - 1, len(source_lines)):
        line = source_lines[line_index]
        for char in line:
            if char == "{":
                brace_depth += 1
                opening_brace_seen = True
            elif char == "}":
                brace_depth -= 1
                if opening_brace_seen and brace_depth == 0:
                    return start_line, line_index + 1
    return start_line, len(source_lines)


def _extract_member_sources(source_code, members, class_end_line):
    if not members:
        return {}

    source_lines = source_code.splitlines()
    ordered_members = sorted(
        [member for member in members if getattr(member, "position", None) is not None],
        key=lambda member: member.position.line,
    )
    member_sources = {}

    for index, member in enumerate(ordered_members):
        start_line = member.position.line
        if index + 1 < len(ordered_members):
            end_line = ordered_members[index + 1].position.line - 1
        else:
            end_line = class_end_line - 1
        member_sources.setdefault(member.name, []).append(
            {
                "start_line": start_line,
                "end_line": end_line,
                "source": "\n".join(source_lines[start_line - 1:end_line]).strip(),
            }
        )
    return member_sources


def _collect_class_info(source_code, class_declaration):
    class_info = {
        "name": class_declaration.name,
        "methods": [],
        "constructors": [],
        "visibility": sorted(class_declaration.modifiers),
    }

    for method in class_declaration.methods:
        method_info = _build_method_signature(method)
        method_info["class_method_signature"] = (
            f"{class_declaration.name}.{method_info['signature']}"
        )
        class_info["methods"].append(method_info)

    for constructor in class_declaration.constructors:
        constructor_info = _build_constructor_signature(constructor)
        constructor_info["class_method_signature"] = (
            f"{class_declaration.name}.{constructor_info['signature']}"
        )
        class_info["constructors"].append(constructor_info)

    class_start_line, class_end_line = _find_class_bounds(source_code, class_declaration)
    class_info["start_line"] = class_start_line
    class_info["end_line"] = class_end_line
    class_info["method_sources"] = _extract_member_sources(
        source_code, class_declaration.methods, class_end_line
    )
    class_info["constructor_sources"] = _extract_member_sources(
        source_code, class_declaration.constructors, class_end_line
    )
    return class_info


def _get_primary_class(tree):
    for type_declaration in tree.types:
        if isinstance(type_declaration, javalang.tree.ClassDeclaration):
            return type_declaration
    return None


def _build_focal_method_index(class_declaration):
    focal_methods = {}
    for method in class_declaration.methods:
        focal_methods.setdefault(method.name, []).append(
            {
                "signature": _build_method_signature(method)["signature"],
                "parameter_count": len(method.parameters),
            }
        )
    return focal_methods


def _collect_field_receivers(class_declaration, focal_class_name):
    receivers = set()
    for field_declaration in class_declaration.fields:
        field_type = _type_to_string(field_declaration.type).split(".")[-1]
        if field_type == focal_class_name:
            for declarator in field_declaration.declarators:
                receivers.add(declarator.name)
    return receivers


def _find_module_root(java_file_path):
    normalized_path = os.path.normpath(java_file_path)
    test_marker = os.path.normpath(os.path.join("src", "test", "java"))
    main_marker = os.path.normpath(os.path.join("src", "main", "java"))

    for marker in (test_marker, main_marker):
        marker_fragment = f"{marker}{os.sep}"
        marker_index = normalized_path.find(marker_fragment)
        if marker_index != -1:
            return normalized_path[:marker_index].rstrip("\\/")
    return os.path.dirname(normalized_path)


def _find_class_declaration(tree, class_name):
    for type_declaration in tree.types:
        if (
            isinstance(type_declaration, javalang.tree.ClassDeclaration)
            and type_declaration.name == class_name
        ):
            return type_declaration
    return None


def _resolve_superclass_source_file(java_file_path, superclass_name):
    if not superclass_name:
        return None

    superclass_simple_name = superclass_name.split(".")[-1]
    module_root = _find_module_root(java_file_path)
    source_roots = [
        os.path.join(module_root, "src", "test", "java"),
        os.path.join(module_root, "src", "main", "java"),
        module_root,
    ]
    seen_candidates = set()

    for source_root in source_roots:
        if not os.path.isdir(source_root):
            continue
        pattern = os.path.join(source_root, "**", f"{superclass_simple_name}.java")
        for candidate in iglob(pattern, recursive=True):
            normalized_candidate = os.path.normpath(candidate)
            if normalized_candidate in seen_candidates:
                continue
            seen_candidates.add(normalized_candidate)
            return normalized_candidate
    return None


def _collect_inherited_focal_receivers(
    java_file_path,
    class_declaration,
    focal_class_name,
    visited_superclasses=None,
):
    visited_superclasses = visited_superclasses or set()
    superclass_type = getattr(class_declaration, "extends", None)
    superclass_name = getattr(superclass_type, "name", None)
    if not superclass_name:
        return set()

    superclass_simple_name = superclass_name.split(".")[-1]
    if superclass_simple_name in visited_superclasses:
        return set()
    visited_superclasses.add(superclass_simple_name)

    superclass_path = _resolve_superclass_source_file(java_file_path, superclass_name)
    if not superclass_path:
        return set()

    try:
        superclass_source = _read_java_source(superclass_path)
        superclass_tree = _parse_java_source(superclass_source, superclass_path)
    except (OSError, ValueError):
        return set()

    superclass_declaration = _find_class_declaration(superclass_tree, superclass_simple_name)
    if superclass_declaration is None:
        superclass_declaration = _get_primary_class(superclass_tree)
    if superclass_declaration is None:
        return set()

    receivers = _collect_field_receivers(superclass_declaration, focal_class_name)
    receivers.update(
        _collect_inherited_focal_receivers(
            superclass_path,
            superclass_declaration,
            focal_class_name,
            visited_superclasses=visited_superclasses,
        )
    )
    return receivers


def _collect_focal_receivers(java_file_path, class_declaration, focal_class_name):
    receivers = {focal_class_name}
    receivers.update(_collect_field_receivers(class_declaration, focal_class_name))
    receivers.update(
        _collect_inherited_focal_receivers(java_file_path, class_declaration, focal_class_name)
    )
    return receivers


def _collect_local_receivers(method_declaration, focal_class_name):
    receivers = set()
    for _, local_variable in method_declaration.filter(javalang.tree.LocalVariableDeclaration):
        local_type = _type_to_string(local_variable.type).split(".")[-1]
        if local_type == focal_class_name:
            for declarator in local_variable.declarators:
                receivers.add(declarator.name)
    return receivers


def _matches_focal_receiver(invocation, focal_receivers):
    qualifier = getattr(invocation, "qualifier", None)
    if qualifier is None:
        return False
    qualifier_name = qualifier.split(".")[-1]
    return qualifier_name in focal_receivers


def _match_invocation_to_focal_methods(invocation, focal_methods, focal_receivers):
    if invocation.member not in focal_methods:
        return []
    if not _matches_focal_receiver(invocation, focal_receivers):
        return []

    argument_count = len(getattr(invocation, "arguments", []) or [])
    matches = []
    for focal_method in focal_methods[invocation.member]:
        if focal_method["parameter_count"] == argument_count:
            matches.append(invocation.member)
    if matches:
        return matches
    return [invocation.member]


def _format_invocation_signature(invocation):
    qualifier = getattr(invocation, "qualifier", None)
    qualifier_prefix = f"{qualifier}." if qualifier else ""
    arguments = getattr(invocation, "arguments", []) or []
    placeholder_arguments = ", ".join("..." for _ in arguments)
    return f"{qualifier_prefix}{invocation.member}({placeholder_arguments})"


def _get_source_line(source_lines, line_number):
    if line_number is None:
        return None
    if line_number < 1 or line_number > len(source_lines):
        return None
    return source_lines[line_number - 1].strip()


def _get_primary_member_source(member_sources, member_name):
    for source_item in member_sources.get(member_name, []):
        source_code = source_item.get("source", "").strip()
        if source_code:
            return source_code
    return None


def _extract_invocation_matches(method_declaration, focal_methods, focal_receivers, source_lines):
    matched_methods = set()
    invocation_matches = []

    for _, invocation in method_declaration.filter(javalang.tree.MethodInvocation):
        matched_focal_methods = sorted(
            set(_match_invocation_to_focal_methods(invocation, focal_methods, focal_receivers))
        )
        if not matched_focal_methods:
            continue

        line_number = getattr(getattr(invocation, "position", None), "line", None)
        invocation_matches.append(
            {
                "invocation": _format_invocation_signature(invocation),
                "matched_focal_methods": matched_focal_methods,
                "qualifier": getattr(invocation, "qualifier", None),
                "line": line_number,
                "source_line": _get_source_line(source_lines, line_number),
            }
        )
        matched_methods.update(matched_focal_methods)

    return sorted(matched_methods), invocation_matches


def build_ast_prompt_context(test_class_path, focal_class_path, selected_test_methods=None):
    test_source = _read_java_source(test_class_path)
    focal_source = _read_java_source(focal_class_path)

    test_tree = _parse_java_source(test_source, test_class_path)
    focal_tree = _parse_java_source(focal_source, focal_class_path)

    test_class = _get_primary_class(test_tree)
    focal_class = _get_primary_class(focal_tree)
    if test_class is None or focal_class is None:
        return {
            "mapping": {},
            "invocations_by_test": {},
            "mapped_test_method_sources": {},
            "mapped_focal_method_sources": {},
            "mapped_focal_methods": [],
        }

    selected_methods = None
    if selected_test_methods:
        selected_methods = {method_name for method_name in selected_test_methods if method_name}

    focal_methods = _build_focal_method_index(focal_class)
    base_receivers = _collect_focal_receivers(test_class_path, test_class, focal_class.name)
    test_source_lines = test_source.splitlines()

    test_class_info = _collect_class_info(test_source, test_class)
    focal_class_info = _collect_class_info(focal_source, focal_class)
    test_method_sources = test_class_info["method_sources"]
    focal_method_sources = focal_class_info["method_sources"]

    mapping = {}
    invocations_by_test = {}
    mapped_test_method_sources = {}
    mapped_focal_method_sources = {}

    for method_declaration in test_class.methods:
        if not _is_test_method(method_declaration):
            continue
        if selected_methods is not None and method_declaration.name not in selected_methods:
            continue

        focal_receivers = base_receivers | _collect_local_receivers(
            method_declaration, focal_class.name
        )
        matched_methods, invocation_matches = _extract_invocation_matches(
            method_declaration,
            focal_methods,
            focal_receivers,
            test_source_lines,
        )
        mapping[method_declaration.name] = matched_methods
        if not matched_methods:
            continue
        invocations_by_test[method_declaration.name] = invocation_matches

        test_method_source = _get_primary_member_source(test_method_sources, method_declaration.name)
        if test_method_source:
            mapped_test_method_sources[method_declaration.name] = test_method_source

        for focal_method_name in matched_methods:
            focal_sources = []
            for source_item in focal_method_sources.get(focal_method_name, []):
                source_code = source_item.get("source", "").strip()
                if source_code:
                    focal_sources.append(source_code)
            if focal_sources:
                mapped_focal_method_sources[focal_method_name] = focal_sources

    return {
        "mapping": mapping,
        "invocations_by_test": invocations_by_test,
        "mapped_test_method_sources": mapped_test_method_sources,
        "mapped_focal_method_sources": mapped_focal_method_sources,
        "mapped_focal_methods": sorted(mapped_focal_method_sources.keys()),
    }


def get_structure(rootdir):
    structure_file = f"{rootdir}/project_structure.json"
    with open(structure_file, "r") as file:
        project_structure = json.load(file)
        project_structure_str = re.sub(r":\sNone", " ", str(project_structure))
        return project_structure_str


def save_project_structure(rootdir):
    project_structure = _get_structure_json(rootdir)
    file_name = os.path.join(rootdir, "project_structure.json")
    with open(file_name, "w") as file:
        json.dump(project_structure, file, indent=4)


def map_test_to_focal_methods(test_class_path, focal_class_path):
    return build_ast_prompt_context(test_class_path, focal_class_path)["mapping"]
