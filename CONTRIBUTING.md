# Contributing to PROUT

PROUT is a prototype of a language in which an AI writes, an AI verifies, and a human reads. The useful contributions are a **new pattern in the registry** and a **smaller, more general form of a pattern that already exists**.

Questions go to [GitHub Issues](https://github.com/RyujiHashimoto/prout-lang/issues). Vulnerability reports follow [SECURITY.md](SECURITY.md). Conduct reports follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

`oss@j-prout.co.jp` is a placeholder for the owner to confirm. It is not a monitored contact until the owner says so. Use GitHub Issues until then.

The contract spec's original is [SPEC.md](SPEC.md) (Japanese). [SPEC.en.md](SPEC.en.md) is a translation. If they disagree, `SPEC.md` wins. Update `SPEC.md` in the same pull request when a change alters syntax, types, effects, or preconditions, and update the translation in the same pull request.

## Coordination

Two language pull requests are open, and a contract-layer pull request for `agent` / `task` is still to come.

- [#9](https://github.com/RyujiHashimoto/prout-lang/pull/9) edits `prout.py` for `Date` and `Decimal`.
- [#10](https://github.com/RyujiHashimoto/prout-lang/pull/10) edits `patterns/proutc.py` for the agent-task pattern layer (`ai_task`, `approval_gate`).
- Contract-layer `agent` / `task` syntax is reserved for the owner's upcoming pull request. `prout.py` on main does not parse it.

A docs or pattern pull request should leave `prout.py` and `patterns/proutc.py` alone unless the change is the point of that pull request and it is rebased onto the open work. Parallel edits to those files will conflict.

## Development

```bash
git clone https://github.com/RyujiHashimoto/prout-lang.git
cd prout-lang

python3 prout.py check examples/refund_fixed.prout
python3 render.py

python3 -m unittest discover -s tests -v

cd patterns
python3 proutc.py index
python3 proutc.py build examples/shop.prout
python3 proutc.py build examples/expense.prout
python3 proutc.py build bench/pc_lending.prout
python3 proutc.py build examples/staffing.prout
python3 proutc.py build examples/staffing_desk.prout
python3 proutc.py bench
python3 growth_report.py
```

CI runs these commands. A green local run is usually a green CI run. Python 3.12, standard library only.

`pip install .` exposes `prout` and `proutc` console scripts. See the README. The package is not published. Do not upload it to PyPI from a contribution.

Optional local install, from the repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install .
```

## Proposing a pattern

A pattern is a JSON manifest, not code. Add `patterns/registry/<name>.json`, regenerate `index.json`, and open a pull request.

```bash
cd patterns
cp registry/approval.json registry/my_pattern.json
# Rewrite name, intent, tags, slots, and artifacts.
python3 proutc.py index
python3 proutc.py find "what you want"
python3 proutc.py build examples/your_app.prout
```

Use fictional names in sample specs. Do not include personal contact details, customer data, or material from inside a company.

| Field | What it holds |
|---|---|
| `name` | Pattern name. Lowercase English and underscores |
| `version` | Semantic version |
| `intent` | One sentence: when to use it. This is the search key |
| `tags` | Both English and Japanese, so an agent's query can hit |
| `slots` | Required and optional slots. `desc` is what the agent reads |
| `effects` | One of `read`, `write`, `net`, `spend`, `emit`, `notify` |
| `artifacts` | Path, kind, estimated lines, description |
| `examples` | Spec files that actually build |
| `provenance` | Who extracted it, when, and from which app |

### Review bar

Add a pattern only when the same shape has appeared more than once. A one-off stays in `custom`. Review checks:

1. `intent` is one sentence and contains no app-specific name.
2. Required slots are minimal and do not demand an app-specific value.
3. `effects` match the artifacts that are actually produced.
4. Expansion is deterministic: the same input yields the same output.
5. At least two examples exist (one real app and one synthetic spec is enough).
6. `python3 proutc.py bench` does not get worse.
7. A pattern with state transitions defines `states` and `transitions`. `on_approved` points at a `custom` block or at a pattern applied in the same spec. A hook that points at a pattern has arguments that match that pattern's slots and `auth.roles`.

### Versions

- `patch` — wording, or an updated line estimate.
- `minor` — an optional slot or an added artifact. Existing specs still build.
- `major` — a new required slot, or a change to `effects`. Existing specs break.

## Pull requests written by an AI

Human review is required when an AI wrote the patch. The description includes:

- The model, and when the patch was generated.
- Pasted output of `proutc.py build` and `bench`.
- The apps the pattern was extracted from, and why the shared part is shared.
- What could not be generalized and stayed in `custom`.

## Changes to the contract language

If a change alters the meaning of syntax, types, effects, or preconditions in `prout.py`, update `SPEC.md` and `SPEC.en.md` in the same pull request. A change that makes existing `.prout` files fail is a `major` change.

## 日本語

手順、レビュー基準、版の上げ方は英語の本文が正です。

- テストは `python3 -m unittest discover -s tests -v`。依存パッケージはありません。
- 新しいパターンは `patterns/registry/<name>.json` を足し、`python3 proutc.py index` で索引を更新して Pull Request にします。同じ形が二度以上出てから追加します。
- AI が書いた Pull Request も、人間のレビューが必須です。
- 質問は [GitHub Issues](https://github.com/RyujiHashimoto/prout-lang/issues)。脆弱性は [SECURITY.md](SECURITY.md)。
- `oss@j-prout.co.jp` はオーナー確認前の仮アドレスです。確認されるまでは Issues を使ってください。
- `prout.py` と `patterns/proutc.py` は、進行中の Pull Request（#9、#10）と、これから来る契約層の `agent` / `task` と衝突しやすいです。ドキュメントだけの変更では触らないでください。

## License

Contributions are accepted under the MIT License in [LICENSE](LICENSE). By submitting a contribution, you agree that it is licensed under MIT.

Project copyright line for new documents: Ryuji Hashimoto (J-PROUT Inc.). The `LICENSE` file keeps its existing copyright line.
