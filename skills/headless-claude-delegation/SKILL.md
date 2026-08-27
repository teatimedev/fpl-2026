---
name: "headless-claude-delegation"
description: "Delegating long-running work to headless Claude Code: spawn detached with a log, schedule an automation to check completion and report back"
---

# Headless Claude Code Delegation

Delegate long-running edits or specs to Claude Code without blocking the turn, and guarantee the user gets the result without asking.

## Spawn the task

1. Build one self-contained prompt: absolute file paths, explicit scope ("design, do NOT implement" for spec work), style constraints, and required cross-reference updates.
2. Run it detached with output captured:
   `nohup <claude-binary> -p "<prompt>" --permission-mode acceptEdits --output-format text > /tmp/<topic>.log 2>&1 & echo "started pid $!"`
   Completion criterion: the command prints a PID and returns immediately.
3. Record the PID and log path — both are needed by the completion check.

## Schedule the completion check

4. Add a one-shot automation (`automations` action=add): schedule kind `at` ~15 minutes after spawn (longer for large docs), `deleteAfterRun: true`, delivery mode `announce`, payload instructing: check the PID (`ps -p <pid>`), tail the log, verify the expected change in the target file, then summarize the result to the user — or reschedule another check if still running.
   Completion criterion: the automation response confirms `nextRunAtMs` is set.

## Report

5. Tell the user what was delegated, what to expect, and when you will report back. Do not end the turn while a delegated process may finish unattended with no scheduled check.
   Completion criterion: the reply names the deliverable and the promised report time.

## Verify on wake

6. When the check fires and the process is done: read the changed file sections (not just the log tail), summarize concretely for the user, and surface follow-up decisions (e.g. uncommitted changes, next implementation step).
   Completion criterion: the user receives a summary of actual file content, not just "it finished".
