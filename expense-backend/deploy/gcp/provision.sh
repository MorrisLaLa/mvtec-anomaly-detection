#!/usr/bin/env bash
# Google Cloud 資源建立（M4）。預設只在 --dry-run 列出將建立的資源，不呼叫 gcloud、不含任何金鑰。
# 真正執行需要使用者自己的專案與帳單：  PROJECT_ID=... SSH_SOURCE_RANGE=<你的固定 IP>/32 ./provision.sh
# 注意（⚠ 上線前請以 Google 官方文件核對）：各服務在 asia-east1 的提供情況與價格、uptime check 與預算的指令細節。
set -euo pipefail

DRY=0
FREE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --free-tier) FREE=1 ;;
    *) echo "用法：provision.sh [--dry-run] [--free-tier]（不認得的參數：$arg）" >&2; exit 2 ;;
  esac
done

PROJECT="${PROJECT_ID:-}"
SSH_RANGE="${SSH_SOURCE_RANGE:-}"
BUDGET="${BUDGET_AMOUNT:-}"
NOTIFY="${NOTIFY_EMAIL:-yys1114@gmail.com}"
DISK_TYPE="pd-balanced"; DISK_SIZE="20GB"
if [ "$FREE" -eq 1 ]; then
  # Google 的常駐免費額度（⚠ 上線前以官方「免費方案」頁面核對）：1 台 e2-micro、30GB 標準硬碟，
  # 僅限美國的 us-west1／us-central1／us-east1。Cloud Storage 免費 5GB 同樣只在這三個區域。
  REGION="${REGION:-us-central1}"; ZONE="${ZONE:-us-central1-a}"; MACHINE="${MACHINE_TYPE:-e2-micro}"
  DISK_TYPE="pd-standard"; DISK_SIZE="30GB"
  case "$REGION" in
    us-west1|us-central1|us-east1) ;;
    *) echo "錯誤：免費額度只適用 us-west1／us-central1／us-east1，不適用 $REGION" >&2; exit 2 ;;
  esac
else
  REGION="${REGION:-asia-east1}"; ZONE="${ZONE:-asia-east1-b}"; MACHINE="${MACHINE_TYPE:-e2-small}"
fi
BUCKET="${BACKUP_BUCKET:-${PROJECT:-<PROJECT_ID>}-expense-backup}"

if [ "$DRY" -eq 0 ]; then
  [ -n "$PROJECT" ] || { echo "錯誤：請設定 PROJECT_ID（你的 Google Cloud 專案）" >&2; exit 2; }
  [ -n "$SSH_RANGE" ] || { echo "錯誤：請設定 SSH_SOURCE_RANGE（允許 SSH 的來源 IP 範圍）" >&2; exit 2; }
  [ -n "$BUDGET" ] || { echo "錯誤：請設定 BUDGET_AMOUNT（每月預算金額；超出免費額度會計費，一定要有預算警示）" >&2; exit 2; }
fi

P="${PROJECT:-<PROJECT_ID>}"
S="${SSH_RANGE:-<SSH_SOURCE_RANGE>}"
B="${BUDGET:-<BUDGET_AMOUNT>}"

run() {  # 乾跑只印出，不執行
  if [ "$DRY" -eq 1 ]; then echo "WOULD RUN: $*"; else "$@"; fi
}

echo "== 專案 $P  區域 $REGION =="
if [ "$FREE" -eq 1 ]; then
  echo "注意：免費額度只在美國區域。選用 --free-tier 代表收據資料與備份（含統編等個資）存放在美國＝跨境，"
  echo "      請先確認可接受，並在隱私說明載明；正式上線若要留在台灣，請改用預設的 asia-east1（需付費）。"
fi
echo "WOULD CREATE: 防火牆 expense-web：允許 tcp:80,tcp:443 來自 0.0.0.0/0（其餘全部拒絕）"
run gcloud compute firewall-rules create expense-web --project "$P" --allow tcp:80,tcp:443 --target-tags expense --source-ranges 0.0.0.0/0
echo "WOULD CREATE: 防火牆 expense-ssh：SSH tcp:22 只允許來源 $S（僅金鑰登入）"
run gcloud compute firewall-rules create expense-ssh --project "$P" --allow tcp:22 --target-tags expense --source-ranges "$S"
echo "WOULD CREATE: 備份儲存桶 gs://$BUCKET（$REGION、uniform-bucket-level-access、public-access-prevention enforced、versioning 開啟）"
run gcloud storage buckets create "gs://$BUCKET" --project "$P" --location "$REGION" --uniform-bucket-level-access --public-access-prevention
run gcloud storage buckets update "gs://$BUCKET" --versioning
echo "WOULD CREATE: VM expense-vm（$MACHINE、$ZONE、開機碟 $DISK_TYPE $DISK_SIZE、tag expense、Debian + docker compose）"
run gcloud compute instances create expense-vm --project "$P" --zone "$ZONE" --machine-type "$MACHINE" --tags expense --boot-disk-type "$DISK_TYPE" --boot-disk-size "$DISK_SIZE"
echo "WOULD CREATE: Cloud Monitoring uptime check：https://<PUBLIC_HOSTNAME>/api/v1/health 每 5 分鐘，失敗寄信到 $NOTIFY"
echo "WOULD CREATE: 磁碟用量 >80% 告警，寄信到 $NOTIFY"
echo "WOULD CREATE: Billing budget 警示（每月 $B，達 50%／90%／100% 通知），寄信到 $NOTIFY"
echo "（監控與預算的 gcloud 指令需對照官方文件，尚未納入腳本；上述三項目前為待辦清單）"
[ "$DRY" -eq 1 ] && echo "乾跑結束：未建立任何資源。"
exit 0
