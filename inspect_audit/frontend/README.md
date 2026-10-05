# frontend

A loopback web chat for talking to an agent inside a running Inspect sample.

The eval side is standard Inspect: any eval launched with `--acp-server` binds
an [ACP](https://agentclientprotocol.com) endpoint (AF_UNIX socket by default)
and writes a discovery file, exactly as `inspect acp` expects. This frontend is
the browser equivalent of `inspect acp`: `server.py` (FastAPI + uvicorn on
127.0.0.1, same hosting shape as `inspect view`) serves the chat page and
relays JSON-RPC frames 1:1 between a browser WebSocket and the eval's socket.
All protocol semantics live in the standard; the page is a minimal ACP client.

## Run

```bash
# terminal 1: an ACP-enabled eval, e.g. the synthesis session
.venv/bin/inspect eval inspect_audit/report -T logs=<log-dir> \
    --model <model> --acp-server --display none

# terminal 2: the chat
.venv/bin/python frontend/server.py        # http://127.0.0.1:7676
```

The page lists running ACP evals (auto-connects when there is exactly one).
Type to talk; Esc button interrupts the current generation; everything is
recorded in the eval log (operator messages as `ChatMessageUser` with
`source="operator"`).

Turn-taking: `inspect_audit/report` hands the floor back via an ACP
elicitation whenever it finishes a turn without tool calls (see
`_report.py:_operator_turn`), so the agent waits for you instead of
self-continuing. The same page also works against any react()/deepagent()
task, where messages are delivered at the next turn boundary instead.

Loopback only, by design: the ACP server has no authentication and the evals
this fronts may hold private benchmark content. Do not bind either side to a
non-loopback interface.
