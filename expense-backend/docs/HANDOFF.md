# 新工作階段交接提示詞

使用方式：在 Claude Code 新開工作階段，**選取 repo `MorrisLaLa/mvtec-anomaly-detection` 與分支 `claude/expense-reporting-system-a4vamf`**（沒選 repo 就沒有推送權限），把下面「提示詞」整段貼進去。

---

## 提示詞

你接手「支出申報系統」的開發。這是一個多工作階段的專案，所有進度與決定都寫在 repo 裡；你不會知道先前的對話，所以**不要憑印象，一律以檔案為準**。

### 第一步（只讀這些，不要探索整個專案）
1. `expense-backend/PROGRESS.md`：只讀最後 3 個區段。
2. `expense-backend/docs/WINDOWS.md`：找出第一個未勾選的視窗，只做那一個。
3. `expense-backend/docs/APPROVALS.md`：確認該工作已核准。
4. 該視窗對應的規格（`expense-backend/docs/specs/`）。目前下一個是 **視窗 6c**：`M1-auth.md` 的「修訂 5」；做完才做 **視窗 6b**：`F2-field-ux.md`。
5. `expense-backend/docs/WORKING-HOURS.md`：工作時間規則。

### 工作規則（必須遵守）
- **TDD：先寫測試、確認紅，再實作、全綠才算完成。** 不得為了讓測試通過而修改測試；測試本身有錯時要在 PROGRESS.md 說明原因。完成後做 2–3 項「突變驗證」（故意弄壞關鍵行為，確認測試會變紅）。
- 每個視窗結束前：更新 `PROGRESS.md`、勾選 `WINDOWS.md`、提交並推送到分支 `claude/expense-reporting-system-a4vamf`。不要建立新分支、不要建立 PR、不要碰 `main`、不要建立或修改任何排程。
- 一次做一個視窗，單線依序，不要同時開多個 agent。白天每次最多 20 分鐘（見 WORKING-HOURS.md）。
- 自我修正迴圈超過 5 次就停下來，把卡住的地方寫進 PROGRESS.md 並回報。
- **需要使用者決定、真實資料、金鑰、刪除資料、改既有 schema 的事，先停下來問，不要猜。** 高風險模組（M1–M4）的新設計必須先有使用者核准（看 APPROVALS.md）。
- 不得提交任何金鑰、`.env`、真實個資。測試用的金鑰要自行產生。
- 回報要誠實：測試失敗就說失敗；沒做的、沒驗證的（例如沒有在真實手機上驗證）要明講。
- 使用者說繁體中文，請用繁體中文，簡短說明「做了什麼、測了什麼、還有什麼限制、需要使用者做什麼」。

### 執行測試
```
cd expense-backend
pip install -r requirements.txt   # 另需系統套件 libzbar0
python -m pytest -q               # 目前 196 個測試；含 Playwright 瀏覽器測試，約 4 分鐘
```
瀏覽器測試用 `/opt/pw-browsers/chromium`，**不要執行 `playwright install`**。

### 專案現況（摘要，細節以檔案為準）
- 後端：FastAPI + SQLite（`expense-backend/app/`）。已完成：M1 裝置金鑰與角色、M2 收件加固、M3 自動審核與審核畫面、F1 前台（Wi‑Fi 策略、手動重送、圖片壓縮）、F6 真實拍照／相簿上傳。
- 前台：`expense-backend/frontend/expense-demo.html` 與 `sync.js`；示範版由 `scripts/build_demo.py` 打包成 `frontend/demo.html`（自帶模擬伺服器，已發布為 Artifact，網址在對話中；重新發布時用同一檔案路徑）。
- 使用者回饋：**示範介面對工程現場人員不夠直覺**。已決定：第一次不用打字（配對連結／QR）、日常「拍照→確認→送出」、工地用語、PIN 選用。
- 待使用者提供（會阻擋對應工作，不要自行編造）：網域名稱、Google Cloud 專案與帳單帳號、寄信服務的帳號或機密、會計對「5 年保存」起算點的確認、真實收據照片樣本、`expense-backend.zip`。
- 夜間排程 `trig_01VhULHWTZZBRQrEuvZoezmR` 暫停中（排程工作階段沒有 repo 推送授權）。不要恢復或修改。

### 你的第一個任務
做 **視窗 6c**：一次性配對碼與配對端點（`M1-auth.md` 修訂 5）。先寫測試（單次使用、過期、相同的 400 回應、限流、配對碼不能當金鑰、撤銷後失效、QR 內容等於連結、資料庫沒有明文），再實作 `pairing_codes` 資料表、`POST /api/v1/pair`、管理指令產生連結與 QR 圖。完成後再做 6b。每完成一個視窗就提交推送並回報。
