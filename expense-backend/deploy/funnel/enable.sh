#!/usr/bin/env bash
# 啟動 funnel 路線：啟動 app 與本機 8080 的 Caddy，再用 Tailscale Funnel 把 https 轉進來。
# 用法：deploy/funnel/enable.sh [--dry-run]。前置條件見 deploy/funnel/README.md。
set -euo pipefail

DRY=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    *) echo "用法：enable.sh [--dry-run]（不認得的參數：$arg）" >&2; exit 2 ;;
  esac
done

if [ "$DRY" -eq 0 ] && ! command -v tailscale >/dev/null 2>&1; then
  echo "錯誤：找不到 tailscale 指令，請先安裝並登入（見 deploy/funnel/README.md）" >&2
  exit 2
fi

run() { if [ "$DRY" -eq 1 ]; then echo "WOULD RUN: $*"; else "$@"; fi; }

run docker compose --profile funnel up -d
run tailscale funnel --bg 8080
run tailscale funnel status
echo "完成後，https 網址會顯示在 funnel status（形如 https://<機器>.<tailnet>.ts.net）。把這個網址用於配對連結的 --base-url。"
