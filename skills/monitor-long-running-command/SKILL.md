---
name: "monitor-long-running-command"
description: "Monitor a long-running command or background job: run it detached with a log, poll the log, tell hung from busy (macOS/zsh)."
---

# Monitor a long-running command (macOS/zsh)

Slow scripts, solvers, and pipelines run for minutes. Run them detached with a
log file and poll the log, not the process. GNU `timeout` is absent on macOS,
and exec/process wrappers around `sleep` are unreliable to poll.

## Run detached with a log

1. Start the job with unbuffered output redirected to a log:
   `nohup .venv/bin/python -u script.py --flag > /tmp/job.log 2>&1 & echo started $!`
   Completion: the command returns instantly with a PID.
2. Read the log directly in short exec calls: `cat /tmp/job.log`.
   Completion: you see fresh progress lines, or you know the job prints nothing
   until it exits.
3. To tell busy from hung, inspect the PID:
   `ps -o pid,pcpu,etime,state -p <PID>`
   Completion: 100% CPU with state R means crunching; ~0% CPU for minutes means
   stuck.
4. When the log shows the job finished, confirm the output file was rewritten:
   `ls -la <output-file>`
   Completion: output mtime is newer than the job start.
5. To abort: kill the job PID, then confirm with `ps -p <PID> || echo gone`.
   Completion: the process is no longer listed.

## Pitfalls

- No GNU `timeout` on macOS: `timeout 300 cmd` fails with `command not found:
  timeout`. Do not retry it; use the detached-log pattern above.
- Do not poll a `sleep N; cat log` wrapper through the process tool: the wrapper
  reports stale "still running" indefinitely. Kill the wrapper and `cat` the log
  in a fresh call. Poll state can lag generally; a fresh `cat`/`ps` call is
  ground truth.
- Killing the exec session that launched a job inline kills the whole process
  tree, including a long solve mid-run. Detached (`nohup`) jobs survive session
  kills; if you must stop an inline job, kill only the child PID.
- Do not pipe the long job through `tail`: pipes buffer, so no progress appears
  until the command exits. Redirect to a file instead.
- Python buffers stdout when redirected: add `-u` or progress lines stay in the
  buffer.
- zsh expands `=word` to a command path (EQUALS option): `echo ===` fails with
  `== not found`. Use a plain separator like `echo SEP`.

## Scheduling the completion check

A recurring check-in automation rejects `schedule.intervalMinutes`; the
`schedule` accepts `everyMs` or `expr` (plus `at`). Read the validation error,
which lists the accepted keys, before retrying.
