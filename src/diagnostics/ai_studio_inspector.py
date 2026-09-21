from typing import Dict, Any, Optional
from playwright.async_api import Page
from src.adapters.ai_studio_adapter import AIStudioAdapter


class AIStudioInspector:
    """Read-only diagnostic inspector for Google AI Studio DOM state."""

    @staticmethod
    async def inspect(page: Page) -> Dict[str, Any]:
        adapter = AIStudioAdapter(page)
        return await adapter.inspect()
