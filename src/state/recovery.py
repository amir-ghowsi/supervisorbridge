from enum import Enum
from typing import Dict, Any, Optional, List
from src.state.state_repository import (
    StateRepository,
    CorruptStateError,
    SchemaMismatchError,
)
from src.state.task_state import ActiveTaskData, TaskState
from src.safety.idempotency import IdempotencyManager
from src.safety.checkpoint import CheckpointManager
from src.utils.logger import get_logger


class ReconciliationOutcome(Enum):
    PROVEN_EXECUTED = "PROVEN_EXECUTED"
    PROVEN_NOT_EXECUTED = "PROVEN_NOT_EXECUTED"
    PROVEN_COMPLETED = "PROVEN_COMPLETED"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ReconciliationEngine:
    """
    Read-first manual reconciliation component.
    Independently inspects durable state, idempotency records, checkpoints,
    and browser evidence to prove whether an ambiguous mutation occurred.
    """

    def __init__(self, repo: StateRepository):
        self.repo = repo
        self.idempotency = IdempotencyManager(state_dir=repo.state_dir)
        self.checkpoint = CheckpointManager(state_dir=repo.state_dir)
        self.logger = get_logger()

    def reconcile_submission_prepared(
        self,
        task: ActiveTaskData,
        ai_studio_turns: Optional[List[str]] = None,
        expected_raw_command: Optional[str] = None,
    ) -> ReconciliationOutcome:
        """
        Reconciles SUBMISSION_PREPARED state.
        Checks if expected Supervisor command was submitted to AI Studio.
        """
        try:
            ledger = self.repo.load_completed_ledger()
            if any(t.get("task_id") == task.task_id and t.get("phase") == task.phase for t in ledger):
                return ReconciliationOutcome.PROVEN_COMPLETED
        except Exception:
            return ReconciliationOutcome.CONFLICTING_EVIDENCE

        # Check positive evidence in turns
        if ai_studio_turns:
            for turn in ai_studio_turns:
                if (expected_raw_command and expected_raw_command in turn) or task.task_id in turn:
                    return ReconciliationOutcome.PROVEN_EXECUTED

        rec = self.idempotency.get_record(task.idempotency_key)
        if rec and rec.state == "CONFIRMED":
            return ReconciliationOutcome.PROVEN_EXECUTED

        # If no idempotency record / checkpoint AND zero turns exist in conversation -> PROVEN_NOT_EXECUTED
        if (not rec or rec.state != "CONFIRMED") and (ai_studio_turns is not None and len(ai_studio_turns) == 0):
            return ReconciliationOutcome.PROVEN_NOT_EXECUTED

        # Absence alone in non-empty conversation does not prove non-execution -> Fail closed with INSUFFICIENT_EVIDENCE
        return ReconciliationOutcome.INSUFFICIENT_EVIDENCE

    def reconcile_return_prepared(
        self,
        task: ActiveTaskData,
        chatgpt_user_messages: Optional[List[str]] = None,
        expected_report_block: Optional[str] = None,
    ) -> ReconciliationOutcome:
        """
        Reconciles RETURN_PREPARED state or ambiguous return click.
        Checks if Implementer Report was already posted to ChatGPT in a USER-role message.
        """
        try:
            ledger = self.repo.load_completed_ledger()
            if any(t.get("task_id") == task.task_id and t.get("phase") == task.phase for t in ledger):
                return ReconciliationOutcome.PROVEN_COMPLETED
        except Exception:
            return ReconciliationOutcome.CONFLICTING_EVIDENCE

        if chatgpt_user_messages:
            for msg in chatgpt_user_messages:
                if (expected_report_block and expected_report_block in msg) or (task.task_id in msg and "[IMPLEMENTER_REPORT]" in msg):
                    return ReconciliationOutcome.PROVEN_EXECUTED

        rec = self.idempotency.get_record(f"idem_return_{task.task_id}_{task.phase}_{task.command_sha256}")
        if rec and rec.state == "CONFIRMED":
            return ReconciliationOutcome.PROVEN_EXECUTED

        if (not rec or rec.state != "CONFIRMED") and (chatgpt_user_messages is not None and len(chatgpt_user_messages) == 0):
            return ReconciliationOutcome.PROVEN_NOT_EXECUTED

        return ReconciliationOutcome.INSUFFICIENT_EVIDENCE


class StateRecoveryManager:
    """Utility for evaluating and inspecting state recovery status."""

    def __init__(self, repo: StateRepository):
        self.repo = repo

    def inspect_state_status(self) -> Dict[str, Any]:
        """
        Inspects active state without mutating disk.
        Returns detailed recovery report.
        """
        res: Dict[str, Any] = {
            "has_active_task": False,
            "corrupted": False,
            "error": None,
            "task_data": None,
        }

        try:
            task = self.repo.load_active_task()
            if task:
                res["has_active_task"] = True
                res["task_data"] = task.to_dict()
        except CorruptStateError as e:
            res["corrupted"] = True
            res["error"] = f"Corrupt state: {e}"
        except SchemaMismatchError as e:
            res["corrupted"] = True
            res["error"] = f"Schema mismatch: {e}"
        except Exception as e:
            res["corrupted"] = True
            res["error"] = f"Unexpected state error: {e}"

        return res
