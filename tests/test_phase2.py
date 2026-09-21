import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from src.browser.chrome_manager import ChromeManager, ChromeConnectionError
from src.browser.tab_manager import TabManager, TabNotFoundError, InvalidDomainError
from src.adapters.chatgpt_adapter import ChatGPTAdapter
from src.adapters.ai_studio_adapter import AIStudioAdapter


class TestPhase2BrowserAndAdapters(unittest.IsolatedAsyncioTestCase):

    async def test_chrome_manager_connect_failure(self):
        chrome = ChromeManager(cdp_url="http://127.0.0.1:99999", timeout_ms=100)
        with self.assertRaises(ChromeConnectionError):
            await chrome.connect()
        self.assertFalse(chrome.is_connected())

    async def test_tab_manager_domain_filtering_and_multiple_tabs(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()

        # Create mock pages with different URLs
        page_chatgpt1 = AsyncMock()
        page_chatgpt1.url = "https://chatgpt.com/c/123"

        page_chatgpt2 = AsyncMock()
        page_chatgpt2.url = "https://chat.openai.com/g/456"

        page_other = AsyncMock()
        page_other.url = "https://example.com"

        page_aistudio = AsyncMock()
        page_aistudio.url = "https://aistudio.google.com/app/prompts/new_chat"

        mock_context.pages = [page_chatgpt2, page_other, page_chatgpt1, page_aistudio]
        mock_browser.contexts = [mock_context]

        tab_mgr = TabManager(mock_browser)

        # Multiple ChatGPT tabs should deterministically pick the first sorted by URL
        # Sorted URLs: https://aistudio..., https://chat.openai..., https://chatgpt..., https://example...
        chatgpt_tab = await tab_mgr.locate_chatgpt_tab()
        self.assertEqual(chatgpt_tab.url, "https://chat.openai.com/g/456")

        aistudio_tab = await tab_mgr.locate_ai_studio_tab()
        self.assertEqual(aistudio_tab.url, "https://aistudio.google.com/app/prompts/new_chat")

    async def test_tab_manager_tab_missing(self):
        mock_browser = MagicMock()
        mock_context = MagicMock()

        page_other = AsyncMock()
        page_other.url = "https://example.com"

        mock_context.pages = [page_other]
        mock_browser.contexts = [mock_context]

        tab_mgr = TabManager(mock_browser)

        with self.assertRaises(TabNotFoundError):
            await tab_mgr.locate_chatgpt_tab()

        with self.assertRaises(TabNotFoundError):
            await tab_mgr.locate_ai_studio_tab()

    async def test_chatgpt_adapter_inspection_read_only(self):
        mock_page = AsyncMock()
        mock_page.url = "https://chatgpt.com/c/test"
        mock_page.title.return_value = "ChatGPT Title"

        # Mock query selectors
        mock_landmark = AsyncMock()
        mock_landmark.evaluate.return_value = "main"

        mock_composer = AsyncMock()
        mock_send_btn = AsyncMock()

        mock_msg = AsyncMock()
        mock_msg.inner_text.return_value = "Hello from ChatGPT Assistant"

        async def mock_query_selector_all(selector):
            if "main" in selector or "nav" in selector:
                return [mock_landmark]
            if "article" in selector:
                return [mock_msg]
            return []

        async def mock_query_selector(selector):
            if "prompt" in selector or "textarea" in selector:
                return mock_composer
            if "send" in selector:
                return mock_send_btn
            return None

        mock_page.query_selector_all.side_effect = mock_query_selector_all
        mock_page.query_selector.side_effect = mock_query_selector

        adapter = ChatGPTAdapter(mock_page)
        res = await adapter.inspect()

        self.assertEqual(res["adapter"], "ChatGPTAdapter")
        self.assertTrue(res["composer_present"])
        self.assertTrue(res["send_button_present"])
        self.assertEqual(res["assistant_message_count"], 1)

        # READ-ONLY VERIFICATION: Ensure no mutating page methods were ever called
        for forbidden_method in ["click", "fill", "type", "press", "keyboard", "mouse"]:
            self.assertFalse(
                hasattr(mock_page, forbidden_method) and getattr(mock_page, forbidden_method).called,
                f"Forbidden mutating method '{forbidden_method}' was called on page!",
            )

    async def test_ai_studio_adapter_inspection_read_only(self):
        mock_page = AsyncMock()
        mock_page.url = "https://aistudio.google.com/app"
        mock_page.title.return_value = "Google AI Studio"

        mock_composer = AsyncMock()
        mock_send_btn = AsyncMock()
        mock_turn = AsyncMock()
        mock_turn.inner_text.return_value = "Model response in AI Studio"

        async def mock_query_selector_all(selector):
            if "turn" in selector:
                return [mock_turn]
            return []

        async def mock_query_selector(selector):
            if "textarea" in selector or "contenteditable" in selector:
                return mock_composer
            if "run" in selector or "Send" in selector:
                return mock_send_btn
            return None

        mock_page.query_selector_all.side_effect = mock_query_selector_all
        mock_page.query_selector.side_effect = mock_query_selector

        adapter = AIStudioAdapter(mock_page)
        res = await adapter.inspect()

        self.assertEqual(res["adapter"], "AIStudioAdapter")
        self.assertTrue(res["composer_present"])
        self.assertEqual(res["conversation_turns_count"], 1)

        # READ-ONLY VERIFICATION: Ensure no mutating page methods were ever called
        for forbidden_method in ["click", "fill", "type", "press", "keyboard", "mouse"]:
            self.assertFalse(
                hasattr(mock_page, forbidden_method) and getattr(mock_page, forbidden_method).called,
                f"Forbidden mutating method '{forbidden_method}' was called on page!",
            )

    async def test_adapter_dom_read_exception_resilience(self):
        mock_page = AsyncMock()
        mock_page.url = "https://chatgpt.com/"
        mock_page.title.side_effect = Exception("DOM detached")
        mock_page.query_selector_all.side_effect = Exception("DOM error")
        mock_page.query_selector.side_effect = Exception("DOM error")

        adapter = ChatGPTAdapter(mock_page)
        res = await adapter.inspect()

        self.assertEqual(res["title"], "<error reading title>")
        self.assertFalse(res["composer_present"])
        self.assertEqual(res["assistant_message_count"], 0)


if __name__ == "__main__":
    unittest.main()
