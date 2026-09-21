from typing import Dict, Any, List, Optional
from playwright.async_api import Page
from src.adapters.base_adapter import BaseAdapter


class AIStudioAdapter(BaseAdapter):
    """Read-only adapter for Google AI Studio page DOM inspection."""

    async def inspect(self) -> Dict[str, Any]:
        """Inspects AI Studio DOM elements without mutating page state."""
        info = await self.get_page_info()
        sel = self.selectors.get("ai_studio", {})

        composer_selector = sel.get("composer", "textarea")
        send_button_selector = sel.get("send_button", "button")
        stop_button_selector = sel.get("stop_button", "button")
        turns_selector = sel.get("conversation_turns", "div.turn")
        gen_indicator_selector = sel.get("generation_indicator", ".loading-spinner")
        retry_selector = sel.get("retry_button", "button.retry")
        error_selector = sel.get("error_message", ".error-message")
        stopped_selector = sel.get("stopped_indicator", ".generation-stopped")
        network_selector = sel.get("network_error", ".network-error")

        composer_present = False
        try:
            c = await self.page.query_selector(composer_selector)
            composer_present = c is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio composer: {e}")

        send_button_present = False
        try:
            sb = await self.page.query_selector(send_button_selector)
            send_button_present = sb is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio send button: {e}")

        stop_button_present = False
        try:
            sb = await self.page.query_selector(stop_button_selector)
            stop_button_present = sb is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio stop button: {e}")

        gen_indicator_present = False
        try:
            gi = await self.page.query_selector(gen_indicator_selector)
            gen_indicator_present = gi is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio generation indicator: {e}")

        retry_button_present = False
        try:
            rb = await self.page.query_selector(retry_selector)
            retry_button_present = rb is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio retry button: {e}")

        error_message_present = False
        try:
            em = await self.page.query_selector(error_selector)
            error_message_present = em is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio error message: {e}")

        stopped_indicator_present = False
        try:
            si = await self.page.query_selector(stopped_selector)
            stopped_indicator_present = si is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio stopped indicator: {e}")

        network_error_present = False
        try:
            ne = await self.page.query_selector(network_selector)
            network_error_present = ne is not None
        except Exception as e:
            self.logger.debug(f"Error querying AI Studio network error: {e}")

        turns_count = 0
        turns_summary = []
        try:
            turns = await self.page.query_selector_all(turns_selector)
            turns_count = len(turns)
            for idx, turn in enumerate(turns):
                text = await turn.inner_text()
                turns_summary.append({"index": idx, "length": len(text), "preview": text[:100]})
        except Exception as e:
            self.logger.debug(f"Error reading conversation turns: {e}")

        return {
            "adapter": "AIStudioAdapter",
            "url": info["url"],
            "title": info["title"],
            "composer_present": composer_present,
            "send_button_present": send_button_present,
            "stop_button_present": stop_button_present,
            "generation_indicator_present": gen_indicator_present,
            "retry_button_present": retry_button_present,
            "error_message_present": error_message_present,
            "stopped_indicator_present": stopped_indicator_present,
            "network_error_present": network_error_present,
            "conversation_turns_count": turns_count,
            "conversation_turns": turns_summary,
        }

    async def extract_all_turns_text(self) -> Optional[List[str]]:
        """Extracts full text content of all conversation turns in DOM order. Returns None on error."""
        sel = self.selectors.get("ai_studio", {})
        turns_selector = sel.get("conversation_turns", "div.turn")
        results = []
        try:
            turns = await self.page.query_selector_all(turns_selector)
            for turn in turns:
                results.append(await turn.inner_text())
            return results
        except Exception as e:
            self.logger.error(f"Failed to extract AI Studio turns text: {e}")
            return None

    async def extract_latest_response_text(self) -> str:
        """Extracts text content of the latest model/assistant turn in DOM order (excluding user prompts and non-model turns)."""
        sel = self.selectors.get("ai_studio", {})
        turns_selector = sel.get("conversation_turns", "div.turn")
        model_selector = "ms-chat-turn.model-turn, div.model-turn, ms-chat-turn[data-role='model'], div[data-role='model'], .model-response, .assistant-response"
        try:
            # First attempt explicit model turn selectors
            model_turns = await self.page.query_selector_all(model_selector)
            if model_turns:
                return await model_turns[-1].inner_text()

            # Fallback: Scan general turns in reverse, strictly rejecting user/prompt turns
            turns = await self.page.query_selector_all(turns_selector)
            for turn in reversed(turns):
                # Evaluate class/attribute to reject explicit user turns
                is_user = await turn.evaluate("el => el.classList.contains('user-turn') || el.getAttribute('data-role') === 'user' || el.getAttribute('data-message-author-role') === 'user'")
                if is_user:
                    continue

                text = await turn.inner_text()
                if text and "[SUPERVISOR]" not in text and "<<<SUPERVISOR_BRIDGE_COMMAND>>>" not in text:
                    return text
        except Exception as e:
            self.logger.error(f"Failed to extract latest AI Studio response text: {e}")
        return ""
