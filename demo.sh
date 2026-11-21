#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

echo "=============================================="
echo "1. AIの初稿（意図的に壊してある）"
echo "=============================================="
python3 prout.py check examples/refund_broken.prout || true

echo
echo "=============================================="
echo "2. 検証器の指摘を受けて修正した版"
echo "=============================================="
python3 prout.py check examples/refund_fixed.prout

echo
echo "=============================================="
echo "3. ASTから人間向けビューを生成"
echo "=============================================="
python3 render.py
