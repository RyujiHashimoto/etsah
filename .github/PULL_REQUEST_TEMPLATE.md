<!-- 説明は英語で書いてください。日本語の説明も受け付けます。 -->

## Summary

## How to verify

Paste the commands you ran and the result. The CI workflow is the baseline:

```bash
python3 -m unittest discover -s tests -v
python3 prout.py check examples/refund_fixed.prout
```

## Contract language

- [ ] No change to `prout.py`
- [ ] `SPEC.md` and `SPEC.en.md` updated in this pull request

`prout.py` is edited by open pull request #9. Contract-layer `agent` / `task` syntax is reserved for a pull request that has not been opened yet.

## Patterns

- [ ] No change to `patterns/proutc.py`
- [ ] Manifest, `index.json`, and at least two example specs
- [ ] `python3 proutc.py bench` output pasted below

`patterns/proutc.py` is edited by open pull request #10 (`ai_task`, `approval_gate`).

## If an AI wrote this patch

- Model and date
- Apps the pattern was extracted from, and why the shared part is shared
- What stayed in `custom`

Sample data uses fictional names. No personal contact details, customer data, or company-internal material.
