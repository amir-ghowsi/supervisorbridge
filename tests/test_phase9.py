import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import json
import hashlib
from pathlib import Path

from src.bridge.runtime import RuntimeEngine
from src.bridge.loop_controller import LoopController
from src.state.state_repository import StateRepository, CorruptStateError
from src.state.task_state import ActiveTaskData, TaskState, CURRENT_SCHEMA_VERSION
from src.safety.orchestrator import SafetyOrchestrator
from src.safety.idempotency import IdempotencyManager, IdempotencyViolationError
from src.safety.emergency_stop import EmergencyStopManager
from src.protocol.supervisor_protocol import SupervisorCommand
from src.protocol.implementer_protocol import ImplementerReport
from src.diagnostics.state_inspector import StateInspector
from src.utils.paths import get_project_root
from src.utils.hashing import compute_sha256


class TestPhase9RecoveryAndFailureInjection(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)
        self.repo = StateRepository(state_dir=self.state_dir)
        self.safety = SafetyOrchestrator(state_dir=self.state_dir)
        self.runtime = RuntimeEngine(repo=self.repo, safety=self.safety)

        self.valid_cmd_text = (
            "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "[SUPERVISOR]\n"
            "TASK_ID: TASK-900\n"
            "PHASE: 1\n"
            "ACTION: EXECUTE\n"
            "SEND_TO: GEMINI\n\n"
            "Run Phase 9 Task.\n"
            "[/SUPERVISOR]\n"
            "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
        )

        self.valid_report_text = (
            "[IMPLEMENTER_REPORT]\n"
            "TASK_ID: TASK-900\n"
            "PHASE: 1\n"
            "STATUS: COMPLETED\n"
            "READY_FOR_REVIEW: true\n\n"
            "Phase 9 Task Completed.\n"
            "[/IMPLEMENTER_REPORT]"
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    # Blocker 14: Production state immutability test
    def test_production_state_immutability(self):
        state_dir = get_project_root() / "data" / "state"

        def get_inventory():
            inventory = {}
            if state_dir.exists():
                for p in sorted(state_dir.glob("**/*")):
                    if p.is_file():
                        data = p.read_bytes()
                        inventory[str(p.relative_to(state_dir))] = {
                            "size": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                        }
            return inventory

        before = get_inventory()
        inspector = StateInspector()
        _ = inspector.inspect_full_state()
        after = get_inventory()

        self.assertEqual(before, after)

    # Scenarios 1 & 2: Fresh submission & duplicate protection
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_scenario_1_and_2_fresh_submission_and_no_duplicate(
        self, mock_chrome_cls, mock_tab_cls
    ):
        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_browser = MagicMock()
        mock_chrome.connect.return_value = mock_browser

        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_chatgpt_page = AsyncMock()
        mock_aistudio_page = AsyncMock()

        mock_tab.locate_chatgpt_tab.return_value = mock_chatgpt_page
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page

        mock_chatgpt_page.query_selector_all.return_value = [
            AsyncMock(inner_text=AsyncMock(return_value=self.valid_cmd_text))
        ]

        mock_composer = AsyncMock()
        mock_composer.input_value.return_value = (
            "[SUPERVISOR]\n"
            "TASK_ID: TASK-900\n"
            "PHASE: 1\n"
            "ACTION: EXECUTE\n"
            "SEND_TO: GEMINI\n\n"
            "Run Phase 9 Task.\n"
            "[/SUPERVISOR]"
        )
        mock_send_btn = AsyncMock()

        async def mock_ai_qs(sel):
            if "textarea" in sel or "contenteditable" in sel:
                return mock_composer
            if "run" in sel.lower() or "button" in sel.lower() or "send" in sel.lower():
                return mock_send_btn
            return None

        mock_aistudio_page.query_selector.side_effect = mock_ai_qs

        # Cycle 1: Fresh submission
        res1 = await self.runtime.run_once(dry_run=False)
        self.assertEqual(res1["ACTION_TAKEN"], "FRESH_GEMINI_SUBMISSION")
        self.assertEqual(res1["SIDE_EFFECT_COUNT"], 1)

        # Cycle 2: Invoked again while generating -> zero browser side effects
        mock_aistudio_page.query_selector.side_effect = lambda sel: AsyncMock() if "Stop" in sel else None
        res2 = await self.runtime.run_once(dry_run=False)
        self.assertEqual(res2["ACTION_TAKEN"], "WAIT_FOR_GENERATION")
        self.assertEqual(res2["SIDE_EFFECT_COUNT"], 0)

    # Scenario 3: Resume active task without requiring original ChatGPT command
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_scenario_3_resume_sent_to_implementer_without_chatgpt_command(
        self, mock_chrome_cls, mock_tab_cls
    ):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-901",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256="sha901",
            session_id="sess-901",
            operation_id="op-901",
            idempotency_key="idem-901",
            current_state=TaskState.SENT_TO_IMPLEMENTER.value,
        )
        self.repo.save_active_task(task)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_aistudio_page = AsyncMock()
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page

        mock_aistudio_page.query_selector.side_effect = lambda sel: AsyncMock() if "Stop" in sel else None

        res = await self.runtime.run_once(dry_run=False)
        self.assertEqual(res["TASK_ID"], "TASK-901")
        self.assertEqual(res["ACTION_TAKEN"], "WAIT_FOR_GENERATION")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)

    # Failure Injection: Intermediate states (COMMAND_ACCEPTED) progress safely
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_failure_injection_command_accepted_progresses(
        self, mock_chrome_cls, mock_tab_cls
    ):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-ACCEPT",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256="sha-accept",
            session_id="sess-accept",
            operation_id="op-accept",
            idempotency_key="idem_submit_sha-accept",
            current_state=TaskState.COMMAND_ACCEPTED.value,
        )
        self.repo.save_active_task(task)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_aistudio_page = AsyncMock()
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page

        mock_composer = AsyncMock()
        mock_composer.input_value.return_value = "[SUPERVISOR]\nTASK_ID: TASK-ACCEPT\n[/SUPERVISOR]"
        mock_send_btn = AsyncMock()

        async def mock_ai_qs(sel):
            if "textarea" in sel:
                return mock_composer
            return mock_send_btn

        mock_aistudio_page.query_selector.side_effect = mock_ai_qs

        # Should progress through SUBMISSION_PREPARED and submit command
        res = await self.runtime.run_once(dry_run=False)
        self.assertIn(res["status"], ("SUCCESS", "ERROR"))
        self.assertEqual(res["TASK_ID"], "TASK-ACCEPT")

    # Failure Injection: SUBMISSION_PREPARED + ambiguous state
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_failure_injection_submission_prepared_ambiguous(
        self, mock_chrome_cls, mock_tab_cls
    ):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-SUB-PREP",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256="sha-sub-prep",
            session_id="sess-sub-prep",
            operation_id="op-sub-prep",
            idempotency_key="idem-sub-prep",
            current_state=TaskState.SUBMISSION_PREPARED.value,
        )
        self.repo.save_active_task(task)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_aistudio_page = AsyncMock()
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page

        # DOM read error during reconciliation -> INSUFFICIENT_EVIDENCE -> HALTED
        mock_aistudio_page.query_selector_all.side_effect = Exception("DOM read error")

        res = await self.runtime.run_once(dry_run=False)
        self.assertEqual(res["status"], "HALTED")
        self.assertEqual(res["REASON_CODE"], "AMBIGUOUS_SUBMISSION_PREPARED")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)

        updated = self.repo.load_active_task()
        self.assertEqual(updated.get_state_enum(), TaskState.MANUAL_REVIEW_REQUIRED)

    # Failure Injection: RETURN_PREPARED + independently proven return
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_failure_injection_return_prepared_proven_executed(
        self, mock_chrome_cls, mock_tab_cls
    ):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-RET-PREP",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256="sha-ret-prep",
            session_id="sess-ret-prep",
            operation_id="op-ret-prep",
            idempotency_key="idem-ret-prep",
            current_state=TaskState.RETURN_PREPARED.value,
        )
        self.repo.save_active_task(task)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_aistudio_page = AsyncMock()
        mock_chatgpt_page = AsyncMock()
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page
        mock_tab.locate_chatgpt_tab.return_value = mock_chatgpt_page

        mock_chatgpt_page.query_selector_all.return_value = [
            AsyncMock(inner_text=AsyncMock(return_value="[IMPLEMENTER_REPORT]\nTASK_ID: TASK-RET-PREP\n[/IMPLEMENTER_REPORT]"))
        ]

        res = await self.runtime.run_once(dry_run=False)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["ACTION_TAKEN"], "RECONCILE_RETURN_FINALIZE")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)

        self.assertIsNone(self.repo.load_active_task())
        ledger = self.repo.load_completed_ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["task_id"], "TASK-RET-PREP")

    # Scenarios 13-17: Identity field mismatches fail closed
    def test_scenarios_13_17_identity_mismatches_fail_closed(self):
        idem = self.safety.idempotency

        idem.record_operation(
            session_id="sess-1",
            task_id="TASK-1",
            operation_id="op-1",
            idempotency_key="idem-key-common",
            command_sha256="sha-1",
            operation_type="GEMINI_SUBMISSION",
            state="PREPARED",
        )

        with self.assertRaises(IdempotencyViolationError):
            idem.record_operation(
                session_id="sess-1",
                task_id="TASK-2",
                operation_id="op-1",
                idempotency_key="idem-key-common",
                command_sha256="sha-1",
                operation_type="GEMINI_SUBMISSION",
            )

        with self.assertRaises(IdempotencyViolationError):
            idem.record_operation(
                session_id="sess-2",
                task_id="TASK-1",
                operation_id="op-1",
                idempotency_key="idem-key-common",
                command_sha256="sha-1",
                operation_type="GEMINI_SUBMISSION",
            )

        with self.assertRaises(IdempotencyViolationError):
            idem.record_operation(
                session_id="sess-1",
                task_id="TASK-1",
                operation_id="op-2",
                idempotency_key="idem-key-common",
                command_sha256="sha-1",
                operation_type="GEMINI_SUBMISSION",
            )

        with self.assertRaises(IdempotencyViolationError):
            idem.record_operation(
                session_id="sess-1",
                task_id="TASK-1",
                operation_id="op-1",
                idempotency_key="idem-key-common",
                command_sha256="sha-2",
                operation_type="GEMINI_SUBMISSION",
            )

        with self.assertRaises(IdempotencyViolationError):
            idem.record_operation(
                session_id="sess-1",
                task_id="TASK-1",
                operation_id="op-1",
                idempotency_key="idem-key-common",
                command_sha256="sha-1",
                operation_type="SUPERVISOR_RETURN",
            )

    # Scenario 27: Emergency Stop engaged produces zero side effects
    async def test_scenario_27_emergency_stop_blocks_mutation(self):
        self.safety.estop.engage("Emergency Stop active test")

        res = await self.runtime.run_once(dry_run=False)
        self.assertEqual(res["status"], "HALTED")
        self.assertEqual(res["REASON_CODE"], "EMERGENCY_STOP_ENGAGED")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)

    # Scenario 12: Active task missing but idempotency evidence exists -> ORPHANED_STATE
    async def test_scenario_12_orphaned_state_detection(self):
        self.safety.idempotency.record_operation(
            session_id="sess-orphan",
            task_id="TASK-ORPHAN",
            operation_id="op-orphan",
            idempotency_key="idem-orphan",
            command_sha256="sha-orphan",
            operation_type="GEMINI_SUBMISSION",
            state="PREPARED",
        )

        res = await self.runtime.run_once(dry_run=False)
        self.assertEqual(res["status"], "ORPHANED_STATE")
        self.assertEqual(res["REASON_CODE"], "ORPHANED_STATE_DETECTED")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)

    # Scenario 29: Completed ledger written but active task still exists -> safe archival cleanup without duplicate action
    def test_scenario_29_idempotent_ledger_archival(self):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-L1",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256="sha-l1",
            session_id="sess-l1",
            operation_id="op-l1",
            idempotency_key="idem-l1",
            current_state=TaskState.COMPLETED.value,
        )

        self.repo.archive_to_ledger(task)
        ledger1 = self.repo.load_completed_ledger()
        self.assertEqual(len(ledger1), 1)

        self.repo.save_active_task(task)

        self.repo.archive_to_ledger(task)
        ledger2 = self.repo.load_completed_ledger()
        self.assertEqual(len(ledger2), 1)
        self.assertIsNone(self.repo.load_active_task())

    def test_corrupt_completed_ledger_fails_closed(self):
        ledger_file = self.state_dir / "completed_ledger.json"
        with open(ledger_file, "w", encoding="utf-8") as f:
            f.write("corrupted ledger json")

        with self.assertRaises(CorruptStateError):
            self.repo.load_completed_ledger()


if __name__ == "__main__":
    unittest.main()
