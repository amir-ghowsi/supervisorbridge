from pathlib import Path
from typing import Any, Dict
import yaml
from src.utils.paths import resolve_path


class ConfigurationError(Exception):
    """Exception raised for errors in configuration loading or validation."""

    pass


def load_yaml_file(filepath: str | Path) -> Dict[str, Any]:
    """Loads a YAML configuration file safely."""
    path = resolve_path(filepath)
    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                return {}
            if not isinstance(data, dict):
                raise ConfigurationError(
                    f"Configuration file {path} must contain a top-level mapping/dictionary."
                )
            return data
    except yaml.YAMLError as e:
        raise ConfigurationError(f"YAML parsing error in {path}: {e}") from e
    except Exception as e:
        raise ConfigurationError(f"Failed to read configuration file {path}: {e}") from e


def validate_settings(settings: Dict[str, Any]) -> None:
    """Validates the structure and required keys of settings.yaml."""
    required_keys = [
        "cdp_url",
        "timeouts",
        "polling",
        "limits",
        "paths",
        "logging",
        "allowed_domains",
    ]
    for key in required_keys:
        if key not in settings:
            raise ConfigurationError(f"Missing required settings key: '{key}'")

    timeouts = settings.get("timeouts", {})
    if not isinstance(timeouts, dict) or "connect_ms" not in timeouts:
        raise ConfigurationError("Settings 'timeouts' must contain 'connect_ms'")

    limits = settings.get("limits", {})
    if not isinstance(limits, dict) or "max_retries" not in limits:
        raise ConfigurationError("Settings 'limits' must contain 'max_retries'")

    allowed_domains = settings.get("allowed_domains", {})
    if (
        not isinstance(allowed_domains, dict)
        or "chatgpt" not in allowed_domains
        or "ai_studio" not in allowed_domains
    ):
        raise ConfigurationError(
            "Settings 'allowed_domains' must specify 'chatgpt' and 'ai_studio'"
        )


def validate_selectors(selectors: Dict[str, Any]) -> None:
    """Validates the structure and required keys of selectors.yaml."""
    if "chatgpt" not in selectors or "ai_studio" not in selectors:
        raise ConfigurationError(
            "Selectors config must contain 'chatgpt' and 'ai_studio' sections."
        )


class ConfigManager:
    """Manages application settings and selectors configurations."""

    def __init__(
        self,
        settings_path: str = "config/settings.yaml",
        selectors_path: str = "config/selectors.yaml",
    ):
        self.settings_path = settings_path
        self.selectors_path = selectors_path
        self._settings: Dict[str, Any] = {}
        self._selectors: Dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        self._settings = load_yaml_file(self.settings_path)
        validate_settings(self._settings)

        self._selectors = load_yaml_file(self.selectors_path)
        validate_selectors(self._selectors)

    @property
    def settings(self) -> Dict[str, Any]:
        return self._settings

    @property
    def selectors(self) -> Dict[str, Any]:
        return self._selectors


_global_config: ConfigManager | None = None


def get_config(
    settings_path: str = "config/settings.yaml",
    selectors_path: str = "config/selectors.yaml",
    force_reload: bool = False,
) -> ConfigManager:
    global _global_config
    if _global_config is None or force_reload:
        _global_config = ConfigManager(settings_path, selectors_path)
    return _global_config
