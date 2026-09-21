import unittest
from unittest.mock import AsyncMock, MagicMock
import asyncio

from src.bridge.generation_monitor import (
    GenerationMonitor,
    GenerationState,
    GeminiRetryManager,
)


class TestPhase7GenerationMonitorAndRetry(unittest.IsolatedAsyncioTestCase):

    async def test_detect_generating_state(self):
        mock_page = AsyncMock()

        async def mock_query_selector(sel):
            if "Stop" in sel or "spinner" in sel or "progress" in sel or "busy" in sel:
                return AsyncMock()  # Stop button / loading indicator present
            return None

        mock_page.query_selector.side_effect = mock_query_selector

        monitor = GenerationMonitor()
        state, details = await monitor.detect_state(mock_page)

        self.assertEqual(state, GenerationState.GENERATING)
        self.assertTrue(details["stop_present"])

    async def test_detect_completed_state(self):
        mock_page = AsyncMock()
        mock_turn = AsyncMock()
        mock_turn.inner_text.return_value = "Completed Gemini Response Text"

        async def mock_query_selector_all(sel):
            if "turn" in sel:
                return [mock_turn]
            return []

        async def mock_query_selector(sel):
            if "run" in sel.lower() or "send" in sel.lower():
                return AsyncMock()  # Send button present
            return None  # No stop button or loading indicator

        mock_page.query_selector_all.side_effect = mock_query_selector_all
        mock_page.query_selector.side_effect = mock_query_selector

        monitor = GenerationMonitor()
        state, details = await monitor.detect_state(mock_page)

        self.assertEqual(state, GenerationState.COMPLETED)
        self.assertFalse(details["stop_present"])
        self.assertTrue(details["send_present"])
        self.assertEqual(details["turns_count"], 1)

    async def test_wait_for_completion_text_stability(self):
        mock_page = AsyncMock()
        mock_turn = AsyncMock()
        mock_turn.inner_text.return_value = "Stable Output Text"

        async def mock_query_selector_all(sel):
            if "turn" in sel:
                return [mock_turn]
            return []

        async def mock_query_selector(sel):
            if "run" in sel.lower() or "send" in sel.lower() or "textarea" in sel.lower():
                return AsyncMock()
            return None

        mock_page.query_selector_all.side_effect = mock_query_selector_all
        mock_page.query_selector.side_effect = mock_query_selector

        monitor = GenerationMonitor(
            poll_interval_sec=0.001, stable_samples_required=3, generation_timeout_sec=5.0
        )

        state, text, details = await monitor.wait_for_completion(mock_page, max_wait_sec=5.0)

        self.assertEqual(state, GenerationState.COMPLETED)
        self.assertEqual(text, "Stable Output Text")

    def test_retry_manager_rules(self):
        retry_mgr = GeminiRetryManager()

        # Case 1: RETRY_AVAILABLE, no response text, budget remains -> True
        can_r1 = retry_mgr.can_retry(
            current_state=GenerationState.RETRY_AVAILABLE,
            retry_count=0,
            response_text_exists=False,
        )
        self.assertTrue(can_r1)

        # Case 2: Response text exists -> False
        can_r2 = retry_mgr.can_retry(
            current_state=GenerationState.RETRY_AVAILABLE,
            retry_count=0,
            response_text_exists=True,
        )
        self.assertFalse(can_r2)

        # Case 3: Exhausted budget -> False
        can_r3 = retry_mgr.can_retry(
            current_state=GenerationState.RETRY_AVAILABLE,
            retry_count=3,  # Max retries is 3
            response_text_exists=False,
        )
        self.assertFalse(can_r3)

        # Case 4: Ambiguous state -> False
        can_r4 = retry_mgr.can_retry(
            current_state=GenerationState.UNCERTAIN,
            retry_count=0,
            response_text_exists=False,
        )
        self.assertFalse(can_r4)


if __name__ == "__main__":
    unittest.main()
