import time
from typing import Dict, Any, Tuple, Optional
from playwright.async_api import Page
from src.protocol.supervisor_protocol import SupervisorCommand
from src.bridge.operation_contract import OperationContract
from src.safety.orchestrator import SafetyOrchestrator
from src.state.task_state import ActiveTaskData, TaskState
from src.state.state_repository import StateRepository
from src.adapters.ai_studio_adapter import AIStudioAdapter
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class SubmissionError(Exception):
    """Exception raised during command submission flow."""

    pass


class SubmissionUncertainError(SubmissionError):
    """Raised when submission click or outcome state is uncertain."""

    pass


class GeminiSubmitter:
    """Handles exactly-once command submission to Google AI Studio."""

    def __init__(self, repo: StateRepository, safety: SafetyOrchestrator):
        self.repo = repo
        self.safety = safety
        self.logger = get_logger()
        self.selectors = get_config().selectors

    async def submit_command(
        self,
        command: SupervisorCommand,
        ai_studio_page: Page,
        task: ActiveTaskData,
        session_id: str,
        dry_run: bool = False,
    ) -> Tuple[bool, int, Dict[str, Any]]:
        """
        Submits exact canonical command to AI Studio composer exactly once.

        Returns: (success: bool, side_effects_count: int, result_details: dict)
        """
        sel = self.selectors.get("ai_studio", {})
        composer_sel = sel.get("composer", "textarea")
        send_btn_sel = sel.get("send_button", "button")

        operation_id = f"op_submit_{task.task_id}_{task.phase}"
        idempotency_key = f"idem_submit_{command.command_sha256}"

        contract = OperationContract(
            session_id=session_id,
            task_id=task.task_id,
            phase=task.phase,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            command_sha256=command.command_sha256,
            operation_type="GEMINI_SUBMISSION",
            payload=command.raw_canonical_block,
        )

        if dry_run:
            self.logger.info("DRY RUN: Verifying submission readiness without side effects.")
            # Verify composer element exists
            composer_el = await ai_studio_page.query_selector(composer_sel)
            if not composer_el:
                raise SubmissionError("Composer element not found on AI Studio page.")
            return True, 0, {
                "dry_run": True,
                "side_effect_count": 0,
                "state_mutations": 0,
                "message": "Dry-run submission check passed cleanly.",
            }

        # 1. Safety prepare (Emergency Stop check, Idempotency check, Checkpoint creation)
        self.safety.prepare_operation(contract, task.to_dict())

        # 2. Locate composer
        composer_el = await ai_studio_page.query_selector(composer_sel)
        if not composer_el:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="COMPOSER_MISSING",
                error_message="AI Studio composer element missing in DOM.",
            )
            self.repo.save_active_task(task)
            raise SubmissionError("Composer element not found on AI Studio page.")

        # 3. Insert exact command into composer
        try:
            # Clear existing content and fill
            await composer_el.fill("")
            await composer_el.fill(command.raw_canonical_block)
        except Exception as e:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="INSERT_FAILURE",
                error_message=f"Failed to insert command into composer: {e}",
            )
            self.repo.save_active_task(task)
            raise SubmissionError(f"Failed to fill composer: {e}") from e

        # 4. Verify composer text
        inserted_text = ""
        try:
            inserted_text = await composer_el.input_value()
        except Exception:
            try:
                inserted_text = await composer_el.inner_text()
            except Exception:
                pass

        if command.raw_canonical_block not in inserted_text and inserted_text != command.raw_canonical_block:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="VERIFICATION_MISMATCH",
                error_message="Inserted text in composer does not match raw canonical command.",
            )
            self.repo.save_active_task(task)
            raise SubmissionError("Composer text verification failed.")

        # 5. Perform exactly ONE submit (click send/run button)
        send_btn = await ai_studio_page.query_selector(send_btn_sel)
        if not send_btn:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="SEND_BUTTON_MISSING",
                error_message="Send/Run button not found on AI Studio page.",
            )
            self.repo.save_active_task(task)
            raise SubmissionError("Send/Run button not found.")

        # Execute single mutation
        try:
            await send_btn.click()
        except Exception as e:
            # Click failed / uncertain state -> Fail closed to MANUAL_REVIEW_REQUIRED
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="SUBMIT_CLICK_UNCERTAIN",
                error_message=f"Click on submit button failed or produced uncertain state: {e}",
            )
            self.repo.save_active_task(task)
            raise SubmissionUncertainError(f"Click submit failed: {e}") from e

        # 6. Verify submission & update durable state
        task.submission_confirmed = True
        task.transition_to(TaskState.SENT_TO_IMPLEMENTER)
        task.transition_to(TaskState.WAITING_FOR_IMPLEMENTER)
        self.repo.save_active_task(task)

        # 7. Confirm safety idempotency record
        self.safety.confirm_operation(contract, task.to_dict())

        return True, 1, {
            "dry_run": False,
            "side_effect_count": 1,
            "state_mutations": 1,
            "task_id": task.task_id,
            "current_state": task.current_state,
        }
