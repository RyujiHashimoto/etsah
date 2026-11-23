# PROUT 情報フローラベル設計（v0.1案）

## 目的

`pii` や `secret` のような機密ラベルを型に付け、値が許可されていない場所へ流れることをコンパイル時に検出する。対象は、ログ、ネットワーク、DB書き込み、イベント発行、画面表示などの出力先（sink）である。

基本方針は次の3つ。

1. **ラベルは型の一部** — 別構文のアノテーションではなく、`String @pii` のように型に付ける。
2. **デフォルトは public** — ラベルを書かなければ public。既存プログラムの意味とハッシュを変えない。
3. **明示的な declassify だけがラベルを下げられる** — `@public` と書いてもラベルは消えない。監査可能な `declassify` だけを許可する。

`Pii<String>` のようなラッパー型も考えられるが、算術・比較・既存コード生成をすべてラップする必要がある。PROUTは既存の型構文に後置ラベルを足す方式を採り、段階的に導入する。

## 用語

- **ラベル** — `pii`, `secret`, `internal` などのタグ。順序ではなく集合として扱う。
- **public** — ラベルなし。空集合。
- **実効ラベル** `L(T)` — 型 `T` の値が持ち得るラベルの集合。
- **sink** — 値が出ていく先。`net(email)`, `write(customers)`, `emit(audit)`, `spend(stripe)` など。
- **source** — 値が入ってくる先。`read(customers)` など。source自体はラベルを持たず、戻り値の型がラベルを持つ。
- **declassify** — 明示的にラベルを下げる操作。ポリシーと理由が必要。

## 構文

### ラベル宣言

```prout
label pii "個人情報"
label secret "秘密情報"
label internal "社内情報"
```

`public` は組み込みで、宣言できない。`label` の文字列は人間向けの説明で省略可能。未宣言のラベルを型やポリシーで使うと `E-LABEL`。

### 型・フィールド・引数・戻り値

型の後ろに `@ラベル` を付ける。複数ラベルは繰り返す。

```prout
type Customer {
  id: Id
  email: String @pii
  api_key: String @secret
  plan: String @internal
}

type ApiKey @secret
```

- レコード型の実効ラベルは、全フィールドのラベルの和集合。
- 不透明型 `type ApiKey @secret` は、その型の全値が `secret`。
- フィールドを省略した場合は public。機密フィールドは必ず明示する。
- 型注釈 `Customer @internal` は、型固有のラベルに `internal` を追加する。ラベルを置き換えるのではない。

opとflowの引数・戻り値にも同じ構文を使う。

```prout
op send_receipt(to: String @pii, body: String) -> Receipt
  eff net(email)
  sends to, body -> net(email)
  impl native
```

### sinkポリシー

トップレベルに `policy` ブロックを1つ置く。

```prout
policy
  sink net(email) accepts pii
  sink net(analytics) accepts public
  sink write(customers) accepts pii
  sink emit(audit) accepts internal
  sink net(*) rejects secret
```

- `sink <effect> accepts <labels>` — そのsinkが受け取れるラベルを追加する。
- `sink <effect> rejects <labels>` — 明示的に拒否する。`rejects` は `accepts` より優先。
- デフォルトは public のみ。
- `net(*)` は全ネットワーク先のワイルドカード。具体的な `net(email)` が優先される。
- `read(...)` はsinkではないため、ポリシーを書かない。
- 同じsinkに対する重複・矛盾するルールは `E-POLICY`。

### 効果へのデータ束縛 `sends`

ラベルだけでは「どの引数がどのsinkへ流れるか」が分からない。特に `impl native` は本体が見えないため、明示的な束縛が必要になる。

```prout
op send_receipt(to: String @pii, body: String) -> Receipt
  eff net(email)
  sends to, body -> net(email)
  impl native

op save_customer(customer: Customer) -> Id
  eff write(customers)
  sends customer -> write(customers)
  impl native

op health_check() -> Bool
  eff net(health)
  sends none -> net(health)
  impl native
```

- `sends p1, p2 -> effect` は、引数 `p1`, `p2` がそのeffectのsinkへ流れると宣言する。
- `effect` は `eff` に宣言済みでなければならない（`E-SINK`）。
- 引数名は実在しなければならない（`E-SINK`）。
- `sends none -> effect` は、そのeffectにラベル付きデータを渡さない宣言。
- ラベル付きの引数・戻り値を持ち、`read` 以外の効果を持つ `impl native` のopは、各effectに `sends` を要求する。なければ `E-SINK-UNBOUND`。
- 式実装やflowは本体からデータフローを推論できるため、`sends` は任意の文書化として扱う。ただし書く場合は実際のフローと一致しなければならない。

### declassify

ラベルを下げる唯一の操作。式として書く。

```prout
declassify(customer.email, pii, "領収書の宛先に使う")
```

- 第2引数は外すラベル。`pii` を外すと、`secret` が残っていれば `secret` のまま。
- 第2引数に `public` を書くと全ラベルを外す。最も強い操作なので、ポリシーで明示的に許可された箇所だけに限る。
- 第3引数は監査ログに残す理由。空文字は `E-DECLASS`。
- 結果のラベルは `L(expr) - {label}`。`public` の場合は空集合。
- 対象ラベルを持っていない場合は `E-DECLASS`（タイポを黙って通さない）。

許可は `policy` に書く。

```prout
policy
  declassify pii in send_receipt require role support
  declassify public in export_audit require role security_admin
```

- `in <op|flow>` は、その操作の中でのみ許可する。
- `require role <role>` は、パターン層の `auth.roles` と照合する。契約層だけではロール宣言がないため、文字列として保持し、パターンコンパイラが検査する。
- 許可されていない場所での `declassify` は `E-DECLASS`。
- 許可された `declassify` は `W-DECLASS` として監査レポートに残す。

## ラベルの意味論

### 集合ラベル

ラベルは順序を持たない集合として扱う。`@pii @secret` と `@secret @pii` は同じ。

`pii` と `secret` は独立したタグであり、`secret` が `pii` を包含するとは定義しない。APIキーは `secret` だがPIIではないし、メールアドレスは `pii` だがAPIキーではない。両方必要な値は `@pii @secret` とする。

将来的に `label secret > pii` のような階層を足せるが、v0.1では明示的な集合のままにする。ポリシーで両方を許可するか、declassifyで片方を外す。

### 型の実効ラベル

```
L(String) = {}
L(Int) = {}
L(Bool) = {}
L(type T @L) = L
L(type T { f1: T1 @L1, ... }) = L1 ∪ ... ∪ Ln
L(T @L) = L(T) ∪ L
```

レコード型の値全体をsinkへ渡すときは、全フィールドのラベルの和集合が検査される。フィールドアクセス `e.f` のラベルは、そのフィールドの宣言ラベルだけを使う。ほかのフィールドのラベルは混ぜない。これにより、`Customer` 全体は `pii` でも、`customer.id` は public のまま扱える。

ただし、機密フィールドにラベルを付け忘れると、そのフィールドは public になる。ラベルの正しさは型宣言の正しさに依存する。パターンレビューで、PII・秘密・認証情報のフィールドがすべてラベルされていることを確認する。

### 代入可能性

`T1 @L1` を `T2 @L2` として使えるのは、基底型が互換で、`L1 ⊆ L2` のときだけ。

```
String @public  -> String @pii     許可（publicはPIIとして扱える）
String @pii     -> String @public  拒否（PIIをpublic文脈に渡せない）
String @pii     -> String @pii @secret 許可（より多く扱う文脈）
String @pii @secret -> String @pii 拒否（secretを扱えない文脈）
```

引数は `L(actual) ⊆ L(param)`、戻り値は `L(actual) ⊆ L(declared_return)` を検査する。関数が `@pii` を返すと宣言して public を返すのは安全（約束より少ない）。逆に public と宣言して PII を返すのは `E-IFLOW-RET`。

## 静的検査ルール

### 式のラベル推論

```
L(literal) = {}
L(variable) = 変数の宣言ラベル（letは初期化式から推論）
L(e.field) = フィールドの宣言ラベル
L(op(args)) = opの戻り値ラベル
L(e1 + e2) = L(e1) ∪ L(e2)
L(e1 == e2) = L(e1) ∪ L(e2)
L(!e) = L(e)
L(declassify(e, l, reason)) = L(e) - {l}
```

比較結果の `Bool` にもオペランドのラベルを付ける。`customer.email == "a@b"` の真偽はPIIの情報を含むため、publicとして扱わない。`&&` / `||` も両辺のラベルの和集合。

### 文とフロー

- `let x = e` — `L(x) = L(e)`。型注釈はv0.1では付けない。
- `return e` — `L(e) ⊆ L(flow.ret)`。違反は `E-IFLOW-RET`。
- `pre` / `post` — 契約式は public でなければならない。`L(expr) ⊆ {public}`。違反は `E-IFLOW-CONTRACT`。エラーメッセージにPIIを出さないため。
- 関数呼び出し — 各引数について `L(arg) ⊆ L(param)`。違反は `E-IFLOW-ARG`。
- `sends p -> effect` — `L(p) ⊆ accepted(effect)`。違反は `E-IFLOW-SINK`。
- `declassify` — 現在のop/flow名とラベルに対するポリシーがあること。理由が空でないこと。違反は `E-DECLASS`。

### 効果とsink

1. `eff` の各effectについて、`policy` のsinkルールを解決する。
2. 具体的なターゲット `net(email)` が `net(*)` より優先される。
3. `rejects` が `accepts` より優先される。
4. ルールがなければ public のみ許可。
5. `sends` で束縛された引数のラベルが許可集合の部分集合でなければ `E-IFLOW-SINK`。
6. `impl native` でラベル付きデータをsinkへ流すのに `sends` がなければ `E-SINK-UNBOUND`。
7. 式実装では、呼び出しの引数ラベルと呼び出し先の効果からフローを推論し、同じ規則を適用する。

### 暗黙フロー（v0.2以降）

現在のflowは直線的で `if` がないため、v0.1では暗黙フローを扱わない。将来 `if` を追加するときは、条件式のラベルをプログラムカウンタ（PC）ラベルに加え、分岐内で代入・returnされる値にPCラベルを合成する。

```
if customer.is_vip:        # is_vip が pii なら PC = {pii}
  return "vip"             # 結果は pii を含む
```

v0.1の `&&` / `||` は短絡評価をせず、両辺のラベルの和集合を結果に付けることで保守的に扱う。

### ASTハッシュ

ラベルとポリシーはセキュリティ意味論の一部なので、ASTハッシュに含める。

- ラベル集合はソートして直列化する。
- 空ラベルはJSONに出さない。これにより、ラベルを一切使わない既存プログラムのハッシュは変わらない。
- `policy` ブロックの有無・内容はハッシュに含める。
- `sends` の有無・内容もハッシュに含める。

## 診断コード

| コード | 意味 | 例 |
|---|---|---|
| `E-LABEL` | 未宣言ラベル、重複宣言、publicの再定義 | `unknown label 'pii2'` |
| `E-IFLOW-ARG` | 引数のラベルが高すぎる | `send_receipt expects String @pii, got String @secret` |
| `E-IFLOW-RET` | 戻り値のラベルが宣言より高い | `flow returns String @pii, declares String @public` |
| `E-IFLOW-SINK` | sinkが受け取れないラベル | `net(analytics) does not accept pii` |
| `E-IFLOW-CONTRACT` | pre/postにラベル付き値 | `precondition uses pii value` |
| `E-SINK` | sendsのeffect/引数が不正 | `sends references undeclared effect net(email)` |
| `E-SINK-UNBOUND` | native opにsendsがない | `op send_receipt has labeled data but no sends for net(email)` |
| `E-DECLASS` | 許可されないdeclassify | `declassify pii is not allowed in flow export` |
| `W-DECLASS` | 許可されたdeclassifyの監査警告 | `pii declassified in send_receipt` |

診断メッセージにはラベル付きの**値**を含めない。変数名・型名・ラベル名だけを出す。エラーログ自体が漏洩経路になるため。

## 例

### 安全な例

```prout
label pii "個人情報"
label secret "秘密情報"
label internal "社内情報"

type Customer {
  id: Id
  email: String @pii
  api_key: String @secret
}

policy
  sink net(email) accepts pii
  sink net(analytics) accepts public
  sink write(customers) accepts pii
  sink emit(audit) accepts internal
  sink net(*) rejects secret

op customers.get(id: Id) -> Customer
  eff read(customers)
  impl native

op send_receipt(to: String @pii, body: String) -> Receipt
  eff net(email)
  sends to, body -> net(email)
  impl native

op track_event(event: String) -> Bool
  eff net(analytics)
  sends event -> net(analytics)
  impl native

flow receipt(customer_id: Id) -> Receipt
  eff read(customers), net(email)
  let customer = customers.get(customer_id)
  return send_receipt(customer.email, "receipt")
```

`customer.email` は `pii`、`send_receipt` の `to` は `pii`、`net(email)` は `pii` を受け取れる。`customer.api_key` を渡すと `secret` が `net(email)` の許可集合にないため `E-IFLOW-SINK`。

### 違反例

```prout
flow leak(customer_id: Id) -> Bool
  eff read(customers), net(analytics)
  let customer = customers.get(customer_id)
  return track_event(customer.email)
```

`track_event` の `event: String` は public だが、実引数は `String @pii`。`E-IFLOW-ARG`。仮に `track_event` の引数を `String @pii` にしても、`net(analytics)` が `pii` を受け取れないため `E-IFLOW-SINK`。

### declassifyを使う例

```prout
policy
  declassify pii in masked_email require role support

flow masked_email(customer_id: Id) -> Bool
  eff read(customers), net(analytics)
  let customer = customers.get(customer_id)
  let masked = declassify(customer.email, pii, "分析用にマスクする")
  return track_event(masked)
```

`masked` は public になり、`track_event` と `net(analytics)` を通る。`declassify` は `W-DECLASS` として監査レポートに残る。`require role support` はパターン層の `auth.roles` と照合する。

## パターン層との統合

パターンmanifestに `data_policy` を追加する。

```json
{
  "name": "customer_portal",
  "slots": {
    "email": {"type": "String", "labels": ["pii"]}
  },
  "data_policy": {
    "labels": ["pii", "secret"],
    "sources": {"customers": ["pii"]},
    "sinks": {
      "net(email)": {"accepts": ["pii"]},
      "net(analytics)": {"accepts": []},
      "emit(audit)": {"accepts": ["internal"]}
    },
    "declassify": [
      {"label": "pii", "in": "masked_email", "role": "support"}
    ]
  }
}
```

- `labels` — パターンが使うラベル。
- `sources` — 読み込み元と、そのデータに付くラベル。
- `sinks` — パターンが要求するsinkと許可ラベル。
- `declassify` — パターンが要求するdeclassifyとロール。
- アプリ側の `policy` とマージする。許可の交差を基本とし、パターンが要求するsinkをアプリが許可しなければ `E-POLICY`。
- `data_policy` がないパターンは public のみ。ラベル付きデータに適用すると失敗する（fail closed）。
- `build` は `security.json` に、ラベル付きフロー、sink、declassify、未解決の保証を出力する。`report.html` には `pii` / `secret` バッジを表示する。

## 実装計画

1. **パーサー** — `@` トークン、`label` / `policy` / `sends` 宣言、型の後置ラベル。
2. **AST** — `TypeRef(base, labels)`, `Policy`, `Send` を追加。既存の型文字列は互換アダプタで扱う。
3. **チェッカー** — `check_labels`, `infer_labels`, `check_sends`, `check_declassify` を追加。`compatible` をラベル対応にする。
4. **ハッシュ** — 空ラベルを省略して既存ハッシュを維持。ポリシーとsendsは含める。
5. **テスト** — `tests/test_information_flow.py` に、引数・戻り値・sink・契約・declassify・native・未宣言ラベル・後方互換のケースを追加。既存148件はそのまま通す。
6. **パターン層** — manifest schema、マージ、`security.json`、レポート表示。
7. **ランタイム** — 生成コードに `Labeled<T>` ラッパーを渡し、sinkアダプタだけがラベルを検査してunwrapする。能力ブローカーと組み合わせる。

### 段階導入

- **v0.1** — 型ラベル、引数・戻り値、sinkポリシー、sends、declassify。暗黙フローなし。静的検査のみ。
- **v0.2** — `if` のPCラベル、パターンmanifestのdata_policy、security.json。
- **v0.3** — ランタイムのLabeled<T>と能力ブローカー、監査ログ。
- **将来** — ラベル多相（`@L` 型変数）、ラベル階層、暗号化されたsecret、情報フロー制御の完全性証明。

## 限界

- ラベルは「宣言の正しさ」に依存する。機密フィールドのラベル漏れは検出できない。
- 静的なラベルだけでは、customコードやnative実装の内部までは守れない。実行時の能力ブローカーとLabeled<T>が必要。
- v0.1では暗黙フローを扱わない。分岐・例外・タイミングなどによる漏洩は将来の課題。
- ラベルは暗号化ではない。secretとラベルしても、保存時・通信時の暗号化は別途必要。
- ネイティブopの `sends` は宣言を信頼する。悪意あるnative実装は、ランタイムの能力制限でしか止められない。
- 完全な情報フロー制御は決定不能な部分を含む。PROUTは「高リスクなフローを止める実用的な型システム」を目指す。
