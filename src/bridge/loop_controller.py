import asyncio
import time
from typing import Dict, Any, Optional
from src.bridge.runtime import RuntimeEngine
from src.safety.loop_guard import LoopGuard, LoopGuardViolationError
from src.utils.logger import get_logger
from src.utils.config_loader import get_config


class LoopController:
    """Manages continuous loop execution for SupervisorBridge."""

    def __init__(self, runtime: Optional[RuntimeEngine] = None):
        self.logger = get_logger()
        self.runtime = runtime or RuntimeEngine()
        cfg = get_config()
        self.poll_interval_sec = cfg.settings.get("polling", {}).get("interval_sec", 2.0)
        self.loop_guard = LoopGuard()

    async def run_loop(self, dry_run: bool = False, max_cycles: Optional[int] = None) -> None:
        """Runs the loop until stopped or terminal/manual review state reached."""
        self.logger.info("Starting SupervisorBridge runtime loop...")
        cycle_count = 0

        while True:
            cycle_count += 1
            if max_cycles and cycle_count > max_cycles:
                self.logger.info(f"Loop reached maximum cycle limit ({max_cycles}). Stopping loop.")
                break

            try:
                # Record cycle with loop guard
                action_sig = f"cycle_{cycle_count}"
                self.loop_guard.record_cycle(operation_signature=action_sig)

                result = await self.runtime.run_once(dry_run=dry_run)
                self.logger.info(
                    f"Cycle {cycle_count}: Action={result.get('ACTION_TAKEN')}, SideEffects={result.get('SIDE_EFFECT_COUNT')}, Reason={result.get('REASON_CODE')}"
                )

                status = result.get("status")
                if status in ("HALTED", "ORPHANED_STATE", "ERROR") or result.get("CYCLE_TERMINAL", False):
                    self.logger.warning(
                        f"Loop halted due to status '{status}'. Reason: {result.get('DIAGNOSTIC')}"
                    )
                    break

            except LoopGuardViolationError as e:
                self.logger.error(f"Loop Guard violation: {e}. Stopping loop.")
                break
            except Exception as e:
                self.logger.error(f"Unexpected error in runtime loop cycle: {e}")
                break

            await asyncio.sleep(self.poll_interval_sec)
