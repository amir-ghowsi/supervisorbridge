import os
import sys
import json
import shutil
import tempfile
import unittest
import asyncio
from unittest.mock import MagicMock, patch

from src.utils.config_loader import get_config
from src.state.state_repository import StateRepository
from src.safety.emergency_stop import EmergencyStopManager
from src.bridge.runtime import RuntimeEngine
from src.diagnostics.state_inspector import StateInspector
from scripts.live_validation import run_live_validation_async
import main as main_module


class TestPhase10ProductionHardening(unittest.TestCase):
    """
    Phase 10 Tests: Production Hardening, Live Validation Harness & CLI Entrypoints.
    Ensures tests strictly isolate production state and do not pollute data/state.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.test_dir, "data", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.repo = StateRepository(state_dir=self.state_dir)
        self.estop = EmergencyStopManager(state_dir=self.state_dir)

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_live_validation_harness_dry_run(self):
        """Tests live validation script executing a dry-run cycle safely."""
        with patch("scripts.live_validation.RuntimeEngine") as MockEngine:
            mock_engine_instance = MagicMock()
            async def async_run_once(*args, **kwargs):
                return {
                    "status": "SUCCESS",
                    "SESSION_ID": "sess_100",
                    "TASK_ID": "TASK-100",
                    "COMMAND_SHA256": "abc123sha256",
                    "OPERATION_ID": "op_submit_TASK-100_1",
                    "IDEMPOTENCY_KEY": "idem_100",
                    "CYCLE_STATE": "IDLE",
                    "ACTION_TAKEN": "NONE",
                    "SIDE_EFFECT_COUNT": 0,
                    "REASON_CODE": "NO_ACTION",
                    "DIAGNOSTIC": "Dry run verification OK",
                }
            mock_engine_instance.run_once = async_run_once
            MockEngine.return_value = mock_engine_instance

            res = asyncio.run(
                run_live_validation_async(
                    session_id="sess_100",
                    task_id="TASK-100",
                    command_sha256="abc123sha256",
                    operation_id="op_submit_TASK-100_1",
                    idempotency_key="idem_100",
                    dry_run=True,
                )
            )

            self.assertEqual(res["SESSION_ID"], "sess_100")
            self.assertEqual(res["TASK_ID"], "TASK-100")
            self.assertTrue(res["DRY_RUN"])
            self.assertTrue(res["VALIDATION_ASSERTIONS"]["session_id_match"])
            self.assertTrue(res["VALIDATION_ASSERTIONS"]["task_id_match"])

    def test_cli_check_command(self):
        """Tests 'python main.py check' execution."""
        ret = main_module.run_check()
        self.assertEqual(ret, 0)

    def test_cli_status_command(self):
        """Tests 'python main.py status --json' execution."""
        ret = main_module.run_status(json_output=True)
        self.assertEqual(ret, 0)

    def test_cli_state_inspect_command(self):
        """Tests 'python main.py state-inspect --json' execution."""
        inspector = StateInspector(state_dir=self.state_dir)
        with patch("main.StateInspector", lambda: inspector):
            ret = main_module.run_state_inspect(json_output=True)
            self.assertEqual(ret, 0)

    def test_cli_recovery_status_command(self):
        """Tests 'python main.py recovery-status --json' execution."""
        inspector = StateInspector(state_dir=self.state_dir)
        with patch("main.StateInspector", lambda: inspector):
            ret = main_module.run_recovery_status(json_output=True)
            self.assertEqual(ret, 0)

    def test_cli_emergency_stop_commands(self):
        """Tests engagement, status check, and release of emergency stop via CLI helper."""
        with patch("main.EmergencyStopManager", lambda: self.estop):
            # Engage
            ret1 = main_module.run_emergency_stop(estop_action="engage", reason="Test engage", json_output=True)
            self.assertEqual(ret1, 0)
            self.assertTrue(self.estop.is_engaged())

            # Status
            ret2 = main_module.run_emergency_stop(estop_action="status", json_output=True)
            self.assertEqual(ret2, 0)

            # Release
            ret3 = main_module.run_emergency_stop(estop_action="release", reason="Test release", json_output=True)
            self.assertEqual(ret3, 0)
            self.assertFalse(self.estop.is_engaged())

    def test_no_production_state_pollution(self):
        """Verifies that running Phase 10 tests produces zero state pollution under data/state."""
        prod_state_files = os.listdir("data/state") if os.path.exists("data/state") else []
        self.assertEqual(len(prod_state_files), 0, f"Production state directory contaminated: {prod_state_files}")


if __name__ == "__main__":
    unittest.main()
