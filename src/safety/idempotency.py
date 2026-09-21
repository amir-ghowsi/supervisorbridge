import os
import json
import time
import tempfile
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
from src.utils.paths import ensure_dir
from src.utils.logger import get_logger

CURRENT_IDEMPOTENCY_SCHEMA = "1.0.0"


class IdempotencyViolationError(Exception):
    """Raised when an operation idempotency check fails or duplicate execution is attempted."""

    pass


@dataclass
class IdempotencyRecord:
    schema_version: str
    session_id: str
    task_id: str
    operation_id: str
    idempotency_key: str
    command_sha256: str
    operation_type: str  # e.g., "GEMINI_SUBMISSION", "SUPERVISOR_RETURN", "GEMINI_RETRY"
    state: str  # "PREPARED", "EXECUTED", "CONFIRMED", "FAILED"
    created_at: float
    updated_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdempotencyRecord":
        schema_v = data.get("schema_version")
        if schema_v != CURRENT_IDEMPOTENCY_SCHEMA:
            raise ValueError(
                f"Incompatible idempotency record schema: '{schema_v}'. Expected '{CURRENT_IDEMPOTENCY_SCHEMA}'."
            )
        return cls(**data)


class IdempotencyManager:
    """Manages idempotent operation tracking and duplicate execution prevention."""

    def __init__(self, state_dir: Optional[str | Path] = None):
        self.logger = get_logger()
        if state_dir:
            self.state_dir = ensure_dir(state_dir)
        else:
            self.state_dir = ensure_dir("data/state")

        self.records_file = self.state_dir / "idempotency_records.json"

    def _atomic_write(self, data: Dict[str, Any]) -> None:
        fd, tmp_str = tempfile.mkstemp(
            dir=self.state_dir, prefix=".idempotency_tmp_", suffix=".tmp"
        )
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.records_file)
        except Exception as e:
            if tmp_path.exists():
                tmp_path.unlink()
            raise RuntimeError(f"Failed to save idempotency records: {e}") from e

    def load_records(self) -> Dict[str, Dict[str, Any]]:
        if not self.records_file.exists():
            return {}
        try:
            with open(self.records_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return {}
                return json.loads(content)
        except Exception as e:
            self.logger.error(f"Error loading idempotency records: {e}")
            raise IdempotencyViolationError(
                f"Corrupt idempotency records file: {e}"
            ) from e

    def get_record(self, idempotency_key: str) -> Optional[IdempotencyRecord]:
        records = self.load_records()
        raw = records.get(idempotency_key)
        if raw:
            return IdempotencyRecord.from_dict(raw)
        return None

    def get_unfinished_prepared_record(
        self, task_id: str, operation_type: str = "GEMINI_RETRY"
    ) -> Optional[IdempotencyRecord]:
        """
        Searches durable idempotency records for any unfinished PREPARED operation for a given task_id and operation_type.
        If multiple conflicting PREPARED records exist, raises IdempotencyViolationError (fails closed).
        """
        records = self.load_records()
        matches = []
        for raw in records.values():
            rec = IdempotencyRecord.from_dict(raw)
            if rec.task_id == task_id and rec.operation_type == operation_type and rec.state == "PREPARED":
                matches.append(rec)

        if len(matches) > 1:
            raise IdempotencyViolationError(
                f"Multiple conflicting PREPARED '{operation_type}' records found for task '{task_id}'! Failing closed."
            )

        if matches:
            return matches[0]
        return None

    def record_operation(
        self,
        session_id: str,
        task_id: str,
        operation_id: str,
        idempotency_key: str,
        command_sha256: str,
        operation_type: str,
        state: str = "PREPARED",
    ) -> IdempotencyRecord:
        """
        Creates or updates an idempotency record for a mutation operation.
        Strictly validates all 7 identity fields: schema_version, session_id, task_id,
        operation_id, idempotency_key, command_sha256, operation_type.
        Fails closed on any mismatch or when re-attempting CONFIRMED / ambiguous PREPARED state without explicit transition.
        """
        records = self.load_records()
        existing_raw = records.get(idempotency_key)

        now = time.time()
        if existing_raw:
            rec = IdempotencyRecord.from_dict(existing_raw)

            # Strict 7-field identity verification
            if rec.session_id != session_id:
                raise IdempotencyViolationError(
                    f"Idempotency identity mismatch (session_id): '{rec.session_id}' vs '{session_id}'."
                )
            if rec.task_id != task_id:
                raise IdempotencyViolationError(
                    f"Idempotency identity mismatch (task_id): '{rec.task_id}' vs '{task_id}'."
                )
            if rec.operation_id != operation_id:
                raise IdempotencyViolationError(
                    f"Idempotency identity mismatch (operation_id): '{rec.operation_id}' vs '{operation_id}'."
                )
            if rec.command_sha256 != command_sha256:
                raise IdempotencyViolationError(
                    f"Idempotency identity mismatch (command_sha256): '{rec.command_sha256}' vs '{command_sha256}'."
                )
            if rec.operation_type != operation_type:
                raise IdempotencyViolationError(
                    f"Idempotency identity mismatch (operation_type): '{rec.operation_type}' vs '{operation_type}'."
                )

            if rec.state == "CONFIRMED":
                raise IdempotencyViolationError(
                    f"Operation with idempotency key '{idempotency_key}' was already CONFIRMED!"
                )

            if rec.state == "PREPARED" and state == "PREPARED":
                raise IdempotencyViolationError(
                    f"Ambiguous execution: Operation '{idempotency_key}' is already in PREPARED state on disk."
                )

            rec.state = state
            rec.updated_at = now
            record_obj = rec
        else:
            record_obj = IdempotencyRecord(
                schema_version=CURRENT_IDEMPOTENCY_SCHEMA,
                session_id=session_id,
                task_id=task_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_sha256=command_sha256,
                operation_type=operation_type,
                state=state,
                created_at=now,
                updated_at=now,
            )

        records[idempotency_key] = record_obj.to_dict()
        self._atomic_write(records)
        return record_obj
