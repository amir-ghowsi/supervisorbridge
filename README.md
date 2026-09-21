# SupervisorBridge

**SupervisorBridge** is a durable, restart-safe, deterministic orchestration engine connecting **ChatGPT** as SUPERVISOR to **Google AI Studio / Gemini** as IMPLEMENTER using an existing Chrome session through the Chrome DevTools Protocol (CDP).

---

## 1. Project Purpose

SupervisorBridge orchestrates an automated loop between ChatGPT and Google AI Studio without using proprietary external APIs (NO OpenAI API, NO Gemini API). It acts as an authoritative, durable Windows application operating over an existing Chrome browser instance.

### System Workflow
```text
ChatGPT (Supervisor)
    ↓
Formal Executable Command
    ↓
SupervisorBridge (Local Engine)
    ↓
Google AI Studio (Gemini Implementer)
    ↓
Implementer Report
    ↓
SupervisorBridge
    ↓
ChatGPT
    ↓
Next Supervisor Command
```

The system is designed to safely survive process crashes, terminal closures, Chrome refreshes, browser restarts, Windows restarts, temporary network dropouts, DOM selector shifts, and duplicate CLI invocations.

---

## 2. Architecture & Design Principles

SupervisorBridge enforces 10 strict architectural rules:

1. **Browser DOM is NOT durable state:** Persistent local disk state (`data/state/`) is authoritative after task acceptance.
2. **Idempotent External Actions:** Every mutating browser action (typing, clicking submit, clicking retry) is wrapped in an idempotent operation contract.
3. **Fail Closed:** Any ambiguous, unknown, corrupted, or mismatched state halts immediately into `MANUAL_REVIEW_REQUIRED`.
4. **Single Side Effect Rule:** A single runtime execution cycle (`run_once()`) performs at most **ONE** external browser side effect.
5. **Canonical Task Identity:** Task identity originates strictly from the raw executable Supervisor command SHA256 hash.
6. **No API Dependencies:** Relies entirely on Playwright CDP connections to an existing Chrome browser instance.
7. **Strict Recovery Subsystem:** Reconciles pending operations against DOM state before re-executing actions after crashes.
8. **Test State Isolation:** Tests write strictly to isolated temporary directories; production state is never mutated during test runs.

---

## 3. State Machine

Active tasks transition deterministically through defined conceptual states:

```text
                  [ NO_ACTIVE_TASK ]
                         │
                         ▼
                [ COMMAND_DETECTED ]
                         │
                         ▼
               [ COMMAND_VALIDATED ]
                         │
                         ▼
                [ COMMAND_ACCEPTED ]
                         │
                         ▼
              [ SUBMISSION_PREPARED ] ────(proven in DOM)────┐
                         │                                │
                         ▼                                ▼
              [ SENT_TO_IMPLEMENTER ] ◄───────────────────┘
                         │
                         ▼
            [ WAITING_FOR_IMPLEMENTER ]
                         │
                         ▼
         [ IMPLEMENTER_RESPONSE_DETECTED ]
                         │
                         ▼
        [ IMPLEMENTER_RESPONSE_VALIDATED ]
                         │
                         ▼
                 [ RETURN_PREPARED ] ─────(proven in DOM)────┐
                         │                                │
                         ▼                                ▼
             [ RETURNED_TO_SUPERVISOR ] ◄───────────────────┘
                         │
                         ▼
                    [ COMPLETED ] ───► Archived to Ledger
```

If any condition fails, the task transitions to **`FAILED`** or **`MANUAL_REVIEW_REQUIRED`**.

---

## 4. Formal Protocol Specifications

### Executable Supervisor Command Format
ChatGPT commands MUST be enclosed in the mandatory outer bridge envelope:

```text
<<<SUPERVISOR_BRIDGE_COMMAND>>>
[SUPERVISOR]
TASK_ID: <task_id>
PHASE: <phase_number>
ACTION: <action_name>
SEND_TO: GEMINI

<command body text>

[/SUPERVISOR]
<<<END_SUPERVISOR_BRIDGE_COMMAND>>>
```

- **Candidate Rule:** Only `assistant` role messages containing the outer envelope are executable.
- **Precedence Rule:** The newest valid candidate wins; if the newest candidate is malformed, it fails closed (never falling back to an older command).
- **Canonical Hash:** Computed as `SHA256(UTF-8 exact bytes of raw [SUPERVISOR]...[/SUPERVISOR] block)`.

### Implementer Report Format
Gemini reports MUST match the formal report envelope:

```text
[IMPLEMENTER_REPORT]
TASK_ID: <task_id>
PHASE: <phase_number>
STATUS: <SUCCESS|FAILED>
READY_FOR_REVIEW: <YES|NO>

<report body text>

[/IMPLEMENTER_REPORT]
```

---

## 5. Chrome CDP Setup & Windows Run Instructions

### Launching Chrome for DevTools Remote Debugging
On Windows, launch Google Chrome with remote debugging enabled on port `9222`:

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="C:\SupervisorBridge\ChromeProfile"
```

> **Warning:** Do NOT point automation at an actively-used personal Chrome profile unless you understand the security implications. Use a dedicated user profile directory.

### Navigating to Required Tabs
1. Open a tab with ChatGPT logged in (`chatgpt.com` or `chat.openai.com`).
2. Open a tab with Google AI Studio logged in (`aistudio.google.com`).

---

## 6. Configuration

Settings are controlled via YAML in `config/`:

- `config/settings.yaml`: Specifies CDP URL (`http://127.0.0.1:9222`), timeouts, polling parameters, retry/loop limits, state directory, logging, and allowed domain lists.
- `config/selectors.yaml`: Maps DOM selectors for ChatGPT and Google AI Studio landmarks, composers, send buttons, and generation indicators.

---

## 7. Command Line Interface (CLI)

```bash
# Verify system prerequisites & config
python main.py check

# Display system status (JSON optional)
python main.py status [--json]

# Inspect ChatGPT DOM (read-only)
python main.py inspect-chatgpt [--json]

# Inspect Google AI Studio DOM (read-only)
python main.py inspect-ai-studio [--json]

# Inspect persistent state & ledger (read-only)
python main.py state-inspect [--json]

# Inspect recovery status, orphaned state, & active task
python main.py recovery-status [--json]

# Execute a single runtime cycle
python main.py run-once [--dry-run] [--json]

# Execute a safe bounded runtime loop
python main.py run-loop [--max-cycles 10] [--dry-run] [--json]

# Manage Emergency Stop
python main.py emergency-stop status [--json]
python main.py emergency-stop engage [--reason "Maintenance"] [--json]
python main.py emergency-stop release [--reason "Maintenance completed"] [--json]
```

---

## 8. Safety & Emergency Stop Controls

SupervisorBridge includes built-in safety controls:
- **Emergency Stop:** Persisted on disk at `data/state/phase4_checkpoint.json`. When engaged, all browser-mutating actions and state updates are blocked instantly.
- **Idempotency Manager:** Tracks operations on disk to prevent duplicate command submissions or duplicate returns.
- **Loop Guard:** Protects against repeated failing operations by enforcing cycle threshold limits.
- **Retry Budget:** Enforces configurable bounds on Gemini retry attempts.

---

## 9. Recovery & Orphaned States

Upon restart, SupervisorBridge reads persisted disk state:
- If a task is in `SUBMISSION_PREPARED`, it checks AI Studio user turns. If proven submitted, it resumes without re-typing. If unproven, it submits safely.
- If an idempotency or checkpoint file exists on disk for an unarchived task without an active task file, the system flags **`ORPHANED_STATE_DETECTED`** and halts for manual review.

---

## 10. Live Validation Harness

Execute live validation cycles with strict parameter correlation assertions:

```bash
python scripts/live_validation.py \
  --session-id "sess_1001" \
  --task-id "TASK-1001" \
  --command-sha256 "<64_char_hex_hash>" \
  --operation-id "op_submit_TASK-1001_1" \
  --idempotency-key "idem_submit_<64_char_hex_hash>" \
  --dry-run
```

---

## 11. Known Limitations

1. **Browser Active Session Requirement:** Chrome must remain open with active, authenticated sessions on ChatGPT and Google AI Studio.
2. **DOM Selector Shifts:** Major frontend revisions to ChatGPT or Google AI Studio may require updating `config/selectors.yaml`.
3. **Manual Review Resolution:** Tasks halted under `MANUAL_REVIEW_REQUIRED` require operator inspection before state files are cleared or updated.

---

## 12. Testing & Verification

Run standard and strict warning checks across all 60+ unit & integration tests:

```bash
# Run standard unit tests
python3 -m unittest discover -s tests -p "test_*.py"

# Run strict unit tests (warnings treated as errors)
python3 -W error -m unittest discover -s tests -p "test_*.py"

# Run compilation check
python3 -m compileall -q src scripts main.py tests
```
