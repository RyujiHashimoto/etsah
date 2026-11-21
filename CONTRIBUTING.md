# PROUTへの貢献

PROUTは「AIが書き、AIが検証し、人間が読む」ための言語の試作です。貢献の中心は、**新しいパターンをレジストリに追加すること**と、**既存パターンをより小さく一般的にすること**です。

## 開発の流れ

```bash
git clone https://github.com/RyujiHashimoto/prout-lang.git
cd prout-lang

# 契約言語の検証
python3 prout.py check examples/refund_fixed.prout
python3 render.py

# 単体テスト（標準ライブラリの unittest。追加の依存はない）
python3 -m unittest discover -s tests -v

# パターン層の検証
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

CIは上記と同じコマンドを実行します。ローカルで通れば、ほぼCIも通ります。

## パターンの提案

パターンはコードではなくJSONマニフェストです。`patterns/registry/<name>.json` を追加し、`index.json` を再生成してPull Requestを出してください。

```bash
cd patterns
# 既存マニフェストを雛形にする
cp registry/approval.json registry/my_pattern.json
# name, intent, tags, slots, artifacts を書き換える
python3 proutc.py index
python3 proutc.py find "やりたいこと"
python3 proutc.py build examples/your_app.prout
```

マニフェストに必要な項目は次のとおりです。

| 項目 | 内容 |
|---|---|
| `name` | パターン名。英小文字とアンダースコア |
| `version` | セマンティックバージョン |
| `intent` | 「何をしたいときに使うか」を一文で。検索の主キーになる |
| `tags` | 英語と日本語の両方。AIの検索語彙を広げる |
| `slots` | 必須・任意のスロット。`desc` はAIが読む |
| `effects` | `read`, `write`, `net`, `spend`, `emit`, `notify` のいずれか |
| `artifacts` | 生成物のパス、種類、概算行数、説明 |
| `examples` | 実際にビルドが通る仕様ファイル |
| `provenance` | 誰が、いつ、どのアプリから抽出したか |

### レビューの基準

パターンは「同じ形が二度以上現れた」ときにだけ追加します。一度きりの実装は `custom` に置きます。レビューでは次を確認します。

1. `intent` が一つの文で書かれ、アプリ固有の名前を含まない。
2. 必須スロットが最小限で、アプリ固有の値を要求しない。
3. `effects` が実際の生成物と一致している。
4. 生成物が決定論的で、同じ入力から同じ出力になる。
5. 二つ以上の利用例（実アプリ一つと、合成した仕様一つでよい）がある。
6. `python3 proutc.py bench` の数値が悪化していない。
7. 状態遷移を持つパターンは `states` と `transitions` を定義し、`on_approved` の参照先が custom か適用済みパターンになっている。

### バージョンの上げ方

- `patch` — 説明文の修正、生成物の行数更新。
- `minor` — 任意スロットや生成物の追加。既存の仕様はそのまま動く。
- `major` — 必須スロットの追加、`effects` の変更。既存の仕様が動かなくなる。

## AIが生成したPull Request

AIが提案する場合も、人間のレビューを必須にします。PRの説明には次を含めてください。

- 使用したモデルと、生成した日時。
- `proutc.py build` と `bench` の実行結果（貼り付け）。
- パターンを抽出した元のアプリと、共通部分だと判断した理由。
- 一般化できず `custom` に残した部分。

## 契約言語への変更

`prout.py` の構文・型・効果・事前条件の意味を変える場合は、`SPEC.md` を同じPRで更新してください。既存の `.prout` ファイルが通らなくなる変更は、`major` として扱います。

## ライセンス

貢献はMITライセンスの下で受け入れます。著作権は橋元 龍二（Ryuji Hashimoto）に帰属します。
