from typing import Optional
from src.protocol.supervisor_protocol import SupervisorCommand, ProtocolError
from src.protocol.implementer_protocol import ImplementerReport


class SessionGuardError(ProtocolError):
    """Raised when task correlation or session integrity check fails."""

    pass


class SessionGuard:
    """Validates session integrity and task identity correlations."""

    @staticmethod
    def validate_report_correlation(
        active_task_id: str,
        active_phase: int,
        report: ImplementerReport,
    ) -> None:
        """
        Validates that an Implementer Report matches the active task_id and phase.
        Fails closed on mismatch.
        """
        if report.task_id != active_task_id:
            raise SessionGuardError(
                f"Task ID mismatch in report! Expected '{active_task_id}', got '{report.task_id}'."
            )

        if report.phase != active_phase:
            raise SessionGuardError(
                f"Phase mismatch in report! Expected '{active_phase}', got '{report.phase}'."
            )

    @staticmethod
    def validate_command_identity(
        cmd1: SupervisorCommand,
        cmd2: SupervisorCommand,
    ) -> None:
        """Validates that two SupervisorCommands have identical canonical SHA256 hashes."""
        if cmd1.command_sha256 != cmd2.command_sha256:
            raise SessionGuardError(
                f"Command SHA256 hash mismatch! '{cmd1.command_sha256}' vs '{cmd2.command_sha256}'."
            )
