[English](SPEC.en.md)

# Etsah v0.6 — AIのための契約言語

## なぜ新しい言語か

AIがコードを書く量が増えるほど、ボトルネックは「書くこと」から「正しさを確かめること」に移る。既存の汎用言語は人間の可読性と書きやすさを優先しており、AIが生成し、AIが検証し、人間がGUIで確認する用途には最適化されていない。

Etsahは、次の分業を前提にした小さな言語である。

- **AI**がEtsahで仕様と実装の骨格を書き、別のAIが検証する。
- **検証器**が型・効果・契約・ポリシーを決定論的にチェックする。
- **人間**はEtsahを直接読まず、自然言語の要約とGUIで確認する。

## 設計原則

1. **正規形が一意** — 宣言順、フィールド順、効果順、事前条件・事後条件の並び順を正規化し、同じ構造のプログラムは同じAST・同じハッシュになる。論理的に同値な式の変形（可換則・ド・モルガン等）までは行わない。差分と監査が容易。
2. **効果は明示** — ファイル読み書き、ネットワーク、課金、イベント発行を宣言しなければ実行できない。宣言と実際の効果が一致しなければエラー。
3. **契約はコードの一部** — 事前条件・事後条件を式で書き、呼び出し地点で検証する。検証できない条件は「実行時ガード」として人間に可視化する。
4. **診断は修復可能** — エラーはコード、位置、期待値、実際値、修正案を構造化して返す。AIはそのまま修復ループに使える。
5. **人間向けビューは派生物** — 自然言語要約もGUIもASTから生成する。ビューを別の真実にしない。ビューにはASTハッシュを付ける。

## 構文

### 型

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

`type Name` は不透明型。フィールドを持たない。

### 操作（op）

```
op refund(order: Order, amount: Money) -> Receipt
  doc "注文を指定額だけ返金する"
  eff read(orders), write(payments), net(stripe)
  pre amount <= order.total
  pre order.status == "paid"
  post result.status == "refunded"
  impl stripe.refund(order, amount)
```

- `doc` は人間向けの短い説明。GUIのラベルに使う。
- `eff` は効果の宣言。`read`, `write`, `net`, `spend`, `emit` の5種類。
- `pre` は呼び出し側が満たすべき条件。`post` は実装が保証する条件。
- `impl` は実装。別のopの呼び出し、または `native`（外部実装）。

### フロー（flow）

```
flow handle_refund(order_id: Id) -> Receipt
  doc "返金リクエストを処理する"
  eff read(orders), write(payments), net(stripe)
  let order = orders.get(order_id)
  let receipt = refund(order, order.total)
  return receipt
```

フローの効果は、呼び出すopの効果の和集合と一致しなければならない。これにより「この処理は何に触るか」が静的に分かる。

フローは `return` で終わらなければならない。`return` がないフローはエラー（E-FLOW）であり、`return` の後ろに書かれた文も到達不能としてエラーになる。

フローは `if` / `else` で分岐できる。条件は `Bool` で、本体は2スペース深くする。`else` は省略できる。

```
flow classify(id: Id) -> String
  eff read(customers)
  let c = customers.get(id)
  if c.email == "blocked@example.com"
    return "blocked"
  else
    return "allowed"
```

`let` で束縛した変数のスコープは、その分岐の中だけである。両方の分岐が `return` すれば、その `if` より後ろの文は到達不能になる。どちらかの分岐が返らない場合、フロー全体が返るには `if` の後ろに `return` が必要になる。効果は、条件式と両方の分岐が呼ぶ op の和集合である。

`if` は flow の中だけで使える。task の本体は `let` と `return` だけである。

### エージェントとタスク

AIに任せる仕事は、`agent`（持ってよい効果の封筒）と `task`（その中の一つの仕事）の二段で書く。`gate` は人が許可するまで外部効果を止め、`policy` はどの効果が社外か、支払いか、契約の変更かを名指しする。インデントはほかの宣言と同じくスペース2つ。予算の数値は整数円である。

```
agent staffing_mail
  doc "承認済みの文面だけを社外へ送る"
  allow read(timesheets), emit(mail)
  budget run 0 period 0
  limit net 0 hour
  limit emit 20 hour
  audit ast_hash

policy
  external emit(mail), net(stripe)
  payment spend(payroll)
  contract write(contracts)

gate remind_client
  doc "督促メールはコーディネーターの承認後だけ送る"
  approver coordinator
  before emit(mail)
  on_approved mail.send

task remind_timesheet(period: Id) -> Bool
  agent staffing_mail
  doc "未提出の勤怠について、承認された督促だけを送る"
  trigger schedule "0 9 * * 1"
  eff read(timesheets), emit(mail)
  gate remind_client
  key period
  retry 2 escalate coordinator
  let sheet = timesheets.open(period)
  let sent = mail.send(sheet)
  return sent
```

起動は三種類である。

- `trigger schedule "0 9 * * 1"` は分・時・日・月・曜の5欄。`*`、`a-b`、`*/n`、カンマ区切りを受け、`MON` のような名前は受けない。
- `trigger record Invoice` は、その型の新しい行だけである。更新では起動しない。
- `trigger webhook "inbound/inquiry"` は受け口の名前だけを見る。署名は検査しない。

期間の境界は `Asia/Tokyo` である。タスクごとに変える構文は無い。

静的な規則は次である。cron は動かさない。整数円の残数も、同じキーの再実行を畳むストアも、検査器は持たない。回数と十進金額の残数は `etsah guard` がファイルに数える。

1. `task` の `eff` は、呼んだ op の効果の和集合と一致する（E-EFF）。和集合は、その `agent` の `allow` の部分集合でなければならない。はみ出した効果は E-POLICY。
2. `policy external` に入る `emit` または `net`、すべての `spend`、`policy contract` の `write` は、`before` がその効果を挙げた `gate` が無いと E-POLICY。社内への `emit`（`policy external` に無いもの）はゲート無しで通す。全社 Slack のような通知を社外にするかは、`policy external` にその宛先を書くかどうかで決める。
3. `spend` を許す agent は、`budget run` と `budget period` が 1 以上の整数円で、1回は期間以下である。許さない agent の予算は 0 である。タスクが自分の `budget` を書くときも同じで、agent の予算を超えてはならない。
4. `limit emit` と `limit net` は、その効果を許すとき 1 以上、許さないとき 0 である。単位は1時間である。これは静的な上限で、実行時の残数は数えない。
5. `limit calls N period hour|day|month` と `limit spend AMOUNT CURRENCY period hour|day|month` は、`agent`、`task`、`op` に書ける。回数は正の整数、金額は正の十進数、通貨は大文字3文字である。期間の境界は `Asia/Tokyo` である。`spend` の op は `spend from 引数[.フィールド] currency CURRENCY` で、金額を読む場所を Decimal として宣言する。タスクの上限が、同じ期間・同じ通貨の agent の上限を超えると E-POLICY。同じ agent を名指しする task が二つ以上あり、同じ期間の `limit calls` の合計、または同じ期間かつ同じ通貨の `limit spend` の合計が、その agent の宣言した上限を超えるときも E-POLICY である。phase 1 では、その宣言した上限が親の残予算のすべてである。実行時の残数は引かない。通貨が違う子の金額は合計に入れず、通貨の不一致は E-POLICY である。期間が違う子は合計に入れない。上限を書いていない子は 0 として足さない。`spend` を使う task に、task か agent かその op の十進金額上限が無いときは警告（W-BUDGET）であり、エラーではない。整数円の `budget` は従来どおり、無いと E-POLICY である。
6. `retry` は 0 以上 5 以下。尽きたあとは `escalate` の人のロールへ渡す。その引き渡しは agent の `allow` に入れない。エスカレーションが op や agent を指し、その効果が上のゲート対象なら E-POLICY である。
7. `key` は引数か、引数のフィールドである。冪等の同一性は agent、task、key であり、プログラムのハッシュが変わっても外部効果をやり直す意味は持たせない。ストア自体はまだ無い。
8. `audit ast_hash` は agent に必須である。監査の同一性は正規化 AST の SHA-256 全64桁（`ast_digest`）である。表示と既存の `ast_hash` は、その先頭12桁のままである。
9. 承認者は人のロールである。`approver` に agent の名前は書けない。`on_approved` が外部効果の唯一の呼び出しであることまでは、この検査は見ない。

`net` の宛先が許可リスト（既定は `stripe`）に入っているかは、これまでどおり op のポリシー検査が止める。`policy external` は、その宛先にゲートが要るかである。

情報フローの `policy`（`sink` と `declassify`）と、このタスク用の `policy`（`external`、`payment`、`contract`）は同じ宣言名で、中の行で区別する。一つのブロックに両方は書けない。プログラムには、情報フローの `policy` を一つと、タスク用の `policy` を一つまで置ける。

`check` は、タスクがあるとき、起動、効果、ゲート、再試行、キー、予算を文章で出す。終了コードは 0（エラーなし。警告だけなら 0）、1（エラーが1件以上）、2（ファイルを解析できない）である。

`check --json` は一つのオブジェクトを出す。常にある鍵は `hash`（正規化 AST の SHA-256 の先頭12桁）と `diagnostics` である。診断の鍵は `code`、`severity`、`line`、`message` で、あるときだけ `fix`、`detail`、`context` が付く。タスクがあるときは `audit_id`（SHA-256 の64桁）と `plan`（人が読む行の配列）も付く。`manifest` は `hash`、タスクがあるときの `audit_id`、各 agent の `allow`、整数円の `budget_run` と `budget_period`、`limit_net` と `limit_emit`、宣言があれば `call_limit` と `spend_limit`、その agent の task が名指しした gate 名である。agent は名前順、`allow` と gate 名も名前順で、同じソースは同じバイト列になる。`signature` は常に `null` である。このフィールドは署名しない。鍵も読まない。

### 式

- 変数、フィールドアクセス（`order.total`）、関数呼び出し（`refund(order, amount)`）
- リテラル（整数、十進数、日付、文字列、真偽値）
- 二項演算（`+`, `-`, `*`, `/`, `==`, `<=`, `<`, `>`, `>=`, `&&`, `||`）
- 単項演算（`!`）。ASTでは二項演算とは別の単項ノードとして表す

組み込み型は `Int`、`Decimal`、`Date`、`String`、`Bool`。`Money` は不透明型のまま、利用者の宣言と、`Int` との演算規則で扱う。

リテラルの形:

- 十進数は `1800.50` のように、点の両側に数字がある。意味は `decimal.Decimal` で、浮動小数ではない。`1800.50` と `1800.5` は等しい。`1.` や `.5` や `1e2` はリテラルではない。
- 日付は空白を挟まない `2026-11-23`（ゼロ埋めのグレゴリオ暦）。存在しない日（`2026-02-31` など）は構文エラー。空白を入れた `2026 - 11 - 23` は従来どおり整数の減算である。
- `Int` と `Decimal` は混ぜてよい。どちらかが `Decimal` なら結果は `Decimal`。`Int` 同士の `+ - * /` は従来どおり `Int` のままである。`1 / 2` は定数畳み込みしない。十進数の除算も線形ではないので、事前条件では実行時ガードになる。
- `Date + Int` と `Int + Date` は日数を足した `Date`。`Date - Int` は `Date`。`Date - Date` は日数の `Int`。`Date` と `Decimal` の演算と比較は型エラー。`Date` 同士の加算も型エラー。

比較演算の型規則:

- `==` / `!=` は、互換な型同士でだけ使える（例: `Status` と文字列リテラル、`Int` と `Decimal`）。`Money` と `String` のような比較は型エラー（E-TYPE）。
- `<`, `<=`, `>`, `>=` は、順序を付けられる型同士（`Int`、`Decimal`、`Money`、`Date`、同じ型の `String`）でだけ使える。`Int` と `Decimal`、`Int` と `Money` の混合は許す。`Date` は `Date` とだけ比べる。
- `!` のオペランドは `Bool` でなければならない。

契約検査は、整数・十進数・日付の差を線形（アフィン）な形に直して定数比較する。日付の内部表現はグレゴリオ暦の通日で、差と比較では原点が消える。変数が残る、または除算を含む式は証明せず、実行時ガードとして報告する。黙って通過させない。

## 検証パイプライン

1. **構文解析** — 行指向の文法。インデントは2スペース固定。
2. **型検査** — 引数、戻り値、フィールド、演算の型を検査。
3. **効果検査** — opの宣言効果と実装が行う効果を比較。フローとタスクは呼び出しの和集合と宣言を比較する。
4. **契約検査** — 呼び出し地点で事前条件を実引数に代入し、線形算術と等価性で判定。真なら通過、偽ならエラー、不明なら実行時ガード。
5. **情報フロー検査** — ラベルの宣言・注釈・情報フローの `policy` がある場合だけ、引数・戻り値・契約・sink・`sends`・`declassify` を検査する。タスク用の `policy` だけではこの検査は始まらない。
6. **ポリシー検査** — 許可されたネットワーク先・効果種別のみを許可する。AIタスクは、封筒、ゲート、整数円の予算、時間あたり回数、再試行もここで止める。
7. **正規化** — ASTをソート済みJSONに変換し、SHA-256ハッシュを付与。型・op・flow・フィールド・効果に加え、pre/postの連言も式の正規化キーでソートする。ラベル、情報フローの `policy`、agent、task、gate、タスク用の `policies` は、あるときだけハッシュに入る。無いプログラムのハッシュは変えない。引数や文の順序は意味を持つためソートしない。

## 人間向けビュー

`doc` とASTから、次を生成する。

- 日本語の要約（「注文を取得し、全額を返金して、領収書を返す」）
- ステップごとのカード（入力、出力、効果、契約の状態）
- 効果とポリシーのバッジ
- 検証履歴（最初のドラフトで見つかった問題と修正）
- 形式コード（折りたたみ）
- ASTハッシュ

ビューは常にASTから再生成できる。ビューだけを編集することはできない。GUIでの操作は、対応するASTの変更に翻訳される。

## 試作の範囲

- パーサー、型検査、効果検査、事前条件検査、ポリシー検査、HTMLレンダラー。AIタスクの静的検査（封筒、ゲート、整数円の予算、回数、再試行、冪等キーの形）。
- 情報フローは静的なラベル集合の検査だけを行い、暗黙のフロー、コレクションの要素ラベル、ラベル多相、実行時の強制は対象外。
- 線形算術の事前条件のみ。文字列やコレクションの契約は実行時ガード扱い。十進数と暦日の差も、定数まで落ちるものだけを証明する。
- 実装の意味論は検証しない。`impl` は宣言として扱う。
- 並行性、時刻、リソース消費の証明は対象外。暦日（`Date`）は対象であり、時刻ではない。cron の実行、予算の残数、冪等ストアは対象外である。

これは言語の完全な実装ではなく、「AIが生成し、AIが検証し、人間がGUIで見る」というループが成立するかの試作である。

## なぜ「全言語の上位互換」にしないのか

厳密な上位互換は不可能に近い。

- 意味論が衝突する。`1/2` はC/Java/Rustで0、Python 3で0.5。整数オーバーフローは、Javaではラップ、Rustのデバッグビルドではパニック、Pythonでは任意精度。同じ式に一つの意味を与えられない。
- 構文が衝突する。`<>` はJava/C#ではジェネリクス、Pythonでは比較。`{}` はブロック、オブジェクト、集合。インデント有意と自由形式。コメント記法も違う。
- マクロとメタプログラミング。Lispのマクロ、Cプリプロセッサ、Rustの手続きマクロ、C++テンプレート。構文拡張を許すと言語の解析は一般に決定不能になる。
- 実行環境。JVM、BEAM、V8、CPython、.NET、OSのAPI。言語はランタイムと不可分で、一つの言語で全部を置き換えるにはVMとOSを作ることになる。
- 未定義動作。C/C++のUBは「意味が定義されていない」状態。上位互換がUBを保存すれば安全でなくなり、安全にすれば元の意味を保存できない。

代わりに取れる道は三つ。

1. 共通の中間表現（LLVM IR、MLIR、WebAssembly、GraalVM）。意味は保てるが、ソース言語の上位互換ではなく、プラットフォーム。
2. 多言語を埋め込むホスト言語（Racketの`#lang`、Scalaの文字列補間、MPS）。境界ごとに意味を委譲する。
3. 契約・効果のメタ言語（Etsah）。実装言語を置き換えず、その上に検証可能な契約をかぶせる。

Etsahは3を選ぶ。`impl`はPythonでもRustでもSQLでもよく、Etsahは「何を保証するか」だけを担う。全言語を包含するのではなく、全言語に接続する。

## リバースエンジニアリングへの含意

Etsahのような契約層は、リバースエンジニアリングを「意味の復元」から「契約の照合」に変える可能性がある。

- AI生成コードにEtsahの契約が添付されていれば、バイナリや難読化コードから意図を推測する代わりに、契約と実装の一致を検証できる。
- 共通の効果・データフロー表現は、言語をまたいだ比較を容易にする。マルウェアの挙動分析では、API呼び出しの列ではなく「何を読み、何を書き、どこへ送るか」で比較できる。
- ただし、コンパイルで失われる情報は戻らない。最適化、インライン展開、シンボル削除、難読化、パッキング。バイナリからの意味復元は一般に決定不能で、観測した入力に対して等価なプログラムを一つ見つけることにすぎない。
- 契約が自己申告である場合、攻撃者は嘘の契約を付けられる。契約は検証されて初めて信頼できる。これは防御側にも攻撃側にも効く諸刃の剣。

つまり、リバースエンジニアリングが楽になるのは「契約が存在し、検証されている場合」に限られる。契約のないバイナリの解析は、依然として職人芸とツールの世界に残る。

## 情報フロー制御

`pii` や `secret` を型に付け、許可されていない sink への流出をコンパイル時に検出する。設計の全体像と未実装部分は [docs/information-flow.md](docs/information-flow.md) にあり、ここでは実装済みの規則をまとめる。

### ラベルの宣言と注釈

```
label pii
label secret

type Customer {
  id: Id
  email: String @pii
  internal_note: String @secret
}
```

`public` は暗黙のラベルで、宣言も注釈もできない。ラベル名は `[a-z][a-z0-9_]*` で、効果種別（`read`、`write`、`net`、`spend`、`emit`）と衝突してはならない。注釈を付けられるのは不透明型とフィールドの型で、レコード型そのものや型引数の内側（`Box<String @pii>`）には付けられない。これらは `E-LABEL` になる。

ラベルの集合は型の一部として扱う。`String @pii` と `String` は異なる型だが、`String` から `String @pii` への代入は許す（公開データは秘密に流せる）。逆向きは `E-IFLOW-ARG` または `E-IFLOW-RET` になる。

### 式のラベル推論

式のラベルは次のように決まる。

| 式 | ラベル |
|---|---|
| リテラル、ラベルなし変数 | 空（public） |
| フィールドアクセス `c.email` | そのフィールドのラベルだけ |
| レコード値 `Customer { ... }` | 全フィールドのラベルの和集合 |
| 二項演算 `a + b` | 両辺のラベルの和集合 |
| 比較 `a == b` | 両辺の和集合（結果は `Bool` でもラベルを持つ） |
| 関数呼び出し | 宣言された戻り値のラベル |
| `declassify(e, l, reason)` | `e` のラベルから `l` を除いたもの |

レコード全体を変数に持つと全フィールドのラベルを帯びるが、フィールドを取り出せばそのフィールドのラベルだけになる。これにより「顧客レコードは PII を含むが、`id` は公開」という扱いができる。

### sink と `sends`

外部に触る操作は、どの引数がどの効果へ流れるかを `sends` で宣言する。

```
policy
  sink net(email) accepts pii
  sink net(analytics) accepts pii
  sink net(*) rejects secret

op send_receipt(to: String @pii) -> Bool
  eff net(email)
  sends to -> net(email)
  impl native
```

`sink` は効果の対象ごとに、受け入れるラベル（`accepts`）と決して受け入れないラベル（`rejects`）を宣言する。`net(*)` はワイルドカードで、より具体的な `net(email)` があれば `accepts` は具体的な規則で置き換わり、`rejects` は具体・ワイルドカードの和集合になり、上書きできない。`read` は sink になれない（`E-POLICY`）。

`sends` は `impl native` の op にだけ書ける。ラベル付きの引数・戻り値、または `read` 効果を持つ native op は、`read` 以外の各効果に対して `sends` を持たなければならない。抜けは `E-SINK-UNBOUND`、未知の引数名や効果は `E-SINK` になる。`sends none -> net(health)` は「この効果にラベル付きデータを流さない」と明示する。

### `declassify`

ラベルを落とす操作は、ポリシーで許可された場所と役割の中でだけ行える。

```
policy
  declassify pii in masked_tracking require role support

flow masked_tracking(id: Id) -> Bool
  eff read(customers), net(analytics)
  let c = customers.get(id)
  let masked = declassify(c.email, pii, "分析用にドメインだけ残す")
  return track_event(masked)
```

理由は 1〜200 文字で改行を含まない。許可された `declassify` は `W-DECLASS` として監査ログに残り、ポリシーにない場所・役割・ラベルは `E-DECLASS` になる。レコード全体の `declassify` と契約式（`pre` / `post`）内の `declassify` は拒否する。

### 分岐と暗黙のフロー

`if` の条件がラベルを持つとき、その分岐の中で作られる値は条件と同じラベルを帯びる。これをプログラムカウンタ（PC）ラベルと呼ぶ。条件のラベルは、ネストした `if` で和集合になる。

```
flow classify(id: Id) -> String
  eff read(customers)
  let c = customers.get(id)
  if c.email == "blocked@example.com"
    return "blocked"
  else
    return "allowed"
```

戻り値の文字列自体は公開でも、どちらを返したかでメールアドレスが分かる。`return` の検査は `L(式) ∪ PC ⊆ L(戻り値型)` で行うため、このフローは両方の `return` で `E-IFLOW-RET` になる。分岐内の `let` も PC ラベルを帯びるので、`let answer = "yes"` を `return answer` しても同じように検出する。

条件を公開してよいと判断した場合は、条件式そのものを `declassify` する。

```
let blocked = declassify(c.email == "blocked@example.com", pii, "判定結果だけを公開する")
if blocked
  return "blocked"
```

これで PC ラベルは空になり、`W-DECLASS` だけが残る。分岐内の変数は分岐の外へ漏れない。`else` を省略した場合、その分岐を抜けた後の文は通常どおり実行される。

### 検査と診断

検査は、引数（`E-IFLOW-ARG`）、戻り値（`E-IFLOW-RET`）、契約式（`E-IFLOW-CONTRACT`）、sink（`E-IFLOW-SINK`）、`sends`（`E-SINK`、`E-SINK-UNBOUND`）、ポリシー（`E-POLICY`）、`declassify`（`E-DECLASS`、`W-DECLASS`）の順に走る。診断には行番号と修正案を付け、`--json` では `context` に「どの値がどの引数へ流れたか」を入れる。

ラベル宣言も注釈もポリシーもないプログラムでは、情報フロー検査を丸ごと省略する。この高速パスにより、既存プログラムの AST とハッシュは変わらない（`examples/refund_fixed.etsah` は `bae8501e8b3f` のまま）。

### 現時点の限界

分岐は `return` による早期脱出だけで、代入・ループ・例外はない。そのため「分岐の後で変数を更新して合流する」形の暗黙フローは表現できない。ラベル付きのコレクション、ラベル多相、実行時の強制、`declassify` の呼び出し元認証も未実装である。`impl native` の内部は信頼する。つまり、この検査は「宣言された境界を越えた流れ」を止めるものであり、情報の非干渉性を証明するものではない。
