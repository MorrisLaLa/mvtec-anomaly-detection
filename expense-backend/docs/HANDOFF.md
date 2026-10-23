# 新工作階段交接提示詞

使用方式：在 Claude Code 新開工作階段，**選取 repo `MorrisLaLa/mvtec-anomaly-detection` 與分支 `claude/expense-reporting-system-a4vamf`**（沒選 repo 就沒有推送權限），**視窗 8 請選 Opus**（WINDOWS.md 規定），把下面「提示詞」整段貼進去。

---

## 提示詞

你接手「支出申報系統」的開發。這是一個多工作階段的專案，所有進度與決定都寫在 repo 裡；你不會知道先前的對話，所以**不要憑印象，一律以檔案為準**。

### 第一步（只讀這些，不要探索整個專案）
1. `expense-backend/PROGRESS.md`：只讀最後 3 個區段（含「視窗 7 卡住待回報」）。
2. `expense-backend/docs/WINDOWS.md`：視窗 1–6、6b、6c 已完成；視窗 7 的「不需使用者資料」部分已完成但**卡住待回報、未勾選**；下一個要做的是 **視窗 8（資安審查與對抗測試）**。
3. `expense-backend/docs/APPROVALS.md`、`docs/WORKING-HOURS.md`。
4. 視窗 8 的依據：`docs/baseline-audit.md`（A1…A14 缺口清單）、`docs/specs/M1–M4`、`F2-field-ux.md` 的驗收條件。

### 視窗 8 要做什麼
以攻擊者角度審查整個後端與前台，寫成**對抗測試**（先寫測試、確認紅或確認攻擊被擋住），並把發現與嚴重度寫入 `docs/security-review.md`：
- 身分與授權：金鑰／配對碼的繞過、越權（submitter 看主管資料、自審）、限流繞過、時序差異。
- 收件：上傳（路徑穿越、檔案炸彈、惡意圖片、MIME 偽裝）、注入（SQL、CSV 公式注入、XSS 儲存型）、每日上限繞過、重放。
- 前台：CSP 下的 XSS、`localStorage` 中的敏感資料、配對連結洩漏。
- 部署設定：`Dockerfile`、`docker-compose.yml`、`deploy/Caddyfile`、`deploy/gcp/*`、`deploy/rollback.sh` 的注入與過度權限。
- 發現的問題：**嚴重者先修再回報；需要使用者決定的（會改行為或 schema）先停下來問**。視窗 9 才做大規模修正與定稿。

### 工作規則（必須遵守）
- **TDD：先寫測試、確認紅，再實作、全綠才算完成。** 不得為了讓測試通過而修改測試；測試本身有錯時要在 PROGRESS.md 說明原因。完成後做 2–3 項「突變驗證」。
- 每個單元結束就提交並推送到分支 `claude/expense-reporting-system-a4vamf`。不要建立新分支、不要建立 PR、不要碰 `main`、不要建立或修改任何排程。
- 一次做一件事，單線依序。白天每次最多 20 分鐘（WORKING-HOURS.md）；使用者說「不限時」才可超過。
- 完整測試約 4–5 分鐘；**跑的時候不要改檔案**。
- 自我修正迴圈超過 5 次就停下來，寫進 PROGRESS.md 並回報。
- **需要使用者決定、真實資料、金鑰、刪除資料、改既有 schema 的事，先停下來問，不要猜。**
- 不得提交任何金鑰、`.env`、真實個資。
- 回報要誠實：測試失敗就說失敗；沒做的、沒驗證的要明講。
- 使用者說繁體中文，請用繁體中文，簡短說明「做了什麼、測了什麼、還有什麼限制、需要使用者做什麼」。

### 執行測試
```
cd expense-backend
pip install -r requirements.txt   # 另需系統套件 libzbar0（apt-get update && apt-get install -y libzbar0）
python -m pytest -q               # 目前 334 個，約 4–5 分鐘
```
瀏覽器測試用 `/opt/pw-browsers/chromium`，**不要執行 `playwright install`**。本環境 docker 只有用戶端、沒有 daemon，也沒有 Caddy。

### 專案現況（摘要，細節以檔案為準）
- 後端：FastAPI + SQLite（`app/`）。M1 裝置金鑰與角色、配對碼；M2 收件加固；M3 自動審核（用途未確定**不**轉主管確認，使用者已決定）與審核畫面；M4 通知／維運檢查（`ops.py`）、備份／還原／演練（`backup.py`）、排程器（`scheduler.py`，到期刪除 purge 預設**關閉**）。
- 前台：`frontend/expense-demo.html` 與 `sync.js`；視窗 6b 已完成（配對連結零輸入、拍收據大按鈕、工地用語、結果卡、設定頁 PIN 選用開關、姓名一律來自伺服器）。示範版由 `scripts/build_demo.py` 打包成 `frontend/demo.html`，**已發布為 Artifact**（https://claude.ai/artifact/4ZTt1Fii5jqgKEaiDLkMVh ，私人；更新時用同一檔案路徑發布）。
- 部署：`Dockerfile`、`docker-compose.yml`（app／ops／caddy／選用 litestream）、`deploy/Caddyfile`、`deploy/gcp/provision.sh`（`--dry-run`、`--free-tier`）、`deploy/gcp/vm/*`、`deploy/rollback.sh`、`.github/workflows/expense-ci.yml`、`.github/dependabot.yml`。**全部只做過內容／乾跑檢查，從未實際啟動容器。**
- **不需要網域**：使用者選擇 Tailscale Funnel（規格 M4 修訂 4、`deploy/funnel/`），待實機驗證；Apps Script＋試算表方案評估後不採用。入口代理由 `.env` 的 `COMPOSE_PROFILES`（funnel／domain）決定。
- 待使用者提供（會阻擋對應工作，不要自行編造）：Google Cloud 專案與帳單帳號、寄信服務帳號或機密、會計對「5 年」起算點的確認、真實收據照片樣本、正式資料可否放美國（免費額度只在美國區域）。
- 夜間排程 `trig_01VhULHWTZZBRQrEuvZoezmR` 暫停中。不要恢復或修改。
