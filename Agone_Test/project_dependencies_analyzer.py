import json
import os
import re
import xml.etree.ElementTree as ET
import time


def _get_dependencies_project_data(rootdir):
    for root, dirs, files in os.walk(rootdir):
        for file in files:

            if file == "pom.xml":
                pom_path = f"{root}/{file}"
                dependencies = _parse_pom_dependencies(pom_path)

                current_project = {
                    'pompath': pom_path,
                    'dependencies': dependencies
                }

                subdependencies = {}
                for subdir in dirs:
                    subdir_path = f"{root}/{subdir}"
                    subdependency = _get_dependencies_project_data(subdir_path)
                    if subdependency:
                        subdependencies[subdir] = subdependency

                if subdependencies:
                    current_project['subdependencies'] = subdependencies

                return current_project

    return {}


def _parse_pom_dependencies(pom_path):
    def sanitize_pom_file(pom_path):
        with open(pom_path, 'r', encoding='utf-8') as file:
            content = file.read()
        processed_content = re.sub(r"<%= .*? %>", "placeholder", content)
        return processed_content

    def parse_pom(pom_path):
        processed_content = sanitize_pom_file(pom_path)
        processed_content = re.sub(r".*<project>", "<project>", processed_content)
        processed_content = re.sub(r"</project>.*", "</project>", processed_content)
        root = ET.ElementTree(ET.fromstring(processed_content)).getroot()
        return root

    root = parse_pom(pom_path)
    namespaces = {'mvn': 'http://maven.apache.org/POM/4.0.0'}
    dependencies = []

    for dependency in root.findall('.//mvn:dependencies/mvn:dependency', namespaces):
        dep = {}
        groupId = dependency.find('mvn:groupId', namespaces)
        artifactId = dependency.find('mvn:artifactId', namespaces)
        raw_version = dependency.find('mvn:version', namespaces)
        scope = dependency.find('mvn:scope', namespaces)

        if groupId is not None:
            dep['groupId'] = groupId.text
        if artifactId is not None:
            dep['artifactId'] = artifactId.text
        if scope is not None:
            dep['scope'] = scope.text
        if raw_version is not None:
            raw_value = raw_version.text
            if _is_valid_version(raw_value):
                dep['version'] = raw_value
            else:
                version = _get_version_from_placeholders(raw_value, root, namespaces)
                if version:
                    dep['version'] = version

        dependencies.append(dep)

    return dependencies


def _get_version_from_placeholders(raw_value, root, namespaces):
    placeholders = re.findall(r'\${[^\s}]+}|\d+', raw_value)
    len_placeholders_list = len(placeholders)

    if placeholders:
        if len_placeholders_list == 3:
            return _get_version_string(root, namespaces, placeholders)

        elif len_placeholders_list == 1:
            raw_value = _get_property_value(root, namespaces, placeholders[0])
            if _is_valid_version(raw_value):
                return raw_value
            else:
                if raw_value:
                    placeholders_temp = re.findall(r'\${[^\s}]+}|\d+', raw_value)
                    len_placeholders_temp_list = len(placeholders_temp)
                    if len_placeholders_temp_list == 3:
                        return _get_version_string(root, namespaces, placeholders_temp)
        else:
            return None
    else:
        return None


def _get_version_string(root, namespaces, placeholders):
    version_digits = []
    for placeholder in placeholders:
        if placeholder.startswith('${') and placeholder.endswith('}'):
            single_version_digit = _get_property_value(root, namespaces, placeholder)
            if single_version_digit and single_version_digit.isdigit():
                version_digits.append(single_version_digit)
        elif placeholder.isdigit():
            version_digits.append(placeholder)
    version_string = '.'.join(version_digits)
    if _is_valid_version(version_string):
        return version_string
    else:
        return None


def _get_property_value(root, namespaces, property_name):
    cleaned_property_name = property_name.strip("${}")
    property_element = root.find(f".//mvn:properties/mvn:{cleaned_property_name}", namespaces)
    if property_element is not None:
        return property_element.text
    return None


def _is_valid_version(version):
    if version:
        parts = version.split('.')
        return all(part.isdigit() for part in parts)
    else:
        return False


def _merge_dependencies_by_group_id(dependencies_dict):
    if not isinstance(dependencies_dict, dict):
        return {'dependencies': {}, 'subdependencies': {}}

    grouped_dependencies = {}

    def add_dependency(group_id, dependency):
        artifact_id = dependency.get('artifactId')
        if not group_id or not artifact_id:
            return
        key = (group_id, artifact_id)
        if key not in grouped_dependencies:
            grouped_dependencies[key] = dependency
        else:
            existing_dep = grouped_dependencies[key]
            if len(dependency) > len(existing_dep):
                grouped_dependencies[key] = dependency

    for dep in dependencies_dict.get('dependencies', []) or []:
        group_id = dep.get('groupId')
        add_dependency(group_id, dep)

    def process_subdependencies(subdependencies):
        subdependencies_grouped = {}
        for subdep_key, subdep_data in subdependencies.items():
            sub_grouped = {}
            for dep in subdep_data.get('dependencies', []) or []:
                group_id = dep.get('groupId')
                artifact_id = dep.get('artifactId')
                if not group_id or not artifact_id:
                    continue

                if group_id not in sub_grouped:
                    sub_grouped[group_id] = []
                dep_cleaned = {key: value for key, value in dep.items() if key != 'groupId'}
                sub_grouped[group_id].append(dep_cleaned)

            subdependencies_grouped[subdep_key] = {
                'pompath': subdep_data.get('pompath'),
                'dependencies': sub_grouped
            }
        return subdependencies_grouped

    dependencies_dict['subdependencies'] = process_subdependencies(dependencies_dict.get('subdependencies', {}) or {})

    final_dependencies = {}
    for (group_id, artifact_id), dep in grouped_dependencies.items():
        if group_id not in final_dependencies:
            final_dependencies[group_id] = []
        dep_cleaned = {key: value for key, value in dep.items() if key != 'groupId'}
        final_dependencies[group_id].append(dep_cleaned)

    dependencies_dict['dependencies'] = final_dependencies

    return dependencies_dict


def get_structure(rootdir):
    structure_file = f'{rootdir}/project_dependencies.json'
    with open(structure_file, 'r') as file:
        return json.load(file)


def save_project_dependencies(rootdir):
    project_dependencies = _get_dependencies_project_data(rootdir)
    dependencies_to_save = _merge_dependencies_by_group_id(project_dependencies)
    file_path = f"{rootdir}/project_dependencies.json"
    with open(file_path, 'w') as file:
        json.dump(dependencies_to_save, file, indent=4)

#save_project_dependencies('./compiledrepos/117021824')
