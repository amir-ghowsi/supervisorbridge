import os
import json
import time
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any
from src.utils.paths import ensure_dir
from src.utils.logger import get_logger


class CheckpointError(Exception):
    """Raised when checkpoint creation or validation fails."""

    pass


class CheckpointManager:
    """
    Creates explicit execution checkpoints before external mutations.
    Will NOT create checkpoints automatically on module import or instantiation.
    """

    def __init__(self, state_dir: Optional[str | Path] = None):
        self.logger = get_logger()
        if state_dir:
            self.state_dir = ensure_dir(state_dir)
        else:
            self.state_dir = ensure_dir("data/state")

        self.checkpoint_file = self.state_dir / "checkpoint.json"

    def create_checkpoint(
        self,
        task_id: str,
        operation_id: str,
        state_data: Dict[str, Any],
        idempotency_key: str,
    ) -> Path:
        """Explicitly writes a pre-mutation state checkpoint."""
        checkpoint_payload = {
            "checkpoint_version": "1.0.0",
            "timestamp": time.time(),
            "task_id": task_id,
            "operation_id": operation_id,
            "idempotency_key": idempotency_key,
            "task_state": state_data,
        }

        fd, tmp_str = tempfile.mkstemp(
            dir=self.state_dir, prefix=".ckpt_tmp_", suffix=".tmp"
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(checkpoint_payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.checkpoint_file)
            self.logger.info(f"Checkpoint created for task '{task_id}', op '{operation_id}'.")
            return self.checkpoint_file
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise CheckpointError(f"Failed to write checkpoint: {e}") from e

    def load_checkpoint(self) -> Optional[Dict[str, Any]]:
        """Loads the current checkpoint if present."""
        if not self.checkpoint_file.exists():
            return None
        try:
            with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise CheckpointError(f"Corrupt checkpoint file {self.checkpoint_file}: {e}") from e

    def clear_checkpoint(self) -> None:
        """Removes the checkpoint file."""
        if self.checkpoint_file.exists():
            try:
                self.checkpoint_file.unlink()
            except Exception as e:
                self.logger.error(f"Failed to clear checkpoint: {e}")
