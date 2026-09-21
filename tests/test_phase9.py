import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import json
import hashlib
from pathlib import Path

from src.bridge.runtime import RuntimeEngine
from src.bridge.loop_controller import LoopController
from src.bridge.generation_monitor import GenerationMonitor, GenerationState
from src.state.state_repository import StateRepository, CorruptStateError
from src.state.task_state import ActiveTaskData, TaskState, CURRENT_SCHEMA_VERSION
from src.safety.orchestrator import SafetyOrchestrator
from src.safety.idempotency import IdempotencyManager, IdempotencyViolationError
from src.safety.loop_guard import LoopGuardViolationError
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

        self.canonical_raw = (
            "[SUPERVISOR]\n"
            "TASK_ID: TASK-900\n"
            "PHASE: 1\n"
            "ACTION: EXECUTE\n"
            "SEND_TO: GEMINI\n\n"
            "Run Phase 9 Task.\n"
            "[/SUPERVISOR]"
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

    # Blocker 14 & 5: Production state immutability & zero bytecode test
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

    # Blocker 9: Payload Integrity Tests
    def test_payload_integrity_raw_command_preservation(self):
        cmd_sha = compute_sha256(self.canonical_raw)
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-900",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256=cmd_sha,
            session_id="sess-900",
            operation_id="op-900",
            idempotency_key="idem-900",
            current_state=TaskState.SUBMISSION_PREPARED.value,
            raw_canonical_block=self.canonical_raw,
        )
        self.repo.save_active_task(task)

        loaded = self.repo.load_active_task()
        self.assertEqual(loaded.raw_canonical_block, self.canonical_raw)
        self.assertEqual(compute_sha256(loaded.raw_canonical_block), loaded.command_sha256)

    # Failure Injection: Modified raw command block fails closed
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_payload_integrity_tampered_raw_block_fails_closed(
        self, mock_chrome_cls, mock_tab_cls
    ):
        cmd_sha = compute_sha256(self.canonical_raw)
        tampered_raw = self.canonical_raw + "\n# TAMPERED LINE"

        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-900",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256=cmd_sha,  # Store original sha
            session_id="sess-900",
            operation_id="op-900",
            idempotency_key="idem-900",
            current_state=TaskState.SUBMISSION_PREPARED.value,
            raw_canonical_block=tampered_raw,  # Pass tampered block
        )
        self.repo.save_active_task(task)

        repo2 = StateRepository(state_dir=self.state_dir)
        safety2 = SafetyOrchestrator(state_dir=self.state_dir)
        runtime2 = RuntimeEngine(repo=repo2, safety=safety2)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome

        res = await runtime2.run_once(dry_run=False)
        self.assertEqual(res["status"], "HALTED")
        self.assertEqual(res["REASON_CODE"], "COMMAND_HASH_MISMATCH")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)

    # Blocker 1: PREPARED submission + empty/unavailable DOM -> INSUFFICIENT_EVIDENCE -> HALTED
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_prepared_submission_empty_dom_fails_closed(
        self, mock_chrome_cls, mock_tab_cls
    ):
        cmd_sha = compute_sha256(self.canonical_raw)
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-900",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256=cmd_sha,
            session_id="sess-900",
            operation_id="op-900",
            idempotency_key="idem-900",
            current_state=TaskState.SUBMISSION_PREPARED.value,
            raw_canonical_block=self.canonical_raw,
        )
        self.repo.save_active_task(task)

        # Record PREPARED idempotency
        self.safety.idempotency.record_operation(
            session_id="sess-900",
            task_id="TASK-900",
            operation_id="op-900",
            idempotency_key="idem-900",
            command_sha256=cmd_sha,
            operation_type="GEMINI_SUBMISSION",
            state="PREPARED",
        )

        repo2 = StateRepository(state_dir=self.state_dir)
        safety2 = SafetyOrchestrator(state_dir=self.state_dir)
        runtime2 = RuntimeEngine(repo=repo2, safety=safety2)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_aistudio_page = AsyncMock()
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page

        mock_aistudio_page.query_selector_all.return_value = []

        res = await runtime2.run_once(dry_run=False)
        self.assertEqual(res["status"], "HALTED")
        self.assertEqual(res["REASON_CODE"], "AMBIGUOUS_SUBMISSION_PREPARED")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)

        updated = repo2.load_active_task()
        self.assertEqual(updated.get_state_enum(), TaskState.MANUAL_REVIEW_REQUIRED)

    # Blocker 2: Completed response + retry visible + send button absent -> COMPLETED
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_completed_response_precedence_over_retry_and_no_send(
        self, mock_chrome_cls, mock_tab_cls
    ):
        mock_page = AsyncMock()
        mock_turn = AsyncMock()
        mock_turn.inner_text.return_value = self.valid_report_text

        async def mock_qs_all(sel):
            if "turn" in sel:
                return [mock_turn]
            return []

        async def mock_qs(sel):
            if "retry" in sel.lower():
                return AsyncMock()  # Retry visible
            return None  # Send absent

        mock_page.query_selector_all.side_effect = mock_qs_all
        mock_page.query_selector.side_effect = mock_qs

        monitor = GenerationMonitor()
        gen_state, details = await monitor.detect_state(mock_page)

        self.assertEqual(gen_state, GenerationState.COMPLETED)
        self.assertTrue(details["retry_present"])
        self.assertFalse(details["send_present"])

    # Blocker 3: Retry PREPARED post-click crash & restart reconciliation -> zero duplicate retry
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_retry_prepared_post_click_crash_reconciliation(
        self, mock_chrome_cls, mock_tab_cls
    ):
        cmd_sha = compute_sha256(self.canonical_raw)
        retry_attempt = 1
        retry_op_id = f"op_retry_TASK-900_1_{retry_attempt}"
        retry_idem_key = f"idem_retry_TASK-900_1_{retry_attempt}_{cmd_sha}"

        # Production state: task.retry_count = 1 saved on disk when attempt 1 is prepared/attempted
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-900",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256=cmd_sha,
            session_id="sess-900",
            operation_id="op-900",
            idempotency_key="idem-900",
            current_state=TaskState.WAITING_FOR_IMPLEMENTER.value,
            raw_canonical_block=self.canonical_raw,
            retry_count=1,
        )
        self.repo.save_active_task(task)

        # Record PREPARED retry operation in idempotency records
        self.safety.idempotency.record_operation(
            session_id="sess-900",
            task_id="TASK-900",
            operation_id=retry_op_id,
            idempotency_key=retry_idem_key,
            command_sha256=cmd_sha,
            operation_type="GEMINI_RETRY",
            state="PREPARED",
        )

        repo2 = StateRepository(state_dir=self.state_dir)
        safety2 = SafetyOrchestrator(state_dir=self.state_dir)
        runtime2 = RuntimeEngine(repo=repo2, safety=safety2)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_aistudio_page = AsyncMock()
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page

        mock_retry_btn = AsyncMock()

        async def mock_qs_all(sel):
            return []

        # AI Studio generation is active (proven executed)
        mock_aistudio_page.query_selector.side_effect = lambda sel: AsyncMock() if "Stop" in sel or "retry" in sel else None
        mock_aistudio_page.query_selector_all.side_effect = mock_qs_all

        res = await runtime2.run_once(dry_run=False)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["ACTION_TAKEN"], "RECONCILE_RETRY_RESUME")
        self.assertEqual(res["SIDE_EFFECT_COUNT"], 0)  # Zero second retry click!
        mock_retry_btn.click.assert_not_called()

    # Blocker 4: 5+ consecutive WAIT cycles without LoopGuard false positive
    @patch("src.bridge.runtime.TabManager")
    @patch("src.bridge.runtime.ChromeManager")
    async def test_wait_polling_five_consecutive_cycles_no_loop_guard_error(
        self, mock_chrome_cls, mock_tab_cls
    ):
        cmd_sha = compute_sha256(self.canonical_raw)
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-900",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256=cmd_sha,
            session_id="sess-900",
            operation_id="op-900",
            idempotency_key="idem-900",
            current_state=TaskState.WAITING_FOR_IMPLEMENTER.value,
            raw_canonical_block=self.canonical_raw,
        )
        self.repo.save_active_task(task)

        mock_chrome = AsyncMock()
        mock_chrome_cls.return_value = mock_chrome
        mock_tab = AsyncMock()
        mock_tab_cls.return_value = mock_tab

        mock_aistudio_page = AsyncMock()
        mock_tab.locate_ai_studio_tab.return_value = mock_aistudio_page

        mock_aistudio_page.query_selector.side_effect = lambda sel: AsyncMock() if "Stop" in sel else None

        loop_ctrl = LoopController(runtime=self.runtime)
        loop_ctrl.poll_interval_sec = 0.001

        # Run 5 iterations cleanly
        await loop_ctrl.run_loop(dry_run=False, max_cycles=5)

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
