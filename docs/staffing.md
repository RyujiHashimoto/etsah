# Staffing desk — English summary

This is a summary of the Japanese design note below. The Japanese text is the original and is unchanged. If the summary and the Japanese text disagree, the Japanese text wins.

This note is an industry example, not the center of the language. The language's broader aim is to check what an AI agent is allowed to do before it runs. A staffing desk is one place that shape shows up.

The repeating core is staff, client sites and openings, matching, individual dispatch contracts and working conditions, timesheets with client approval, invoices and payroll, and gross margin. Rules that come from Japan's dispatch statute (the conflict date, overtime, wages) are checked as configured values. The language does not embed statutory numbers. Judgments that are disputed stay in the Japanese note.

The contract layer has `Date` and `Decimal`. A date literal is `YYYY-MM-DD` with no spaces. A decimal looks like `1800.50`. The pattern layer still checks conflict dates and rates. The Japanese note below describes the current tree.

### What the contract layer already expresses

`examples/staffing_dates.prout` uses calendar dates and decimal yen:

- Bill rate ≥ pay rate as `Decimal`. `1500.5 >= 1800.50` is `E-PRE`. `2800.50 >= 1800` passes. An `Int` may be passed where a `Decimal` is expected.
- End date ≤ conflict date. `2029-04-02 <= 2029-04-01` is `E-PRE`. `Date - Date` is a number of days (`Int`). `Date + Int` shifts the date.
- `examples/staffing.prout` keeps the older integer-yen and day-count encoding. `1500 >= 1800` and day count `21641 <= 21640` are still `E-PRE`.
- Client approval: `Bool` `client_approved == true`. `false` is `E-PRE`.
- Invoice amount: `Money * Int` is `Money`.
- A monthly close must match `read(contracts)`, `write(invoices)`, `write(payroll)`, `emit(audit)`.
- A flow that reads a stored contract cannot prove field comparisons. It finishes with 3 `W-PRE` runtime guards and 0 errors.

The network allow list (default `stripe`) and missing or extra effects work as elsewhere.

### What the pattern layer adds

Existing patterns cover login and roles, lists, search, résumé upload, a margin dashboard, notification, and a generic approval transition. They do not know that a timesheet must be approved by the client site before billing, or that a bill rate must stay at or above the pay rate.

Four patterns were added. Both `patterns/examples/staffing.prout` (the desk) and `patterns/examples/staffing_desk.prout` (a short synthetic desk) build.

- `timesheet` — submit, then client approval. `approver` is required. States run `draft → submitted → client_approved`, with `returned` for a send-back. `overtime_cap_hours` is a `Duration`. Comparing hours worked with the cap stays a runtime check, because the contract layer has no time-of-day.
- `dispatch_contract` — staff, client, start, end, and conflict date are required. The only comparisons are `start_date <= end_date` and `end_date <= conflict_date`. The checker does not add three years to the start date. `exempt` of `indefinite` or `age60` past the conflict date is `W-POLICY` with the words "not a legal opinion", not an error. `none` past the date is `E-POLICY`.
- `staffing_billing` — `bill_rate` and `pay_rate` are non-negative decimals (`Rate`). `bill_rate >= pay_rate` is checked in the spec. Equal rates pass. `1800.50` and `1800.5` are the same value. A spec with no `timesheet` is `E-POLICY`. Social insurance, employment insurance, premiums, and commuting are not subtracted. A non-negative gap can still lose money after those. This check is not a determination of equal pay for equal work.
- `matching` — the staff skill field and the opening's required-skill field exist. It does not prove that the person's skills satisfy the opening.

`patterns/examples/staffing_broken.prout` fails three pattern checks: an end date after the conflict date, a timesheet with no approver, and a bill rate of `1500` under a pay rate of `1800.50`. `examples/staffing_broken.prout` fails three `E-PRE` checks: `1500 >= 1800`, day count `21641 <= 21640`, and `false == true`.

A local build of the current tree matches the measurements recorded in the Japanese section: the desk spec is 104 lines, about 3,383 generated lines, 32.5×; the short desk is 65 lines, about 1,525 generated lines, 23.5×. Run `python3 -m unittest discover -s tests -v` for the current test count.

### Left out of the language on purpose

The Japanese section lists what is not encoded as law: organization-level period limits and cooling-off, the full exception list, Article 36 caps and special clauses, equal pay against a comparator payroll, social-insurance rates, premium wages, and the full text of a working-conditions notice. `overtime_cap_hours` is a field for the agreement's own number. It is not a default inside the pattern.

### Owner questions

The Japanese section ends with questions for the owner: the shape of a `Date` literal, whether decimal rates stay split between the two layers, whether two conflict-date exceptions are enough, whether an Article 36 cap must not be a pattern default, whether equal pay becomes its own pattern, whether insurance rates become a slot, whether these four patterns satisfy the "seen twice" rule, and whether `requires_fields` should check types.

---

# 人材派遣の基幹を PROUT で書く

対象は、人材派遣会社の基幹で繰り返す部分である。スタッフ、派遣先と案件、マッチング、個別契約と就業条件、勤怠と派遣先承認、請求と給与、粗利。労働者派遣法まわり（抵触日、時間外、賃金）は、法令の数値を言語に埋め込まず、設定された規則として検査する。判断が分かれるところはこの文書に残す。

契約層は `Date` と `Decimal` を持つ。日付リテラルは空白なしの `YYYY-MM-DD`、小数は `1800.50` のような十進数である。パターン層の抵触日・単価検査はそのまま残る。

## 今の言語で既に書けること

### 契約層

`examples/staffing_dates.prout` は、暦日と小数円で次を表現する。

- 請求単価 >= 支払単価。`Decimal` で、`1500.5 >= 1800.50` は `E-PRE`、`2800.50 >= 1800` は静的に通る。整数は `Decimal` の引数に渡せる。
- 終了日 <= 抵触日。`2029-04-02 <= 2029-04-01` は `E-PRE`。`Date - Date` は日数の `Int`、`Date + Int` は日をずらした `Date`。
- `examples/staffing.prout` は、以前の整数円と通算日の書き方を残している。`1500 >= 1800` と通算日 `21641 <= 21640` も引き続き `E-PRE` になる。
- 派遣先承認。`Bool` の `client_approved == true`。`false` は `E-PRE`。
- 請求額の式。`Money * Int` は `Money` になる。`invoice_amount` の `impl rate * hours` は型が付く。
- 効果。月次締めは `read(contracts)`、`write(invoices)`、`write(payroll)`、`emit(audit)` と一致しなければならない。
- 保存済みの契約を読む flow は、フィールド比較を証明できない。`W-PRE` の実行時ガードが3件残り、エラーは0件で終了する。

ネットワークの許可リスト（既定は `stripe`）と、効果の過不足もそのまま使える。

### パターン層

基幹の器は、既存のパターンでかなり組める。

| 業務 | 既存パターン |
|---|---|
| ログインとロール（コーディネーター、派遣先、給与、スタッフ） | `auth` |
| スタッフ・案件の一覧と登録 | `crud` |
| 名前とスキル欄の検索 | `search` |
| 履歴書 | `upload` |
| 請求額・給与・粗利の画面 | `dashboard` |
| 派遣先への提出通知 | `notify` |
| 社内の申請→承認→差し戻し | `approval` |

`approval` は汎用の状態遷移であり、勤怠の「派遣先が承認するまで請求しない」、個別契約の抵触日、請求単価と支払単価の関係は知らない。

## ギャップ

契約層に残っている穴は次である。試作範囲の「時刻は対象外」「線形算術の事前条件のみ」は維持している。

1. **日付。** 空白なしの `2026-04-01` は `Date` である。空白を入れた `2026 - 04 - 01` は従来どおり整数の減算である。変数同士の `end <= conflict` は実行時ガードのままである。
2. **小数の時間単価。** `1800.50` は `Decimal`（`decimal.Decimal`）である。`1 / 2` と十進数の除算は定数畳み込みしない。`SPEC.md` が除算の意味を一つに決めない、という方針は整数除算については残している。
3. **金額リテラル。** `Money` は不透明型のままである。`Decimal` とは混ぜない。`Money` の引数に `2800` を渡すと `E-TYPE` になる。
4. **時刻。** 暦日の差はある。時刻、時間と分、月の営業日は無い。
5. **コレクション。** スキルの包含は実行時ガードに落ちる。マッチングの中身は証明できない。
6. **パターン層の型。** `requires_fields` はフィールド名だけを見る。`refund` は `status: String` と書くが、ショップの仕様は `OrderStatus` のまま通る。

## 方針

契約層の字句と線形算術には手を入れない。日付・期間・小数単価はパターンコンパイラのスロットとフィールド型として足し、マニフェストの `constraints` で仕様時に比較する。法令の閾値はスロットの値であり、マニフェストの定数ではない。

検査できないものは `custom` と `notes` に残し、エラーメッセージとこの文書で「検査していない」と書く。

## 追加したパターン

四つとも `patterns/examples/staffing.prout`（基幹）と `patterns/examples/staffing_desk.prout`（短期案件の合成例）でビルドが通る。

### `timesheet`

勤怠の提出と派遣先承認。`approver` は必須なので、承認ロールの無い勤怠は `E-SLOT` になる。状態は `draft → submitted → client_approved`、差し戻しは `returned`。`overtime_cap_hours` は `Duration`（`45` や `7h30m`）。非負でない文字列はスロットエラーになる。実労働時間と上限の比較は、時間が契約層に無いので実行時のままである。

### `dispatch_contract`

個別契約。スタッフ、派遣先、開始日、終了日、抵触日が必須。エンティティには `duties` と `workplace` を要求する。これは就業条件明示の全文ではない。

検査は次の二つだけである。

- `start_date <= end_date`
- `end_date <= conflict_date`

抵触日は派遣先が通知した日をスロットに書く。開始日に3年を足す計算はしない。`exempt` が `indefinite`（無期雇用）または `age60`（60歳以上）のとき、抵触日を超えてもエラーではなく `W-POLICY` になる。メッセージに "not a legal opinion" を付ける。`none` のまま超えていれば `E-POLICY`。

有期プロジェクト、日数限定、産休代替は列挙していない。例外の認定を言語がしないためである。

### `staffing_billing`

請求と給与。`bill_rate` と `pay_rate` は `Rate`（非負の十進数）。`bill_rate >= pay_rate` を仕様時に見る。等しい単価は通す。`1800.50` と `1800.5` は同じ値である。

同じ仕様に `timesheet` が無いと `E-POLICY`（勤怠の派遣先承認が無い）になる。社会保険、雇用保険、割増、交通費は引かない。差が非負でも、それらを引くと赤字になり得る。この検査は同一労働同一賃金の充足ではない。

### `matching`

スタッフのスキル欄と案件の必須スキル欄が存在することを見る。省略した `required_skills` スロットは、案件エンティティの `required_skills` フィールドを既定にする。スキルの中身が案件を満たすかは見ない。

## 壊した例

`patterns/examples/staffing_broken.prout` はパターン検査が3件で落とす。

1. 終了日 `2029-04-02` が抵触日 `2029-04-01` の後
2. `timesheet` に `approver` が無い
3. 請求単価 `1500` が支払単価 `1800.50` を下回る

`examples/staffing_broken.prout` は契約検査が3件の `E-PRE` で落とす。整数円 `1500 >= 1800`、通算日 `21641 <= 21640`、`false == true`。

## 検証

ローカルで次を確認した。数値は実行結果である。

- `python3 -m unittest discover -s tests -v` が通る。`tests/test_spec_contracts.py` の回帰を含めて成功する。
- `python3 prout.py check examples/staffing.prout` はエラー 0、警告 3（整数エンコードの実行時ガード）。
- `python3 prout.py check examples/staffing_dates.prout` はエラー 0、警告 4（保存した単価・日付・日数・承認）。
- `python3 prout.py check examples/staffing_broken.prout` は `E-PRE` が 3。
- `python3 prout.py check examples/staffing_dates_broken.prout` は `E-PRE` が 3（`1500.5 >= 1800.50`、`2029-04-02 <= 2029-04-01`、`false == true`）。
- `patterns/examples/staffing.prout` はエラー 0。仕様 104 行、生成見積もり 3,383 行、展開 32.5 倍。
- `patterns/examples/staffing_desk.prout` はエラー 0。仕様 65 行、生成見積もり 1,525 行、展開 23.5 倍。
- 壊したパターン仕様は上の3件だけで失敗する。
- `find "申請 承認"` の先頭は従来どおり `approval`、score 15。`勤怠`、`抵触日`、`請求 給与`、`マッチング` は新しいパターンを返す。
- 既存の shop、経費、出張、備品、PC貸出はエラー 0 のままである。

CI は通る仕様のビルドと、壊した仕様が落ちることの両方を実行する。

## 提案したが実装していないこと

1. **時刻と `Duration`。** 日数の差は `Date - Date` で `Int` になる。時間と分、営業日、タイムゾーンは無い。
2. **除算の定数畳み込み。** `1 / 2` も `1.0 / 2` も実行時ガードのままである。整数除算の意味は変えていない。
3. **`Money` の整数リテラル、および `Money` と `Decimal` の混合。** 混ぜると型エラーになる。`yen(amount: Int) -> Money` のような op は仕様側に残る。
4. **実労働時間 <= 協定上限** を契約の `pre` にする。上限の数値は仕様の定数であり、45 や 360 を検証器が知らない。
5. **スキル包含。** 集合の契約は、仕様が実行時ガードと明記している。マッチングの中身はそこに残す。
6. **`requires_fields` の型検査。** 今やると `status: String` と `OrderStatus` の既存仕様が落ちる。新しい `field_types` だけを厳密にした。
7. **勤怠の操作デモ。** `demo.html` は `approval` 専用のままである。`timesheet` の状態遷移も同じ機構で出せるが、今回は生成していない。

## 法令として埋め込んでいないこと

ここは未確認であり、実装の根拠にしていない。

- 事業所単位・組織単位の期間制限、抵触日の起算、クーリング期間。比較しているのは通知された日付だけである。
- 期間制限の例外の全体。スロットにあるのは `none`、`indefinite`、`age60` だけである。有期プロジェクト、日数限定、産休育休の代替などは入っていない。無期雇用と60歳以上を警告に下げることも、適用を認定したことにはならない。
- 36協定の上限時間、特別条項、月と年の関係。`overtime_cap_hours` は協定書の値を書く欄である。
- 同一労働同一賃金（派遣先の通常の労働者との均衡・均等）。請求単価と支払単価の大小は、その判定ではない。比較対象の賃金台帳はエンティティにしていない。
- 社会保険・雇用保険の加入要件、料率、標準報酬月額。年度と事業所で変わるため `custom` に残した。`insured` は文字列であり、加入判定はしない。
- 割増賃金の率、深夜・休日、通勤手当。粗利の式に入れていない。
- 就業条件明示の必須記載の全文。業務と就業場所だけをフィールド名で要求している。

## オーナーへの質問

1. 日付リテラルは空白なしの `YYYY-MM-DD` にした。`d"..."` は採っていない。空白付きは減算のままである。この形でよいか。
2. 小数は銭への固定ではなく `decimal.Decimal` にした。`Money` とは混ぜていない。この二段でよいか。
3. 抵触日の例外は `indefinite` と `age60` の二つで足りるか。警告のまま人が見る形でよいか。
4. 36協定の上限を、パターンの既定値にしない判断でよいか。会社ごとに協定が違う前提である。
5. 同一労働同一賃金を、次のパターン（比較対象の賃金を持つエンティティ）にするか、`custom` のままにするか。
6. 社会保険の料率テーブルをスロットにするか。今回は年度で変わるものとして外した。
7. パターンは「同じ形が二度出てから入れる」が、今回の四つは派遣業務の分析と、基幹仕様・短期デスク仕様の二例から入れた。この追加でよいか。
8. `requires_fields` を型検査に昇格させるか。既存仕様の `OrderStatus` をどう扱うかが先に要る。
