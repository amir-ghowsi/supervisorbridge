import time
from typing import Optional, Dict
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class LoopGuardViolationError(Exception):
    """Raised when loop execution limit or repeated operation loop is detected."""

    pass


class LoopGuard:
    """Tracks loop iterations and repeated identical operations to prevent infinite loops."""

    def __init__(self, max_iterations: Optional[int] = None):
        self.logger = get_logger()
        cfg = get_config()
        self.max_iterations = max_iterations or cfg.settings.get("limits", {}).get(
            "max_loop_iterations", 10
        )
        self.iteration_count = 0
        self.last_operation_key: Optional[str] = None
        self.repeated_operation_count = 0

    def record_cycle(self, operation_signature: Optional[str] = None) -> None:
        """
        Records a runtime loop iteration.
        Raises LoopGuardViolationError if limits are breached.
        """
        self.iteration_count += 1
        if self.iteration_count > self.max_iterations:
            raise LoopGuardViolationError(
                f"Loop limit exceeded: {self.iteration_count} iterations > max allowed ({self.max_iterations})."
            )

        if operation_signature:
            if operation_signature == self.last_operation_key:
                self.repeated_operation_count += 1
                if self.repeated_operation_count >= 3:
                    raise LoopGuardViolationError(
                        f"Repeated identical operation loop detected: signature '{operation_signature}' executed {self.repeated_operation_count} consecutive times."
                    )
            else:
                self.last_operation_key = operation_signature
                self.repeated_operation_count = 1

    def reset(self) -> None:
        """Resets loop counter state."""
        self.iteration_count = 0
        self.last_operation_key = None
        self.repeated_operation_count = 0
