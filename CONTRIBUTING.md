# Contributing to Etsah

Etsah is a prototype of a language in which an AI writes, an AI verifies, and a human reads. The useful contributions are a **new pattern in the registry** and a **smaller, more general form of a pattern that already exists**.

Questions go to [GitHub Issues](https://github.com/RyujiHashimoto/etsah/issues). Vulnerability reports follow [SECURITY.md](SECURITY.md). Conduct reports follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

`oss@j-prout.co.jp` is a placeholder for the owner to confirm. It is not a monitored contact until the owner says so. Use GitHub Issues until then.

The contract spec's original is [SPEC.md](SPEC.md) (Japanese). [SPEC.en.md](SPEC.en.md) is a translation. If they disagree, `SPEC.md` wins. Update `SPEC.md` in the same pull request when a change alters syntax, types, effects, or preconditions, and update the translation in the same pull request.

## Coordination

`Date`, `Decimal`, `agent` / `task`, information-flow labels, the MCP server, and the browser playground are on `main`. Change the checker in a pull request that also updates `SPEC.md` and `SPEC.en.md`. Do not restyle `etsah.py` in a docs-only pull request.

## Development

```bash
git clone https://github.com/RyujiHashimoto/etsah.git
cd etsah

python3 etsah.py check examples/refund_fixed.etsah
python3 etsah.py check examples/ai_tasks.etsah
python3 render.py

python3 -m unittest discover -s tests -v

cd patterns
python3 etsahc.py index
python3 etsahc.py build examples/shop.etsah
python3 etsahc.py build examples/expense.etsah
python3 etsahc.py build bench/pc_lending.etsah
python3 etsahc.py build examples/staffing.etsah
python3 etsahc.py build examples/staffing_desk.etsah
python3 etsahc.py build examples/ai_automation.etsah
python3 etsahc.py build examples/ai_desk.etsah
python3 etsahc.py bench
python3 growth_report.py
```

CI runs these commands. A green local run is usually a green CI run. Python 3.12, standard library only.

`tests/` is not a package, so `python3 -m unittest tests.test_checker.TestCli` fails to import `support`. One class:

```bash
PYTHONPATH=tests python3 -m unittest test_checker.TestCli
```

CI runs on `ubuntu-latest` only. The Windows lines below set `PYTHONPATH` and then run the same class. CI has not run them.

Command Prompt:

```cmd
set PYTHONPATH=tests
python -m unittest test_checker.TestCli
```

PowerShell:

```powershell
$env:PYTHONPATH="tests"
python -m unittest test_checker.TestCli
```

`pip install .` exposes the `etsah`, `etsahc`, and `etsah-mcp` console scripts. See the README. The package is not published. Do not upload it to PyPI from a contribution.

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
python3 etsahc.py index
python3 etsahc.py find "what you want"
python3 etsahc.py build examples/your_app.etsah
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
6. `python3 etsahc.py bench` does not get worse.
7. A pattern with state transitions defines `states` and `transitions`. `on_approved` points at a `custom` block or at a pattern applied in the same spec. A hook that points at a pattern has arguments that match that pattern's slots and `auth.roles`.

### Versions

- `patch` — wording, or an updated line estimate.
- `minor` — an optional slot or an added artifact. Existing specs still build.
- `major` — a new required slot, or a change to `effects`. Existing specs break.

## Pull requests written by an AI

Human review is required when an AI wrote the patch. The description includes:

- The model, and when the patch was generated.
- Pasted output of `etsahc.py build` and `bench`.
- The apps the pattern was extracted from, and why the shared part is shared.
- What could not be generalized and stayed in `custom`.

## Changes to the contract language

If a change alters the meaning of syntax, types, effects, or preconditions in `etsah.py`, update `SPEC.md` and `SPEC.en.md` in the same pull request. A change that makes existing `.etsah` files fail is a `major` change.

## 日本語

手順、レビュー基準、版の上げ方は英語の本文が正です。

- テストは `python3 -m unittest discover -s tests -v`。依存パッケージはありません。
- クラス一つは `PYTHONPATH=tests python3 -m unittest test_checker.TestCli`。`tests.` で始めると `support` が見つかりません。
- 新しいパターンは `patterns/registry/<name>.json` を足し、`python3 etsahc.py index` で索引を更新して Pull Request にします。同じ形が二度以上出てから追加します。
- AI が書いた Pull Request も、人間のレビューが必須です。
- 質問は [GitHub Issues](https://github.com/RyujiHashimoto/etsah/issues)。脆弱性は [SECURITY.md](SECURITY.md)。
- `oss@j-prout.co.jp` はオーナー確認前の仮アドレスです。確認されるまでは Issues を使ってください。
- 検査器の意味を変えるときは `SPEC.md` と `SPEC.en.md` を同じ Pull Request で更新してください。ドキュメントだけの変更で `etsah.py` を整形し直さないでください。

## License

Contributions are accepted under the MIT License in [LICENSE](LICENSE). By submitting a contribution, you agree that it is licensed under MIT.

Project copyright line for new documents: Ryuji Hashimoto (J-PROUT Inc.). The `LICENSE` file keeps its existing copyright line.
