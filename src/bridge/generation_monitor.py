import asyncio
import time
from enum import Enum
from typing import Dict, Any, Optional, Tuple, List
from playwright.async_api import Page
from src.adapters.ai_studio_adapter import AIStudioAdapter
from src.safety.retry import RetryBudgetManager, RetryBudgetExhaustedError
from src.safety.timeout import TimeoutController, OperationTimeoutError
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class GenerationState(Enum):
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    RETRY_AVAILABLE = "RETRY_AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NETWORK_FAILURE = "NETWORK_FAILURE"
    UNCERTAIN = "UNCERTAIN"


class GenerationMonitor:
    """
    Monitors Gemini / AI Studio generation state using multi-signal detection:
    - Network error banners -> NETWORK_FAILURE
    - Error elements -> ERROR
    - Stop button / progress indicator -> GENERATING
    - Stopped indicator -> STOPPED
    - Send button + turns count > 0 -> COMPLETED (Precedence over Retry UI!)
    - Retry button present + no stop button -> RETRY_AVAILABLE
    - Zero turns + composer missing -> UNAVAILABLE
    - Fallback -> UNCERTAIN
    """

    def __init__(
        self,
        poll_interval_sec: Optional[float] = None,
        stable_samples_required: Optional[int] = None,
        generation_timeout_sec: Optional[float] = None,
    ):
        self.logger = get_logger()
        cfg = get_config()
        poll_cfg = cfg.settings.get("polling", {})
        timeout_cfg = cfg.settings.get("timeouts", {})

        self.poll_interval_sec = poll_interval_sec or poll_cfg.get("interval_sec", 2.0)
        self.stable_samples_required = stable_samples_required or poll_cfg.get(
            "stable_completion_sample_count", 3
        )
        self.timeout_sec = generation_timeout_sec or timeout_cfg.get(
            "generation_timeout_sec", 180.0
        )

    async def detect_state(self, page: Page) -> Tuple[GenerationState, Dict[str, Any]]:
        """
        Inspects page DOM signals to evaluate current generation state.
        Returns: (GenerationState, diagnostic_details: dict)
        """
        adapter = AIStudioAdapter(page)
        inspection = await adapter.inspect()

        stop_present = inspection.get("stop_button_present", False)
        indicator_present = inspection.get("generation_indicator_present", False)
        send_present = inspection.get("send_button_present", False)
        retry_present = inspection.get("retry_button_present", False)
        error_present = inspection.get("error_message_present", False)
        stopped_present = inspection.get("stopped_indicator_present", False)
        network_present = inspection.get("network_error_present", False)
        composer_present = inspection.get("composer_present", False)
        turns_count = inspection.get("conversation_turns_count", 0)

        details = {
            "stop_present": stop_present,
            "indicator_present": indicator_present,
            "send_present": send_present,
            "retry_present": retry_present,
            "error_present": error_present,
            "stopped_present": stopped_present,
            "network_present": network_present,
            "composer_present": composer_present,
            "turns_count": turns_count,
        }

        # Signal 1: Network failure banner
        if network_present:
            return GenerationState.NETWORK_FAILURE, details

        # Signal 2: Explicit Error banner
        if error_present:
            return GenerationState.ERROR, details

        # Signal 3: Stop button or indicator active -> GENERATING
        if stop_present or indicator_present:
            return GenerationState.GENERATING, details

        # Signal 4: Explicit Stopped indicator
        if stopped_present:
            return GenerationState.STOPPED, details

        # Signal 5: Completed response (takes precedence over retry button!)
        if send_present and turns_count > 0 and not retry_present:
            return GenerationState.COMPLETED, details

        # Signal 6: Retry button present and no stop button
        if retry_present and not stop_present:
            return GenerationState.RETRY_AVAILABLE, details

        if send_present and turns_count > 0:
            return GenerationState.COMPLETED, details

        # Signal 7: Unavailable
        if not composer_present and turns_count == 0:
            return GenerationState.UNAVAILABLE, details

        return GenerationState.UNCERTAIN, details

    async def wait_for_completion(
        self, page: Page, max_wait_sec: Optional[float] = None
    ) -> Tuple[GenerationState, str, Dict[str, Any]]:
        """
        Polls AI Studio page until output text stabilizes across `stable_samples_required` consecutive checks
        or until timeout.
        """
        timeout_limit = max_wait_sec or self.timeout_sec
        timeout_ctrl = TimeoutController(timeout_sec=timeout_limit)
        timeout_ctrl.start()

        adapter = AIStudioAdapter(page)
        sample_history: List[str] = []

        while True:
            try:
                timeout_ctrl.check()
            except OperationTimeoutError:
                self.logger.warning("Generation wait timed out.")
                return GenerationState.UNCERTAIN, "", {"error": "Generation wait timeout"}

            state, details = await self.detect_state(page)

            if state == GenerationState.GENERATING:
                sample_history.clear()
            elif state == GenerationState.COMPLETED:
                latest_text = await adapter.extract_latest_response_text()
                if latest_text:
                    sample_history.append(latest_text)
                    if len(sample_history) >= self.stable_samples_required:
                        # Check if all samples in history are identical
                        if len(set(sample_history[-self.stable_samples_required :])) == 1:
                            return GenerationState.COMPLETED, latest_text, details
                else:
                    sample_history.clear()
            elif state in (GenerationState.ERROR, GenerationState.STOPPED, GenerationState.NETWORK_FAILURE):
                return state, "", details

            await asyncio.sleep(self.poll_interval_sec)


class GeminiRetryManager:
    """Handles safe retry of Gemini generation when permitted."""

    def __init__(self):
        self.logger = get_logger()
        self.retry_budget = RetryBudgetManager()

    def can_retry(
        self,
        current_state: GenerationState,
        retry_count: int,
        response_text_exists: bool,
    ) -> bool:
        """
        Validates whether retry is permitted.
        Rules:
        - Explicit retriable state (RETRY_AVAILABLE);
        - Retry budget remains;
        - No completed response text exists;
        - No uncertain state.
        """
        if current_state != GenerationState.RETRY_AVAILABLE:
            return False

        if response_text_exists:
            return False

        try:
            self.retry_budget.check_and_increment(retry_count)
            return True
        except RetryBudgetExhaustedError:
            return False
