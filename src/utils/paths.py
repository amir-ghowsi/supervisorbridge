import os
from pathlib import Path

# Base project root (two levels up from src/utils/paths.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_project_root() -> Path:
    """Returns the absolute path to the project root directory."""
    return PROJECT_ROOT


def resolve_path(relative_or_absolute_path: str | Path) -> Path:
    """
    Resolves a path relative to the project root if it is not already absolute.
    Handles Windows path separators cleanly on any platform.
    """
    if isinstance(relative_or_absolute_path, str):
        # Normalize backslashes to standard separator for cross-platform compatibility
        relative_or_absolute_path = relative_or_absolute_path.replace("\\", "/")
    p = Path(relative_or_absolute_path)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


def ensure_dir(dir_path: str | Path) -> Path:
    """Ensures a directory exists, creating it if necessary."""
    p = resolve_path(dir_path)
    p.mkdir(parents=True, exist_ok=True)
    return p
