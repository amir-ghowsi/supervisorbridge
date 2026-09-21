from typing import Dict, Any, Optional
from pathlib import Path
from src.state.state_repository import StateRepository, CorruptStateError, SchemaMismatchError
from src.safety.idempotency import IdempotencyManager
from src.safety.checkpoint import CheckpointManager
from src.safety.emergency_stop import EmergencyStopManager


class StateInspector:
    """Read-only diagnostic inspector for SupervisorBridge local persistent state."""

    def __init__(self, state_dir: Optional[str | Path] = None):
        self.state_dir = state_dir
        self.repo = StateRepository(state_dir=state_dir)
        self.idempotency = IdempotencyManager(state_dir=state_dir)
        self.checkpoint = CheckpointManager(state_dir=state_dir)
        self.estop = EmergencyStopManager(state_dir=state_dir)

    def inspect_full_state(self) -> Dict[str, Any]:
        """Inspects all durable state files and returns diagnostic summary without mutating disk."""
        res: Dict[str, Any] = {
            "has_active_task": False,
            "corrupted": False,
            "active_task": None,
            "emergency_stop": self.estop.get_status(),
            "checkpoint": None,
            "idempotency_records_count": 0,
            "completed_ledger_count": 0,
            "orphaned_state_detected": False,
            "reconciliation_required": False,
            "error": None,
        }

        try:
            task = self.repo.load_active_task()
            if task:
                res["has_active_task"] = True
                res["active_task"] = task.to_dict()
                if task.get_state_enum().value == "MANUAL_REVIEW_REQUIRED":
                    res["reconciliation_required"] = True
        except (CorruptStateError, SchemaMismatchError) as e:
            res["corrupted"] = True
            res["error"] = str(e)
            res["reconciliation_required"] = True

        try:
            res["checkpoint"] = self.checkpoint.load_checkpoint()
        except Exception as e:
            res["error"] = f"Checkpoint error: {e}"

        try:
            records = self.idempotency.load_records()
            res["idempotency_records_count"] = len(records)

            if not res["has_active_task"] and records:
                ledger = self.repo.load_completed_ledger()
                ledger_ids = {t["task_id"] for t in ledger}
                unarchived_orphans = [
                    r for r in records.values()
                    if r.get("task_id") not in ledger_ids and r.get("state") in ("PREPARED", "CONFIRMED")
                ]
                if unarchived_orphans:
                    res["orphaned_state_detected"] = True
                    res["reconciliation_required"] = True
        except Exception as e:
            res["error"] = f"Idempotency error: {e}"

        try:
            ledger = self.repo.load_completed_ledger()
            res["completed_ledger_count"] = len(ledger)
        except Exception as e:
            res["error"] = f"Ledger error: {e}"

        return res
