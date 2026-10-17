#!/usr/bin/env bash
# Google Cloud 資源建立（M4）。預設只在 --dry-run 列出將建立的資源，不呼叫 gcloud、不含任何金鑰。
# 真正執行需要使用者自己的專案與帳單：  PROJECT_ID=... SSH_SOURCE_RANGE=<你的固定 IP>/32 ./provision.sh
# 注意（⚠ 上線前請以 Google 官方文件核對）：各服務在 asia-east1 的提供情況與價格、uptime check 與預算的指令細節。
set -euo pipefail

DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

PROJECT="${PROJECT_ID:-}"
REGION="${REGION:-asia-east1}"
ZONE="${ZONE:-asia-east1-b}"
SSH_RANGE="${SSH_SOURCE_RANGE:-}"
BUCKET="${BACKUP_BUCKET:-${PROJECT:-<PROJECT_ID>}-expense-backup}"
MACHINE="${MACHINE_TYPE:-e2-small}"
NOTIFY="${NOTIFY_EMAIL:-yys1114@gmail.com}"

if [ "$DRY" -eq 0 ]; then
  [ -n "$PROJECT" ] || { echo "錯誤：請設定 PROJECT_ID（你的 Google Cloud 專案）" >&2; exit 2; }
  [ -n "$SSH_RANGE" ] || { echo "錯誤：請設定 SSH_SOURCE_RANGE（允許 SSH 的來源 IP 範圍）" >&2; exit 2; }
fi

P="${PROJECT:-<PROJECT_ID>}"
S="${SSH_RANGE:-<SSH_SOURCE_RANGE>}"

run() {  # 乾跑只印出，不執行
  if [ "$DRY" -eq 1 ]; then echo "WOULD RUN: $*"; else "$@"; fi
}

echo "== 專案 $P  區域 $REGION =="
echo "WOULD CREATE: 防火牆 expense-web：允許 tcp:80,tcp:443 來自 0.0.0.0/0（其餘全部拒絕）"
run gcloud compute firewall-rules create expense-web --project "$P" --allow tcp:80,tcp:443 --target-tags expense --source-ranges 0.0.0.0/0
echo "WOULD CREATE: 防火牆 expense-ssh：SSH tcp:22 只允許來源 $S（僅金鑰登入）"
run gcloud compute firewall-rules create expense-ssh --project "$P" --allow tcp:22 --target-tags expense --source-ranges "$S"
echo "WOULD CREATE: 備份儲存桶 gs://$BUCKET（$REGION、uniform-bucket-level-access、public-access-prevention enforced、versioning 開啟）"
run gcloud storage buckets create "gs://$BUCKET" --project "$P" --location "$REGION" --uniform-bucket-level-access --public-access-prevention
run gcloud storage buckets update "gs://$BUCKET" --versioning
echo "WOULD CREATE: VM expense-vm（$MACHINE、$ZONE、tag expense、Container-Optimized OS 或 Debian + docker compose）"
run gcloud compute instances create expense-vm --project "$P" --zone "$ZONE" --machine-type "$MACHINE" --tags expense
echo "WOULD CREATE: Cloud Monitoring uptime check：https://<PUBLIC_HOSTNAME>/api/v1/health 每 5 分鐘，失敗寄信到 $NOTIFY"
echo "WOULD CREATE: 磁碟用量 >80% 告警，寄信到 $NOTIFY"
echo "WOULD CREATE: Billing budget 警示（金額由使用者決定），寄信到 $NOTIFY"
echo "（監控與預算的 gcloud 指令需對照官方文件，尚未納入腳本；上述三項目前為待辦清單）"
[ "$DRY" -eq 1 ] && echo "乾跑結束：未建立任何資源。"
exit 0
