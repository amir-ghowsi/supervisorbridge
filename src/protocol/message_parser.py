from typing import Optional, List, Dict, Any
from src.protocol.supervisor_protocol import (
    SupervisorProtocolParser,
    SupervisorCommand,
)
from src.protocol.implementer_protocol import (
    ImplementerReportParser,
    ImplementerReport,
)
from src.protocol.command_classifier import CommandClassifier, CommandClassification


class MessageParser:
    """High-level message parsing utility."""

    def __init__(self):
        self.supervisor_parser = SupervisorProtocolParser()
        self.report_parser = ImplementerReportParser()

    def parse_chatgpt_messages(
        self, messages: List[Dict[str, str]]
    ) -> Optional[SupervisorCommand]:
        """
        Parses list of message dicts ({'role': ..., 'text': ...}) from ChatGPT.
        Iterates in order and returns the latest executable candidate.
        If the newest candidate is malformed, raises ProtocolError and fails closed.
        """
        executable_candidates: List[SupervisorCommand] = []

        for msg in messages:
            role = msg.get("role", "assistant")
            text = msg.get("text", "")
            classification = CommandClassifier.classify(text, role=role)

            if classification == CommandClassification.MALFORMED_COMMAND:
                # Malformed command fails closed
                # Triggers protocol error
                SupervisorProtocolParser.extract_executable_command(text, role=role)

            if classification == CommandClassification.EXECUTABLE_SUPERVISOR_COMMAND:
                cmd = SupervisorProtocolParser.extract_executable_command(text, role=role)
                if cmd:
                    executable_candidates.append(cmd)

        if executable_candidates:
            return executable_candidates[-1]
        return None

    def parse_gemini_report(self, text: str) -> Optional[ImplementerReport]:
        """Parses an Implementer Report from Gemini text output."""
        return self.report_parser.extract_report(text)
