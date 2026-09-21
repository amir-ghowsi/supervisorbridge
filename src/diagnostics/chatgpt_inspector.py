from typing import Dict, Any, Optional
from playwright.async_api import Page
from src.adapters.chatgpt_adapter import ChatGPTAdapter


class ChatGPTInspector:
    """Read-only diagnostic inspector for ChatGPT DOM state."""

    @staticmethod
    async def inspect(page: Page) -> Dict[str, Any]:
        adapter = ChatGPTAdapter(page)
        return await adapter.inspect()
