import os
import re
import shutil
import stat
import time
from pathlib import Path


_WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_COMPILED_PROJECT_PATTERN = re.compile(r"(^|/)compiledrepos/([0-9]+)(?=/|$)")
_COMPILED_WORKER_PROJECT_PATTERN = re.compile(
    r"(^|/)compiledrepos/worker_[A-Za-z0-9_-]+/([0-9]+)(?=/|$)"
)
_OUTPUT_WORKER_PATTERN = re.compile(r"(^|/)output/worker_[A-Za-z0-9_-]+(?=/|$)")
_OUTPUT_ROOT_PATTERN = re.compile(r"(^|/)output(?=/|$)")


def _sanitize_worker_id(raw_worker_id):
    candidate = str(raw_worker_id or "default").strip()
    if _WORKER_ID_PATTERN.match(candidate):
        return candidate
    return "default"


def get_worker_id():
    return _sanitize_worker_id(os.environ.get("AGONE_WORKER_ID", "default"))


def _handle_remove_readonly(func, target_path, _):
    try:
        os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)
        func(target_path)
    except Exception:
        pass


def remove_path_force(path_to_remove, retries=4, delay_seconds=0.5):
    target_path = Path(path_to_remove)
    for _ in range(max(1, retries)):
        if not target_path.exists() and not target_path.is_symlink():
            return True
        try:
            if target_path.is_symlink() or target_path.is_file():
                target_path.unlink(missing_ok=True)
            else:
                shutil.rmtree(str(target_path), onerror=_handle_remove_readonly)
        except FileNotFoundError:
            return True
        except Exception:
            pass
        if not target_path.exists() and not target_path.is_symlink():
            return True
        time.sleep(max(0.0, delay_seconds))
    return not target_path.exists() and not target_path.is_symlink()


class PathContext:
    def __init__(self, workspace_root=None, worker_id=None):
        if workspace_root is None:
            workspace_root = Path(__file__).resolve().parent.parent
        self.workspace_root = Path(workspace_root).resolve()
        self.worker_id = _sanitize_worker_id(worker_id or os.environ.get("AGONE_WORKER_ID", "default"))
        self.worker_directory = f"worker_{self.worker_id}"

    def get_compiled_root(self):
        return str(self.workspace_root / "compiledrepos" / self.worker_directory)

    def get_compiled_repo_path(self, project_id):
        return str(Path(self.get_compiled_root()) / str(project_id))

    def get_output_path(self):
        return str(self.workspace_root / "output" / self.worker_directory)

    def get_project_output_path(self, project_id):
        return str(Path(self.get_output_path()) / str(project_id))

    def get_log_path(self, log_name):
        return str(Path(self.get_output_path()) / str(log_name))

    def ensure_worker_directories(self):
        Path(self.get_compiled_root()).mkdir(parents=True, exist_ok=True)
        Path(self.get_output_path()).mkdir(parents=True, exist_ok=True)

    def to_worker_compiled_path(self, project_id, raw_path):
        if raw_path is None:
            return None
        text = str(raw_path).strip()
        if not text or text.lower() == "nan":
            return None

        normalized = text.replace("\\", "/")
        if os.path.isabs(text):
            absolute = os.path.abspath(text)
            normalized_absolute = absolute.replace("\\", "/")
            # Rewrite legacy compiledrepos/<project>/... to compiledrepos/worker_<id>/<project>/...
            if _COMPILED_WORKER_PROJECT_PATTERN.search(normalized_absolute):
                return absolute
            legacy_match = _COMPILED_PROJECT_PATTERN.search(normalized_absolute)
            if legacy_match:
                project_segment = legacy_match.group(2)
                suffix = normalized_absolute.split(f"/compiledrepos/{project_segment}/", 1)[1]
                return os.path.abspath(
                    os.path.join(self.get_compiled_repo_path(project_segment), suffix.replace("/", os.sep))
                )
            return absolute

        if normalized.startswith("compiledrepos/worker_"):
            return normalized
        if normalized.startswith("compiledrepos/"):
            parts = normalized.split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                project_segment = parts[1]
                remainder = "/".join(parts[2:])
                base = f"compiledrepos/{self.worker_directory}/{project_segment}"
                return f"{base}/{remainder}" if remainder else base
            return normalized
        if normalized.startswith("repos/"):
            parts = normalized.split("/")
            if len(parts) >= 2 and parts[1].isdigit():
                project_segment = parts[1]
                remainder = "/".join(parts[2:])
            else:
                project_segment = str(project_id)
                remainder = "/".join(parts[1:])
            base = f"compiledrepos/{self.worker_directory}/{project_segment}"
            return f"{base}/{remainder}" if remainder else base

        if project_id is None:
            return normalized.lstrip("/")
        return f"compiledrepos/{self.worker_directory}/{project_id}/{normalized.lstrip('/')}"

    def to_worker_output_path(self, raw_path):
        if raw_path is None:
            return None
        text = str(raw_path).strip()
        if not text or text.lower() == "nan":
            return None
        normalized = text.replace("\\", "/")

        if os.path.isabs(text):
            absolute = os.path.abspath(text)
            normalized_absolute = absolute.replace("\\", "/")
            if _OUTPUT_WORKER_PATTERN.search(normalized_absolute):
                return absolute
            if _OUTPUT_ROOT_PATTERN.search(normalized_absolute):
                return os.path.abspath(
                    _OUTPUT_ROOT_PATTERN.sub(rf"\1output/{self.worker_directory}", normalized_absolute, count=1)
                )
            return absolute

        if normalized.startswith("output/worker_"):
            return normalized
        if normalized == "output":
            return f"output/{self.worker_directory}"
        if normalized.startswith("output/"):
            suffix = normalized.split("output/", 1)[1]
            return f"output/{self.worker_directory}/{suffix}".rstrip("/")
        return normalized

    def extract_project_id(self, any_path):
        if any_path is None:
            return None
        normalized = os.path.abspath(str(any_path)).replace("\\", "/")
        worker_match = _COMPILED_WORKER_PROJECT_PATTERN.search(normalized)
        if worker_match:
            return worker_match.group(2)
        legacy_match = _COMPILED_PROJECT_PATTERN.search(normalized)
        if legacy_match:
            return legacy_match.group(2)
        return None


_PATH_CONTEXT = PathContext()


def get_path_context():
    return _PATH_CONTEXT
