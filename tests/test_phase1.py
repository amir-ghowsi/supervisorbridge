import sys
import os
import unittest
import tempfile
import argparse
from pathlib import Path

from src.utils.paths import get_project_root, resolve_path, ensure_dir
from src.utils.logger import setup_logger, get_logger
from src.utils.config_loader import (
    load_yaml_file,
    validate_settings,
    validate_selectors,
    ConfigManager,
    ConfigurationError,
)
from src.utils.hashing import compute_sha256
from main import build_cli_parser, run_check, run_status


class TestPhase1Foundation(unittest.TestCase):

    def test_path_resolution(self):
        root = get_project_root()
        self.assertTrue(root.exists())
        self.assertTrue((root / "pyproject.toml").exists())

        resolved_relative = resolve_path("config/settings.yaml")
        self.assertEqual(resolved_relative, root / "config" / "settings.yaml")

        resolved_abs = resolve_path(root / "main.py")
        self.assertEqual(resolved_abs, root / "main.py")

    def test_windows_path_compatibility(self):
        # Path resolution should handle mixed/backslash separators cleanly
        win_path_str = "config\\settings.yaml"
        resolved = resolve_path(win_path_str)
        self.assertEqual(resolved, get_project_root() / "config" / "settings.yaml")

    def test_config_load_and_validation(self):
        cfg = ConfigManager()
        self.assertIn("cdp_url", cfg.settings)
        self.assertIn("chatgpt", cfg.selectors)
        self.assertEqual(cfg.settings["cdp_url"], "http://127.0.0.1:9222")

    def test_missing_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            non_existent = Path(tmpdir) / "does_not_exist.yaml"
            with self.assertRaises(ConfigurationError):
                load_yaml_file(non_existent)

    def test_invalid_config_structure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            invalid_file = Path(tmpdir) / "invalid.yaml"
            with open(invalid_file, "w", encoding="utf-8") as f:
                f.write("invalid_yaml: [unclosed list")

            with self.assertRaises(ConfigurationError):
                load_yaml_file(invalid_file)

        # Invalid keys test
        with self.assertRaises(ConfigurationError):
            validate_settings({"cdp_url": "http://127.0.0.1:9222"})  # Missing other keys

        with self.assertRaises(ConfigurationError):
            validate_selectors({"chatgpt": {}})  # Missing ai_studio

    def test_logging_initialization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = setup_logger(level="DEBUG", log_dir=tmpdir, log_file_name="test.log")
            logger.debug("Test log entry")
            log_file = Path(tmpdir) / "test.log"
            self.assertTrue(log_file.exists())
            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn("Test log entry", content)

    def test_sha256_hashing(self):
        exact_text = "[SUPERVISOR]\nTASK_ID: 1\n[/SUPERVISOR]"
        sha1 = compute_sha256(exact_text)
        sha2 = compute_sha256(exact_text.encode("utf-8"))
        self.assertEqual(sha1, sha2)
        # Ensure exact preservation
        different_text = "[SUPERVISOR]\nTASK_ID: 1 \n[/SUPERVISOR]"
        self.assertNotEqual(compute_sha256(exact_text), compute_sha256(different_text))

    def test_cli_parsing(self):
        parser = build_cli_parser()

        args_check = parser.parse_args(["check"])
        self.assertEqual(args_check.command, "check")

        args_status = parser.parse_args(["status", "--json"])
        self.assertEqual(args_status.command, "status")
        self.assertTrue(args_status.json)

    def test_import_safety(self):
        """Verify that importing modules produces NO state files on disk."""
        state_dir = get_project_root() / "data" / "state"
        logs_dir = get_project_root() / "logs"

        # Record initial state if directories exist
        state_files_before = set(state_dir.glob("*")) if state_dir.exists() else set()
        log_files_before = set(logs_dir.glob("*")) if logs_dir.exists() else set()

        # Re-import all modules
        import src.utils.paths
        import src.utils.logger
        import src.utils.config_loader
        import src.utils.hashing
        import main

        state_files_after = set(state_dir.glob("*")) if state_dir.exists() else set()
        log_files_after = set(logs_dir.glob("*")) if logs_dir.exists() else set()

        self.assertEqual(state_files_before, state_files_after)
        self.assertEqual(log_files_before, log_files_after)


if __name__ == "__main__":
    unittest.main()
