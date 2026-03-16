import os
import glob
import shutil
import subprocess
import sys
from pathlib import Path


def _configure_java_8():
    existing_java_8 = os.environ.get("JAVA_HOME_8")
    if existing_java_8:
        os.environ["JAVA_HOME"] = existing_java_8
        java_bin = str(Path(existing_java_8) / "bin")
        os.environ["PATH"] = java_bin + os.pathsep + os.environ.get("PATH", "")
        print(f"Using Java 8 Path: {os.environ['JAVA_HOME_8']}")
        return

    search_patterns = [
        r"C:\Program Files\Eclipse Adoptium\jdk-8.0*",
        r"C:\Program Files\AdoptOpenJDK\jdk-8.0*",
    ]
    candidates = []
    for pattern in search_patterns:
        candidates.extend(glob.glob(pattern))

    candidates = sorted(
        [candidate for candidate in candidates if Path(candidate).is_dir()],
        reverse=True,
    )
    if not candidates:
        print("Warning: no Java 8 installation was found under the default Temurin locations.")
        return

    selected_java_home = candidates[0]
    os.environ["JAVA_HOME_8"] = selected_java_home
    os.environ["JAVA_HOME"] = selected_java_home
    java_bin = str(Path(selected_java_home) / "bin")
    os.environ["PATH"] = java_bin + os.pathsep + os.environ.get("PATH", "")
    print(f"Using Java 8 Path: {os.environ['JAVA_HOME_8']}")


def _configure_maven(workspace_root):
    existing_maven = shutil.which("mvn.cmd") or shutil.which("mvn")
    if existing_maven:
        print(f"Using Maven Path: {existing_maven}")
        return

    search_patterns = [
        str(workspace_root / "tools" / "apache-maven-*" / "bin" / "mvn.cmd"),
        str(workspace_root / "tools" / "apache-maven-*" / "bin" / "mvn"),
        r"C:\Program Files\apache-maven-*\bin\mvn.cmd",
    ]
    candidates = []
    for pattern in search_patterns:
        candidates.extend(glob.glob(pattern))

    candidates = sorted(
        [candidate for candidate in candidates if Path(candidate).is_file()],
        reverse=True,
    )
    if not candidates:
        print("Warning: Maven was not found on PATH or in the workspace tools directory.")
        return

    selected_maven = Path(candidates[0])
    maven_home = str(selected_maven.parent.parent)
    os.environ["MAVEN_HOME"] = maven_home
    os.environ["M2_HOME"] = maven_home
    os.environ["PATH"] = str(selected_maven.parent) + os.pathsep + os.environ.get("PATH", "")
    print(f"Using Maven Path: {selected_maven}")


def main():
    script_directory = Path(__file__).resolve().parent
    workspace_root = script_directory.parent
    target_json = workspace_root / "Classes2Test" / "42949039_429.json"
    smoke_log_path = workspace_root / "smoke_current.log"

    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "java.exe", "/T"],
            capture_output=True,
            check=False,
        )
    except Exception:
        pass

    _configure_java_8()
    _configure_maven(workspace_root)

    env = os.environ.copy()
    env["AGONE_SMOKE_TEST"] = "1"
    env.setdefault("AGONE_SMOKE_TARGET_JSON", str(target_json))
    env.setdefault("PYTHONUNBUFFERED", "1")

    command = [sys.executable, str(script_directory / "agone_test.py")]
    print(f"Launching smoke test with target JSON: {env['AGONE_SMOKE_TARGET_JSON']}")
    print(f"Writing smoke log to: {smoke_log_path}")
    print("Expected sequence: baseline -> mutation -> failure -> Codex repair -> final CSV write")
    with open(smoke_log_path, "w", encoding="utf-8", errors="replace") as smoke_log:
        completed_process = subprocess.run(
            command,
            cwd=str(workspace_root),
            env=env,
            check=False,
            stdout=smoke_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    raise SystemExit(completed_process.returncode)


if __name__ == "__main__":
    main()
