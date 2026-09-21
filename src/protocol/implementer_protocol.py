from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from src.protocol.supervisor_protocol import ProtocolError, MalformedCommandError


@dataclass(frozen=True)
class ImplementerReport:
    task_id: str
    phase: int
    status: str
    ready_for_review: bool
    headers: Dict[str, str]
    body: str
    raw_report_block: str


START_REPORT_TAG = "[IMPLEMENTER_REPORT]"
END_REPORT_TAG = "[/IMPLEMENTER_REPORT]"


class ImplementerReportParser:
    """Parser for Google AI Studio / Gemini Implementer Reports."""

    @staticmethod
    def extract_report(text: str) -> Optional[ImplementerReport]:
        """
        Extracts and validates an [IMPLEMENTER_REPORT] block from text.
        Returns None if no report tag is present.
        Raises MalformedCommandError if report tag is present but malformed.
        """
        if START_REPORT_TAG not in text and END_REPORT_TAG not in text:
            return None

        start_count = text.count(START_REPORT_TAG)
        end_count = text.count(END_REPORT_TAG)

        if start_count != end_count:
            raise MalformedCommandError(
                f"Report tag boundary mismatch: {start_count} start, {end_count} end tags."
            )

        start_idx = text.rfind(START_REPORT_TAG)  # Latest report wins
        end_idx = text.find(END_REPORT_TAG, start_idx)

        if end_idx == -1:
            raise MalformedCommandError("Missing end tag [/IMPLEMENTER_REPORT] for report.")

        raw_report_block = text[start_idx : end_idx + len(END_REPORT_TAG)]

        inside_content = raw_report_block[
            len(START_REPORT_TAG) : -len(END_REPORT_TAG)
        ].strip()

        lines = inside_content.splitlines()
        headers: Dict[str, str] = {}
        body_lines: List[str] = []
        in_headers = True

        for line in lines:
            if in_headers and ":" in line:
                k, v = line.split(":", 1)
                k_clean = k.strip().upper()
                v_clean = v.strip()
                if k_clean in ["TASK_ID", "PHASE", "STATUS", "READY_FOR_REVIEW"]:
                    headers[k_clean] = v_clean
                else:
                    in_headers = False
                    body_lines.append(line)
            else:
                in_headers = False
                body_lines.append(line)

        # Validations
        if "TASK_ID" not in headers or not headers["TASK_ID"]:
            raise MalformedCommandError("Missing TASK_ID in Implementer Report.")

        if "PHASE" not in headers:
            raise MalformedCommandError("Missing PHASE in Implementer Report.")

        try:
            phase_val = int(headers["PHASE"])
        except ValueError:
            raise MalformedCommandError(
                f"PHASE header in Implementer Report must be integer, got '{headers['PHASE']}'."
            )

        if "STATUS" not in headers or not headers["STATUS"]:
            raise MalformedCommandError("Missing STATUS in Implementer Report.")

        ready_for_review_raw = headers.get("READY_FOR_REVIEW", "false").lower()
        ready_for_review = ready_for_review_raw in ("true", "1", "yes")

        body = "\n".join(body_lines).strip()

        return ImplementerReport(
            task_id=headers["TASK_ID"],
            phase=phase_val,
            status=headers["STATUS"],
            ready_for_review=ready_for_review,
            headers=headers,
            body=body,
            raw_report_block=raw_report_block,
        )
