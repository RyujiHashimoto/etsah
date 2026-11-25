---
name: New pattern
about: Propose a pattern for the registry
title: "pattern: "
labels: enhancement
---

Patterns are JSON manifests. Add one when the same shape has already appeared twice. Read CONTRIBUTING.md before a large patch.

`ai_task` and `approval_gate` are already in the registry. Extend them in place instead of adding a second agent envelope.

## Intent

One sentence. No app-specific name.

## Why it is shared

Name two specs or apps where the shape appeared. Fictional names only. No customer or company-internal material.

## Slots

| Slot | Required | Meaning |
|---|---|---|
| | | |

## Effects

`read`, `write`, `net`, `spend`, `emit`, `notify`. Which ones, and on what target?

## What stays in `custom`

## Examples

- [ ] `patterns/registry/<name>.json`
- [ ] `python3 proutc.py index` regenerates `index.json` with no hand edits
- [ ] Two specs build
- [ ] `python3 proutc.py bench` is no worse
- [ ] Tags include English and Japanese
