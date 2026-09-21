import os
import json
import time
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from src.utils.paths import ensure_dir
from src.utils.logger import get_logger


class EmergencyStopEngagedError(Exception):
    """Raised when an operation is blocked due to active Emergency Stop."""

    pass


class EmergencyStopManager:
    """
    Manages persistent Emergency Stop state.
    Survives system restart. Blocks all browser mutating actions.
    """

    def __init__(self, state_dir: Optional[str | Path] = None):
        self.logger = get_logger()
        if state_dir:
            self.state_dir = ensure_dir(state_dir)
        else:
            self.state_dir = ensure_dir("data/state")

        self.estop_file = self.state_dir / "emergency_stop.json"

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        fd, tmp_str = tempfile.mkstemp(
            dir=self.state_dir, prefix=".estop_tmp_", suffix=".tmp"
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.estop_file)
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise RuntimeError(f"Failed to update emergency stop file: {e}") from e

    def is_engaged(self) -> bool:
        """Checks if Emergency Stop is engaged on disk."""
        if not self.estop_file.exists():
            return False
        try:
            with open(self.estop_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return bool(data.get("engaged", False))
        except Exception as e:
            self.logger.error(f"Error reading emergency stop state: {e}. Failing safe (engaged).")
            return True  # Fail safe

    def engage(self, reason: str = "Manual Emergency Stop engaged") -> None:
        """Engages Emergency Stop persistently."""
        data = {
            "engaged": True,
            "reason": reason,
            "timestamp": time.time(),
        }
        self._atomic_write(data)
        self.logger.warning(f"EMERGENCY STOP ENGAGED: {reason}")

    def release(self, reason: str = "Manual Emergency Stop released") -> None:
        """Releases Emergency Stop persistently."""
        data = {
            "engaged": False,
            "reason": reason,
            "timestamp": time.time(),
        }
        self._atomic_write(data)
        self.logger.info(f"Emergency Stop released: {reason}")

    def get_status(self) -> Dict[str, Any]:
        """Returns current emergency stop status."""
        if not self.estop_file.exists():
            return {"engaged": False, "reason": "No emergency stop record"}
        try:
            with open(self.estop_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"engaged": True, "reason": f"Corrupt estop file: {e}"}

    def assert_not_engaged(self) -> None:
        """Raises EmergencyStopEngagedError if emergency stop is active."""
        if self.is_engaged():
            status = self.get_status()
            raise EmergencyStopEngagedError(
                f"Emergency Stop is active! Reason: {status.get('reason')}"
            )
