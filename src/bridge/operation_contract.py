from dataclasses import dataclass
from typing import Dict, Any


@dataclass(frozen=True)
class OperationContract:
    """
    Contract defining a single external browser mutation.
    Ensures exactly-once identity assertions before any side effect.
    """

    session_id: str
    task_id: str
    phase: int
    operation_id: str
    idempotency_key: str
    command_sha256: str
    operation_type: str  # "GEMINI_SUBMISSION" or "SUPERVISOR_RETURN"
    payload: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "phase": self.phase,
            "operation_id": self.operation_id,
            "idempotency_key": self.idempotency_key,
            "command_sha256": self.command_sha256,
            "operation_type": self.operation_type,
            "payload_length": len(self.payload),
        }
