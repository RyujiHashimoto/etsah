# Guard your agent's tools

Etsah is contracts for AI agents: declare effects and approvals, catch unsafe automations before they run.

This is a five-minute path from a tool list to a check that fails before an agent emails a customer, charges a card, cancels a subscription, or calls a host that is not on the allowlist. Python 3.12 is enough. There are no packages to install.

The same check runs in the browser at [docs/playground/index.html](../playground/index.html). That page goes live once GitHub Pages is enabled. It does not enable Pages by itself. How this differs from Cedar, OPA, Guardrails, and effect systems is in [comparison.md](comparison.md).

```bash
git clone https://github.com/RyujiHashimoto/prout-lang.git
cd prout-lang
```

The checker reads source. It does not run the agent.

## 1. Check a contract

`examples/agents/support_reply.etsah` drafts a reply for a fictional ticket. Reading the ticket is `read`. Emailing the customer is `emit(mail)`, and `policy external` says that destination is outside the desk. The task names `gate approve_reply`, whose approver is the human role `support_lead`.

```bash
python3 etsah.py check examples/agents/support_reply.etsah
```

The command exits 0. The plan names `draft_reply`, the gate, and the hourly `emit` cap. `0 error(s), 0 warning(s)` is the line to look for.

The broken twin declares the same gate and then forgets to put it on the task:

```bash
python3 etsah.py check examples/agents/support_reply_broken.etsah
```

The exit code is 1. The diagnostic is `E-POLICY` and the message contains `emit to an external party has no approval gate (emit(mail))`.

## 2. The same check from Python

`etsah_api.py` calls the parser and checker that `etsah.py check` uses. Run it from the repository root, or put that directory on `PYTHONPATH`.

```python
from etsah_api import check_file

result = check_file("examples/agents/support_reply.etsah")
assert result.ok
assert result.plans[0].name == "draft_reply"
assert result.plans[0].gates == ("approve_reply",)
print(result.ast_hash)
print(result.plans[0].text)
```

`check_source(text)` takes a string instead of a path. Each diagnostic has `code`, `message`, and `line` (`severity` is there too). `ast_hash` is the 12-character display prefix. `audit_id` is the full SHA-256. `ok` stays true when the only findings are warnings. A syntax error is one `E-PARSE` diagnostic and an empty hash; the checker still stops at the first syntax error. A missing file raises `OSError`.

`allow_net` replaces the default host list. An omitted or empty list keeps the default, which is `stripe`.

`deny_net` rejects a host even when the allow list includes it. `examples/lantern_status.etsah` is the command-line form: `--deny-net lantern` fails the check even with `--allow-net lantern`.

```python
from etsah_api import check_file

result = check_file(
    "examples/agents/github_triage.etsah",
    allow_net=["github"],
)
assert result.ok
```

## 3. Import the tools the agent already has

MCP tool lists and OpenAI function-calling tools become `op` declarations. Inferred effects are comments. Edit the `eff` line to override them. Comments do not change the check.

```bash
python3 etsah.py import mcp examples/agents/tools/mcp_tools.json > /tmp/tools.etsah
python3 etsah.py import openai examples/agents/tools/openai_tools.json > /tmp/openai.etsah
```

The same conversions are committed at `examples/agents/tools/mcp_tools.etsah` and `examples/agents/tools/openai_tools.etsah`. `send_email` is the op to read first:

```etsah
op send_email(to: String, body: String, attachments: SendEmailAttachments) -> String
  doc "Email a drafted reply to the customer."
  # review: return type String is a placeholder; the tool JSON has no output schema
  # review: JSON Schema required omits attachments; Etsah parameters are all required
  # review: parameter attachments has JSON type array; imported as opaque type SendEmailAttachments
  # inferred: openWorldHint -> net(send_email). Change this to emit if the tool sends a message instead of calling a host.
  # review: name matches send -> emit(mail)
  # review: name matches email -> emit(mail)
  eff net(send_email), emit(mail)
  impl native
```

`net(send_email)` is not on the default allowlist, so checking this file as-is reports `E-POLICY` for that host. Rename the target to a host you pass with `--allow-net`, or drop `net` and keep `emit(mail)` when the tool sends mail. Then add an `agent`, a `task`, and a `gate`, as `examples/agents/support_reply.etsah` does.

`post_status` in the MCP sample sets `"effects": ["emit(board)"]`. The name still matches `post`. The comment says that `emit(mail)` suggestion was not applied. The `eff` line is the override.

`get_forecast` has no true hint and no matching name. There is no `eff` line. A review comment says the op stays pure until you add one. An empty effect list is not a silent claim that the tool is safe.

From Python, `etsah_import.import_mcp` and `etsah_import.import_openai` take the parsed JSON and return the same text.

## Mapping

A hint is used only when its value is JSON `true`. `false` and a missing hint add no effect.

| Signal | Effect | Comment |
|---|---|---|
| `readOnlyHint: true` | `read(<op name>)` | `inferred` |
| `destructiveHint: true` | `write(<op name>)` | `inferred` |
| `openWorldHint: true` | `net(<op name>)` | `inferred`. The comment says to switch the effect to `emit` if the tool sends a message instead of calling a host. |
| Name token `send`, `email`, or `post` | `emit(mail)` | `review` |
| Name token `pay` or `charge` | `spend(payment)` | `review` |
| Tool field `effects` | those `kind(target)` strings, in order | `declared`. Name suggestions stay as `review` comments and are not added. |

The tool name is split on camelCase and on every run of non-letters. A token matches a word when it is that word, or that word plus one of `s`, `es`, `ed`, `ing`, `ment`, `ments`, `er`, `ers`. `payment` matches `pay`. `payload` and `chargeback` do not. `sendEmail` and `send-email` both match `send` and `email`. A renamed op gets a review comment with the original tool name.

`idempotentHint` is mentioned and not turned into an effect. The word `github` in a name does not become `net(github)`. `post_github_comment` in the OpenAI sample is only the `post` rule, so the import suggests `emit(mail)`. The hand-written triage file is the contract after a person reviews that suggestion.

JSON Schema `string`, `integer`, `number`, and `boolean` become `String`, `Int`, `Decimal`, and `Bool`. Objects and arrays become an opaque `type`. Properties that are not in `required` are still parameters, with a review comment, because Etsah parameters are required. The return type is `String` when the tool has no output schema, and the comment says it is a placeholder.

Accepted documents are one tool, a list of tools, or an object with a `tools` list. OpenAI tools may nest the function under `function` or use the flat shape. MCP schemas may be `inputSchema` or `input_schema`.

## 4. The other three checks

Invoice generation is a payment. `spend` needs a gate even when the amount is inside the budget. The budget is integer yen: 5000 for one run, 20000 for the period.

```bash
python3 etsah.py check examples/agents/invoice.etsah
python3 etsah.py check examples/agents/invoice_broken.etsah
```

The broken file's error contains `spend has no approval gate`.

Cancelling fictional plan `plan_basic` writes the subscription record. `policy contract` marks that write, and the task names a gate whose approver is `account_owner`.

```bash
python3 etsah.py check examples/agents/subscription_cancel.etsah
python3 etsah.py check examples/agents/subscription_cancel_broken.etsah
```

The broken file's error contains `a contract change has no approval gate`.

GitHub issue triage for fictional `example-org/widget` stays on `net(github)` and caps that host at 30 calls an hour. `github` is not the default allowlist, so the passing command names it. `--allow-net` replaces `stripe`; it does not add to it.

```bash
python3 etsah.py check examples/agents/github_triage.etsah --allow-net github
python3 etsah.py check examples/agents/github_triage_broken.etsah --allow-net github
```

The good file is 0 errors with that flag. The broken file still fails, with `network target 'gist' is not allowed by policy` and `net effect needs a positive hourly rate limit`.

## What the check does not do

It does not run cron, send mail, charge a card, cancel a subscription, or store idempotency keys. It does not prove that `on_approved` is the only call that performs the gated effect. The task has to name the gate, and the gate's `before` list has to include the effect.

The period printed on a task plan uses `Asia/Tokyo`. That is not a per-file setting.

The `agent` and `task` rules are in `SPEC.md`. The design notes for this layer are in `docs/ai_automation.md`.

Codes the checkers already emit are listed in [docs/diagnostics.md](../diagnostics.md).
