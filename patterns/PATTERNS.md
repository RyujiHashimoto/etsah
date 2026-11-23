# PROUT Patterns v0.1 — AIが速く書くためのパターン言語

## 前提：人間が欲しがる形は有限

Webアプリの依頼は、驚くほど同じ形の組み合わせでできている。認証、一覧、詳細、作成、編集、削除、検索、ページネーション、決済、返金、通知、ダッシュボード、ファイルアップロード。AIから見れば、これは有限のパターン集合と、その合成問題である。

長尾は確かにある。しかし長尾に時間を使う前に、頭の部分を一瞬で生成できる言語が必要になる。PROUT Patternsは、その「頭」を言語の第一級要素にしたものである。

## 設計：パターンを第一級にする

プログラムは「パターンの適用」の列である。AIはコードを書かない。エンティティを宣言し、パターンにスロットを埋める。コンパイラがデータベース、API、UI、テストを決定論的に展開する。

```
app shop
  stack nextjs, postgres, stripe
  freedom guided

  entity Order
    id: Id
    total: Money
    status: OrderStatus

  pattern crud Orders
    entity: Order
    access: admin
    search: [id, status]
    paginate: 20

  pattern refund
    entity: Order
    payment: stripe
    policy: admin_only
```

## 三つの速度

1. **書く速度** — 1行のパターンが数百行を生む。ボイラープレートを生成しない。
2. **検証する速度** — パターンのスロットは型付き。足りないスロット、存在しないエンティティ、権限の矛盾をコンパイル時に検出する。
3. **直す速度** — 生成物はパターンとスロットから決まる。修正は仕様の1行を変える。生成物を手で直さない。

## 自由度ダイヤル

- `strict` — パターンのみ。最も速く、最も検証しやすい。新規事業の立ち上げ向け。
- `guided` — パターン + `custom` ブロック。customには `why` が必須。既存業務ロジックとの接続向け。
- `open` — パターン + 任意のコード。最も自由だが、検証はPROUTの契約層に委ねる。

自由度は言語の外にあるのではなく、言語のモードとして明示する。これにより「速いが縛られる」と「自由だが遅い」のトレードオフを、プロジェクトごとに選べる。

## パターンライブラリ

| パターン | 生成物 | 必須スロット |
|---|---|---|
| `auth` | ユーザー、セッション、サインイン、ルート保護 | provider |
| `crud` | テーブル、一覧/詳細API、管理画面、バリデーション、テスト | entity |
| `checkout` | 決済セッション、Webhook、チェックアウト画面 | entity, payment |
| `refund` | 返金API、返金ボタン、監査ログ、テスト | entity, payment |
| `dashboard` | 指標API、集計、ダッシュボード画面 | entity, metrics |
| `search` | 検索API、フィルタUI、インデックス | entity, fields |
| `notify` | メール/Slack通知、テンプレート | channel |
| `upload` | 署名付きURL、アップロードUI、ウイルススキャン | entity, field |
| `approval` | 承認テーブル、申請/承認API、承認画面、状態遷移、テスト | entity, approver |
| `scene` | 選択肢で移動するHTMLの場面 | title, body |
| `activity_log` | 時系列イベントの追記・表示、テスト | entity, events |
| `archive` | アーカイブ状態、一覧からの除外、復元 | entity, timestamp |
| `assignment` | 担当者の割り当て、変更履歴 | entity, assignee |
| `checklist` | 確認項目、完了状態、テンプレート | entity, template |
| `comment_thread` | コメントの追加、時系列表示 | entity, author |
| `csv_export` | 列を指定したCSV出力 | entity, columns |
| `due_date` | 期限の表示、期限超過の判定 | entity, deadline |
| `labels` | 複数ラベルの付け外し、絞り込み | entity, vocabulary |
| `survey` | 対応後の評価記録、集計 | entity, scale |
| `work_queue` | 優先度順の作業一覧 | entity, priority |
| `timesheet` | 勤怠の提出、派遣先承認、時間外上限のスロット | entity, approver |
| `overtime_agreement` | 36協定の上限、警告（notify）と硬停止（reject）。一般則を既定かつ天井にする | entity, timesheet, warn_at_hours, on_warning, on_limit |
| `dispatch_contract` | 個別契約、就業条件、通知された抵触日と、開始日から計算した抵触日 | entity, staff, client, start_date, end_date, conflict_date |
| `staffing_billing` | 請求と給与、請求単価 >= 支払単価、勤怠承認の必須 | entity, timesheet, bill_rate, pay_rate |
| `matching` | スタッフと案件をスキル欄で結ぶ候補一覧 | staff, opening, skills |
`Date`（`YYYY-MM-DD`）、`Duration`（`7.5` または `7h30m`）、`Rate`（非負の十進数、円の時間単価）はパターン層のフィールド型とスロット型である。マニフェストの `constraints` がスロットの値を比較し、`field_types` がエンティティの型を見る。`requires_patterns` は、請求のように他のパターン（勤怠承認）が同じ仕様に無いとエラーにする。36協定の上限はスロットで、一般則の天井を超える値だけをエラーにする。抵触日は通知日に加え、仕様の派遣期間から計算する。どちらも実績データは見ない。人材派遣での使い方と、社労士の確認が要る箇所は `docs/staffing.md` に書いた。

パターンは `registry/<name>.json` のマニフェストとして定義する。`proutc.py index` が `registry/index.json` を作り、`proutc.py find` が `intent` と `tags` から候補を返す。AIはコードを読まずに、この索引とマニフェストだけでパターンを発見・適用できる。

## 展開の例

`crud Orders` は次を生成する。

```
db/schema.sql#orders
api/orders/route.ts
api/orders/[id]/route.ts
app/(admin)/orders/page.tsx
app/(admin)/orders/[id]/page.tsx
lib/validation/order.ts
lib/permissions.ts
tests/orders.test.ts
```

合計は約430行。仕様は7行。これが「高速に開発できる」の中身である。

## 人間向けデモ

`build` は `report.html` に加えて `demo.html` を生成する。承認パターンを含む仕様なら、申請→承認→差し戻しをブラウザで操作でき、承認後にだけ `on_approved` の処理が動くことを確認できる。サーバも外部依存も不要で、生成されたHTMLを開くだけでよい。

```bash
python3 proutc.py build examples/expense.prout --out build/expense
open build/expense/demo.html
```

デモは仕様の `entity`、`approver`、`on_approved` から決定論的に生成される。人間はパターン名とスロットを確認し、ボタンを押して状態遷移を見る。コードを読む必要はない。

## 状態遷移をデータで持つ

`approval` のマニフェストは `states` と `transitions` を持つ。`draft` から `pending`、`approved`、`returned`、`rejected` への遷移がデータとして定義され、`validate()` は遷移元・遷移先が `states` に含まれることを検査する。

`on_approved` は custom ブロックか、適用済みの別パターンを参照しなければならない。存在しない名前を書くと `E-HOOK` で止まる。この検査は `on_approved` だけでなく、`checkout.success` など `type: hook` のスロットすべてに適用される。パターンが持たないスロットを書いた場合は `W-SLOT` 警告を出し、黙って無視しない。

`approval` のフックは `notify` を想定しており、`channel` と `recipient` を引数として要求する。`notify` の `channel` が `email` / `slack` 以外なら `E-HOOK-ARG`、`recipient` が `auth.roles` に含まれなければ `E-HOOK-ARG` になる。`notify` を適用していない場合や、複数回適用して参照先が曖昧な場合も `E-HOOK` で止まる。

## なぜ「最も自由度が高い」と言えるのか

- パターンは追加できる。ユーザーもAIも、新しいパターンをライブラリに足せる。
- `custom` ブロックで任意のコードを埋め込める。PROUTの契約で効果と事前条件を宣言する。
- ターゲットは一つではない。Next.js、Rails、Django、Phoenixなど、同じ仕様から複数のスタックに展開できる。
- 生成物は決定論的で、差分が安定する。AIが何度生成しても同じ結果になる。

## 限界

- パターンに当てはまらないプロダクトは、`open` モードでも遅くなる。長尾は消えない。
- パターンの組み合わせ爆発は、テストの組み合わせ爆発になる。合成の検証が必要。
- 生成物の品質はパターンライブラリの品質に依存する。ライブラリは言語の一部である。
- `on_approved` が custom ブロックを指す場合、その引数スキーマまではまだ検証できない。標準パターンを指すフックの引数は検証できる。

## PROUTとの関係

PROUTは「何を保証するか」を書く契約言語。PROUT Patternsは「何を作るか」を書くパターン言語。パターンは契約を運び、`custom` ブロックはPROUTで検証される。二つは置き換えではなく、上下の層である。
