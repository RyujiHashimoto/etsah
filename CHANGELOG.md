# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/). It has not been tagged. The owner may tag the prototype as `v0.1.0`. That tag is listed as an owner decision in [docs/launch-checklist.md](docs/launch-checklist.md) and has not been created.

## [Unreleased]

### Added

- English README. The previous Japanese README is preserved in full as `README.ja.md`.
- The English README leads with contracts for AI agents: declare effects and approvals, and catch an unsafe automation before it runs. Spend budgets, external approval gates, and `agent` / `task` syntax are described as in progress ([pull request #10](https://github.com/RyujiHashimoto/prout-lang/pull/10) for the pattern layer; a contract-layer pull request is still to come).
- `SPEC.en.md`, an English translation of `SPEC.md`. The Japanese spec remains the original.
- An English summary at the top of `docs/staffing.md`. The Japanese note is unchanged.
- `CONTRIBUTING.md` in English, with a short Japanese section.
- `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `CHANGELOG.md`.
- GitHub issue templates (bug, feature, new pattern) and a pull request template.
- `pyproject.toml` so a local `pip install .` exposes the `prout` and `proutc` commands. The package is not published.
- `docs/launch-checklist.md` for owner decisions before a public launch, including five good-first-issue candidates. Those issues were not opened.

### Notes

The checker, the pattern registry, the approval demo, and the staffing example were already on `main` before this changelog existed. History for those is in the commit log and in `README.ja.md`.
