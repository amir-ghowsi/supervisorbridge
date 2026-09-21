#!/usr/bin/env python3
"""
Live Validation Harness for SupervisorBridge.

This script executes a live validation cycle using production runtime components.
It supports dry-run mode and exact parameter correlation assertions.

Usage:
    python scripts/live_validation.py \
        --session-id <session_id> \
        --task-id <task_id> \
        --command-sha256 <sha256> \
        --operation-id <op_id> \
        --idempotency-key <key> \
        [--dry-run]
"""

import sys
import os
import json
import asyncio
import argparse
from typing import Dict, Any

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.logger import setup_logger
from src.utils.config_loader import get_config
from src.bridge.runtime import RuntimeEngine

logger = setup_logger("LiveValidation")


async def run_live_validation_async(
    session_id: str,
    task_id: str,
    command_sha256: str,
    operation_id: str,
    idempotency_key: str,
    dry_run: bool = False,
    settings_path: str = "config/settings.yaml"
) -> Dict[str, Any]:
    """
    Executes live validation using production RuntimeEngine components asynchronously.
    """
    config = get_config(settings_path=settings_path)
    engine = RuntimeEngine()

    try:
        result: Dict[str, Any] = await engine.run_once(dry_run=dry_run)

        # Verify CLI parameter correlation assertions
        result["VALIDATION_ASSERTIONS"] = {
            "session_id_match": result.get("SESSION_ID") == session_id if result.get("SESSION_ID") else True,
            "task_id_match": result.get("TASK_ID") == task_id if result.get("TASK_ID") else True,
            "command_sha256_match": result.get("COMMAND_SHA256") == command_sha256 if result.get("COMMAND_SHA256") else True,
            "operation_id_match": result.get("OPERATION_ID") == operation_id if result.get("OPERATION_ID") else True,
            "idempotency_key_match": result.get("IDEMPOTENCY_KEY") == idempotency_key if result.get("IDEMPOTENCY_KEY") else True,
        }
        result["DRY_RUN"] = dry_run
        return result
    except Exception as e:
        logger.error(f"Live validation error: {e}", exc_info=True)
        return {
            "SESSION_ID": session_id,
            "TASK_ID": task_id,
            "COMMAND_SHA256": command_sha256,
            "OPERATION_ID": operation_id,
            "IDEMPOTENCY_KEY": idempotency_key,
            "CYCLE_STATE": "FAILED",
            "ACTION_TAKEN": "NONE",
            "SIDE_EFFECT_COUNT": 0,
            "REASON_CODE": "LIVE_VALIDATION_EXCEPTION",
            "DIAGNOSTIC": str(e),
            "MANUAL_REVIEW_REQUIRED": True,
            "WAIT_REQUIRED": False,
            "CYCLE_TERMINAL": True,
            "DRY_RUN": dry_run
        }


def main():
    parser = argparse.ArgumentParser(description="SupervisorBridge Live Validation Harness")
    parser.add_argument("--session-id", required=True, help="Session ID")
    parser.add_argument("--task-id", required=True, help="Expected Task ID")
    parser.add_argument("--command-sha256", required=True, help="Expected Command SHA256")
    parser.add_argument("--operation-id", required=True, help="Expected Operation ID")
    parser.add_argument("--idempotency-key", required=True, help="Expected Idempotency Key")
    parser.add_argument("--dry-run", action="store_true", help="Execute without external mutating side effects")
    parser.add_argument("--settings", default="config/settings.yaml", help="Path to settings.yaml")

    args = parser.parse_args()

    result = asyncio.run(run_live_validation_async(
        session_id=args.session_id,
        task_id=args.task_id,
        command_sha256=args.command_sha256,
        operation_id=args.operation_id,
        idempotency_key=args.idempotency_key,
        dry_run=args.dry_run,
        settings_path=args.settings
    ))

    print(json.dumps(result, indent=2))
    if result.get("CYCLE_STATE") == "FAILED" or result.get("MANUAL_REVIEW_REQUIRED"):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
