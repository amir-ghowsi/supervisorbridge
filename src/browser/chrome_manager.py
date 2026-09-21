from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, Playwright
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class ChromeConnectionError(Exception):
    """Exception raised when unable to connect to Chrome over CDP."""

    pass


class ChromeManager:
    """Manages CDP connection to an existing Chrome browser instance."""

    def __init__(self, cdp_url: Optional[str] = None, timeout_ms: Optional[int] = None):
        cfg = get_config()
        self.cdp_url = cdp_url or cfg.settings.get("cdp_url", "http://127.0.0.1:9222")
        self.timeout_ms = timeout_ms or cfg.settings.get("timeouts", {}).get(
            "connect_ms", 10000
        )
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.logger = get_logger()

    async def connect(self) -> Browser:
        """Connects to Chrome via CDP."""
        if self.browser and self.browser.is_connected():
            return self.browser

        try:
            self.logger.info(f"Connecting to Chrome CDP at {self.cdp_url}...")
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(
                self.cdp_url, timeout=self.timeout_ms
            )
            self.logger.info("Successfully connected to Chrome via CDP.")
            return self.browser
        except Exception as e:
            await self.disconnect()
            raise ChromeConnectionError(
                f"Failed to connect to Chrome CDP at {self.cdp_url}: {e}"
            ) from e

    async def disconnect(self) -> None:
        """Disconnects CDP connection cleanly without closing the target browser."""
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                self.logger.debug(f"Error during browser disconnect: {e}")
            self.browser = None

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception as e:
                self.logger.debug(f"Error during playwright stop: {e}")
            self.playwright = None

        self.logger.info("Disconnected from Chrome CDP.")

    def is_connected(self) -> bool:
        return self.browser is not None and self.browser.is_connected()

    async def get_diagnostics(self) -> Dict[str, Any]:
        """Returns diagnostic info regarding the CDP browser connection."""
        connected = self.is_connected()
        contexts_count = len(self.browser.contexts) if connected and self.browser else 0
        pages_count = 0
        if connected and self.browser:
            for ctx in self.browser.contexts:
                pages_count += len(ctx.pages)

        return {
            "cdp_url": self.cdp_url,
            "connected": connected,
            "contexts_count": contexts_count,
            "pages_count": pages_count,
        }
