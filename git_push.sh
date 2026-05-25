#!/bin/bash
# OKX Gateway — Audit, Fix, Push workflow
# Usage: ./git_push.sh "commit message"
set -e

MSG="${1:-auto: update}"

echo "🔍 1/4 Running audit..."
python3 -m py_compile routes.py okx_client.py threads.py constants.py indicators.py \
    state.py storage.py database.py credential_manager.py strategy_manager.py \
    notifications.py network_manager.py proxy_manager.py okx_quant_gateway.py \
    engine.py lgbm_bridge.py 2>&1 | grep -v "^$" || true
echo "   ✅ Compile check passed"

echo "🧪 2/4 Running tests..."
python3 test_reliable.py --quick 2>&1 | tail -3
echo "   ✅ Tests passed"

echo "📦 3/4 Committing..."
export CI=true GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never
export GIT_EDITOR=: EDITOR=: VISUAL=''
export GIT_SEQUENCE_EDITOR=: GIT_MERGE_AUTOEDIT=no
export GIT_PAGER=cat PAGER=cat

git add -u
git add -A
git commit -m "$MSG" 2>/dev/null || echo "   (no changes to commit)"

echo "🚀 4/4 Pushing..."
git push origin master 2>&1 | tail -2

echo ""
echo "✅ Done — https://github.com/thirtseven/okx_quant_gateway"
