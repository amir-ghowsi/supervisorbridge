from urllib.parse import urlparse
from typing import List, Optional, Tuple, Dict, Any
from playwright.async_api import Browser, Page
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class TabNotFoundError(Exception):
    """Raised when a required target tab is not found."""

    pass


class InvalidDomainError(Exception):
    """Raised when a tab domain is not in the list of allowed domains."""

    pass


class TabManager:
    """Manages tab resolution and validation for ChatGPT and AI Studio."""

    def __init__(self, browser: Browser):
        self.browser = browser
        self.logger = get_logger()
        cfg = get_config()
        self.allowed_domains = cfg.settings.get(
            "allowed_domains",
            {
                "chatgpt": ["chatgpt.com", "chat.openai.com"],
                "ai_studio": ["aistudio.google.com"],
            },
        )

    def _extract_hostname(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            return parsed.hostname or ""
        except Exception:
            return ""

    def _is_allowed_domain(self, url: str, allowed_list: List[str]) -> bool:
        hostname = self._extract_hostname(url).lower()
        for domain in allowed_list:
            domain_lower = domain.lower()
            if hostname == domain_lower or hostname.endswith("." + domain_lower):
                return True
        return False

    def get_all_pages(self) -> List[Page]:
        """Collects all pages across all browser contexts in deterministic order."""
        pages: List[Page] = []
        for ctx in self.browser.contexts:
            for page in ctx.pages:
                pages.append(page)
        # Sort deterministically by page URL
        pages.sort(key=lambda p: p.url)
        return pages

    async def locate_chatgpt_tab(self) -> Page:
        """
        Locates the ChatGPT tab deterministically.
        Rejects disallowed domains.
        """
        allowed = self.allowed_domains.get(
            "chatgpt", ["chatgpt.com", "chat.openai.com"]
        )
        pages = self.get_all_pages()
        matching_pages: List[Page] = []

        for page in pages:
            if self._is_allowed_domain(page.url, allowed):
                matching_pages.append(page)

        if not matching_pages:
            raise TabNotFoundError(
                f"No ChatGPT tab found matching allowed domains: {allowed}"
            )

        if len(matching_pages) > 1:
            self.logger.warning(
                f"Multiple ({len(matching_pages)}) ChatGPT tabs found. Deterministically selecting the first one: {matching_pages[0].url}"
            )

        return matching_pages[0]

    async def locate_ai_studio_tab(self) -> Page:
        """
        Locates the Google AI Studio tab deterministically.
        Rejects disallowed domains.
        """
        allowed = self.allowed_domains.get("ai_studio", ["aistudio.google.com"])
        pages = self.get_all_pages()
        matching_pages: List[Page] = []

        for page in pages:
            if self._is_allowed_domain(page.url, allowed):
                matching_pages.append(page)

        if not matching_pages:
            raise TabNotFoundError(
                f"No AI Studio tab found matching allowed domains: {allowed}"
            )

        if len(matching_pages) > 1:
            self.logger.warning(
                f"Multiple ({len(matching_pages)}) AI Studio tabs found. Deterministically selecting the first one: {matching_pages[0].url}"
            )

        return matching_pages[0]

    async def get_tabs_status(self) -> Dict[str, Any]:
        """Returns diagnostic info about all current tabs."""
        pages = self.get_all_pages()
        chatgpt_allowed = self.allowed_domains.get("chatgpt", [])
        aistudio_allowed = self.allowed_domains.get("ai_studio", [])

        details = []
        for p in pages:
            details.append(
                {
                    "url": p.url,
                    "is_chatgpt": self._is_allowed_domain(p.url, chatgpt_allowed),
                    "is_ai_studio": self._is_allowed_domain(p.url, aistudio_allowed),
                }
            )

        return {
            "total_tabs": len(pages),
            "tabs": details,
        }
