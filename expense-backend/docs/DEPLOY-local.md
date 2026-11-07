# 部署手冊：在自己的 Windows 或 Mac 上執行

使用者決定（台北 2026-10-24）：主機用自己的電腦；**2026-11-07 決定伺服器放在 Windows**（Mac 只用來操作）。本手冊寫給使用者照著做。
**誠實說明：這份手冊的步驟都還沒有在真實的 Windows 或 Mac 上實際跑過**，第一次部署時請和 Claude 一起逐步確認，有錯就回報修正。

---

## 0. 先決定：伺服器只放在一台電腦上

系統的資料（申報、收據照片、備份）都存在「執行伺服器的那台電腦」。Windows 和 Mac 可以都拿來**用**（打開網頁審核、匯出），但**伺服器只能跑在其中一台**，否則資料會分散在兩台。

選哪一台：

| 條件 | 為什麼重要 |
|---|---|
| 可以 24 小時開機、不進入睡眠 | 現場人員隨時會上傳；電腦睡著，大家就連不上 |
| 有穩定網路 | Tailscale Funnel 靠這台電腦對外連線 |
| 硬碟至少剩 30 GB | 收據照片與備份會累積 |

桌上型電腦或常插電的 Mac mini 比筆電適合。

---

## 1. 安裝 Docker Desktop

系統用 Docker 執行，不需要另外安裝 Python 等軟體。

- **Windows**：到 Docker 官網下載 Docker Desktop for Windows。安裝時保持勾選「Use WSL 2」；安裝完畢依提示重新開機。
- **Mac**：到 Docker 官網下載 Docker Desktop for Mac，依晶片選擇 Apple Silicon（M1／M2／M3…）或 Intel 版本。

安裝後：
1. 打開 Docker Desktop，等左下角顯示執行中。
2. 設定（Settings）→ General → 勾選 **Start Docker Desktop when you sign in**（開機自動啟動）。
3. **授權**：Docker Desktop 對小型企業免費，但有員工數與營收門檻，請到 Docker 官網確認貴公司適用的條款。

---

## 2. 讓這台電腦不要睡眠

- **Windows**：設定 → 系統 → 電源 → 「螢幕關閉」可以設定，但「睡眠」設為「永不」（插電時）。
- **Mac**：系統設定 → 能源（或「電池」→「選項」）→ 開啟「防止電腦在顯示器關閉時自動進入睡眠」（插電時）。

---

## 3. 取得程式

任選一種方式：
- **下載 ZIP**：在 GitHub 打開 repo，切換到 `claude/expense-reporting-system-a4vamf` 分支 → Code → Download ZIP，解壓縮到例如 `文件/expense`。
- **用 git**（會用的話）：`git clone`，再 `git checkout claude/expense-reporting-system-a4vamf`。

以下指令都在 `expense-backend` 資料夾裡執行：
- **Windows**：在資料夾空白處按右鍵 →「在終端機中開啟」（PowerShell）。
- **Mac**：打開「終端機」，輸入 `cd ` 後把資料夾拖進視窗，按 Enter。

---

## 4. 建立 `.env` 設定檔

在 `expense-backend` 資料夾新增一個名為 `.env` 的純文字檔，內容：

```
COMPOSE_PROFILES=funnel
```

`.env` 已被 git 排除，不會被上傳。寄信用的應用程式密碼也放在這裡（**不要**放進其他檔案、不要貼到對話或訊息裡），見第 4-1 步。

### 4-1. Email 警報（使用者 2026-11-07 決定用 Email）

1. 另外申請一個**專門給系統寄信**的 Gmail 帳號（例如 `xxx-system@gmail.com`）。
2. 用那個帳號登入 Google 帳戶 → 安全性 → 開啟「兩步驟驗證」。
3. 同一頁找「應用程式密碼」，產生一組（16 個英文字母）。
4. 在 `.env` 加上兩行（換成你的帳號與那組密碼）：

```
EXPENSE_SMTP_USER=xxx-system@gmail.com
EXPENSE_SMTP_PASSWORD=那組十六個字母
```

警報會寄到 `config/settings.yaml` 的 `notify.recipient`（目前是 yys1114@gmail.com）。啟動後寄一封測試信確認：

```
docker compose exec ops python -m app.notify test
```

看到「已寄出測試信」並收到信就完成。失敗時原因會寫在通知記錄檔，不會包含密碼。密碼外洩或不用時，到 Google 帳戶刪除那組應用程式密碼即可，不影響其他東西。

---

## 5. 啟動

```
docker compose up -d --build
```

第一次會下載與建置，需要幾分鐘。完成後確認：

```
docker compose ps
```

應看到 `app`、`ops`、`caddy-funnel` 三個服務都是 running。

在**這台電腦的瀏覽器**打開 http://localhost:8080/api/v1/health ，看到 `{"status":"ok"}` 就表示伺服器正常。
（此時只有這台電腦連得到；手機要等第 7 步開啟 Tailscale Funnel 之後才能連。）

---

## 6. 建立你的管理員裝置

```
docker compose exec app python -m app.admin create-device --name 你的名字 --role admin --base-url https://<Funnel 網址>
```

- 會印出一個「配對連結」（24 小時內有效、只能用一次）。在手機打開這個連結就完成綁定。
- `--base-url` 要等第 7 步拿到 Funnel 網址後才填得出來；在那之前可以先不建立。
- 會計：同樣指令，`--role reviewer`。現場人員：`--role submitter`。
- 查看所有裝置：`docker compose exec app python -m app.admin list`

---

## 7. 對外開放（上線前，使用者決定最後再處理）

用 Tailscale Funnel 取得 `https://<機器名>.<tailnet>.ts.net` 網址，不需要買網域。細節與注意事項見 `deploy/funnel/README.md`。重點：
1. 在這台電腦安裝 Tailscale App 並登入。
2. 依 README 開啟 MagicDNS、HTTPS 憑證與 funnel 權限。
3. 執行：`tailscale funnel --bg 8080`（Windows 在 PowerShell、Mac 在終端機；指令語法未在本環境驗證）。
4. **上線前必驗**：用錯誤的金鑰連一次，檢查存取記錄中的 IP 是不是手機的真實 IP（資安審查 D2）。

---

## 8. 日後維運

**自動進行（`ops` 服務，台北時間）**
| 時間 | 工作 |
|---|---|
| 每天 03:10 | 備份資料庫與收據 |
| 每天 09:00 | 健康檢查：磁碟空間、逾期未審的申報 |
| 每月 1 日 09:30 | 月報 |
| 1、4、7、10 月 1 日 04:00 | 還原演練（確認備份真的能用） |
| （關閉中） | 到期刪除：等會計確認「5 年」起算點 |

**需要你注意**
- **備份目前存在同一台電腦上**。電腦壞掉或遺失，備份會一起不見。建議每月把 Docker 資料（或備份資料夾）複製到外接硬碟或雲端硬碟；要自動化異地備份，需另外決定存放位置。
- **Email 警報**：完成第 4-1 步後，備份失敗、還原演練失敗、磁碟快滿、申報放太久沒人審、每月報告都會寄信；沒設定時只寫進記錄檔。
- 作業系統更新：Windows Update、macOS 自動更新保持開啟。更新重開機後，Docker Desktop 會自動啟動，服務也會自動重啟（compose 設定為 `restart: unless-stopped`）。
- 程式更新：在 `expense-backend` 資料夾執行 `docker compose up -d --build`。
- 回到舊版：`deploy/rollback.sh <版本>`（Mac 可直接執行；Windows 需在 WSL 中執行，或改用手動步驟：`git checkout <版本>` 後 `docker compose up -d --build`）。

**常用指令**
| 要做什麼 | 指令 |
|---|---|
| 看服務狀態 | `docker compose ps` |
| 看最近的錯誤 | `docker compose logs --tail 100 app` |
| 重新啟動（也會解除所有登入封鎖） | `docker compose restart app` |
| 停止 | `docker compose down`（資料保留） |

---

## 尚未驗證／待辦（誠實說明）
- 所有步驟未在真實 Windows／Mac 上執行過；Docker 映像版本標籤未核對。
- Windows 上 `deploy/*.sh` 腳本需要 WSL 或 Git Bash。
- 異地備份尚未設定；Email 寄送只用模擬的郵件伺服器測過，沒有真的寄到 Gmail（本環境連不到）。
