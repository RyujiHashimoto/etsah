# 勤怠・配属・請求単価の記入テンプレート

完成した仕様は [`examples/staffing.etsah`](../staffing.etsah) と [`examples/staffing_dates.etsah`](../staffing_dates.etsah) にある。ここは、派遣の担当がコピーして空欄を埋めるための短い契約である。会社、人、金額はすべて架空である。人の氏名は書かない。

時間外の上限、抵触日までの年数、社会保険料のように決まっていない法的な数値は置いていない。そのような箇所には「各社の規程や専門家に確認」と書いてある。設計の限界は [docs/staffing.md](../../docs/staffing.md)。

| 種類 | 空欄 | 記入例 |
|---|---|---|
| 勤怠 | [timesheet.etsah](timesheet.etsah) | [timesheet_filled.etsah](timesheet_filled.etsah) |
| 配属 | [assignment.etsah](assignment.etsah) | [assignment_filled.etsah](assignment_filled.etsah) |
| 請求単価 | [bill_rate.etsah](bill_rate.etsah) | [bill_rate_filled.etsah](bill_rate_filled.etsah) |

勤怠は、派遣先の承認と、期間の合計時間である。承認が `false` だと `E-PRE` になる。配属は、終了日が通知された抵触日以前であることを見る。開始日に年数を足す計算はしない。請求単価は、支払単価以上であることを見る。同一労働同一賃金の判定ではない。

```bash
python3 etsah.py check examples/templates/timesheet.etsah
python3 etsah.py check examples/templates/timesheet_filled.etsah
python3 etsah.py check examples/templates/assignment.etsah
python3 etsah.py check examples/templates/assignment_filled.etsah
python3 etsah.py check examples/templates/bill_rate.etsah
python3 etsah.py check examples/templates/bill_rate_filled.etsah
```

6つとも終了コードは 0 である。
