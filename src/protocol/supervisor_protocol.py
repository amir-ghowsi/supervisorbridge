import re
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from src.utils.hashing import compute_sha256


class ProtocolError(Exception):
    """Base exception for protocol parsing errors."""

    pass


class MalformedCommandError(ProtocolError):
    """Raised when an executable command envelope or content is malformed."""

    pass


class DuplicateEnvelopeError(ProtocolError):
    """Raised when duplicate outer command envelopes are detected in a single block."""

    pass


@dataclass(frozen=True)
class SupervisorCommand:
    task_id: str
    phase: int
    action: str
    send_to: str
    zip_required: bool
    headers: Dict[str, str]
    body: str
    raw_canonical_block: str
    command_sha256: str


START_ENVELOPE = "<<<SUPERVISOR_BRIDGE_COMMAND>>>"
END_ENVELOPE = "<<<END_SUPERVISOR_BRIDGE_COMMAND>>>"


class SupervisorProtocolParser:
    """Parser for executable Supervisor commands."""

    @staticmethod
    def extract_executable_command(
        text: str, role: str = "assistant"
    ) -> Optional[SupervisorCommand]:
        """
        Parses text for executable Supervisor commands.

        Rules:
        - Must come from 'assistant' role.
        - Bare [SUPERVISOR] without outer envelope is NON-EXECUTABLE.
        - Outer envelope (<<<SUPERVISOR_BRIDGE_COMMAND>>> ... <<<END_SUPERVISOR_BRIDGE_COMMAND>>>) is mandatory.
        - Duplicate outer envelopes in text fail closed.
        - Missing boundaries fail closed.
        - Latest executable candidate wins.
        - Malformed newest candidate fails closed (never silently falls back to older valid executable command).
        """
        if role.lower() != "assistant":
            # Non-assistant messages (e.g. user messages) are never executable
            return None

        if START_ENVELOPE not in text and END_ENVELOPE not in text:
            # No envelope present -> non-executable
            return None

        start_count = text.count(START_ENVELOPE)
        end_count = text.count(END_ENVELOPE)

        # Check for boundary mismatch or duplicates
        if start_count != end_count:
            raise MalformedCommandError(
                f"Boundary count mismatch: {start_count} start envelopes, {end_count} end envelopes."
            )

        # Locate candidates
        candidates = []
        pos = 0
        while True:
            start_idx = text.find(START_ENVELOPE, pos)
            if start_idx == -1:
                break
            end_idx = text.find(END_ENVELOPE, start_idx + len(START_ENVELOPE))
            if end_idx == -1:
                raise MalformedCommandError("Found start envelope without matching end envelope.")

            envelope_content = text[start_idx : end_idx + len(END_ENVELOPE)]
            candidates.append((start_idx, end_idx, envelope_content))
            pos = end_idx + len(END_ENVELOPE)

        if not candidates:
            return None

        # Rule: Duplicate outer envelopes in the exact same candidate block or multiple candidates.
        # Check if the latest candidate is malformed or duplicate
        latest_start_idx, latest_end_idx, latest_envelope = candidates[-1]

        # Inner content between outer tags
        inner_block = text[
            latest_start_idx + len(START_ENVELOPE) : latest_end_idx
        ].strip()

        # Check for nested outer envelopes
        if START_ENVELOPE in inner_block or END_ENVELOPE in inner_block:
            raise DuplicateEnvelopeError("Nested or duplicate outer envelope detected.")

        # Parse canonical raw block [SUPERVISOR]...[/SUPERVISOR]
        sup_start = inner_block.find("[SUPERVISOR]")
        sup_end = inner_block.rfind("[/SUPERVISOR]")

        if sup_start == -1 or sup_end == -1 or sup_end < sup_start:
            raise MalformedCommandError(
                "Missing or malformed [SUPERVISOR] ... [/SUPERVISOR] block inside envelope."
            )

        # Canonical raw block includes [SUPERVISOR] and [/SUPERVISOR]
        canonical_raw_block = inner_block[sup_start : sup_end + len("[/SUPERVISOR]")]

        # Hash computed on exact canonical raw block
        command_sha256 = compute_sha256(canonical_raw_block)

        # Extract header lines and body inside [SUPERVISOR] ... [/SUPERVISOR]
        inside_content = canonical_raw_block[
            len("[SUPERVISOR]") : -len("[/SUPERVISOR]")
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
                # Check if this line is a recognized header
                if k_clean in ["TASK_ID", "PHASE", "ACTION", "SEND_TO", "ZIP_REQUIRED"]:
                    headers[k_clean] = v_clean
                else:
                    in_headers = False
                    body_lines.append(line)
            else:
                in_headers = False
                body_lines.append(line)

        # Header validations
        if "TASK_ID" not in headers or not headers["TASK_ID"]:
            raise MalformedCommandError("Missing or empty TASK_ID header.")

        if "PHASE" not in headers:
            raise MalformedCommandError("Missing PHASE header.")

        try:
            phase_val = int(headers["PHASE"])
        except ValueError:
            raise MalformedCommandError(f"PHASE header must be integer, got '{headers['PHASE']}'.")

        if "ACTION" not in headers or not headers["ACTION"]:
            raise MalformedCommandError("Missing or empty ACTION header.")

        if "SEND_TO" not in headers:
            raise MalformedCommandError("Missing SEND_TO header.")

        if headers["SEND_TO"].upper() != "GEMINI":
            raise MalformedCommandError(f"SEND_TO must be GEMINI, got '{headers['SEND_TO']}'.")

        zip_required = headers.get("ZIP_REQUIRED", "false").lower() in ("true", "1", "yes")
        body = "\n".join(body_lines).strip()

        return SupervisorCommand(
            task_id=headers["TASK_ID"],
            phase=phase_val,
            action=headers["ACTION"],
            send_to=headers["SEND_TO"].upper(),
            zip_required=zip_required,
            headers=headers,
            body=body,
            raw_canonical_block=canonical_raw_block,
            command_sha256=command_sha256,
        )
