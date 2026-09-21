import unittest
from unittest.mock import AsyncMock, MagicMock
import tempfile
from pathlib import Path

from src.protocol.supervisor_protocol import SupervisorCommand
from src.state.task_state import (
    TaskState,
    ActiveTaskData,
    CURRENT_SCHEMA_VERSION,
)
from src.state.state_repository import StateRepository
from src.safety.orchestrator import SafetyOrchestrator
from src.bridge.submission import GeminiSubmitter, SubmissionError, SubmissionUncertainError
from src.utils.hashing import compute_sha256


class TestPhase6Submission(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)
        self.repo = StateRepository(state_dir=self.state_dir)
        self.safety = SafetyOrchestrator(state_dir=self.state_dir)
        self.submitter = GeminiSubmitter(self.repo, self.safety)

        self.raw_cmd_text = (
            "[SUPERVISOR]\n"
            "TASK_ID: TASK-600\n"
            "PHASE: 1\n"
            "ACTION: EXECUTE\n"
            "SEND_TO: GEMINI\n\n"
            "Perform submission step.\n"
            "[/SUPERVISOR]"
        )

        self.cmd = SupervisorCommand(
            task_id="TASK-600",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            zip_required=False,
            headers={},
            body="Perform submission step.",
            raw_canonical_block=self.raw_cmd_text,
            command_sha256=compute_sha256(self.raw_cmd_text),
        )

        self.task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-600",
            phase=1,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256=self.cmd.command_sha256,
            session_id="sess-600",
            operation_id="op-600",
            idempotency_key=f"idem_submit_{self.cmd.command_sha256}",
            current_state=TaskState.SUBMISSION_PREPARED.value,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    async def test_normal_submission_flow(self):
        mock_page = AsyncMock()
        mock_composer = AsyncMock()
        mock_composer.input_value.return_value = self.raw_cmd_text
        mock_send_btn = AsyncMock()

        async def mock_query_selector(sel):
            if "textarea" in sel or "contenteditable" in sel:
                return mock_composer
            if "run" in sel or "button" in sel or "Send" in sel:
                return mock_send_btn
            return None

        mock_page.query_selector.side_effect = mock_query_selector

        self.repo.save_active_task(self.task)

        success, side_effects, details = await self.submitter.submit_command(
            command=self.cmd,
            ai_studio_page=mock_page,
            task=self.task,
            session_id="sess-600",
            dry_run=False,
        )

        self.assertTrue(success)
        self.assertEqual(side_effects, 1)
        mock_send_btn.click.assert_called_once()

        # Verify state updated to WAITING_FOR_IMPLEMENTER
        updated_task = self.repo.load_active_task()
        self.assertEqual(updated_task.get_state_enum(), TaskState.WAITING_FOR_IMPLEMENTER)
        self.assertTrue(updated_task.submission_confirmed)

    async def test_dry_run_zero_side_effects(self):
        mock_page = AsyncMock()
        mock_composer = AsyncMock()
        mock_page.query_selector.return_value = mock_composer

        success, side_effects, details = await self.submitter.submit_command(
            command=self.cmd,
            ai_studio_page=mock_page,
            task=self.task,
            session_id="sess-600",
            dry_run=True,
        )

        self.assertTrue(success)
        self.assertEqual(side_effects, 0)
        self.assertEqual(details["state_mutations"], 0)
        # Ensure no click happened
        mock_composer.click.assert_not_called()

    async def test_composer_missing_fails_closed(self):
        mock_page = AsyncMock()
        mock_page.query_selector.return_value = None

        self.repo.save_active_task(self.task)

        with self.assertRaises(SubmissionError):
            await self.submitter.submit_command(
                command=self.cmd,
                ai_studio_page=mock_page,
                task=self.task,
                session_id="sess-600",
                dry_run=False,
            )

        updated = self.repo.load_active_task()
        self.assertEqual(updated.get_state_enum(), TaskState.MANUAL_REVIEW_REQUIRED)

    async def test_post_click_uncertainty_fails_closed(self):
        mock_page = AsyncMock()
        mock_composer = AsyncMock()
        mock_composer.input_value.return_value = self.raw_cmd_text
        mock_send_btn = AsyncMock()
        mock_send_btn.click.side_effect = Exception("Network timeout during click")

        async def mock_query_selector(sel):
            if "textarea" in sel or "contenteditable" in sel:
                return mock_composer
            return mock_send_btn

        mock_page.query_selector.side_effect = mock_query_selector

        self.repo.save_active_task(self.task)

        with self.assertRaises(SubmissionUncertainError):
            await self.submitter.submit_command(
                command=self.cmd,
                ai_studio_page=mock_page,
                task=self.task,
                session_id="sess-600",
                dry_run=False,
            )

        updated = self.repo.load_active_task()
        self.assertEqual(updated.get_state_enum(), TaskState.MANUAL_REVIEW_REQUIRED)

    async def test_duplicate_submission_blocked_by_idempotency(self):
        mock_page = AsyncMock()
        mock_composer = AsyncMock()
        mock_composer.input_value.return_value = self.raw_cmd_text
        mock_send_btn = AsyncMock()

        mock_page.query_selector.return_value = mock_composer

        async def mock_query_selector(sel):
            if "textarea" in sel:
                return mock_composer
            return mock_send_btn

        mock_page.query_selector.side_effect = mock_query_selector

        self.repo.save_active_task(self.task)

        # First submit
        await self.submitter.submit_command(
            command=self.cmd,
            ai_studio_page=mock_page,
            task=self.task,
            session_id="sess-600",
            dry_run=False,
        )

        # Second submit attempt must fail closed due to idempotency violation
        with self.assertRaises(Exception):
            await self.submitter.submit_command(
                command=self.cmd,
                ai_studio_page=mock_page,
                task=self.task,
                session_id="sess-600",
                dry_run=False,
            )


if __name__ == "__main__":
    unittest.main()
