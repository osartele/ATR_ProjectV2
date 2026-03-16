import argparse
import os
import shutil
import tarfile
import json
import time
import pandas as pd
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
import re
import sys

from dotenv import load_dotenv
import utils
import mavenLib
import gradleLib
import project_structure_analyzer as psa
import project_dependencies_analyzer as pda

load_dotenv()

IMMUTABLE_SOURCE_ROOTS = {"repos", "Classes2Test"}


def _detect_system():
    if os.name == "nt":
        return "Windows"
    if sys.platform == "darwin":
        return "Darwin"
    return "Linux"


def _copy_project_snapshot(source_path, destination_path):
    if not os.path.exists(source_path):
        return False
    if os.path.exists(destination_path) or os.path.islink(destination_path):
        shutil.rmtree(destination_path, ignore_errors=True)
        time.sleep(1)
    shutil.copytree(
        source_path,
        destination_path,
        ignore=shutil.ignore_patterns(".git", "target", "build", ".gradle"),
        copy_function=shutil.copy2,
    )
    return True


def _snapshot_repo_to_compiled(project_name):
    return _copy_project_snapshot(f"repos/{project_name}", f"compiledrepos/{project_name}")


def _snapshot_repo_to_failed(project_name):
    return _copy_project_snapshot(f"repos/{project_name}", f"failedrepos/{project_name}")


def _snapshot_compiled_to_failed(project_name):
    if _copy_project_snapshot(f"compiledrepos/{project_name}", f"failedrepos/{project_name}"):
        shutil.rmtree(f"compiledrepos/{project_name}", ignore_errors=True)
        time.sleep(1)
        return True
    return False


def _default_smoke_test_json():
    return Path(__file__).resolve().parent.parent / "Classes2Test" / "42949039_429.json"


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

        source_entries = [
            entry.name
            for entry in source_path.iterdir()
            if entry.name not in {".git", "target", "build", ".gradle"}
        ]
        if not source_entries:
            return compiled_path.exists()

        for entry_name in source_entries[:5]:
            if not (compiled_path / entry_name).exists():
                return False
        return True

    if _is_materialized(source_repo_path, compiled_repo_path):
        return "existing"
    if compiled_repo_path.exists() or compiled_repo_path.is_symlink():
        shutil.rmtree(compiled_repo_path, ignore_errors=True)
        time.sleep(1)
    compiled_repo_path.parent.mkdir(parents=True, exist_ok=True)

    shutil.copytree(
        source_repo_path,
        compiled_repo_path,
        ignore=shutil.ignore_patterns(".git", "target", "build", ".gradle"),
        copy_function=shutil.copyfile,
    )
    return "copy"


def _build_smoke_project_info(project_id, project_root, module_relative_path, project_type):
    project_info = {}
    module_root = project_root / module_relative_path if module_relative_path else project_root

    if project_type == "Maven":
        root_compiler_version = mavenLib.extract_maven_version(str(project_root)) if (project_root / "pom.xml").is_file() else None
        root_java_version, root_junit_version, root_testng_version = mavenLib.extract_test_and_java_version_maven(str(project_root)) if (project_root / "pom.xml").is_file() else (None, None, None)
        module_compiler_version = mavenLib.extract_maven_version(str(module_root)) if (module_root / "pom.xml").is_file() else root_compiler_version
        module_java_version, module_junit_version, module_testng_version = mavenLib.extract_test_and_java_version_maven(str(module_root)) if (module_root / "pom.xml").is_file() else (root_java_version, root_junit_version, root_testng_version)
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
            module_java_version, module_junit_version, module_testng_version = root_java_version, root_junit_version, root_testng_version
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

    project_info = set_project_info(root_java_version, root_testng_version, root_junit_version, str(project_id), root_compiler_version, project_type, project_info)

    if module_relative_path:
        if module_java_version is None:
            module_java_version = root_java_version
        if module_junit_version is None:
            module_junit_version = root_junit_version
        if module_testng_version is None:
            module_testng_version = root_testng_version
        if module_compiler_version is None:
            module_compiler_version = root_compiler_version
        project_info = set_project_info(module_java_version, module_testng_version, module_junit_version, f"{project_id}_{module_relative_path}", module_compiler_version, project_type, project_info)
        project_info = add_module_to_list_project_info(str(project_id), module_relative_path, project_info)

    return project_info


def prepare_smoke_test_environment(target_json_path=None, refresh_compiled_repo=True):
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
    focal_method = record.get("focal_method", {}).get("identifier", "-")
    test_case = record.get("test_case", {}).get("identifier", test_class)

    if not all([project_id, focal_class, test_class, focal_file, test_file]):
        raise ValueError(f"Incomplete smoke test record in {target_json}")

    agone_root = Path(__file__).resolve().parent
    repo_root = (agone_root.parent / "repos" / project_id).resolve()
    if not repo_root.exists():
        raise FileNotFoundError(f"Repository for smoke test not found: {repo_root}")

    build_root, project_type = _find_nearest_build_directory(repo_root, focal_file)
    if project_type is None:
        raise ValueError(f"Unable to detect Maven/Gradle project type for smoke target: {target_json}")
    module_relative_path = os.path.relpath(build_root, repo_root)
    if module_relative_path == ".":
        module_relative_path = None

    output_root = agone_root.parent / "output"
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
            }
        ]
    )
    classes_df.to_csv(output_root / "classes.csv", index=False)

    if refresh_compiled_repo:
        compiled_repo_root = agone_root.parent / "compiledrepos" / project_id
        failed_repo_root = agone_root.parent / "failedrepos" / project_id
        if failed_repo_root.exists():
            shutil.rmtree(failed_repo_root, ignore_errors=True)
        repo_materialization = _materialize_smoke_repo(repo_root, compiled_repo_root)
    else:
        repo_materialization = "existing"

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
        "target_json": str(target_json),
        "target_name": f"{test_class}::{test_case} -> {focal_class}.{focal_method}",
        "repo_materialization": repo_materialization,
    }

    with open(output_root / project_id / "smoke_target.json", "w", encoding="utf-8") as smoke_target_file:
        json.dump(smoke_target, smoke_target_file, indent=2)

    return smoke_target


def extract_files():
    """
    Extracts files from the dataset 'methods2test' and puts them all in a new directory called 'source' 
    """
    print("\n extracting files from the dataset...")
    # Loop through all files in the dataset directory
    for file in os.listdir('methods2test/dataset'):
        # If the file is a tar.bz2 file
        if file.endswith('eval.tar.bz2'):  # TODO: Change to '.tar.bz2' for the full dataset
            # Open the tar file
            tar = tarfile.open('methods2test/dataset/' + file, 'r:bz2')
            # Extract all files from the tar file to the source directory
            tar.extractall('source')
            print('Extracted ' + file + ' in source')
            # Close the tar file
            tar.close()

    if system=="Windows":
        # Move all files from subdirectories to the source directory
        for root, dirs, files in os.walk('.\\source'):
            if root.endswith('eval') or root.endswith('train') or root.endswith('test'):
                for dir in dirs:
                    shutil.move(os.path.join(root, dir), '.\\source')
        # Remove unnecessary directories
        shutil.rmtree('.\\source\\eval', ignore_errors=True)  
        shutil.rmtree('.\\source\\test', ignore_errors=True) 
        shutil.rmtree('.\\source\\train', ignore_errors=True)

    # Linux or Darwin system
    else:  
        # Move all files from subdirectories to the source directory
        os.system('mv source/eval/* source')
        os.system('mv source/test/* source')
        os.system('mv source/train/* source')

        # Remove unnecessary directories
        os.system('rm -r source/eval')
        os.system('rm -r source/test')
        os.system('rm -r source/train')
        print("\n extraction completed!")
    print("\n extraction completed!")





def get_classes_from_json(start_project, end_project, specific_folder):
    """
    Creates a dataframe containing, for each project, the names and paths of the focal classes and the associated test classes 
        Parameters:
                start_project: the ordinal number of the project from which the function starts 
                end_project: the ordinal number of the project at which the function ends
                : For example, if start_project is 0 and end_project is 5, then the projects 0(first project),1,2,3,4 will be processed
                specific folder: if the user wishes to process only one specific folder, then the specific_folder variable must set to the project ID. If the user does not wish to process only one specific folder, the specific_folder variable must be set to None
         Returns:
                df (Dataframe): the dataframe containing, for each project, the names and paths of the focal classes and the associated test classes
                repo_urls (Set): Set that contains the names and the corriesponding GitHub urls of all the projects
    """
    if ((start_project is not None and end_project is not None and specific_folder is None) or (start_project is None and end_project is None and specific_folder is not None)) == False:
        print("An error occured in get_classes_from_json!")
        system.exit(1)
    print("\nStart get_classes_from_json()\n")
    source_dir = 'source'
    data = []
    # Set to store unique repository URLs
    repo_urls = set()
    num_folder_processed = 0
    num_folder_iteration = 0
    if specific_folder is not None:
        num_folder_to_be_processed = 1
    elif start_project is not None and end_project is not None:
        num_folder_to_be_processed = end_project - start_project
    # Loop through all folders in the source directory
    for folder in os.listdir(source_dir): 
        if specific_folder is not None:
            if folder != specific_folder:
                continue
        elif start_project is not None and end_project is not None:
            if num_folder_iteration<start_project:
                num_folder_iteration = num_folder_iteration + 1
                continue
            if num_folder_iteration==end_project:
                num_folder_iteration  = num_folder_iteration + 1
                break
            num_folder_iteration  = num_folder_iteration + 1
        if utils.verify_if_folder_has_already_been_processed(folder):
           print(f"{folder} has already been processed")
           num_folder_to_be_processed = num_folder_to_be_processed - 1
           continue
        num_folder_processed = num_folder_processed + 1
        print("Completion percentage", (num_folder_processed/num_folder_to_be_processed)*100, "%")
        print(f'In folder {folder}...')
        folder_path = os.path.join(source_dir, folder)
        # If the folder is a directory
        if os.path.isdir(folder_path):
            # Set to store seen class pairs
            seen = set()
            # Loop through all files in the folder
            for file in os.listdir(folder_path):
                # If the file is a JSON file
                if file.endswith('.json'):
                    file_path = os.path.join(folder_path, file)
                    # Open the JSON file
                    with open(file_path, 'r') as json_file:
                        # Load the JSON data
                        json_data = json.load(json_file)
                        # Get the focal class, test class, repository URL, focal file, and test file
                        focal_class = json_data.get('focal_class', {}).get('identifier')
                        test_class = json_data.get('test_class', {}).get('identifier')
                        focal_method = json_data.get('focal_method', {}).get('identifier', '-')
                        test_case = json_data.get('test_case', {}).get('identifier', '-')
                        repo_url = json_data.get('repository', {}).get('url')
                        focal_file = json_data.get('focal_class', {}).get('file')
                        test_file = json_data.get('test_class', {}).get('file')
                        # If all necessary data is present
                        if focal_class and test_class and repo_url and focal_file and test_file:
                            # Create a pair of the focal class, focal method, test class, and test case
                            pair = (focal_class, focal_method, test_class, test_case)
                            # If the pair has not been seen before
                            if pair not in seen:
                                # Add the pair to the seen set
                                seen.add(pair)
                                # Create the paths to the focal and test files
                                focal_path = "repos/" + folder + "/" + focal_file
                                test_path = "repos/" + folder + "/" + test_file
                                # Add the data to the data list
                                data.append([folder, focal_class, test_class, focal_path, test_path, None, focal_method, test_case])
                                # Add the repository URL and folder to the repository URLs set
                                repo_urls.add((repo_url, folder))
    # Create a DataFrame from the data
    df = pd.DataFrame(data, columns=['Project', 'Focal_Class', 'Test_Class', 'Focal_Path', 'Test_Path', 'Module', 'Focal_Method', 'Test_Case'])
    # Sort the DataFrame by the project
    df = df.sort_values(by='Project')
    # Convert the repository URLs set to a list and sort it by the folder
    repo_urls = sorted(list(repo_urls), key=lambda x: x[1])
    return df, repo_urls

def convert_to_https(url):
    """
    Converts a git repository URL to HTTPS if it is not already in HTTPS format.

    Parameters:
        url (str): The original git repository URL.

    Returns:
        str: The HTTPS version of the git repository URL.
    """
    if url.startswith('git@'):
        url = url.replace(':', '/')
        url = url.replace('git@', 'https://')
    elif url.startswith('http://'):
        url = url.replace('http://', 'https://')
    return url

def clone_repos(repo_urls):
    """
    Clones all the repositories in a directory called 'repos'
        Parameters:
                    repo_urls (Set): set that contains the IDs and the corresponding GitHub urls of all the projects
        Returns:
                    failed_clones (Set): set containing all the projects that failed during the cloning process. Each item of the set consists of two values: the GitHub url and the corresponding project ID.
    """
    print("\nStart clone_repos()\n")
    git_username = os.getenv('GIT_USERNAME', 'default_username')
    git_email = os.getenv('GIT_EMAIL', 'default_email@example.com')
    # Set Git credentials
    subprocess.run(['git', 'config', '--global', 'user.name', git_username])
    subprocess.run(['git', 'config', '--global', 'user.email', git_email])
    #subprocess.run(['git', 'config', '--global', 'http.https://github.com.proxy', 'socks5h://127.0.0.1:1080'])
    subprocess.run(['git', 'config', '--global', 'http.postbuffer', '524288000'])
    subprocess.run(['git', 'config', '--global', 'credential.helper', '"cache --timeout=86400"'])


    # Create the repositories directory if it does not exist
    os.makedirs('repos', exist_ok=True)
    # List to store repositories that failed to clone
    failed_clones = []
    # Get the total number of repositories
    total_repos = len(repo_urls)
    # Counter for cloned repositories
    cloned_repos = 0
    # Loop through all repository URLs
    for url, folder in repo_urls:
        print("-----------------------------------------------------------------------------------------------------------------------------")
        print(f'Project {folder}...')
        repo_path = f'repos/{folder}'
        # If the repository has already been cloned
        if os.path.exists(repo_path):
            cloned_repos += 1
            print(f'Repository {folder} already cloned.')
            continue
        # Convert URL to HTTPS
        url = convert_to_https(url)
        try:
            cloned_repos += 1
            # Try to clone only last commit of the repository
            subprocess.check_call(['gh', 'repo', 'clone', url, repo_path, '--','--depth=1', '--single-branch', '--verbose'])
            result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo_path, capture_output=True, text=True, timeout=900)
            if result.stdout.__contains__("not a git repository"):
                failed_clones.append((url, folder))
                print(f'Failed to clone repository {folder}.')
            print(f'Cloned {cloned_repos}/{total_repos} repositories ({(cloned_repos / total_repos) * 100:.2f}%)')
        except subprocess.CalledProcessError:
            # If cloning fails, add the URL and folder to the failed clones list
            failed_clones.append((url, folder))
            print(f'Failed to clone repository {folder}.')
    return failed_clones


def remove_failed_clones(df, failed_clones):
    """
    Removes, from the given dataframe, the projects that failed during the cloning process.
        Parameters:
                    df (Dataframe): the dataframe containing, for each project, the names and paths of the focal classes and the associated test classes
                    failed_clones (List): a list containing all the projects that failed during the cloning process. Each item of the list consists of two values: the GitHub url and the corresponding project ID.
        Returns:
                    df (Dataframe): the new dataframe without the projects that failed during the cloning process.
    """
    # Extract the project IDs from the failed clones
    failed_ids = [clone[1] for clone in failed_clones]
    # Filter the DataFrame to exclude rows with project IDs in the failed clones
    df = df[~df['Project'].isin(failed_ids)]
    return df



def create_json(df, repo_urls):
    """
    Creates the 'output' directory. Each JSON file in this directory corresponds to a focal class or test class present in the given dataframe and the corresponding cloned repository. 
    Each JSON file contains information about the project (e.g. project ID, commit hash, java version, maven version, junit version,....) and the specific class (identifier, content and relative path).     
        Parameters:
                    df (Dataframe): the dataframe containing, for each project, the names and paths of the focal classes and the associated test classes
                    repo_urls (Set): set that contains the GitHub urls of all the projects
        Returns:
                    missing_files (List): list containing all the files present in the given dataframe but missing in the corresponding cloned repository. Each item in the list consists of two values: the project ID and the path of the missing file.
    """
    # Create the output directory if it does not exist
    os.makedirs('output', exist_ok=True)
    # List to store missing files
    missing_files = []
    # Contains all the projects that are processed and are in the output directory
    projects_in_output = set()
    projects_in_dataframe = df['Project'].unique()
    # Loop through all rows in the DataFrame
    for index, row in df.iterrows():
        # Read the content of the focal and test classes
        focal_content = utils.read_class_content(row['Focal_Path'])
        test_content = utils.read_class_content(row['Test_Path'])
        # If the focal content is missing
        if focal_content is None:
            # Add the project and focal path to the missing files list
            missing_files.append((row['Project'], row['Focal_Path']))
        # If the test content is missing
        if test_content is None:
            # Add the project and test path to the missing files list
            missing_files.append((row['Project'], row['Test_Path']))
        # If both the focal and test content are present
        if focal_content is not None and test_content is not None:
            # Create a new JSON object
            new_json = {
                'project_id': row['Project'],
                'repository': {
                    'url': [repo_url for repo_url, folder in repo_urls if folder == row['Project']][0],
                    'commit_hash': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=f'repos/{row["Project"]}').decode('utf-8').strip(),
                    'java_version': None,
                    'maven_version': None,
                    'testng_version': None,
                    'junit_version': None,
                    'gradle_version': None,
                },

                'focal_class': {
                    'identifier': row['Focal_Class'],
                    'file_content': focal_content,
                    'relative_path': row['Focal_Path'].replace(f'repos/{row["Project"]}/', '')
                },
                'test_class': {
                    'identifier': row['Test_Class'],
                    'file_content': test_content,
                    'relative_path': row['Test_Path'].replace(f'repos/{row["Project"]}/', '')
                }
            }
            projects_in_output.add(row['Project'])
            # Create the project directory in the output directory if it does not exist
            os.makedirs(f'output/{row["Project"]}', exist_ok=True)
            # Open the JSON file
            with open(f'output/{row["Project"]}/{index}.json', 'w') as json_file:
                # Dump the JSON object to the file
                json.dump(new_json, json_file)


    for project_in_dataframe in projects_in_dataframe:
        if project_in_dataframe not in projects_in_output:
            if os.path.exists(f'repos/{project_in_dataframe}'):
                print(f"[INFO] All the methods2test files for the project {project_in_dataframe} are not present in the corresponding repository. Project has been moved in failedrepos.")
                try:
                    _snapshot_repo_to_failed(project_in_dataframe)
                except Exception as e:
                    print(e)
                                    
    return missing_files



def check_project_types(df):
    """
    Searches for the Java version, Maven/Gradle version and JUnit/TestNG version for each project. Executes each project and, if the execution is successful, moves the project to the 'compiledrepos' directory, otherwise, moves it to the 'failedrepos' directory.
    This function also generates the project_info.json file. 
        Parameters:
                df: the Dataframe containing the projects.
        Returns:
                project_info (Dictionary): dictionary where the keys represent the project IDs and the values are JSON-formatted descriptions of the project versions (Java, Maven/Gradle and JUnit/TestNG)           
    """
    print("\nStart check_project_types()\n")
    # Initialize project_failed flag
    project_failed = False
    # create failedrepos directory
    os.makedirs('failedrepos', exist_ok=True)
    # create compiledrepos directory
    os.makedirs('compiledrepos', exist_ok=True)
    java_directory = os.getenv('JAVA_DIRECTORY')
    print("javahome set:" + java_directory)
    current_project = ""
    project_info = {}
    if os.path.exists('output/project_info.json'):
        with open('output/project_info.json', 'r') as json_file:
            project_info = json.load(json_file)
    # Extract unique project names from the DataFrame
    unique_projects = df['Project'].unique()
    unique_projects = [str(project) for project in unique_projects.tolist()]

    # Iterate over each row in the DataFrame
    for project_name in unique_projects:
        project_path = f'repos/{project_name}'
        try:
            # Check if the project already exists in compiledrepos
            if os.path.exists(f'compiledrepos/{project_name}'):
                print(f"Project {project_name} already exists in compiledrepos.")
                continue
            # Check if the project already exists in failedrepos
            if os.path.exists(f'failedrepos/{project_name}'):
                print(f"Project {project_name} already exists in failedrepos.")
                continue

            if current_project == project_name and project_failed:
                continue
            
            current_project = project_name
            project_failed = False

            # Check if the project exists in .repos
            if not os.path.exists(f'repos/{project_name}'):
                print(f"Project {project_name} does not exist in repos.")
                continue

            # Run the tests for the project
            try:
                # If it's a Maven project
                if os.path.exists(f'repos/{project_name}/pom.xml'):
                    java_version = None 
                    compiler_version = None # Maven version
                    junit_version = None
                    testng_version = None

                    # Extract maven version
                    print("Extracting maven version...")
                    compiler_version = mavenLib.extract_maven_version(project_path)
                   
                    # Extract java, junit and testng version
                    print("Extracting java, junit and testng version...")
                    java_version, junit_version, testng_version = mavenLib.extract_test_and_java_version_maven(project_path)

                    print("Setting java_home...")
                    utils.set_java_home(java_directory, java_version, system)
                    # Check that is all correct
                    print("-----------------------------------------------------------------------------------------------------------------------------")
                    print("Project: ", project_name)
                    subprocess.check_call(['java', '-version'])
                    print("The Java version detected by the script.: ", java_version)
                    print("The Junit version detected by the script: ", junit_version)
                    print("The testNG version detected by the script: ", testng_version)
                    print("The Compiler version (Maven or Gradle) detected by the script: ", compiler_version)

                    project_info=set_project_info(java_version, testng_version, junit_version, project_name, compiler_version, "Maven", project_info)                        
                    # in all the output files of the current project, which are json files, I put the java_version, maven_version, testng_version and junit_version
                    for json_file in os.listdir(f'output/{project_name}'):
                        if json_file.endswith('.json'):
                            with open(f'output/{project_name}/{json_file}', 'r+') as file:
                                data = json.load(file)
                                if 'repository' in data:
                                        if java_version is not None:
                                            data['repository']['java_version'] = java_version
                                        if compiler_version is not None:
                                            data['repository']['maven_version'] = compiler_version
                                        if testng_version is not None:
                                            data['repository']['testng_version'] = testng_version
                                        if junit_version is not None:
                                            data['repository']['junit_version'] = junit_version
                                file.seek(0)
                                json.dump(data, file)
                                file.truncate()
                    if java_version is None or (testng_version is None and junit_version is None):
                            print(f"[INFO] Lack of information about java and junit/testng version for the project {project_name}")
                            print("\nAnalyzing the modules...")
                            at_least_one = False # False if all the modules all have lack of informazion, True otherwise.
                            modules = mavenLib.search_modules_pom(project_path, df, project_name)
                            for module in modules:
                                module_path = f'{project_path}/{module}'
                                # Extract maven version
                                compiler_version_module = mavenLib.extract_maven_version(module_path)
                                # Extract java, junit and testng version
                                java_version_module, junit_version_module, testng_version_module = mavenLib.extract_test_and_java_version_maven(module_path)
                                if java_version_module is None and java_version is not None:
                                    java_version_module = java_version
                                if junit_version_module is None and testng_version_module is None:
                                    if junit_version is not None:
                                        junit_version_module = junit_version
                                    if testng_version is not None:
                                        testng_version_module = testng_version
                                if compiler_version_module is None and compiler_version is not None:
                                    compiler_version_module = compiler_version
                                utils.set_java_home(java_directory, java_version_module, system)
                                # Check that is all correct
                                print("-----------------------------------------------------------------------------------------------------------------------------")
                                print(f"Project: {project_name}, Module: {module}")
                                subprocess.check_call(['java', '-version'])
                                print("The Java version detected by the script.: ", java_version_module)
                                print("The Junit version detected by the script: ", junit_version_module)
                                print("The testNG version detected by the script: ", testng_version_module)
                                print("The Compiler version Maven detected by the script: ", compiler_version_module)

                                if java_version_module is None or (testng_version_module is None and junit_version_module is None):
                                    print(f"[INFO] Lack of information about java and junit/testng version for the project {project_name}, module {module}")
                                else:
                                    try:
                                        if system=="Windows":
                                            subprocess.check_call(['mvn.cmd', '-Drat.skip=true', '-DfailIfNoTests=false', 'clean', 'verify'], cwd=module_path,
                                                timeout=900)
                                        else:
                                            subprocess.check_call(['mvn', '-Drat.skip=true', '-DfailIfNoTests=false', 'clean', 'verify'], cwd=module_path,
                                                timeout=900)
                                    except subprocess.CalledProcessError:
                                        print(f"[INFO] Tests failed for project: {project_name}., module: {module}")
                                        continue # Switch to next module
                                    project_info=set_project_info(java_version_module, testng_version_module, junit_version_module, f'{project_name}_{module}', compiler_version, "Maven", project_info)     
                                    project_info=add_module_to_list_project_info(project_name, module, project_info)     
                                    at_least_one = True
                                    for json_file in os.listdir(f'output/{project_name}'):
                                        if json_file.endswith('.json'):
                                            with open(f'output/{project_name}/{json_file}', 'r+') as file:
                                                data = json.load(file)
                                                if 'repository' in data:
                                                        if 'focal_class' in data:
                                                            if data['focal_class']['relative_path'].startswith(f'{module}/'):
                                                                data['repository']['java_version'] = java_version_module
                                                                data['repository']['maven_version'] = compiler_version_module
                                                                data['repository']['testng_version'] = testng_version_module
                                                                data['repository']['junit_version'] = junit_version_module
                                                file.seek(0)
                                                json.dump(data, file)
                                                file.truncate()
                                    df = pd.read_csv('./output/classes.csv')
                                    for index, row in df.iterrows(): 
                                        if row['Focal_Path'].startswith(f'repos/{project_name}/{module}/'):
                                            df.at[index, 'Module'] = module
                                    df.to_csv('./output/classes.csv', index=False)
                            if at_least_one == False:
                                if os.path.exists(f'compiledrepos/{project_name}'):
                                    print(f"[INFO] Project has been removed from compiledrepos and has been moved in failedrepos.")
                                    try:
                                        _snapshot_compiled_to_failed(project_name)
                                    except Exception as e:
                                        print(e)
                                else:
                                    if os.path.exists(f'repos/{project_name}'):
                                        print(f"[INFO]  Project has been moved in failedrepos.")
                                        try:
                                            _snapshot_repo_to_failed(project_name)
                                        except Exception as e:
                                            print(e)

                                continue # Switch to next project
                            else: 
                                print(f"[INFO] Tests passed for {project_name}. Project has been moved to compiledrepos.")
                                try:
                                    _snapshot_repo_to_compiled(project_name)
                                except Exception as e:
                                    print(e)
                                continue # Switch to next project
                    
                    # Run tests (Maven projects)
                    try:
                        if system=="Windows":
                            subprocess.check_call(['mvn.cmd', '-Drat.skip=true', '-DfailIfNoTests=false', 'clean', 'verify'], cwd=f'repos/{project_name}',
                                                timeout=900)
                        else:
                            subprocess.check_call(['mvn', '-Drat.skip=true', '-DfailIfNoTests=false', 'clean', 'verify'], cwd=f'repos/{project_name}',
                                                timeout=900)
                    except subprocess.CalledProcessError:
                        project_failed = True
                        # If tests fail, remove the project from compiledrepos if it exists
                        if os.path.exists(f'compiledrepos/{project_name}'):
                            print(f"[INFO] Tests failed for {project_name}. Project has been removed from compiledrepos and has been moved in failedrepos.")
                            try:
                                _snapshot_compiled_to_failed(project_name)
                            except Exception as e:
                                print(e)
                        else:
                            print(f"[INFO] Tests failed for {project_name}. Project was not in compiledrepos. Project has been moved in failedrepos")
                            if os.path.exists(f'repos/{project_name}'):
                                try:
                                    _snapshot_repo_to_failed(project_name)
                                except Exception as e:
                                    print(e)
                    else:

                        # If tests pass, move the project to compiledrepos
                        print(f"[INFO] Tests passed for {project_name}. Project has been moved to compiledrepos.")
                        try:
                            _snapshot_repo_to_compiled(project_name)
                        except Exception as e:
                            print(e)
                    



                # If it's a Gradle project
                elif os.path.exists(f'repos/{project_name}/build.gradle') or os.path.exists(f'repos/{project_name}/build.gradle.kts'):
                    compiler_version = None # Gradle version
                    java_version = None
                    junit_version = None
                    testng_version = None
                    gradle_directory = r"/Gradle"


                    # Extract Gradle version from gradle-wrapper
                    compiler_version = gradleLib.extract_gradle_version_from_gradle_wrapper(project_name)

                    # Extract info from build.gradle file: junit/testNG version, java version and Gradle version
                    if compiler_version is None:
                        java_version, junit_version, testng_version, compiler_version = gradleLib.extract_info_build_gradle(project_path, True)
                    else:
                        java_version, junit_version, testng_version = gradleLib.extract_info_build_gradle(project_path, False)


                    if compiler_version is None:
                        compiler_version = gradleLib.extract_gradle_version_from_gradle_properties(project_name)

                    utils.set_java_home(java_directory, java_version, system)
                    utils.set_gradle_variable(gradle_directory, '8', system)
                    # Check that is all correct
                    print("-----------------------------------------------------------------------------------------------------------------------------")
                    print("Project: ", project_name)
                    subprocess.check_call(['java', '-version'])
                    print("The Java version detected by the script.: ", java_version)
                    print("The Junit version detected by the script: ", junit_version)
                    print("The testNG version detected by the script: ", testng_version)
                    print("The Gradle version detected by the script: ", compiler_version)


                    project_info=set_project_info(java_version, testng_version, junit_version, project_name, compiler_version, "Gradle", project_info)
                    # in all the output files of the current project, which are json files, I put the java_version, gradle_version, testng_version and junit_version
                    for json_file in os.listdir(f'output/{project_name}'):
                        if json_file.endswith('.json'):
                            with open(f'output/{project_name}/{json_file}', 'r+') as file:
                                data = json.load(file)
                                if 'repository' in data:
                                        if java_version is not None:
                                            data['repository']['java_version'] = java_version
                                        if compiler_version is not None:
                                            data['repository']['gradle_version'] = compiler_version
                                        if testng_version is not None:
                                            data['repository']['testng_version'] = testng_version
                                        if junit_version is not None:
                                            data['repository']['junit_version'] = junit_version
                                file.seek(0)
                                json.dump(data, file)
                                file.truncate()


                    if java_version is None or (testng_version is None and junit_version is None):
                            print(f"[INFO] Lack of information about java and junit/testng version for the project {project_name}")
                            print("\nAnalyzing the modules...")
                            at_least_one = False # False if all the modules all have lack of information, True otherwise.
                            modules = gradleLib.search_modules_build_gradle(project_path, df, project_name)
                            for module in modules:
                                module_path = f'{project_path}/{module}'
                                # Extract info from build.gradle file: junit/testNG version, java version and Gradle version
                                java_version_module, junit_version_module, testng_version_module, compiler_version_module = gradleLib.extract_info_build_gradle(module_path, True)
                                if java_version_module is None and java_version is not None:
                                    java_version_module = java_version
                                if junit_version_module is None and testng_version_module is None:
                                    if junit_version is not None:
                                        junit_version_module = junit_version
                                    if testng_version is not None:
                                        testng_version_module = testng_version
                                if compiler_version_module is None and compiler_version is not None:
                                    compiler_version_module = compiler_version
                                utils.set_java_home(java_directory, java_version, system)
                                utils.set_gradle_variable(gradle_directory, '8', system)
                                # Check that is all correct
                                print("-----------------------------------------------------------------------------------------------------------------------------")
                                print(f"Project: {project_name}, Module: {module}")
                                subprocess.check_call(['java', '-version'])
                                print("The Java version detected by the script.: ", java_version_module)
                                print("The Junit version detected by the script: ", junit_version_module)
                                print("The testNG version detected by the script: ", testng_version_module)
                                print("The Gradle version detected by the script: ", compiler_version_module)

                                if java_version_module is None or (testng_version_module is None and junit_version_module is None):
                                    print(f"[INFO] Lack of information about java and junit/testng version for the project {project_name}, module {module}")
                                else:
                                    try:
                                        if system=="Windows":
                                            subprocess.check_call(['gradle.bat', 'build'], cwd=f'repos/{project_name}', timeout=900)
                                        else:
                                            subprocess.check_call(['gradle', 'build'], cwd=f'repos/{project_name}', timeout=900)
                                    except subprocess.CalledProcessError:
                                        print(f"[INFO] Tests failed for project: {project_name}., module: {module}")
                                        continue # Switch to next module
                                    project_info=set_project_info(java_version_module, testng_version_module, junit_version_module, f'{project_name}_{module}', compiler_version, "Maven", project_info)     
                                    project_info=add_module_to_list_project_info(project_name, module, project_info)     
                                    at_least_one = True
                                    for json_file in os.listdir(f'output/{project_name}'):
                                        if json_file.endswith('.json'):
                                            with open(f'output/{project_name}/{json_file}', 'r+') as file:
                                                data = json.load(file)
                                                if 'repository' in data:
                                                        if 'focal_class' in data:
                                                            if data['focal_class']['relative_path'].startswith(module):
                                                                data['repository']['java_version'] = java_version_module
                                                                data['repository']['maven_version'] = compiler_version_module
                                                                data['repository']['testng_version'] = testng_version_module
                                                                data['repository']['junit_version'] = junit_version_module
                                                file.seek(0)
                                                json.dump(data, file)
                                                file.truncate()
                                    df = pd.read_csv('./output/classes.csv')
                                    for index, row in df.iterrows(): 
                                        if row['Focal_Path'].startswith(f'repos/{project_name}/{module}'):
                                            df.at[index, 'Module'] = module
                                    df.to_csv('./output/classes.csv', index=False)
                            if at_least_one == False:
                                if os.path.exists(f'compiledrepos/{project_name}'):
                                    print(f"[INFO] Project has been removed from compiledrepos and has been moved in failedrepos.")
                                    try:
                                        _snapshot_compiled_to_failed(project_name)
                                    except Exception as e:
                                        print(e)
                                else:
                                    if os.path.exists(f'repos/{project_name}'):
                                        print(f"[INFO]  Project has been moved in failedrepos.")
                                        try:
                                            _snapshot_repo_to_failed(project_name)
                                        except Exception as e:
                                            print(e)
                                continue # Switch to next project
                            else: 
                                print(f"[INFO] Tests passed for {project_name}. Project has been moved to compiledrepos.")
                                try:
                                    _snapshot_repo_to_compiled(project_name)
                                except Exception as e:
                                    print(e)
                                continue # Switch to next project
                                 

                    # Run tests (Gradle projects)
                    try:
                        if system=="Windows":
                            subprocess.check_call(['gradle.bat', 'build'], cwd=f'repos/{project_name}', timeout=900)
                        else:
                            subprocess.check_call(['gradle', 'build'], cwd=f'repos/{project_name}', timeout=900)
                    except subprocess.CalledProcessError:
                        project_failed = True
                        # If tests fail, remove the project from compiledrepos if it exists
                        if os.path.exists(f'compiledrepos/{project_name}'):
                            print(f"[INFO] Tests failed for {project_name}. Project has been removed from compiledrepos and has been moved in failedrepos.")
                            try:
                                _snapshot_compiled_to_failed(project_name)
                            except Exception as e:
                                print(e)
                        else:
                            print(f"[INFO] Tests failed for {project_name}. Project was not in compiledrepos. Project has been moved in failedrepos")
                            if os.path.exists(f'repos/{project_name}'):
                                try:
                                    _snapshot_repo_to_failed(project_name)
                                except Exception as e:
                                    print(e)
                    else:
                        # If tests pass, move the project to compiledrepos
                        print(f"[INFO] Tests passed for {project_name}. Project has been moved to compiledrepos.")
                        try:
                            _snapshot_repo_to_compiled(project_name)
                        except Exception as e:
                            print(e)
                else:
                    print("-----------------------------------------------------------------------------------------------------------------------------")
                    print(f"[INFO] Project {project_name} is not a Maven or Gradle project. Project {project_name} has been moved in failedrepos")
                    try:
                        _snapshot_repo_to_failed(project_name)
                    except Exception as e:
                        print(e)
                    project_failed = True
            except Exception as e:
                print(f"[INFO] Failed to run tests for project {project_name}. Error: {e}")
                try:
                    if os.path.exists(f'compiledrepos/{project_name}'):
                        _snapshot_compiled_to_failed(project_name)
                    elif os.path.exists(f'repos/{project_name}'):
                        _snapshot_repo_to_failed(project_name)
                except Exception as e:
                    print(e)

        except Exception as e:
            print(f"[INFO] Failed to run tests for project {project_name}. Error: {e}")
            try:
                if os.path.exists(f'compiledrepos/{project_name}'):
                    _snapshot_compiled_to_failed(project_name)
                elif os.path.exists(f'repos/{project_name}'):
                        _snapshot_repo_to_failed(project_name)
            except Exception as e:
                print(e)
    return project_info



def add_module_to_list_project_info(project_name, module, project_info):
    """
    Takes the current project_info dictionary and adds a new module to the given project. Write the new project_info dictionary in the project_info.json file.
        Parameters:
                    java_version: the Java version of the new project
                    testng_version: the TestNG version of the new project
                    junit_version: the JUnit version of the new project
                    project_name: the project id of the new project
                    compiler_version: the Gradle or Maven version of the new project
                    type: the type of the new project (Maven or Gradle)
                    project_info (Dictionary): dictionary where the keys represent the project ID and the values are JSON-formatted descriptions of the project versions (Java, Maven/Gradle and JUnit/TestNG)           
        Returns:
                    project_info (Dictionary): the new project_info dictionary with the information of the new module
    """

    # Aggiungi la lista 'modules' a ciascun progetto
    for project_id, project_details in project_info.items():
        if project_id == project_name:
            if 'modules' in project_details:
                project_details['modules'].append(module)
            else:
                project_details['modules'] = [module]

    # Save project_info.json
    with open('output/project_info.json', 'w') as json_file:
        json.dump(project_info, json_file)
    return project_info
    
def set_project_info(java_version, testng_version, junit_version, project_name, compiler_version, type, project_info):
    """
    Takes the current project_info dictionary as input and adds the information of a new project/module. Write the new project_info dictionary in the project_info.json file.
        Parameters:
                    java_version: the Java version of the new project/module
                    testng_version: the TestNG version of the new project/module
                    junit_version: the JUnit version of the new project/module
                    project_name: the project id of the new project/module
                    compiler_version: the Gradle or Maven version of the new project/module
                    type: the type of the new project/module (Maven or Gradle)
                    project_info (Dictionary): dictionary where the keys represent the project ID/module and the values are JSON-formatted descriptions of the project versions (Java, Maven/Gradle and JUnit/TestNG)           
        Returns:
                    project_info (Dictionary): the new project_info dictionary with the information of the new project/module
    """
    if testng_version is None and junit_version is not None:
        project_info[project_name] = {'type': type, 'version': compiler_version,
                                        'java_version': java_version,
                                        'junit_version': junit_version}
    elif testng_version is not None and junit_version is None:
        project_info[project_name] = {'type': type, 'version': compiler_version,
                                        'java_version': java_version,
                                        'testng_version': testng_version}
    else:
        project_info[project_name] = {'type': type, 'version': compiler_version,
                                        'java_version': java_version,
                                        'junit_version': junit_version,
                                        'testng_version': testng_version}
    # Save project_info.json
    with open('output/project_info.json', 'w') as json_file:
        json.dump(project_info, json_file)
    return project_info





def choice_num_folder_to_be_processed():
    """
    Asks user how many projects he/she wants to be processed.
        Returns:
                    num_folder_to_be_processed: number of projects that the user wants to be processed
    """
    while True:
        num_folder_to_be_processed = input("How many folders would you like to process? (range 1-9410) ")
        if num_folder_to_be_processed.isdigit():
            num_folder_to_be_processed = int(num_folder_to_be_processed)
            if num_folder_to_be_processed <= 9410 and num_folder_to_be_processed > 0:
                return num_folder_to_be_processed
        print("Invalid input!")



def ask_user_input():
    """
    Asks user what he/she wants to do
        Returns:
                    num_folder_to_be_processed: number of projects that the user wants to be processed
                    specific_folder:  if the user wishes to process only one specific folder, then the specific_folder is the project ID. If the user does not wish to process only one specific folder, the specific_folder variable is set to None
    """
    choice_folders = None
    num_folder_to_be_processed = 0
    specific_folder = None
    while True:
        print("1. Process all folders in source directory")
        print("2. Process a specific number of folders")
        print("3. Process a specific folder")
        choice_folders = input("What do you want to do? ")
        if choice_folders == "1":
            num_folder_to_be_processed = utils.count_files_of_a_dir('source')
            break
        elif choice_folders == '2':
            num_folder_to_be_processed = choice_num_folder_to_be_processed()
            break
        elif choice_folders == '3':
            while True:
                num_folder_to_be_processed = 1
                specific_folder = input("Insert the project id: ")
                if os.path.exists(f'source/{specific_folder}'):
                    break
                print(f"{specific_folder} not found!")
            break
        print("Invalid input!")
    return num_folder_to_be_processed, specific_folder


def main():
    # Uncomment the following line to extract files
    #extract_files()

    num_folder_to_be_processed, specific_folder = ask_user_input()
    if specific_folder is not None:
        # Get the classes from the JSON files
        df, repo_urls = get_classes_from_json(None, None, specific_folder)
        execute_script(df, repo_urls)
        print('Execution completed!')
        
    elif num_folder_to_be_processed<=100:
        # Get the classes from the JSON files
        df, repo_urls = get_classes_from_json(0, num_folder_to_be_processed, specific_folder)
        execute_script(df, repo_urls)
        print('Execution completed!')
    else:
        step = 100
        iteration = num_folder_to_be_processed//step
        rest = num_folder_to_be_processed % 100
        start = 0
        for i in range(iteration):
            # Get the classes from the JSON files
            end = start + step
            df, repo_urls = get_classes_from_json(start, end, specific_folder)
            start = end
            execute_script(df, repo_urls)
            if rest == 0:
                if i == iteration - 1:
                    print('Execution completed!')
                else:
                    print(f'Iteration: {i+1}/{iteration}')
            else:
                print(f'Iteration: {i+1}/{iteration+1}')
        if rest != 0:
            df, repo_urls = get_classes_from_json(iteration*step, (iteration*step)+rest, specific_folder)
            start = end
            execute_script(df, repo_urls)


def execute_script(df, repo_urls):
    # Clone the repositories
    failed_clones = clone_repos(repo_urls)
    print(f'Failed clones:\n{failed_clones}')

    # Remove the failed clones from the DataFrame
    df = remove_failed_clones(df, failed_clones)

    # Create the JSON files
    missing_files = create_json(df, repo_urls)

    # clean up df to remove rows with missing files
    df = df[~df['Focal_Path'].isin([file for project, file in missing_files])]
    df = df[~df['Test_Path'].isin([file for project, file in missing_files])]

    # Read the previous output/classes.csv file if it exists
    df_new = df.copy()
    if os.path.exists('output/classes.csv'):
        df_old = pd.read_csv('output/classes.csv')
        # Concatenate the current dataframe with the older dataframe
        df = pd.concat([df_old, df], axis=0)
        df.drop_duplicates(subset={'Project', 'Focal_Class', 'Test_Class', 'Focal_Path', 'Test_Path', 'Focal_Method', 'Test_Case'}, keep='last', inplace=True)
    # Save the new dataframe to the output/classes.csv file.
    df.to_csv('output/classes.csv', index=False)
    print('Saved classes.csv')
    project_info = check_project_types(df_new)

    if os.path.exists('repos'):
        print("[INFO] Preserving immutable source repositories under repos/.")
    save_project_structures('./compiledrepos')


def _camel_case_tokens(identifier):
    cleaned_identifier = re.sub(r"[^A-Za-z0-9]", " ", identifier or "")
    spaced_identifier = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", cleaned_identifier)
    return [token.lower() for token in spaced_identifier.split() if token]


def regex_based_map_test_to_focal_methods(test_method_name, focal_methods):
    test_tokens = _camel_case_tokens(test_method_name)
    filtered_test_tokens = [
        token for token in test_tokens if token not in {"test", "should", "when", "then", "given", "verify"}
    ]
    if not filtered_test_tokens:
        filtered_test_tokens = test_tokens

    test_joined = "".join(filtered_test_tokens)
    best_score = 0
    matches = []

    for focal_method in focal_methods:
        focal_tokens = _camel_case_tokens(focal_method)
        if not focal_tokens:
            continue

        score = 0
        focal_joined = "".join(focal_tokens)
        overlap = len(set(filtered_test_tokens) & set(focal_tokens))
        score += overlap
        if focal_joined and focal_joined in test_joined:
            score += len(focal_tokens) + 1

        if score > best_score:
            best_score = score
            matches = [focal_method]
        elif score == best_score and score > 0:
            matches.append(focal_method)

    return sorted(set(matches))


def _compute_precision_recall(predicted, expected):
    predicted_set = set(predicted)
    expected_set = set(expected)
    if not predicted_set:
        precision = 0.0
    else:
        precision = len(predicted_set & expected_set) / len(predicted_set)
    if not expected_set:
        recall = 0.0
    else:
        recall = len(predicted_set & expected_set) / len(expected_set)
    return round(precision, 4), round(recall, 4)


def _resolve_dataset_class_path(record, key, repo_roots):
    relative_path = record.get(key, {}).get("file")
    if not relative_path:
        return None

    repository = record.get("repository", {})
    project_id = str(
        repository.get("repo_id")
        or record.get("project_id")
        or record.get("Project")
        or ""
    )

    candidates = []
    for repo_root in repo_roots:
        if project_id:
            candidates.append(os.path.join(repo_root, project_id, relative_path))
        candidates.append(os.path.join(repo_root, relative_path))
    candidates.append(relative_path)

    for candidate in candidates:
        normalized_candidate = os.path.normpath(candidate)
        if os.path.isfile(normalized_candidate):
            return normalized_candidate
    return None


def evaluate_mapping_accuracy(dataset_root, output_dir="output/mapping_evaluation", repo_roots=None):
    repo_roots = repo_roots or ["compiledrepos", "repos", "source", "."]
    dataset_records = []

    for root, _, files in os.walk(dataset_root):
        for file_name in files:
            if file_name.endswith(".json"):
                dataset_records.append(os.path.join(root, file_name))

    results = []
    for record_path in sorted(dataset_records):
        try:
            with open(record_path, "r", encoding="utf-8") as dataset_file:
                record = json.load(dataset_file)
        except (OSError, json.JSONDecodeError):
            continue

        test_method_name = record.get("test_case", {}).get("identifier")
        expected_method = record.get("focal_method", {}).get("identifier")
        focal_methods = [
            method.get("identifier")
            for method in record.get("focal_class", {}).get("methods", [])
            if method.get("identifier")
        ]

        if not test_method_name or not expected_method or not focal_methods:
            continue

        test_class_path = _resolve_dataset_class_path(record, "test_class", repo_roots)
        focal_class_path = _resolve_dataset_class_path(record, "focal_class", repo_roots)

        ast_prediction = []
        status = "evaluated"
        skip_reason = ""
        if test_class_path and focal_class_path:
            try:
                ast_mapping = psa.map_test_to_focal_methods(test_class_path, focal_class_path)
                ast_prediction = ast_mapping.get(test_method_name, [])
            except ValueError as exc:
                status = "skipped"
                skip_reason = str(exc)
        else:
            status = "skipped"
            skip_reason = "Missing test or focal class path in local dataset checkout."

        regex_prediction = regex_based_map_test_to_focal_methods(test_method_name, focal_methods)
        ast_precision, ast_recall = _compute_precision_recall(ast_prediction, [expected_method])
        regex_precision, regex_recall = _compute_precision_recall(regex_prediction, [expected_method])

        results.append(
            {
                "record": record_path,
                "project_id": record.get("repository", {}).get("repo_id"),
                "test_method": test_method_name,
                "expected_focal_methods": json.dumps([expected_method]),
                "ast_prediction": json.dumps(ast_prediction),
                "regex_prediction": json.dumps(regex_prediction),
                "ast_precision": ast_precision,
                "ast_recall": ast_recall,
                "regex_precision": regex_precision,
                "regex_recall": regex_recall,
                "status": status,
                "skip_reason": skip_reason,
            }
        )

    results_df = pd.DataFrame(results)
    os.makedirs(output_dir, exist_ok=True)
    output_csv = os.path.join(output_dir, "mapping_accuracy.csv")
    output_json = os.path.join(output_dir, "mapping_accuracy_summary.json")
    results_df.to_csv(output_csv, index=False)

    evaluated_df = results_df[results_df["status"] == "evaluated"] if not results_df.empty else results_df
    summary = {
        "dataset_root": os.path.abspath(dataset_root),
        "records_found": int(len(results_df)),
        "records_evaluated": int(len(evaluated_df)),
        "records_skipped": int(len(results_df) - len(evaluated_df)),
        "ast_macro_precision": round(float(evaluated_df["ast_precision"].mean()), 4)
        if not evaluated_df.empty
        else 0.0,
        "ast_macro_recall": round(float(evaluated_df["ast_recall"].mean()), 4)
        if not evaluated_df.empty
        else 0.0,
        "regex_macro_precision": round(float(evaluated_df["regex_precision"].mean()), 4)
        if not evaluated_df.empty
        else 0.0,
        "regex_macro_recall": round(float(evaluated_df["regex_recall"].mean()), 4)
        if not evaluated_df.empty
        else 0.0,
        "output_csv": os.path.abspath(output_csv),
    }

    with open(output_json, "w", encoding="utf-8") as summary_file:
        json.dump(summary, summary_file, indent=2)

    return summary, results_df


def run_mapping_evaluation_cli():
    parser = argparse.ArgumentParser(description="Evaluate AST and regex test-to-focal mapping.")
    parser.add_argument(
        "--dataset-root",
        default="methods2test",
        help="Root directory that contains the methods2test JSON records.",
    )
    parser.add_argument(
        "--output-dir",
        default="output/mapping_evaluation",
        help="Directory where the CSV and JSON reports will be written.",
    )
    parser.add_argument(
        "--repo-root",
        action="append",
        dest="repo_roots",
        default=None,
        help="Additional roots where dataset source checkouts may exist. Can be passed multiple times.",
    )
    arguments = parser.parse_args(sys.argv[2:])
    summary, _ = evaluate_mapping_accuracy(
        dataset_root=arguments.dataset_root,
        output_dir=arguments.output_dir,
        repo_roots=arguments.repo_roots,
    )
    print(json.dumps(summary, indent=2))

# Get the system: Windows, Linux or Darwin
system = _detect_system()


def save_project_structures(root_directory):
    for entry in os.listdir(root_directory):
        entry_path = f"{root_directory}/{entry}"
        if os.path.isdir(entry_path):
            psa.save_project_structure(entry_path)
            pda.save_project_dependencies(entry_path)



if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "evaluate-mapping":
        run_mapping_evaluation_cli()
        sys.exit(0)
    if utils.is_admin(system) == False:
        print("This script is running without administrator privileges!")
        print("Please re-run the script with administrator privileges to avoid errors during execution!") 
    main()
