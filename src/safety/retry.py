from typing import Optional
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class RetryBudgetExhaustedError(Exception):
    """Raised when retry attempt exceeds max_retries limit."""

    pass


class RetryBudgetManager:
    """Manages retry limits for transient failures."""

    def __init__(self, max_retries: Optional[int] = None):
        self.logger = get_logger()
        cfg = get_config()
        self.max_retries = max_retries or cfg.settings.get("limits", {}).get(
            "max_retries", 3
        )

    def check_and_increment(self, current_retry_count: int) -> int:
        """
        Validates whether another retry attempt is permitted.
        Returns the incremented retry count or raises RetryBudgetExhaustedError.
        """
        if current_retry_count >= self.max_retries:
            raise RetryBudgetExhaustedError(
                f"Retry budget exhausted: current retries {current_retry_count} >= max {self.max_retries}."
            )
        return current_retry_count + 1
