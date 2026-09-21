from typing import Optional, Dict, Any
from src.protocol.implementer_protocol import ImplementerReportParser, ImplementerReport
from src.protocol.session_guard import SessionGuard, SessionGuardError
from src.protocol.supervisor_protocol import MalformedCommandError
from src.utils.logger import get_logger


class ResponseInspectionError(Exception):
    """Raised when Gemini response inspection fails or report validation fails."""

    pass


class ResponseInspector:
    """Extracts and validates Implementer Reports from Gemini raw output."""

    def __init__(self):
        self.logger = get_logger()
        self.parser = ImplementerReportParser()

    def inspect_and_validate_report(
        self,
        gemini_text: str,
        active_task_id: str,
        active_phase: int,
    ) -> ImplementerReport:
        """
        Extracts latest Implementer Report and asserts task_id and phase correlation.
        Rejects arbitrary prose or mismatched reports.
        Fails closed.
        """
        try:
            report = self.parser.extract_report(gemini_text)
        except MalformedCommandError as e:
            raise ResponseInspectionError(f"Malformed Implementer Report in Gemini output: {e}") from e

        if not report:
            raise ResponseInspectionError("No [IMPLEMENTER_REPORT] block found in Gemini output.")

        # Validate correlation
        try:
            SessionGuard.validate_report_correlation(
                active_task_id=active_task_id,
                active_phase=active_phase,
                report=report,
            )
        except SessionGuardError as e:
            raise ResponseInspectionError(f"Report correlation validation failed: {e}") from e

        return report
