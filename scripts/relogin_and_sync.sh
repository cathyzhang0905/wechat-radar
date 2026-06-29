#!/bin/bash
# 本地重新扫码登录微信公众号平台，并把新 token 同步到 GitHub Actions secret
# 用法：./scripts/relogin_and_sync.sh
set -e

PROJ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJ"

set -a
source .env
set +a

.venv/bin/python3 main.py --login

echo ""
echo "正在同步新 token 到 GitHub..."
GH_PAT="$GH_PAT_SYNC" .venv/bin/python3 scripts/gh_secret_set.py "$GH_REPO" WECHAT_TOKEN_JSON --file token.json

echo "✓ 完成，云端下次运行会用新 token"
