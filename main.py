import argparse
import asyncio
import json
import sys
from typing import Dict, Any

from src.utils.config_loader import get_config, ConfigurationError
from src.utils.logger import setup_logger, get_logger
from src.browser.chrome_manager import ChromeManager
from src.browser.tab_manager import TabManager
from src.adapters.chatgpt_adapter import ChatGPTAdapter
from src.adapters.ai_studio_adapter import AIStudioAdapter
from src.bridge.runtime import RuntimeEngine
from src.bridge.loop_controller import LoopController
from src.diagnostics.state_inspector import StateInspector
from src.safety.emergency_stop import EmergencyStopManager


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SupervisorBridge: Deterministic Orchestration Engine connecting ChatGPT to Google AI Studio"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # check command
    subparsers.add_parser("check", help="Verify configuration and system prerequisites")

    # status command
    status_parser = subparsers.add_parser("status", help="Show system and configuration status")
    status_parser.add_argument("--json", action="store_true", help="Output status in JSON format")

    # inspect-chatgpt command
    inspect_chatgpt_parser = subparsers.add_parser("inspect-chatgpt", help="Inspect ChatGPT DOM state (read-only)")
    inspect_chatgpt_parser.add_argument("--json", action="store_true", help="Output inspection in JSON format")

    # inspect-ai-studio command
    inspect_aistudio_parser = subparsers.add_parser("inspect-ai-studio", help="Inspect AI Studio DOM state (read-only)")
    inspect_aistudio_parser.add_argument("--json", action="store_true", help="Output inspection in JSON format")

    # state-inspect command
    state_inspect_parser = subparsers.add_parser("state-inspect", help="Inspect persistent state and recovery status (read-only)")
    state_inspect_parser.add_argument("--json", action="store_true", help="Output state inspection in JSON format")

    # recovery-status command
    recovery_status_parser = subparsers.add_parser("recovery-status", help="Inspect recovery status, orphaned state, and active tasks")
    recovery_status_parser.add_argument("--json", action="store_true", help="Output recovery status in JSON format")

    # run-once command
    run_once_parser = subparsers.add_parser("run-once", help="Execute single cycle of orchestration engine")
    run_once_parser.add_argument("--dry-run", action="store_true", help="Dry run without browser mutation or state changes")
    run_once_parser.add_argument("--json", action="store_true", help="Output cycle result in JSON format")

    # run-loop command
    run_loop_parser = subparsers.add_parser("run-loop", help="Execute safe bounded runtime loop")
    run_loop_parser.add_argument("--max-cycles", type=int, default=10, help="Maximum loop cycles before stopping")
    run_loop_parser.add_argument("--dry-run", action="store_true", help="Dry run without browser mutation or state changes")
    run_loop_parser.add_argument("--json", action="store_true", help="Output loop results in JSON format")

    # emergency-stop command
    estop_parser = subparsers.add_parser("emergency-stop", help="Manage emergency stop state")
    estop_subparsers = estop_parser.add_subparsers(dest="estop_action", help="Emergency stop actions")

    estop_status = estop_subparsers.add_parser("status", help="Get emergency stop status")
    estop_status.add_argument("--json", action="store_true", help="Output status in JSON format")

    estop_engage = estop_subparsers.add_parser("engage", help="Engage emergency stop")
    estop_engage.add_argument("--reason", default="Manual CLI emergency stop engagement", help="Reason for engaging emergency stop")
    estop_engage.add_argument("--json", action="store_true", help="Output in JSON format")

    estop_release = estop_subparsers.add_parser("release", help="Release emergency stop")
    estop_release.add_argument("--reason", default="Manual CLI emergency stop release", help="Reason for releasing emergency stop")
    estop_release.add_argument("--json", action="store_true", help="Output in JSON format")

    return parser


def run_check() -> int:
    logger = get_logger()
    logger.info("Running system check...")
    try:
        cfg = get_config()
        logger.info("Configuration check PASSED.")
        logger.info(f"CDP URL: {cfg.settings.get('cdp_url')}")
        logger.info(f"Log Level: {cfg.settings.get('logging', {}).get('level')}")
        return 0
    except ConfigurationError as e:
        logger.error(f"Configuration check FAILED: {e}")
        return 1
    except Exception as e:
        logger.error(f"System check FAILED with unexpected error: {e}")
        return 1


def run_status(json_output: bool = False) -> int:
    try:
        cfg = get_config()
        status_data = {
            "status": "OK",
            "cdp_url": cfg.settings.get("cdp_url"),
            "state_dir": cfg.settings.get("paths", {}).get("state_dir"),
            "log_level": cfg.settings.get("logging", {}).get("level"),
            "allowed_domains": cfg.settings.get("allowed_domains"),
        }
    except Exception as e:
        status_data = {
            "status": "ERROR",
            "error": str(e),
        }

    if json_output:
        print(json.dumps(status_data, indent=2))
    else:
        print("=== SupervisorBridge Status ===")
        for k, v in status_data.items():
            print(f"{k}: {v}")

    return 0 if status_data.get("status") == "OK" else 1


async def run_inspect_chatgpt(json_output: bool = False) -> int:
    logger = get_logger()
    chrome = ChromeManager()
    try:
        browser = await chrome.connect()
        tab_mgr = TabManager(browser)
        page = await tab_mgr.locate_chatgpt_tab()
        adapter = ChatGPTAdapter(page)
        inspection = await adapter.inspect()
        await chrome.disconnect()

        if json_output:
            print(json.dumps(inspection, indent=2))
        else:
            print("=== ChatGPT Inspection ===")
            for k, v in inspection.items():
                print(f"{k}: {v}")
        return 0
    except Exception as e:
        err_res = {"status": "ERROR", "target": "ChatGPT", "error": str(e)}
        if json_output:
            print(json.dumps(err_res, indent=2))
        else:
            logger.error(f"ChatGPT inspection failed: {e}")
        await chrome.disconnect()
        return 1


async def run_inspect_ai_studio(json_output: bool = False) -> int:
    logger = get_logger()
    chrome = ChromeManager()
    try:
        browser = await chrome.connect()
        tab_mgr = TabManager(browser)
        page = await tab_mgr.locate_ai_studio_tab()
        adapter = AIStudioAdapter(page)
        inspection = await adapter.inspect()
        await chrome.disconnect()

        if json_output:
            print(json.dumps(inspection, indent=2))
        else:
            print("=== AI Studio Inspection ===")
            for k, v in inspection.items():
                print(f"{k}: {v}")
        return 0
    except Exception as e:
        err_res = {"status": "ERROR", "target": "AI Studio", "error": str(e)}
        if json_output:
            print(json.dumps(err_res, indent=2))
        else:
            logger.error(f"AI Studio inspection failed: {e}")
        await chrome.disconnect()
        return 1


def run_state_inspect(json_output: bool = False) -> int:
    inspector = StateInspector()
    state_data = inspector.inspect_full_state()
    if json_output:
        print(json.dumps(state_data, indent=2))
    else:
        print("=== State Inspection ===")
        for k, v in state_data.items():
            print(f"{k}: {v}")
    return 0


def run_recovery_status(json_output: bool = False) -> int:
    inspector = StateInspector()
    full_state = inspector.inspect_full_state()
    recovery_data = {
        "status": "OK",
        "has_active_task": full_state.get("has_active_task", False),
        "active_task": full_state.get("active_task"),
        "orphaned_state_detected": full_state.get("orphaned_state_detected", False),
        "completed_count": full_state.get("completed_count", 0),
        "idempotency_record_count": len(full_state.get("idempotency_records", {})),
        "estop_engaged": full_state.get("emergency_stop", {}).get("engaged", False),
    }

    if json_output:
        print(json.dumps(recovery_data, indent=2))
    else:
        print("=== Recovery Status ===")
        for k, v in recovery_data.items():
            print(f"{k}: {v}")
    return 0


async def run_run_once(dry_run: bool = False, json_output: bool = False) -> int:
    runtime = RuntimeEngine()
    result = await runtime.run_once(dry_run=dry_run)

    if json_output:
        print(json.dumps(result, indent=2))
    else:
        print("=== Run Once Result ===")
        for k, v in result.items():
            print(f"{k}: {v}")

    return 0 if result.get("status") in ("SUCCESS", "HALTED", "NO_ACTION") else 1


async def run_run_loop(max_cycles: int = 10, dry_run: bool = False, json_output: bool = False) -> int:
    controller = LoopController(max_cycles=max_cycles)
    results = await controller.run_loop(dry_run=dry_run)

    if json_output:
        print(json.dumps(results, indent=2))
    else:
        print(f"=== Run Loop Completed ({len(results)} cycles) ===")
        for idx, res in enumerate(results, start=1):
            print(f"Cycle {idx}: Action={res.get('ACTION_TAKEN')} State={res.get('CYCLE_STATE')} Reason={res.get('REASON_CODE')}")

    return 0


def run_emergency_stop(estop_action: str, reason: str = "", json_output: bool = False) -> int:
    estop = EmergencyStopManager()

    if estop_action == "engage":
        estop.engage(reason=reason)
        res = {"status": "ENGAGED", "reason": reason}
    elif estop_action == "release":
        estop.release(reason=reason)
        res = {"status": "RELEASED", "reason": reason}
    else:
        # status or default
        status_info = estop.get_status()
        res = {"status": "OK", "estop": status_info}

    if json_output:
        print(json.dumps(res, indent=2))
    else:
        print("=== Emergency Stop ===")
        for k, v in res.items():
            print(f"{k}: {v}")
    return 0


def main() -> int:
    parser = build_cli_parser()
    args = parser.parse_args()

    setup_logger(level="INFO", to_console=True)

    if args.command == "check":
        return run_check()
    elif args.command == "status":
        return run_status(json_output=getattr(args, "json", False))
    elif args.command == "inspect-chatgpt":
        return asyncio.run(run_inspect_chatgpt(json_output=getattr(args, "json", False)))
    elif args.command == "inspect-ai-studio":
        return asyncio.run(run_inspect_ai_studio(json_output=getattr(args, "json", False)))
    elif args.command == "state-inspect":
        return run_state_inspect(json_output=getattr(args, "json", False))
    elif args.command == "recovery-status":
        return run_recovery_status(json_output=getattr(args, "json", False))
    elif args.command == "run-once":
        return asyncio.run(
            run_run_once(
                dry_run=getattr(args, "dry_run", False),
                json_output=getattr(args, "json", False),
            )
        )
    elif args.command == "run-loop":
        return asyncio.run(
            run_run_loop(
                max_cycles=getattr(args, "max_cycles", 10),
                dry_run=getattr(args, "dry_run", False),
                json_output=getattr(args, "json", False),
            )
        )
    elif args.command == "emergency-stop":
        return run_emergency_stop(
            estop_action=getattr(args, "estop_action", "status"),
            reason=getattr(args, "reason", ""),
            json_output=getattr(args, "json", False),
        )
    elif args.command is None:
        parser.print_help()
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
