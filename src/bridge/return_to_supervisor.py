from typing import Tuple, Dict, Any
from playwright.async_api import Page
from src.protocol.implementer_protocol import ImplementerReport
from src.bridge.operation_contract import OperationContract
from src.safety.orchestrator import SafetyOrchestrator
from src.state.task_state import ActiveTaskData, TaskState
from src.state.state_repository import StateRepository
from src.adapters.chatgpt_adapter import ChatGPTAdapter
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class ReturnError(Exception):
    """Exception raised during return to ChatGPT flow."""

    pass


class ReturnUncertainError(ReturnError):
    """Raised when return click or post-return state is uncertain."""

    pass


class SupervisorReturner:
    """Delivers validated Implementer Report back to ChatGPT composer exactly once."""

    def __init__(self, repo: StateRepository, safety: SafetyOrchestrator):
        self.repo = repo
        self.safety = safety
        self.logger = get_logger()
        self.selectors = get_config().selectors

    async def return_report_to_supervisor(
        self,
        report: ImplementerReport,
        chatgpt_page: Page,
        task: ActiveTaskData,
        session_id: str,
        dry_run: bool = False,
    ) -> Tuple[bool, int, Dict[str, Any]]:
        """
        Submits exact raw report block into ChatGPT composer exactly once.
        Returns: (success: bool, side_effects_count: int, details: dict)
        """
        sel = self.selectors.get("chatgpt", {})
        composer_sel = sel.get("composer", "textarea")
        send_btn_sel = sel.get("send_button", "button")

        operation_id = f"op_return_{task.task_id}_{task.phase}"
        idempotency_key = f"idem_return_{task.task_id}_{task.phase}_{task.command_sha256}"

        contract = OperationContract(
            session_id=session_id,
            task_id=task.task_id,
            phase=task.phase,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            command_sha256=task.command_sha256,
            operation_type="SUPERVISOR_RETURN",
            payload=report.raw_report_block,
        )

        if dry_run:
            self.logger.info("DRY RUN: Verifying return readiness without side effects.")
            composer_el = await chatgpt_page.query_selector(composer_sel)
            if not composer_el:
                raise ReturnError("ChatGPT composer element not found.")
            return True, 0, {
                "dry_run": True,
                "side_effect_count": 0,
                "state_mutations": 0,
                "message": "Dry-run return check passed cleanly.",
            }

        # 1. Safety prepare (Emergency Stop check, Idempotency check, Checkpoint creation)
        self.safety.prepare_operation(contract, task.to_dict())

        # 2. Locate ChatGPT composer
        composer_el = await chatgpt_page.query_selector(composer_sel)
        if not composer_el:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="CHATGPT_COMPOSER_MISSING",
                error_message="ChatGPT composer element missing in DOM.",
            )
            self.repo.save_active_task(task)
            raise ReturnError("ChatGPT composer element not found.")

        # 3. Insert exact report text
        try:
            await composer_el.fill("")
            await composer_el.fill(report.raw_report_block)
        except Exception as e:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="RETURN_FILL_FAILURE",
                error_message=f"Failed to fill ChatGPT composer: {e}",
            )
            self.repo.save_active_task(task)
            raise ReturnError(f"Failed to fill ChatGPT composer: {e}") from e

        # 4. Verify composer text
        inserted_text = ""
        try:
            inserted_text = await composer_el.input_value()
        except Exception:
            try:
                inserted_text = await composer_el.inner_text()
            except Exception:
                pass

        if report.raw_report_block not in inserted_text and inserted_text != report.raw_report_block:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="RETURN_VERIFICATION_MISMATCH",
                error_message="ChatGPT composer text mismatch.",
            )
            self.repo.save_active_task(task)
            raise ReturnError("ChatGPT composer text verification failed.")

        # 5. Locate send button
        send_btn = await chatgpt_page.query_selector(send_btn_sel)
        if not send_btn:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="CHATGPT_SEND_BUTTON_MISSING",
                error_message="ChatGPT send button missing in DOM.",
            )
            self.repo.save_active_task(task)
            raise ReturnError("ChatGPT send button not found.")

        # 6. Execute single return click
        try:
            await send_btn.click()
        except Exception as e:
            task.transition_to(
                TaskState.MANUAL_REVIEW_REQUIRED,
                reason_code="RETURN_CLICK_UNCERTAIN",
                error_message=f"Click on ChatGPT send button failed or uncertain: {e}",
            )
            self.repo.save_active_task(task)
            raise ReturnUncertainError(f"Click return failed: {e}") from e

        # 7. Update durable state & archive
        task.return_confirmed = True
        task.transition_to(TaskState.RETURN_PREPARED)
        task.transition_to(TaskState.RETURNED_TO_SUPERVISOR)
        task.transition_to(TaskState.COMPLETED)

        self.safety.confirm_operation(contract, task.to_dict())

        # Archive to completed ledger
        self.repo.archive_to_ledger(task)

        return True, 1, {
            "dry_run": False,
            "side_effect_count": 1,
            "state_mutations": 1,
            "task_id": task.task_id,
            "status": "RETURNED_AND_COMPLETED",
        }
