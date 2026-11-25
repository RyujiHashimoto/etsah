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
| `ASK` | 人に承認を求めるツール結果 | 呼ばない。承認トークンがあるときだけ転送する |

`PROUT_APPROVAL_TOKEN` か `--approval-token` が空でないとき、`ASK` を転送します。このトークンは、この段階では「置いてあるか」だけを見ます。ゲートを選びません。`BLOCK` は覆しません。ログにも書きません。判定は stderr に JSON を 1 行ずつ出します。

```json
{"audit_id":"<SHA-256 の64桁>","tool":"mail.send_reply","verdict":"ASK","reason":"...","forwarded":false,"approved":false}
```

`audit_id` は契約の SHA-256 です。ソースが解析できないときは空です。

時間あたりの回数も、予算の残数も、ここでは減らしません。`on_approved` がその効果の唯一の呼び出しであることも証明しません。

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

手元の実験で `ASK` を通すときは、そのサーバの `env` にトークンを置きます。本物の秘密はリポジトリに入れません。

```json
"env": {
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

cron の実行、冪等キーの保存、予算の減算はしません。それらは契約の文面に残ります。ガードは実行時の最初の一歩で、呼び出しごとに 1 回判定し、契約のハッシュと一緒に残します。

英語の手順は [docs/en/mcp.md](en/mcp.md) です。
