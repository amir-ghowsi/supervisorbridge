import unittest
from src.protocol.supervisor_protocol import (
    SupervisorProtocolParser,
    SupervisorCommand,
    MalformedCommandError,
    DuplicateEnvelopeError,
)
from src.protocol.implementer_protocol import (
    ImplementerReportParser,
    ImplementerReport,
)
from src.protocol.command_classifier import CommandClassifier, CommandClassification
from src.protocol.message_parser import MessageParser
from src.protocol.session_guard import SessionGuard, SessionGuardError
from src.utils.hashing import compute_sha256


class TestPhase3Protocol(unittest.TestCase):

    def setUp(self):
        self.valid_command_text = (
            "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "[SUPERVISOR]\n"
            "TASK_ID: TASK-1001\n"
            "PHASE: 1\n"
            "ACTION: INITIALIZE\n"
            "SEND_TO: GEMINI\n"
            "ZIP_REQUIRED: true\n\n"
            "Please run step 1.\n"
            "[/SUPERVISOR]\n"
            "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
        )

        self.valid_report_text = (
            "[IMPLEMENTER_REPORT]\n"
            "TASK_ID: TASK-1001\n"
            "PHASE: 1\n"
            "STATUS: COMPLETED\n"
            "READY_FOR_REVIEW: true\n\n"
            "Phase 1 work completed successfully.\n"
            "[/IMPLEMENTER_REPORT]"
        )

    def test_valid_supervisor_command(self):
        cmd = SupervisorProtocolParser.extract_executable_command(
            self.valid_command_text, role="assistant"
        )
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.task_id, "TASK-1001")
        self.assertEqual(cmd.phase, 1)
        self.assertEqual(cmd.action, "INITIALIZE")
        self.assertEqual(cmd.send_to, "GEMINI")
        self.assertTrue(cmd.zip_required)
        self.assertEqual(cmd.body, "Please run step 1.")

        # Check raw canonical block exact match
        expected_raw = (
            "[SUPERVISOR]\n"
            "TASK_ID: TASK-1001\n"
            "PHASE: 1\n"
            "ACTION: INITIALIZE\n"
            "SEND_TO: GEMINI\n"
            "ZIP_REQUIRED: true\n\n"
            "Please run step 1.\n"
            "[/SUPERVISOR]"
        )
        self.assertEqual(cmd.raw_canonical_block, expected_raw)
        self.assertEqual(cmd.command_sha256, compute_sha256(expected_raw))

    def test_bare_supervisor_command_rejected(self):
        bare_text = (
            "[SUPERVISOR]\n"
            "TASK_ID: TASK-1001\n"
            "PHASE: 1\n"
            "ACTION: INITIALIZE\n"
            "SEND_TO: GEMINI\n"
            "[/SUPERVISOR]"
        )
        cmd = SupervisorProtocolParser.extract_executable_command(bare_text, role="assistant")
        self.assertIsNone(cmd)

    def test_user_role_rejection(self):
        cmd = SupervisorProtocolParser.extract_executable_command(
            self.valid_command_text, role="user"
        )
        self.assertIsNone(cmd)

    def test_malformed_command_headers(self):
        # Missing TASK_ID
        no_task_id = (
            "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "[SUPERVISOR]\n"
            "PHASE: 1\n"
            "ACTION: INIT\n"
            "SEND_TO: GEMINI\n"
            "[/SUPERVISOR]\n"
            "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
        )
        with self.assertRaises(MalformedCommandError):
            SupervisorProtocolParser.extract_executable_command(no_task_id, role="assistant")

        # Non-integer PHASE
        invalid_phase = (
            "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "[SUPERVISOR]\n"
            "TASK_ID: 1\n"
            "PHASE: ONE\n"
            "ACTION: INIT\n"
            "SEND_TO: GEMINI\n"
            "[/SUPERVISOR]\n"
            "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
        )
        with self.assertRaises(MalformedCommandError):
            SupervisorProtocolParser.extract_executable_command(invalid_phase, role="assistant")

        # Wrong SEND_TO
        wrong_send_to = (
            "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "[SUPERVISOR]\n"
            "TASK_ID: 1\n"
            "PHASE: 1\n"
            "ACTION: INIT\n"
            "SEND_TO: CLAUDE\n"
            "[/SUPERVISOR]\n"
            "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
        )
        with self.assertRaises(MalformedCommandError):
            SupervisorProtocolParser.extract_executable_command(wrong_send_to, role="assistant")

    def test_duplicate_envelopes_fail_closed(self):
        duplicate_text = (
            "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "[SUPERVISOR]\n"
            "TASK_ID: 1\n"
            "PHASE: 1\n"
            "ACTION: INIT\n"
            "SEND_TO: GEMINI\n"
            "[/SUPERVISOR]\n"
            "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>\n"
            "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
        )
        with self.assertRaises((MalformedCommandError, DuplicateEnvelopeError)):
            SupervisorProtocolParser.extract_executable_command(duplicate_text, role="assistant")

    def test_latest_wins_and_newer_malformed_blocks_older_valid(self):
        msg_parser = MessageParser()

        # Test latest valid wins
        messages_valid = [
            {
                "role": "assistant",
                "text": (
                    "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
                    "[SUPERVISOR]\n"
                    "TASK_ID: TASK-1\n"
                    "PHASE: 1\n"
                    "ACTION: STEP1\n"
                    "SEND_TO: GEMINI\n"
                    "[/SUPERVISOR]\n"
                    "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
                ),
            },
            {
                "role": "assistant",
                "text": (
                    "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
                    "[SUPERVISOR]\n"
                    "TASK_ID: TASK-2\n"
                    "PHASE: 2\n"
                    "ACTION: STEP2\n"
                    "SEND_TO: GEMINI\n"
                    "[/SUPERVISOR]\n"
                    "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
                ),
            },
        ]
        cmd = msg_parser.parse_chatgpt_messages(messages_valid)
        self.assertIsNotNone(cmd)
        self.assertEqual(cmd.task_id, "TASK-2")

        # Test newer malformed blocks older valid (fails closed)
        messages_with_malformed = [
            messages_valid[0],
            {
                "role": "assistant",
                "text": (
                    "<<<SUPERVISOR_BRIDGE_COMMAND>>>\n"
                    "[SUPERVISOR]\n"
                    "TASK_ID: TASK-3\n"
                    "# Missing PHASE, ACTION, SEND_TO\n"
                    "[/SUPERVISOR]\n"
                    "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"
                ),
            },
        ]
        with self.assertRaises(MalformedCommandError):
            msg_parser.parse_chatgpt_messages(messages_with_malformed)

    def test_implementer_report_parsing(self):
        report = ImplementerReportParser.extract_report(self.valid_report_text)
        self.assertIsNotNone(report)
        self.assertEqual(report.task_id, "TASK-1001")
        self.assertEqual(report.phase, 1)
        self.assertEqual(report.status, "COMPLETED")
        self.assertTrue(report.ready_for_review)

    def test_session_guard_report_correlation(self):
        report = ImplementerReportParser.extract_report(self.valid_report_text)

        # Matching correlation succeeds
        SessionGuard.validate_report_correlation("TASK-1001", 1, report)

        # Wrong task ID fails closed
        with self.assertRaises(SessionGuardError):
            SessionGuard.validate_report_correlation("TASK-9999", 1, report)

        # Wrong phase fails closed
        with self.assertRaises(SessionGuardError):
            SessionGuard.validate_report_correlation("TASK-1001", 2, report)


if __name__ == "__main__":
    unittest.main()
