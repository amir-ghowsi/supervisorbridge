from typing import Optional, Dict, Any
from pathlib import Path
from src.bridge.operation_contract import OperationContract
from src.safety.emergency_stop import EmergencyStopManager, EmergencyStopEngagedError
from src.safety.idempotency import IdempotencyManager, IdempotencyViolationError
from src.safety.checkpoint import CheckpointManager
from src.safety.loop_guard import LoopGuard
from src.safety.retry import RetryBudgetManager
from src.safety.timeout import TimeoutController
from src.utils.logger import get_logger


class SafetyOrchestrator:
    """
    Unified Safety Orchestrator ensuring zero unauthorized or duplicate browser side effects.
    Must be invoked before any external DOM write.
    """

    def __init__(self, state_dir: Optional[str | Path] = None):
        self.logger = get_logger()
        self.estop = EmergencyStopManager(state_dir=state_dir)
        self.idempotency = IdempotencyManager(state_dir=state_dir)
        self.checkpoint = CheckpointManager(state_dir=state_dir)
        self.loop_guard = LoopGuard()
        self.retry_budget = RetryBudgetManager()

    def prepare_operation(
        self, contract: OperationContract, current_task_dict: Dict[str, Any]
    ) -> None:
        """
        Executes all mandatory pre-mutation safety checks in order:
        1. Emergency Stop assertion
        2. Idempotency record verification & registration
        3. Explicit pre-mutation state checkpoint creation
        4. Loop guard cycle check
        """
        # 1. Emergency stop assertion
        self.estop.assert_not_engaged()

        # 2. Idempotency registration
        self.idempotency.record_operation(
            session_id=contract.session_id,
            task_id=contract.task_id,
            operation_id=contract.operation_id,
            idempotency_key=contract.idempotency_key,
            command_sha256=contract.command_sha256,
            operation_type=contract.operation_type,
            state="PREPARED",
        )

        # 3. Create explicit checkpoint
        self.checkpoint.create_checkpoint(
            task_id=contract.task_id,
            operation_id=contract.operation_id,
            state_data=current_task_dict,
            idempotency_key=contract.idempotency_key,
        )

        # 4. Loop guard cycle
        self.loop_guard.record_cycle(operation_signature=contract.idempotency_key)

    def confirm_operation(
        self, contract: OperationContract, updated_task_dict: Dict[str, Any]
    ) -> None:
        """
        Confirms operation success post-mutation.
        Updates idempotency record to CONFIRMED.
        """
        self.idempotency.record_operation(
            session_id=contract.session_id,
            task_id=contract.task_id,
            operation_id=contract.operation_id,
            idempotency_key=contract.idempotency_key,
            command_sha256=contract.command_sha256,
            operation_type=contract.operation_type,
            state="CONFIRMED",
        )
