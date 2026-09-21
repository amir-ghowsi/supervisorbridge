from typing import Dict, Any, List
from playwright.async_api import Page
from src.adapters.base_adapter import BaseAdapter


class ChatGPTAdapter(BaseAdapter):
    """Read-only adapter for ChatGPT page DOM inspection."""

    async def inspect(self) -> Dict[str, Any]:
        """Inspects ChatGPT DOM elements without mutating page state."""
        info = await self.get_page_info()
        sel = self.selectors.get("chatgpt", {})

        landmarks_selector = sel.get("landmarks", "main, nav")
        composer_selector = sel.get("composer", "textarea")
        send_button_selector = sel.get("send_button", "button")
        assistant_selector = sel.get("assistant_messages", "article")

        landmarks_found: List[str] = []
        try:
            elements = await self.page.query_selector_all(landmarks_selector)
            for el in elements:
                tag = await el.evaluate("el => el.tagName.toLowerCase()")
                landmarks_found.append(tag)
        except Exception as e:
            self.logger.debug(f"Error reading landmarks: {e}")

        composer_present = False
        try:
            composer = await self.page.query_selector(composer_selector)
            composer_present = composer is not None
        except Exception as e:
            self.logger.debug(f"Error querying composer: {e}")

        send_button_present = False
        try:
            sb = await self.page.query_selector(send_button_selector)
            send_button_present = sb is not None
        except Exception as e:
            self.logger.debug(f"Error querying send button: {e}")

        assistant_messages: List[Dict[str, Any]] = []
        try:
            msgs = await self.page.query_selector_all(assistant_selector)
            for idx, msg in enumerate(msgs):
                text = await msg.inner_text()
                assistant_messages.append({"index": idx, "text_length": len(text), "preview": text[:100]})
        except Exception as e:
            self.logger.debug(f"Error reading assistant messages: {e}")

        return {
            "adapter": "ChatGPTAdapter",
            "url": info["url"],
            "title": info["title"],
            "composer_present": composer_present,
            "send_button_present": send_button_present,
            "landmarks": landmarks_found,
            "assistant_message_count": len(assistant_messages),
            "assistant_messages": assistant_messages,
        }

    async def extract_raw_assistant_messages(self) -> List[str]:
        """Extracts text content of all assistant message elements in DOM order."""
        sel = self.selectors.get("chatgpt", {})
        assistant_selector = sel.get("assistant_messages", "article")
        results = []
        try:
            msgs = await self.page.query_selector_all(assistant_selector)
            for msg in msgs:
                results.append(await msg.inner_text())
        except Exception as e:
            self.logger.error(f"Failed to extract assistant messages: {e}")
        return results
