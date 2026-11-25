# MCP から PROUT を使う

Claude Desktop、Claude Code、Cursor、ChatGPT のコネクタなど、stdio の MCP を話すクライアントから PROUT の検査を呼べます。サーバは Python の標準ライブラリだけで動きます。`mcp` パッケージは要りません。Python 3.12 で足ります。

検査はソースを読むだけです。メールも、課金も、検査対象のツールの実行もしません。

```bash
python3 prout.py mcp
```

`prout-mcp` は同じサーバです。このリポジトリの `pyproject.toml` は、そのコンソールスクリプトと `prout` を登録するだけです。別の変更がパッケージ定義を足している場合は、二つのファイルを合わせてください。

## ツール

| ツール | 答え | 読み取り専用 |
|---|---|---|
| `prout_check` | 契約は通るか。タスクごとの判定は何か | はい |
| `prout_import_tools` | MCP または OpenAI のツール定義を `op` にするとどうなるか | はい |
| `prout_explain` | `E-POLICY` は何で、どう直すか | はい |
| `prout_check_plan` | このツール呼び出しの列は実行してよいか | はい |

四つとも `readOnlyHint` は true です。上流のサーバは呼びません。

`prout_check` は `ok`、診断（`code`、`line`、`message`）、`ast_hash`、`audit_id`、タスクごとの計画を返します。計画には判定が付きます。

| 判定 | 意味 |
|---|---|
| `ALLOW` | ゲートが要る効果が無く、そのタスクにエラーも無い |
| `ASK` | ゲートの効果を実行する前に、人が承認する。理由にゲートと承認者がある |
| `BLOCK` | そのタスクのエラー、または特定のタスクに結び付かないエラーがある |

`examples/agents/support_reply.prout` は `ASK` です。架空のチケット `T-1042` を読むことはでき、顧客へのメールは `support_lead` の承認を待ちます。壊した対は `BLOCK` で、メッセージは `emit to an external party has no approval gate` です。

`prout_import_tools` は MCP の `tools/list`（JSON-RPC の結果を包んだ形も含む）か、OpenAI の function tools を受けます。形が曖昧なときは `"kind": "mcp"` または `"kind": "openai"` を渡します。副作用が推論できない op は、`read`、`write`、`net`、`spend`、`emit` のすべてを持ち、`needs review` が付きます。使う前に `eff` 行を直してください。コマンドの `prout import` はこれまでどおりで、推論できない op は効果なしのまま、コメントだけが付きます。

`prout_explain` は `E-POLICY` のようなコードを受け、説明と直し方を返します。知らないコードは、知らないまま返します。

`prout_check_plan` は契約のソースと、ステップの列（`tool` または `name`、引数は任意）を受けます。`ok` は、すべてのステップが `ALLOW` のときだけ true です。引数の値は評価しません。見るのはツール名と、宣言された効果、agent の allow、net の許可リスト、タスクが名指ししたゲートです。

エラーが残っている契約は、すべてのステップが `BLOCK` です。通った契約では、宣言の無い呼び出しは `BLOCK`、ゲートが名指しされた効果は `ASK`、ゲートが要らず allow の中にある呼び出しは `ALLOW` です。`allow_net` は既定の許可リスト（`stripe`）を置き換えます。`prout check --allow-net` と同じです。

## ガード

`prout guard` は stdio のプロキシです。`tools/list` は上流の MCP サーバへ転送します。`tools/call` の前に、`prout_check_plan` と同じ規則で判定します。

```bash
python3 prout.py guard --contract examples/agents/support_reply.prout -- python3 path/to/upstream_server.py
```

| 判定 | クライアントが見るもの | 上流 |
|---|---|---|
| `ALLOW` | 上流の結果 | 転送する |
| `BLOCK` | `isError: true` のツール結果と理由 | 呼ばない |
| `ASK` | 人に承認を求めるツール結果 | 呼ばない。この呼び出しに合う署名付き承認があるときだけ転送する |

`ASK` を転送するには、`prout approve` が発行した署名が要ります。署名は HMAC-SHA256 で、鍵は標準ライブラリだけを使います。共有鍵は `PROUT_APPROVAL_KEY` か、`--approval-key-file` / `PROUT_APPROVAL_KEY_FILE` のファイルです。ファイルは 1 行で、先頭の BOM と末尾の改行 1 つは無視します。鍵の置き場所は一つだけにし、コマンド引数にもログにも書きません。

承認は次を縛ります。

| 項目 | 意味 |
|---|---|
| 承認者 | ゲートに書いてある `approver` と一致する |
| 契約 | `audit_id`（契約の SHA-256） |
| ゲート | そのツールのゲート付き効果をすべて覆う、タスクが名指ししたゲート |
| ツール | op の名前 |
| 引数 | 引数 JSON の SHA-256。キーの順は関係しない。`1` と `1.0` は別の値。無い引数は `{}` |
| 期限 | `exp`（Unix 秒）。`now >= exp` で期限切れ。最長 366 日 |
| 一度きり | `nonce` と `approval_id` をファイルに記録し、どちらかの再使用を拒否する |

署名が合わない、期限切れ、別の契約、別のゲート、別のツール、別の引数、承認者の不一致、再使用は、どれも転送しません。合わないトークンでは nonce を消費しません。転送すると決めたあとに上流が失敗しても、nonce は戻しません。`BLOCK` は署名があっても覆しません。

```bash
python3 prout.py approve \
  --contract examples/agents/support_reply.prout \
  --gate approve_reply \
  --op mail.send_reply \
  --approver support_lead \
  --args '{}' \
  --ttl 300
```

トークンは標準出力に 1 行です。`PROUT_APPROVAL_TOKEN` に渡します。`--approval-token` も読めますが、プロセス一覧に出るので、他の利用者のいるマシンでは使いません。ガード側には同じ鍵と、使用済みを書く `--nonce-file`（または `PROUT_APPROVAL_NONCE_FILE`）が要ります。一度きりの範囲は、同じファイルを見るガードの間だけです。

判定は stderr に JSON を 1 行ずつ出します。署名が通った承認は `approver_id` と `approval_id` を残します。鍵とトークンは残しません。

```json
{"audit_id":"<SHA-256 の64桁>","tool":"mail.send_reply","verdict":"ASK","reason":"...","forwarded":true,"approved":true,"approver_id":"support_lead","approval_id":"<16文字以上>","approval_mode":"signed","args_hash":"<SHA-256 の64桁>"}
```

`audit_id` は契約の SHA-256 です。ソースが解析できないときは空です。

以前の「空でない文字列なら `ASK` を転送する」動作は、既定では使いません。`--allow-legacy-approval-token` か `PROUT_ALLOW_LEGACY_APPROVAL_TOKEN=1` のときだけ残します。これは非推奨です。フラグがあると署名も引数も見ず、空でない文字列でそのプロセスのすべての `ASK` を転送します。nonce は記録しません。`BLOCK` はこれまでどおり転送しません。

`limit calls` と `limit spend` は、ガードがファイルの残数を減らします。保存先は `--ledger-file` か `PROUT_LEDGER_FILE` です。書き込みはロックの中で置き換えます。上限を超えた呼び出しは `BLOCK` で、署名付きの承認があっても通しません。`spend` の金額は、op の `spend from` が指す引数から十進数として読みます。読めない、正でない、台帳が要るのに無いときは `BLOCK` です。監査行には `remaining_calls` と `remaining_spend`、`spend_currency` を残します。整数円の `budget` と、`limit net` / `limit emit` の1時間あたりの上限は、これまでどおり残数を減らしません。

`on_approved` がその効果の唯一の呼び出しであることも証明しません。一つのゲートが、そのツールのゲート付き効果を全部は覆っていないとき、そのツールの `ASK` は署名では通せません。

## Claude Desktop

`claude_desktop_config.json` に置きます。パスはチェックアウトの場所に合わせます。

```json
{
  "mcpServers": {
    "prout": {
      "command": "python3",
      "args": ["/path/to/prout-lang/prout.py", "mcp"]
    }
  }
}
```

架空のサポート返信を、承認があるまで止める上流サーバの例です。

```json
{
  "mcpServers": {
    "harbor-lane": {
      "command": "python3",
      "args": [
        "/path/to/prout-lang/prout.py",
        "guard",
        "--contract",
        "/path/to/prout-lang/examples/agents/support_reply.prout",
        "--",
        "python3",
        "/path/to/upstream_server.py"
      ]
    }
  }
}
```

鍵と、使用済み nonce のファイルは、そのサーバの `env` に置きます。トークンは呼び出しのたびに `prout approve` で作り、ガードを起動するときの `PROUT_APPROVAL_TOKEN` に渡します。下の値は架空の例です。本物の鍵はリポジトリに入れません。

```json
"env": {
  "PROUT_APPROVAL_KEY_FILE": "/path/to/approval.key",
  "PROUT_APPROVAL_NONCE_FILE": "/path/to/used-approvals.json"
}
```

非推奨の互換フラグを使う実験だけ、空でない文字列をトークンにします。既定のガードはこれを承認にしません。

```json
"env": {
  "PROUT_ALLOW_LEGACY_APPROVAL_TOKEN": "1",
  "PROUT_APPROVAL_TOKEN": "example-approval"
}
```

## Cursor

プロジェクトの `.cursor/mcp.json` か、ユーザ設定の MCP に同じ形で書きます。

```json
{
  "mcpServers": {
    "prout": {
      "command": "python3",
      "args": ["prout.py", "mcp"]
    }
  }
}
```

作業ディレクトリがチェックアウトでないときは、`prout.py` を絶対パスにします。ガードの `args` は Claude Desktop の例と同じです。

## 汎用の MCP クライアント

stdio のサーバを起動できるクライアントなら、次で足ります。

```json
{
  "command": "python3",
  "args": ["/path/to/prout-lang/prout.py", "mcp"]
}
```

このチェックアウトで `pip install -e .` したあとなら、`command` を `prout-mcp` にし、`args` を空にできます。そのインストールは手元だけです。この変更でパッケージは公開しません。

通信は JSON-RPC 2.0 で、1 行に 1 個の JSON です。実装しているのは `initialize`、`tools/list`、`tools/call` です。サーバは、クライアントが送ったプロトコル版を返します。入力に `Content-Length` が付いていても読めます。返事は常に 1 行です。

## ここまででしないこと

cron の実行、上流の冪等キーの保存、整数円の `budget` の減算はしません。回数と十進金額の残数だけを台帳ファイルに数えます。ガードは呼び出しごとに 1 回判定し、契約のハッシュ、通した承認の承認者 ID・承認 ID、残りの回数と残りの金額を残します。使用済み nonce は承認の再使用を拒むための記録で、上流の結果を覚えるものではありません。

英語の手順は [docs/en/mcp.md](en/mcp.md) です。
