# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/). It has not been tagged. The owner may tag the prototype as `v0.1.0`. That tag is listed as an owner decision in [docs/launch-checklist.md](docs/launch-checklist.md) and has not been created.

## [Unreleased]

### Fixed

- `pip install .` can run `etsahc`. The compiler imports `staffing_rules` from the installed package, and still imports it as a neighbor when `python3 etsahc.py` is run from `patterns/`.
- The pattern registry, example specs, and bench files are declared as package data, so an installed `etsahc` or `proutc` loads them from the installed copy. CI installs the package in a fresh virtualenv outside the checkout and smoke-tests `etsah`, `etsahc`, `etsah-mcp`, `prout`, `proutc`, and `prout-mcp`. The legacy commands still warn on stderr.

### Added

- A parent agent's declared `limit calls` or `limit spend` bounds the sum of the tasks that name it. Two or more child limits of the same period, and for spend the same currency, must fit. Overflow is `E-POLICY`. A different currency is not added. Phase 1 does not share a ledger or block calls in the guard. The next step is described in `docs/ai_automation.md`.
- Japanese fill-in templates for a timesheet, an assignment, and a bill rate, each with one fictional filled example. They live in `examples/templates/`. Open legal numbers are not asserted.
- E-POLICY diagnostics that CI checks now include an English `fix` line with one contract line to write. `etsah check` prints it, and `etsah check --json` adds `fix` without removing the existing fields. Diagnostic codes are unchanged.
- A composite action (`action.yml`) checks `.etsah` files changed in a pull request, prints GitHub `::error` annotations with file and line, and fails the job when the checker reports an error. `glob`, `scope`, `allow-net`, and `deny-net` are inputs. The same action also reads the legacy source extension. Tags are not published; pin `@main`. This repository's CI calls it with `uses: ./` on `examples/ai_tasks.etsah` and on `examples/ai_tasks_broken.etsah`, which fails with `E-POLICY`.
- The language name is Etsah. Commands are `etsah`, `etsahc`, and `etsah-mcp`, and source files use `.etsah`. For one minor version, `prout`, `proutc`, and `prout-mcp` still run and warn on stderr, `.prout` files still check, and the old module names re-export the new ones. The repository URL is unchanged.
- A prompt-injection pair: `examples/prompt_injection_leak.etsah` is rejected with `E-IFLOW-ARG`, and `examples/prompt_injection_safe.etsah` keeps the fictional vault token inside while the guard asks before external mail and blocks a refund above the daily JPY cap.
- `limit calls` and `limit spend` declare a per-period call cap and a decimal spend cap, with a currency, on an agent, a task, or an op. `etsah guard` counts them in a ledger file and blocks a call that would pass the cap, including one with a signed approval. A spend op names the decimal argument with `spend from`. A spend task with no decimal cap is a warning, `W-BUDGET`.
- `etsah approve` issues an HMAC-SHA256 approval for one ASK call. `etsah guard` forwards that call only when the signature matches the approver, contract `audit_id`, gate, tool, and argument hash, and the nonce has not been used. The shared key comes from the environment or a file and is not logged. The old non-empty token remains only behind `--allow-legacy-approval-token`.
- English README. The previous Japanese README is preserved in full as `README.ja.md`.
- The English README leads with contracts for AI agents: declare effects and approvals, and catch an unsafe automation before it runs. After merging main through #16, that page also covers `agent` / `task`, MCP, and the browser playground.
- `SPEC.en.md`, an English translation of `SPEC.md`. The Japanese spec remains the original.
- An English summary at the top of `docs/staffing.md`. The Japanese note now also describes calculated conflict dates and 36-agreement ceilings, and the English summary matches it.
- Pattern `overtime_agreement`, and a calculated 抵触日 on `dispatch_contract`. Both check spec constants only. Runtime checks are described, not executed.
- `CONTRIBUTING.md` in English, with a short Japanese section.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `CHANGELOG.md`.
- GitHub issue templates (bug, feature, new pattern) and a pull request template.
- `pyproject.toml` so a local `pip install .` exposes the `etsah` and `etsahc` commands. The package is not published.
- `docs/launch-checklist.md` for owner decisions before a public launch, including five good-first-issue candidates. Those issues were not opened.

### Notes

The checker, the pattern registry, the approval demo, and the staffing example were already on `main` before this changelog existed. History for those is in the commit log and in `README.ja.md`.
