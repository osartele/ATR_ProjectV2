import re
import os
import subprocess
import sys

import pandas as pd

import errorCorrection
import utils
import shutil

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
    ]
)


def record_tracking_metrics(test_class, test_path, generator, technique, chance, prompt_tokens, completion_tokens, iterations_to_pass):
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
                    }
                ]
            ),
        ],
        ignore_index=True,
    )




def search_modules_build_gradle(project_path, project_dataframe, project_id):
    """
    Searches all the modules where are stored build.gradle (or build.gradle.kts) files

        Parameters:
                    project_path: the path of the proejct
                    project_dataframe (Dataframe): the dataframe containing all the focal classes and test classes
                    project_id: the ID of the project
        Returns:
                    modules (List): the list of modules found               
    """
    project_df = project_dataframe.copy()
    modules = []
    for index, row in project_df.iterrows():
            location = row['Test_Path'].replace(f'repos/{project_id}/', '').replace(f"{row['Test_Class']}.java", '')
            file_name = f"/{row['Test_Class']}.java"
            while ((os.path.isfile(f"{project_path}/{location}/build.gradle")) or (os.path.isfile(f"{project_path}/{location}/build.gradle.kts"))) == False:
                location = os.path.dirname(location)
                if location == '':
                    break
            if location != '':
                modules.append(location)
    # remove duplicates
    modules = list(dict.fromkeys(modules)) 
    return modules


def extract_gradle_version_from_gradle_wrapper(project_name):
    """
    Extracts Gradle version from the given project. 
    This function searches for the Gradle version in the gradle-wrapper properties file of the given project.
        Parameters:
                    project_name: the ID of the project
        Returns:
                    compiler_version: the Gradle version expressed as a numeric value, 'None' if the function did not find it or if an error occured
    """
    compiler_version = None
    gradleWrapperFile = f'repos/{project_name}/gradle/wrapper/gradle-wrapper.properties'
    try:
        with open(gradleWrapperFile, 'r') as file:
            for line in file:
                if line.strip().startswith('distributionUrl'):
                    gradleVersionMatch = re.search(r'gradle-(.*?)(?:-bin)?(?:-all)?\.zip', line)
                    if gradleVersionMatch:
                        compiler_version = gradleVersionMatch.group(1)
                    break
    except Exception as e:
        print(e)
    return compiler_version


def extract_gradle_version_from_gradle_properties(project_name):
    """
    Extracts Gradle version from the given project. 
    This function searches for the Gradle version in the gradle.properties file of the given project.
        Parameters:
                    project_name: the ID of the project
        Returns:
                    compiler_version: the Gradle version expressed as a numeric value, 'None' if the function did not find it or if an error occured
    """
    compiler_version = None
    gradleProperties = f'repos/{project_name}/gradle.properties'
    try:
        with open(gradleProperties, 'r') as file:
                content=file.read()
                content = content.replace("\n", "")
                gradleVersionMatch = re.search(r'gradle.version\s*=\s*([\d.]+)', content)
                if gradleVersionMatch:
                    compiler_version = gradleVersionMatch.group(1)
    except Exception as e:
        print(e)
    return compiler_version





def extract_info_build_gradle(path, compiler_search):
    """
    Extracts java version, Gradle version and JUnit or TestNG version from the given project. 
    This function searches for all the versions in the build.gradle file of the given project.  
        Parameters:
                    project_path: the path of the project or of the module
                    compiler_search: If 'True' indicates that the function has to search for the Gradle version, if 'False' indicates that the function should not search for the Gradle version

        Returns:
                    java_version: if the function finds the Java version it returns a numeric value, otherwise it returns None
                    junit_version: if the function finds the Junit version it returns a numeric value, otherwise it returns None
                    testng_version: if the function finds the TestNG version it returns a numeric value, otherwise it returns None
                    compiler_version [if compiler_search is set to 'True']: if the function finds the Gradle version it returns a numeric value, otherwise it returns None
    """
    java_version = None
    junit_version = None
    testng_version = None
    compiler_version = None
    path_build_gradle=f'{path}/build.gradle'
    path_build_gradle_kts=f'{path}/build.gradle.kts'
    path_file = None # path of the file to be opened
    try:
        if os.path.exists(path_build_gradle):
            path_file = path_build_gradle
        else:
            path_file = path_build_gradle_kts
        with open(path_file, 'r') as file:
            content=file.read()
            content = content.replace("\n", " ")
            if compiler_search is True:
                gradleVersionMatch = re.search(r"gradleVersion\s*=\s*\'([\d.]+)\'", content)
                if gradleVersionMatch:
                    compiler_version = gradleVersionMatch.group(1)


            findJava = False # True if I found the java version, false otherwhise
            # research java version
            java_version_match = re.search(r"sourceCompatibility\s*=\s*([\d.]+)", content)
            if java_version_match:
                java_version = java_version_match.group(1)
                findJava = True
            if findJava == False: # if expressed with letters
                java_version_match = re.search(r"sourceCompatibility\s*=\s*JavaVersion\.(\S*)", content)
                if java_version_match:
                    java_version_text = java_version_match.group(1)
                    findJava = True
                    if java_version_text == 'Version_1.5' or java_version_text == 'Version_1_5' or java_version_text == 'Version_5' or java_version_text == 'VERSION_1.5' or java_version_text == 'VERSION_1_5' or java_version_text == 'VERSION_5':
                        java_version = '1.5'
                    elif java_version_text == 'Version_1.6' or java_version_text == 'Version_1_6' or java_version_text == 'Version_6' or java_version_text == 'VERSION_1.6' or java_version_text == 'VERSION_1_6' or java_version_text == 'VERSION_6':
                        java_version = '1.6'
                    elif java_version_text == 'Version_1.7' or java_version_text == 'Version_1_7' or java_version_text == 'Version_7' or java_version_text == 'VERSION_1.7' or java_version_text == 'VERSION_1_7' or java_version_text == 'VERSION_7':
                        java_version = '1.7'
                    elif java_version_text == 'Version_1.8' or java_version_text == 'Version_1_8' or java_version_text == 'Version_8' or java_version_text == 'VERSION_1.8' or java_version_text == 'VERSION_1_8' or java_version_text == 'Version_8':
                        java_version = '1.8'
                    elif java_version_text == 'Version_11' or java_version_text == 'VERSION_11':
                        java_version = '11'
                    elif java_version_text == 'Version_17' or java_version_text == 'VERSION_17':
                        java_version = '17'
                    elif java_version_text == 'Version_21' or java_version_text == 'VERSION_21':
                        java_version = '21'
                    else:
                        findJava = False
                
            # research test version
            findTest = False # True if I found the Junit/testNG version, false otherwhise
            # research junit version
            junit_version_match = re.search(r"junit(?:5)?Version\s*=\s*[\'\"]([0-9.]+)[\'\"]", content)
            if junit_version_match:
                junit_version = junit_version_match.group(1)
                findTest = True
            if findTest == False:
                dependency_match = re.search(r"(testCompile|testImplementation|testCompileOnly|implementation).?[\'\"]junit:junit:([0-9.]+)[\'\"]", content)
                if dependency_match:
                    junit_version = dependency_match.group(2)
                    findTest = True
            if findTest == False:
                dependency_match = re.search(r"(testCompile|testImplementation|testCompileOnly|implementation)\s+\'junit:junit:([0-9.]+)\'", content)
                if dependency_match:
                    junit_version = dependency_match.group(2)
                    findTest = True
            if findTest == False:
                dependency_match = re.search(r"name:\s*\'junit\',\s*version:\s*\'([0-9.]+)\'", content)
                if dependency_match:
                    junit_version = dependency_match.group(1)
                    findTest = True
            if findTest == False: #build-gradle.kts
                dependency_match = re.search(r'(testCompile|testImplementation|testCompileOnly|implementation)\s*\("junit:junit:([0-9.]+)"\)', content)
                if dependency_match:
                    junit_version = dependency_match.group(2)
                    findTest = True
            if findTest == False:
                dependency_match = re.search(r'(testCompile|testImplementation|testCompileOnly|implementation)\s+\'org.junit.jupiter:junit-jupiter:([0-9.]+)\'', content)
                if dependency_match:
                    junit_version = dependency_match.group(2)
                    findTest = True
            if findTest == False:
                dependency_match = re.search(r'(testCompile|testImplementation|testCompileOnly|implementation)\s+\'org.junit.jupiter:junit-jupiter-api:([0-9.]+)\'', content)
                if dependency_match:
                    junit_version = dependency_match.group(2)
                    findTest = True

                                    

            # research testng version
            if findTest == False:
                testng_version_match = re.search(r"testng(?:5)?Version\s*=\s*[\'\"]([0-9.]+)[\'\"]", content)
                if testng_version_match:
                    testng_version = testng_version_match.group(1)
                    findTest = True
            if findTest == False:
                dependency_match = re.search(r"(testCompile|testImplementation|testCompileOnly|implementation).?[\'\"]testng:testng::([0-9.]+)[\'\"]", content)
                if dependency_match:
                    testng_version = dependency_match.group(2)
                    findTest = True
            if findTest == False:
                dependency_match = re.search(r"(testCompile|testImplementation|testCompileOnly|implementation)\s+\'testng:testng:([0-9.]+)\'", content)
                if dependency_match:
                    testng_version = dependency_match.group(2)
                    findTest = True
            if findTest == False:
                dependency_match = re.search(r"name:\s*\'testng\',\s*version:\s*\'([0-9.]+)\'", content)
                if dependency_match:
                    testng_version = dependency_match.group(1)
                    findTest = True     
            if findTest == False: #build-gradle.kts
                dependency_match = re.search(r'(testCompile|testImplementation|testCompileOnly|implementation)\s*\("testng:testng:([0-9.]+)"\)', content)
                if dependency_match:
                    testng_version = dependency_match.group(2)
                    findTest = True
    
    except Exception as e:
        print(e)
    if compiler_search is True:
        return java_version, junit_version, testng_version, compiler_version
    else:
        return java_version, junit_version, testng_version







def run_evosuite_generation_gradle(focal_path):
    """
    Given a focal class of a Gradle project, it runs EvoSuite to generate the corresponding test class.
    This function uses the evosuite-1.0.6.jar file; therefore, the evosuite JAR file must be present in the same directory as gradleLib.py
        Parameters:
                focal_path: the path of the focal class
        Returns:
                :'True' if the generation has been executed correctly, 'False' otherwise
       
    """
    name_focal_class = focal_path.split('java/')[1].replace('.java', '').replace('/', '.')
    location = focal_path
    while not os.path.isdir(f"{location}/build/classes/java/main"):
        location = os.path.dirname(location)
        if location == '':
            break
    if location != '':
        project_cp = f"{location}/build/classes/java/main"
    else:
        return False

    command = f'java -jar evosuite-1.0.6.jar -class {name_focal_class} -projectCP "{project_cp}"'
    try:
        build_timeout_seconds = utils.get_subprocess_timeout_seconds("build_timeout_seconds", 900)
        result = subprocess.run(command, capture_output=True, text=True, timeout=build_timeout_seconds)
        if result.stdout.__contains__("Computation finished"):
            return True
        else:
            return False
    except subprocess.TimeoutExpired as e:
        print(f"EvoSuite Gradle generation timed out after {build_timeout_seconds} seconds: {e}")
        return False
    except Exception as e:
        print(e)
        return False
    


def add_evosuite_build_gradle(path):
    """
    Adds the evosuite dependency (version 1.0.6) to the build.gradle file of the given project.
        Parameters:
                    project_path: the path of the Gradle project or of the module
        Returns:
                    old_build_gradle: the content of the build.gradle file before the edit, 'None' if an error occured
    """
    build_gradle_path = os.path.join(path, "build.gradle")
    build_gradle_kts_path = os.path.join(path, "build.gradle.kts")
    if os.path.exists(build_gradle_path):
        try:
            with open(build_gradle_path, "r") as file:
                build_gradle_content = file.read()
        except Exception as e:
            print(e)
            return None
        old_build_gradle = build_gradle_content
        build_gradle_content = build_gradle_content.replace("apply plugin: 'jacoco'", f"""apply plugin: 'jacoco' \n dependencies{{\n implementation files('../../../evosuite-standalone-runtime-1.0.6.jar')\n}}""")
        try:
            with open(build_gradle_path, "w") as file:
                file.write(build_gradle_content)
        except Exception as e:
            print(e)
            return None
        return old_build_gradle
    
    elif os.path.exists(build_gradle_kts_path):
        try:
            with open(build_gradle_kts_path, "r") as file:
                build_gradle_content = file.read()
        except Exception as e:
            print(e)
            return None
        old_build_gradle = build_gradle_content
        build_gradle_content = build_gradle_content.replace('apply(plugin = "jacoco")', f"""apply(plugin = "jacoco") \n dependencies{{\n implementation(files("../../../evosuite-standalone-runtime-1.0.6.jar"))\n}}""")
        try:
            with open(build_gradle_kts_path, "w") as file:
                file.write(build_gradle_content)
        except Exception as e:
            print(e)
            return None
        return old_build_gradle
    else:
        return None
    


    



def run_gradle_test_command(path, project_dataframe, system):
    """
    Runs the package command for the given Gradle project. 
    It generates the Jacoco and PITest reports. 
        Parameters:
                    path: the path of the project or of the module
                    project_dataframe (Dataframe): the dataframe containing the focal classes and test classes that are to be executed by gradle
                    system (string): the current OS (Windows, Linux, etc..)  
        Returns:
                    : 'True' if the project has been compiled successfully,'False' if the project has been compiled with errors
    """
    project_df = project_dataframe.copy()
    try:
        build_timeout_seconds = utils.get_subprocess_timeout_seconds("build_timeout_seconds", 900)
        # Create -Dtest= parameter only for the test classes that exist
        test_classes = project_df['Test_Path'].tolist()
        result = None
        # convert the test pathresult =  to the correct format cut after test/java/
        test_classes = [test_path.split('test/java/')[1].replace('/', '.').replace('.java', '') for test_path in
                        test_classes]
        test_classes = ','.join(test_classes)
        test_classes = f'--tests={test_classes}'
        print(f"Test classes: {test_classes}")
        subprocess.check_call(['java', '-version'])
        if system == 'Windows':
            result = subprocess.run(
                    ['gradle.bat', '--no-daemon', '--console=plain', 'clean', 'test', test_classes, 'pitest'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
        else: 
            result = subprocess.run(
                    ['gradle', '--no-daemon', '--console=plain', 'clean', 'test', test_classes, 'pitest'], cwd=path, capture_output=True, text=True, timeout=build_timeout_seconds)
        if result.stdout.__contains__("BUILD SUCCESSFUL"):  
            return True, None
        else:
            errori = errorCorrection.extract_gradle_errors(result.stdout, result.stderr)
            print("\n--------------------")
            print(errori)
            print("\n--------------------")
            return False, errori
    except subprocess.TimeoutExpired as e:
            print(f"Gradle command timed out after {build_timeout_seconds} seconds.")
            timed_out_error = errorCorrection.extract_gradle_errors(e.stdout or "", e.stderr or "")
            return False, timed_out_error
    except Exception as e:
            print(e)
            return False
    


def edit_build_gradle_file(path, project_dataframe, junit_version):
    """
    Edits the build.gradle file to add Jacoco and PITest dependencies
        Parameters:
                    project_path: the path of the project or of the module
                    project_dataframe (Dataframe): the dataframe containing all the focal classes and test classes that are to be executed with PITest 
                    junit_version: the JUnit version of the Gradle project, "None" if the project does not include the JUnit framework

        Returns:
                    build_gradle_content: the content of the build.gradle file before the edit (if the build.gradle file has been edited successfully),'False' if the 'pistest' or 'jacoco' dependency has already been implemented (currently, it is not possible to edit the build.gradle file if it already has the PITest or Jacoco dependency implemented), 'None' if an error occured
    """
    project_df = project_dataframe.copy()
    build_gradle_path = os.path.join(path, 'build.gradle')
    build_gradle_kts_path = os.path.join(path, 'build.gradle.kts')
    # Get the test classes
    test_classes = project_df['Test_Path'].tolist()
    test_classes = [test_path.split('test/java/')[1].replace('/', '.').replace('.java', '') for test_path in
                    test_classes]
    for i in range(len(test_classes)):
        test_classes[i] = "'" + test_classes[i] + "'"
    test_classes = ','.join(test_classes)

    # Get the focal classes
    focal_classes = project_df['Focal_Path'].tolist()
    focal_classes = [focal_path.split('main/java/')[1].replace('/', '.').replace('.java', '') for focal_path in
                    focal_classes]
    for i in range(len(focal_classes)):
        focal_classes[i] = "'" + focal_classes[i] + "'"
    focal_classes = ','.join(focal_classes)


    if os.path.exists(build_gradle_path):
        try:
            # Read the build.gradle content
            with open(build_gradle_path, 'r') as file:
                build_gradle_content = file.read()
        except Exception as e:
            print(e)
            return None

        # Check if the 'pistest' or 'jacoco' dependency has already been implemented
        if build_gradle_content.__contains__("pitest") or (build_gradle_content.__contains__("jacoco")):
            return False
        else:
            if junit_version.startswith('5'):
                add_dependecies=f"""buildscript {{\n    repositories {{\n        mavenCentral()\n    }}\n    dependencies {{\n        classpath 'info.solidsoft.gradle.pitest:gradle-pitest-plugin:1.15.0'\n        classpath 'org.jacoco:org.jacoco.core:0.8.9'\n    }}\n}}\\n\\nallprojects {{\n    apply plugin: 'java'\n    apply plugin: 'info.solidsoft.pitest'\n    apply plugin: 'jacoco'\n\n    pitest{{\n        junit5PluginVersion = '1.2.1'\n        targetTests = [{test_classes}] \n        targetClasses = [{focal_classes}]\n        outputFormats = ['csv']\n        threads = 4\n        failWhenNoMutations = false\n    }}\n    \n    jacocoTestReport {{\n        dependsOn test\n        reports {{\n            xml.required= false\n            html.required = false\n            csv.required = true\n            csv.destination file("${{buildDir}}/reports/jacoco/jacoco.csv")\n    }}\n }}\n    test {{\n        filter{{\n            setFailOnNoMatchingTests(false)\n        }}\n        finalizedBy jacocoTestReport \n    }}\n\n}}\n"""
            else:
                add_dependecies=f"""buildscript {{\n    repositories {{\n        mavenCentral()\n    }}\n    dependencies {{\n        classpath 'info.solidsoft.gradle.pitest:gradle-pitest-plugin:1.15.0'\n        classpath 'org.jacoco:org.jacoco.core:0.8.9'\n    }}\n}}\n\nallprojects {{\n    apply plugin: 'java'\n    apply plugin: 'info.solidsoft.pitest'\n    apply plugin: 'jacoco'\n\n    pitest{{\n           targetTests = [{test_classes}] \n        targetClasses = [{focal_classes}]\n        outputFormats = ['csv']\n        threads = 4\n        failWhenNoMutations = false\n    }}\n    \n    jacocoTestReport {{\n        dependsOn test\n        reports {{\n            xml.required= false\n            html.required = false\n            csv.required = true\n            csv.destination file("${{buildDir}}/reports/jacoco/jacoco.csv")\n    }}\n }}\n    test {{\n        filter{{\n            setFailOnNoMatchingTests(false)\n        }}\n        finalizedBy jacocoTestReport \n    }}\n\n}}\n"""
            old_build_gradle_content = build_gradle_content
            build_gradle_content = add_dependecies + build_gradle_content
            try:
                with open(build_gradle_path, 'w') as file:
                    file.write(build_gradle_content)
            except Exception as e:
                print(e)
                return None
            return old_build_gradle_content
    elif os.path.exists(build_gradle_kts_path):
        try:
            # Read the build.gradle content
            with open(build_gradle_path, 'r') as file:
                build_gradle_content = file.read()
        except Exception as e:
            print(e)
            return None
        # Check if the 'pistest' or 'jacoco' dependency has already been implemented
        if build_gradle_content.__contains__("pitest") or (build_gradle_content.__contains__("jacoco")):
            return False
        if junit_version.startswith('5'):
            add_dependecies = f"""buildscript {{\n    repositories {{\n        mavenCentral()\n    }}\n    dependencies {{\n        classpath ("info.solidsoft.gradle.pitest:gradle-pitest-plugin:1.15.0")\n        classpath ("org.jacoco:org.jacoco.core:0.8.9")\n    }}\n}}\\n\\nallprojects {{\n    apply(plugin = "java")\n    apply(plugin = 'info.solidsoft.pitest")\n    apply(plugin = "jacoco")\n\n    pitest{{\n        junit5PluginVersion = '1.2.1'\n        targetTests = listOf({test_classes}) \n        targetClasses = listOf({focal_classes})\n        outputFormats = listOf("csv")\n        threads = 4\n        failWhenNoMutations = false\n    }}\n    \n    jacocoTestReport {{\n        dependsOn ("test")\n        reports {{\n            xml.required= false\n            html.required = false\n            csv.required = true\n            csv.destination(file("${{buildDir}}/reports/jacoco/jacoco.csv"))\n    }}\n }}\n    task.named("test") {{\n        filter{{\n            setFailOnNoMatchingTests(false)\n        }}\n        finalizedBy ("jacocoTestReport") \n    }}\n\n}}\n"""
        else:
            add_dependecies = f"""buildscript {{\n    repositories {{\n        mavenCentral()\n    }}\n    dependencies {{\n        classpath ("info.solidsoft.gradle.pitest:gradle-pitest-plugin:1.15.0")\n        classpath ("org.jacoco:org.jacoco.core:0.8.9")\n    }}\n}}\\n\\nallprojects {{\n    apply(plugin = "java")\n    apply(plugin = 'info.solidsoft.pitest")\n    apply(plugin = "jacoco")\n\n    pitest{{\n        targetTests = listOf({test_classes}) \n        targetClasses = listOf({focal_classes})\n        outputFormats = listOf("csv")\n        threads = 4\n        failWhenNoMutations = false\n    }}\n    \n    jacocoTestReport {{\n        dependsOn ("test")\n        reports {{\n            xml.required= false\n            html.required = false\n            csv.required = true\n            csv.destination(file("${{buildDir}}/reports/jacoco/jacoco.csv"))\n    }}\n }}\n    task.named("test") {{\n        filter{{\n            setFailOnNoMatchingTests(false)\n        }}\n        finalizedBy ("jacocoTestReport") \n    }}\n\n}}\n"""
        old_build_gradle_content = build_gradle_content
        build_gradle_content = add_dependecies + build_gradle_content
        try:
            with open(build_gradle_path, 'w') as file:
                file.write(build_gradle_content)
        except Exception as e:
            print(e)
            return None
        return True
    else:
        return None
    


def write_build_gradle(path, build_gradle_content):
    """
    Writes the build.gradle file of the given project with the given content.
        Parameters:
                    path: the path of the project or of the module
                    build_gradle_content: the content that needs to be written
    """
    
    build_gradle_path = os.path.join(path, 'build.gradle')
    build_gradle_kts_path = os.path.join(path, 'build.gradle.kts')
    if os.path.exists(build_gradle_path):
        try:
            with open(build_gradle_path, 'w') as build_gradle_file:
                build_gradle_file.write(build_gradle_content)
        except Exception as e:
            print(e)
    elif (os.path.exists(build_gradle_kts_path)):
        try:
            with open(build_gradle_kts_path, 'w') as build_gradle_file:
                build_gradle_file.write(build_gradle_content)
        except Exception as e:
            print(e)





def process_gradle_project(project, test_types, techniques, project_path, project_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, project_structure, project_dependencies, correct):
    """
    It processes the given Gradle project with the given test types and techniques.
    Parameters:
                project: the ID of the project.
                test_types (List): the list of test types to execute.
                techniques (List): the list of prompt techniques (for the AI test types) to execute.
                project_path: the path of the project. 
                project_df: the dataframe that contains all the focal/test classes of the project.
                compilter_version: the Gradle version of the given project.
                java_version: the Java version of the given project.
                junit_version: the Junit version of the given project.
                testng_version: the testNG version of the given project.
                has_mockito: the string that will be used to specify to the API whether the AI test types can use the Mockito framework or not.
                system (String): the current OS (Windows, Linux, etc...)
    Returns:
                0(int) if the process failed.
    """
    swtich_to_next_project = False
    if testng_version is not None:
        print(f"{project} skipped because pitest is not compatible with testng on gradle projects. Switch to next project...")
        return 0 # Switch to next project
    gradle_directory = r"/Gradle"
    utils.set_gradle_variable(gradle_directory, '8', system)
    # add jacoco and pitest dependecies to build.gradle
    original_build_gradle = edit_build_gradle_file(project_path, project_df, junit_version)
    if(original_build_gradle==False):
        print(f"{project} skipped because pitest or jacoco is already implemented in the build.gradle file. Switch to next project...")
        return 0 # Switch to next project
    elif(original_build_gradle==None):
        print(f"{project} skipped because an error occurred while trying to integrate the Jacoco and Pitest dependencies into the build.gradle file. Switch to next project...")
        return 0 # Switch to next project
    for i, test_type in enumerate(test_types):
        output_path_failed = f'./output/{project}/TestClasses_{project}_{test_type}.failed' # Indicate that the test type failed due to an error during the execution of the script.
        output_path_failed_gradle = f'./output/{project}/TestClasses_{project}_{test_type}.gradlefailed' # Indicates that all the test classes of the test type failed during the gradle execution.

        restart_test_type = False
        print('\n----')
        print(f"STARTING {test_type} test type\n")
        if test_type == "human":
            # configure the test smell detector
            csv_path_input_test_smell = utils.configure_test_smell_detector(project_df, project)
            # Run the Gradle package command. 
            print("--//loading gradle execution..//")
            if(run_gradle_test_command(project_path, project_df, system)[0]==True):
                print(f"Package command completed for {project}\n")
            else:
                print(f"Package command failed for {project}. Switch to next project...\n")
                write_build_gradle(project_path, original_build_gradle)
                swtich_to_next_project = True
                break # switch to next project

            # Run the test smell detector
            path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None)
            if path_csv_result_test_smell is None:
                print("An error occured while trying to run the test smell detector")
            else:
                print("The test smell detector ended successfully")  
            # Retrieve Code Coverage and Cyclomatic Complexity on test classes
            measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(project_path, project_df, project, 'Gradle')
            if measures_df is None:
                print(f"Switch to next project because edit_build_gradle_file() failed for the project {project}")
                write_build_gradle(project_path, original_build_gradle)
                swtich_to_next_project = True
                break # Switch to next project
            output_csv_path = utils.generate_output_csv_test_type(project, test_type, None, measures_df, path_csv_result_test_smell)
            if output_csv_path is None:
                print("An errore occured while trying to save the test type csv file!")
            else:
                print(f"DataFrame saved to {output_csv_path}")
        elif test_type == 'evosuite':
            project_df_evosuite = project_df.copy() # dataframe for the evosuite test type
            build_gradle_before_evosuite = add_evosuite_build_gradle(project_path)
            if build_gradle_before_evosuite is None:
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
                test_path = row['Test_Path'].replace('repos/', 'compiledrepos/')
                focal_path = row['Focal_Path'].replace('repos/', 'compiledrepos/')
                last_execution = None # outcome of the last gradle execution, True = Build Success, False = Build Failure
                try:
                    with open(test_path, 'r') as test_file_read:
                        human_test_class = test_file_read.read() # save the human version of the test class
                        dictionary_for_restore[test_path] =  human_test_class 
                    os.remove(test_path)      
                except Exception as e:
                    print(f"An error occured while trying to open and read the test_path: {e}")
                    try:
                        write_build_gradle(project_path, build_gradle_before_evosuite)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(project_path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True # Switch to next test type
                    break
                
                print("--//loading evosuite generation..//")
                if run_evosuite_generation_gradle(focal_path) == False: # if error while running evosuite
                    print ("An error accored while trying to run the evosuite generation")
                    try:
                        write_build_gradle(project_path, build_gradle_before_evosuite)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(project_path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True
                    break # Switch to next test type
                else:
                    print(f"Evosuite generation performed correctly for the {name_focal_class} class")

                # Insert the test class generated by evosuite in the test_path
                test_path_package_evosuite = test_path.split('java/')[1].replace(f'{name_test_class}.java', f'{name_focal_class}_ESTest.java')
                evosuite_test_path = f'evosuite-tests/{test_path_package_evosuite}'
                try:
                    if not os.path.exists(evosuite_test_path):
                        project_df_evosuite = project_df_evosuite[project_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Gradle execution
                        utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Gradle execution
                        utils.remove_evosuite_scaffolding_files(list(test_path))
                        utils.remove_directory_evosuite_command_line()
                        continue 
                    with open(evosuite_test_path, 'r') as evosuite_file:
                        evosuite_content = evosuite_file.read()
                    evosuite_content = evosuite_content.replace(F'public class {name_focal_class}_ESTest', f'public class {name_test_class}') 
                    evosuite_content = evosuite_content.replace('separateClassLoader = true', 'separateClassLoader = false') # when setting separateClassLoader to false, JaCoCo can correctly calculate code coverage
                    with open(test_path, 'w') as test_file:
                        test_file.write(evosuite_content)
                except Exception as e:
                    print(f"An error occured while trying to copy the evosuite class test: {e}")
                    try:
                        write_build_gradle(project_path, build_gradle_before_evosuite)
                        utils.write_files(dictionary_for_restore)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.remove_directory_evosuite_command_line()
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(project_path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_directory_evosuite_command_line()
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True
                    break # Switch to next test type 
                
                # Move the scaffolding file in the test class directory
                test_path_package_evosuite_scaffolding = test_path.split('java/')[1].replace(f'{name_test_class}.java', f'{name_focal_class}_ESTest_scaffolding.java')
                evosuite_test_path_scaffolding = f'evosuite-tests/{test_path_package_evosuite_scaffolding}'
                try:
                    shutil.move(evosuite_test_path_scaffolding, test_path.replace('.java', '').replace(f'{name_test_class}', ''))
                except Exception as e:
                    print(f"An error occured while trying to move the scaffolding file: {e}")
                    try:
                        write_build_gradle(project_path, build_gradle_before_evosuite)
                        utils.write_files(dictionary_for_restore)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.remove_directory_evosuite_command_line()
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(project_path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_directory_evosuite_command_line()
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True
                    break # Switch to next test type 
                
                print("--//loading gradle execution..//")
                if run_gradle_test_command(project_path, project_df, system)[0]==False: # if error while running gradle
                    print(f"Package command failed for project: {project}, test type: {test_type}\n")
                    project_df_evosuite = project_df_evosuite[project_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Gradle execution
                    utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Gradle execution
                    utils.remove_evosuite_scaffolding_files(list(test_path))
                    utils.remove_directory_evosuite_command_line()
                    last_execution = False
                    continue 
                else:
                    last_execution = True
                    print(f"Package command completed for {project}\n") 


                utils.remove_directory_evosuite_command_line()

            if restart_test_type == True:
                continue
            
            if project_df_evosuite.empty: # If all the test classes provided by Evosuite failed during Gradle execution
                try:
                    with open(output_path_failed_gradle, 'w') as file:
                        pass
                except Exception as e:
                    write_build_gradle(project_path, original_build_gradle)
                    utils.write_files(dictionary_for_restore)
                    utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                    print(f'An error occured while trying to open output_path_failed_gradle: {e}')
                    sys.exit(1)
            else: # if at least one of the test classes provided by Evosuite runned succesfully during Gradle execution
                # configure the test smell detector
                csv_path_input_test_smell = utils.configure_test_smell_detector(project_df_evosuite, project)
                # Run the test smell detector
                path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None)
                if path_csv_result_test_smell is None:
                    print("An error occured while trying to run the test smell detector")
                else:
                    print("The test smell detector ended successfully")  
                if last_execution == False: # if last gradle execution outcome is False, then I run one more time gradle
                    print("--//loading gradle execution..//")
                    if run_gradle_test_command(project_path, project_df, system)[0]==False: # if error while running gradle
                        print('An error occured while trying to execute the final version of test classes.\n')
                        try:
                            write_build_gradle(project_path, build_gradle_before_evosuite)
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            write_build_gradle(project_path, original_build_gradle)
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        continue # Switch to next test type
                    # Retrieve Code Coverage and Cyclomatic Complexity on test classes
                measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(project_path, project_df_evosuite, project, 'Gradle')
                if measures_df is not None:
                    output_csv_path = utils.generate_output_csv_test_type(project, test_type, None, measures_df, path_csv_result_test_smell)
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
                        write_build_gradle(project_path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
            utils.write_files(dictionary_for_restore)
            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
            write_build_gradle(project_path, build_gradle_before_evosuite)



        else:
            # Iterate over each technique
            for j, technique in enumerate(techniques):  
                output_path_failed = f'./output/{project}/TestClasses_{project}_{test_type}_{technique}.failed' # Indicate that the test type/technique failed due to an error during the execution of the script or during a call to the API
                output_path_failed_gradle = f'./output/{project}/TestClasses_{project}_{test_type}_{technique}.gradlefailed' # Indicates that all the test classes of the test type failed during the gradle execution.

                restart_technique = False 
                print(f"\nProcessing test_type: {test_type}, technique: {technique}")
                project_df_technique = project_df.copy() # dataframe of the current test type and technique
                dictionary_for_restore = {} # dictionary that contains test_path as keys and the respective 'human_test_class' as values. This dictionary is used to restore the test classes to the human version.
                for index, row in project_df_technique.iterrows(): # iterate over each test class and focal class
                    name_focal_class = row['Focal_Class']
                    name_test_class = row['Test_Class']
                    focal_path = row['Focal_Path'].replace('repos/', 'compiledrepos/')
                    test_path = row['Test_Path'].replace('repos/', 'compiledrepos/')
                    last_execution = None # outcome of the last gradle execution, True = Build Success, False = Build Failure
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
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(project_path, original_build_gradle)
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
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(project_path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True
                        break

                    print(f"\nDispatching Codex CLI for test_type: {test_type}, technique: {technique}, focal class: {name_focal_class}")
                    package_test_class = utils.find_package(test_path)
                    generated_test_content, messages, usage_metadata = utils.generate_test_with_codex(test_type, technique, focal_class, focal_path, testing_framework, java_version, has_mockito, test_path, name_test_class, project_structure, project_dependencies, package_test_class)
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
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(project_path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                    
                    try:
                        with open(test_path, 'w') as test_file_write:
                            test_file_write.write(generated_test_content)
                        api_output_file_path = f'output/{project}/response_{test_type}_{technique}_{name_test_class}.java'
                        with open(api_output_file_path, 'w', encoding='utf-8') as api_output_file:
                            api_output_file.write(generated_test_content)
                            print(f"File generated at: {api_output_file_path}")
                    except Exception as e:
                        print(f"An error occured while trying to open and read the test_path: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(project_path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                                

                    # Run the Gradle package command
                    print("--//loading gradle execution..//")
                    esito, errori = run_gradle_test_command(project_path, project_df, system)
                    if not esito and correct == True: # if error while running gradle
                        chance_result = False
                        iterations_to_pass = 0
                        for num_chance in range(1, 5):
                            if chance_result:
                                break
                            chance_result, errori, correction_usage = errorCorrection.correct_errors(project, test_type, technique, test_path, focal_path, project_path, project_df, system, messages, errori, dictionary_for_restore, num_chance, "Gradle")
                            total_prompt_tokens += correction_usage.get("prompt_tokens", 0)
                            total_completion_tokens += correction_usage.get("completion_tokens", 0)
                            iterations_to_pass = num_chance
                            if chance_result:
                                last_execution = True
                                record_tracking_metrics(name_test_class, test_path, test_type, technique, num_chance, total_prompt_tokens, total_completion_tokens, iterations_to_pass)
                        errorCorrection.save_conversation_to_json(messages, name_test_class, os.path.join("output", str(project), "codex_conversations"))
                        if not chance_result:
                            record_tracking_metrics(name_test_class, test_path, test_type, technique, 6, total_prompt_tokens, total_completion_tokens, iterations_to_pass)
                    elif not esito and correct == False:
                        print(
                            f"Package command failed for project: {project}, test type: {test_type}, technique: {technique}\n")
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 6, total_prompt_tokens, total_completion_tokens, 0)
                    elif esito:
                        last_execution = True
                        print(f"Package command completed for {project}\n")
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 0, total_prompt_tokens, total_completion_tokens, 0)

                if restart_technique == True:
                    continue

                    
                if project_df_technique.empty: # If all the test classes provided by the API failed during Gradle execution
                    try:
                        with open(output_path_failed_gradle, 'w') as file:
                            pass
                    except Exception as e:
                        utils.write_files(dictionary_for_restore)
                        write_build_gradle(project_path, original_build_gradle)
                        print(f'An error occured while trying to open output_path_failed_gradle: {e}')
                        sys.exit()
                else: # if at least one of the test classes provided by the API runned succesfully during Gradle execution
                    # configure the test smell detector
                    csv_path_input_test_smell = utils.configure_test_smell_detector(project_df_technique, project)
                    # Run the test smell detector
                    path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, technique)
                    if path_csv_result_test_smell is None:
                        print("An error occured while trying to run the test smell detector")
                    else:
                        print("The test smell detector ended successfully")  
                    if last_execution == False: # if last gradle execution outcome is False, then I run one more time gradle
                        print("--//loading gradle execution..//")
                        if run_gradle_test_command(project_path, project_df, system)[0]==False: # if error while running gradle
                            print('An error occured while trying to execute the final version of test classes.\n')
                            try:
                                utils.write_files(dictionary_for_restore)
                                with open(output_path_failed, 'w') as file:
                                    pass
                            except Exception as e:
                                utils.write_files(dictionary_for_restore)
                                write_build_gradle(project_path, original_build_gradle)
                                print(f'An error occured while trying to open output_path_failed: {e}')
                                sys.exit(1)
                            continue  # switch to next technique      
                    # Retrieve Code Coverage and Cyclomatic Complexity on test classes
                    measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(project_path, project_df_technique, project, 'Gradle')
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
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(project_path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                    utils.write_files(dictionary_for_restore)
    if swtich_to_next_project == True:
        return 0
    write_build_gradle(project_path, original_build_gradle)



def process_gradle_module(project, module, test_types, techniques, path, module_df, compiler_version, java_version, junit_version, testng_version, has_mockito, system, project_structure, project_dependencies, correct=False):
    """
    It processes the given Gradle module with the given test types and techniques.
    Parameters:
                project: the ID of the project.
                module: the name of the module.
                test_types (List): the list of test types to execute.
                techniques (List): the list of prompt techniques (for the AI test types) to execute.
                path: the path of the module. 
                module_df: the dataframe that contains all the focal/test classes of the module.
                compilter_version: the Gradle version of the given project.
                java_version: the Java version of the given project.
                junit_version: the Junit version of the given project.
                testng_version: the testNG version of the given project.
                has_mockito: the string that will be used to specify to the API whether the AI test types can use the Mockito framework or not.
    Returns:
                0(int) if the process failed.
    """
    if testng_version is not None:
        print(f"{project} skipped because pitest is not compatible with testng on gradle projects. Switch to next project/module...")
        return 0 # Switch to next project or module
    gradle_directory = r"/Gradle"
    utils.set_gradle_variable(gradle_directory, '8', system)
    # add jacoco and pitest dependecies to build.gradle
    original_build_gradle = edit_build_gradle_file(path, module_df, junit_version)
    if(original_build_gradle==False):
        print(f"{project} skipped because pitest or jacoco is already implemented in the build.gradle file. Switch to next project/module...")
        return 0 # Switch to next project or module
    elif(original_build_gradle==None):
        print(f"{project} skipped because an error occurred while trying to integrate the Jacoco and Pitest dependencies into the build.gradle file. Switch to next project/module...")
        return 0 # Switch to next project or module
    for test_type in test_types:
        output_path_failed = f'./output/{project}/TestClasses_{project}_{test_type}.failed' # Indicate that the test type failed due to an error during the execution of the script.
        output_path_failed_gradle = f'./output/{project}/TestClasses_{project}_{test_type}.gradlefailed' # Indicates that all the test classes of the test type failed during the gradle execution.

        restart_test_type = False
        print('\n----')
        print(f"STARTING {test_type} test type\n")
        if test_type == "human":
            # configure the test smell detector
            csv_path_input_test_smell = utils.configure_test_smell_detector(module_df, project)
            # Run the Gradle package command. 
            print("--//loading gradle execution..//")
            if(run_gradle_test_command(path, module_df, system)[0]==True):
                print(f"Package command completed for {project}_{module}\n")
            else:
                print(f"Package command failed for {project}_{module}. Switch to next project/module...\n")
                write_build_gradle(path, original_build_gradle)
                return None # switch to next project

            # Run the test smell detector
            path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None, module)
            if path_csv_result_test_smell is None:
                print("An error occured while trying to run the test smell detector")
            else:
                print("The test smell detector ended successfully")  
            # Retrieve Code Coverage and Cyclomatic Complexity on test classes                measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(f'compiledrepos/{project}', module_df, project, type_project, module)
            if measures_df is None:
                print(f"Switch to next project/module because edit_build_gradle_file() failed for the project {project}_{module_df}")
                write_build_gradle(path, original_build_gradle)
                return 0 # Switch to next project or module
            output_csv_path = utils.generate_output_csv_test_type(project, test_type, None, measures_df, path_csv_result_test_smell, module)
            if output_csv_path is None:
                print("An errore occured while trying to save the test type csv file!")
            else:
                print(f"DataFrame saved to {output_csv_path}")
        elif test_type == 'evosuite':
            module_df_evosuite = module_df.copy() # dataframe for the evosuite test type
            build_gradle_before_evosuite = add_evosuite_build_gradle(path)
            if build_gradle_before_evosuite is None:
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
                test_path = row['Test_Path'].replace('repos/', 'compiledrepos/')
                focal_path = row['Focal_Path'].replace('repos/', 'compiledrepos/')
                last_execution = None # outcome of the last gradle execution, True = Build Success, False = Build Failure
                try:
                    with open(test_path, 'r') as test_file_read:
                        human_test_class = test_file_read.read() # save the human version of the test class
                        dictionary_for_restore[test_path] =  human_test_class
                    os.remove(test_path)      
                except Exception as e:
                    print(f"An error occured while trying to open and read the test_path: {e}")
                    try:
                        write_build_gradle(path, build_gradle_before_evosuite)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True # Switch to next test type
                    break
                
                print("--//loading evosuite generation..//")
                if run_evosuite_generation_gradle(focal_path) == False: # if error while running evosuite
                    print ("An error accored while trying to run the evosuite generation")
                    try:
                        write_build_gradle(path, build_gradle_before_evosuite)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True
                    break # Switch to next test type
                else:
                    print(f"Evosuite generation performed correctly for the {name_focal_class} class")

                # Insert the test class generated by evosuite in the test_path
                test_path_package_evosuite = test_path.split('java/')[1].replace(f'{name_test_class}.java', f'{name_focal_class}_ESTest.java')
                evosuite_test_path = f'evosuite-tests/{test_path_package_evosuite}'
                try:
                    if not os.path.exists(evosuite_test_path):
                        project_df_evosuite = project_df_evosuite[project_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Gradle execution
                        utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Gradle execution
                        utils.remove_evosuite_scaffolding_files(list(test_path))
                        utils.remove_directory_evosuite_command_line()
                        continue 
                    with open(evosuite_test_path, 'r') as evosuite_file:
                        evosuite_content = evosuite_file.read()
                    evosuite_content = evosuite_content.replace(F'public class {name_focal_class}_ESTest', f'public class {name_test_class}') 
                    evosuite_content = evosuite_content.replace('separateClassLoader = true', 'separateClassLoader = false') # when setting separateClassLoader to false, JaCoCo can correctly calculate code coverage
                    with open(test_path, 'w') as test_file:
                        test_file.write(evosuite_content)
                except Exception as e:
                    print(f"An error occured while trying to copy the evosuite class test: {e}")
                    try:
                        write_build_gradle(path, build_gradle_before_evosuite)
                        utils.write_files(dictionary_for_restore)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.remove_directory_evosuite_command_line()
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_directory_evosuite_command_line()
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True
                    break # Switch to next test type 
                
                # Move the scaffolding file in the test class directory
                test_path_package_evosuite_scaffolding = test_path.split('java/')[1].replace(f'{name_test_class}.java', f'{name_focal_class}_ESTest_scaffolding.java')
                evosuite_test_path_scaffolding = f'evosuite-tests/{test_path_package_evosuite_scaffolding}'
                try:
                    shutil.move(evosuite_test_path_scaffolding, test_path.replace('.java', '').replace(f'{name_test_class}', ''))
                except Exception as e:
                    print(f"An error occured while trying to move the scaffolding file: {e}")
                    try:
                        write_build_gradle(path, build_gradle_before_evosuite)
                        utils.write_files(dictionary_for_restore)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.remove_directory_evosuite_command_line()
                        with open(output_path_failed, 'w') as file:
                            pass
                    except Exception as e:
                        write_build_gradle(path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        utils.remove_directory_evosuite_command_line()
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
                    restart_test_type = True
                    break # Switch to next test type 
                
                print("--//loading gradle execution..//")
                if run_gradle_test_command(path, module_df, system)[0]==False: # if error while running gradle
                    print(f"Package command failed for project: {project}_{module}, test type: {test_type}\n")
                    module_df_evosuite = module_df_evosuite[module_df_evosuite['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Gradle execution
                    utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Gradle execution
                    utils.remove_evosuite_scaffolding_files(list(test_path))
                    utils.remove_directory_evosuite_command_line()
                    last_execution = False
                    continue 
                else:
                    last_execution = True
                    print(f"Package command completed for {project}_{module}\n") 


                utils.remove_directory_evosuite_command_line()

            if restart_test_type == True:
                continue
            
            if module_df_evosuite.empty: # If all the test classes provided by Evosuite failed during Gradle execution
                try:
                    with open(output_path_failed_gradle, 'w') as file:
                        pass
                except Exception as e:
                    write_build_gradle(path, original_build_gradle)
                    utils.write_files(dictionary_for_restore)
                    utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                    print(f'An error occured while trying to open output_path_failed_gradle: {e}')
                    sys.exit(1)
            else: # if at least one of the test classes provided by Evosuite runned succesfully during Gradle execution
                # configure the test smell detector
                csv_path_input_test_smell = utils.configure_test_smell_detector(module_df_evosuite, project)
                # Run the test smell detector
                path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, None, module)
                if path_csv_result_test_smell is None:
                    print("An error occured while trying to run the test smell detector")
                else:
                    print("The test smell detector ended successfully")  
                if last_execution == False: # if last gradle execution outcome is False, then I run one more time gradle
                    print("--//loading gradle execution..//")
                    if run_gradle_test_command(path, module, system)[0]==False: # if error while running gradle
                        print('An error occured while trying to execute the final version of test classes.\n')
                        try:
                            write_build_gradle(path, build_gradle_before_evosuite)
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            write_build_gradle(path, original_build_gradle)
                            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                            utils.write_files(dictionary_for_restore)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        continue # Switch to next test type
                    # Retrieve Code Coverage and Cyclomatic Complexity on test classes
                measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(f'compiledrepos/{project}', module_df_evosuite, project, 'Gradle', module)
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
                        write_build_gradle(path, original_build_gradle)
                        utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
                        utils.write_files(dictionary_for_restore)
                        print(f'An error occured while trying to open output_path_failed: {e}')
                        sys.exit(1)
            utils.write_files(dictionary_for_restore)
            utils.remove_evosuite_scaffolding_files(list(dictionary_for_restore.keys()))
            write_build_gradle(path, build_gradle_before_evosuite)



        else:
            # Iterate over each technique
            for technique in techniques: 
                output_path_failed = f'./output/{project}/TestClasses_{project}_{test_type}_{technique}.failed' # Indicate that the test type/technique failed due to an error during the execution of the script or during a call to the API
                output_path_failed_gradle = f'./output/{project}/TestClasses_{project}_{test_type}_{technique}.gradlefailed' # Indicates that all the test classes of the test type failed during the gradle execution.
            
                restart_technique = False 
                print(f"\nProcessing test_type: {test_type}, technique: {technique}")
                module_df_technique = module_df.copy() # dataframe of the current test type and technique
                dictionary_for_restore = {} # dictionary that contains test_path as keys and the respective 'human_test_class' as values. This dictionary is used to restore the test classes to the human version.
                for index, row in module_df_technique.iterrows(): # iterate over each test class and focal class
                    name_focal_class = row['Focal_Class']
                    name_test_class = row['Test_Class']
                    focal_path = row['Focal_Path'].replace('repos/', 'compiledrepos/')
                    test_path = row['Test_Path'].replace('repos/', 'compiledrepos/')
                    last_execution = None # outcome of the last gradle execution, True = Build Success, False = Build Failure
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
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(path, original_build_gradle)
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
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True
                        break

                    print(f"\nDispatching Codex CLI for test_type: {test_type}, technique: {technique}, focal class: {name_focal_class}")
                    package_test_class = utils.find_package(test_path)
                    generated_test_content, messages, usage_metadata = utils.generate_test_with_codex(test_type, technique, focal_class, focal_path, testing_framework, java_version, has_mockito, test_path, name_test_class, project_structure, project_dependencies, package_test_class)
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
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                    
                    try:
                        with open(test_path, 'w') as test_file_write:
                            test_file_write.write(generated_test_content)
                        api_output_file_path = f'output/{project}/response_{test_type}_{technique}_{name_test_class}.java'
                        with open(api_output_file_path, 'w', encoding='utf-8') as api_output_file:
                            api_output_file.write(generated_test_content)
                            print(f"File generated at: {api_output_file_path}")
                    except Exception as e:
                        print(f"An error occured while trying to open and read the test_path: {e}")
                        try:
                            utils.write_files(dictionary_for_restore)
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                        restart_technique = True # Switch to next technique
                        break 
                                

                    # Run the Gradle package command
                    print("--//loading gradle execution..//")
                    esito, errori = run_gradle_test_command(path, module_df, system)
                    if not esito and correct:
                        chance_result = False
                        iterations_to_pass = 0
                        for num_chance in range(1, 5):
                            if chance_result:
                                break
                            chance_result, errori, correction_usage = errorCorrection.correct_errors(project, test_type, technique, test_path, focal_path, path, module_df, system, messages, errori, dictionary_for_restore, num_chance, "Gradle")
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
                        module_df_technique = module_df_technique[module_df_technique['Test_Path'] != row['Test_Path']] # Delete the row from the DataFrame that corresponds to the test class causing an error during Gradle execution
                        utils.write_file(test_path, human_test_class) # Restore to human version the test class causing an error during Gradle execution
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 6, total_prompt_tokens, total_completion_tokens, 0)
                        last_execution = False
                        continue
                    else:
                        last_execution = True
                        print(f"Package command completed for {project}_{module}\n")
                        record_tracking_metrics(name_test_class, test_path, test_type, technique, 0, total_prompt_tokens, total_completion_tokens, 0)

                if restart_technique == True:
                    continue

                    
                if module_df_technique.empty: # If all the test classes provided by the API failed during Gradle execution
                    try:
                        with open(output_path_failed_gradle, 'w') as file:
                            pass
                    except Exception as e:
                        utils.write_files(dictionary_for_restore)
                        write_build_gradle(path, original_build_gradle)
                        print(f'An error occured while trying to open output_path_failed_gradle: {e}')
                        sys.exit()
                else: # if at least one of the test classes provided by the API runned succesfully during Gradle execution
                    # configure the test smell detector
                    csv_path_input_test_smell = utils.configure_test_smell_detector(module_df_technique, project)
                    # Run the test smell detector
                    path_csv_result_test_smell = utils.run_test_smell_detector(csv_path_input_test_smell, project, test_type, technique, module)
                    if path_csv_result_test_smell is None:
                        print("An error occured while trying to run the test smell detector")
                    else:
                        print("The test smell detector ended successfully")  
                    if last_execution == False: # if last gradle execution outcome is False, then I run one more time gradle
                        print("--//loading gradle execution..//")
                        if run_gradle_test_command(path, module_df, system)[0]==False: # if error while running gradle
                            print('An error occured while trying to execute the final version of test classes.\n')
                            try:
                                utils.write_files(dictionary_for_restore)
                                with open(output_path_failed, 'w') as file:
                                    pass
                            except Exception as e:
                                utils.write_files(dictionary_for_restore)
                                write_build_gradle(path, original_build_gradle)
                                print(f'An error occured while trying to open output_path_failed: {e}')
                                sys.exit(1)
                            continue  # switch to next technique      
                    # Retrieve Code Coverage and Cyclomatic Complexity on test classes
                    measures_df = utils.retrieve_code_coverage_and_cyclomatic_complexity(f'compiledrepos/{project}', module_df_technique, project, 'Gradle', module)
                    if measures_df is not None:
                        output_csv_path = utils.generate_output_csv_test_type(project, test_type, technique, measures_df, path_csv_result_test_smell, module)
                        if output_csv_path is None:
                            print("An errore occured while trying to save the test type csv file!")
                        else:
                            print(f"DataFrame saved to {output_csv_path}")
                    else:
                        print(f"An occured while trying to retrieve data coverage of the project {project}_{module}.")
                        try:
                            with open(output_path_failed, 'w') as file:
                                pass
                        except Exception as e:
                            utils.write_files(dictionary_for_restore)
                            write_build_gradle(path, original_build_gradle)
                            print(f'An error occured while trying to open output_path_failed: {e}')
                            sys.exit(1)
                    utils.write_files(dictionary_for_restore)
    write_build_gradle(path, original_build_gradle)
