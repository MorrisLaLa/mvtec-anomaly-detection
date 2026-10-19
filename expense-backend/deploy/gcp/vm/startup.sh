#!/usr/bin/env bash
# VM 開機腳本（Debian／Ubuntu）：安裝 docker、啟用自動安全更新、強化 SSH。可重複執行。
# 不含任何金鑰；機密由使用者以環境變數或雲端機密管理另行提供。
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends docker.io docker-compose-v2 unattended-upgrades

# 自動安全更新（設定檔與本腳本同目錄；由 provision／scp 放到 /opt/expense/vm/）
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 0644 "$HERE/50unattended-upgrades-expense" /etc/apt/apt.conf.d/50unattended-upgrades-expense
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# SSH：僅金鑰、禁止 root 登入（來源 IP 限制由 GCP 防火牆處理）
install -d /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/99-expense.conf <<'CONF'
PasswordAuthentication no
PermitRootLogin no
CONF
systemctl reload ssh || systemctl reload sshd

systemctl enable --now docker
echo "VM 初始化完成。接著請放入專案、建立 .env（PUBLIC_HOSTNAME、BACKUP_BUCKET），再執行 docker compose up -d。"
