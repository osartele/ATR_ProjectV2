import os
import pandas as pd
import subprocess
import shutil
import re
import xml.etree.ElementTree as ET
import ctypes
import copy
import json
import yaml
import time

import javalang

import mavenLib
from dotenv import load_dotenv
from execution_manager import ExecutionManager
import project_structure_analyzer as psa
from path_context import get_path_context

load_dotenv()

TEST_ANNOTATIONS = {
    "Test",
    "ParameterizedTest",
    "RepeatedTest",
    "TestFactory",
    "TestTemplate",
}

ITERATIVE_STYLE_ALLOWED_UNQUALIFIED_CALLS = {
    "assertAll",
    "assertArrayEquals",
    "assertDoesNotThrow",
    "assertEquals",
    "assertFalse",
    "assertNotEquals",
    "assertNotNull",
    "assertNotSame",
    "assertNull",
    "assertSame",
    "assertThat",
    "assertThrows",
    "assertTrue",
    "doAnswer",
    "doNothing",
    "doReturn",
    "doThrow",
    "eq",
    "fail",
    "inOrder",
    "isNull",
    "lenient",
    "never",
    "notNull",
    "reset",
    "same",
    "spy",
    "times",
    "verify",
    "verifyNoInteractions",
    "verifyNoMoreInteractions",
    "when",
}

IMMUTABLE_SOURCE_ROOTS = {"repos", "Classes2Test"}
PROJECT_WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SMOKE_TARGET_CACHE = {}
PATH_CONTEXT = get_path_context()


def _worker_output_path(*parts):
    return os.path.join(PATH_CONTEXT.get_output_path(), *[str(part) for part in parts])


def _worker_project_output_path(project, *parts):
    return os.path.join(PATH_CONTEXT.get_project_output_path(project), *[str(part) for part in parts])


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
            f"Refusing to modify immutable source path: {file_path}. "
            "Only compiled/output workspace copies may be changed."
        )

def set_gradle_variable(gradle_directory, gradle_version, system):
    """
    Sets the gradle variable accordingly to the given gradle version.
        Parameters:
                gradle_directory (String): the directory containing the gradle bin files.
                gradle_version (String): the version of Gradle
                system (String): the current OS (Windows, Linux, etc..)   
    """
    if gradle_version.startswith('4'):
        gradle_variable = f"{gradle_directory}/gradle-4.10.2"
    elif gradle_version.startswith('5'):
        gradle_variable = f"{gradle_directory}/gradle-5.6.4"
    elif gradle_version.startswith('6'):
        gradle_variable = f"{gradle_directory}/gradle-6.8.3"
    elif gradle_version.startswith('7'):
        gradle_variable = f"{gradle_directory}/gradle-7.6"
    elif gradle_version.startswith('8'):
        gradle_variable = f"{gradle_directory}/gradle-8.6"
    else:
        gradle_variable = f"{gradle_directory}/gradle-8.6"
    if system=="Windows":
        os.environ['PATH'] = f"{gradle_variable}/bin;{os.environ['PATH']}"
    else: # for Linux and Darwin
        os.environ['PATH'] = f"{gradle_variable}/bin:{os.environ['PATH']}"


def set_java_home(java_directory, java_version, system):
    """
    Sets the java home variable accordingly to the given java version.
        Parameters:
                java_directory (String): the directory containing the jdk files.
                java_version (String): the version of Java
                system (String): the current OS (Windows, Linux, etc..)   
    """
    if java_version == '1.5' or java_version == '5':
        java_home = os.getenv('JAVA_HOME_5')
    elif java_version == '1.6' or java_version == '6':
        java_home = os.getenv('JAVA_HOME_6')
    elif java_version == '1.7' or java_version == '7':
        java_home = os.getenv('JAVA_HOME_7')
    elif java_version == '1.8' or java_version == '8':
        java_home = os.getenv('JAVA_HOME_8')
    elif java_version == '11':
        java_home = os.getenv('JAVA_HOME_11')
    elif java_version == '17':
        java_home = os.getenv('JAVA_HOME_17')
    elif java_version == '21':
        java_home = os.getenv('JAVA_HOME_21')
    else:
        java_home = os.getenv('JAVA_HOME_DEFAULT')

    if java_home is None:
        java_home = os.getenv('JAVA_HOME') or os.getenv('JAVA_HOME_DEFAULT')
    if java_home is None:
        java_binary = shutil.which("java")
        if java_binary is not None:
            java_home = os.path.dirname(os.path.dirname(java_binary))
    if java_home is None:
        print(f"Unable to resolve JAVA_HOME for Java {java_version}. Continuing with the existing PATH.")
        return None

    print(f"Using JAVA_HOME {java_home}")
    os.environ['JAVA_HOME'] = java_home
    if system=="Windows":
        os.environ['PATH'] = f"{java_home}/bin;{os.environ['PATH']}"
    else: # for Linux and Darwin
        os.environ['PATH'] = f"{java_home}/bin:{os.environ['PATH']}"
    return java_home


    
def read_class_content(file_path): 
    """
    Reads the content of a test class or focal class.
        Parameters:
                    file_path (string): the path of the test class/focal class
        Returns:
                    :the content of the given test class/focal class. 'None' if the given path is not a class, if the given path does not exist or if an error occurred while reading the file 
    """
    # if the given file is not a class
    if file_path.endswith(".java") == False:
        return None
    # If the file exists
    try:
        if os.path.exists(file_path):
            # Open the file
            with open(file_path, 'r') as file:
                # Return the content of the file
                return file.read()
        else:
            # If the file does not exist, return None
            return None
    except Exception as e:
        print(e)
        # If an exception occurs, return None
        return None
    


def verify_if_folder_has_already_been_processed(folder):
    """
    Verifies if the given folder has already been processed. 
        Parameters:
                    folder: the ID of the project (that is the name of the corresponding folder)
        Returns:
                    :'True' if the folder has already been processed, False otherwise
    """
    compiled_path = PATH_CONTEXT.get_compiled_repo_path(folder)
    failed_path = f'failedrepos/{folder}'
    if os.path.exists(compiled_path):
        return True
    elif os.path.exists(failed_path):
        return True
    else:
        return False
    


def count_files_of_a_dir(dir_path):
    """
    Returns the number of files given a directory path.
        Parameters:
                    dir_path: the directory path
        Returns:
                    count: the number of files
    """
    count = 0
    # Iterate directory
    for path in os.listdir(dir_path):
        # check if current path is a file
        if os.path.isdir(os.path.join(dir_path, path)):
            count += 1
    return count





def remove_missing_files_from_dataframe(project_df):
    """
    Removes from the given dataframe all the rows that contain a missing file (in other words, a file that is not present in the corresponding repository directory)        
        Parameters:
                    project_df (Dataframe): the dataframe containing the names and paths of the focal classes and the associated test classes
        Returns:
                    proejct_df (Dataframe): the new dataframe without the rows that contain a missing file
    """
    index_to_remove = set() # contains all the indexes that are to be removed because the corresponding focal path or test path is not present in the repository
    # If a test_path or a focal_path of the project_df (that is output/classes.csv filtered with the current project) doesn't exist in the repository, it will be removed from the dataframe
    for index, row in project_df.iterrows():
        if "repos/" in row['Test_Path']:
            test_path = PATH_CONTEXT.to_worker_compiled_path(row.get('Project'), row['Test_Path'])
            focal_path = PATH_CONTEXT.to_worker_compiled_path(row.get('Project'), row['Focal_Path'])
        else:
            project = row['Project']
            test_path = PATH_CONTEXT.to_worker_compiled_path(project, row['Test_Path'])
            focal_path = PATH_CONTEXT.to_worker_compiled_path(project, row['Focal_Path'])
        if not (os.path.isfile(test_path) and os.path.isfile(focal_path)):
            index_to_remove.add(index)
    project_df = project_df.drop(index=index_to_remove)
    project_df = project_df.reset_index()
    return project_df



def configure_test_smell_detector(project_dataframe, project):
    """
    Configures tsDetect to analyze the focal classes and the test classes specified in the given dataframe. 
    It must be executed prior to running tsDetect.
        Parameters:
                    project_dataframe (Dataframe): the dataframe that contains all the focal classes and test classes that are to be executed by tsDetect
                    project: the ID of the project
        Returns:
                    csv_path: the path of the CSV file that needs to be passed as input to the test smell detector
    """
    def normalize_compiled_path(raw_path):
        if raw_path is None or pd.isna(raw_path):
            return None
        normalized_path = PATH_CONTEXT.to_worker_compiled_path(project, raw_path)
        if normalized_path is None:
            return None
        return os.path.abspath(normalized_path)

    data = []
    project_df = project_dataframe.copy()
    for index, row in project_df.iterrows():
        test_path_absolute = normalize_compiled_path(row.get("Test_Path"))
        focal_path_absolute = normalize_compiled_path(row.get("Focal_Path"))
        data.append([project, test_path_absolute, focal_path_absolute])
    df = pd.DataFrame(data)
    csv_path = _worker_project_output_path(project, "pathToInputFile.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    df.to_csv(csv_path, index=False, header=False, na_rep="-")
    return csv_path




           

def run_test_smell_detector(csv_path, project, test_type, technique, module=None):
    """
    Runs tsDetect and saves the result in the 'TestSmellDetection_{project}_{test_type}_{technique}.csv' file. 
    It must be executed after configure_test_smell_detector().
    This function uses the TestSmellDetector.jar file; therefore, the tsDetect JAR file must be present in the same directory as utils.py
        Parameters:
                    csv_path: the path of the CSV file returned by configure_test_smell_detector()
                    project: the ID of the project
                    test_type: the type of the test
                    technique: the prompt technique adopted if the test type is an AI model, 'None' if it is not an AI model
                    module: the name of the module of the project
        Returns:
                    result_path: the path of the CSV file containing the results provided by tsDetect. 'None' if an error occurred while trying to run tsDetect
    """
    detector_path = os.path.join(os.path.dirname(__file__), "TestSmellDetector.jar")
    if not os.path.exists(detector_path):
        return None
    project_output_dir = PATH_CONTEXT.get_project_output_path(project)
    os.makedirs(project_output_dir, exist_ok=True)
    detector_workdir = project_output_dir

    # Remove stale detector outputs from prior runs to avoid cross-sample/cross-worker contamination.
    for entry in os.listdir(detector_workdir):
        if entry.startswith("Output_TestSmellDetection"):
            stale_path = os.path.join(detector_workdir, entry)
            try:
                if os.path.isfile(stale_path):
                    os.remove(stale_path)
            except OSError:
                pass

    command = ["java", "-jar", detector_path, os.path.abspath(csv_path)]
    try:
        test_smell_timeout_seconds = get_subprocess_timeout_seconds("test_smell_timeout_seconds", 300)
        subprocess.check_output(
            command,
            stderr=subprocess.STDOUT,
            timeout=test_smell_timeout_seconds,
            cwd=detector_workdir,
        )
    except subprocess.TimeoutExpired:
        print("Test smell detector timed out.")
        return None
    except Exception as e:
        error_output = getattr(e, "output", b"")
        if isinstance(error_output, bytes):
            print(error_output.decode("utf-8", errors="replace"))
        else:
            print(str(error_output))
        return None
    files = os.listdir(detector_workdir)
    result_path = None
    for file in files:
        if file.startswith('Output_TestSmellDetection'): 
            if module is None:  
                if technique is not None:
                    new_name = f'TestSmellDetection_{project}_{test_type}_{technique}.csv'
                else:
                    new_name = f'TestSmellDetection_{project}_{test_type}.csv'
            else:
                if technique is not None:
                    new_name = f'TestSmellDetection_{project}_{module}_{test_type}_{technique}.csv'
                else:
                    new_name = f'TestSmellDetection_{project}_{module}_{test_type}.csv'
            source_path = os.path.join(detector_workdir, file)
            result_path = os.path.join(detector_workdir, new_name)
            if os.path.exists(result_path):
                os.remove(result_path)
            os.rename(source_path, result_path)
            break
    return result_path




def _normalize_optional_identifier(value):
    if value is None or pd.isna(value):
        return None
    normalized_value = str(value).strip()
    if not normalized_value or normalized_value == "-" or normalized_value.lower() == "nan":
        return None
    return normalized_value


def _normalize_method_identifier(method_name):
    normalized_method = _normalize_optional_identifier(method_name)
    if normalized_method is None:
        return None
    return normalized_method.split("(", 1)[0].strip()


def _java_path_to_fqn(java_path):
    normalized_path = _normalize_optional_identifier(java_path)
    if normalized_path is None:
        return None
    normalized_path = normalized_path.replace("\\", "/")
    class_path = None
    for marker in ("src/main/java/", "src/test/java/", "java/"):
        if marker in normalized_path:
            class_path = normalized_path.split(marker, 1)[1]
            break
    if class_path is None or not class_path.endswith(".java"):
        return None
    return class_path[:-5].replace("/", ".")


def _sanitize_snapshot_segment(value):
    normalized_value = _normalize_optional_identifier(value)
    if normalized_value is None:
        return "none"
    sanitized_value = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalized_value)
    sanitized_value = sanitized_value.strip("._")
    return sanitized_value or "none"


def build_coverage_snapshot_key(test_type, technique=None):
    snapshot_key = _sanitize_snapshot_segment(test_type)
    normalized_technique = _normalize_optional_identifier(technique)
    if normalized_technique is not None:
        snapshot_key = f"{snapshot_key}__{_sanitize_snapshot_segment(normalized_technique)}"
    return snapshot_key


def _find_nested_report_file(root_directory, target_file_name):
    if not os.path.isdir(root_directory):
        return None
    for current_root, _, files in os.walk(root_directory):
        if target_file_name in files:
            return os.path.join(current_root, target_file_name)
    return None


def _resolve_live_coverage_report_paths(module_path, type_project):
    if type_project == 'Maven':
        jacoco_candidates = [
            os.path.join(module_path, "target/site/jacoco/jacoco.csv"),
            os.path.join(module_path, "target/site/jacoco-ut/jacoco.csv"),
        ]
        pitest_candidates = [os.path.join(module_path, "target/pit-reports/mutations.csv")]
    else:
        jacoco_candidates = [
            os.path.join(module_path, "build/reports/jacoco/jacoco.csv"),
            os.path.join(module_path, "build/reports/jacoco-ut/jacoco.csv"),
        ]
        pitest_candidates = [os.path.join(module_path, "build/reports/pitest/mutations.csv")]

    jacoco_path = next((candidate for candidate in jacoco_candidates if os.path.exists(candidate)), None)
    pitest_path = next((candidate for candidate in pitest_candidates if os.path.exists(candidate)), None)

    if pitest_path is None:
        nested_root = os.path.dirname(pitest_candidates[0]) if pitest_candidates else module_path
        pitest_path = _find_nested_report_file(nested_root, "mutations.csv")

    return jacoco_path, pitest_path


def snapshot_coverage_reports(
    project_path,
    project_dataframe,
    project_id,
    type_project,
    test_type,
    technique=None,
    module=None,
):
    project_df = project_dataframe.copy()
    if module is None:
        modules = search_modules(project_path, project_df, project_id, type_project)
        if not modules:
            return None
    else:
        modules = [module]

    snapshot_key = build_coverage_snapshot_key(test_type, technique)
    snapshot_root = _worker_project_output_path(project_id, "coverage_snapshots", snapshot_key)
    os.makedirs(snapshot_root, exist_ok=True)
    copied_module_count = 0

    for module_name in modules:
        module_relative_path = str(module_name).replace("\\", "/")
        module_live_path = os.path.join(project_path, module_name)
        jacoco_path, pitest_path = _resolve_live_coverage_report_paths(module_live_path, type_project)
        if jacoco_path is None or pitest_path is None:
            continue

        module_snapshot_path = os.path.join(snapshot_root, module_relative_path)
        os.makedirs(module_snapshot_path, exist_ok=True)
        shutil.copy2(jacoco_path, os.path.join(module_snapshot_path, "jacoco.csv"))
        shutil.copy2(pitest_path, os.path.join(module_snapshot_path, "mutations.csv"))
        copied_module_count += 1

    if copied_module_count == 0:
        return None
    return snapshot_key


def retrieve_code_coverage_and_cyclomatic_complexity(
    project_path,
    project_dataframe,
    project_id,
    type_project,
    module=None,
    test_type=None,
    technique=None,
):
    """
    Retrieves the code coverage (Branch, Method, Line and Mutation) of the single classes from the given Maven/Gradle project.
    Retriveves also the cyclomatic_complexity of the single classes from the given Maven/Gradle projects.
    Parameters:
                    project_path: the path of the project
                    project_dataframe (Dataframe): the dataframe that contains the focal classes and the test classes of the project involved in the code and mutation coverage
                    project_id: the ID of the project
                    type_project: the type of the project (if Maven or Gradle project)
                    module (optional): the module to analyze
    Returns:
                    measures_df (DataFrame): The DataFrame containing the measures of code coverage and cyclomatic complexity for individual classes from the given Maven/Gradle project.

    """
    
    project_df = project_dataframe.copy()
    project_df = project_df.reset_index(drop=True)
    project_df["Coverage_Row_Id"] = project_df.index
    project_df["Focal_FQN"] = project_df["Focal_Path"].apply(_java_path_to_fqn)
    if "Focal_Method" in project_df.columns:
        project_df["Focal_Method_Normalized"] = project_df["Focal_Method"].apply(_normalize_method_identifier)
    else:
        project_df["Focal_Method_Normalized"] = None
    target_fqns = {
        focal_fqn
        for focal_fqn in project_df["Focal_FQN"].tolist()
        if _normalize_optional_identifier(focal_fqn) is not None
    }
    if module is None:
        # find all modules and save their paths in a list for JaCoCo and PITest
        modules = search_modules(project_path, project_df, project_id, type_project)
        if not modules:
            return None
    else:
        modules = [module]
        
    jacoco_df_all = None
    pitest_df_all = None
    pitest_method_df_all = None
    # For each module, retrieve the .csv files and read them to obtain the results from JaCoCo and PITest. All the results are then merged into a single DataFrame.
    snapshot_key = None
    if _normalize_optional_identifier(test_type) is not None:
        snapshot_key = build_coverage_snapshot_key(test_type, technique)

    for module in modules:
        jacoco_df = None
        pitest_df = None
        path = os.path.join(project_path, module)
        if snapshot_key is not None:
            module_relative_path = str(module).replace("\\", "/")
            snapshot_module_path = _worker_project_output_path(
                project_id,
                "coverage_snapshots",
                snapshot_key,
                module_relative_path,
            )
            jacoco_snapshot_path = os.path.join(snapshot_module_path, "jacoco.csv")
            pitest_snapshot_path = os.path.join(snapshot_module_path, "mutations.csv")
            if os.path.exists(jacoco_snapshot_path):
                jacoco_df = pd.read_csv(jacoco_snapshot_path)
            if os.path.exists(pitest_snapshot_path):
                pitest_df = pd.read_csv(pitest_snapshot_path, header=None)
        else:
            jacoco_live_path, pitest_live_path = _resolve_live_coverage_report_paths(path, type_project)
            if jacoco_live_path is not None:
                jacoco_df = pd.read_csv(jacoco_live_path)
            if pitest_live_path is not None:
                pitest_df = pd.read_csv(pitest_live_path, header=None)
        
        if jacoco_df is None or pitest_df is None:
            return None

        jacoco_df["Focal_FQN"] = jacoco_df.apply(
            lambda jacoco_row: (
                f"{str(jacoco_row['PACKAGE']).strip()}.{str(jacoco_row['CLASS']).strip()}".strip(".")
                if "PACKAGE" in jacoco_row.index and pd.notna(jacoco_row["PACKAGE"])
                else str(jacoco_row["CLASS"]).strip()
            ),
            axis=1,
        )
        if target_fqns:
            jacoco_df = jacoco_df[jacoco_df["Focal_FQN"].isin(target_fqns)]

        pitest_df[0] = pitest_df[0].astype(str).str.replace('.java', '', regex=False)
        pitest_df.columns = ['Focal_Class', 'Package', 'Mutation_Name', 'Method_Name', 'Line_Number', 'Result', 'Killing_test']
        def _resolve_pitest_fqn(pitest_row):
            focal_class_name = _normalize_optional_identifier(pitest_row['Focal_Class']) or ''
            mutated_class_col = _normalize_optional_identifier(pitest_row['Package']) or ''
            # Safely handle both modern PIT (FQN) and older PIT (package-only) formats.
            if mutated_class_col.endswith(focal_class_name):
                return mutated_class_col
            return f"{mutated_class_col}.{focal_class_name}".strip(".")
        pitest_df["Focal_FQN"] = pitest_df.apply(
            _resolve_pitest_fqn,
            axis=1,
        )
        pitest_df["Method_Name_Normalized"] = pitest_df["Method_Name"].apply(_normalize_method_identifier)
        if target_fqns:
            pitest_df = pitest_df[pitest_df["Focal_FQN"].isin(target_fqns)]
        # Each row of the pitest_df DataFrame represents a mutation
        # Focal_Class: the name of the focal class without the .java extension
        # Package: the package of the focal class
        # Mutation_Name: the name of the engine used for the mutation
        # Method_Signature: the name of the method involved in the mutation
        # Line_Number: the number of the line of code involved in the mutation
        # Killing_Test: the test that ultimately killed the mutation
        pitest_class_df = pitest_df.groupby('Focal_FQN').agg(
            {'Result': lambda x: round((x == 'KILLED').sum() / len(x) * 100, 2)})
        pitest_class_df = pitest_class_df.rename(columns={'Result': 'Mutation_Coverage_Class'})
        pitest_class_df = pitest_class_df.reset_index()
        if pitest_df_all is None:
            pitest_df_all = pitest_class_df
        else:
            pitest_df_all = pd.concat([pitest_df_all, pitest_class_df], ignore_index=True)

        pitest_method_df = pitest_df.groupby(['Focal_FQN', 'Method_Name_Normalized']).agg(
            {'Result': lambda x: round((x == 'KILLED').sum() / len(x) * 100, 2)}
        )
        pitest_method_df = pitest_method_df.rename(columns={'Result': 'Mutation_Coverage_Method'})
        pitest_method_df = pitest_method_df.reset_index()
        if pitest_method_df_all is None:
            pitest_method_df_all = pitest_method_df
        else:
            pitest_method_df_all = pd.concat([pitest_method_df_all, pitest_method_df], ignore_index=True)

        if jacoco_df_all is None:
            jacoco_df_all = jacoco_df
        else:
            jacoco_df_all = pd.concat([jacoco_df_all, jacoco_df], ignore_index=True)

        
    project_df = pd.merge(project_df, jacoco_df_all, how="left", on=['Focal_FQN'])
            

    # add branch, method and line coverage as a percentages
    # add the cyclomatic complexity
    measures_data = []
    for index, row in project_df.iterrows():
        focal_class = row['Focal_Class']
        try:
            if row['BRANCH_COVERED'] + row ['BRANCH_MISSED'] != 0:
                branch_coverage = round((row['BRANCH_COVERED']/(row['BRANCH_COVERED'] + row ['BRANCH_MISSED'])*100),2)
            else:
                branch_coverage = '-'
        except Exception as e:
            branch_coverage = '-'
            
        try:
            if row['METHOD_COVERED'] + row ['METHOD_MISSED'] != 0:
                method_coverage = round((row['METHOD_COVERED']/(row['METHOD_COVERED'] + row ['METHOD_MISSED'])*100),2)
            else:
                method_coverage = '-'
        except Exception as e:
            method_coverage = '-'
            
        try:
            if row['LINE_COVERED'] + row ['LINE_MISSED'] != 0:
                line_coverage = round((row['LINE_COVERED']/(row['LINE_COVERED'] + row ['LINE_MISSED'])*100),2)
            else:
                line_coverage = '-'
        except Exception as e:
            line_coverage = '-'
            
        try:
            cyclomatic_complexity = row['COMPLEXITY_MISSED'] + row['COMPLEXITY_COVERED']
        except Exception as e:
            cyclomatic_complexity = '-'
        try:
            loc = row['LINE_MISSED'] + row['LINE_COVERED']
        except Exception as e:
            loc = '-'


        measures_data.append([row['Coverage_Row_Id'], focal_class, cyclomatic_complexity, loc, branch_coverage, method_coverage, line_coverage])
    
    measures_df = pd.DataFrame(measures_data, columns=['Coverage_Row_Id', 'Focal_Class', 'Cyclomatic_complexity', 'Lines_of_code', 'Branch_coverage', 'Method_coverage', 'Line_coverage'])
    measures_df = pd.merge(project_df, measures_df, how="left",
                            on=['Coverage_Row_Id', 'Focal_Class'])
    measures_df = pd.merge(measures_df, pitest_df_all, how="left",
                                on=['Focal_FQN'])
    measures_df = pd.merge(
        measures_df,
        pitest_method_df_all,
        how="left",
        left_on=['Focal_FQN', 'Focal_Method_Normalized'],
        right_on=['Focal_FQN', 'Method_Name_Normalized'],
    )
    if 'Mutation_Coverage_Method' in measures_df.columns:
        measures_df['Mutation_Coverage'] = measures_df['Mutation_Coverage_Method'].combine_first(
            measures_df.get('Mutation_Coverage_Class')
        )
    else:
        measures_df['Mutation_Coverage'] = measures_df.get('Mutation_Coverage_Class')
    measures_df.drop(
        columns=[
            'GROUP',
            'PACKAGE',
            'INSTRUCTION_MISSED',
            'INSTRUCTION_COVERED',
            'BRANCH_MISSED',
            'BRANCH_COVERED',
            'LINE_MISSED',
            'LINE_COVERED',
            'COMPLEXITY_MISSED',
            'COMPLEXITY_COVERED',
            'METHOD_MISSED',
            'METHOD_COVERED',
            'Coverage_Row_Id',
            'Focal_FQN',
            'Focal_Method_Normalized',
            'Method_Name_Normalized',
            'Mutation_Coverage_Class',
            'Mutation_Coverage_Method',
        ],
        inplace=True,
        errors='ignore',
    )


    return measures_df





def generate_output_csv_test_type(project_id, test_type, technique, measures_df, csv_path_test_smell, module=None):
    """
    Generates and saves the output CSV file that contains all the measures about the single test type applied in the project (code coverage, mutation coverage and number of test smells)
        Parameters:
                    project_id: the ID of the project
                    test_type: the type of the test (e.g 'human', 'evosuite',...)
                    technique: the prompt technique adopted if the test type is an AI model, 'None' if it is not an AI model
                    measures_df (Dataframe): the dataframe containing measures about code coverage and cyclomatic complexity
                    csv_path_test_smell: the path of the CSV file returned by tsDetect
                    module: the name of the project module (optional)
        Returns:
                    csv_path: the path of the output CSV file that includes all the measures about the single test type applied in the project (code coverage, mutation coverage and number of test smells)
                    : 'None' if an error occurred
    """

    if csv_path_test_smell is not None:
        if os.path.exists(csv_path_test_smell):
            test_smell_df = pd.read_csv(csv_path_test_smell)
            # remove .java from the TestClass column
            test_smell_df['TestClass']=test_smell_df['TestClass'].str.replace('.java','')
            test_smell_df['TestClass'] = test_smell_df['TestClass'].str.split('/').str[-1]
            test_smell_df = test_smell_df.drop(columns=['App', 'TestFilePath', 'ProductionFilePath', 'RelativeTestFilePath', 'RelativeProductionFilePath'])
            # rename the column TestClass to Test_Class
            test_smell_df = test_smell_df.rename(columns={'TestClass':'Test_Class'})
            measures_df = pd.merge(measures_df, test_smell_df, how="left", on=['Test_Class'])

    # replace /repos with /compiledrepos in Focal_path e Test_Path
    for index, row in measures_df.iterrows():
        measures_df.at[index, 'Focal_Path'] = PATH_CONTEXT.to_worker_compiled_path(project_id, row['Focal_Path'])
        measures_df.at[index, 'Test_Path'] = PATH_CONTEXT.to_worker_compiled_path(project_id, row['Test_Path'])

    csv_path = None
    if module is None:
        if technique is not None:
            csv_path = _worker_project_output_path(project_id, f"TestClasses_{project_id}_{test_type}_{technique}.csv")
        else:
            csv_path = _worker_project_output_path(project_id, f"TestClasses_{project_id}_{test_type}.csv")
    else:
        if technique is not None:
            csv_path = _worker_project_output_path(project_id, f"TestClasses_{project_id}_{module}_{test_type}_{technique}.csv")
        else:
            csv_path = _worker_project_output_path(project_id, f"TestClasses_{project_id}_{module}_{test_type}.csv")
    try:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        measures_df.to_csv(csv_path, index=False, na_rep="-")
    except Exception as e:
        return None
    return csv_path

    


def search_modules(project_path, project_dataframe, project_id, type_project):
    """
    Searches all the modules where are stored pitest and jacoco measures. 
    It works with either Maven and Gradle projects.
        Parameters:
                    project_path: the path of the proejct
                    project_dataframe (Dataframe): the dataframe containing the focal classes and the test classes for which to search the modules
                    project_id: the ID of the project
                    type_project: the type of the project (if Maven or Gradle project)
        Returns:
                    modules (List): the list of modules found
                    
    """
    project_df = project_dataframe.copy()
    modules = set()
    if type_project == 'Maven':
        for index, row in project_df.iterrows():
            location = row['Test_Path'].replace(f'repos/{project_id}/', '').replace(f"{row['Test_Class']}.java", '')
            while not os.path.isfile(f"{project_path}/{location}/target/site/jacoco/jacoco.csv") and not os.path.isfile(f"{project_path}/{location}/target/site/jacoco-ut/jacoco.csv"):
                location = os.path.dirname(location)
                if location == '':
                    break
            if location != '':
                modules.add(location)
    elif type_project == 'Gradle':
        for index, row in project_df.iterrows():
            location = row['Test_Path'].replace(f'repos/{project_id}/', '').replace(f"{row['Test_Class']}.java", '')
            while not os.path.isfile(f"{project_path}/{location}/build/reports/jacoco/jacoco.csv") and not os.path.isfile(f"{project_path}/{location}/build/reports/jacoco-ut/jacoco.csv"):
                location = os.path.dirname(location)
                if location == '':
                    break
            if location != '':
                modules.add(location)
    return list(modules)





def verify_mockito(type_project, path):
    """
    Verifies if the given project implements the Mockito framework or not.
        Parameters:
                    type_project: the type of the project (if Maven or Gradle project)
                    path: the path of the project or of the module
        Returns:
                   :'True' if the given project implements the Mockito framework, 'False' if the given project does not implement the Mockito framework or if an error occurred
                   
    """
    if type_project == 'Maven':
        ns = {'mvn': 'http://maven.apache.org/POM/4.0.0'}
        try:
            tree = ET.parse(os.path.join(path, 'pom.xml'))
            root = tree.getroot()
            dependencies_root=root.findall('mvn:dependencies/mvn:dependency', ns)
            dependencies_management=root.findall('mvn:dependencyManagement/mvn:dependencies/mvn:dependency', ns)
            all_dependencies=dependencies_root+dependencies_management
            for dependency in all_dependencies:
                group_id = dependency.find('mvn:groupId', ns)
                artifact_id = dependency.find('mvn:artifactId', ns)
                if group_id is not None and group_id.text.__contains__('org.mockito'):
                    return True
                elif artifact_id is not None and artifact_id.text.__contains__('mockito'):
                    return True
            return False
        except Exception as e:
            print(e)
            return False

    elif type_project == 'Gradle':
        path_build_gradle=os.path.join(path, 'build.gradle')
        path_build_gradle_kts=os.path.join(path, 'build.gradle.kts')
        path_file = None # path of the file to be opened
        try:
            if os.path.exists(path_build_gradle):
                path_file = path_build_gradle
            else:
                path_file = path_build_gradle_kts
            with open(path_file, 'r') as file:
                content=file.read()
                if content.__contains__('mockito'):
                    return True
                else:
                    return False
        except Exception as e:
            print(e)
            return False
    return False


def _annotation_name(annotation):
    return annotation.name.split(".")[-1]


def _is_test_method(method_declaration):
    annotations = getattr(method_declaration, "annotations", []) or []
    return any(_annotation_name(annotation) in TEST_ANNOTATIONS for annotation in annotations)


def _extract_usage_metadata(response):
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")

    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0}

    prompt_tokens = getattr(usage, "prompt_tokens", None)
    if prompt_tokens is None and isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens", 0)

    completion_tokens = getattr(usage, "completion_tokens", None)
    if completion_tokens is None and isinstance(usage, dict):
        completion_tokens = usage.get("completion_tokens", 0)

    return {
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
    }


def _normalize_generated_test_content(result, name_test_class, package_test_class=None):
    if result is None:
        return None

    cleaned_result = _extract_java_source_from_text(result).strip()

    class_name_match = re.search(r"\bclass\s+(\w+)", cleaned_result)
    if class_name_match and class_name_match.group(1) != name_test_class:
        cleaned_result = re.sub(
            r"(\bclass\s+)\w+",
            rf"\1{name_test_class}",
            cleaned_result,
            count=1,
        )

    if package_test_class:
        cleaned_content = re.sub(r"//.*", "", cleaned_result)
        cleaned_content = re.sub(r"/\*.*?\*/", "", cleaned_content, flags=re.DOTALL)
        pattern = r"package\s+" + re.escape(package_test_class) + r";"
        if re.search(pattern, cleaned_content) is None:
            cleaned_result = "package " + package_test_class + ";\n" + cleaned_result

    return cleaned_result.strip()


def _extract_java_source_from_text(text):
    if text is None:
        return ""

    cleaned_text = str(text).strip()
    if not cleaned_text:
        return ""

    fence_matches = re.findall(r"```(?:java)?\s*(.*?)```", cleaned_text, re.DOTALL | re.IGNORECASE)
    if fence_matches:
        return fence_matches[-1].strip()

    class_block_match = re.search(
        r"(?s)((?:package\s+[A-Za-z_][A-Za-z0-9_\.]*\s*;\s*)?(?:import\s+[^;]+;\s*)*(?:public\s+)?(?:final\s+)?class\s+\w+.*)",
        cleaned_text,
    )
    if class_block_match:
        return class_block_match.group(1).strip()

    return cleaned_text


def _parse_java_or_none(source_code):
    try:
        return javalang.parse.parse(source_code)
    except (
        javalang.parser.JavaSyntaxError,
        javalang.tokenizer.LexerError,
        TypeError,
        IndexError,
        StopIteration,
    ):
        return None


def _get_primary_class(tree):
    if tree is None:
        return None
    for type_declaration in tree.types:
        if isinstance(type_declaration, javalang.tree.ClassDeclaration):
            return type_declaration
    return None


def _find_class_end_line(source_code, class_declaration):
    source_lines = source_code.splitlines()
    if class_declaration is None or class_declaration.position is None:
        return len(source_lines)

    start_line = class_declaration.position.line
    brace_depth = 0
    opening_brace_seen = False
    for line_index in range(start_line - 1, len(source_lines)):
        for char in source_lines[line_index]:
            if char == "{":
                brace_depth += 1
                opening_brace_seen = True
            elif char == "}":
                brace_depth -= 1
                if opening_brace_seen and brace_depth == 0:
                    return line_index + 1
    return len(source_lines)


def _extract_method_blocks(
    source_code,
    class_declaration,
    test_methods_only=False,
    include_annotations=False,
):
    if class_declaration is None:
        return {}

    method_entries = _collect_method_entries(
        source_code,
        class_declaration,
        test_methods_only=test_methods_only,
        include_annotations=include_annotations,
    )
    if not method_entries:
        return {}

    source_lines = source_code.splitlines()
    method_blocks = {}
    for method_entry in method_entries:
        method_blocks[method_entry["name"]] = "\n".join(
            source_lines[method_entry["start_line"] - 1 : method_entry["end_line"]]
        ).strip()
    return method_blocks


def _collect_method_entries(
    source_code,
    class_declaration,
    test_methods_only=False,
    include_annotations=False,
):
    if class_declaration is None:
        return []

    source_lines = source_code.splitlines()
    method_nodes = [
        method for method in class_declaration.methods if method.position is not None
    ]
    if test_methods_only:
        method_nodes = [method for method in method_nodes if _is_test_method(method)]
    method_nodes = sorted(method_nodes, key=lambda method: method.position.line)
    if not method_nodes:
        return []

    start_lines = []
    for method in method_nodes:
        start_line = method.position.line
        if include_annotations:
            annotation_start_line = start_line
            while annotation_start_line > 1:
                previous_line = source_lines[annotation_start_line - 2].strip()
                if previous_line.startswith("@"):
                    annotation_start_line -= 1
                    continue
                break
            start_line = annotation_start_line
        start_lines.append(start_line)

    class_end_line = _find_class_end_line(source_code, class_declaration)
    entries = []
    for index, method in enumerate(method_nodes):
        start_line = start_lines[index]
        if index + 1 < len(method_nodes):
            end_line = start_lines[index + 1] - 1
        else:
            end_line = class_end_line - 1
        if end_line < start_line:
            end_line = start_line
        entries.append(
            {
                "name": method.name,
                "start_line": start_line,
                "end_line": end_line,
                "is_test": _is_test_method(method),
            }
        )
    return entries


def _extract_method_block_by_name(
    source_code,
    target_method_name,
    test_methods_only=False,
    include_annotations=True,
):
    normalized_target_method = _normalize_method_name(target_method_name)
    if normalized_target_method is None:
        return None, "target method name is missing or invalid"

    tree = _parse_java_or_none(source_code)
    class_declaration = _get_primary_class(tree)
    if class_declaration is None:
        return None, "source is not a parsable Java class"

    method_entries = _collect_method_entries(
        source_code,
        class_declaration,
        test_methods_only=test_methods_only,
        include_annotations=include_annotations,
    )
    matching_entries = [
        entry for entry in method_entries if entry["name"] == normalized_target_method
    ]
    if not matching_entries:
        return None, f"target method {normalized_target_method} was not found"
    if len(matching_entries) > 1:
        return None, f"target method {normalized_target_method} is ambiguous"

    source_lines = source_code.splitlines()
    target_entry = matching_entries[0]
    method_block = "\n".join(
        source_lines[target_entry["start_line"] - 1 : target_entry["end_line"]]
    ).strip()
    if not method_block:
        return None, f"target method {normalized_target_method} block is empty"
    return method_block, ""


def _extract_target_method_block_regex(source_text, target_method_name):
    normalized_target_method = _normalize_method_name(target_method_name)
    if normalized_target_method is None:
        return None

    normalized_text = str(source_text or "").replace("\r\n", "\n")
    if not normalized_text.strip():
        return None

    method_pattern = re.compile(rf"\b{re.escape(normalized_target_method)}\s*\(")
    method_match = method_pattern.search(normalized_text)
    if method_match is None:
        return None

    lines = normalized_text.split("\n")
    line_offsets = []
    current_offset = 0
    for line in lines:
        line_offsets.append(current_offset)
        current_offset += len(line) + 1

    method_line_index = 0
    for index, line_offset in enumerate(line_offsets):
        line_end = line_offset + len(lines[index])
        if method_match.start() <= line_end:
            method_line_index = index
            break

    start_line_index = method_line_index
    while start_line_index > 0 and lines[start_line_index - 1].strip().startswith("@"):
        start_line_index -= 1

    method_search_offset = line_offsets[method_line_index]
    opening_brace_index = normalized_text.find("{", method_search_offset)
    if opening_brace_index == -1:
        return None

    brace_depth = 0
    for char_index in range(opening_brace_index, len(normalized_text)):
        current_char = normalized_text[char_index]
        if current_char == "{":
            brace_depth += 1
        elif current_char == "}":
            brace_depth -= 1
            if brace_depth == 0:
                start_offset = line_offsets[start_line_index]
                return normalized_text[start_offset : char_index + 1].strip()
    return None


def _extract_target_method_patch_block(generated_content, target_method_name):
    normalized_target_method = _normalize_method_name(target_method_name)
    if normalized_target_method is None:
        return None, "target method name is missing or invalid"

    raw_text = str(generated_content or "").strip()
    if not raw_text:
        return None, "generated patch content is empty"

    candidate_sources = [raw_text]
    extracted_source = _extract_java_source_from_text(raw_text).strip()
    if extracted_source and extracted_source not in candidate_sources:
        candidate_sources.append(extracted_source)

    for candidate_source in candidate_sources:
        direct_method_block, _ = _extract_method_block_by_name(
            candidate_source,
            normalized_target_method,
            test_methods_only=False,
            include_annotations=True,
        )
        if direct_method_block:
            return direct_method_block, ""

        wrapped_candidate = f"public class __CodexMethodPatch__ {{\n{candidate_source}\n}}"
        wrapped_method_block, _ = _extract_method_block_by_name(
            wrapped_candidate,
            normalized_target_method,
            test_methods_only=False,
            include_annotations=True,
        )
        if wrapped_method_block:
            return wrapped_method_block, ""

        regex_method_block = _extract_target_method_block_regex(
            candidate_source,
            normalized_target_method,
        )
        if regex_method_block:
            return regex_method_block, ""

    return None, (
        f"generated content does not contain a parsable block for target method "
        f"{normalized_target_method}"
    )


def _type_node_to_signature_string(type_node):
    if type_node is None:
        return "void"

    name = str(getattr(type_node, "name", "") or "")
    sub_type = getattr(type_node, "sub_type", None)
    if sub_type is not None:
        sub_name = _type_node_to_signature_string(sub_type)
        if sub_name and sub_name != "void":
            name = f"{name}.{sub_name}" if name else sub_name

    dimensions = getattr(type_node, "dimensions", None) or []
    return name + ("[]" * len(dimensions))


def _annotation_names(annotation_list):
    names = []
    for annotation in annotation_list or []:
        annotation_name = _annotation_name(annotation)
        if annotation_name:
            names.append(annotation_name)
    return names


def _parameter_to_signature(parameter):
    parameter_type = _type_node_to_signature_string(getattr(parameter, "type", None))
    parameter_name = str(getattr(parameter, "name", "") or "")
    parameter_modifiers = sorted(list(getattr(parameter, "modifiers", []) or []))
    parameter_annotations = _annotation_names(getattr(parameter, "annotations", []) or [])
    parameter_varargs = bool(getattr(parameter, "varargs", False))
    return (
        parameter_type,
        parameter_name,
        tuple(parameter_modifiers),
        tuple(parameter_annotations),
        parameter_varargs,
    )


def _throws_to_signature(method_declaration):
    throws_clause = getattr(method_declaration, "throws", None) or []
    normalized_throws = []
    for throws_type in throws_clause:
        if isinstance(throws_type, str):
            normalized_throws.append(throws_type.strip())
            continue
        throws_name = getattr(throws_type, "name", None)
        if throws_name is not None:
            normalized_throws.append(str(throws_name).strip())
            continue
        normalized_throws.append(str(throws_type).strip())
    return tuple(normalized_throws)


def _iterative_declaration_lock_fingerprint(method_declaration):
    return {
        "name": str(getattr(method_declaration, "name", "") or ""),
        "modifiers": tuple(sorted(list(getattr(method_declaration, "modifiers", []) or []))),
        "annotations": tuple(sorted(_annotation_names(getattr(method_declaration, "annotations", []) or []))),
        "return_type": _type_node_to_signature_string(getattr(method_declaration, "return_type", None)),
    }


def _parse_single_method_declaration(method_block):
    wrapper_source = f"public class __MethodWrapper__ {{\n{method_block}\n}}\n"
    tree = _parse_java_or_none(wrapper_source)
    wrapper_class = _get_primary_class(tree)
    if wrapper_class is None:
        return None, "candidate method block is not parsable Java"

    method_declarations = getattr(wrapper_class, "methods", None) or []
    if len(method_declarations) != 1:
        return None, "candidate method block must contain exactly one method declaration"
    return method_declarations[0], ""


def _extract_unqualified_method_invocations(method_declaration):
    invocation_names = set()
    if method_declaration is None:
        return invocation_names

    for _, node in method_declaration:
        if not isinstance(node, javalang.tree.MethodInvocation):
            continue
        qualifier = getattr(node, "qualifier", None)
        if qualifier not in (None, "", "this"):
            continue
        invocation_name = _normalize_method_name(getattr(node, "member", None))
        if invocation_name:
            invocation_names.add(invocation_name)
    return invocation_names


def _contains_local_focal_instantiation_ast(method_declaration, focal_class_simple_name):
    if method_declaration is None or not focal_class_simple_name:
        return False

    normalized_focal_class_name = str(focal_class_simple_name).strip()
    for _, node in method_declaration:
        if isinstance(node, javalang.tree.LocalVariableDeclaration):
            declared_type_name = _type_node_to_signature_string(getattr(node, "type", None)).split(".")[-1]
            for declarator in getattr(node, "declarators", None) or []:
                initializer = getattr(declarator, "initializer", None)
                if not isinstance(initializer, javalang.tree.ClassCreator):
                    continue
                created_type_name = _type_node_to_signature_string(getattr(initializer, "type", None)).split(".")[-1]
                if declared_type_name == normalized_focal_class_name and created_type_name == normalized_focal_class_name:
                    return True
        elif isinstance(node, javalang.tree.ClassCreator):
            created_type_name = _type_node_to_signature_string(getattr(node, "type", None)).split(".")[-1]
            if created_type_name == normalized_focal_class_name:
                return True
    return False


def _contains_local_mock_creation_ast(method_declaration):
    if method_declaration is None:
        return False

    for _, node in method_declaration:
        if not isinstance(node, javalang.tree.MethodInvocation):
            continue
        method_name = _normalize_method_name(getattr(node, "member", None))
        if method_name != "mock":
            continue
        qualifier = getattr(node, "qualifier", None)
        if qualifier in (None, "", "Mockito"):
            return True
    return False


def _contains_local_focal_instantiation_regex(method_block, focal_class_simple_name):
    if not method_block or not focal_class_simple_name:
        return False
    pattern = rf"\bnew\s+{re.escape(focal_class_simple_name)}\s*\("
    return re.search(pattern, method_block) is not None


def _contains_local_mock_creation_regex(method_block):
    if not method_block:
        return False
    if re.search(r"\bMockito\s*\.\s*mock\s*\(", method_block) is not None:
        return True
    return re.search(r"(?<!\.)\bmock\s*\(", method_block) is not None


def _method_has_local_focal_instantiation(
    method_declaration,
    method_block,
    focal_class_simple_name,
):
    has_local_focal_instantiation = _contains_local_focal_instantiation_ast(
        method_declaration,
        focal_class_simple_name,
    )
    if not has_local_focal_instantiation:
        has_local_focal_instantiation = _contains_local_focal_instantiation_regex(
            method_block,
            focal_class_simple_name,
        )
    return has_local_focal_instantiation


def _validate_iterative_method_style_lock(
    original_test_class_source,
    generated_patch_content,
    target_test_method,
    focal_class_simple_name,
):
    normalized_target_method = _normalize_method_name(target_test_method)
    if normalized_target_method is None:
        return False, "missing AST target @Test method for iterative style lock"

    original_method_block, original_method_error = _extract_method_block_by_name(
        original_test_class_source,
        normalized_target_method,
        test_methods_only=True,
        include_annotations=True,
    )
    if original_method_block is None:
        return False, f"failed to extract original mapped method block ({original_method_error})"

    candidate_method_block, candidate_method_error = _extract_target_method_patch_block(
        generated_patch_content,
        normalized_target_method,
    )
    if candidate_method_block is None:
        return False, candidate_method_error

    original_method_declaration, original_parse_error = _parse_single_method_declaration(
        original_method_block
    )
    if original_method_declaration is None:
        return False, f"failed to parse original mapped method declaration ({original_parse_error})"

    candidate_method_declaration, candidate_parse_error = _parse_single_method_declaration(
        candidate_method_block
    )
    if candidate_method_declaration is None:
        return False, f"failed to parse generated mapped method declaration ({candidate_parse_error})"

    original_declaration_lock = _iterative_declaration_lock_fingerprint(original_method_declaration)
    candidate_declaration_lock = _iterative_declaration_lock_fingerprint(candidate_method_declaration)
    if original_declaration_lock != candidate_declaration_lock:
        return (
            False,
            "mapped method declaration lock changed for "
            f"{normalized_target_method} "
            "(params/throws may change, but name/modifiers/annotations/return type must match) "
            f"(expected={original_declaration_lock}, actual={candidate_declaration_lock})",
        )

    original_has_local_focal_instantiation = _method_has_local_focal_instantiation(
        original_method_declaration,
        original_method_block,
        focal_class_simple_name,
    )
    candidate_has_local_focal_instantiation = _method_has_local_focal_instantiation(
        candidate_method_declaration,
        candidate_method_block,
        focal_class_simple_name,
    )
    if original_has_local_focal_instantiation and not candidate_has_local_focal_instantiation:
        return (
            False,
            "mapped method style requires local focal-class instantiation to be preserved "
            f"({focal_class_simple_name})",
        )
    if not original_has_local_focal_instantiation and candidate_has_local_focal_instantiation:
        return (
            False,
            "local focal-class instantiation is forbidden in iterative style lock when the mapped "
            f"method does not instantiate the focal class ({focal_class_simple_name})",
        )

    has_local_mock_creation = _contains_local_mock_creation_ast(candidate_method_declaration)
    if not has_local_mock_creation:
        has_local_mock_creation = _contains_local_mock_creation_regex(candidate_method_block)
    if has_local_mock_creation:
        return False, "local Mockito mock creation is forbidden in iterative style lock"

    original_class_methods = set(_extract_method_names_from_source(original_test_class_source) or [])
    original_unqualified_calls = _extract_unqualified_method_invocations(original_method_declaration)
    candidate_unqualified_calls = _extract_unqualified_method_invocations(candidate_method_declaration)
    added_unqualified_calls = sorted(candidate_unqualified_calls - original_unqualified_calls)
    disallowed_added_calls = [
        call_name
        for call_name in added_unqualified_calls
        if call_name not in original_class_methods
        and call_name not in ITERATIVE_STYLE_ALLOWED_UNQUALIFIED_CALLS
    ]
    if disallowed_added_calls:
        return (
            False,
            "candidate references helper/static calls not present in original style: "
            + ", ".join(disallowed_added_calls),
        )

    return True, ""


def inject_mapped_test_method_patch(
    original_test_class_source,
    generated_patch_content,
    target_test_method,
):
    normalized_target_test_method = _normalize_method_name(target_test_method)
    if normalized_target_test_method is None:
        return False, None, "target @Test method is missing or invalid"

    original_tree = _parse_java_or_none(original_test_class_source)
    original_class = _get_primary_class(original_tree)
    if original_class is None:
        return False, None, "existing test class is not parsable Java"

    target_method_entries = [
        entry
        for entry in _collect_method_entries(
            original_test_class_source,
            original_class,
            test_methods_only=True,
            include_annotations=True,
        )
        if entry["name"] == normalized_target_test_method
    ]
    if not target_method_entries:
        return (
            False,
            None,
            f"target @Test method not found in existing class: {normalized_target_test_method}",
        )
    if len(target_method_entries) > 1:
        return (
            False,
            None,
            f"target @Test method is ambiguous in existing class: {normalized_target_test_method}",
        )

    replacement_method_block, extraction_error = _extract_target_method_patch_block(
        generated_patch_content,
        normalized_target_test_method,
    )
    if replacement_method_block is None:
        return False, None, extraction_error

    source_lines = original_test_class_source.splitlines()
    target_entry = target_method_entries[0]
    replacement_lines = replacement_method_block.strip().splitlines()
    if not replacement_lines:
        return False, None, "generated target @Test method block is empty"

    patched_lines = (
        source_lines[: target_entry["start_line"] - 1]
        + replacement_lines
        + source_lines[target_entry["end_line"] :]
    )
    patched_source = "\n".join(patched_lines)
    if original_test_class_source.endswith("\n"):
        patched_source += "\n"

    return True, patched_source, ""


def _extract_candidate_test_method_blocks_in_order(candidate_source):
    normalized_source = str(candidate_source or "").strip()
    if not normalized_source:
        return []

    source_variants = [normalized_source]
    wrapped_variant = f"public class __CodexMethodPatch__ {{\n{normalized_source}\n}}"
    if wrapped_variant not in source_variants:
        source_variants.append(wrapped_variant)

    for source_variant in source_variants:
        source_tree = _parse_java_or_none(source_variant)
        source_class = _get_primary_class(source_tree)
        if source_class is None:
            continue

        method_entries = _collect_method_entries(
            source_variant,
            source_class,
            test_methods_only=True,
            include_annotations=True,
        )
        if not method_entries:
            continue

        source_lines = source_variant.splitlines()
        method_blocks = []
        for method_entry in method_entries:
            method_block = "\n".join(
                source_lines[method_entry["start_line"] - 1 : method_entry["end_line"]]
            ).strip()
            if method_block:
                method_blocks.append(
                    {
                        "name": method_entry["name"],
                        "block": method_block,
                    }
                )
        if method_blocks:
            return method_blocks
    return []


def _extract_regenerative_patch_bundle(generated_patch_content, target_test_method):
    normalized_target_test_method = _normalize_method_name(target_test_method)
    if normalized_target_test_method is None:
        return None, None, "target @Test method is missing or invalid"

    target_method_block, target_extraction_error = _extract_target_method_patch_block(
        generated_patch_content,
        normalized_target_test_method,
    )
    if target_method_block is None:
        return None, None, target_extraction_error

    raw_text = str(generated_patch_content or "").strip()
    candidate_sources = [raw_text]
    extracted_source = _extract_java_source_from_text(raw_text).strip()
    if extracted_source and extracted_source not in candidate_sources:
        candidate_sources.append(extracted_source)

    additional_test_methods = []
    for candidate_source in candidate_sources:
        candidate_method_blocks = _extract_candidate_test_method_blocks_in_order(candidate_source)
        if not candidate_method_blocks:
            continue
        if not any(
            _normalize_method_name(method_entry.get("name")) == normalized_target_test_method
            for method_entry in candidate_method_blocks
        ):
            continue
        additional_test_methods = [
            method_entry
            for method_entry in candidate_method_blocks
            if _normalize_method_name(method_entry.get("name")) != normalized_target_test_method
        ]
        break

    return target_method_block, additional_test_methods, ""


def inject_regenerative_test_method_patch_bundle(
    original_test_class_source,
    generated_patch_content,
    target_test_method,
):
    normalized_target_test_method = _normalize_method_name(target_test_method)
    if normalized_target_test_method is None:
        return False, None, "target @Test method is missing or invalid"

    original_test_blocks = _extract_method_blocks_from_source(
        original_test_class_source,
        test_methods_only=True,
    )
    if original_test_blocks is None:
        return False, None, "existing test class is not parsable Java"
    if normalized_target_test_method not in original_test_blocks:
        return (
            False,
            None,
            f"target @Test method not found in existing class: {normalized_target_test_method}",
        )

    replacement_method_block, additional_test_methods, extraction_error = _extract_regenerative_patch_bundle(
        generated_patch_content,
        normalized_target_test_method,
    )
    if replacement_method_block is None:
        return False, None, extraction_error

    injected_ok, patched_source, patch_error = inject_mapped_test_method_patch(
        original_test_class_source,
        replacement_method_block,
        normalized_target_test_method,
    )
    if not injected_ok or patched_source is None:
        return False, None, patch_error

    methods_to_append = []
    seen_new_method_names = set()
    original_test_method_names = set(original_test_blocks.keys())
    for method_entry in additional_test_methods:
        method_name = _normalize_method_name(method_entry.get("name"))
        method_block = str(method_entry.get("block") or "").strip()
        if method_name is None or not method_block:
            return False, None, "generated patch contains an invalid additional @Test method block"
        if method_name == normalized_target_test_method:
            continue

        parsed_method_declaration, parsed_method_error = _parse_single_method_declaration(method_block)
        if parsed_method_declaration is None:
            return (
                False,
                None,
                "generated patch contains an unparsable additional method "
                f"({method_name}): {parsed_method_error}",
            )
        if not _is_test_method(parsed_method_declaration):
            return (
                False,
                None,
                f"generated patch attempted to add non-@Test method: {method_name}",
            )

        if method_name in original_test_method_names:
            original_method_block = original_test_blocks.get(method_name, "").strip()
            if original_method_block == method_block.strip():
                continue
            return (
                False,
                None,
                f"generated patch attempted to redefine existing unmapped @Test method: {method_name}",
            )

        if method_name in seen_new_method_names:
            return (
                False,
                None,
                f"generated patch contains duplicate new @Test method: {method_name}",
            )
        seen_new_method_names.add(method_name)
        methods_to_append.append(method_block)

    if not methods_to_append:
        return True, patched_source, ""

    patched_tree = _parse_java_or_none(patched_source)
    patched_class = _get_primary_class(patched_tree)
    if patched_class is None:
        return False, None, "patched test class is not parsable Java after target replacement"

    target_entries = [
        entry
        for entry in _collect_method_entries(
            patched_source,
            patched_class,
            test_methods_only=True,
            include_annotations=True,
        )
        if entry["name"] == normalized_target_test_method
    ]
    if len(target_entries) != 1:
        return False, None, "failed to locate mapped target @Test method after replacement"

    patched_lines = patched_source.splitlines()
    insertion_index = max(target_entries[0]["end_line"], 0)
    appended_lines = [""]
    for method_block in methods_to_append:
        appended_lines.append(method_block)
        appended_lines.append("")
    merged_lines = patched_lines[:insertion_index] + appended_lines + patched_lines[insertion_index:]
    merged_source = "\n".join(merged_lines)
    if patched_source.endswith("\n") and not merged_source.endswith("\n"):
        merged_source += "\n"
    return True, merged_source, ""


def _extract_test_method_names_from_source(source_code):
    tree = _parse_java_or_none(source_code)
    class_declaration = _get_primary_class(tree)
    if class_declaration is None:
        return None
    return [method.name for method in class_declaration.methods if _is_test_method(method)]


def _extract_method_names_from_source(source_code):
    tree = _parse_java_or_none(source_code)
    class_declaration = _get_primary_class(tree)
    if class_declaration is None:
        return None
    return [method.name for method in class_declaration.methods]


def _extract_primary_class_name(source_code):
    tree = _parse_java_or_none(source_code)
    class_declaration = _get_primary_class(tree)
    if class_declaration is None:
        return None
    return class_declaration.name


def _extract_package_name(source_code):
    package_match = re.search(
        r"(?m)^\s*package\s+([A-Za-z_][A-Za-z0-9_\.]*)\s*;",
        source_code or "",
    )
    if package_match:
        return package_match.group(1)
    return None


def _find_empty_test_methods(source_code):
    tree = _parse_java_or_none(source_code)
    class_declaration = _get_primary_class(tree)
    if class_declaration is None:
        return []
    empty_methods = []
    for method in class_declaration.methods:
        if not _is_test_method(method):
            continue
        if not getattr(method, "body", None):
            empty_methods.append(method.name)
    return empty_methods


def _extract_method_blocks_from_source(
    source_code,
    test_methods_only=False,
    include_annotations=False,
):
    tree = _parse_java_or_none(source_code)
    class_declaration = _get_primary_class(tree)
    if class_declaration is None:
        return None
    return _extract_method_blocks(
        source_code,
        class_declaration,
        test_methods_only=test_methods_only,
        include_annotations=include_annotations,
    )


def _validate_strict_test_repair_boundaries(
    original_source,
    candidate_source,
    technique=None,
    target_test_method=None,
):
    normalized_technique = str(technique or "").strip()
    normalized_target_test_method = _normalize_method_name(target_test_method)

    original_methods = _extract_method_names_from_source(original_source)
    candidate_methods = _extract_method_names_from_source(candidate_source)
    if original_methods is None:
        return False, "failed to parse original test class methods for boundary validation"
    if candidate_methods is None:
        return False, "generated output is not a parsable Java class"

    original_method_set = set(original_methods)
    candidate_method_set = set(candidate_methods)
    added_methods_any = sorted(candidate_method_set - original_method_set)
    removed_methods_any = sorted(original_method_set - candidate_method_set)
    if normalized_technique == "iterative-healing":
        if added_methods_any or removed_methods_any:
            detail_parts = []
            if added_methods_any:
                detail_parts.append("added methods: " + ", ".join(added_methods_any))
            if removed_methods_any:
                detail_parts.append("removed or renamed methods: " + ", ".join(removed_methods_any))
            return False, "; ".join(detail_parts)
    elif removed_methods_any:
        return False, "removed or renamed methods: " + ", ".join(removed_methods_any)

    original_test_methods = _extract_test_method_names_from_source(original_source)
    candidate_test_methods = _extract_test_method_names_from_source(candidate_source)
    if original_test_methods is None:
        return False, "failed to parse original @Test methods for boundary validation"
    if candidate_test_methods is None:
        return False, "generated output is not a parsable Java test class"
    if not candidate_test_methods:
        return False, "generated output contains no @Test methods"

    original_set = set(original_test_methods)
    candidate_set = set(candidate_test_methods)
    added_methods = sorted(candidate_set - original_set)
    removed_methods = sorted(original_set - candidate_set)
    if normalized_technique == "iterative-healing":
        if added_methods or removed_methods:
            detail_parts = []
            if added_methods:
                detail_parts.append("added @Test methods: " + ", ".join(added_methods))
            if removed_methods:
                detail_parts.append("removed or renamed @Test methods: " + ", ".join(removed_methods))
            return False, "; ".join(detail_parts)
    else:
        if removed_methods:
            return False, "removed or renamed @Test methods: " + ", ".join(removed_methods)
        if normalized_technique == "regenerative-sync":
            added_non_test_methods = sorted(set(added_methods_any) - set(added_methods))
            if added_non_test_methods:
                return False, (
                    "added non-@Test helper methods are not allowed for regenerative-sync: "
                    + ", ".join(added_non_test_methods)
                )

    original_class_name = _extract_primary_class_name(original_source)
    candidate_class_name = _extract_primary_class_name(candidate_source)
    if (
        original_class_name is not None
        and candidate_class_name is not None
        and original_class_name != candidate_class_name
    ):
        return False, (
            f"class name changed from {original_class_name} to {candidate_class_name}"
        )

    original_package = _extract_package_name(original_source)
    candidate_package = _extract_package_name(candidate_source)
    if original_package != candidate_package:
        return False, (
            f"package declaration changed from {original_package} to {candidate_package}"
        )

    empty_test_methods = _find_empty_test_methods(candidate_source)
    if empty_test_methods:
        return False, "empty @Test methods detected: " + ", ".join(sorted(empty_test_methods))

    original_test_blocks = _extract_method_blocks_from_source(
        original_source,
        test_methods_only=True,
        include_annotations=True,
    )
    candidate_test_blocks = _extract_method_blocks_from_source(
        candidate_source,
        test_methods_only=True,
        include_annotations=True,
    )
    if original_test_blocks is None or candidate_test_blocks is None:
        return False, "failed to extract test method bodies for boundary validation"

    if (
        normalized_target_test_method
        and normalized_target_test_method not in original_test_blocks
        and normalized_technique in {"iterative-healing", "regenerative-sync"}
    ):
        return False, f"AST target test method not found in original class: {normalized_target_test_method}"

    for test_method_name, original_method_block in original_test_blocks.items():
        if (
            normalized_target_test_method
            and test_method_name == normalized_target_test_method
            and normalized_technique in {"iterative-healing", "regenerative-sync"}
        ):
            continue
        candidate_method_block = candidate_test_blocks.get(test_method_name)
        if candidate_method_block is None:
            return False, f"missing preserved @Test method: {test_method_name}"
        if original_method_block.strip() != candidate_method_block.strip():
            return False, f"unmapped @Test method changed: {test_method_name}"

    if normalized_technique == "iterative-healing":
        original_all_method_blocks = _extract_method_blocks_from_source(
            original_source,
            test_methods_only=False,
            include_annotations=True,
        )
        candidate_all_method_blocks = _extract_method_blocks_from_source(
            candidate_source,
            test_methods_only=False,
            include_annotations=True,
        )
        if original_all_method_blocks is None or candidate_all_method_blocks is None:
            return False, "failed to extract all method bodies for strict iterative boundary validation"

        for method_name, original_method_block in original_all_method_blocks.items():
            if (
                normalized_target_test_method
                and method_name == normalized_target_test_method
            ):
                continue
            candidate_method_block = candidate_all_method_blocks.get(method_name)
            if candidate_method_block is None:
                return False, f"missing preserved method: {method_name}"
            if original_method_block.strip() != candidate_method_block.strip():
                return False, f"non-target method changed: {method_name}"

    return True, ""


def _import_to_string(import_declaration):
    import_path = import_declaration.path
    if import_declaration.wildcard:
        import_path = import_path + ".*"
    prefix = "import static" if import_declaration.static else "import"
    return f"{prefix} {import_path};"


def _insert_missing_imports(original_source, original_tree, new_tree):
    if original_tree is None or new_tree is None:
        return original_source

    original_imports = {
        _import_to_string(import_declaration) for import_declaration in original_tree.imports
    }
    new_imports = [
        _import_to_string(import_declaration)
        for import_declaration in new_tree.imports
        if _import_to_string(import_declaration) not in original_imports
    ]
    if not new_imports:
        return original_source

    source_lines = original_source.splitlines()
    insert_index = 0
    for index, line in enumerate(source_lines):
        stripped_line = line.strip()
        if stripped_line.startswith("package "):
            insert_index = index + 1
        elif stripped_line.startswith("import "):
            insert_index = index + 1
        elif insert_index > 0 and stripped_line:
            break

    import_lines = []
    if insert_index > 0 and source_lines[insert_index - 1].strip():
        import_lines.append("")
    import_lines.extend(new_imports)

    updated_lines = source_lines[:insert_index] + import_lines + source_lines[insert_index:]
    return "\n".join(updated_lines)


def _format_ast_test_method_context(ast_context):
    invocations_by_test = ast_context.get("invocations_by_test", {})
    if not invocations_by_test:
        return "No AST-mapped focal method invocations were found inside JUnit test methods."

    formatted_blocks = []
    for test_method_name, invocation_entries in invocations_by_test.items():
        exercised_methods = []
        formatted_invocations = []
        for invocation_entry in invocation_entries:
            exercised_methods.extend(invocation_entry.get("matched_focal_methods", []))
            invocation_line = invocation_entry.get("source_line") or invocation_entry.get("invocation")
            matched_methods = ", ".join(invocation_entry.get("matched_focal_methods", [])) or "unknown focal method"
            line_number = invocation_entry.get("line")
            if line_number is not None:
                formatted_invocations.append(
                    f"line {line_number}: {invocation_line} -> {matched_methods}"
                )
            else:
                formatted_invocations.append(f"{invocation_line} -> {matched_methods}")
        exercised_methods = sorted(set(exercised_methods))
        header = f"JUnit test method {test_method_name}"
        if exercised_methods:
            header += f" -> {', '.join(exercised_methods)}"
        formatted_blocks.append(f"{header}:\n" + "\n".join(formatted_invocations))
    return "\n\n".join(formatted_blocks)


def _format_ast_focal_method_context(ast_context, fallback_focal_class):
    mapped_focal_method_sources = ast_context.get("mapped_focal_method_sources", {})
    if not mapped_focal_method_sources:
        return fallback_focal_class

    formatted_blocks = []
    for focal_method_name, source_blocks in mapped_focal_method_sources.items():
        for source_block in source_blocks:
            formatted_blocks.append(f"Focal method {focal_method_name}:\n{source_block}")
    return "\n\n".join(formatted_blocks)


def _normalize_method_name(method_name):
    if method_name is None:
        return None
    normalized_method_name = str(method_name).strip()
    if not normalized_method_name or normalized_method_name == "-" or normalized_method_name.lower() == "nan":
        return None
    return normalized_method_name


def _resolve_project_id_from_path(file_path):
    return PATH_CONTEXT.extract_project_id(file_path)


def _load_smoke_target_for_path(file_path):
    if not _is_smoke_test_mode_enabled():
        return {}
    project_id = _resolve_project_id_from_path(file_path)
    if project_id is None:
        return {}
    if project_id in SMOKE_TARGET_CACHE:
        return SMOKE_TARGET_CACHE[project_id]
    target_path = _worker_project_output_path(project_id, "smoke_target.json")
    if not os.path.exists(target_path):
        SMOKE_TARGET_CACHE[project_id] = {}
        return {}
    try:
        with open(target_path, "r", encoding="utf-8") as target_file:
            target_data = json.load(target_file) or {}
            SMOKE_TARGET_CACHE[project_id] = target_data
            return target_data
    except (OSError, json.JSONDecodeError):
        SMOKE_TARGET_CACHE[project_id] = {}
        return {}


def _filter_ast_context(ast_context, selected_test_methods=None, selected_focal_method=None):
    filtered_context = {
        "mapping": dict(ast_context.get("mapping", {})),
        "invocations_by_test": dict(ast_context.get("invocations_by_test", {})),
        "mapped_test_method_sources": dict(ast_context.get("mapped_test_method_sources", {})),
        "mapped_focal_method_sources": dict(ast_context.get("mapped_focal_method_sources", {})),
        "mapped_focal_methods": list(ast_context.get("mapped_focal_methods", [])),
    }

    selected_methods = None
    if selected_test_methods:
        selected_methods = {method_name for method_name in selected_test_methods if method_name}
        if selected_methods:
            filtered_context["mapping"] = {
                test_name: mapped_methods
                for test_name, mapped_methods in filtered_context["mapping"].items()
                if test_name in selected_methods
            }
            filtered_context["invocations_by_test"] = {
                test_name: invocation_entries
                for test_name, invocation_entries in filtered_context["invocations_by_test"].items()
                if test_name in selected_methods
            }
            filtered_context["mapped_test_method_sources"] = {
                test_name: source_block
                for test_name, source_block in filtered_context["mapped_test_method_sources"].items()
                if test_name in selected_methods
            }

    normalized_focal_method = _normalize_method_name(selected_focal_method)
    if normalized_focal_method:
        filtered_context["mapped_focal_method_sources"] = {
            focal_method_name: source_blocks
            for focal_method_name, source_blocks in filtered_context["mapped_focal_method_sources"].items()
            if focal_method_name == normalized_focal_method
        }

        filtered_mapping = {}
        for test_name, mapped_methods in filtered_context["mapping"].items():
            narrowed_methods = [method_name for method_name in mapped_methods if method_name == normalized_focal_method]
            filtered_mapping[test_name] = narrowed_methods
        filtered_context["mapping"] = filtered_mapping

        filtered_invocations = {}
        for test_name, invocation_entries in filtered_context["invocations_by_test"].items():
            narrowed_entries = []
            for invocation_entry in invocation_entries:
                matched_methods = [
                    method_name
                    for method_name in invocation_entry.get("matched_focal_methods", [])
                    if method_name == normalized_focal_method
                ]
                if not matched_methods:
                    continue
                narrowed_entry = dict(invocation_entry)
                narrowed_entry["matched_focal_methods"] = matched_methods
                narrowed_entries.append(narrowed_entry)
            if narrowed_entries:
                filtered_invocations[test_name] = narrowed_entries
        filtered_context["invocations_by_test"] = filtered_invocations

    filtered_context["mapped_focal_methods"] = sorted(filtered_context["mapped_focal_method_sources"].keys())
    return filtered_context


def _build_ast_prompt_context(focal_path, focal_class, test_path, selected_test_methods=None, selected_focal_method=None):
    try:
        ast_context = psa.build_ast_prompt_context(test_path, focal_path, selected_test_methods=selected_test_methods)
    except Exception:
        ast_context = {
            "mapping": {},
            "invocations_by_test": {},
            "mapped_test_method_sources": {},
            "mapped_focal_method_sources": {},
            "mapped_focal_methods": [],
        }

    ast_context = _filter_ast_context(
        ast_context,
        selected_test_methods=selected_test_methods,
        selected_focal_method=selected_focal_method,
    )

    return {
        "ast_test_method_context": _format_ast_test_method_context(ast_context),
        "mapped_focal_methods": json.dumps(ast_context.get("mapping", {}), indent=2),
        "focal_methods_without_tests": "[]",
        "focal_method_context": _format_ast_focal_method_context(ast_context, focal_class),
    }


def _truncate_prompt_text(text, max_chars):
    if text is None:
        return ""
    normalized_text = str(text)
    if len(normalized_text) <= max_chars:
        return normalized_text
    return normalized_text[:max_chars] + f"\n...[truncated to {max_chars} chars for smoke mode]..."


def _compact_prompt_data_for_smoke(prompt_data, technique, test_file_content):
    compact_prompt_data = prompt_data.copy()
    compact_prompt_data["project_structure"] = (
        "Smoke mode: full project structure omitted for speed. "
        "Infer imports and collaborators from the provided focal/test files and direct file inspection."
    )
    compact_prompt_data["project_dependencies"] = (
        "Smoke mode: full dependency list omitted for speed. "
        "Use existing imports, test base classes, and local file inspection as the source of truth."
    )
    compact_prompt_data["focal_class"] = _truncate_prompt_text(compact_prompt_data.get("focal_class", ""), 1200)
    compact_prompt_data["focal_method_context"] = _truncate_prompt_text(
        compact_prompt_data.get("focal_method_context", ""),
        900,
    )
    compact_prompt_data["mapped_focal_methods"] = _truncate_prompt_text(
        compact_prompt_data.get("mapped_focal_methods", "{}"),
        300,
    )
    compact_prompt_data["focal_methods_without_tests"] = _truncate_prompt_text(
        compact_prompt_data.get("focal_methods_without_tests", "[]"),
        120,
    )
    compact_prompt_data["existing_test_class"] = _truncate_prompt_text(
        compact_prompt_data.get("existing_test_class", ""),
        700,
    )
    compact_prompt_data["mapped_test_method_anchor"] = _truncate_prompt_text(
        compact_prompt_data.get("mapped_test_method_anchor", ""),
        600,
    )
    compact_prompt_data["failure_log"] = _truncate_prompt_text(
        compact_prompt_data.get("failure_log", ""),
        900,
    )
    return compact_prompt_data


def _lookup_tracking_metrics(tracking_df, test_class, test_path, generator, technique):
    metrics = {
        "Chance": 0,
        "Total_Prompt_Tokens": 0,
        "Total_Completion_Tokens": 0,
        "Iterations_to_Pass": 0,
        "High_Signal": "-",
        "Signal_Reason": "-",
    }
    if tracking_df is None or tracking_df.empty:
        return metrics

    filtered_df = tracking_df.copy()
    if "Test_Path" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Test_Path"] == test_path]
    if filtered_df.empty and "Test_Class" in tracking_df.columns:
        filtered_df = tracking_df[tracking_df["Test_Class"] == test_class]
    if "Generator(LLM/EVOSUITE)" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Generator(LLM/EVOSUITE)"] == generator]
    if technique not in (None, "-") and "Prompt_Technique" in filtered_df.columns:
        filtered_df = filtered_df[filtered_df["Prompt_Technique"] == technique]

    if filtered_df.empty:
        return metrics

    row = filtered_df.iloc[-1]
    for column_name in metrics.keys():
        if column_name in row.index and pd.notna(row[column_name]):
            metrics[column_name] = row[column_name]
    return metrics




def _load_run_settings():
    run_settings_path = os.path.join(os.path.dirname(__file__), "run_settings.yaml")
    try:
        with open(run_settings_path, "r", encoding="utf-8") as run_settings_file:
            return yaml.safe_load(run_settings_file) or {}
    except Exception as e:
        print(e)
        return {}


def _is_mock_codex_enabled():
    return bool(_load_run_settings().get("mock_codex", False))


def _get_run_setting(setting_name, default_value):
    run_settings = _load_run_settings()
    return run_settings.get(setting_name, default_value)


def get_subprocess_timeout_seconds(setting_name, default_value):
    try:
        return int(_get_run_setting(setting_name, default_value))
    except (TypeError, ValueError):
        return int(default_value)


def _replace_prompt_placeholders(text, replacements_dict):
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda match: str(replacements_dict.get(match.group(1), match.group(0))),
        text,
    )


def _get_prompt_by_name(prompt_name, prompt_data):
    prompts = ExecutionManager.get_prompts()
    raw_prompt = prompts.get(prompt_name, None)
    if raw_prompt is None:
        return None

    final_prompt = copy.deepcopy(raw_prompt)
    for role in final_prompt:
        role["content"] = _replace_prompt_placeholders(role.get("content", ""), prompt_data)
    return final_prompt


def _format_prompt_instruction(prompt_roles):
    sections = []
    for role in prompt_roles or []:
        role_name = str(role.get("role", "user")).upper()
        content = str(role.get("content", "")).strip()
        if content:
            sections.append(f"{role_name}:\n{content}")
    return "\n\n".join(sections).strip()


def _common_working_root(target_files_list):
    common_root = _common_target_root(target_files_list)
    workspace_markers = ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", ".git")
    candidate_root = common_root
    while True:
        if any(os.path.exists(os.path.join(candidate_root, marker)) for marker in workspace_markers):
            return candidate_root
        parent_root = os.path.dirname(candidate_root)
        if not parent_root or parent_root == candidate_root:
            break
        candidate_root = parent_root

    return common_root


def _common_target_root(target_files_list):
    normalized_directories = []
    for file_path in target_files_list or []:
        if not file_path:
            continue
        absolute_path = os.path.abspath(file_path)
        directory = absolute_path if os.path.isdir(absolute_path) else os.path.dirname(absolute_path)
        if directory:
            normalized_directories.append(directory)

    if not normalized_directories:
        return os.getcwd()

    try:
        return os.path.commonpath(normalized_directories)
    except ValueError:
        return os.getcwd()

def _summarize_codex_execution(execution_result):
    stdout = (execution_result.get("stdout") or "").strip()
    stderr = (execution_result.get("stderr") or "").strip()
    last_message = (execution_result.get("last_message") or "").strip()
    details = [f"returncode={execution_result.get('returncode', 1)}"]
    diagnostic_log = execution_result.get("diagnostic_log")
    if diagnostic_log:
        details.append(f"diagnostic_log={diagnostic_log}")
    last_message_path = execution_result.get("last_message_path")
    if last_message_path:
        details.append(f"last_message_path={last_message_path}")
    if stdout:
        details.append(f"stdout:\n{stdout[-4000:]}")
    if stderr:
        details.append(f"stderr:\n{stderr[-4000:]}")
    if last_message:
        details.append(f"last_message:\n{last_message[-4000:]}")
    return "\n\n".join(details)


def _resolve_codex_executable():
    configured_executable = os.getenv("AGONE_CODEX_EXECUTABLE") or _get_run_setting("codex_executable", None)
    if configured_executable:
        configured_executable = os.path.abspath(os.path.expandvars(os.path.expanduser(str(configured_executable))))
        return configured_executable
    sandbox_binary = os.path.join(os.path.expanduser("~"), ".codex", ".sandbox-bin", "codex.exe")
    if os.path.isfile(sandbox_binary):
        return sandbox_binary
    executable_candidates = ["codex.cmd", "codex.exe", "codex"]
    for executable_name in executable_candidates:
        executable_path = shutil.which(executable_name)
        if executable_path:
            return executable_path
    return "codex"


def _is_smoke_test_mode_enabled():
    return os.getenv("AGONE_SMOKE_TEST", "0").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_codex_diagnostic_log_path(target_files_list):
    project_id = None
    for file_path in target_files_list or []:
        detected_project = PATH_CONTEXT.extract_project_id(file_path)
        if detected_project is not None:
            project_id = detected_project
            break

    if project_id is not None:
        output_directory = PATH_CONTEXT.get_project_output_path(project_id)
    else:
        output_directory = PATH_CONTEXT.get_output_path()
    os.makedirs(output_directory, exist_ok=True)
    return os.path.join(output_directory, "codex_cli_diagnostics.log")


def _resolve_codex_last_message_path(target_files_list):
    project_id = None
    for file_path in target_files_list or []:
        detected_project = PATH_CONTEXT.extract_project_id(file_path)
        if detected_project is not None:
            project_id = detected_project
            break

    if project_id is not None:
        output_directory = PATH_CONTEXT.get_project_output_path(project_id)
    else:
        output_directory = PATH_CONTEXT.get_output_path()
    os.makedirs(output_directory, exist_ok=True)
    return os.path.join(output_directory, "codex_last_message.txt")


def _resolve_failure_log_path_for_file(file_path):
    project_id = PATH_CONTEXT.extract_project_id(file_path)
    if project_id:
        output_directory = PATH_CONTEXT.get_project_output_path(project_id)
    else:
        output_directory = PATH_CONTEXT.get_output_path()
    os.makedirs(output_directory, exist_ok=True)
    return os.path.join(output_directory, "latest_failure_log.txt")


def _load_failure_log_for_path(file_path, max_chars=12000):
    failure_log_path = _resolve_failure_log_path_for_file(file_path)
    if not os.path.exists(failure_log_path):
        return ""
    try:
        with open(failure_log_path, "r", encoding="utf-8", errors="replace") as failure_log_file:
            failure_text = failure_log_file.read().strip()
            if not failure_text:
                return ""
            if len(failure_text) <= max_chars:
                return failure_text
            return failure_text[-max_chars:]
    except Exception:
        return ""


def _resolve_codex_workspace_root(target_files_list):
    diagnostic_log_path = _resolve_codex_diagnostic_log_path(target_files_list)
    workspace_root = os.path.join(os.path.dirname(diagnostic_log_path), "codex_workspace")
    if os.path.exists(workspace_root):
        shutil.rmtree(workspace_root, ignore_errors=True)
    os.makedirs(workspace_root, exist_ok=True)
    return workspace_root


def _append_codex_diagnostic(log_path, message):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a", encoding="utf-8", errors="replace") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


def _read_text_tail(file_path, max_chars=4000):
    if not os.path.exists(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as log_file:
            return log_file.read()[-max_chars:]
    except Exception:
        return ""


def _read_text_file(file_path):
    if not os.path.exists(file_path):
        return ""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as text_file:
            return text_file.read()
    except Exception:
        return ""


def _run_codex_preflight(codex_executable, working_root, diagnostic_log_path):
    preflight_timeout_seconds = get_subprocess_timeout_seconds("codex_preflight_timeout_seconds", 20)
    version_command = [codex_executable, "--version"]
    _append_codex_diagnostic(
        diagnostic_log_path,
        f"Starting Codex version preflight. command={version_command} timeout={preflight_timeout_seconds}s",
    )
    version_start = time.monotonic()
    with open(diagnostic_log_path, "a", encoding="utf-8", errors="replace") as diagnostic_log:
        try:
            version_result = subprocess.run(
                version_command,
                stdout=diagnostic_log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                timeout=preflight_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - version_start
            _append_codex_diagnostic(
                diagnostic_log_path,
                f"Codex version preflight timed out after {elapsed:.2f}s.",
            )
            return False, f"Codex version preflight timed out after {preflight_timeout_seconds} seconds."
        except Exception as exc:
            elapsed = time.monotonic() - version_start
            _append_codex_diagnostic(
                diagnostic_log_path,
                f"Codex version preflight raised {type(exc).__name__} after {elapsed:.2f}s: {exc}",
            )
            return False, f"Codex version preflight failed: {exc}"

    elapsed = time.monotonic() - version_start
    _append_codex_diagnostic(
        diagnostic_log_path,
        f"Finished Codex version preflight in {elapsed:.2f}s with returncode={version_result.returncode}.",
    )
    if version_result.returncode != 0:
        return False, f"Codex version preflight exited with returncode {version_result.returncode}."

    if _is_smoke_test_mode_enabled():
        exec_preflight_timeout_seconds = get_subprocess_timeout_seconds("codex_exec_preflight_timeout_seconds", 20)
        exec_command = _build_codex_exec_command(codex_executable, working_root)
        exec_prompt = "Reply with the single word OK. Do not modify any files."
        _append_codex_diagnostic(
            diagnostic_log_path,
            f"Starting Codex exec preflight. command={exec_command} timeout={exec_preflight_timeout_seconds}s",
        )
        exec_start = time.monotonic()
        with open(diagnostic_log_path, "a", encoding="utf-8", errors="replace") as diagnostic_log:
            try:
                exec_result = subprocess.run(
                    exec_command,
                    input=exec_prompt,
                    stdout=diagnostic_log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    timeout=exec_preflight_timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - exec_start
                _append_codex_diagnostic(
                    diagnostic_log_path,
                    f"Codex exec preflight timed out after {elapsed:.2f}s.",
                )
                return False, f"Codex exec preflight timed out after {exec_preflight_timeout_seconds} seconds."
            except Exception as exc:
                elapsed = time.monotonic() - exec_start
                _append_codex_diagnostic(
                    diagnostic_log_path,
                    f"Codex exec preflight raised {type(exc).__name__} after {elapsed:.2f}s: {exc}",
                )
                return False, f"Codex exec preflight failed: {exc}"

        elapsed = time.monotonic() - exec_start
        _append_codex_diagnostic(
            diagnostic_log_path,
            f"Finished Codex exec preflight in {elapsed:.2f}s with returncode={exec_result.returncode}.",
        )
        if exec_result.returncode != 0:
            return False, f"Codex exec preflight exited with returncode {exec_result.returncode}."

    return True, ""


def _build_codex_exec_command(codex_executable, working_root, output_last_message_path=None):
    command = [codex_executable]
    if os.name == "nt":
        command.extend(["-c", 'windows.sandbox="unelevated"'])
    command.extend(
        [
            "-a",
            "never",
            "-s",
            "read-only",
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
            "-C",
            working_root,
        ]
    )
    if output_last_message_path:
        command.extend(["-o", output_last_message_path])
    command.append("-")
    return command


def run_codex_agent(prompt_instruction, target_files_list):
    """
    Runs Codex non-interactively against a focused set of files and captures
    stdout/stderr for pipeline diagnostics.
    """
    normalized_files = []
    seen_paths = set()
    for file_path in target_files_list or []:
        if not file_path:
            continue
        absolute_path = os.path.abspath(file_path)
        if absolute_path not in seen_paths:
            seen_paths.add(absolute_path)
            normalized_files.append(absolute_path)

    working_root = _common_working_root(normalized_files)
    scoped_file_descriptions = []
    for file_path in normalized_files:
        try:
            relative_path = os.path.relpath(file_path, working_root).replace("\\", "/")
        except ValueError:
            relative_path = os.path.abspath(file_path).replace("\\", "/")
        scoped_file_descriptions.append(f"- {relative_path}")
    allowed_files_text = "\n".join(scoped_file_descriptions) or "- (none)"
    scoped_prompt = (
        "[SYSTEM DIRECTIVE: YOU ARE IN A RESTRICTED READ-ONLY ENVIRONMENT. DO NOT USE ANY TOOLS, SHELL COMMANDS, BASH, OR POWERSHELL. YOU MUST ONLY OUTPUT THE FINAL JAVA CODE. DO NOT ATTEMPT TO SEARCH, READ, OR WRITE FILES ON THE DISK.]\n\n"
        f"{str(prompt_instruction).strip()}\n\n"
        "Reference file scope (read-only context):\n"
        f"{allowed_files_text}\n"
        "Return only the final Java source code."
    )
    usage_metadata = {"prompt_tokens": 0, "completion_tokens": 0}
    diagnostic_log_path = _resolve_codex_diagnostic_log_path(normalized_files)
    last_message_path = _resolve_codex_last_message_path(normalized_files)

    if _is_mock_codex_enabled():
        _append_codex_diagnostic(diagnostic_log_path, "mock_codex enabled; Codex subprocess skipped.")
        return {
            "success": True,
            "returncode": 0,
            "stdout": "mock_codex enabled; skipped Codex subprocess.",
            "stderr": "",
            "command": ["mock_codex"],
            "usage": usage_metadata,
            "diagnostic_log": diagnostic_log_path,
            "last_message": "",
            "last_message_path": last_message_path,
        }

    codex_timeout_seconds = get_subprocess_timeout_seconds("codex_timeout_seconds", 300)
    if _is_smoke_test_mode_enabled():
        codex_timeout_seconds = min(
            codex_timeout_seconds,
            get_subprocess_timeout_seconds("codex_smoke_timeout_seconds", 60),
        )
    codex_executable = _resolve_codex_executable()
    command = _build_codex_exec_command(codex_executable, working_root, last_message_path)
    try:
        if os.path.exists(last_message_path):
            os.remove(last_message_path)
    except OSError:
        pass

    print(f"Codex diagnostic log: {os.path.abspath(diagnostic_log_path)}")
    print(f"Codex last message path: {os.path.abspath(last_message_path)}")
    print(f"Codex executable resolved to: {codex_executable}")
    print(f"Codex working root: {working_root}")
    print(f"Codex target files count: {len(normalized_files)}")
    print(f"Codex prompt length: {len(scoped_prompt)} characters")
    print(f"Codex timeout: {codex_timeout_seconds} seconds")
    if "WindowsApps" in codex_executable:
        print("Warning: Codex executable is running from WindowsApps; this can hang if the packaged CLI wrapper is interactive.")
    _append_codex_diagnostic(
        diagnostic_log_path,
        f"Resolved Codex executable to {codex_executable}",
    )
    _append_codex_diagnostic(
        diagnostic_log_path,
        f"Codex working_root={working_root} timeout={codex_timeout_seconds}s prompt_length={len(scoped_prompt)} target_files={normalized_files} last_message_path={last_message_path}",
    )

    preflight_ok, preflight_message = _run_codex_preflight(codex_executable, working_root, diagnostic_log_path)
    if not preflight_ok:
        print(f"Codex preflight failed: {preflight_message}")
        diagnostic_tail = _read_text_tail(diagnostic_log_path)
        return {
            "success": False,
            "returncode": 1,
            "stdout": diagnostic_tail,
            "stderr": preflight_message,
            "command": command,
            "usage": usage_metadata,
            "diagnostic_log": diagnostic_log_path,
            "last_message": _read_text_file(last_message_path),
            "last_message_path": last_message_path,
        }

    start_time = time.monotonic()
    print("Starting Codex subprocess...")
    _append_codex_diagnostic(
        diagnostic_log_path,
        f"Starting Codex subprocess. command={command}",
    )
    try:
        with open(diagnostic_log_path, "a", encoding="utf-8", errors="replace") as diagnostic_log:
            result = subprocess.run(
                command,
                input=scoped_prompt,
                stdout=diagnostic_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=codex_timeout_seconds,
            )
    except subprocess.TimeoutExpired as e:
        elapsed = time.monotonic() - start_time
        _append_codex_diagnostic(
            diagnostic_log_path,
            f"Codex subprocess timed out after {elapsed:.2f}s.",
        )
        print(f"Codex subprocess timed out after {elapsed:.2f}s. Diagnostic log: {os.path.abspath(diagnostic_log_path)}")
        return {
            "success": False,
            "returncode": 124,
            "stdout": _read_text_tail(diagnostic_log_path),
            "stderr": f"Codex CLI timed out after {codex_timeout_seconds} seconds.",
            "command": command,
            "usage": usage_metadata,
            "diagnostic_log": diagnostic_log_path,
            "last_message": _read_text_file(last_message_path),
            "last_message_path": last_message_path,
        }
    except Exception as e:
        elapsed = time.monotonic() - start_time
        _append_codex_diagnostic(
            diagnostic_log_path,
            f"Codex subprocess raised {type(e).__name__} after {elapsed:.2f}s: {e}",
        )
        print(f"Codex subprocess raised {type(e).__name__}: {e}")
        return {
            "success": False,
            "returncode": 1,
            "stdout": _read_text_tail(diagnostic_log_path),
            "stderr": str(e),
            "command": command,
            "usage": usage_metadata,
            "diagnostic_log": diagnostic_log_path,
            "last_message": _read_text_file(last_message_path),
            "last_message_path": last_message_path,
        }

    elapsed = time.monotonic() - start_time
    diagnostic_tail = _read_text_tail(diagnostic_log_path)
    last_message = _read_text_file(last_message_path)
    _append_codex_diagnostic(
        diagnostic_log_path,
        f"Finished Codex subprocess in {elapsed:.2f}s with returncode={result.returncode}.",
    )
    print(f"Codex subprocess finished in {elapsed:.2f}s with returncode={result.returncode}.")
    print(f"Codex diagnostic log available at: {os.path.abspath(diagnostic_log_path)}")

    return {
        "success": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": diagnostic_tail,
        "stderr": "",
        "command": command,
        "usage": usage_metadata,
        "diagnostic_log": diagnostic_log_path,
        "last_message": last_message,
        "last_message_path": last_message_path,
    }


def generate_test_with_codex(
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
    package_test_class=None,
    output_contract="full_class",
    target_test_method=None,
    target_focal_method=None,
    failure_log_override=None,
):
    """
    Expands the configured prompt template and asks Codex CLI to generate test
    source text. Python code applies the write to disk after validation.
    """
    java_class_example = (
        "package com.example.project;\n\npublic class Calculator {\n\n\tpublic int add(int a, int b) {\n\n\treturn a + b;\n\t}\n\n}"
    )
    test_class_example = (
        'package com.example.project;\n\nimport static org.junit.jupiter.api.Assertions.assertEquals;\n'
        'import org.junit.jupiter.api.DisplayName;\nimport org.junit.jupiter.api.Test;\n'
        'import org.junit.jupiter.params.ParameterizedTest;\n'
        'import org.junit.jupiter.params.provider.CsvSource;\n\nclass CalculatorTests {\n\n\t@Test\n'
        '\t@DisplayName("1 + 1 = 2")\n\tvoid addsTwoNumbers() {\n\t\tCalculator calculator = new Calculator();\n'
        '\t\tassertEquals(2, calculator.add(1, 1), "1 + 1 should equal 2");\n\t}\n\n'
        '\t@ParameterizedTest(name = "{0} + {1} = {2}")\n\t@CsvSource({\n\t\t\t"0,    1,   1",\n'
        '\t\t\t"1,    2,   3",\n\t\t\t"49,  51, 100",\n\t\t\t"1,  100, 101"\n\t})\n'
        '\tvoid add(int first, int second, int expectedResult) {\n\t\tCalculator calculator = new Calculator();\n'
        '\t\tassertEquals(expectedResult, calculator.add(first, second),\n'
        '\t\t\t\t() -> first + " + " + second + " should equal " + expectedResult);\n\t}\n}'
    )

    try:
        with open(test_path, "r", encoding="utf-8") as test_file:
            test_file_content = test_file.read()
    except Exception as e:
        print(e)
        return None, [], {"prompt_tokens": 0, "completion_tokens": 0}

    if has_mockito is True or "mockito" in test_file_content.lower():
        can_use_mockito = "You can use the Mockito framework."
    else:
        can_use_mockito = "You cannot use the Mockito framework."

    normalized_output_contract = str(output_contract or "full_class").strip().lower()
    if normalized_output_contract not in {"full_class", "mapped_method", "mapped_method_additive"}:
        normalized_output_contract = "full_class"

    failure_log_text = (
        str(failure_log_override).strip()
        if failure_log_override is not None and str(failure_log_override).strip()
        else _load_failure_log_for_path(test_path)
    )

    normalized_target_test_method = _normalize_method_name(target_test_method)
    normalized_target_focal_method = _normalize_method_name(target_focal_method)

    prompt_data = {
        "focal_class": focal_class,
        "focal_path": focal_path,
        "testing_framework": testing_framework,
        "java_version": java_version,
        "project_structure": str(project_structure),
        "project_dependencies": str(project_dependencies),
        "java_class_example": java_class_example,
        "test_class_example": test_class_example,
        "example_testing_framework": "JUnit 5",
        "example_java_version": "11",
        "can_use_mockito": can_use_mockito,
        "set_public": "You must declare the test class and all the test methods with the public access modifier.",
        "existing_test_class": test_file_content,
        "mapped_focal_methods": "{}",
        "focal_methods_without_tests": "[]",
        "focal_method_context": focal_class,
        "mapped_test_method_anchor": "",
        "failure_log": failure_log_text,
    }

    selected_test_methods = None
    selected_focal_method = None
    if normalized_target_test_method:
        selected_test_methods = [normalized_target_test_method]
    if normalized_target_focal_method:
        selected_focal_method = normalized_target_focal_method

    prompt_data.update(
        _build_ast_prompt_context(
            focal_path,
            focal_class,
            test_path,
            selected_test_methods=selected_test_methods,
            selected_focal_method=selected_focal_method,
        )
    )

    smoke_target = {}
    if _is_smoke_test_mode_enabled():
        smoke_target = _load_smoke_target_for_path(test_path)
        if normalized_target_test_method is None:
            smoke_test_case = _normalize_method_name(smoke_target.get("test_case"))
            if smoke_test_case:
                selected_test_methods = [smoke_test_case]
                normalized_target_test_method = smoke_test_case
        if normalized_target_focal_method is None:
            smoke_focal_method = _normalize_method_name(smoke_target.get("focal_method"))
            if smoke_focal_method:
                selected_focal_method = smoke_focal_method
                normalized_target_focal_method = smoke_focal_method
        if selected_test_methods or selected_focal_method:
            prompt_data.update(
                _build_ast_prompt_context(
                    focal_path,
                    focal_class,
                    test_path,
                    selected_test_methods=selected_test_methods,
                    selected_focal_method=selected_focal_method,
                )
            )

    if _is_smoke_test_mode_enabled():
        prompt_data = _compact_prompt_data_for_smoke(prompt_data, technique, test_file_content)

    ast_target_test_case = normalized_target_test_method
    ast_target_focal_method = normalized_target_focal_method
    if ast_target_test_case is None:
        try:
            mapped_focal_methods = json.loads(prompt_data.get("mapped_focal_methods", "{}"))
            if isinstance(mapped_focal_methods, dict) and mapped_focal_methods:
                ast_target_test_case = _normalize_method_name(next(iter(mapped_focal_methods.keys())))
                mapped_focal_list = mapped_focal_methods.get(ast_target_test_case, [])
                if (
                    isinstance(mapped_focal_list, list)
                    and mapped_focal_list
                    and ast_target_focal_method is None
                ):
                    ast_target_focal_method = _normalize_method_name(mapped_focal_list[0])
        except (TypeError, json.JSONDecodeError):
            pass

    if ast_target_test_case:
        mapped_test_method_anchor, _ = _extract_method_block_by_name(
            test_file_content,
            ast_target_test_case,
            test_methods_only=True,
            include_annotations=True,
        )
        if mapped_test_method_anchor:
            prompt_data["mapped_test_method_anchor"] = mapped_test_method_anchor

    prompt_roles = _get_prompt_by_name(technique, prompt_data)
    if prompt_roles is None:
        return None, [], {"prompt_tokens": 0, "completion_tokens": 0}

    instruction_parts = [
        _format_prompt_instruction(prompt_roles),
        "Use the provided focal class and AST mapping as the source of truth.",
    ]
    smoke_mode_enabled = _is_smoke_test_mode_enabled()
    if technique == "iterative-healing":
        if ast_target_test_case:
            instruction_parts.append(
                f"Repair only the mapped @Test method `{ast_target_test_case}`."
            )
        instruction_parts.append(
            "Preserve the mapped method declaration lock exactly: name, modifiers, annotations, and return type must match the original mapped method."
        )
        instruction_parts.append(
            "You may change parameters and throws only if needed to align with evolved focal behavior."
        )
        focal_class_simple_name = os.path.splitext(os.path.basename(str(focal_path or "")))[0]
        mapped_style_anchor = str(prompt_data.get("mapped_test_method_anchor", "") or "").strip()
        anchor_has_local_focal_instantiation = _contains_local_focal_instantiation_regex(
            mapped_style_anchor,
            focal_class_simple_name,
        )
        if anchor_has_local_focal_instantiation:
            instruction_parts.append(
                "Preserve local focal-class instantiation style from the mapped method anchor when present "
                f"(e.g., `new {focal_class_simple_name}(...)`)."
            )
        else:
            instruction_parts.append(
                "Do not introduce local focal-class instantiation if the mapped method anchor does not use it."
            )
        instruction_parts.append("Do not create local Mockito mocks inside the target method.")
        instruction_parts.append("Do not modify any other method in the class.")
        instruction_parts.append("Do not add helpers, fields, imports, or annotations.")
        if mapped_style_anchor:
            instruction_parts.append(
                "Preserve this method style anchor exactly (except for the minimal assertion/setup edits needed to resolve the failure):\n"
                f"<code>\n{mapped_style_anchor}\n</code>"
            )
    elif technique == "regenerative-sync":
        if ast_target_test_case:
            instruction_parts.append(
                f"Update the mapped @Test method `{ast_target_test_case}` first."
            )
        instruction_parts.append("Preserve all existing unmapped @Test methods exactly as they are.")
        instruction_parts.append("You may add new @Test methods only if the focal change requires extra coverage.")
        instruction_parts.append(
            "Do not rewrite package/class declarations, imports, fields, or helper methods."
        )
    else:
        instruction_parts.append(
            f"Rewrite the test file as needed so it is a complete compilable Java test class named {name_test_class}."
        )
    if ast_target_test_case and ast_target_focal_method:
        instruction_parts.append(
            f"AST target mapping: {ast_target_test_case} -> {ast_target_focal_method}."
        )
    elif ast_target_test_case:
        instruction_parts.append(f"AST target test method: {ast_target_test_case}.")
    if package_test_class:
        instruction_parts.append(f"Keep or add this package declaration: package {package_test_class};")
    if smoke_mode_enabled:
        instruction_parts.append(
            "Smoke mode: prefer the provided focal and test files, avoid exploring unrelated modules, and make the minimal direct edit needed to restore compilation or coverage."
        )
    if normalized_output_contract == "mapped_method":
        instruction_parts.append(
            "Return only the full mapped @Test method block (annotations + signature + body)."
        )
        instruction_parts.append(
            "Do not return the entire class, and do not include markdown fences or explanations."
        )
    elif normalized_output_contract == "mapped_method_additive":
        instruction_parts.append(
            "Return method blocks only: the mapped @Test method block is required, and optional brand-new @Test method blocks may follow."
        )
        instruction_parts.append(
            "Do not return a full class, package declaration, imports, fields, or helper methods."
        )
        instruction_parts.append(
            "Do not include markdown fences or explanations."
        )
    else:
        instruction_parts.append(
            "Return only the complete Java test class source code. Do not use markdown fences or explanations."
        )
    prompt_instruction = "\n\n".join(part for part in instruction_parts if part)

    messages = [{"role": "user", "content": prompt_instruction}]
    execution_result = run_codex_agent(prompt_instruction, [test_path, focal_path])
    execution_summary = _summarize_codex_execution(execution_result)
    messages.append({"role": "assistant", "content": execution_summary})
    if _is_smoke_test_mode_enabled() or not execution_result.get("success"):
        print(f"Codex execution summary:\n{execution_summary}")
    usage_metadata = execution_result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0})
    last_message_content = execution_result.get("last_message")
    if not last_message_content and execution_result.get("success"):
        last_message_content = execution_result.get("stdout")

    if normalized_output_contract in {"mapped_method", "mapped_method_additive"}:
        if not execution_result.get("success") and not (last_message_content or "").strip():
            return None, messages, usage_metadata

        if technique == "iterative-healing":
            focal_class_simple_name = ""
            if focal_path:
                focal_class_simple_name = os.path.splitext(os.path.basename(str(focal_path)))[0]
            style_lock_passed, style_lock_reason = _validate_iterative_method_style_lock(
                test_file_content,
                last_message_content,
                ast_target_test_case,
                focal_class_simple_name,
            )
            if not style_lock_passed:
                validation_message = (
                    f"Codex output rejected for {technique}: iterative style-lock validation failed "
                    f"({style_lock_reason})."
                )
                print(validation_message)
                diagnostic_log_path = execution_result.get("diagnostic_log")
                if diagnostic_log_path:
                    _append_codex_diagnostic(diagnostic_log_path, validation_message)
                usage_metadata = dict(usage_metadata or {})
                usage_metadata["rejection_reason"] = validation_message
                messages.append({"role": "assistant", "content": validation_message})
                write_file(test_path, test_file_content)
                return None, messages, usage_metadata

        if normalized_output_contract == "mapped_method_additive":
            injected_ok, patched_test_content, patch_error = inject_regenerative_test_method_patch_bundle(
                test_file_content,
                last_message_content,
                ast_target_test_case,
            )
        else:
            injected_ok, patched_test_content, patch_error = inject_mapped_test_method_patch(
                test_file_content,
                last_message_content,
                ast_target_test_case,
            )
        if not injected_ok or patched_test_content is None:
            validation_message = (
                f"Codex output rejected for {technique}: method-patch merge failed ({patch_error})."
            )
            print(validation_message)
            diagnostic_log_path = execution_result.get("diagnostic_log")
            if diagnostic_log_path:
                _append_codex_diagnostic(diagnostic_log_path, validation_message)
            usage_metadata = dict(usage_metadata or {})
            usage_metadata["rejection_reason"] = validation_message
            messages.append({"role": "assistant", "content": validation_message})
            write_file(test_path, test_file_content)
            return None, messages, usage_metadata

        boundaries_respected, boundary_reason = _validate_strict_test_repair_boundaries(
            test_file_content,
            patched_test_content,
            technique=technique,
            target_test_method=ast_target_test_case,
        )
        if not boundaries_respected:
            validation_message = (
                f"Codex output rejected for {technique}: strict boundary validation failed ({boundary_reason})."
            )
            print(validation_message)
            diagnostic_log_path = execution_result.get("diagnostic_log")
            if diagnostic_log_path:
                _append_codex_diagnostic(diagnostic_log_path, validation_message)
            usage_metadata = dict(usage_metadata or {})
            usage_metadata["rejection_reason"] = validation_message
            messages.append({"role": "assistant", "content": validation_message})
            write_file(test_path, test_file_content)
            return None, messages, usage_metadata

        write_file(test_path, patched_test_content)
        return patched_test_content, messages, usage_metadata

    fallback_generated_content = _normalize_generated_test_content(
        last_message_content,
        name_test_class,
        package_test_class,
    )

    if not execution_result.get("success") and not fallback_generated_content:
        return None, messages, usage_metadata

    if fallback_generated_content is not None:
        generated_test_content = fallback_generated_content
        write_file(test_path, generated_test_content)
    else:
        try:
            with open(test_path, "r", encoding="utf-8") as generated_test_file:
                generated_test_content = generated_test_file.read()
        except Exception as e:
            messages.append({"role": "assistant", "content": str(e)})
            return None, messages, usage_metadata

    normalized_test_content = _normalize_generated_test_content(
        generated_test_content,
        name_test_class,
        package_test_class,
    )
    if normalized_test_content is None:
        return None, messages, usage_metadata

    if technique in {"regenerative-sync", "iterative-healing"}:
        boundaries_respected, boundary_reason = _validate_strict_test_repair_boundaries(
            test_file_content,
            normalized_test_content,
            technique=technique,
            target_test_method=ast_target_test_case,
        )
        if not boundaries_respected:
            validation_message = (
                f"Codex output rejected for {technique}: strict boundary validation failed ({boundary_reason})."
            )
            print(validation_message)
            diagnostic_log_path = execution_result.get("diagnostic_log")
            if diagnostic_log_path:
                _append_codex_diagnostic(diagnostic_log_path, validation_message)
            messages.append({"role": "assistant", "content": validation_message})
            write_file(test_path, test_file_content)
            return None, messages, usage_metadata

    if normalized_test_content != generated_test_content:
        write_file(test_path, normalized_test_content)

    return normalized_test_content, messages, usage_metadata




def generate_output_csv_project(project, project_dataframe, test_types, techniques, module=None):
    """
    Generates and saves the output CSV file that includes all the measures about all the test types applied in the project (code coverage, mutation coverage and number of test smells).
    Parameters:
                project: the ID of the project
                project_dataframe (Dataframe): the dataframe that contains all the focal classes and test classes involved in the test types 
                test_types: all the test types
                techniques: all the prompt techniques associated with the AI test types
                module: the name of the module (optional)
    Returns:
                df_output (Dataframe): the dataframe that includes all the measures about all the test types applied in the project (code coverage, mutation coverage and number of test smells).
                output_csv_path: the path of the CSV file that includes all the measures about all the test types applied in the project (code coverage, mutation coverage and number of test smells)    
    """
    try:
        import gradleLib
    except Exception:
        gradleLib = None

    tracking_frames = []
    if hasattr(mavenLib, "df_chance") and not mavenLib.df_chance.empty:
        tracking_frames.append(mavenLib.df_chance.copy())
    if gradleLib is not None and hasattr(gradleLib, "df_chance") and not gradleLib.df_chance.empty:
        tracking_frames.append(gradleLib.df_chance.copy())
    if tracking_frames:
        df_chance = pd.concat(tracking_frames, ignore_index=True)
    else:
        df_chance = pd.DataFrame()

    def normalize_test_path(test_path_value):
        if test_path_value is None or pd.isna(test_path_value):
            return None
        return PATH_CONTEXT.to_worker_compiled_path(project, test_path_value)

    def normalize_focal_path(focal_path_value):
        if focal_path_value is None or pd.isna(focal_path_value):
            return None
        return PATH_CONTEXT.to_worker_compiled_path(project, focal_path_value)

    def load_mutation_lookup():
        mutation_lookup = {}
        mutation_file_path = _worker_project_output_path(project, "focal_mutations.json")
        if not os.path.exists(mutation_file_path):
            return mutation_lookup
        try:
            with open(mutation_file_path, "r", encoding="utf-8") as mutation_file:
                mutation_records = json.load(mutation_file)
        except (OSError, json.JSONDecodeError):
            return mutation_lookup

        for mutation_record in mutation_records:
            focal_path = normalize_focal_path(mutation_record.get("focal_path"))
            if focal_path is None:
                continue
            mutation_type = mutation_record.get("mutation_type", "unknown")
            method_name = mutation_record.get("method_name", "-")
            summary = f"{mutation_type}:{method_name}"
            if mutation_type == "logical":
                old_operator = mutation_record.get("old_operator", "?")
                new_operator = mutation_record.get("new_operator", "?")
                summary = f"{mutation_type}:{method_name}:{old_operator}->{new_operator}"
            elif mutation_type == "signature":
                inserted_parameter = mutation_record.get("inserted_parameter", "unusedFlag")
                summary = f"{mutation_type}:{method_name}:{inserted_parameter}"
            elif mutation_type == "exception":
                added_exception = mutation_record.get("added_exception", "Exception")
                summary = f"{mutation_type}:{method_name}:{added_exception}"
            mutation_lookup[focal_path] = summary
        return mutation_lookup

    mutation_lookup = load_mutation_lookup()

    def apply_tracking_metrics(output_index, project_row, generator, prompt_technique):
        if project_row is None:
            return
        normalized_test_path = normalize_test_path(project_row.get("Test_Path"))
        metrics = _lookup_tracking_metrics(
            df_chance,
            project_row.get("Test_Class"),
            normalized_test_path,
            generator,
            prompt_technique,
        )
        for column_name, value in metrics.items():
            if column_name in df_output.columns:
                df_output.at[output_index, column_name] = value
        if metrics.get("Chance") == 6:
            df_output.at[output_index, "Compilation"] = "0"

    def apply_mutation_metrics(output_index, project_row):
        if project_row is None:
            return
        compilation_value = "-"
        if "Compilation" in df_output.columns:
            compilation_value = str(df_output.at[output_index, "Compilation"]).strip()
        # If compilation failed, mutation score is unavailable (not a measured 0.0).
        if compilation_value == "0":
            if "Mutation_Coverage" in df_output.columns:
                df_output.at[output_index, "Mutation_Coverage"] = "-"
            if "Post_Repair_Mutation_Coverage" in df_output.columns:
                df_output.at[output_index, "Post_Repair_Mutation_Coverage"] = "-"
            return
        normalized_focal_path = normalize_focal_path(project_row.get("Focal_Path"))
        mutation_applied = mutation_lookup.get(normalized_focal_path, "-")
        if "Mutation_Applied" in df_output.columns:
            df_output.at[output_index, "Mutation_Applied"] = mutation_applied
        if "Post_Repair_Mutation_Coverage" in df_output.columns and "Mutation_Coverage" in df_output.columns:
            df_output.at[output_index, "Post_Repair_Mutation_Coverage"] = df_output.at[output_index, "Mutation_Coverage"]
    project_df = project_dataframe.copy()
    project_output_dir = PATH_CONTEXT.get_project_output_path(project)
    files = os.listdir(project_output_dir) if os.path.isdir(project_output_dir) else []
    # dictionary where the keys are the dataframes label and the values are the dataframes. There is a dataframe for each TestClasses file
    dataframes = dict()
    for test_type in test_types:
        if test_type == 'human' or  test_type == 'evosuite':
            dataframes[test_type] = pd.DataFrame()
        else:
            for technique in techniques:
                dataframes[f'{test_type}_{technique}'] = pd.DataFrame()

    def resolve_testclasses_dataframe(key, module_name=None):
        if module_name is None:
            token = f"TestClasses_{project}_{key}"
        else:
            token = f"TestClasses_{project}_{module_name}_{key}"

        matching_files = [file for file in files if token in file]
        if not matching_files:
            return None

        try:
            matching_files = sorted(
                matching_files,
                key=lambda file: os.path.getmtime(os.path.join(project_output_dir, file)),
                reverse=True,
            )
        except OSError:
            pass

        selected_file = matching_files[0]
        selected_path = os.path.join(project_output_dir, selected_file)
        if selected_file.endswith(".csv"):
            return pd.read_csv(selected_path)
        if selected_file.endswith(".mavenfailed"):
            return pd.DataFrame()
        if selected_file.endswith(".failed"):
            return None
        return None

    # if the TestClasses file is maven failed or gradle failed (all the test classes failed during the maven execution), then leave the corresponding dataframe empty
    # if the TestClasses file is failed (generic error in AgoneTest.py) or not found, then set the corresponding dataframe to None
    # if the TestClasses file is a csv, then set the dataframe to the content of the csv
    if module is None:
        for key in dataframes.keys():
            dataframes[key] = resolve_testclasses_dataframe(key)
    else:
         for key in dataframes.keys():
            dataframes[key] = resolve_testclasses_dataframe(key, module_name=module)
        

    # Remove from the dictionary the keys associated to a dataframe setted to None
    key_to_remove = set()
    for key in dataframes.keys():
        if dataframes[key] is None:
            key_to_remove.add(key)
    for key in key_to_remove:
        del dataframes[key]

    
    def normalize_target_method(method_value):
        if method_value is None or pd.isna(method_value):
            return None
        normalized_method = str(method_value).strip()
        if not normalized_method or normalized_method == "-" or normalized_method.lower() == "nan":
            return None
        return normalized_method

    def build_target_id(project_row):
        target_id = f"{project}_{project_row['Focal_Class']}"
        focal_method = normalize_target_method(project_row.get('Focal_Method'))
        if focal_method is not None:
            return f"{target_id}::{focal_method}"
        return target_id

    def row_matches_target(result_row, project_row):
        if result_row.get('Focal_Class') != project_row.get('Focal_Class'):
            return False
        target_method = normalize_target_method(project_row.get('Focal_Method'))
        if target_method is None:
            return True
        result_method = normalize_target_method(result_row.get('Focal_Method'))
        if result_method is None:
            return True
        return result_method == target_method

    target_subset = ['Focal_Class']
    if 'Focal_Method' in project_df.columns:
        target_subset.append('Focal_Method')
    target_rows = project_df.drop_duplicates(subset=target_subset)
    data_df_output = []
    df_output = pd.DataFrame(data_df_output, columns=['ID_Focal_Class', 'Cyclomatic_Complexity_Focal_Class', 'Lines_Of_Code_Focal_Class', 'Generator(LLM/EVOSUITE)', 'Prompt_Technique', 'Branch_Coverage', 'Line_Coverage', 'Method_Coverage', 'Compilation', 'Mutation_Coverage', 'Post_Repair_Mutation_Coverage', 'Mutation_Applied', 'NumberOfMethods', 'Assertion Roulette', 'Conditional Test Logic',
        'Constructor Initialization', 'Default Test', 'EmptyTest',
        'Exception Catching Throwing', 'General Fixture', 'Mystery Guest',
        'Print Statement', 'Redundant Assertion', 'Sensitive Equality',
        'Verbose Test', 'Sleepy Test', 'Eager Test', 'Lazy Test',
        'Duplicate Assert', 'Unknown Test', 'IgnoredTest', 'Resource Optimism',
        'Magic Number Test', 'Dependent Test', 'Chance', 'Total_Prompt_Tokens', 'Total_Completion_Tokens', 'Iterations_to_Pass', 'High_Signal', 'Signal_Reason'])

    for _, project_row in target_rows.iterrows():
        focal_class = project_row['Focal_Class']
        target_id = build_target_id(project_row)
        for test_type in test_types: 
            if test_type != "human" and test_type != "evosuite": # in other words, if test_type is an AI model
                for technique in techniques:
                    if (f"{test_type}_{technique}" in dataframes.keys()) == False:
                        continue
                    dataframe = dataframes.get(f'{test_type}_{technique}')
                    if dataframe is not None:
                        find_focal_class = False
                        for index, row in dataframe.iterrows():
                            if row_matches_target(row, project_row):
                                if 'NumberOfMethods' in row.index:
                                    data_df_output = [target_id, row['Cyclomatic_complexity'], row['Lines_of_code'], test_type, technique, row['Branch_coverage'], row['Line_coverage'], row['Method_coverage'], "1", row['Mutation_Coverage'], row['NumberOfMethods'], row['Assertion Roulette'], row['Conditional Test Logic'], row['Constructor Initialization'], row['Default Test'], row['EmptyTest'], row['Exception Catching Throwing'], row['General Fixture'], row['Mystery Guest'], row['Print Statement'], row['Redundant Assertion'], row['Sensitive Equality'], row['Verbose Test'], row['Sleepy Test'], row['Eager Test'], row['Lazy Test'], row['Duplicate Assert'], row['Unknown Test'], row['IgnoredTest'], row['Resource Optimism'], row['Magic Number Test'], row['Dependent Test']]
                                else:
                                    data_df_output = [target_id, row['Cyclomatic_complexity'], row['Lines_of_code'], test_type, technique, row['Branch_coverage'], row['Line_coverage'], row['Method_coverage'], "1", row['Mutation_Coverage']]
                                find_focal_class = True
                                break
                        if find_focal_class == False:
                            data_df_output = [target_id, "-", "-", test_type, technique, "-", "-", "-", "0", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]

                        if len(data_df_output) != len(df_output.columns):
                            missing_length = len(df_output.columns) - len(data_df_output)
                            print(f"Adjusting data_df_output length. Expected {len(df_output.columns)}, got {len(data_df_output)}")
                            data_df_output += ['-'] * missing_length  # Completa con '-' per le colonne mancanti

                        if len(data_df_output) == len(df_output.columns):
                            df_output.loc[len(df_output)] = data_df_output
                            apply_tracking_metrics(len(df_output) - 1, project_row, test_type, technique)
                            apply_mutation_metrics(len(df_output) - 1, project_row)

                        else:
                            print(f"[Error]Error: Mismatched columns for focal class {focal_class}. Expected {len(df_output.columns)}, got {len(data_df_output)}")
                         
            else: # if the test_type is 'human' or 'evosuite'
                if (f"{test_type}" in dataframes.keys()) == False:
                        continue
                dataframe = dataframes[f'{test_type}']
                if dataframe is not None:
                    find_focal_class = False
                    for index, row in dataframe.iterrows():
                        if row_matches_target(row, project_row):
                            if 'NumberOfMethods' in row.index:
                                data_df_output = [target_id, row['Cyclomatic_complexity'], row['Lines_of_code'], test_type, "-", row['Branch_coverage'], row['Line_coverage'], row['Method_coverage'], "1", row['Mutation_Coverage'], row['NumberOfMethods'], row['Assertion Roulette'], row['Conditional Test Logic'], row['Constructor Initialization'], row['Default Test'], row['EmptyTest'], row['Exception Catching Throwing'], row['General Fixture'], row['Mystery Guest'], row['Print Statement'], row['Redundant Assertion'], row['Sensitive Equality'], row['Verbose Test'], row['Sleepy Test'], row['Eager Test'], row['Lazy Test'], row['Duplicate Assert'], row['Unknown Test'], row['IgnoredTest'], row['Resource Optimism'], row['Magic Number Test'], row['Dependent Test']]
                            else:
                                data_df_output = [target_id, row['Cyclomatic_complexity'], row['Lines_of_code'], test_type, "-", row['Branch_coverage'], row['Line_coverage'], row['Method_coverage'], "1", row['Mutation_Coverage']]
                            find_focal_class = True
                            break
                    if find_focal_class == False:
                        data_df_output = [target_id, "-", "-", test_type, "-", "-", "-", "-", "0", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]

                    if len(data_df_output) != len(df_output.columns):
                        missing_length = len(df_output.columns) - len(data_df_output)
                        print(
                            f"Adjusting data_df_output length. Expected {len(df_output.columns)}, got {len(data_df_output)}")
                        data_df_output += ['-'] * missing_length  # Completa con '-' per le colonne mancanti

                    if len(data_df_output) == len(df_output.columns):
                            df_output.loc[len(df_output)] = data_df_output
                            apply_tracking_metrics(len(df_output) - 1, project_row, test_type, "-")
                            apply_mutation_metrics(len(df_output) - 1, project_row)
                    else:
                        print(f"[Error]Error: Mismatched columns for focal class {focal_class}. Expected {len(df_output.columns)}, got {len(data_df_output)}")
                         
    allowed_generator_prompt_pairs = set()
    for current_test_type in test_types:
        if current_test_type in ("human", "evosuite"):
            allowed_generator_prompt_pairs.add((current_test_type, "-"))
        else:
            for current_technique in (techniques or []):
                allowed_generator_prompt_pairs.add((current_test_type, current_technique))

    def normalize_prompt_value(value):
        normalized_value = _normalize_optional_identifier(value)
        if normalized_value is None:
            return "-"
        return normalized_value

    def filter_previous_output_rows(previous_df):
        if previous_df is None or previous_df.empty:
            return previous_df
        required_columns = {"Generator(LLM/EVOSUITE)", "Prompt_Technique"}
        if not required_columns.issubset(previous_df.columns):
            return previous_df
        if not allowed_generator_prompt_pairs:
            return previous_df
        keep_mask = previous_df.apply(
            lambda row: (
                normalize_prompt_value(row.get("Generator(LLM/EVOSUITE)")),
                normalize_prompt_value(row.get("Prompt_Technique")),
            )
            in allowed_generator_prompt_pairs,
            axis=1,
        )
        return previous_df[keep_mask].copy()

    if module is None:
        df_output_path = _worker_project_output_path(project, f"{project}_Output.csv")
    else:
        df_output_path = _worker_project_output_path(project, f"{project}_{module}_Output.csv")

    df_new_execution = pd.DataFrame()
    if os.path.exists(df_output_path):
        try:
            df_previous_execution = pd.read_csv(df_output_path)
            df_previous_execution = filter_previous_output_rows(df_previous_execution)
            if not df_previous_execution.empty and not df_output.empty:
                df_new_execution = pd.concat([df_previous_execution, df_output], ignore_index=True)
            elif df_previous_execution.empty and not df_output.empty:
                df_new_execution = df_output
            else:
                df_new_execution = df_previous_execution
        except Exception as e:
            try:
                df_new_execution = df_output
            except Exception as e:
                print(e)
                return None
    else:
        try:
            df_new_execution = df_output
        except Exception as e:
            print(e)
            return None
    df_new_execution.drop_duplicates(subset={'ID_Focal_Class', 'Generator(LLM/EVOSUITE)', 'Prompt_Technique'}, keep='last', inplace=True)
    df_new_execution.to_csv(df_output_path, index=False, na_rep="-") 
        
    output_csv_path = f"/{project}_Output.csv"

    return df_new_execution, output_csv_path




def write_file(file_path, content):
    """
    Writes the given content in the given file. 
        Parameters:
                    file_path: the path of the file
                    content: the content that needs to be written                
    """
    try:
        _assert_mutable_workspace_path(file_path)
        with open(file_path, 'w') as file:
            file.write(content) 
    except Exception as e:
        print(e)



def write_files(dictionary_for_write):
   """
    Writes the given contents in the given files.
        Parameters:
                    dictionary_for_write: it has file paths as keys and file contents as values
    """
   for file_path, content in dictionary_for_write.items():
    try:
        _assert_mutable_workspace_path(file_path)
        with open(file_path, 'w') as file:
            file.write(content) 
    except Exception as e:
       print(e)



def remove_evosuite_scaffolding_files(test_paths):
    """
    Removes the scaffolding files added by evosuite.       
        Parameters:
                    test_paths (List): list containing all the paths that refere to test classes presumably associated to a scaffolding file
    """
    for test_path in test_paths:
        name_focal_class = ''
        regex_name_focal_class = r'/([^\/]+)\.java'
        match = re.search(regex_name_focal_class, test_path)
        if match:
            name_focal_class = match.group(0)
            name_focal_class = name_focal_class.replace('.java', '').replace('/', '').replace('Test', '').replace('test', '')

        regex_pattern = r'.*/'
        match = re.search(regex_pattern, test_path)
        if match:
            scaffolding_path = f"{match.group(0)}{name_focal_class}_ESTest_scaffolding.java"
            try:
                if os.path.exists(scaffolding_path):
                    os.remove(scaffolding_path)
            except Exception as e:
                print(e)



def remove_dot_evosuite_dir(project, module):
    """
    Removes the .evosuite directory    
        Parameters:
                    project: the ID of the project containing the .evosuite directory
                    module: the project module containing the .evosuite directory
    """
    if module is not None:
        dot_evosuite_to_remove = os.path.join(PATH_CONTEXT.get_compiled_repo_path(project), module, ".evosuite")
        try:
            if os.path.exists(dot_evosuite_to_remove):
                shutil.rmtree(dot_evosuite_to_remove)
        except Exception as e:
            print(e)


def find_module_class(project, class_path):
    """
    Searches the module of the given focal or test class. 
        Parameters:
                    project: the ID of the project
                    class_path: the path of the focal or test class
        Returns:
                    module: the module of the given focal of test class, 'None' if an error occurred

    """
    # Search the current module
    module = None
    regex = rf"{project}/(.*?)/"
    match = re.search(regex, class_path)
    if match:
        module = match.group(0)
        module = module.replace('/', '').replace(f'{project}', '')
        return  module
    else:
        return None
    

def remove_directory_evosuite_command_line():
    """
    Removes the directory generated by evosuite jar file  
    """
    evosuite_tests_path = 'evosuite-tests'
    evosuite_report_path = 'evosuite-report'
    if os.path.exists(evosuite_tests_path):
        shutil.rmtree(evosuite_tests_path)
    if os.path.exists(evosuite_report_path):
        shutil.rmtree(evosuite_report_path)


def check_version(version):
    """
    Check if the given version matches the correct pattern for versions (e.g., 1.2.3, 1.2, or 12.3.1).
        Parameters:
                version: the version to check
        Returns:
                :True if the given version matches the correct pattern for versions, False otherwise."""
    pattern = r'^(\d{1,3})(\.(\d{1,3}))*$'
    if version is None:
        return False
    if re.match(pattern, version):
        return True
    else:
        return False
    

def find_package(class_path):
    """
    Search the package of the given class.
        Parameters:
                class_path: the path of the focal or test class.
        Returns:
                :the package as a string, 'None' if the package was not found
    """
    with open(class_path, 'r') as class_file_read:
        class_content = class_file_read.read()
    cleaned_content = re.sub(r'//.*', '', class_content) # Remove the single-line comments
    cleaned_content = re.sub(r'/\*.*?\*/', '', cleaned_content, flags=re.DOTALL)  # Remove the multi-lines comments
    match = re.search(r'package\s+([\w.]+);', cleaned_content)
    if match:
        return match.group(1)
    else:
        return None
    


def find_max_value(values):
    """
    Search for the maximum value in the given list or set.
    
    Parameters:
            values (list or set): the list or set of values in which to search for the maximum.
        
    Returns:
            :the maximum value found, or 'None' if the given list/set is empty.
    """
    if len(values) == 0:
        return None
    if isinstance(values, set):
        values = list(values)
    max_value = values[0]
    for value in values:
        if value>max_value:
            max_value = value
    return max_value
            
    

def find_min_value(values):
    """
    Search for the minimum value in the given list or set.
    
    Parameters:
            values (list or set): the list or set of values in which to search for the minimum.
        
    Returns:
            :the minimum value found, or 'None' if the given list/set is empty.
    """
    if len(values) == 0:
        return None  
    if isinstance(values, set):
        values = list(values)
    min_value = values[0]
    for value in values:
        if value < min_value:
            min_value = value
    return min_value



def is_admin(system):
    """
    Check if the script is running with or without the administrator privileges.
    Parameters:
            system (String): the current OS (Windows, Linux, etc..)   
    Returns:
            :True if the script is running with the administrator privileges, False otherwise.
    """
    if system == 'Windows':
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except:
            return False
    else:
        try:
            if os.geteuid() == 0:
                return True
            else:
                return False
        except:
            return False


