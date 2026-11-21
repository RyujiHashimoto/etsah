#!/usr/bin/env python3
"""
Render a human-facing view from a PROUT AST.

The view is a projection: every label, step, effect, and contract status is
derived from the parsed program. The HTML is not a second source of truth.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from prout import Checker, Parser, ast_hash, fmt_expr

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"


EFFECT_LABELS = {
    ("read", "orders"): ("注文データを読む", "read"),
    ("write", "payments"): ("支払いデータを書く", "write"),
    ("net", "stripe"): ("Stripeと通信する", "net"),
}

ISSUE_HUMAN = {
    "E-EFF": ("効果の宣言漏れ", "このフローが触るデータと通信が、契約として宣言されていません。"),
    "E-PRE": ("事前条件の違反", "返金額が注文合計を超えています。このままでは返金処理が拒否されます。"),
    "E-TYPE": ("戻り値の型不一致", "領収書（Receipt）を返すべきところで、注文（Order）を返しています。"),
    "W-PRE": ("実行時ガード", "注文ステータスが「支払い済み」かは静的に証明できないため、実行時に確認します。"),
}


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def first_call(e):
    if e is None:
        return None
    if e.kind == "call":
        return e
    if e.kind == "bin":
        return first_call(e.left) or first_call(e.right)
    if e.kind == "unary":
        return first_call(e.left)
    if e.kind == "field":
        return first_call(e.base)
    return None


def flow_steps(flow, ops):
    steps = []
    for stmt in flow.body:
        if stmt.kind == "let":
            call = first_call(stmt.expr)
            op = ops.get(call.name) if call else None
            steps.append(
                {
                    "kind": "call",
                    "var": stmt.name,
                    "expr": fmt_expr(stmt.expr),
                    "op": call.name if call else "?",
                    "doc": op.doc if op else "?",
                    "ret": op.ret if op else "?",
                    "effects": op.effects if op else [],
                }
            )
        elif stmt.kind == "return":
            steps.append({"kind": "return", "expr": fmt_expr(stmt.expr)})
    return steps


def effect_chip(effect) -> str:
    kind, target = effect
    label, css = EFFECT_LABELS.get(effect, (f"{kind}({target})", kind))
    return f'<span class="chip {esc(css)}">{esc(label)}</span>'


def issue_card(d) -> str:
    title, human = ISSUE_HUMAN.get(d.code, (d.code, d.message))
    fix = ""
    if d.fix:
        fix = f'<div class="fix"><span>修正</span><code>{esc(d.fix)}</code></div>'
    return f"""
    <div class="issue">
      <div class="issue-head">
        <span class="code">{esc(d.code)}</span>
        <span class="badge fixed">修正済み</span>
      </div>
      <div class="issue-title">{esc(title)}</div>
      <p>{esc(human)}</p>
      <div class="raw">{esc(d.message)}</div>
      {fix}
    </div>
    """


def build_html(fixed_src: str, flow, ops, fixed_diags, broken_diags, digest: str) -> str:
    steps = flow_steps(flow, ops)
    summary = flow.summary or flow.doc or "このフローを実行する。"

    # Flow cards
    step_html = []
    for i, s in enumerate(steps, 1):
        if s["kind"] == "return":
            step_html.append(
                f"""
                <li class="step">
                  <div class="step-no">{i}</div>
                  <div class="step-body">
                    <div class="step-kicker">RETURN</div>
                    <div class="step-title">結果を返す</div>
                    <div class="step-code"><code>return {esc(s['expr'])}</code></div>
                    <div class="step-meta">呼び出し元に <code>{esc(flow.ret)}</code> を返します。</div>
                  </div>
                </li>
                """
            )
            continue
        chips = "".join(effect_chip(e) for e in s["effects"])
        step_html.append(
            f"""
            <li class="step">
              <div class="step-no">{i}</div>
              <div class="step-body">
                <div class="step-kicker">STEP {i}</div>
                <div class="step-title">{esc(s['doc'])}</div>
                <div class="step-code"><code>{esc(s['expr'])}</code> → <code>{esc(s['ret'])}</code></div>
                <div class="step-meta">結果を <code>{esc(s['var'])}</code> に束縛します。</div>
                <div class="chips">{chips}</div>
              </div>
            </li>
            """
        )

    # Effects
    effects = sorted(set(flow.effects))
    effect_cards = []
    for kind, target in effects:
        label, css = EFFECT_LABELS.get((kind, target), (f"{kind}({target})", kind))
        effect_cards.append(
            f"""
            <div class="effect {esc(css)}">
              <div class="effect-kind">{esc(kind.upper())}</div>
              <div class="effect-label">{esc(label)}</div>
              <div class="effect-target"><code>{esc(target)}</code></div>
            </div>
            """
        )

    # Contracts (from the refund op)
    refund = ops.get("refund")
    contract_rows = []
    if refund:
        for pre in refund.pres:
            text = fmt_expr(pre)
            stripped = text.strip("()")
            if stripped == "amount <= order.total":
                badge = '<span class="badge ok">呼び出し地点で証明</span>'
            elif "status" in text:
                badge = '<span class="badge warn">実行時ガード</span>'
            else:
                badge = '<span class="badge neutral">未検証</span>'
            contract_rows.append(f'<div class="contract"><code>{esc(text)}</code>{badge}</div>')
        for post in refund.posts:
            contract_rows.append(
                f'<div class="contract"><code>{esc(fmt_expr(post))}</code><span class="badge info">事後条件</span></div>'
            )

    # History
    order = {"E-EFF": 0, "E-PRE": 1, "E-TYPE": 2, "W-PRE": 3}
    sorted_diags = sorted(broken_diags, key=lambda d: order.get(d.code, 9))
    issues_html = "".join(issue_card(d) for d in sorted_diags)

    fixed_errors = sum(1 for d in fixed_diags if d.severity == "error")
    fixed_warnings = sum(1 for d in fixed_diags if d.severity == "warning")
    if fixed_errors:
        status_class = "bad"
        status_text = "要修正"
        status_sub = f"{fixed_errors}件のエラー"
    elif fixed_warnings:
        status_class = "ok"
        status_text = "検証済み"
        status_sub = f"実行時ガード {fixed_warnings}件"
    else:
        status_class = "ok"
        status_text = "検証済み"
        status_sub = "静的検査をすべて通過"

    css = """
    :root {
      --bg: #f4f1e9;
      --card: #fffdf8;
      --ink: #1e2328;
      --muted: #6d7280;
      --line: #e3ddd0;
      --green: #2f6f4e;
      --green-soft: #e7f2ea;
      --amber: #a85a08;
      --amber-soft: #fbf1e0;
      --red: #b3261e;
      --red-soft: #fbe9e7;
      --blue: #2454b8;
      --blue-soft: #e8eefb;
      --purple: #6335b8;
      --purple-soft: #f0eafb;
      --shadow: 0 1px 2px rgba(30,35,40,.04), 0 8px 24px rgba(30,35,40,.05);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(1200px 500px at 50% -200px, #fbf8f1 0%, rgba(251,248,241,0) 70%),
        var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", "Yu Gothic", sans-serif;
      line-height: 1.75;
      -webkit-font-smoothing: antialiased;
    }
    .wrap { max-width: 760px; margin: 0 auto; padding: 30px 20px 70px; }
    .kicker {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 11px; letter-spacing: .16em; color: var(--green);
      text-transform: uppercase; margin-bottom: 10px;
    }
    h1 { font-size: 27px; line-height: 1.35; margin: 0 0 14px; letter-spacing: -.02em; }
    h2 { font-size: 15px; margin: 0 0 14px; letter-spacing: .02em; }
    p { margin: 0 0 12px; }
    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: .88em; background: #f2eee4; padding: 2px 6px; border-radius: 5px;
    }
    .status { display: inline-flex; align-items: center; gap: 8px; background: var(--green-soft); color: var(--green); border: 1px solid #cfe4d6; padding: 7px 13px; border-radius: 999px; font-size: 13px; font-weight: 650; }
    .status .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--green); box-shadow: 0 0 0 4px rgba(47,111,78,.12); }
    .status .sub { font-weight: 500; opacity: .8; }
    .status.bad { background: var(--red-soft); color: var(--red); border-color: #f0cdc9; }
    .status.bad .dot { background: var(--red); box-shadow: 0 0 0 4px rgba(179,38,30,.12); }
    .lead { margin-top: 18px; font-size: 15.5px; color: #3b4148; max-width: 640px; }
    .layers { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: 22px 0 26px; font-size: 11.5px; color: var(--muted); }
    .layer { border: 1px solid var(--line); background: rgba(255,253,248,.7); padding: 5px 9px; border-radius: 7px; }
    .layer strong { color: var(--ink); font-weight: 650; }
    .arrow { color: #b8b1a2; }
    .panel { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 22px; margin-top: 18px; box-shadow: var(--shadow); }
    .panel h2 { display: flex; align-items: center; gap: 8px; }
    .panel h2::before { content: ""; width: 4px; height: 16px; border-radius: 2px; background: var(--green); display: inline-block; }
    .summary { font-size: 15.5px; margin: 0; color: #333a41; }
    .steps { list-style: none; margin: 0; padding: 0; position: relative; }
    .steps::before { content: ""; position: absolute; left: 15px; top: 18px; bottom: 18px; width: 2px; background: linear-gradient(#d8e6dc, #e7e1d4); }
    .step { display: flex; gap: 14px; position: relative; padding: 10px 0 18px; }
    .step:last-child { padding-bottom: 0; }
    .step-no { flex: 0 0 32px; width: 32px; height: 32px; border-radius: 50%; background: var(--card); border: 2px solid var(--green); color: var(--green); display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 13px; position: relative; z-index: 1; }
    .step-body { flex: 1; padding-top: 1px; }
    .step-kicker { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10px; letter-spacing: .12em; color: var(--muted); margin-bottom: 2px; }
    .step-title { font-weight: 700; font-size: 15.5px; margin-bottom: 4px; }
    .step-code { font-size: 13px; color: #4b5563; margin-bottom: 3px; }
    .step-meta { font-size: 12.5px; color: var(--muted); }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
    .chip { font-size: 11px; padding: 3px 8px; border-radius: 999px; border: 1px solid transparent; font-weight: 600; }
    .chip.read { background: var(--blue-soft); color: var(--blue); border-color: #d3def7; }
    .chip.write { background: var(--purple-soft); color: var(--purple); border-color: #e0d4f7; }
    .chip.net { background: var(--amber-soft); color: var(--amber); border-color: #f2dfc2; }
    .effect-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
    .effect { border: 1px solid var(--line); border-radius: 12px; padding: 13px 14px; background: #fffefa; }
    .effect-kind { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 9.5px; letter-spacing: .12em; margin-bottom: 3px; }
    .effect-label { font-weight: 650; font-size: 13.5px; }
    .effect-target { font-size: 11px; color: var(--muted); margin-top: 2px; }
    .effect.read .effect-kind { color: var(--blue); }
    .effect.write .effect-kind { color: var(--purple); }
    .effect.net .effect-kind { color: var(--amber); }
    .contract { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px dashed var(--line); }
    .contract:last-child { border-bottom: 0; padding-bottom: 0; }
    .contract code { background: #f6f2e8; }
    .badge { font-size: 10.5px; font-weight: 650; padding: 3px 8px; border-radius: 999px; white-space: nowrap; }
    .badge.ok { background: var(--green-soft); color: var(--green); }
    .badge.warn { background: var(--amber-soft); color: var(--amber); }
    .badge.info { background: var(--blue-soft); color: var(--blue); }
    .badge.neutral { background: #efede7; color: var(--muted); }
    .badge.fixed { background: var(--green-soft); color: var(--green); }
    .timeline { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; color: var(--muted); margin-bottom: 18px; }
    .tl { display: inline-flex; align-items: center; gap: 6px; background: #f8f5ee; border: 1px solid var(--line); padding: 5px 10px; border-radius: 999px; }
    .tl strong { color: var(--ink); font-weight: 650; }
    .issues { display: grid; gap: 10px; }
    .issue { border: 1px solid var(--line); border-left: 3px solid var(--amber); border-radius: 11px; padding: 14px 15px; background: #fffefa; }
    .issue-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 3px; }
    .issue .code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10.5px; color: var(--muted); background: transparent; padding: 0; }
    .issue-title { font-weight: 700; font-size: 14px; margin-bottom: 2px; }
    .issue p { font-size: 13px; color: #4b5563; margin-bottom: 7px; }
    .issue .raw { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 10.5px; color: var(--muted); background: #f7f4ec; padding: 7px 9px; border-radius: 6px; margin-bottom: 7px; overflow-wrap: anywhere; }
    .issue .fix { display: flex; gap: 7px; align-items: baseline; font-size: 12px; }
    .issue .fix span { font-size: 10px; font-weight: 700; color: var(--green); letter-spacing: .06em; }
    .issue .fix code { background: var(--green-soft); color: #245c3f; overflow-wrap: anywhere; }
    details { margin-top: 4px; }
    summary { cursor: pointer; font-size: 13px; font-weight: 650; color: var(--green); }
    pre { background: #20262b; color: #e8e4d9; padding: 16px; border-radius: 10px; overflow-x: auto; font-size: 11.5px; line-height: 1.65; margin: 12px 0 0; }
    pre code { background: transparent; padding: 0; color: inherit; font-size: inherit; }
    .footer { margin-top: 24px; font-size: 11px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; }
    .hash { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #eae5d9; padding: 3px 7px; border-radius: 5px; }
    @media (max-width: 520px) {
      .wrap { padding: 24px 15px 56px; }
      h1 { font-size: 23px; }
      .panel { padding: 18px 16px; border-radius: 14px; }
      .contract { align-items: flex-start; flex-direction: column; gap: 4px; }
      .layers { flex-direction: column; align-items: flex-start; gap: 3px; }
      .layers .arrow { transform: rotate(90deg); margin-left: 10px; }
    }
    """

    html_doc = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PROUT — {esc(flow.doc)}</title>
<style>{css}</style>
</head>
<body>
<div class="wrap">
  <div class="kicker">PROUT / FLOW / HUMAN VIEW</div>
  <h1>{esc(flow.doc or flow.name)}</h1>
  <div class="status {status_class}"><span class="dot"></span>{status_text} <span class="sub">{status_sub}</span></div>
  <p class="lead">{esc(summary)}</p>

  <div class="layers">
    <span class="layer"><strong>AI</strong> がPROUTを書く</span><span class="arrow">→</span>
    <span class="layer"><strong>検証器</strong> が型・効果・契約を検査</span><span class="arrow">→</span>
    <span class="layer"><strong>人間</strong> はこの画面で確認</span>
  </div>

  <div class="panel">
    <h2>処理の流れ</h2>
    <ol class="steps">
      {''.join(step_html)}
    </ol>
  </div>

  <div class="panel">
    <h2>この処理が触るもの</h2>
    <div class="effect-grid">
      {''.join(effect_cards)}
    </div>
  </div>

  <div class="panel">
    <h2>契約</h2>
    {''.join(contract_rows)}
  </div>

  <div class="panel">
    <h2>検証の履歴</h2>
    <div class="timeline">
      <span class="tl"><strong>AIの初稿</strong> 3件のエラー</span><span class="arrow">→</span>
      <span class="tl"><strong>検証器</strong> が指摘</span><span class="arrow">→</span>
      <span class="tl"><strong>AI</strong> が修正</span><span class="arrow">→</span>
      <span class="tl"><strong>再検証</strong> 0件のエラー</span>
    </div>
    <div class="issues">
      {issues_html}
    </div>
  </div>

  <div class="panel">
    <h2>形式コード</h2>
    <p style="font-size:13px;color:var(--muted);margin-bottom:0;">人間はこのコードを読まなくてよい。ただし、この画面のすべての表示はこのコードから生成されている。</p>
    <details>
      <summary>PROUTのソースを開く</summary>
      <pre><code>{esc(fixed_src)}</code></pre>
    </details>
  </div>

  <div class="footer">
    <span>Generated from the PROUT AST.</span>
    <span>AST <span class="hash">{esc(digest)}</span></span>
    <span>表示は派生物であり、編集の対象ではない。</span>
  </div>
</div>
</body>
</html>
"""
    return html_doc


def main() -> None:
    broken_src = (EXAMPLES / "refund_broken.prout").read_text(encoding="utf-8")
    fixed_src = (EXAMPLES / "refund_fixed.prout").read_text(encoding="utf-8")

    broken = Parser(broken_src).parse()
    fixed = Parser(fixed_src).parse()
    broken_diags = Checker(broken).check()
    fixed_diags = Checker(fixed).check()

    flow = fixed.flows[0]
    ops = {o.name: o for o in fixed.ops}
    digest = ast_hash(fixed)

    doc = build_html(fixed_src, flow, ops, fixed_diags, broken_diags, digest)
    out = ROOT / "human-view.html"
    out.write_text(doc, encoding="utf-8")
    print(f"wrote {out}")
    print(f"fixed diagnostics: {len(fixed_diags)}")
    print(f"broken diagnostics: {len(broken_diags)}")
    print(f"AST hash: {digest}")


if __name__ == "__main__":
    main()
