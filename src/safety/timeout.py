import time
from typing import Optional
from src.utils.config_loader import get_config


class OperationTimeoutError(Exception):
    """Raised when an operation exceeds allocated time limit."""

    pass


class TimeoutController:
    """Monitors operation durations and enforces timeout limits."""

    def __init__(self, timeout_sec: Optional[float] = None):
        cfg = get_config()
        self.timeout_sec = timeout_sec or cfg.settings.get("timeouts", {}).get(
            "generation_timeout_sec", 180
        )
        self.start_time: Optional[float] = None

    def start(self) -> None:
        self.start_time = time.time()

    def check(self) -> float:
        """
        Checks elapsed time.
        Raises OperationTimeoutError if timeout_sec has elapsed.
        Returns elapsed time in seconds.
        """
        if self.start_time is None:
            self.start()
            return 0.0

        elapsed = time.time() - self.start_time
        if elapsed > self.timeout_sec:
            raise OperationTimeoutError(
                f"Operation timed out after {elapsed:.1f}s (max allowed {self.timeout_sec}s)."
            )
        return elapsed
