import unittest
from unittest.mock import AsyncMock
import tempfile
from pathlib import Path

from src.protocol.implementer_protocol import ImplementerReport
from src.state.task_state import ActiveTaskData, TaskState, CURRENT_SCHEMA_VERSION
from src.state.state_repository import StateRepository
from src.safety.orchestrator import SafetyOrchestrator
from src.bridge.response_inspector import ResponseInspector, ResponseInspectionError
from src.bridge.return_to_supervisor import SupervisorReturner, ReturnError, ReturnUncertainError


class TestPhase8ReturnPath(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)
        self.repo = StateRepository(state_dir=self.state_dir)
        self.safety = SafetyOrchestrator(state_dir=self.state_dir)
        self.inspector = ResponseInspector()
        self.returner = SupervisorReturner(self.repo, self.safety)

        self.valid_raw_report = (
            "[IMPLEMENTER_REPORT]\n"
            "TASK_ID: TASK-800\n"
            "PHASE: 1\n"
            "STATUS: COMPLETED\n"
            "READY_FOR_REVIEW: true\n\n"
            "Step 1 completed successfully.\n"
            "[/IMPLEMENTER_REPORT]"
        )

        self.report = ImplementerReport(
            task_id="TASK-800",
            phase=1,
            status="COMPLETED",
            ready_for_review=True,
            headers={},
            body="Step 1 completed successfully.",
            raw_report_block=self.valid_raw_report,
        )

        self.task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-800",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256="commandsha800",
            session_id="sess-800",
            operation_id="op-800",
            idempotency_key="idem-800",
            current_state=TaskState.WAITING_FOR_IMPLEMENTER.value,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_response_inspector_validation(self):
        # Valid report succeeds
        rpt = self.inspector.inspect_and_validate_report(
            gemini_text=self.valid_raw_report,
            active_task_id="TASK-800",
            active_phase=1,
        )
        self.assertEqual(rpt.task_id, "TASK-800")

        # Wrong task ID fails closed
        with self.assertRaises(ResponseInspectionError):
            self.inspector.inspect_and_validate_report(
                gemini_text=self.valid_raw_report,
                active_task_id="WRONG-TASK",
                active_phase=1,
            )

        # Wrong phase fails closed
        with self.assertRaises(ResponseInspectionError):
            self.inspector.inspect_and_validate_report(
                gemini_text=self.valid_raw_report,
                active_task_id="TASK-800",
                active_phase=2,
            )

        # Arbitrary prose without report tags fails closed
        with self.assertRaises(ResponseInspectionError):
            self.inspector.inspect_and_validate_report(
                gemini_text="I finished doing the work, here is code.",
                active_task_id="TASK-800",
                active_phase=1,
            )

    async def test_normal_return_to_supervisor_flow(self):
        mock_page = AsyncMock()
        mock_composer = AsyncMock()
        mock_composer.input_value.return_value = self.valid_raw_report
        mock_send_btn = AsyncMock()

        async def mock_query_selector(sel):
            if "prompt-textarea" in sel or "placeholder" in sel:
                return mock_composer
            if "send-button" in sel or "Send prompt" in sel:
                return mock_send_btn
            return None

        mock_page.query_selector.side_effect = mock_query_selector

        # Update task state to IMPLEMENTER_RESPONSE_VALIDATED
        self.task.transition_to(TaskState.IMPLEMENTER_RESPONSE_DETECTED)
        self.task.transition_to(TaskState.IMPLEMENTER_RESPONSE_VALIDATED)
        self.repo.save_active_task(self.task)

        success, side_effects, details = await self.returner.return_report_to_supervisor(
            report=self.report,
            chatgpt_page=mock_page,
            task=self.task,
            session_id="sess-800",
            dry_run=False,
        )

        self.assertTrue(success)
        self.assertEqual(side_effects, 1)
        mock_send_btn.click.assert_called_once()

        # Active task should now be cleared and archived into completed ledger
        self.assertIsNone(self.repo.load_active_task())
        ledger = self.repo.load_completed_ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["task_id"], "TASK-800")
        self.assertEqual(ledger[0]["current_state"], TaskState.COMPLETED.value)

    async def test_duplicate_return_prevention(self):
        mock_page = AsyncMock()
        mock_composer = AsyncMock()
        mock_composer.input_value.return_value = self.valid_raw_report
        mock_send_btn = AsyncMock()

        async def mock_query_selector(sel):
            if "prompt-textarea" in sel or "placeholder" in sel:
                return mock_composer
            if "send-button" in sel or "Send prompt" in sel:
                return mock_send_btn
            return None

        mock_page.query_selector.side_effect = mock_query_selector

        self.task.transition_to(TaskState.IMPLEMENTER_RESPONSE_DETECTED)
        self.task.transition_to(TaskState.IMPLEMENTER_RESPONSE_VALIDATED)
        self.repo.save_active_task(self.task)

        # First return
        await self.returner.return_report_to_supervisor(
            report=self.report,
            chatgpt_page=mock_page,
            task=self.task,
            session_id="sess-800",
            dry_run=False,
        )

        # Second return attempt must fail closed via idempotency
        with self.assertRaises(Exception):
            await self.returner.return_report_to_supervisor(
                report=self.report,
                chatgpt_page=mock_page,
                task=self.task,
                session_id="sess-800",
                dry_run=False,
            )


if __name__ == "__main__":
    unittest.main()
