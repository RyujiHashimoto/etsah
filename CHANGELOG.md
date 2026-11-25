# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/). It has not been tagged. The owner may tag the prototype as `v0.1.0`. That tag is listed as an owner decision in [docs/launch-checklist.md](docs/launch-checklist.md) and has not been created.

## [Unreleased]

### Added

- `limit calls` and `limit spend` declare a per-period call cap and a decimal spend cap, with a currency, on an agent, a task, or an op. `prout guard` counts them in a ledger file and blocks a call that would pass the cap, including one with a signed approval. A spend op names the decimal argument with `spend from`. A spend task with no decimal cap is a warning, `W-BUDGET`.
- `prout approve` issues an HMAC-SHA256 approval for one ASK call. `prout guard` forwards that call only when the signature matches the approver, contract `audit_id`, gate, tool, and argument hash, and the nonce has not been used. The shared key comes from the environment or a file and is not logged. The old non-empty token remains only behind `--allow-legacy-approval-token`.
- English README. The previous Japanese README is preserved in full as `README.ja.md`.
- The English README leads with contracts for AI agents: declare effects and approvals, and catch an unsafe automation before it runs. After merging main through #16, that page also covers `agent` / `task`, MCP, and the browser playground.
- `SPEC.en.md`, an English translation of `SPEC.md`. The Japanese spec remains the original.
- An English summary at the top of `docs/staffing.md`. The Japanese note now also describes calculated conflict dates and 36-agreement ceilings, and the English summary matches it.
- Pattern `overtime_agreement`, and a calculated 抵触日 on `dispatch_contract`. Both check spec constants only. Runtime checks are described, not executed.
- `CONTRIBUTING.md` in English, with a short Japanese section.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `CHANGELOG.md`.
- GitHub issue templates (bug, feature, new pattern) and a pull request template.
- `pyproject.toml` so a local `pip install .` exposes the `prout` and `proutc` commands. The package is not published.
- `docs/launch-checklist.md` for owner decisions before a public launch, including five good-first-issue candidates. Those issues were not opened.

### Notes

The checker, the pattern registry, the approval demo, and the staffing example were already on `main` before this changelog existed. History for those is in the commit log and in `README.ja.md`.
