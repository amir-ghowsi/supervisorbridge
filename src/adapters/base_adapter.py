from abc import ABC, abstractmethod
from typing import Dict, Any
from playwright.async_api import Page
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class BaseAdapter(ABC):
    """Base class for read-only DOM adapters."""

    def __init__(self, page: Page):
        self.page = page
        self.logger = get_logger()
        self.selectors = get_config().selectors

    @abstractmethod
    async def inspect(self) -> Dict[str, Any]:
        """Inspects page DOM state safely without mutation."""
        pass

    async def get_page_info(self) -> Dict[str, Any]:
        """Gets basic page metadata."""
        try:
            title = await self.page.title()
        except Exception:
            title = "<error reading title>"

        return {
            "url": self.page.url,
            "title": title,
        }
