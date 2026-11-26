# Etsah v0.6 — a contract language for AI

This is an English translation of [SPEC.md](SPEC.md). The Japanese text is the original. If the two disagree, `SPEC.md` wins.

## Why a new language

As AI writes more code, the bottleneck moves from writing to checking. Existing general-purpose languages optimize for a human reader and a human author. They are not optimized for a loop in which an AI generates, an AI verifies, and a human confirms through a GUI.

Etsah is a small language that assumes this division of work:

- An **AI** writes the specification and the skeleton of the implementation in Etsah, and a different AI verifies them.
- The **checker** checks types, effects, contracts, and policy deterministically.
- A **human** does not have to read Etsah directly. They confirm a natural-language summary and a GUI.

## Design principles

1. **One canonical form.** Declaration order, field order, effect order, and the order of preconditions and postconditions are normalized, so programs with the same structure have the same AST and the same hash. The checker does not rewrite logically equivalent expressions (commutativity, De Morgan, and so on). Diffs and audits stay straightforward.
2. **Effects are explicit.** File reads and writes, network calls, charges, and emitted events cannot run unless they are declared. A mismatch between the declaration and the actual effects is an error.
3. **Contracts are part of the code.** Preconditions and postconditions are expressions, checked at each call site. A condition the checker cannot prove is shown to the human as a runtime guard.
4. **Diagnostics are repairable.** An error returns a code, a location, the expected value, the actual value, and a suggested fix, in a structured form. An AI can feed that straight into a repair loop.
5. **The human view is a derivative.** Both the natural-language summary and the GUI are generated from the AST. The view is not a second source of truth. The view carries the AST hash.

## Syntax

### Types

```
type Order {
  id: Id
  total: Money
  status: Status
}

type Money
type Id
type Status
```

`type Name` is an opaque type. It has no fields.

### Operations (`op`)

```
op refund(order: Order, amount: Money) -> Receipt
  doc "注文を指定額だけ返金する"
  eff read(orders), write(payments), net(stripe)
  pre amount <= order.total
  pre order.status == "paid"
  post result.status == "refunded"
  impl stripe.refund(order, amount)
```

- `doc` is a short description for humans. The GUI uses it as a label.
- `eff` declares effects. There are five kinds: `read`, `write`, `net`, `spend`, `emit`.
- `pre` is a condition the caller must meet. `post` is a condition the implementation guarantees.
- `impl` is the implementation: a call to another op, or `native` (an external implementation).

### Flows (`flow`)

```
flow handle_refund(order_id: Id) -> Receipt
  doc "返金リクエストを処理する"
  eff read(orders), write(payments), net(stripe)
  let order = orders.get(order_id)
  let receipt = refund(order, order.total)
  return receipt
```

A flow's effects must equal the union of the effects of the ops it calls. That is how "what this step touches" is known statically.

A flow must end with `return`. A flow with no `return` is an error (`E-FLOW`). Statements written after `return` are unreachable and are also an error.

A flow may branch with `if` / `else`. The condition is a `Bool`, and the body is indented two more spaces. `else` may be omitted.

```
flow classify(id: Id) -> String
  eff read(customers)
  let c = customers.get(id)
  if c.email == "blocked@example.com"
    return "blocked"
  else
    return "allowed"
```

A variable bound with `let` is scoped to its branch. If both branches return, any statement after the `if` is unreachable. If one branch falls through, a later `return` is needed for the flow as a whole to return. Effects are the union of the condition and both branches.

`if` is available only inside a flow. A task body has only `let` and `return`.

### Agents and tasks

Work given to an AI is two layers: an `agent` (the envelope of effects it may use) and a `task` (one job inside that envelope). A `gate` holds an external effect until a person allows it. `policy` names which effects are external, a payment, or a contract change. Indentation is two spaces, as for every other declaration. Budget numbers are integer yen.

```
agent staffing_mail
  doc "承認済みの文面だけを社外へ送る"
  allow read(timesheets), emit(mail)
  budget run 0 period 0
  limit net 0 hour
  limit emit 20 hour
  audit ast_hash

policy
  external emit(mail), net(stripe)
  payment spend(payroll)
  contract write(contracts)

gate remind_client
  doc "督促メールはコーディネーターの承認後だけ送る"
  approver coordinator
  before emit(mail)
  on_approved mail.send

task remind_timesheet(period: Id) -> Bool
  agent staffing_mail
  eff read(timesheets), emit(mail)
  gate remind_client
  key period
  retry 2 escalate coordinator
  let sheet = timesheets.open(period)
  let sent = mail.send(sheet)
  return sent
```

Triggers:

- `trigger schedule "0 9 * * 1"` is five cron fields: minute, hour, day, month, weekday. `*`, `a-b`, `*/n`, and comma lists are accepted. Names such as `MON` are not.
- `trigger record Invoice` fires only for a new row of that type, not for an update.
- `trigger webhook "inbound/inquiry"` records the endpoint name. Signatures are not checked.

The period boundary is `Asia/Tokyo`. There is no syntax to change it per task.

The checker does not run cron, does not store the remaining integer-yen budget, and does not store an idempotency key. `etsah guard` does store call counts and decimal spend. The static rules are:

1. A task's `eff` equals the union of the effects of the ops it calls (`E-EFF`). That union must be a subset of the agent's `allow`. An effect outside the allow list is `E-POLICY`.
2. An `emit` or `net` listed in `policy external`, every `spend`, and a `write` listed in `policy contract` need a `gate` whose `before` names that effect. Otherwise `E-POLICY`. An internal `emit` (one not listed as external) needs no gate. Whether a company-wide Slack notice is external is decided by whether that target is written in `policy external`.
3. An agent allowed to `spend` has `budget run` and `budget period` as integers of at least 1 yen, and the per-run budget is at most the period budget. An agent that may not `spend` has budget 0. A task's own `budget` follows the same rule and may not exceed the agent's budget.
4. `limit emit` and `limit net` are at least 1 when that effect is allowed, and 0 when it is not. The unit is one hour. These are static caps. The checker does not count them down.
5. `limit calls N period hour|day|month` and `limit spend AMOUNT CURRENCY period hour|day|month` may be written on an `agent`, a `task`, or an `op`. The count is a positive integer. The amount is a positive decimal. The currency is three uppercase letters. Period boundaries use `Asia/Tokyo`. A `spend` op names the amount with `spend from argument[.field] currency CURRENCY`, and that type must be `Decimal`. A task limit above the agent's limit for the same period and currency is `E-POLICY`. When two or more tasks name the same agent, the sum of their `limit calls` for the parent's period, and the sum of their `limit spend` for the parent's period and currency, must also fit in the agent's declared limit. Overflow is `E-POLICY`. In phase 1 that declared limit is the parent's whole remaining budget. The checker does not subtract usage. A child amount in another currency is not added; the currency mismatch is `E-POLICY`. A child on another period is not added. A child with no limit is not counted as zero. A task that `spend`s without a decimal cap on the task, the agent, or that op is a warning (`W-BUDGET`), not an error. A missing integer-yen `budget` is still `E-POLICY`.
6. `retry` is from 0 through 5. After that, the task escalates to the human role in `escalate`. That handoff is not part of the agent's `allow`. If escalation points at an op or an agent whose effect needs a gate, that is `E-POLICY`.
7. `key` is a parameter or a field of a parameter. Idempotency identity is agent, task, and key. Changing the program hash is not a reason to repeat an external effect. The store itself does not exist yet.
8. `audit ast_hash` is required on an agent. The audit identity is the full 64-hex SHA-256 of the normalized AST (`ast_digest`). The displayed `ast_hash` remains the first 12 hex digits.
9. An approver is a human role. An agent's name cannot be an `approver`. The check does not yet prove that `on_approved` is the only call that performs the external effect.

Whether a `net` target is on the allow list (default `stripe`) is still the op policy check. `policy external` decides whether that target also needs a gate.

The information-flow `policy` (`sink` and `declassify`) and the task `policy` (`external`, `payment`, `contract`) share a declaration name and are distinguished by the lines inside. One block cannot hold both. A program may contain one information-flow `policy` and one task `policy`.

When a program has a task, `check` prints the trigger, effects, gate, retry, key, and budget in prose. The exit code is 0 when there is no error (warnings still exit 0), 1 when there is at least one error, and 2 when the file does not parse.

`check --json` prints one object. `hash` (the first 12 hex digits of the normalized AST's SHA-256) and `diagnostics` are always present. Each diagnostic has `code`, `severity`, `line`, and `message`, plus `fix`, `detail`, and `context` only when set. When the program has tasks, the object also has `audit_id` (64 hex digits) and `plan` (human-readable lines). `manifest` carries `hash`, `audit_id` when there are tasks, and each agent's `allow` list, integer-yen `budget_run` and `budget_period`, `limit_net` and `limit_emit`, `call_limit` and `spend_limit` when declared, and the gate names its tasks name. Agents, `allow` entries, and gate names are in name order, so the same source yields the same bytes. `signature` is always `null`. The field does not sign anything and does not read a key.

### Expressions

- Variables, field access (`order.total`), and calls (`refund(order, amount)`).
- Literals: integers, decimals, dates, strings, and booleans.
- Binary operators: `+`, `-`, `*`, `/`, `==`, `<=`, `<`, `>`, `>=`, `&&`, `||`.
- Unary `!`. In the AST this is a unary node, separate from binary operators.

Built-in types are `Int`, `Decimal`, `Date`, `String`, and `Bool`. `Money` stays an opaque type, handled by the user's declarations and by the rules for mixing it with `Int`.

Literal forms:

- A decimal has digits on both sides of the point, as in `1800.50`. The meaning is `decimal.Decimal`, not a binary float. `1800.50` and `1800.5` are equal. `1.`, `.5`, and `1e2` are not literals.
- A date is `2026-11-23` with no spaces (zero-padded Gregorian). An impossible day such as `2026-02-31` is a syntax error. `2026 - 11 - 23` with spaces is still integer subtraction.
- `Int` and `Decimal` may be mixed. If either side is `Decimal`, the result is `Decimal`. `+`, `-`, `*`, and `/` of two `Int`s stay `Int`. `1 / 2` is not constant-folded. Decimal division is not linear either, so in a precondition it is a runtime guard.
- `Date + Int` and `Int + Date` add days and produce a `Date`. `Date - Int` is a `Date`. `Date - Date` is an `Int` number of days. Arithmetic or comparison of `Date` with `Decimal` is a type error. Adding two `Date`s is a type error.

Type rules for comparisons:

- `==` / `!=` apply only to compatible types (for example `Status` and a string literal, or `Int` and `Decimal`). Comparing `Money` with `String` is a type error (`E-TYPE`).
- `<`, `<=`, `>`, `>=` apply only to types that have an order (`Int`, `Decimal`, `Money`, `Date`, and `String` of the same type). Mixing `Int` with `Decimal`, and `Int` with `Money`, is allowed. A `Date` compares only with a `Date`.
- The operand of `!` must be `Bool`.

Contract checking rewrites integer, decimal, and date-difference expressions into linear (affine) form and compares constants. A date is a Gregorian day count internally. The origin cancels in a difference or a comparison. An expression that still contains a variable, or that contains division, is not proved. It is reported as a runtime guard. It is not silently accepted.

## Verification pipeline

1. **Parse.** A line-oriented grammar. Indentation is exactly two spaces.
2. **Type check.** Arguments, return values, fields, and operators.
3. **Effect check.** Compare an op's declared effects with the effects its implementation performs. For a flow or a task, compare the declaration with the union of the calls.
4. **Contract check.** At each call site, substitute the actual arguments into the precondition and judge it by linear arithmetic and equality. True passes. False is an error. Unknown becomes a runtime guard.
5. **Information-flow check.** Only when the program declares labels, annotations, or an information-flow `policy`: arguments, return values, contracts, sinks, `sends`, and `declassify`. A task `policy` alone does not start this check.
6. **Policy check.** Only allowed network targets and effect kinds pass. AI tasks are also stopped here for the envelope, gates, integer-yen budgets, hourly limits, and retries.
7. **Normalize.** Convert the AST to sorted JSON and attach a SHA-256 hash. Types, ops, flows, fields, and effects are sorted, and so are the conjunctions of `pre` and `post`, by an expression normalization key. Labels, an information-flow `policy`, agents, tasks, gates, and task policies enter the hash only when present. A program without them keeps its previous hash. Argument order and statement order are meaningful, so they are not sorted.

## Human view

From `doc` and the AST, the renderer generates:

- A Japanese summary (for example, "fetch the order, refund the full amount, and return the receipt").
- A card per step: inputs, outputs, effects, and contract status.
- Badges for effects and policy.
- Verification history: problems found in the first draft, and the repairs.
- The formal source, collapsed.
- The AST hash.

The view can always be regenerated from the AST. Editing the view alone is not possible. An operation in the GUI translates into the corresponding AST change.

## Scope of the prototype

- Parser, type checker, effect checker, precondition checker, policy checker, and HTML renderer. Static checks for AI tasks: envelope, gates, integer-yen budgets, rate limits, retries, and the shape of an idempotency key.
- Information flow checks a static set of labels only. Implicit flow, labels on collection elements, label polymorphism, and runtime enforcement are out of scope.
- Preconditions in linear arithmetic only. Contracts over strings or collections are runtime guards. Decimal values and calendar-day differences are proved only when they reduce to constants.
- The meaning of an implementation is not verified. `impl` is treated as a declaration.
- Proofs of concurrency, time of day, and resource consumption are out of scope. A calendar `Date` is in scope. A clock time is not. Running cron, storing remaining budget, and an idempotency store are out of scope.

This is not a complete implementation of the language. It is a prototype of whether the loop "an AI generates, an AI verifies, a human sees a GUI" can hold.

## Why this is not a superset of every language

A strict superset is close to impossible.

- Semantics collide. `1/2` is 0 in C, Java, and Rust, and 0.5 in Python 3. Integer overflow wraps in Java, panics in a Rust debug build, and is arbitrary-precision in Python. One expression cannot be given one meaning.
- Syntax collides. `<>` is generics in Java and C#, and a comparison in Python. `{}` is a block, an object, or a set. Significant indentation versus free form. Comment syntax differs too.
- Macros and metaprogramming. Lisp macros, the C preprocessor, Rust procedural macros, C++ templates. Once syntax extension is allowed, parsing the language is in general undecidable.
- Execution environments. The JVM, BEAM, V8, CPython, .NET, OS APIs. A language is not separable from its runtime. Replacing all of them with one language means building a VM and an OS.
- Undefined behavior. UB in C and C++ is a state whose meaning is not defined. A superset that preserves UB is not safe. A superset that makes it safe does not preserve the original meaning.

Three other roads are available.

1. A common intermediate representation (LLVM IR, MLIR, WebAssembly, GraalVM). Meaning can be kept, but this is a platform, not a superset of the source language.
2. A host language that embeds other languages (Racket `#lang`, Scala string interpolation, MPS). Meaning is delegated at each boundary.
3. A metalanguage of contracts and effects (Etsah). The implementation language stays. A checkable contract is laid on top of it.

Etsah chooses 3. `impl` may be Python, Rust, or SQL. Etsah carries only "what is guaranteed." It connects to every language. It does not contain every language.

## Implications for reverse engineering

A contract layer like Etsah can turn reverse engineering from "recover the meaning" into "check the contract."

- If AI-generated code ships with an Etsah contract, a reviewer can check that the implementation matches the contract, instead of guessing intent from a binary or from obfuscated code.
- A shared representation of effects and data flow makes comparison across languages easier. Behavior can be compared by what is read, what is written, and where it is sent, rather than by a raw list of API calls.
- Information destroyed by compilation does not come back: optimization, inlining, stripped symbols, obfuscation, packing. Recovering meaning from a binary is in general undecidable. What an analyst finds is one program that matches the inputs they observed.
- A self-asserted contract can be a lie. A contract is trustworthy only after it has been checked. That cuts both ways, for defenders and for attackers.

Reverse engineering becomes easier only when a contract exists and has been checked. A binary with no contract remains a matter of craft and tools.

## Information-flow control

Attach `pii` or `secret` to a type and reject a flow into a sink that does not allow that label. The design note, including what is not implemented, is [docs/information-flow.md](docs/information-flow.md). This section is the rules that are implemented.

### Declaring and annotating labels

```
label pii
label secret

type Customer {
  id: Id
  email: String @pii
  internal_note: String @secret
}
```

`public` is implicit. It cannot be declared or annotated. A label name matches `[a-z][a-z0-9_]*` and must not collide with an effect kind (`read`, `write`, `net`, `spend`, `emit`). Annotations go on opaque types and on field types. They cannot go on a record type itself or inside a type argument (`Box<String @pii>`). Those are `E-LABEL`.

A label set is part of the type. `String @pii` and `String` are different types, but assigning `String` to `String @pii` is allowed (public data may flow into a secret). The other direction is `E-IFLOW-ARG` or `E-IFLOW-RET`.

### Inferring labels on expressions

| Expression | Label |
|---|---|
| Literal, or a variable with no label | empty (public) |
| Field access `c.email` | that field's label only |
| Record value `Customer { ... }` | union of every field's labels |
| Binary `a + b` | union of both sides |
| Comparison `a == b` | union of both sides (the `Bool` result still carries the label) |
| Call | the declared return label |
| `declassify(e, l, reason)` | `e`'s labels with `l` removed |

A variable that holds a whole record carries every field's labels. Reading one field keeps only that field's label. A customer record can contain PII while `id` stays public.

### Sinks and `sends`

An operation that touches the outside declares which argument flows to which effect.

```
policy
  sink net(email) accepts pii
  sink net(analytics) accepts pii
  sink net(*) rejects secret

op send_receipt(to: String @pii) -> Bool
  eff net(email)
  sends to -> net(email)
  impl native
```

A `sink` names, per effect target, labels it `accepts` and labels it `rejects`. `net(*)` is a wildcard. A more specific `net(email)` replaces `accepts`. `rejects` is the union of the specific rule and the wildcard, and cannot be overwritten. `read` cannot be a sink (`E-POLICY`).

`sends` may be written only on an `impl native` op. A native op with a labeled argument or return, or with a `read` effect, must have a `sends` for every effect other than `read`. A missing one is `E-SINK-UNBOUND`. An unknown argument or effect is `E-SINK`. `sends none -> net(health)` says that effect receives no labeled data.

### `declassify`

A label may be removed only where policy allows the place and the role.

```
policy
  declassify pii in masked_tracking require role support

flow masked_tracking(id: Id) -> Bool
  eff read(customers), net(analytics)
  let c = customers.get(id)
  let masked = declassify(c.email, pii, "分析用にドメインだけ残す")
  return track_event(masked)
```

The reason is 1 to 200 characters and contains no newline. An allowed `declassify` stays in the audit log as `W-DECLASS`. A place, role, or label outside policy is `E-DECLASS`. Declassifying a whole record, or using `declassify` inside `pre` or `post`, is rejected.

### Branches and implicit flow

When an `if` condition carries a label, values produced inside that branch carry the same label. This is the program-counter (PC) label. Nested conditions add their labels to it.

```
flow classify(id: Id) -> String
  eff read(customers)
  let c = customers.get(id)
  if c.email == "blocked@example.com"
    return "blocked"
  else
    return "allowed"
```

The returned strings are public, but which one is returned reveals the e-mail address. A `return` is checked as `L(expr) ∪ PC ⊆ L(declared return)`, so both returns are `E-IFLOW-RET`. A `let` inside the branch also carries the PC label, so `let answer = "yes"` followed by `return answer` is caught the same way.

When revealing the condition is intended, declassify the condition itself.

```
let blocked = declassify(c.email == "blocked@example.com", pii, "公開してよい判定結果")
if blocked
  return "blocked"
```

The PC label is now empty and only `W-DECLASS` remains. A branch-local variable does not escape the branch. With no `else`, execution continues after the branch.

### Checks and diagnostics

The order is arguments (`E-IFLOW-ARG`), return values (`E-IFLOW-RET`), contract expressions (`E-IFLOW-CONTRACT`), sinks (`E-IFLOW-SINK`), `sends` (`E-SINK`, `E-SINK-UNBOUND`), policy (`E-POLICY`), and `declassify` (`E-DECLASS`, `W-DECLASS`). Diagnostics carry a line and a suggested fix. `--json` puts "which value flowed into which argument" in `context`.

A program with no label declaration, no annotation, and no information-flow policy skips the information-flow check entirely. Existing ASTs and hashes stay the same. `examples/refund_fixed.etsah` remains `bae8501e8b3f`.

### Limits

Branches exist only as early `return`s. There is no assignment, loop, or exception, so a flow that updates a variable and joins after the branch cannot be written. Labeled collections, label polymorphism, runtime enforcement, and authenticating the caller of `declassify` are not implemented. The body of `impl native` is trusted. The check stops a flow that crosses a declared boundary. It does not prove noninterference.
