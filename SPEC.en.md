# PROUT v0.1 — a contract language for AI (prototype)

This is an English translation of [SPEC.md](SPEC.md). The Japanese text is the original. If the two disagree, `SPEC.md` wins. This translation tracks `SPEC.md` on main. Open pull requests may extend the Japanese spec later.

## Why a new language

As AI writes more code, the bottleneck moves from writing to checking. Existing general-purpose languages optimize for a human reader and a human author. They are not optimized for a loop in which an AI generates, an AI verifies, and a human confirms through a GUI.

PROUT is a small language that assumes this division of work:

- An **AI** writes the specification and the skeleton of the implementation in PROUT, and a different AI verifies them.
- The **checker** checks types, effects, contracts, and policy deterministically.
- A **human** does not have to read PROUT directly. They confirm a natural-language summary and a GUI.

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

### Expressions

- Variables, field access (`order.total`), and calls (`refund(order, amount)`).
- Literals: integers, strings, and booleans.
- Binary operators: `+`, `-`, `*`, `/`, `==`, `<=`, `<`, `>`, `>=`, `&&`, `||`.
- Unary `!`. In the AST this is a unary node, separate from binary operators.

Type rules for comparisons:

- `==` / `!=` apply only to compatible types (for example `Status` and a string literal). Comparing `Money` with `String` is a type error (`E-TYPE`).
- `<`, `<=`, `>`, `>=` apply only to types that have an order (`Int`, `Money`, and `String` of the same type). Mixing `Int` and `Money` is allowed.
- The operand of `!` must be `Bool`.

## Verification pipeline

1. **Parse.** A line-oriented grammar. Indentation is exactly two spaces.
2. **Type check.** Arguments, return values, fields, and operators.
3. **Effect check.** Compare an op's declared effects with the effects its implementation performs. For a flow, compare the declaration with the union of the calls.
4. **Contract check.** At each call site, substitute the actual arguments into the precondition and judge it by linear arithmetic and equality. True passes. False is an error. Unknown becomes a runtime guard.
5. **Policy check.** Only allowed network targets and effect kinds pass.
6. **Normalize.** Convert the AST to sorted JSON and attach a SHA-256 hash. Types, ops, flows, fields, and effects are sorted, and so are the conjunctions of `pre` and `post`, by an expression normalization key. Argument order and statement order are meaningful, so they are not sorted.

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

- Parser, type checker, effect checker, precondition checker, policy checker, and HTML renderer.
- Preconditions in linear arithmetic only. Contracts over strings or collections are runtime guards.
- The meaning of an implementation is not verified. `impl` is treated as a declaration.
- Proofs of concurrency, time, and resource consumption are out of scope.

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
3. A metalanguage of contracts and effects (PROUT). The implementation language stays. A checkable contract is laid on top of it.

PROUT chooses 3. `impl` may be Python, Rust, or SQL. PROUT carries only "what is guaranteed." It connects to every language. It does not contain every language.

## Implications for reverse engineering

A contract layer like PROUT can turn reverse engineering from "recover the meaning" into "check the contract."

- If AI-generated code ships with a PROUT contract, a reviewer can check that the implementation matches the contract, instead of guessing intent from a binary or from obfuscated code.
- A shared representation of effects and data flow makes comparison across languages easier. Behavior can be compared by what is read, what is written, and where it is sent, rather than by a raw list of API calls.
- Information destroyed by compilation does not come back: optimization, inlining, stripped symbols, obfuscation, packing. Recovering meaning from a binary is in general undecidable. What an analyst finds is one program that matches the inputs they observed.
- A self-asserted contract can be a lie. A contract is trustworthy only after it has been checked. That cuts both ways, for defenders and for attackers.

Reverse engineering becomes easier only when a contract exists and has been checked. A binary with no contract remains a matter of craft and tools.
