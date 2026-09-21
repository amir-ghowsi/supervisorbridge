import os
import json
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, List
from src.state.task_state import ActiveTaskData, CURRENT_SCHEMA_VERSION, TaskState
from src.utils.paths import resolve_path, ensure_dir
from src.utils.logger import get_logger


class StateError(Exception):
    """Base state repository exception."""

    pass


class CorruptStateError(StateError):
    """Raised when persisted state is corrupted or unparseable."""

    pass


class SchemaMismatchError(StateError):
    """Raised when persisted state schema version is unsupported."""

    pass


class StateRepository:
    """
    Manages durable state persistence for active tasks and completed ledger.
    Guarantees atomic file operations across platforms including Windows.
    Fails closed on corrupt state.
    """

    def __init__(self, state_dir: Optional[str | Path] = None):
        self.logger = get_logger()
        if state_dir:
            self.state_dir = ensure_dir(state_dir)
        else:
            self.state_dir = ensure_dir("data/state")

        self.active_task_file = self.state_dir / "active_task.json"
        self.completed_ledger_file = self.state_dir / "completed_ledger.json"

    def _atomic_write_json(self, filepath: Path, data: Dict[str, Any]) -> None:
        """
        Writes data to a temporary file, flushes/fsyncs, and performs an atomic replace.
        Fully compatible with Windows and POSIX.
        """
        dir_name = filepath.parent
        dir_name.mkdir(parents=True, exist_ok=True)

        content = json.dumps(data, indent=2, ensure_ascii=False)

        # Create temporary file in same directory to ensure same filesystem for atomic move
        fd, tmp_path_str = tempfile.mkstemp(
            dir=dir_name, prefix=f".{filepath.name}_tmp_", suffix=".tmp"
        )
        tmp_path = Path(tmp_path_str)

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace (works on Windows & Linux)
            os.replace(tmp_path, filepath)
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            raise StateError(f"Failed atomic write to {filepath}: {e}") from e

    def load_active_task(self) -> Optional[ActiveTaskData]:
        """
        Loads the active task state from disk.
        Returns None if no active task file exists.
        Fails closed with CorruptStateError or SchemaMismatchError if corrupted.
        """
        if not self.active_task_file.exists():
            return None

        try:
            with open(self.active_task_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    raise CorruptStateError(f"State file {self.active_task_file} is empty.")
                data = json.loads(content)
        except json.JSONDecodeError as e:
            raise CorruptStateError(
                f"State file {self.active_task_file} contains invalid JSON: {e}"
            ) from e
        except Exception as e:
            if isinstance(e, (CorruptStateError, SchemaMismatchError)):
                raise
            raise StateError(f"Error reading state file {self.active_task_file}: {e}") from e

        if not isinstance(data, dict):
            raise CorruptStateError(
                f"State file {self.active_task_file} top-level element is not an object."
            )

        schema_version = data.get("schema_version")
        if schema_version != CURRENT_SCHEMA_VERSION:
            raise SchemaMismatchError(
                f"Schema mismatch in {self.active_task_file}: expected '{CURRENT_SCHEMA_VERSION}', got '{schema_version}'."
            )

        try:
            return ActiveTaskData.from_dict(data)
        except Exception as e:
            raise CorruptStateError(
                f"Failed to instantiate ActiveTaskData from {self.active_task_file}: {e}"
            ) from e

    def save_active_task(self, task: ActiveTaskData) -> None:
        """Saves active task to disk atomically."""
        self._atomic_write_json(self.active_task_file, task.to_dict())

    def clear_active_task(self) -> None:
        """Removes the active task file cleanly."""
        if self.active_task_file.exists():
            try:
                self.active_task_file.unlink()
            except Exception as e:
                raise StateError(f"Failed to delete active task file {self.active_task_file}: {e}")

    def archive_to_ledger(self, task: ActiveTaskData) -> None:
        """
        Appends a finished/terminal task to the completed ledger idempotently and clears active task.
        Prevents duplicate entries if crash occurs after ledger write but before active task cleanup.
        """
        ledger = self.load_completed_ledger()
        task_dict = task.to_dict()

        # Check if task already present in ledger by task_id and phase
        existing_idx = -1
        for idx, entry in enumerate(ledger):
            if entry.get("task_id") == task.task_id and entry.get("phase") == task.phase:
                existing_idx = idx
                break

        if existing_idx != -1:
            ledger[existing_idx] = task_dict
        else:
            ledger.append(task_dict)

        self._atomic_write_json(self.completed_ledger_file, {"tasks": ledger})
        self.clear_active_task()

    def load_completed_ledger(self) -> List[Dict[str, Any]]:
        """
        Loads completed ledger history.
        Fails closed with CorruptStateError if the ledger file exists but is corrupted or malformed.
        """
        if not self.completed_ledger_file.exists():
            return []

        try:
            with open(self.completed_ledger_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    raise CorruptStateError(f"Completed ledger file {self.completed_ledger_file} is empty.")
                data = json.loads(content)
                if not isinstance(data, dict) or "tasks" not in data or not isinstance(data["tasks"], list):
                    raise CorruptStateError(
                        f"Completed ledger file {self.completed_ledger_file} must contain a dict with a 'tasks' list."
                    )
                return data["tasks"]
        except json.JSONDecodeError as e:
            raise CorruptStateError(
                f"Completed ledger file {self.completed_ledger_file} contains invalid JSON: {e}"
            ) from e
        except CorruptStateError:
            raise
        except Exception as e:
            raise CorruptStateError(f"Error reading completed ledger: {e}") from e
