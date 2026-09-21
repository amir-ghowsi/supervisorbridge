from enum import Enum
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, Any, Set
import time


class TaskState(Enum):
    NO_ACTIVE_TASK = "NO_ACTIVE_TASK"
    COMMAND_DETECTED = "COMMAND_DETECTED"
    COMMAND_VALIDATED = "COMMAND_VALIDATED"
    COMMAND_ACCEPTED = "COMMAND_ACCEPTED"
    SUBMISSION_PREPARED = "SUBMISSION_PREPARED"
    SENT_TO_IMPLEMENTER = "SENT_TO_IMPLEMENTER"
    WAITING_FOR_IMPLEMENTER = "WAITING_FOR_IMPLEMENTER"
    IMPLEMENTER_RESPONSE_DETECTED = "IMPLEMENTER_RESPONSE_DETECTED"
    IMPLEMENTER_RESPONSE_VALIDATED = "IMPLEMENTER_RESPONSE_VALIDATED"
    RETURN_PREPARED = "RETURN_PREPARED"
    RETURNED_TO_SUPERVISOR = "RETURNED_TO_SUPERVISOR"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"


class InvalidStateTransitionError(Exception):
    """Raised when an illegal task state transition is attempted."""

    pass


# Explicit legal state transitions
LEGAL_TRANSITIONS: Dict[TaskState, Set[TaskState]] = {
    TaskState.NO_ACTIVE_TASK: {TaskState.COMMAND_DETECTED},
    TaskState.COMMAND_DETECTED: {TaskState.COMMAND_VALIDATED, TaskState.FAILED, TaskState.MANUAL_REVIEW_REQUIRED},
    TaskState.COMMAND_VALIDATED: {TaskState.COMMAND_ACCEPTED, TaskState.FAILED, TaskState.MANUAL_REVIEW_REQUIRED},
    TaskState.COMMAND_ACCEPTED: {TaskState.SUBMISSION_PREPARED, TaskState.FAILED, TaskState.MANUAL_REVIEW_REQUIRED},
    TaskState.SUBMISSION_PREPARED: {TaskState.SENT_TO_IMPLEMENTER, TaskState.FAILED, TaskState.MANUAL_REVIEW_REQUIRED},
    TaskState.SENT_TO_IMPLEMENTER: {TaskState.WAITING_FOR_IMPLEMENTER, TaskState.FAILED, TaskState.MANUAL_REVIEW_REQUIRED},
    TaskState.WAITING_FOR_IMPLEMENTER: {
        TaskState.WAITING_FOR_IMPLEMENTER,  # Self-loop during polling
        TaskState.IMPLEMENTER_RESPONSE_DETECTED,
        TaskState.SENT_TO_IMPLEMENTER,  # Safe retry
        TaskState.FAILED,
        TaskState.MANUAL_REVIEW_REQUIRED,
    },
    TaskState.IMPLEMENTER_RESPONSE_DETECTED: {
        TaskState.IMPLEMENTER_RESPONSE_VALIDATED,
        TaskState.FAILED,
        TaskState.MANUAL_REVIEW_REQUIRED,
    },
    TaskState.IMPLEMENTER_RESPONSE_VALIDATED: {
        TaskState.RETURN_PREPARED,
        TaskState.FAILED,
        TaskState.MANUAL_REVIEW_REQUIRED,
    },
    TaskState.RETURN_PREPARED: {
        TaskState.RETURNED_TO_SUPERVISOR,
        TaskState.FAILED,
        TaskState.MANUAL_REVIEW_REQUIRED,
    },
    TaskState.RETURNED_TO_SUPERVISOR: {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.MANUAL_REVIEW_REQUIRED,
    },
    TaskState.COMPLETED: set(),  # Terminal
    TaskState.FAILED: set(),  # Terminal
    TaskState.MANUAL_REVIEW_REQUIRED: set(),  # Terminal/Halted until manual intervention
}

CURRENT_SCHEMA_VERSION = "1.0.0"


@dataclass
class ActiveTaskData:
    schema_version: str
    task_id: str
    phase: int
    action: str
    send_to: str
    command_sha256: str
    session_id: str
    operation_id: str
    idempotency_key: str
    current_state: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    submission_confirmed: bool = False
    return_confirmed: bool = False
    retry_count: int = 0
    last_reason_code: Optional[str] = None
    last_error: Optional[str] = None

    def get_state_enum(self) -> TaskState:
        return TaskState(self.current_state)

    def transition_to(
        self,
        new_state: TaskState,
        reason_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        curr = self.get_state_enum()
        allowed = LEGAL_TRANSITIONS.get(curr, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(
                f"Illegal transition from '{curr.value}' to '{new_state.value}'."
            )

        self.current_state = new_state.value
        self.updated_at = time.time()
        if reason_code:
            self.last_reason_code = reason_code
        if error_message is not None:
            self.last_error = error_message

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActiveTaskData":
        schema_v = data.get("schema_version")
        if schema_v != CURRENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unknown or incompatible schema version: '{schema_v}'. Expected '{CURRENT_SCHEMA_VERSION}'."
            )
        return cls(**data)
