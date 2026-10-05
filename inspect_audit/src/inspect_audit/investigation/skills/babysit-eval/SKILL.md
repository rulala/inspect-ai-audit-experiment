---
name: babysit-eval
description: "What watching a running eval gets you and what it cannot: live per-sample progress, the runner's actions and stacks, and why steering a job mid-flight is not available to you. Read it before launching anything you intend to watch."
---


## In this container
Read this to understand what babysitting an eval is, then use what you actually have.

Interactive babysitting over the ACP control channel is **not enabled for your jobs**.
It needs `acp_server` and a `human` approval policy in the eval-set config, and the
submission policy refuses both: a job you launch runs unattended and finishes or fails
on its own. So the reference client, `hawk acp`, `session/prompt`, and approving or
rejecting a parked tool call are all out of reach, and you should not try to add those
keys to a config.

What you have instead is observation, which covers everything you need for
"what is this job doing and why is it slow".

Every `hawk <verb> <eval-set-id>` in this file is `jobs(action="<verb>", label="<your job
label>")` for you: evals, watch, logs, trace, stacktrace, status, samples, transcript
(with `sample=<uuid>`), transcripts, wait, collect, stop. There is no `hawk` binary and
no login in this container; the tool runs the same commands on the operator's account,
restricted to the jobs you submitted. Anything the file describes that is not in that
list, you do not have.

Live, while a job runs: `jobs(action="watch")` for per-sample phase and progress,
`jobs(action="trace")` for the runner's in-flight actions, `jobs(action="stacktrace")`
for where it is blocked right now, `jobs(action="logs")` for the runner's output.
After it finishes: `jobs(action="samples")`, then `jobs(action="transcript")` or
`jobs(action="transcripts")` to read what the models actually did, then
`jobs(action="collect")` for the `.eval` logs. `jobs(action="stop")` scores completed
samples and stops the rest; `jobs(action="wait")` blocks without spending tokens.

The judgment in the last section still applies to you: give a starting job a couple of
minutes before deciding something is broken (package install happens before the eval
begins), and prefer one small job you watch to a large one you hope about.

## What this is

Evals launched with `acp_server` set expose a live control channel (Inspect's
ACP server) reachable through the Hawk relay. You can attach to it
programmatically to stream a sample's transcript, answer tool-call approvals
that are parked on a `human` approver, and cancel samples or tool calls.

Full protocol walkthrough: `docs/user-guide/babysitting-evals.md`.
Reference client: `hawk/examples/acp_babysitter.py` (stdlib-only).

## Prerequisites

1. `hawk auth access-token > /dev/null || echo "run 'hawk login' first"`
2. The eval-set config must have `acp_server: <port>` set. For the eval to
   *wait* for you on selected tools it also needs an approval policy with a
   `human` approver (see `hawk/examples/acp-approval.eval-set.yaml`).
   No `acp_server` → nothing to attach to; that can't be added to a running eval.

## Quick start

```bash
# 1. Bridge the run's ACP server to a known local port (keep this running)
hawk acp <eval-set-id> --no-launch --local-port 4444 &

# 2. Babysit: attaches to the first live sample, streams updates, answers approvals
python hawk/examples/acp_babysitter.py 127.0.0.1:4444          # approve tool calls
python hawk/examples/acp_babysitter.py 127.0.0.1:4444 --deny   # reject tool calls
```

The bridge prints `listening on 127.0.0.1:4444` when ready. The in-eval ACP
server starts listening shortly after the runner pod reaches Running (package
install happens first), so wait for `hawk watch <id> --no-follow` to show the
run underway before attaching. Retry pattern for early attaches:

- Client connection drops with no JSON-RPC reply → reconnect the client and retry.
- The **bridge process exits** with a relay error → check which case you're in:
  relay `404` = no live runner pod, i.e. not started yet (restart the bridge and
  retry) **or** already finished (stop — check `hawk watch`); relay `403` = you
  lack write access to the run's model groups, or the CLI is pointed at the
  wrong deployment (retrying won't help).

Give a starting run a couple of minutes of retries before concluding something
is broken.

## Deciding on approvals

Don't blanket-approve with the reference client unless the user asked for
exactly that. For judgment calls, speak the protocol directly (it's
newline-delimited JSON-RPC 2.0 over the bridged TCP socket) or adapt the
reference client: the `session/request_permission` request carries the tool
call's `title` and `rawInput` — evaluate it against the user's instructions,
then reply with the chosen `optionId` (typically `approve` / `reject` /
`terminate`, where `terminate` ends the sample).

Key methods (full table in the docs page):

- `inspect/list_samples` — all active samples; `pending: "approval"` means one
  is waiting on you. Poll this to find work.
- `session/load {sessionId, cwd, mcpServers: []}` — bind; replays the
  transcript, then streams `session/update` notifications.
- `session/request_permission` — server→client *request*; you must reply:
  `{"outcome": {"outcome": "selected", "optionId": "approve"}}`.
- `session/prompt` — send the sample's agent a user message (only when the
  listing says `interactive: true`).
- `inspect/cancel_sample {sessionId, action}` / `inspect/cancel_tool_call {sessionId, toolCallId}`.
- `inspect/session_ended` notification — the sample finished; list samples
  again for the next one.

## Gotchas

- Parked approvals auto-reject after `approval_timeout_minutes` (default one
  week) — a long-unattended eval with a `human` approver won't hang forever,
  but its sandbox pods stay up while parked.
- `interactive: false` samples are observe-only: approvals and cancels work,
  `session/prompt` is rejected.
- One sample can be bound per `session/load`; the connection survives sample
  completion — re-list and load the next `sessionId`.
- The bridged loopback port is unauthenticated while the bridge runs (same
  trust model as `kubectl port-forward`).
- Multiple concurrent attachments are fine (each TCP connection gets its own
  relay WebSocket).

## Related

- Health/progress without ACP: `hawk watch`, `hawk status`, `hawk logs -f`
- Stuck evals: use the `debug-stuck-eval` skill
- Graceful stop: `hawk stop <id>` (scores partial work)
