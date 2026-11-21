#!/usr/bin/env python3
"""
Render the PROUT Patterns growth report.

The report tells one story: two concrete apps (expense, PC lending) share an
approval workflow. The workflow is extracted into a pattern, published in the
registry, discovered by a query, and reused by the next app. The benchmark
compares the AI-authored lines before and after the pattern exists.
"""
from __future__ import annotations

import html
from pathlib import Path

from proutc import (
    DEFAULT_REGISTRY,
    bench_rows,
    find_patterns,
    load_registry,
)

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "build" / "growth.html"


def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def main() -> None:
    registry = load_registry(DEFAULT_REGISTRY)
    approval = registry["approval"]
    rows = bench_rows(registry)
    before = next(r for r in rows if r["case"] == "expense_before")
    after = next(r for r in rows if r["case"] == "expense_after")
    pc = next(r for r in rows if r["case"] == "pc_lending")

    reduction = round((1 - after["ai_authored_lines"] / before["ai_authored_lines"]) * 100)
    reuse_gain = round(after["reuse_ratio"] / before["reuse_ratio"], 1)
    approval_lines = sum(a["lines"] for a in approval.artifacts)

    matches = find_patterns("申請 承認", registry)
    score, match = matches[0] if matches else (0, approval)

    slot_rows = "".join(
        f"""
        <tr>
          <td><code>{esc(name)}</code></td>
          <td>{esc(spec.get('type', ''))}</td>
          <td>{'必須' if spec.get('required') else '任意'}</td>
          <td>{esc(spec.get('desc', ''))}</td>
        </tr>
        """
        for name, spec in approval.slots.items()
    )

    artifact_rows = "".join(
        f"""
        <div class="artifact">
          <code>{esc(a['path'])}</code>
          <span>{esc(a.get('desc', ''))}</span>
          <span class="lines">{a['lines']}行</span>
        </div>
        """
        for a in approval.artifacts
    )

    bench_rows_html = "".join(
        f"""
        <tr class="{'highlight' if r['case'] == 'expense_after' else ''}">
          <td class="mono">{esc(r['case'])}</td>
          <td class="num">{r['spec_lines']}</td>
          <td class="num">{r['custom_lines']}</td>
          <td class="num strong">{r['ai_authored_lines']}</td>
          <td class="num">{r['generated_lines']:,}</td>
          <td class="num strong">{r['reuse_ratio']}×</td>
          <td class="mono small">{esc(', '.join(r['patterns']))}</td>
        </tr>
        """
        for r in rows
    )

    css = """
    :root {
      --bg: #f4f1e9; --card: #fffdf8; --ink: #1e2328; --muted: #6d7280;
      --line: #e3ddd0; --green: #2f6f4e; --green-soft: #e7f2ea;
      --amber: #a85a08; --amber-soft: #fbf1e0; --blue: #2454b8; --blue-soft: #e8eefb;
      --purple: #6335b8; --purple-soft: #f0eafb;
      --shadow: 0 1px 2px rgba(30,35,40,.04), 0 8px 24px rgba(30,35,40,.05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; color: var(--ink); line-height: 1.75;
      background: radial-gradient(1200px 500px at 50% -200px, #fbf8f1 0%, rgba(251,248,241,0) 70%), var(--bg);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Noto Sans JP", "Yu Gothic", sans-serif;
      -webkit-font-smoothing: antialiased;
    }
    .wrap { max-width: 820px; margin: 0 auto; padding: 30px 20px 70px; }
    .kicker { font-family: ui-monospace, Menlo, monospace; font-size: 11px; letter-spacing: .16em; color: var(--green); margin-bottom: 10px; }
    h1 { font-size: 31px; line-height: 1.3; margin: 0 0 10px; letter-spacing: -.02em; }
    h2 { font-size: 15px; margin: 0 0 14px; }
    h3 { font-size: 16px; margin: 0 0 4px; }
    p { margin: 0 0 12px; }
    code { font-family: ui-monospace, Menlo, monospace; font-size: .88em; background: #f2eee4; padding: 2px 6px; border-radius: 5px; }
    .lead { color: #3b4148; font-size: 15px; max-width: 680px; }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 22px 0 18px; }
    .metric { background: var(--card); border: 1px solid var(--line); border-radius: 13px; padding: 14px 15px; box-shadow: var(--shadow); }
    .metric .value { font-size: 25px; font-weight: 750; letter-spacing: -.03em; line-height: 1.2; }
    .metric .label { font-size: 10px; color: var(--muted); letter-spacing: .04em; }
    .metric .accent { color: var(--green); }
    .panel { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 22px; margin-top: 18px; box-shadow: var(--shadow); }
    .panel h2 { display: flex; align-items: center; gap: 8px; }
    .panel h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--green); display: inline-block; }
    .loop { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .step { display: inline-flex; align-items: center; gap: 6px; background: #f8f5ee; border: 1px solid var(--line); border-radius: 999px; padding: 6px 11px; font-size: 12px; font-weight: 600; }
    .step span { font-family: ui-monospace, Menlo, monospace; font-size: 9px; color: var(--green); }
    .arrow { color: #b8b1a2; font-size: 12px; }
    .cases { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; align-items: stretch; }
    .case { border: 1px solid var(--line); border-radius: 12px; padding: 14px; background: #fffefa; }
    .case.common { background: var(--green-soft); border-color: #bcd9c6; }
    .case-label { font-family: ui-monospace, Menlo, monospace; font-size: 9px; letter-spacing: .12em; color: var(--muted); margin-bottom: 3px; }
    .case.common .case-label { color: var(--green); }
    .case p { font-size: 12px; color: #4b5563; margin-bottom: 8px; }
    .case .diff { font-size: 10.5px; color: var(--muted); border-top: 1px dashed var(--line); padding-top: 7px; }
    .case.common .diff { border-color: #cfe4d6; color: #2f5c45; }
    .slots { width: 100%; border-collapse: collapse; font-size: 12px; }
    .slots th { text-align: left; font-size: 10px; color: var(--muted); font-weight: 600; padding: 7px 8px; border-bottom: 1px solid var(--line); }
    .slots td { padding: 8px; border-bottom: 1px solid #f0ebe0; vertical-align: top; }
    .slots tr:last-child td { border-bottom: 0; }
    .artifact { display: grid; grid-template-columns: 1.4fr 1fr auto; gap: 10px; align-items: baseline; padding: 7px 0; border-bottom: 1px dashed #ece6d9; font-size: 11px; }
    .artifact:last-child { border-bottom: 0; }
    .artifact code { background: transparent; padding: 0; font-size: 10.5px; color: #334155; overflow-wrap: anywhere; }
    .artifact span { color: var(--muted); font-size: 10.5px; }
    .artifact .lines { font-family: ui-monospace, Menlo, monospace; white-space: nowrap; }
    .query { display: flex; gap: 8px; align-items: center; background: #20262b; border-radius: 9px; padding: 11px 13px; margin-bottom: 10px; }
    .query span { color: #6b7280; font-family: ui-monospace, Menlo, monospace; font-size: 11px; }
    .query code { background: transparent; color: #e8e4d9; padding: 0; font-size: 11.5px; }
    .result { border: 1px solid #bcd9c6; background: var(--green-soft); border-radius: 11px; padding: 13px 14px; }
    .result-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .result-name { font-weight: 750; font-size: 15px; }
    .result-score { font-family: ui-monospace, Menlo, monospace; font-size: 10px; color: var(--green); background: #d7ebdd; padding: 3px 7px; border-radius: 999px; }
    .result p { font-size: 12px; margin: 5px 0 0; color: #335243; }
    .result .slots-line { font-family: ui-monospace, Menlo, monospace; font-size: 10.5px; color: #2f5c45; margin-top: 6px; }
    table.bench { width: 100%; border-collapse: collapse; font-size: 11.5px; }
    table.bench th { text-align: left; font-size: 9.5px; color: var(--muted); font-weight: 600; padding: 7px 6px; border-bottom: 1px solid var(--line); }
    table.bench td { padding: 9px 6px; border-bottom: 1px solid #f0ebe0; }
    table.bench tr:last-child td { border-bottom: 0; }
    table.bench tr.highlight td { background: #f1f8f3; }
    .num { text-align: right; font-variant-numeric: tabular-nums; }
    .strong { font-weight: 750; color: var(--green); }
    .mono { font-family: ui-monospace, Menlo, monospace; }
    .small { font-size: 9.5px; color: var(--muted); }
    .flow { display: grid; gap: 8px; }
    .flow-step { display: flex; gap: 10px; align-items: baseline; }
    .flow-step .n { flex: 0 0 22px; width: 22px; height: 22px; border-radius: 50%; background: var(--green-soft); color: var(--green); display: flex; align-items: center; justify-content: center; font-family: ui-monospace, Menlo, monospace; font-size: 9px; font-weight: 700; }
    .flow-step .body { font-size: 12.5px; color: #3b4148; }
    .flow-step .body strong { color: var(--ink); }
    .codeblock { background: #20262b; border-radius: 10px; padding: 14px; overflow-x: auto; margin-top: 12px; }
    .codeblock pre { margin: 0; color: #e8e4d9; font-family: ui-monospace, Menlo, monospace; font-size: 10px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
    .footer { margin-top: 24px; font-size: 11px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 16px; }
    .hash { font-family: ui-monospace, Menlo, monospace; background: #eae5d9; padding: 3px 7px; border-radius: 5px; }
    @media (max-width: 680px) {
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .cases { grid-template-columns: 1fr; }
      h1 { font-size: 26px; }
      .wrap { padding: 24px 15px 56px; }
      .panel { padding: 18px 16px; }
      .artifact { grid-template-columns: 1fr auto; }
      .artifact span:not(.lines) { display: none; }
    }
    """

    html_doc = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PROUT Patterns — 言語が育つループ</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">PROUT PATTERNS / GROWTH REPORT</div>
  <h1>言語が育つループ</h1>
  <p class="lead">経費精算とPC貸出から「申請 → 承認 → 差し戻し」を抽出し、次のAIが短い仕様で再利用できるか。パターンをコードからデータへ移し、発見・使用・改良の一周を試作した。</p>

  <div class="metrics">
    <div class="metric"><div class="value">{before['ai_authored_lines']} → {after['ai_authored_lines']}</div><div class="label">AIが書く行数</div></div>
    <div class="metric"><div class="value accent">-{reduction}%</div><div class="label">SPEC + CUSTOM</div></div>
    <div class="metric"><div class="value">{before['reuse_ratio']}× → {after['reuse_ratio']}×</div><div class="label">REUSE RATIO</div></div>
    <div class="metric"><div class="value">{len(registry)}</div><div class="label">REGISTERED PATTERNS</div></div>
  </div>

  <div class="panel">
    <h2>一周のループ</h2>
    <div class="loop">
      <span class="step"><span>01</span>観察</span><span class="arrow">→</span>
      <span class="step"><span>02</span>共通部分を抽出</span><span class="arrow">→</span>
      <span class="step"><span>03</span>スロットで一般化</span><span class="arrow">→</span>
      <span class="step"><span>04</span>レジストリに公開</span><span class="arrow">→</span>
      <span class="step"><span>05</span>別のAIが発見</span><span class="arrow">→</span>
      <span class="step"><span>06</span>使用</span><span class="arrow">→</span>
      <span class="step"><span>07</span>改良を提案</span>
    </div>
  </div>

  <div class="panel">
    <h2>二つの事例、一つの共通部分</h2>
    <div class="cases">
      <div class="case">
        <div class="case-label">CASE 1</div>
        <h3>経費精算</h3>
        <p>申請 → 承認 → 支払い</p>
        <div class="diff">承認後の差分：支払い・仕訳の起票</div>
      </div>
      <div class="case common">
        <div class="case-label">COMMON</div>
        <h3>approval</h3>
        <p>申請 → 承認 → 差し戻し</p>
        <div class="diff">共通部分をパターン化。承認後は on_approved フックで接続</div>
      </div>
      <div class="case">
        <div class="case-label">CASE 2</div>
        <h3>PC貸出</h3>
        <p>申請 → 承認 → 在庫引き当て</p>
        <div class="diff">承認後の差分：在庫引き当て・貸出期間</div>
      </div>
    </div>
  </div>

  <div class="panel">
    <h2>パターンの中身</h2>
    <p style="font-size:12.5px;color:var(--muted);">`approval` v{esc(approval.version)} — {esc(approval.intent)}</p>
    <table class="slots">
      <thead><tr><th>スロット</th><th>型</th><th>必須</th><th>説明</th></tr></thead>
      <tbody>{slot_rows}</tbody>
    </table>
    <div style="margin-top:14px;">
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">効果</div>
      <div>{''.join(f'<code style="margin-right:5px;">{esc(e)}</code>' for e in approval.effects)}</div>
    </div>
    <div style="margin-top:14px;">
      <div style="font-size:11px;color:var(--muted);margin-bottom:4px;">生成物（{len(approval.artifacts)}ファイル / {approval_lines}行）</div>
      {artifact_rows}
    </div>
    <div style="margin-top:12px;font-size:11px;color:var(--muted);">抽出元: {esc(', '.join(approval.provenance.get('extracted_from', [])))}</div>
  </div>

  <div class="panel">
    <h2>発見テスト：元の会話を読まずに使えるか</h2>
    <p style="font-size:12.5px;color:var(--muted);">別のAIには、レジストリの <code>index.json</code> と新しい依頼だけを渡す。ここでは「申請 承認」で検索した結果を示す。</p>
    <div class="query"><span>$</span><code>proutc find "申請 承認"</code></div>
    <div class="result">
      <div class="result-head"><span class="result-name">{esc(match.name)} v{esc(match.version)}</span><span class="result-score">score {score}</span></div>
      <p>{esc(match.intent)}</p>
      <div class="slots-line">required: {esc(', '.join(k for k, v in match.slots.items() if v.get('required')))}  /  optional: {esc(', '.join(k for k, v in match.slots.items() if not v.get('required')))}</div>
      <div class="slots-line">examples: {esc(', '.join(match.examples))}</div>
    </div>
  </div>

  <div class="panel">
    <h2>計測：パターンがある場合とない場合</h2>
    <table class="bench">
      <thead><tr><th>CASE</th><th style="text-align:right">SPEC</th><th style="text-align:right">CUSTOM</th><th style="text-align:right">AI LINES</th><th style="text-align:right">GENERATED</th><th style="text-align:right">REUSE</th><th>PATTERNS</th></tr></thead>
      <tbody>{bench_rows_html}</tbody>
    </table>
    <p style="font-size:11.5px;color:var(--muted);margin-top:10px;">AIが書く行数は「仕様 + custom」。生成行数はパターン展開の試算。{after['case']} は {before['case']} より AI の記述量が {reduction}% 少なく、再利用倍率は {reuse_gain}倍になった。</p>
  </div>

  <div class="panel">
    <h2>改良の取り込み</h2>
    <div class="flow">
      <div class="flow-step"><div class="n">1</div><div class="body"><strong>提案</strong> — AIが新しいパターン、または既存パターンの修正をPull Requestで出す。</div></div>
      <div class="flow-step"><div class="n">2</div><div class="body"><strong>検証</strong> — CIが全サンプルと <code>proutc bench</code> を実行し、既存アプリへの影響を確認する。</div></div>
      <div class="flow-step"><div class="n">3</div><div class="body"><strong>レビュー</strong> — 人間はスロットと効果の差分だけを見る。生成物は読まない。</div></div>
      <div class="flow-step"><div class="n">4</div><div class="body"><strong>取り込み</strong> — バージョンを上げ、<code>proutc index</code> で <code>index.json</code> を更新する。</div></div>
    </div>
    <div class="codeblock"><pre>name: validate-patterns
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {{ python-version: "3.12" }}
      - run: python3 prout.py check examples/refund_fixed.prout
      - run: cd patterns && python3 proutc.py index
      - run: cd patterns && python3 proutc.py build examples/shop.prout
      - run: cd patterns && python3 proutc.py build examples/expense.prout
      - run: cd patterns && python3 proutc.py bench</pre></div>
  </div>

  <div class="panel">
    <h2>次の実験</h2>
    <p style="font-size:13px;">別のAIに、<strong>レジストリのURL</strong>と<strong>「備品購入の申請アプリを作って」</strong>だけを渡す。元の会話は渡さない。テストが通るまでの時間、修正回数、人間の介入回数を測る。</p>
    <p style="font-size:13px;color:var(--muted);">仮説：<code>approval</code> を知らないAIは承認フローを一から書く。知っているAIは <code>entity</code>、<code>approver</code>、<code>on_approved</code> の3つを埋めるだけで済む。差がこの言語の成長の測度になる。</p>
  </div>

  <div class="footer">
    <span>PROUT Patterns v0.2 — 言語が育つループ</span>
    <span>Author: 橋元 龍二 (Ryuji Hashimoto)</span>
    <span class="hash">registry {len(registry)} patterns</span>
  </div>
</div>
</body>
</html>
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html_doc, encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"reduction: {reduction}%  reuse gain: {reuse_gain}x  approval lines: {approval_lines}")


if __name__ == "__main__":
    main()
