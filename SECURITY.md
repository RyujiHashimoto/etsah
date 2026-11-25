# Security policy

Etsah is a local prototype: a contract checker and a pattern compiler. It does not run a hosted agent, and it does not hold customer data.

## Reporting

Prefer a private report through GitHub:

<https://github.com/RyujiHashimoto/prout-lang/security/advisories/new>

The owner has to enable private vulnerability reporting before that page accepts reports. This repository's settings were not changed by the documentation pull request. Until private reporting is enabled, open a [GitHub Issue](https://github.com/RyujiHashimoto/prout-lang/issues) that names the component and the impact. Leave exploit detail, payloads, and proof-of-concept steps out of the public issue.

`oss@j-prout.co.jp` is a placeholder for the owner to confirm. It is not a monitored security contact until the owner says so.

There is no bug bounty.

## What to include

- The commit or version you ran, and the command (`etsah check`, `etsahc build`, or a test).
- Which component is affected: the contract checker (`etsah.py`), the pattern compiler (`patterns/etsahc.py`), or generated HTML.
- The impact: for example, a check that accepts a spec the design says must fail, or a demo page that treats untrusted spec text as HTML in an unexpected way.
- Whether the behavior is on `main` or only on an open pull request.

## Scope

In scope: this repository's checker, pattern compiler, and the HTML they generate.

Out of scope: applications people build with Etsah, third-party services named in sample specs (they are fictional stand-ins), and vulnerabilities in dependencies. The prototype depends only on the Python standard library.

## Supported versions

No release is tagged yet. Security fixes apply to the `main` branch. A future `v0.1.0` tag is an owner decision, listed in [docs/launch-checklist.md](docs/launch-checklist.md).

| Version | Status |
|---|---|
| `main` | Prototype. Reports welcome |
| tagged releases | None yet |
