import unittest
import tempfile
from pathlib import Path

from src.bridge.operation_contract import OperationContract
from src.safety.emergency_stop import EmergencyStopManager, EmergencyStopEngagedError
from src.safety.idempotency import IdempotencyManager, IdempotencyViolationError
from src.safety.checkpoint import CheckpointManager, CheckpointError
from src.safety.loop_guard import LoopGuard, LoopGuardViolationError
from src.safety.retry import RetryBudgetManager, RetryBudgetExhaustedError
from src.safety.timeout import TimeoutController, OperationTimeoutError
from src.safety.orchestrator import SafetyOrchestrator


class TestPhase5SafetyCore(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_emergency_stop_persists_and_blocks_mutation(self):
        estop = EmergencyStopManager(state_dir=self.state_dir)
        self.assertFalse(estop.is_engaged())

        estop.engage("Test emergency stop")
        self.assertTrue(estop.is_engaged())

        # Verify restart persistence
        estop2 = EmergencyStopManager(state_dir=self.state_dir)
        self.assertTrue(estop2.is_engaged())

        with self.assertRaises(EmergencyStopEngagedError):
            estop2.assert_not_engaged()

        estop2.release("Test release")
        self.assertFalse(estop2.is_engaged())

    def test_idempotency_records_full_metadata_and_prevents_duplicates(self):
        idem = IdempotencyManager(state_dir=self.state_dir)

        rec = idem.record_operation(
            session_id="sess-1",
            task_id="TASK-100",
            operation_id="op-100",
            idempotency_key="idem-key-100",
            command_sha256="sha256-hash-val",
            operation_type="SUBMISSION",
            state="PREPARED",
        )

        self.assertEqual(rec.schema_version, "1.0.0")
        self.assertEqual(rec.task_id, "TASK-100")
        self.assertEqual(rec.command_sha256, "sha256-hash-val")

        # Mark as CONFIRMED
        idem.record_operation(
            session_id="sess-1",
            task_id="TASK-100",
            operation_id="op-100",
            idempotency_key="idem-key-100",
            command_sha256="sha256-hash-val",
            operation_type="SUBMISSION",
            state="CONFIRMED",
        )

        # Re-attempting same idempotency key when CONFIRMED must fail closed
        with self.assertRaises(IdempotencyViolationError):
            idem.record_operation(
                session_id="sess-1",
                task_id="TASK-100",
                operation_id="op-100",
                idempotency_key="idem-key-100",
                command_sha256="sha256-hash-val",
                operation_type="SUBMISSION",
                state="PREPARED",
            )

        # Mismatched metadata with same idempotency key must fail closed
        with self.assertRaises(IdempotencyViolationError):
            idem.record_operation(
                session_id="sess-1",
                task_id="DIFFERENT-TASK",
                operation_id="op-100",
                idempotency_key="idem-key-100",
                command_sha256="sha256-hash-val",
                operation_type="SUBMISSION",
            )

    def test_checkpoint_explicit_creation_and_corruption_handling(self):
        ckpt = CheckpointManager(state_dir=self.state_dir)
        self.assertIsNone(ckpt.load_checkpoint())

        ckpt.create_checkpoint(
            task_id="TASK-1",
            operation_id="op-1",
            state_data={"state": "SUBMISSION_PREPARED"},
            idempotency_key="idem-1",
        )

        loaded = ckpt.load_checkpoint()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["task_id"], "TASK-1")

        # Corrupt checkpoint file
        ckpt_file = self.state_dir / "checkpoint.json"
        with open(ckpt_file, "w", encoding="utf-8") as f:
            f.write("corrupted JSON")

        with self.assertRaises(CheckpointError):
            ckpt.load_checkpoint()

    def test_loop_guard_limits(self):
        guard = LoopGuard(max_iterations=3)
        guard.record_cycle("op-1")
        guard.record_cycle("op-2")
        guard.record_cycle("op-3")

        with self.assertRaises(LoopGuardViolationError):
            guard.record_cycle("op-4")

        # Repeated operation check
        guard_repeat = LoopGuard(max_iterations=10)
        guard_repeat.record_cycle("identical-op")
        guard_repeat.record_cycle("identical-op")
        with self.assertRaises(LoopGuardViolationError):
            guard_repeat.record_cycle("identical-op")

    def test_retry_budget_manager(self):
        retry_mgr = RetryBudgetManager(max_retries=2)
        c1 = retry_mgr.check_and_increment(0)
        self.assertEqual(c1, 1)
        c2 = retry_mgr.check_and_increment(1)
        self.assertEqual(c2, 2)

        with self.assertRaises(RetryBudgetExhaustedError):
            retry_mgr.check_and_increment(2)

    def test_safety_orchestrator_integration(self):
        orchestrator = SafetyOrchestrator(state_dir=self.state_dir)

        contract = OperationContract(
            session_id="sess-01",
            task_id="TASK-200",
            phase=1,
            operation_id="op-200",
            idempotency_key="idem-200",
            command_sha256="commandhash",
            operation_type="GEMINI_SUBMISSION",
            payload="Test command payload",
        )

        task_data = {"task_id": "TASK-200", "state": "COMMAND_ACCEPTED"}

        # Prepare operation
        orchestrator.prepare_operation(contract, task_data)

        # Confirm operation
        orchestrator.confirm_operation(contract, task_data)

        # Re-attempting prepare with same contract must fail closed
        with self.assertRaises(IdempotencyViolationError):
            orchestrator.prepare_operation(contract, task_data)


if __name__ == "__main__":
    unittest.main()
