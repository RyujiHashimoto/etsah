# AIタスクの自動化

橋元龍二（J-PROUT）からの依頼は、AIに任せる作業を安全に宣言する機能を考案することである。J-PROUTの事業は、再生PCのレンタルと販売、ZPOOLのクラウド運用支援、AI導入とDXの支援である。人材派遣の基幹（`docs/staffing.md`）で繰り返す定型作業も、同じ境界で扱う。

この文書は設計提案である。契約層の `prout.py` は変更していない。検証器は別途編集中で、別ブランチが日付と小数をそこに足している。動く検査はパターン層に置いた。**要決定** と書いた箇所は、実装の前提にしていない。

## 今の言語で既に守れること

契約層は、効果を宣言しなければ実行できない。`eff` は `read`、`write`、`net`、`spend`、`emit` の5種類で、flow の宣言は呼び出す op の和集合と一致しなければならない。`net` の宛先は許可リスト（既定は `stripe`）に無いと `E-POLICY` になる。事前条件は呼び出し地点で検査される。正規化した AST には SHA-256 のハッシュが付く（`ast_hash` は先頭12桁を返す）。

パターン層の `approval` は、人が申請を承認し、承認後に `on_approved` を呼ぶ状態遷移である。`notify` はメールか Slack を送る。この二つは「人が始めた申請」の形であり、AIが夜中に自分で送る形ではない。

次は、今の言語には無い。

- いつ起動するか（cron、新しい行、webhook）
- AIが使ってよい効果の上限
- `spend` の1回と期間の金額、`net` と `emit` の時間あたり回数
- 社外への送信、支払い、契約変更を人が止めるゲート
- 再試行と、尽きたあとの人への引き渡し
- 同じキーの再実行を1回に畳むこと
- すべての AI の作用を、AST ハッシュ付きの監査行に残すこと

## 危険の分け方

作業を四つに分ける。phase 1 の検査は、この分け方に従う。

| 作用 | 例 | phase 1 |
|---|---|---|
| 社内の読み書き | 勤怠の下書き、在庫表、問い合わせの仕分け | AIが進めてよい |
| 社内への `emit` | 社内キューへのフラグ | ゲート無しで通す |
| 社外への `emit` と `net` | 督促メール、顧客への返信、外部API | `approval_gate` が無いと `E-POLICY` |
| `spend` と契約の変更 | 給与の支払い、請求の送付を伴う入金、個別契約の延長 | audience が internal でもゲートが要る |

社内への `emit` をゲート無しにしたのは、フラグを社内キューに書く作業を止めないためである。全社 Slack のように外に見えうる社内通知を同じ扱いにするかは **要決定**。

## 契約層への提案

ここは未実装である。`task` で始まる宣言は、今のパーサーが `unknown declaration` で落とす。テスト `test_contract_layer_rejects_task_declarations` がその境界を固定している。字句、型、線形算術には手を入れない。

形は、今の `op` と `flow` に揃える。トップレベルは桁0、本体はスペース2つ、文字列は引用符、効果は `kind(target)` の列である。

`agent` は、そのAIが持ってよい能力の封筒である。`task` は、封筒の中の一つの仕事で、手順は op を呼ぶ。`gate` は、人が許可するまで外部効果を止める。

```
agent staffing_clerk
  doc "勤怠の収集と下書きを行う。社外への送信と支払いは持たない"
  allow read(timesheets), read(contracts), write(drafts)
  budget run 0 period 0
  limit net 0 hour
  limit emit 0 hour
  audit ast_hash

task collect_timesheets(period: Id) -> Int
  agent staffing_clerk
  doc "未提出の勤怠を集め、社内の下書きに書く"
  trigger schedule "0 9 * * 1"
  eff read(timesheets), write(drafts)
  key period
  retry 2 escalate coordinator
  let open_count = timesheets.count_open(period)
  let draft = drafts.put(period, open_count)
  return open_count
```

社外へ出す仕事は、ゲートを名指しする。

```
agent staffing_mail
  doc "承認済みの文面だけを社外へ送る"
  allow read(timesheets), emit(mail)
  budget run 0 period 0
  limit net 0 hour
  limit emit 20 hour
  audit ast_hash

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

支払いと契約変更は、audience の語を足さずに効果の種類で止める案である。

```
policy
  external emit(mail), net(billing)
  payment spend(payroll)
  contract write(contracts)

gate pay_staff
  doc "給与の支払いは給与担当の承認後だけ行う"
  approver payroll
  before spend(payroll)
  on_approved payroll.transfer

task pay_approved_hours(period: Id) -> Bool
  agent payroll_clerk
  doc "承認済みの時間について、承認された金額だけを支払う"
  trigger schedule "0 10 1 * *"
  eff read(invoices), spend(payroll)
  gate pay_staff
  budget run 200000 period 2000000
  key period
  retry 0 escalate payroll
  let invoice = invoices.approved(period)
  let paid = payroll.transfer(invoice)
  return paid
```

`budget` の数値は整数円である。小数は、日付と一緒に別ブランチが契約層へ入れる候補なので、この提案は整数のままにしてある。銭で持つかは **要決定**。

起動は三種類である。

```
trigger schedule "0 9 * * 1"
trigger record Invoice
trigger webhook "inbound/inquiry"
```

`schedule` の文字列は、分・時・日・月・曜の5欄である。`*`、`a-b`、`*/n`、カンマ区切りを受け、`MON` のような名前は受けない。名前を許すかは **要決定**。`record` は、そのエンティティの新しい行である。更新でも起動するかは **要決定**。`webhook` は受け口の名前だけを見る。署名や共有秘密の検査は **要決定**。

検証器が契約層で止める規則は、次である。phase 1 ではパターン層が同じ趣旨をスロットで見ている。

1. `task` の `eff` は、呼んだ op の効果の和集合と一致する。これは今の flow と同じ `E-EFF` である。
2. `task` の `eff` は、`agent` の `allow` の部分集合である。超えた効果は `E-POLICY`。
3. `policy external` に入る `emit` または `net` を `eff` が含み、対応する `gate` の `before` がその効果を挙げていなければ `E-POLICY`。メッセージは、社外への emit に承認が無い、という趣旨にする。
4. `spend` は、`before spend(...)` のゲートが無いと `E-POLICY`。
5. `policy contract` の `write` は、`before` が契約を挙げたゲートが無いと `E-POLICY`。
6. `spend` を許す agent は、`budget run` と `budget period` が正の整数で、1回の上限は期間の上限以下である。許さない agent の予算は 0 である。
7. `limit emit` と `limit net` は、その効果を許すとき正の整数、許さないとき 0 である。単位は1時間である。
8. `retry` は 0 以上 5 以下。尽きたら `escalate` のロールへ渡し、その実行は外部効果を出さない。
9. `key` は引数か、対象の安定したフィールドである。同じ agent、task、key の外部効果は1回である。
10. `audit ast_hash` は agent に必須である。実行のたびに、時刻、agent、task、key、効果、ゲートの判断、プログラムの AST ハッシュを1行追記する。

エスカレーションの通知は、agent の `allow` には入れない。ランタイムが、`auth` のロールにいる人へ渡す。AIが「エスカレーション」の名目で顧客へメールする経路を作らないためである。この通知自体をゲート付きの `emit` にするかは **要決定**。

監査ハッシュは、今の `ast_hash` が返す12桁を使う案と、SHA-256 の64桁を保存する案がある。改ざん検知には64桁が厚い。表示には12桁で足りる。どちらを監査行の主キーにするかは **要決定**。

冪等キーは、プログラムのハッシュが変わっても外部効果をやり直さない、を初期値にする案である。ハッシュが変わったことは監査行に両方残す。仕様を直したあとに督促を再送したいかは **要決定**。

承認者は人のロールである。agent 自身を `approver` にできないようにする語彙は、まだ無い。**要決定**。

期間の境界（日次・週次・月次）のタイムゾーンは、J-PROUT の業務なら `Asia/Tokyo` が自然である。言語の既定にするかは **要決定**。

## パターン層に入れたもの

phase 1 は、上の規則のうち仕様時に見られるものを `patterns/proutc.py` に入れた。`prout.py` は読んでいない。

| パターン | 役割 |
|---|---|
| `ai_task` | 起動、使ってよい効果、予算、回数、手順、再試行、冪等キー、監査の宣言 |
| `approval_gate` | 人が許可する作用。名前を持ち、タスクの `approval` がそれを指す |

文法は今のパターン仕様のままである。`app`、`entity`、`pattern`、`custom`、インデントはスペース2つ。

```
pattern ai_task RemindTimesheet
  entity: Timesheet
  trigger: schedule
  schedule: 0 9 * * 1
  steps: draft_reminder
  effects: read, emit
  audience: external
  approval: RemindClient
  spend_per_run: 0
  spend_per_period: 0
  period: weekly
  net_per_hour: 0
  emit_per_hour: 20
  retries: 2
  escalate_to: coordinator
  idempotency_key: period_key
  audit: ast_hash

pattern approval_gate RemindClient
  entity: Timesheet
  approver: coordinator
  action: emit
  audience: external
  on_approved: send_reminder
```

`steps` は op の呼び出しではない。同じ仕様の `custom` か、適用済みパターンの名前である。契約層の「手順は op を呼ぶ」は phase 2 に残している。

`audit` は `ast_hash` だけを受け付ける。コンパイラはハッシュを計算しない。計算は契約層の `ast_hash` の仕事である。スロットは「監査行にそのハッシュを付ける」という宣言を、書き忘れられないようにしている。

`idempotency_key` はエンティティのフィールド名である。フィールドが無いと `E-ENTITY`。同じキーの二回目を実行時に畳むストアは、まだ無い。

`schedule` は5欄の数値 cron である。`trigger` が `schedule` のときだけ書き、`record` なら `on_record`、`webhook` なら `webhook` を書く。別のトリガーのスロットが残っていると `E-SLOT` になる。

`effects` は `read`、`write`、`net`、`spend`、`emit` の重複無しの列である。予算と回数は、宣言した効果と一致させる。

- `spend` があるとき、`spend_per_run` と `spend_per_period` は 1 以上で、1回は期間以下である。
- `spend` が無いとき、両方 0 である。
- `net` があるとき `net_per_hour` は 1 以上、無いとき 0。`emit` も同じ。
- `retries` は 0 以上 5 以下。`escalate_to` は、`auth.roles` がある仕様ではその中のロールである。

ゲートの対応は次である。タスクの `entity` とゲートの `entity`、`audience`、`action` が一致する。一つの作用にゲートは一つ。作用が二つなら `approval` に名前を二つ書く（`approval: SendInvoice, PayInvoice`）。

| 条件 | ゲートの `action` | 無いときの診断 |
|---|---|---|
| `effects` に `emit` があり `audience` が `external` | `emit` | `emit to an external party has no approval gate` |
| `effects` に `net` があり `audience` が `external` | `net` | `net to an external party has no approval gate` |
| `effects` に `spend` がある | `spend` | `spend has no approval gate` |
| `changes` が `contract` | `contract` | `a contract change has no approval gate` |

社内の読み書きだけなのに `approval` を書くと、`approval is set but this task has no gated effect` になる。ゲートを付けたつもりで、止める作用が無い状態を黙って通さない。

`approval_gate` のうしろには名前が要る（`pattern approval_gate RemindClient`）。同じ名前が二つあると `E-DUP`。`on_approved` は任意で、custom か適用済みパターンを指す。承認後の処理が、その外部効果の唯一の実行箇所であることまでは見ていない。これは **要決定** の穴である。phase 2 で、外部 op はゲートの `on_approved` からしか呼べない、に進める。

`demo.html` のボタンは、従来の `approval` のままである。`approval_gate` の承認画面は成果物の見積もりだけを出している。ボタンデモを同じ機構で出すかは **要決定**。

マニフェストの `automation` がこの横断規則をデータで持つ。`ai_task` 以外のパターンは空のままである。

## 実例

三つの業務を、二つの仕様に置いた。人材派遣が `patterns/examples/ai_automation.prout`、PCレンタルと問い合わせ仕分けが `patterns/examples/ai_desk.prout` である。どちらもエラー 0 でビルドできる。

### 人材派遣

| タスク | 起動 | 効果 | ゲート |
|---|---|---|---|
| 未提出の勤怠を集めて社内の下書きに書く | 毎週月曜 9:00 `0 9 * * 1` | `read`, `write` / internal | 無し |
| 抵触日が近い契約を社内キューに載せる | 毎日 8:00 `0 8 * * *` | `read`, `write` / internal | 無し |
| 督促の文面を作り、承認後に派遣先へ送る | 毎週月曜 9:00 | `read`, `emit` / external | `RemindClient`（`emit`） |
| 請求の下書きを作る。送付しない | 毎月1日 9:00 `0 9 1 * *` | `read`, `write` / internal | 無し |
| 承認済み時間の支払いを作る | 毎月1日 10:00 | `read`, `spend` / external | `PayStaff`（`spend`、1回 200000、期間 2000000） |
| 契約延長の案を作り、承認後に終了日を書く | 毎週月曜 11:00 | `read`, `write` / `changes: contract` | `ExtendTerm`（`contract`） |

抵触日のフラグは、日付を足さない。`dispatch_contract` が比較するのは、通知された日付どうしである。36協定の上限、社会保険、同一労働同一賃金も、このタスクは判断しない。`docs/staffing.md` の限界をそのまま残す。

請求は、AIが下書き（`write`）までで止まる。送付はゲートの `on_approved` に残す。支払いの `spend` は、給与担当のゲートが無いと仕様が通らない。1回の上限が期間の上限を超える例は、壊した仕様のほうに置いた。

### J-PROUTのPCレンタル

返却日が近いレンタルの文面は毎日 9:00 に作り、顧客へ出す `emit` は `RemindReturnMail`（承認者 `ops`）が要る。出荷できる再生PCの台数は、毎週月曜 7:00 に社内の在庫表へ書く。こちらは `read` と `write` だけで、ゲートは無い。

既存の `bench/pc_lending.prout` は、社員がPCを借りる申請を `approval` で回すベンチである。今回のタスクは、顧客へ返却を促し、在庫を数える運用である。申請の承認パターンは置き換えない。

### DX支援の問い合わせ仕分け

`Inquiry` に行が増えたとき（`trigger: record`）、本文をレンタル、クラウド運用、DXの社内キューへ分ける。効果は `read` と `write`、相手は internal。顧客への返信は、このタスクに入れていない。返信を `emit` にするなら、社外なのでゲートが要る。

ZPOOLの監視 webhook を同じ `trigger webhook` で受けるかは **要決定**。受け口の名前以外（署名、再送、アラートの重要度）は、この仕様に書いていない。

### 壊した仕様

`patterns/examples/ai_automation_broken.prout` は、ほかのスロットを揃えたうえで次の3件だけを出す。

1. 社外への `emit` にゲートが無い。`emit to an external party has no approval gate`
2. `spend_per_run` 5000 が `spend_per_period` 1000 を超える。`5000 <= 1000 is false`
3. `changes: contract` にゲートが無い。`a contract change has no approval gate`

## フェーズ

### Phase 1（この提案で入っているもの）

パターンの `ai_task` と `approval_gate`。トリガー、効果、予算、回数、再試行、冪等キーのフィールド、`audit: ast_hash` の宣言。社外への `emit` と `net`、`spend`、契約変更にゲートが無いと `E-POLICY`。二つの通る仕様と、一つの壊した仕様。単体テスト。

ランタイムは無い。cron は動かない。メールは送らない。予算のカウンタも無い。ハッシュも計算しない。

### Phase 2（契約層。`prout.py` はオーナーの編集と、日付・小数のブランチのあとに足す）

`agent`、`task`、`gate`、`policy` をパーサーと検証器に入れる。`task` の効果は op の和集合かつ `allow` の部分集合。外部 op はゲートの `on_approved` からしか呼べない。監査行の列に `ast_hash` の値を載せる。`demo.html` でゲートの承認前と承認後をボタンで見る、はここでもまだ **要決定**。

### Phase 3（実行）

スケジューラ、webhook の受け口、冪等ストア、期間ごとの `spend` カウンタ、`net` と `emit` の時間枠。再試行が尽きたら人のロールへ渡す。失敗の経路では外部効果を出さない。

## 検証

ローカルで次を確認した。数値は実行結果である。

- `python3 -m unittest discover -s tests -v` は 148 件がすべて成功する。以前 `expectedFailure` だった契約の穴は `tests/test_spec_contracts.py` で通常のテストになっている。
- `patterns/examples/ai_automation.prout` はエラー 0。仕様 163 行、生成見積もり 2,081 行、展開 12.8 倍。
- `patterns/examples/ai_desk.prout` はエラー 0。仕様 85 行、生成見積もり 1,100 行、展開 12.9 倍。
- 壊した仕様は上の 3 件の `E-POLICY` だけで失敗する。
- `find "申請 承認"` の先頭は従来どおり `approval`、score 15。`自動化` は `ai_task`、`承認ゲート` は `approval_gate` を返す。
- `python3 prout.py check examples/staffing.prout` と `examples/refund_fixed.prout` は、この変更の前と同じ結果である。`task` 宣言は契約層が拒否する。

## オーナーへの質問

1. 社外への `emit` と `net`、すべての `spend`、契約変更、の四つをゲート必須にする分け方でよいか。社内 `emit` をゲート無しのままにしてよいか。
2. 契約層の宣言は `agent`（能力の封筒）と `task`（一つの仕事）の二段でよいか。一段の `task` に `allow` を書く形の方がよいか。
3. 外部宛先の一覧は、`policy external emit(mail), net(billing)` のように契約層へ置く案でよいか。パターン層はタスクの `audience: external` で見ている。
4. `spend` の単位は整数円のままでよいか。小数を入れるブランチと、どちらが先に契約層へ入るか。
5. 再試行の上限 5、期間の既定タイムゾーン `Asia/Tokyo`、cron の名前（`MON`）を拒否する、でよいか。
6. 監査行に保存するハッシュは、今の12桁か、SHA-256 の64桁か。仕様を改訂したあとも、同じ冪等キーの外部効果はやり直さない、でよいか。
7. 承認者から agent を除外する語彙を、phase 2 の必須にするか。
8. エスカレーションの通知は、ランタイムが人のロールへ渡す内部の作用でよいか。それ自体をゲート付き `emit` にするか。
9. `on_approved` が外部効果の唯一の呼び出しであることまで、phase 2 で証明するか。
10. `demo.html` を `approval_gate` にも出すか。phase 1 は見積もりだけで、ボタンは出していない。
11. `record` は新しい行だけでよいか。更新、webhook の署名、ZPOOL の監視受け口は、別の仕様にするか。
12. パターンは「同じ形が二度出てから入れる」が、今回は人材派遣の仕様と、レンタル＋仕分けの仕様の二つに入れた。この追加でよいか。
