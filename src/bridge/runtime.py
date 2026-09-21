import asyncio
import time
from typing import Dict, Any, Tuple, Optional
from src.browser.chrome_manager import ChromeManager
from src.browser.tab_manager import TabManager
from src.adapters.chatgpt_adapter import ChatGPTAdapter
from src.adapters.ai_studio_adapter import AIStudioAdapter
from src.protocol.message_parser import MessageParser
from src.protocol.supervisor_protocol import SupervisorCommand
from src.bridge.operation_contract import OperationContract
from src.state.task_state import TaskState, ActiveTaskData, CURRENT_SCHEMA_VERSION
from src.state.state_repository import StateRepository, CorruptStateError, SchemaMismatchError
from src.state.recovery import ReconciliationEngine, ReconciliationOutcome
from src.safety.orchestrator import SafetyOrchestrator
from src.bridge.submission import GeminiSubmitter
from src.bridge.generation_monitor import GenerationMonitor, GenerationState, GeminiRetryManager
from src.bridge.response_inspector import ResponseInspector
from src.bridge.return_to_supervisor import SupervisorReturner
from src.utils.logger import get_logger
from src.utils.config_loader import get_config
from src.utils.hashing import compute_sha256


class RuntimeEngine:
    """
    Single authoritative decision engine for SupervisorBridge.
    Enforces maximum 1 external browser side effect per cycle.
    Returns consistent machine-readable structured result schema.
    """

    def __init__(
        self,
        repo: Optional[StateRepository] = None,
        safety: Optional[SafetyOrchestrator] = None,
    ):
        self.logger = get_logger()
        self.repo = repo or StateRepository()
        self.safety = safety or SafetyOrchestrator(state_dir=self.repo.state_dir)
        self.msg_parser = MessageParser()
        self.submitter = GeminiSubmitter(self.repo, self.safety)
        self.monitor = GenerationMonitor()
        self.retry_mgr = GeminiRetryManager()
        self.inspector = ResponseInspector()
        self.returner = SupervisorReturner(self.repo, self.safety)
        self.reconciler = ReconciliationEngine(self.repo)

    def _build_structured_result(
        self,
        status: str = "SUCCESS",
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        phase: Optional[int] = None,
        command_sha256: Optional[str] = None,
        operation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        cycle_state: str = "IDLE",
        action_taken: str = "NONE",
        side_effect_count: int = 0,
        state_mutations: int = 0,
        reason_code: str = "NO_ACTION",
        diagnostic: str = "",
        manual_review_required: bool = False,
        wait_required: bool = False,
        cycle_terminal: bool = False,
        orphaned_state: bool = False,
        reconciliation_required: bool = False,
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "SESSION_ID": session_id,
            "TASK_ID": task_id,
            "PHASE": phase,
            "COMMAND_SHA256": command_sha256,
            "OPERATION_ID": operation_id,
            "IDEMPOTENCY_KEY": idempotency_key,
            "CYCLE_STATE": cycle_state,
            "ACTION_TAKEN": action_taken,
            "SIDE_EFFECT_COUNT": side_effect_count,
            "STATE_MUTATIONS": state_mutations,
            "REASON_CODE": reason_code,
            "DIAGNOSTIC": diagnostic,
            "MANUAL_REVIEW_REQUIRED": manual_review_required,
            "WAIT_REQUIRED": wait_required,
            "CYCLE_TERMINAL": cycle_terminal,
            "ORPHANED_STATE": orphaned_state,
            "RECONCILIATION_REQUIRED": reconciliation_required,
            # Backward-compatible lowercase aliases
            "session_id": session_id,
            "task_id": task_id,
            "phase": phase,
            "command_sha256": command_sha256,
            "operation_id": operation_id,
            "idempotency_key": idempotency_key,
            "action_taken": action_taken,
            "side_effect_count": side_effect_count,
            "state_mutations": state_mutations,
            "reason_code": reason_code,
            "diagnostic": diagnostic,
        }

    async def run_once(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Executes a single cycle of the runtime engine.
        Chooses at most ONE external browser side effect.
        """
        # Priority 1: Check Emergency Stop
        if self.safety.estop.is_engaged():
            return self._build_structured_result(
                status="HALTED",
                cycle_state="HALTED_ESTOP",
                action_taken="NONE",
                reason_code="EMERGENCY_STOP_ENGAGED",
                diagnostic="Emergency stop is active on disk.",
                manual_review_required=True,
                cycle_terminal=True,
            )

        # Priority 2: Controlled durable state loading (Fail Closed)
        task: Optional[ActiveTaskData] = None
        try:
            task = self.repo.load_active_task()
        except (CorruptStateError, SchemaMismatchError) as e:
            return self._build_structured_result(
                status="HALTED",
                cycle_state="CORRUPT_STATE",
                action_taken="NONE",
                reason_code="CORRUPT_ACTIVE_TASK_STATE",
                diagnostic=f"Active task state corrupted: {e}",
                manual_review_required=True,
                reconciliation_required=True,
                cycle_terminal=True,
            )
        except Exception as e:
            return self._build_structured_result(
                status="HALTED",
                cycle_state="STATE_READ_ERROR",
                action_taken="NONE",
                reason_code="STATE_READ_EXCEPTION",
                diagnostic=f"Failed to read durable state: {e}",
                manual_review_required=True,
                reconciliation_required=True,
                cycle_terminal=True,
            )

        # Check orphan evidence if no active task exists
        if not task:
            try:
                idempotency_records = self.safety.idempotency.load_records()
                checkpoint_data = self.safety.checkpoint.load_checkpoint()
                ledger = self.repo.load_completed_ledger()
                ledger_ids = {t["task_id"] for t in ledger}

                if idempotency_records or checkpoint_data:
                    unarchived = [
                        rec for rec in idempotency_records.values()
                        if rec.get("task_id") not in ledger_ids and rec.get("state") in ("PREPARED", "CONFIRMED")
                    ]
                    if unarchived or checkpoint_data:
                        orphan_id = unarchived[0].get("task_id") if unarchived else "UNKNOWN"
                        return self._build_structured_result(
                            status="ORPHANED_STATE",
                            task_id=orphan_id,
                            cycle_state="ORPHAN_DETECTED",
                            action_taken="NONE",
                            reason_code="ORPHANED_STATE_DETECTED",
                            diagnostic="Idempotency/checkpoint evidence exists for unarchived task without active_task file.",
                            manual_review_required=True,
                            orphaned_state=True,
                            reconciliation_required=True,
                            cycle_terminal=True,
                        )
            except CorruptStateError as e:
                return self._build_structured_result(
                    status="HALTED",
                    cycle_state="CORRUPT_LEDGER_OR_IDEMPOTENCY",
                    action_taken="NONE",
                    reason_code="CORRUPT_RECOVERY_EVIDENCE",
                    diagnostic=f"Corrupt recovery evidence on disk: {e}",
                    manual_review_required=True,
                    reconciliation_required=True,
                    cycle_terminal=True,
                )

        # Handle Terminal & Completed tasks
        if task:
            if task.get_state_enum() in (TaskState.COMPLETED, TaskState.RETURNED_TO_SUPERVISOR):
                if not dry_run:
                    self.repo.archive_to_ledger(task)
                return self._build_structured_result(
                    status="SUCCESS",
                    session_id=task.session_id,
                    task_id=task.task_id,
                    phase=task.phase,
                    command_sha256=task.command_sha256,
                    cycle_state="TASK_COMPLETED",
                    action_taken="ARCHIVE_AND_CLEAR",
                    side_effect_count=0,
                    state_mutations=1 if not dry_run else 0,
                    reason_code="TASK_TERMINALIZED",
                    diagnostic="Task completed and archived.",
                    cycle_terminal=True,
                )

            if task.get_state_enum() == TaskState.FAILED:
                return self._build_structured_result(
                    status="HALTED",
                    session_id=task.session_id,
                    task_id=task.task_id,
                    phase=task.phase,
                    command_sha256=task.command_sha256,
                    cycle_state="TASK_FAILED",
                    action_taken="NONE",
                    reason_code="TASK_STATE_FAILED",
                    diagnostic=f"Task is in FAILED state. Error: {task.last_error}",
                    manual_review_required=True,
                    cycle_terminal=True,
                )

            if task.get_state_enum() == TaskState.MANUAL_REVIEW_REQUIRED:
                return self._build_structured_result(
                    status="HALTED",
                    session_id=task.session_id,
                    task_id=task.task_id,
                    phase=task.phase,
                    command_sha256=task.command_sha256,
                    cycle_state="MANUAL_REVIEW_REQUIRED",
                    action_taken="NONE",
                    reason_code="MANUAL_REVIEW_REQUIRED",
                    diagnostic=f"Task is in MANUAL_REVIEW_REQUIRED. Last error: {task.last_error}",
                    manual_review_required=True,
                    reconciliation_required=True,
                    cycle_terminal=True,
                )

            # Raw Command Integrity Assertion: Verify SHA256 matches
            if task.raw_canonical_block:
                computed_hash = compute_sha256(task.raw_canonical_block)
                if computed_hash != task.command_sha256:
                    task.transition_to(
                        TaskState.MANUAL_REVIEW_REQUIRED,
                        reason_code="COMMAND_HASH_MISMATCH",
                        error_message=f"Persisted raw_canonical_block SHA256 mismatch! '{computed_hash}' vs '{task.command_sha256}'",
                    )
                    if not dry_run:
                        self.repo.save_active_task(task)
                    return self._build_structured_result(
                        status="HALTED",
                        session_id=task.session_id,
                        task_id=task.task_id,
                        phase=task.phase,
                        command_sha256=task.command_sha256,
                        cycle_state="COMMAND_HASH_MISMATCH",
                        action_taken="NONE",
                        reason_code="COMMAND_HASH_MISMATCH",
                        diagnostic="Persisted raw command block SHA256 does not match command_sha256 header.",
                        manual_review_required=True,
                        reconciliation_required=True,
                        cycle_terminal=True,
                    )

        chrome = ChromeManager()
        try:
            browser = await chrome.connect()
            tab_mgr = TabManager(browser)

            # Case: Fresh task (No active task present)
            if not task:
                chatgpt_page = await tab_mgr.locate_chatgpt_tab()
                adapter = ChatGPTAdapter(chatgpt_page)
                raw_msgs = await adapter.extract_raw_assistant_messages()
                parsed_msgs = [{"role": "assistant", "text": m} for m in raw_msgs]
                cmd = self.msg_parser.parse_chatgpt_messages(parsed_msgs)

                if not cmd:
                    await chrome.disconnect()
                    return self._build_structured_result(
                        status="SUCCESS",
                        cycle_state="IDLE_NO_COMMAND",
                        action_taken="NONE",
                        reason_code="NO_SUPERVISOR_COMMAND_DETECTED",
                        diagnostic="No active task and no fresh Supervisor command in ChatGPT.",
                        wait_required=True,
                    )

                session_id = f"sess_{cmd.task_id}"
                operation_id = f"op_submit_{cmd.task_id}_{cmd.phase}"
                idempotency_key = f"idem_submit_{cmd.command_sha256}"

                new_task = ActiveTaskData(
                    schema_version=CURRENT_SCHEMA_VERSION,
                    task_id=cmd.task_id,
                    phase=cmd.phase,
                    action=cmd.action,
                    send_to=cmd.send_to,
                    command_sha256=cmd.command_sha256,
                    session_id=session_id,
                    operation_id=operation_id,
                    idempotency_key=idempotency_key,
                    current_state=TaskState.COMMAND_DETECTED.value,
                    raw_canonical_block=cmd.raw_canonical_block,  # Persist exact raw canonical command!
                )

                if not dry_run:
                    new_task.transition_to(TaskState.COMMAND_VALIDATED)
                    new_task.transition_to(TaskState.COMMAND_ACCEPTED)
                    new_task.transition_to(TaskState.SUBMISSION_PREPARED)
                    self.repo.save_active_task(new_task)

                task = new_task

                aistudio_page = await tab_mgr.locate_ai_studio_tab()
                success, effects, details = await self.submitter.submit_command(
                    command=cmd,
                    ai_studio_page=aistudio_page,
                    task=task,
                    session_id=session_id,
                    dry_run=dry_run,
                )

                await chrome.disconnect()
                return self._build_structured_result(
                    status="SUCCESS" if success else "ERROR",
                    session_id=task.session_id,
                    task_id=task.task_id,
                    phase=task.phase,
                    command_sha256=task.command_sha256,
                    operation_id=operation_id,
                    idempotency_key=idempotency_key,
                    cycle_state="FRESH_SUBMISSION_COMPLETED",
                    action_taken="FRESH_GEMINI_SUBMISSION",
                    side_effect_count=effects,
                    state_mutations=1 if not dry_run else 0,
                    reason_code="COMMAND_SUBMITTED" if success else "SUBMISSION_FAILED",
                    diagnostic=str(details),
                )

            # Active Task Resume & Recovery Logic
            aistudio_page = await tab_mgr.locate_ai_studio_tab()
            gen_adapter = AIStudioAdapter(aistudio_page)

            # Handle intermediate submission states (COMMAND_DETECTED, COMMAND_VALIDATED, COMMAND_ACCEPTED)
            if task.get_state_enum() in (TaskState.COMMAND_DETECTED, TaskState.COMMAND_VALIDATED, TaskState.COMMAND_ACCEPTED):
                if task.get_state_enum() == TaskState.COMMAND_DETECTED:
                    task.transition_to(TaskState.COMMAND_VALIDATED)
                if task.get_state_enum() == TaskState.COMMAND_VALIDATED:
                    task.transition_to(TaskState.COMMAND_ACCEPTED)
                if task.get_state_enum() == TaskState.COMMAND_ACCEPTED:
                    task.transition_to(TaskState.SUBMISSION_PREPARED)
                if not dry_run:
                    self.repo.save_active_task(task)

            # Recovery for SUBMISSION_PREPARED
            if task.get_state_enum() == TaskState.SUBMISSION_PREPARED:
                full_turns_text = await gen_adapter.extract_all_turns_text()

                outcome = self.reconciler.reconcile_submission_prepared(
                    task=task,
                    ai_studio_turns=full_turns_text,
                    expected_raw_command=task.raw_canonical_block or task.task_id,
                )

                if outcome == ReconciliationOutcome.PROVEN_EXECUTED:
                    task.submission_confirmed = True
                    task.transition_to(TaskState.SENT_TO_IMPLEMENTER)
                    task.transition_to(TaskState.WAITING_FOR_IMPLEMENTER)
                    if not dry_run:
                        self.repo.save_active_task(task)

                    await chrome.disconnect()
                    return self._build_structured_result(
                        status="SUCCESS",
                        session_id=task.session_id,
                        task_id=task.task_id,
                        phase=task.phase,
                        command_sha256=task.command_sha256,
                        cycle_state="SUBMISSION_RECONCILED_EXECUTED",
                        action_taken="RECONCILE_SUBMISSION_RESUME",
                        side_effect_count=0,
                        state_mutations=1 if not dry_run else 0,
                        reason_code="SUBMISSION_PROVEN_EXECUTED",
                        diagnostic="Submission was independently proven in AI Studio turns. Resumed without duplicate submit.",
                    )
                elif outcome == ReconciliationOutcome.PROVEN_NOT_EXECUTED and task.raw_canonical_block:
                    cmd = SupervisorCommand(
                        task_id=task.task_id,
                        phase=task.phase,
                        action=task.action,
                        send_to=task.send_to,
                        zip_required=False,
                        headers={},
                        body="",
                        raw_canonical_block=task.raw_canonical_block,
                        command_sha256=task.command_sha256,
                    )
                    success, effects, details = await self.submitter.submit_command(
                        command=cmd,
                        ai_studio_page=aistudio_page,
                        task=task,
                        session_id=task.session_id,
                        dry_run=dry_run,
                    )
                    await chrome.disconnect()
                    return self._build_structured_result(
                        status="SUCCESS" if success else "ERROR",
                        session_id=task.session_id,
                        task_id=task.task_id,
                        phase=task.phase,
                        command_sha256=task.command_sha256,
                        cycle_state="RESUMED_SUBMISSION_COMPLETED",
                        action_taken="RESUMED_GEMINI_SUBMISSION",
                        side_effect_count=effects,
                        state_mutations=1 if not dry_run else 0,
                        reason_code="COMMAND_SUBMITTED" if success else "SUBMISSION_FAILED",
                        diagnostic=str(details),
                    )
                else:
                    task.transition_to(
                        TaskState.MANUAL_REVIEW_REQUIRED,
                        reason_code="AMBIGUOUS_SUBMISSION_PREPARED",
                        error_message="Submission outcome was ambiguous after crash.",
                    )
                    if not dry_run:
                        self.repo.save_active_task(task)

                    await chrome.disconnect()
                    return self._build_structured_result(
                        status="HALTED",
                        session_id=task.session_id,
                        task_id=task.task_id,
                        phase=task.phase,
                        command_sha256=task.command_sha256,
                        cycle_state="MANUAL_REVIEW_REQUIRED",
                        action_taken="NONE",
                        side_effect_count=0,
                        reason_code="AMBIGUOUS_SUBMISSION_PREPARED",
                        diagnostic="SUBMISSION_PREPARED state was ambiguous. Halted for manual review.",
                        manual_review_required=True,
                        reconciliation_required=True,
                        cycle_terminal=True,
                    )

            # Recovery for RETURN_PREPARED
            if task.get_state_enum() == TaskState.RETURN_PREPARED:
                chatgpt_page = await tab_mgr.locate_chatgpt_tab()
                chatgpt_adapter = ChatGPTAdapter(chatgpt_page)
                cg_user_messages = await chatgpt_adapter.extract_raw_user_messages()

                outcome = self.reconciler.reconcile_return_prepared(
                    task=task,
                    chatgpt_user_messages=cg_user_messages,
                )

                if outcome in (ReconciliationOutcome.PROVEN_EXECUTED, ReconciliationOutcome.PROVEN_COMPLETED):
                    task.return_confirmed = True
                    task.transition_to(TaskState.RETURNED_TO_SUPERVISOR)
                    task.transition_to(TaskState.COMPLETED)
                    if not dry_run:
                        self.repo.archive_to_ledger(task)

                    await chrome.disconnect()
                    return self._build_structured_result(
                        status="SUCCESS",
                        session_id=task.session_id,
                        task_id=task.task_id,
                        phase=task.phase,
                        command_sha256=task.command_sha256,
                        cycle_state="RETURN_RECONCILED_COMPLETED",
                        action_taken="RECONCILE_RETURN_FINALIZE",
                        side_effect_count=0,
                        state_mutations=1 if not dry_run else 0,
                        reason_code="RETURN_PROVEN_EXECUTED",
                        diagnostic="Return was independently proven in ChatGPT USER messages. Finalized task.",
                        cycle_terminal=True,
                    )

            # SENT_TO_IMPLEMENTER / WAITING_FOR_IMPLEMENTER / RESPONSE_DETECTED / RESPONSE_VALIDATED
            if task.get_state_enum() in (
                TaskState.SENT_TO_IMPLEMENTER,
                TaskState.WAITING_FOR_IMPLEMENTER,
                TaskState.IMPLEMENTER_RESPONSE_DETECTED,
                TaskState.IMPLEMENTER_RESPONSE_VALIDATED,
            ):
                # Search for any unfinished PREPARED retry record belonging to the exact active task identity
                existing_prepared_retry = self.safety.idempotency.get_unfinished_prepared_record(
                    session_id=task.session_id,
                    task_id=task.task_id,
                    phase=task.phase,
                    command_sha256=task.command_sha256,
                    operation_type="GEMINI_RETRY",
                )

                gen_state, gen_details = await self.monitor.detect_state(aistudio_page)

                if existing_prepared_retry:
                    if gen_state in (GenerationState.GENERATING, GenerationState.COMPLETED):
                        self.safety.idempotency.record_operation(
                            session_id=task.session_id,
                            task_id=task.task_id,
                            operation_id=existing_prepared_retry.operation_id,
                            idempotency_key=existing_prepared_retry.idempotency_key,
                            command_sha256=task.command_sha256,
                            operation_type="GEMINI_RETRY",
                            state="CONFIRMED",
                        )
                        if not dry_run:
                            self.repo.save_active_task(task)

                        await chrome.disconnect()
                        return self._build_structured_result(
                            status="SUCCESS",
                            session_id=task.session_id,
                            task_id=task.task_id,
                            phase=task.phase,
                            command_sha256=task.command_sha256,
                            cycle_state="RETRY_RECONCILED_CONFIRMED",
                            action_taken="RECONCILE_RETRY_RESUME",
                            side_effect_count=0,
                            state_mutations=1 if not dry_run else 0,
                            reason_code="RETRY_PROVEN_EXECUTED",
                            diagnostic="PREPARED retry click was independently proven executed. Resumed without duplicate click.",
                        )
                    else:
                        task.transition_to(
                            TaskState.MANUAL_REVIEW_REQUIRED,
                            reason_code="AMBIGUOUS_RETRY_CRASH",
                            error_message="Process crashed during GEMINI_RETRY PREPARED state. Retry outcome is ambiguous.",
                        )
                        if not dry_run:
                            self.repo.save_active_task(task)
                        await chrome.disconnect()
                        return self._build_structured_result(
                            status="HALTED",
                            session_id=task.session_id,
                            task_id=task.task_id,
                            phase=task.phase,
                            command_sha256=task.command_sha256,
                            cycle_state="MANUAL_REVIEW_REQUIRED",
                            action_taken="NONE",
                            side_effect_count=0,
                            reason_code="AMBIGUOUS_RETRY_CRASH",
                            diagnostic="GEMINI_RETRY PREPARED crash was ambiguous. Halted for manual review.",
                            manual_review_required=True,
                            reconciliation_required=True,
                            cycle_terminal=True,
                        )

                if gen_state == GenerationState.GENERATING:
                    await chrome.disconnect()
                    return self._build_structured_result(
                        status="SUCCESS",
                        session_id=task.session_id,
                        task_id=task.task_id,
                        phase=task.phase,
                        command_sha256=task.command_sha256,
                        cycle_state="WAITING_FOR_GENERATION",
                        action_taken="WAIT_FOR_GENERATION",
                        side_effect_count=0,
                        reason_code="GENERATION_IN_PROGRESS",
                        diagnostic=str(gen_details),
                        wait_required=True,
                    )

                if gen_state == GenerationState.COMPLETED:
                    latest_output = await gen_adapter.extract_latest_response_text()
                    report = self.inspector.inspect_and_validate_report(
                        gemini_text=latest_output,
                        active_task_id=task.task_id,
                        active_phase=task.phase,
                    )

                    if task.get_state_enum() == TaskState.WAITING_FOR_IMPLEMENTER:
                        task.transition_to(TaskState.IMPLEMENTER_RESPONSE_DETECTED)
                        task.transition_to(TaskState.IMPLEMENTER_RESPONSE_VALIDATED)
                        if not dry_run:
                            self.repo.save_active_task(task)

                    chatgpt_page = await tab_mgr.locate_chatgpt_tab()
                    success, side_effects, details = await self.returner.return_report_to_supervisor(
                        report=report,
                        chatgpt_page=chatgpt_page,
                        task=task,
                        session_id=task.session_id,
                        dry_run=dry_run,
                    )

                    await chrome.disconnect()
                    return self._build_structured_result(
                        status="SUCCESS" if success else "ERROR",
                        session_id=task.session_id,
                        task_id=task.task_id,
                        phase=task.phase,
                        command_sha256=task.command_sha256,
                        cycle_state="RETURN_COMPLETED",
                        action_taken="RETURN_REPORT_TO_CHATGPT",
                        side_effect_count=side_effects,
                        state_mutations=1 if not dry_run else 0,
                        reason_code="REPORT_RETURNED" if success else "RETURN_FAILED",
                        diagnostic=str(details),
                        cycle_terminal=True,
                    )

                # Retry path for RETRY_AVAILABLE (Reconciled Idempotent Operation Contract)
                if gen_state == GenerationState.RETRY_AVAILABLE:
                    retry_attempt = task.retry_count + 1
                    op_id = f"op_retry_{task.task_id}_{task.phase}_{retry_attempt}"
                    idem_key = f"idem_retry_{task.task_id}_{task.phase}_{retry_attempt}_{task.command_sha256}"

                    can_r = self.retry_mgr.can_retry(
                        current_state=gen_state,
                        retry_count=task.retry_count,
                        response_text_exists=False,
                    )
                    if can_r:
                        contract = OperationContract(
                            session_id=task.session_id,
                            task_id=task.task_id,
                            phase=task.phase,
                            operation_id=op_id,
                            idempotency_key=idem_key,
                            command_sha256=task.command_sha256,
                            operation_type="GEMINI_RETRY",
                            payload=f"RETRY_{retry_attempt}",
                        )

                        if not dry_run:
                            self.safety.prepare_operation(contract, task.to_dict())

                        task.retry_count = retry_attempt
                        if not dry_run:
                            self.repo.save_active_task(task)

                        sel = get_config().selectors.get("ai_studio", {})
                        retry_btn = await aistudio_page.query_selector(sel.get("retry_button", "button.retry"))
                        if retry_btn and not dry_run:
                            await retry_btn.click()
                            self.safety.confirm_operation(contract, task.to_dict())

                        await chrome.disconnect()
                        return self._build_structured_result(
                            status="SUCCESS",
                            session_id=task.session_id,
                            task_id=task.task_id,
                            phase=task.phase,
                            command_sha256=task.command_sha256,
                            operation_id=op_id,
                            idempotency_key=idem_key,
                            cycle_state="RETRY_PERFORMED",
                            action_taken="GEMINI_RETRY",
                            side_effect_count=1 if not dry_run else 0,
                            state_mutations=1 if not dry_run else 0,
                            reason_code="RETRY_EXECUTED",
                            diagnostic=f"Executed safe retry attempt {retry_attempt}.",
                        )

            await chrome.disconnect()
            return self._build_structured_result(
                status="SUCCESS",
                session_id=task.session_id if task else None,
                task_id=task.task_id if task else None,
                phase=task.phase if task else None,
                command_sha256=task.command_sha256 if task else None,
                cycle_state="IDLE",
                action_taken="NONE",
                side_effect_count=0,
                reason_code="NO_ACTION",
                diagnostic="No action required in current cycle.",
            )

        except Exception as e:
            await chrome.disconnect()
            return self._build_structured_result(
                status="ERROR",
                session_id=task.session_id if task else None,
                task_id=task.task_id if task else None,
                phase=task.phase if task else None,
                command_sha256=task.command_sha256 if task else None,
                cycle_state="RUNTIME_EXCEPTION",
                action_taken="NONE",
                side_effect_count=0,
                reason_code="RUNTIME_CYCLE_EXCEPTION",
                diagnostic=str(e),
            )
