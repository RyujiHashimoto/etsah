# Use PROUT from an MCP client

PROUT can sit behind any client that speaks MCP over stdio: Claude Desktop, Claude Code, Cursor, ChatGPT connectors, and other MCP hosts. The server is the Python standard library. There is no `mcp` package to install. Python 3.12 is enough.

The checker reads source. It does not send mail, charge a card, or call the tools it is reviewing.

From a checkout:

```bash
python3 prout.py mcp
```

`prout-mcp` is the same server. `pyproject.toml` in this repository only registers that console script and `prout`. Another branch may add its own packaging; merge the two files if both land.

## Tools

| Tool | What it answers | Read-only |
|---|---|---|
| `prout_check` | Is this contract well formed, and what is the verdict for each task? | yes |
| `prout_import_tools` | What would these MCP or OpenAI tools look like as `op` declarations? | yes |
| `prout_explain` | What does `E-POLICY` mean, and what should change? | yes |
| `prout_check_plan` | Is this sequence of tool calls safe to run? | yes |

Every tool sets `readOnlyHint` to true. None of them call an upstream server.

`prout_check` returns `ok`, `diagnostics` (`code`, `line`, `message`), `ast_hash`, `audit_id`, and one plan per task. Each plan has a verdict:

| Verdict | Meaning |
|---|---|
| `ALLOW` | The task has no gated effect, and the check reported no error for it. |
| `ASK` | A person must approve before the gated effects run. The reason names the gate and the approver. |
| `BLOCK` | The task has an error, or the contract has an error that is not tied to one task. |

`support_reply` in `examples/agents/support_reply.prout` is `ASK`: reading ticket `T-1042` is fine, and emailing the customer waits for `support_lead`. The broken twin is `BLOCK` with `emit to an external party has no approval gate`.

`prout_import_tools` accepts an MCP `tools/list` document (including a JSON-RPC result envelope) or OpenAI function tools. Pass `"kind": "mcp"` or `"kind": "openai"` when the shape is ambiguous. An op whose side effects cannot be inferred gets every effect — `read`, `write`, `net`, `spend`, and `emit` — and a `needs review` flag. Edit that `eff` line before relying on it. `prout import` on the command line is unchanged: there, an unknown op stays pure and the comment says so.

`prout_explain` takes a code such as `E-POLICY` and returns an explanation and a suggested fix. An unknown code stays unknown.

`prout_check_plan` takes the contract source and a list of steps (`tool` or `name`, plus optional `arguments`). `ok` is true only when every step is `ALLOW`. Arguments are not evaluated. The decision is the tool name against the declared effects, the agent allow list, the net allow list, and the gates a task actually names.

A contract that still has an error is fail-closed: every step is `BLOCK`. On a clean contract, a call the contract does not declare is `BLOCK`, a call whose effect has a named gate is `ASK`, and a call inside the allow list with no gate is `ALLOW`. `allow_net` replaces the default host list (`stripe`), the same way `prout check --allow-net` does.

## Guard

`prout guard` is a stdio proxy. It forwards `tools/list` to an upstream MCP server. Before each `tools/call` it applies the same per-call rules as `prout_check_plan`.

```bash
python3 prout.py guard --contract examples/agents/support_reply.prout -- python3 path/to/upstream_server.py
```

| Verdict | What the client sees | Upstream |
|---|---|---|
| `ALLOW` | The upstream result | The call is forwarded |
| `BLOCK` | A tool result with `isError: true` and the reason | No call |
| `ASK` | A tool result that asks for a person | No call, unless an approval token is set |

`PROUT_APPROVAL_TOKEN` or `--approval-token` forwards `ASK` when the value is non-empty. The token is a presence flag for this first step. It does not select a gate, it does not override `BLOCK`, and it is not written to the log. Each decision is one JSON object on stderr:

```json
{"audit_id":"<64 hex digits>","tool":"mail.send_reply","verdict":"ASK","reason":"...","forwarded":false,"approved":false}
```

`audit_id` is the contract's SHA-256. It is empty when the source does not parse.

The proxy does not count hourly limits or spend budgets, and it does not prove that `on_approved` is the only call that performs the effect.

## Claude Desktop

Put this in `claude_desktop_config.json`. Replace the path with the checkout.

```json
{
  "mcpServers": {
    "prout": {
      "command": "python3",
      "args": ["/path/to/prout-lang/prout.py", "mcp"]
    }
  }
}
```

A guarded upstream server, with fictional ticket mail held for approval:

```json
{
  "mcpServers": {
    "harbor-lane": {
      "command": "python3",
      "args": [
        "/path/to/prout-lang/prout.py",
        "guard",
        "--contract",
        "/path/to/prout-lang/examples/agents/support_reply.prout",
        "--",
        "python3",
        "/path/to/upstream_server.py"
      ]
    }
  }
}
```

To let `ASK` through for a local experiment, set `env` on that server entry. Do not commit a real token.

```json
"env": {
  "PROUT_APPROVAL_TOKEN": "example-approval"
}
```

## Cursor

`.cursor/mcp.json` in the project, or the user-level MCP settings, use the same shape:

```json
{
  "mcpServers": {
    "prout": {
      "command": "python3",
      "args": ["prout.py", "mcp"]
    }
  }
}
```

Use an absolute path for `prout.py` when Cursor's working directory is not the checkout. The guard entry is the same `args` list as in the Claude Desktop example.

## Generic MCP client

Any client that launches a stdio server can use:

```json
{
  "command": "python3",
  "args": ["/path/to/prout-lang/prout.py", "mcp"]
}
```

After `pip install -e .` from this checkout, `command` may be `prout-mcp` with an empty `args` list. That install is local. This repository is not published as a package from this change.

The wire format is JSON-RPC 2.0, one JSON object per line. `initialize`, `tools/list`, and `tools/call` are implemented. The server returns the protocol version the client sent. A client that still sends `Content-Length` headers is accepted on input; replies are always one line.

## What this does not do

It does not run cron, store idempotency keys, or decrement a budget. Those checks stay in the contract text. The guard is the first runtime step: one decision per call, logged with the contract hash.

日本語の手順は [docs/mcp.ja.md](../mcp.ja.md) です。
