import unittest
import tempfile
import json
from pathlib import Path

from src.state.task_state import (
    TaskState,
    ActiveTaskData,
    InvalidStateTransitionError,
    CURRENT_SCHEMA_VERSION,
)
from src.state.state_repository import (
    StateRepository,
    CorruptStateError,
    SchemaMismatchError,
    StateError,
)
from src.state.recovery import StateRecoveryManager


class TestPhase4DurableState(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tmpdir.name)
        self.repo = StateRepository(state_dir=self.state_dir)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_valid_and_invalid_state_transitions(self):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-001",
            phase=1,
            action="RUN",
            send_to="GEMINI",
            command_sha256="abc123sha",
            session_id="sess-001",
            operation_id="op-001",
            idempotency_key="idem-001",
            current_state=TaskState.COMMAND_DETECTED.value,
        )

        # Valid transition
        task.transition_to(TaskState.COMMAND_VALIDATED)
        self.assertEqual(task.get_state_enum(), TaskState.COMMAND_VALIDATED)

        # Invalid transition (COMMAND_VALIDATED directly to COMPLETED is illegal)
        with self.assertRaises(InvalidStateTransitionError):
            task.transition_to(TaskState.COMPLETED)

    def test_persistence_atomic_write_and_restart(self):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-002",
            phase=2,
            action="EXECUTE",
            send_to="GEMINI",
            command_sha256="sha256hash",
            session_id="sess-002",
            operation_id="op-002",
            idempotency_key="idem-002",
            current_state=TaskState.COMMAND_ACCEPTED.value,
        )

        self.repo.save_active_task(task)

        # Re-instantiate repository (simulate process restart)
        repo2 = StateRepository(state_dir=self.state_dir)
        loaded_task = repo2.load_active_task()

        self.assertIsNotNone(loaded_task)
        self.assertEqual(loaded_task.task_id, "TASK-002")
        self.assertEqual(loaded_task.command_sha256, "sha256hash")
        self.assertEqual(loaded_task.get_state_enum(), TaskState.COMMAND_ACCEPTED)

    def test_corrupt_state_fails_closed(self):
        active_file = self.state_dir / "active_task.json"

        # 1. Invalid JSON
        with open(active_file, "w", encoding="utf-8") as f:
            f.write("{ invalid json content ...")

        with self.assertRaises(CorruptStateError):
            self.repo.load_active_task()

        # 2. Empty file
        with open(active_file, "w", encoding="utf-8") as f:
            f.write("")

        with self.assertRaises(CorruptStateError):
            self.repo.load_active_task()

    def test_schema_mismatch_fails_closed(self):
        active_file = self.state_dir / "active_task.json"

        invalid_schema_data = {
            "schema_version": "99.0.0",
            "task_id": "TASK-OLD",
            "phase": 1,
            "action": "RUN",
            "send_to": "GEMINI",
            "command_sha256": "sha",
            "session_id": "s",
            "operation_id": "o",
            "idempotency_key": "i",
            "current_state": TaskState.COMMAND_DETECTED.value,
        }

        with open(active_file, "w", encoding="utf-8") as f:
            json.dump(invalid_schema_data, f)

        with self.assertRaises(SchemaMismatchError):
            self.repo.load_active_task()

    def test_completed_ledger_archival(self):
        task = ActiveTaskData(
            schema_version=CURRENT_SCHEMA_VERSION,
            task_id="TASK-003",
            phase=1,
            action="RUN",
            send_to="GEMINI",
            command_sha256="sha256hash",
            session_id="sess-003",
            operation_id="op-003",
            idempotency_key="idem-003",
            current_state=TaskState.RETURNED_TO_SUPERVISOR.value,
        )

        task.transition_to(TaskState.COMPLETED)
        self.repo.save_active_task(task)

        # Archive to ledger
        self.repo.archive_to_ledger(task)

        # Active task should now be cleared
        self.assertIsNone(self.repo.load_active_task())

        # Ledger should contain archived task
        ledger = self.repo.load_completed_ledger()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["task_id"], "TASK-003")

    def test_state_recovery_inspection(self):
        recovery = StateRecoveryManager(self.repo)
        status_before = recovery.inspect_state_status()
        self.assertFalse(status_before["has_active_task"])
        self.assertFalse(status_before["corrupted"])

        # Corrupt the file
        active_file = self.state_dir / "active_task.json"
        with open(active_file, "w", encoding="utf-8") as f:
            f.write("corrupted data")

        status_corrupt = recovery.inspect_state_status()
        self.assertTrue(status_corrupt["corrupted"])
        self.assertIsNotNone(status_corrupt["error"])


if __name__ == "__main__":
    unittest.main()
