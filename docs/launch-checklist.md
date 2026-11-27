# 公開前チェックリスト（オーナー判断）

この文書は、リポジトリを世界向けに出しやすくしたあとに、**オーナーが自分で決めて実行するもの**の一覧です。この Pull Request では、設定変更、リリース、Issue 作成、PyPI への公開、告知の投稿はしていません。

英語 README の先頭は、次の一文です。

> Contracts for AI agents: declare effects and approvals, catch unsafe automations before they run.

#13 から #16 が main に入ったあと、このブランチへ取り込みました。効果の一致、通信先の許可リスト、事前条件、AST ハッシュ、`agent` / `task` の人間ゲート、整数円の予算、MCP、ブラウザの playground はツリーにあります。cron の実行、予算の残数、冪等ストアはありません。人材派遣は産業の一例です。告知は、動く検査と、まだ実行しないものとを分けて書いてください。

サンプルや告知に、個人の電話番号・住所・顧客情報・社内情報は入れないでください。作者名が要る箇所は `Ryuji Hashimoto (J-PROUT Inc.)` に揃えています。`LICENSE` の既存の著作権表示は変えていません。

## 1. 英語のリポジトリ説明と topics

現状の説明は日本語です。topics はすでに `agents`, `ai`, `code-generation`, `dsl`, `patterns`, `programming-language` が付いています。変更はしていません。

提案する説明（About）:

```
Contracts for AI agents: declare effects and approvals, catch unsafe automations before they run.
```

topics は現状を残し、`static-analysis` を足すかどうかを決めてください。`formal-verification` は付けない方がよいです。線形算術の事前条件までで、定理証明器ではありません。

ホームページ欄は空です。GitHub Pages を有効にしたあとで、デモの URL を入れるかどうかを決めてください。

## 2. PyPI への公開

公開していません。`pyproject.toml` の配布名は `prout-lang`、コマンド名は `etsah`、`etsahc`、`etsah-mcp` です。一つマイナー版のあいだ、`prout`、`proutc`、`prout-mcp` も動きます。依存は標準ライブラリだけです。

2026-11-23 に `https://pypi.org/pypi/prout-lang/json` と `https://pypi.org/pypi/prout/json` を GET し、どちらも 404 でした。公開直前にもう一度確認してください。名前は先に取られることがあります。

公開するなら、オーナーのマシンから `twine` で行ってください。このリポジトリにアップロード用のトークンやワークフローは置いていません。版は `0.1.0` です。タグを打つ前にアップロードすると、タグと版がずれます。

## 3. GitHub Pages で demo.html

Pages は無効のままです。有効にしていません。

`patterns/build/` は gitignore されているので、生成した `demo.html` はコミットされていません。出すなら、次のどちらかです。

- Actions で `python3 patterns/etsahc.py build patterns/examples/expense.etsah --out patterns/build/expense` を実行し、`demo.html` を Pages に上げる。
- 生成物を一度だけコミットする。生成のたびに差分が出るので、Actions の方が扱いやすいです。

`demo.html` はサーバなしで開く HTML です。経費精算の承認デモが、今のツリーで人間のゲートを見せる例です。人材派遣のビルドは `demo.html` を出しません。

`human-view.html` はリポジトリに含まれています。Pages の入口をデモにするか、人間向けビューにするかも決めてください。

## 4. Discussions を有効にする

2026-11-23 時点で Discussions は無効です。有効化は設定変更なので、ここでは行っていません。有効にしたあとの入口は `https://github.com/RyujiHashimoto/etsah/discussions` です。質問の既定は、それまで GitHub Issues のままにしています。

## 5. リリースタグ v0.1.0

タグも GitHub Release も作っていません。`CHANGELOG.md` は Unreleased のままです。

打つなら、この文書の公開判断が終わったあとの `main` に `v0.1.0` を打ってください。中身は試作です。リリースノートには、効果と承認の検査が動くこと、支出上限と `agent` / `task` は未搭載であること、生成物は見積もりとデモで実ソースではないこと、を書いてください。

## 6. 連絡先メールと非公開の脆弱性報告

`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`SECURITY.md` に、仮のアドレス `oss@j-prout.co.jp` を書いてあります。**オーナーがこのアドレスを使うと確認するまで、監視されている連絡先ではありません。** 確認しないなら、三つの文書から削除してください。

非公開の脆弱性報告（Settings → Advanced Security → Private vulnerability reporting）は有効にしていません。有効にするまで、`SECURITY.md` の advisory URL は受け付けません。有効にしたあと、公開 Issue に再現手順やペイロードを書かない、という方針のままにしてください。

## 7. 告知の下書き

投稿していません。各コミュニティのルールを確認してから、オーナーが投稿してください。下書きにメールアドレスは入れていません。`agent` / `task` が完成したとは書いていません。

### Show HN

Title:

```
Show HN: Etsah – contracts that catch unsafe AI-agent actions before they run
```

Text:

```
Etsah is a prototype language for putting a checked envelope around an AI agent before it runs.

The agent declares effects (read, write, net, spend, emit). A separate checker rejects a flow whose effects do not match the operations it calls, a network target outside the allow list, or a precondition it can disprove. A passing check prints a canonical AST hash. Humans read a generated summary and a GUI, not only the source.

What you can run today, with Python 3.12 and no dependencies:

  git clone https://github.com/RyujiHashimoto/etsah.git && cd etsah
  python3 etsah.py check examples/refund_fixed.etsah
  python3 patterns/etsahc.py build patterns/examples/expense.etsah --out patterns/build/expense

The third command writes demo.html. Open it and approve or reject. The approved step runs only after approval.

Spend budgets and a human gate before an external email, a payment, or a contract change are static checks on agent/task. The checker does not run cron, store remaining budget, or store idempotency keys. A browser playground lives under docs/playground/ and goes live when GitHub Pages is enabled. MCP is `python3 etsah.py mcp`.

Etsah does not replace Python, Rust, or SQL. impl can be any of them. The contract only records what that code may do. It also does not emit real API or database source yet. Line counts from the pattern compiler are estimates.

A staffing-desk spec is included as one industry example. Statutory numbers are configuration, not built into the language.

MIT. Ryuji Hashimoto (J-PROUT Inc.)
https://github.com/RyujiHashimoto/etsah
```

### r/ProgrammingLanguages

投稿前にサイドバーのルール（自己紹介の頻度、フィードバックの求め方）を確認してください。

Title:

```
Etsah, a prototype contract language: declare an agent's effects and approvals, check them before it runs
```

Text:

```
I have been prototyping a small contract language aimed at AI agents, and I would like feedback on the design rather than on a launch.

The split is: one program writes a spec, a deterministic checker accepts or rejects it, and a human reads a generated summary. The checker is not the model that wrote the spec.

On main today the contract layer checks:

- effect completeness (read, write, net, spend, emit). A flow must declare exactly the union of the effects it calls.
- a network allow list (E-POLICY; the default allowed target is stripe)
- preconditions in linear arithmetic (false is E-PRE, unproved is a visible runtime guard)
- a canonical AST hash

The pattern layer is separate. Patterns are JSON manifests. A spec fills slots, and the compiler checks them and estimates an app skeleton. An approval pattern produces a static demo.html where a later step runs only after a person approves. Hook arguments for a notify pattern (channel, recipient versus declared roles) are checked.

What is not on main: numeric spend and rate budgets, a required human gate specifically for external email, payment, and contract changes, and agent/task syntax. The pattern-layer draft is an open pull request. The contract-layer syntax is not opened yet. I am not claiming those as available.

It is deliberately not a superset of existing languages. 1/2, generics syntax, and undefined behavior cannot share one meaning. impl stays in the host language.

Python 3.12, standard library only, MIT:

https://github.com/RyujiHashimoto/etsah

English README, with the Japanese original kept as README.ja.md. SPEC.md (Japanese) is the contract spec; SPEC.en.md is a translation.
```

### Zenn

タイトル:

```
AIエージェントの効果と承認を、実行前に検査する契約言語 Etsah
```

本文:

```
AIに作業を任せるとき、困るのは文章の上手さより、送ってよいメール、引いてよい支払い、変えてよい契約が、実行前に境界として置いてあるかどうかです。Etsah は、その境界を仕様として書き、別の検査器が受け入れる言語の試作です。

分業は三つです。AIが仕様を書く。別のプログラムが検査する。人間は自然言語の要約と GUI を読む。

main で今日動くのは次です。

- 効果（read, write, net, spend, emit）の宣言と、実装との一致
- ネットワーク先の許可リスト
- 線形算術で偽と分かる事前条件はエラー、証明できない条件は実行時ガードとして残す
- 同じ構造なら同じ AST ハッシュ
- パターン層の承認デモ。承認のあとだけ、次の処理が動く

支出の上限と、外部へのメール・支払い・契約変更の人間ゲートは、agent / task の静的検査として入っています。cron は動かさず、予算の残数も冪等ストアもありません。ブラウザの playground は docs/playground/ にあり、GitHub Pages を有効にすると公開されます。MCP は `python3 etsah.py mcp` です。

Python 3.12 だけで動きます。

git clone https://github.com/RyujiHashimoto/etsah.git && cd etsah
python3 etsah.py check examples/refund_fixed.etsah
python3 patterns/etsahc.py build patterns/examples/expense.etsah --out patterns/build/expense

三番目が demo.html を書きます。ブラウザで開いて、承認と差し戻しを押せます。サーバは要りません。

実装言語の置き換えではありません。impl は Python でも Rust でも SQL でもよく、Etsah は「何を読み、何を書き、何を約束するか」だけを持ちます。パターンの build が書くのはレポートとデモとマニフェストで、API や DB の実ソースはまだ出しません。

人材派遣の勤怠・抵触日・請求単価は、産業の一例です。法令の数値は言語に埋め込んでいません。

MIT。仕様の原文は日本語の SPEC.md、英語訳は SPEC.en.md です。
https://github.com/RyujiHashimoto/etsah
```

### Qiita

タイトル:

```
【試作】AIエージェントが実行前に許される効果を宣言する言語 Etsah を3コマンドで動かす
```

本文（投稿時に、コマンド部分だけコードブロックにしてください）:

Etsah は、AIエージェントが実行前にやってよいことを契約として宣言し、別の検査器が危険な自動化を止めるための言語の試作です。

この記事は導入だけです。言語の置き換えではなく、既存の実装の上に契約を載せます。

動かし方。Python 3.12。追加のパッケージはありません。

    git clone https://github.com/RyujiHashimoto/etsah.git && cd etsah
    python3 etsah.py check examples/refund_fixed.etsah
    python3 patterns/etsahc.py build patterns/examples/expense.etsah --out patterns/build/expense

2番目は返金の契約を検査します。効果の過不足と、金額が注文合計を超えるような事前条件を見ます。通ると AST ハッシュを出します。証明できない条件は、黙って通さず警告として残します。

3番目は28行の経費仕様から demo.html を作ります。`patterns/build/expense/demo.html` をブラウザで開くと、申請、承認、差し戻し、却下が押せます。承認のあとだけ、次の処理が動きます。

まだ実行しないもの。支出額や回数は宣言として検査します。残数のカウンタ、cron の実行、冪等ストアはありません。外部メール・支払い・契約変更の人の承認は、agent / task の静的検査です。

読み方。英語 README が入口です。日本語の全文は README.ja.md に残してあります。契約の仕様は SPEC.md（日本語が原文）と SPEC.en.md（訳）です。人材派遣の例は docs/staffing.md にまとめました。言語の中心ではなく、一つの産業例です。

MIT です。https://github.com/RyujiHashimoto/etsah

### X

英語:

```
Etsah is a prototype for AI agents: declare effects and approvals, catch unsafe automations before they run.

Today: effect checks, a network allow list, preconditions, an AST hash, and an approval demo.

In progress: spend/rate budgets and required human gates for external email, payment, and contract changes.

MIT, Python stdlib only.
https://github.com/RyujiHashimoto/etsah
```

日本語:

```
Etsah は、AIエージェントが実行前に許される効果と承認を宣言し、危険な自動化を検査で止める言語の試作です。

今日動くのは、効果の一致、通信先の許可リスト、事前条件、ASTハッシュ、承認デモです。

支出の上限と、外部メール・支払い・契約変更のゲートは、静的検査です。cron と残数のカウンタはまだ動きません。

MIT。Python の標準ライブラリだけ。
https://github.com/RyujiHashimoto/etsah
```

## 8. good first issue の候補

Issue は作っていません。オーナーが採用するものだけ開いてください。`etsah.py` と `patterns/etsahc.py` は #9 と #10 が編集中なので、候補はどれもその二ファイルを変えません。ラベル `good first issue` と `documentation` がリポジトリにあります。

### 候補 1

Title: `Translate patterns/PATTERNS.md into patterns/PATTERNS.en.md`

Body:

```
Keep patterns/PATTERNS.md as the Japanese original. Add patterns/PATTERNS.en.md as a translation, and link the two at the top.

Do not change the meaning of the Japanese file. If you are unsure of a term, leave the English awkward and note it in the pull request, rather than inventing a feature.

Do not edit etsah.py or patterns/etsahc.py. Pull requests #9 and #10 are already editing them.

Check: the new file covers every heading in PATTERNS.md, and both files link to each other.
```

### 候補 2

Title: `Translate patterns/registry/README.md into README.en.md`

Body:

```
Add patterns/registry/README.en.md. Leave the Japanese README.md in that directory unchanged, apart from a link at the top.

The translation should tell an agent the same discovery steps: read index.json, run etsahc find, open one manifest, build a spec.

Do not edit registry JSON, etsah.py, or patterns/etsahc.py.

Check: a reader who has only the English file can run `python3 etsahc.py find "approval"` from patterns/ and know which manifest fields matter (intent, tags, slots, effects, hooks).
```

### 候補 3

Title: `Translate docs/information-flow.md into docs/information-flow.en.md`

Body:

```
docs/information-flow.md is a design note for labels such as pii and secret. The checker does not implement it.

Add an English translation. Do not implement labels, and do not edit etsah.py or patterns/etsahc.py. The Japanese note stays the original.

In the pull request, say that the translation describes a design, not a shipped check. Link the two files from the English README's documentation table.
```

### 候補 4

Title: `Add examples/hello.etsah with English docs and a unittest`

Body:

```
Add a small contract sample, examples/hello.etsah, with English `doc` strings and fictional names only (no personal contact details, no customer data).

`python3 etsah.py check examples/hello.etsah` should exit 0.

Add a unittest that runs that check. Follow tests/support.py. Do not edit etsah.py, patterns/etsahc.py, or the files pull requests #9 and #10 already touch (SPEC.md beyond a link, the lexer tests, ci.yml).

Keep the sample smaller than examples/refund_fixed.etsah. One op, one flow, effects that match.
```

### 候補 5

Title: `Write docs/diagnostics.md, a table of checker codes`

Body:

```
Add docs/diagnostics.md in English. One row per code the checkers already emit, including E-EFF, E-PRE, W-PRE, E-TYPE, E-FLOW, E-POLICY, E-HOOK, E-HOOK-ARG, E-SLOT, W-SLOT.

For each row: the layer (contract or pattern), the meaning in one sentence, and a pointer to an existing test or example. Do not invent codes.

Do not change etsah.py or patterns/etsahc.py. Do not change behavior to make a message easier to document.

Link the page from the English README.
```
