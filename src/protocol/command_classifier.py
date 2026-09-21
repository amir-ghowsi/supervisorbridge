from enum import Enum
from typing import Dict, Any
from src.protocol.supervisor_protocol import (
    SupervisorProtocolParser,
    START_ENVELOPE,
    END_ENVELOPE,
)
from src.protocol.implementer_protocol import (
    ImplementerReportParser,
    START_REPORT_TAG,
    END_REPORT_TAG,
)


class CommandClassification(Enum):
    EXECUTABLE_SUPERVISOR_COMMAND = "EXECUTABLE_SUPERVISOR_COMMAND"
    BARE_SUPERVISOR_COMMAND = "BARE_SUPERVISOR_COMMAND"
    IMPLEMENTER_REPORT = "IMPLEMENTER_REPORT"
    USER_PROMPT = "USER_PROMPT"
    GENERAL_PROSE = "GENERAL_PROSE"
    MALFORMED_COMMAND = "MALFORMED_COMMAND"


class CommandClassifier:
    """Classifies message text based on role and presence of protocol envelopes."""

    @staticmethod
    def classify(text: str, role: str = "assistant") -> CommandClassification:
        if role.lower() == "user":
            return CommandClassification.USER_PROMPT

        if START_ENVELOPE in text or END_ENVELOPE in text:
            try:
                cmd = SupervisorProtocolParser.extract_executable_command(text, role=role)
                if cmd:
                    return CommandClassification.EXECUTABLE_SUPERVISOR_COMMAND
            except Exception:
                return CommandClassification.MALFORMED_COMMAND

        if "[SUPERVISOR]" in text or "[/SUPERVISOR]" in text:
            return CommandClassification.BARE_SUPERVISOR_COMMAND

        if START_REPORT_TAG in text or END_REPORT_TAG in text:
            try:
                rpt = ImplementerReportParser.extract_report(text)
                if rpt:
                    return CommandClassification.IMPLEMENTER_REPORT
            except Exception:
                return CommandClassification.MALFORMED_COMMAND

        return CommandClassification.GENERAL_PROSE
