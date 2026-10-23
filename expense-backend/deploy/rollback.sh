#!/usr/bin/env bash
# 手動一鍵回復到指定的版本（git 標籤或提交）。用法：deploy/rollback.sh <git-ref> [--dry-run]
# 在專案根目錄（有 docker-compose.yml 的地方）執行；入口代理由 .env 的 COMPOSE_PROFILES 決定（funnel 或 domain），沒設定就不會啟動代理。回復後會等健康檢查通過；失敗會明講並保留現況供你處理。
set -euo pipefail

REF="${1:-}"
DRY=0
[ "${2:-}" = "--dry-run" ] && DRY=1

if [ -z "$REF" ]; then
  echo "用法：deploy/rollback.sh <git-ref> [--dry-run]" >&2
  exit 2
fi
if ! [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || [[ "$REF" == *..* ]]; then
  echo "錯誤：版本名稱不合法：$REF" >&2
  exit 2
fi

run() { if [ "$DRY" -eq 1 ]; then echo "WOULD RUN: $*"; else "$@"; fi; }

echo "== 回復到 $REF =="
run git fetch --tags
run git checkout "$REF"
run docker compose up -d --build
echo "等待健康檢查 /api/v1/health（最多 60 秒）"
if [ "$DRY" -eq 1 ]; then
  echo "WOULD RUN: docker compose exec -T app python -c <GET /api/v1/health，最多重試 12 次、每次間隔 5 秒>"
  exit 0
fi
for _ in $(seq 1 12); do
  if docker compose exec -T app python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"; then
    echo "回復完成，健康檢查通過。"
    exit 0
  fi
  sleep 5
done
echo "錯誤：回復後健康檢查仍未通過，請檢查 docker compose logs app。" >&2
exit 1
