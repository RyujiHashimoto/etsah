# Use Etsah from an MCP client

Etsah can sit behind any client that speaks MCP over stdio: Claude Desktop, Claude Code, Cursor, ChatGPT connectors, and other MCP hosts. The server is the Python standard library. There is no `mcp` package to install. Python 3.12 is enough.

The checker reads source. It does not send mail, charge a card, or call the tools it is reviewing.

From a checkout:

```bash
python3 etsah.py mcp
```

`etsah-mcp` is the same server. `pyproject.toml` in this repository only registers that console script and `etsah`. Another branch may add its own packaging; merge the two files if both land.

## Tools

| Tool | What it answers | Read-only |
|---|---|---|
| `etsah_check` | Is this contract well formed, and what is the verdict for each task? | yes |
| `etsah_import_tools` | What would these MCP or OpenAI tools look like as `op` declarations? | yes |
| `etsah_explain` | What does `E-POLICY` mean, and what should change? | yes |
| `etsah_check_plan` | Is this sequence of tool calls safe to run? | yes |

Every tool sets `readOnlyHint` to true. None of them call an upstream server.

`etsah_check` returns `ok`, `diagnostics` (`code`, `line`, `message`), `ast_hash`, `audit_id`, and one plan per task. Each plan has a verdict:

| Verdict | Meaning |
|---|---|
| `ALLOW` | The task has no gated effect, and the check reported no error for it. |
| `ASK` | A person must approve before the gated effects run. The reason names the gate and the approver. |
| `BLOCK` | The task has an error, or the contract has an error that is not tied to one task. |

`support_reply` in `examples/agents/support_reply.etsah` is `ASK`: reading ticket `T-1042` is fine, and emailing the customer waits for `support_lead`. The broken twin is `BLOCK` with `emit to an external party has no approval gate`.

`etsah_import_tools` accepts an MCP `tools/list` document (including a JSON-RPC result envelope) or OpenAI function tools. Pass `"kind": "mcp"` or `"kind": "openai"` when the shape is ambiguous. An op whose side effects cannot be inferred gets every effect — `read`, `write`, `net`, `spend`, and `emit` — and a `needs review` flag. Edit that `eff` line before relying on it. `etsah import` on the command line is unchanged: there, an unknown op stays pure and the comment says so.

`etsah_explain` takes a code such as `E-POLICY` and returns an explanation and a suggested fix. An unknown code stays unknown.

`etsah_check_plan` takes the contract source and a list of steps (`tool` or `name`, plus optional `arguments`). `ok` is true only when every step is `ALLOW`. Arguments are not evaluated. The decision is the tool name against the declared effects, the agent allow list, the net allow list, and the gates a task actually names.

A contract that still has an error is fail-closed: every step is `BLOCK`. On a clean contract, a call the contract does not declare is `BLOCK`, a call whose effect has a named gate is `ASK`, and a call inside the allow list with no gate is `ALLOW`. `allow_net` replaces the default host list (`stripe`), the same way `etsah check --allow-net` does.

## Guard

`etsah guard` is a stdio proxy. It forwards `tools/list` to an upstream MCP server. Before each `tools/call` it applies the same per-call rules as `etsah_check_plan`.

```bash
python3 etsah.py guard --contract examples/agents/support_reply.etsah -- python3 path/to/upstream_server.py
```

| Verdict | What the client sees | Upstream |
|---|---|---|
| `ALLOW` | The upstream result | The call is forwarded |
| `BLOCK` | A tool result with `isError: true` and the reason | No call |
| `ASK` | A tool result that asks for a person | No call, unless a signed approval matches this call |

Forwarding `ASK` requires a signature from `etsah approve`. The signature is HMAC-SHA256 from the Python standard library. The shared key is `ETSAH_APPROVAL_KEY`, or a file named by `--approval-key-file` / `ETSAH_APPROVAL_KEY_FILE`. For one minor version, the `PROUT_*` names are still read when the matching `ETSAH_*` variable is unset. New tokens start with `etsah1.`. Tokens that start with `prout1.` still verify. A key file is one line. A leading BOM and one trailing newline are ignored. Set only one key source. The key is not a command-line argument and it is not written to the log.

An approval binds all of the following:

| Claim | What must match |
|---|---|
| Approver | The `approver` declared on the gate |
| Contract | `audit_id`, the contract's SHA-256 |
| Gate | A gate a task names, covering every gated effect of the tool |
| Tool | The op name |
| Arguments | SHA-256 of the arguments as canonical JSON. Key order does not matter. `1` and `1.0` are different values. Omitted arguments hash as `{}` |
| Expiry | `exp`, a unix time. The approval is expired when `now >= exp`. The maximum life is 366 days |
| One use | The `nonce` and the `approval_id` are recorded in a file. Reusing either is rejected |

A bad signature, an expired approval, a different contract, a different gate, a different tool, different arguments, a different approver, or a reuse are all refused. A token that does not match is not recorded as used. Once the guard decides to forward, a later upstream failure does not return the nonce. `BLOCK` is not overridden by a signature.

```bash
python3 etsah.py approve \
  --contract examples/agents/support_reply.etsah \
  --gate approve_reply \
  --op mail.send_reply \
  --approver support_lead \
  --args '{}' \
  --ttl 300
```

The token is one line on stdout. Pass it as `ETSAH_APPROVAL_TOKEN`. `--approval-token` is also read, but it shows up in the process list, so leave it unused on a machine with other users. The guard needs the same key and a nonce file (`--nonce-file` or `ETSAH_APPROVAL_NONCE_FILE`). One-time use holds across the guards that share that file.

Each decision is one JSON object on stderr. A signature that verifies records `approver_id` and `approval_id`. The key and the token are not recorded.

```json
{"audit_id":"<64 hex digits>","tool":"mail.send_reply","verdict":"ASK","reason":"...","forwarded":true,"approved":true,"approver_id":"support_lead","approval_id":"<16 or more characters>","approval_mode":"signed","args_hash":"<64 hex digits>"}
```

`audit_id` is the contract's SHA-256. It is empty when the source does not parse.

The previous rule, "any non-empty string forwards `ASK`", is not the default. It remains only with `--allow-legacy-approval-token` or `ETSAH_ALLOW_LEGACY_APPROVAL_TOKEN=1`. That flag is deprecated. With it set, the guard does not check a signature or the arguments: a non-empty string forwards every `ASK` for that process, and no nonce is recorded. `BLOCK` is still not forwarded.

`limit calls` and `limit spend` are counted down in a file, `--ledger-file` or `ETSAH_LEDGER_FILE`. The write replaces the file while a lock is held. A call past a cap is `BLOCK`, and a signed approval does not override that. The spend amount is the decimal named by the op's `spend from`. A missing amount, an amount that is not positive, or a missing ledger when a cap applies is `BLOCK`. The audit line records `remaining_calls`, `remaining_spend`, and `spend_currency`. The integer-yen `budget` and the hourly `limit net` / `limit emit` caps are still not counted down.

The proxy does not prove that `on_approved` is the only call that performs the effect. If no single named gate covers every gated effect of a tool, a signature cannot forward that `ASK`.

## Claude Desktop

Put this in `claude_desktop_config.json`. Replace the path with the checkout.

```json
{
  "mcpServers": {
    "etsah": {
      "command": "python3",
      "args": ["/path/to/prout-lang/etsah.py", "mcp"]
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
        "/path/to/prout-lang/etsah.py",
        "guard",
        "--contract",
        "/path/to/prout-lang/examples/agents/support_reply.etsah",
        "--",
        "python3",
        "/path/to/upstream_server.py"
      ]
    }
  }
}
```

Put the key and the used-nonce file in `env` on that server entry. Issue a token with `etsah approve` for each call, and pass it as `ETSAH_APPROVAL_TOKEN` when starting the guard. The values below are fictional. Do not commit a real key.

```json
"env": {
  "ETSAH_APPROVAL_KEY_FILE": "/path/to/approval.key",
  "ETSAH_APPROVAL_NONCE_FILE": "/path/to/used-approvals.json"
}
```

The deprecated compatibility flag is the only way to treat a non-empty string as approval. The default guard does not.

```json
"env": {
  "ETSAH_ALLOW_LEGACY_APPROVAL_TOKEN": "1",
  "ETSAH_APPROVAL_TOKEN": "example-approval"
}
```

## Cursor

`.cursor/mcp.json` in the project, or the user-level MCP settings, use the same shape:

```json
{
  "mcpServers": {
    "etsah": {
      "command": "python3",
      "args": ["etsah.py", "mcp"]
    }
  }
}
```

Use an absolute path for `etsah.py` when Cursor's working directory is not the checkout. The guard entry is the same `args` list as in the Claude Desktop example.

## Generic MCP client

Any client that launches a stdio server can use:

```json
{
  "command": "python3",
  "args": ["/path/to/prout-lang/etsah.py", "mcp"]
}
```

After `pip install -e .` from this checkout, `command` may be `etsah-mcp` with an empty `args` list. That install is local. This repository is not published as a package from this change.

The wire format is JSON-RPC 2.0, one JSON object per line. `initialize`, `tools/list`, and `tools/call` are implemented. The server returns the protocol version the client sent. A client that still sends `Content-Length` headers is accepted on input; replies are always one line.

## What this does not do

It does not run cron, store upstream idempotency keys, or decrement the integer-yen `budget`. It does decrement call counts and decimal spend in the ledger file. The guard decides once per call and logs the contract hash, the approver id and approval id when a signature was used, and the remaining count and remaining amount. The nonce file records used approvals so they cannot be replayed. It does not remember the upstream result.

日本語の手順は [docs/mcp.ja.md](../mcp.ja.md) です。
