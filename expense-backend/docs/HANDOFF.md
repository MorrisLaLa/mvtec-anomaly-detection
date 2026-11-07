# 新工作階段交接提示詞

使用方式：在 Claude Code 新開工作階段，**選取 repo `MorrisLaLa/mvtec-anomaly-detection` 與分支 `claude/expense-reporting-system-a4vamf`**（沒選 repo 就沒有推送權限），把下面「提示詞」整段貼進去。

---

## 提示詞

你接手「支出申報系統」的開發。這是一個多工作階段的專案，所有進度與決定都寫在 repo 裡；你不會知道先前的對話，所以**不要憑印象，一律以檔案為準**。

### 第一步（只讀這些，不要探索整個專案）
1. `expense-backend/PROGRESS.md`：只讀**最後 4 個區段**（從「使用者決定與保存期限改為『年度決算後 5 年』」開始）。
2. `expense-backend/docs/WINDOWS.md`：視窗 1–6、6b、6c、8 已完成；7 卡在需要實機；**目前在做視窗 9（修正與文件定稿、最終驗收）**。
3. `expense-backend/docs/WORKING-HOURS.md`、`docs/APPROVALS.md`。
4. 需要時再讀：`docs/security-review.md`（D2 尚待實機）、`docs/DEPLOY-local.md`（Windows 部署手冊）。

### 使用者已做的決定（不要再問）
- **伺服器放在使用者自己的 Windows 電腦**（Mac 只用來操作；不用 Google Cloud）。
- Tailscale Funnel：**最後上線前再處理**。
- 警報用 **Email**（專用 Gmail＋應用程式密碼，已實作 `app/notify.py`，帳密只放主機 `.env`）。
- 保存期限：**年度決算完成後 5 年**（已實作；決算期限預設次年 06-30，可設定）。**到期刪除保持關閉**，開啟需使用者明確同意。
- 會計取代主管（單層審核，畫面用語「審核」）；勾選後一鍵核准；處理紀錄（手機＋CSV）；「已處理」分頁只給審核人員與管理員；**現場人員介面以簡單、簡潔、好操作為原則**（首頁只有拍收據／從相簿選／沒有收據）。
- 管理員最少 1、最多 2 位，可轉移／增加／減少（手機上不能拿掉另一位管理員）。
- 匯出 CSV 中文欄名、Excel 可直接開（BOM）。
- 偽造發票 QR 的風險（S1）使用者接受。

### 下一步（視窗 9 剩餘）
1. **先問使用者：異地備份存到哪裡**（外接硬碟／雲端硬碟同步資料夾／先手動）。目前備份與資料在同一台電腦。
2. 文件定稿：README、部署手冊與現況一致。
3. 需要實機（使用者準備好 Windows 主機後，陪使用者一步一步做）：實際部署、`python -m app.notify test` 試寄、Tailscale Funnel 下來源 IP 是否正確（資安 D2）。
4. 需要時向使用者索取真實收據照片驗證辨識。

### 工作規則（必須遵守）
- **TDD：先寫測試、確認紅，再實作、全綠才算完成。** 不得為了讓測試通過而修改測試；測試本身有錯、或使用者改了行為而必須改測試時，要在 PROGRESS.md 說明原因。完成後做 2–3 項「突變驗證」。
- 每個單元結束就提交並推送到分支 `claude/expense-reporting-system-a4vamf`。不要建立新分支、不要建立 PR、不要碰 `main`、不要建立或修改任何排程。
- 一次做一件事，單線依序。白天每次最多 20 分鐘（WORKING-HOURS.md）；使用者說「不限時」才可超過。
- 完整測試約 6 分鐘；**跑的時候不要改檔案**。
- 自我修正迴圈超過 5 次就停下來，寫進 PROGRESS.md 並回報。
- **需要使用者決定、真實資料、金鑰、刪除資料、改既有 schema 的事，先停下來問，不要猜。**
- 不得提交任何金鑰、`.env`、真實個資。
- 回報要誠實：測試失敗就說失敗；沒做的、沒驗證的要明講。
- 使用者說繁體中文，請用繁體中文、白話、簡短說明「做了什麼、測了什麼、還有什麼限制、需要使用者做什麼」。
- 對話很長、耗 token 時，主動提醒使用者換新對話，並先更新本檔。

### 執行測試
```
cd expense-backend
pip install -r requirements.txt   # 另需系統套件 libzbar0（apt-get update && apt-get install -y libzbar0）
python -m pytest -q               # 目前 506 個，約 6 分鐘
```
瀏覽器測試用 `/opt/pw-browsers/chromium`，**不要執行 `playwright install`**。本環境 docker 只有用戶端、沒有 daemon，也沒有 Caddy；連不到政府網站與 Gmail。

### 專案現況（摘要，細節以檔案為準）
- 後端：FastAPI + SQLite（`app/`）。裝置金鑰與角色、配對碼、管理員 1–2 位（`admin.py`、`routers/review.py` 的 `/admin/*`）；收件加固；自動審核（抽查以伺服器秘密 HMAC）；審核、一鍵核准、處理紀錄、匯出（中文 CSV、處理紀錄 CSV）；維運排程、備份、還原演練、Email 通知。
- 前台：`frontend/expense-demo.html`＋`app.js`＋`sync.js`（CSP `script-src 'self'`，行內事件改為 `data-click` 統一分派）。示範版由 `scripts/build_demo.py`（模擬伺服器 `scripts/demo_mock.js`）打包成 `frontend/demo.html`，**已發布為 Artifact**（https://claude.ai/artifact/4ZTt1Fii5jqgKEaiDLkMVh ，私人；宣告了 `downloads` 權限；更新時發布到同一網址）。
- 部署：`docs/DEPLOY-local.md`（Windows）、`docker-compose.yml`（app／ops／caddy-funnel／caddy／選用 litestream）、`deploy/*`。**全部只做過內容／乾跑檢查，從未實際啟動容器。**
- 夜間排程 `trig_01VhULHWTZZBRQrEuvZoezmR` 暫停中。不要恢復或修改。
