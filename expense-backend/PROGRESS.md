# PROGRESS

> 前置說明：repo 內原本沒有 expense-backend 骨架，依任務文件描述於 `expense-backend/` 重建
> （欄位與 settings.yaml 為推測，可能與原骨架不同）。

## [任務1] 統一編號檢查碼驗證
- 狀態：完成
- 測試結果：見任務3合併記錄
- 自我修正迴圈次數：0
- 備註：真實統編（台積電、鴻海、統一超商、中華電信）通過檢查碼。依指示實作傳統版（mod 10）。
  使用者已確認維持此公式（權重 1,2,1,2,1,2,4,1，除以 10；第七碼為 7 時餘數 1 亦合法），不做任何改動。

## [任務2] QR碼解析
- 狀態：完成
- 備註：邏輯已用合成 QR 圖片驗證，**實際電子發票的掃描成功率需要真實照片，待你提供樣本**。

## [任務3] 無QR發票統編抽取
- 狀態：完成
- 測試結果（任務1–3 合計）：32/32 通過
- 自我修正迴圈次數：0
- 備註：OCR 邏輯已用合成文字驗證，準確率需真實樣本確認。8碼比對用「前後非數字」邊界避免誤抓長數字子串。

## [任務4] `/submit` 整合辨識與分類
- 狀態：完成
- 測試結果：`tests/test_submit.py` 16 + `tests/test_classify.py` 8，與任務1–3 合計 59/59 通過
- 自我修正迴圈次數：1
- 備註：
  - 辨識觸發條件為「有 receipt 且沒填 seller_tax_id」（文件寫「seller_tax_id／store 都沒提供」，我放寬為只看統編，因為有填店名仍需要統編）。
  - 低信心度：`seller_tax_id` 不寫入（NULL），候選存 `recognition_note`，回應 `needs_review=true` 並附 `review_reasons`。
  - 額外加入：`case_no` 冪等（離線佇列重送不會重複入庫）、填寫金額與 QR 金額不符時標記人工確認、`case_no` 路徑穿越防護、CSV 公式注入防護。
  - 測試修正（如實說明）：`test_submit_ocr_fallback_high` 原先沒給店名／分類，依任務5規則「分類無法判斷→需人工確認」`needs_review=True` 是正確行為；錯在測試漏條件，故補上 `category`，程式未改。
  - OCR 目前為 `NullOCRProvider`（永遠空字串）；接 Google Cloud Vision 只需新增 `OCRProvider` 子類別並傳入 `create_app(ocr=...)`，需要你的金鑰。

## [任務5] 分類自動判斷與修正回饋
- 狀態：完成
- 測試結果：已含於上列 59/59
- 自我修正迴圈次數：0
- 備註：未改 schema（四張表為重建）。待確認建議由 `classify_corrections` 動態計算，不另建表；
  建議條件＝同店家修正≥3次、目標分類唯一、且現行規則尚未給出該分類。
  `POST /api/v1/pending-rule-suggestions/confirm` 才會寫入 `classify_rules`（以店名全名為關鍵字，最長關鍵字優先）。
  `/submit` 帶 `suggested_category` 且使用者改選時自動記錄修正。

## [任務6] 前端串接真實 API
- 狀態：部分完成（缺你的 expense-demo.html）
- 測試結果：Playwright 3/3 通過（連線成功、伺服器離線佇列保留且重整後仍在、伺服器恢復後自動遞增重試）；全部 62/62（第二輪修正前）
- 自我修正迴圈次數：3
- 備註：
  - **你的 PWA HTML 沒有提供，PIN／簽名／案號／分類確認畫面都沒動到。** 我做了可直接嵌入的 `frontend/sync.js`
    （IndexedDB 佇列、10 秒逾時、2s→4s→…→60s 重試、只有 HTTP 200 才清佇列）與最小示範頁 `frontend/index.html`。
    收到你的 HTML 後只需引入 sync.js 並把送出動作改成 `ExpenseSync.submit({fields, receipt, signature})`。
  - 修正1：sync.js 的 IndexedDB keyPath 寫錯（程式 bug，已修）。修正2：離線時 reload 測試設計不合理（無 service worker），改為先恢復伺服器再 reload。修正3：自動重試測試誤用 wait_for_function（不 await Promise），改為 Python 端輪詢。
  - 伺服器位址：`ExpenseSync.configure({SERVER_URL})`（預設同源，後端於 `/pwa` 提供靜態檔）。跨來源（如 Tailscale）需要後端加 CORS，目前未加。
  - 依規格「失敗就保留」，4xx（例如資料格式錯誤）也會被無限重試；建議之後對 422 另外處理。

---
# 第二輪修正

## [修正1] 改用原始 expense-backend.zip 骨架
- 狀態：**卡住待回報**
- 備註：環境中找不到 `expense-backend.zip`（全檔案系統搜尋與 git 歷史皆無），尚未取得正本，因此無法比對。
  目前 `expense-backend/` 仍是我依文件重建的版本。依指示：取得 zip 後會先列 diff 給你，不自行決定保留哪一版。

## [修正2] 統編檢查碼維持除以10
- 狀態：完成（無程式變動，僅移除我先前未查證的 mod 5 備註）
- 測試結果：`tests/test_recognition.py` 32/32 通過

## [修正3] 接入 expense-demo.html
- 狀態：完成（見下方「第三輪」；本條原先因附件未收到而卡住）

## [修正4] 4xx 不重試，只重試 5xx 與網路逾時
- 狀態：完成
- 測試結果：新增 3 個 Playwright 測試（4xx 最終失敗且訊息顯示、4xx 不阻塞後續項目、5xx 保留並重試成功），前端 e2e 共 6/6；全部 72/72（連跑兩次）
- 自我修正迴圈次數：1（新測試先紅、實作後轉綠）
- 備註：
  - 4xx → 從重試佇列移入獨立的 `failed` 清單（保留收據圖與簽名，供修正重送；`ExpenseSync.failed()` / `dismissFailed()`），回應 `detail` 轉成可讀訊息顯示。
  - 依你的字面規則，408／429 也視為最終失敗；若要把這兩個當可重試請告知。
  - 一次性現象：這輪第一次執行（舊 sync.js）時，原本通過的離線測試也失敗過一次，之後連跑多次皆通過，原因未查明，疑為該輪多個逾時測試造成的環境干擾。

## [修正5] CORS
- 狀態：完成（機制完成，**允許來源清單待你提供**）
- 測試結果：`tests/test_cors_config.py` 7/7（允許來源有標頭、其他來源無、preflight 允許/拒絕、預設不開放、`*` 與萬用字元網域拒絕啟動）
- 備註：`settings.yaml` 的 `cors_allowed_origins` 目前為空（= 只允許同源），因為我不知道 PWA 實際網域。
  請提供，例如 Tailscale 的 `http://100.x.y.z:PORT`。僅允許 GET／POST 與 Content-Type 標頭。

## [修正6] 公司統編設定
- 狀態：完成
- 備註：未改動你的統編。為了配合你的 `company.tax_id` 結構，`config.py` 現在優先讀 `company.tax_id`，並相容舊的 `my_company_tax_id`（有測試）。你的 `settings.yaml` 其他欄位我看不到，若結構不同請提供。

---
# 第三輪：expense-demo.html 已收到並接入

## [修正3] 前端串接真實 API
- 狀態：完成
- 測試結果：Playwright 10 個（驅動 `frontend/expense-demo.html`，走真實的身份選擇＋PIN 設定→無單據→填寫→分類→簽名→送出）；全部 79/79（含後端 69）
- 自我修正迴圈次數：3（皆為先紅後修）
- 檔案：`frontend/expense-demo.html`（你的原檔加上同步改動）、`frontend/sync.js`。舊的示範頁 `frontend/index.html` 已刪除。
- **對你的 HTML 做的改動（其餘一律未動）：**
  1. 引入 `sync.js`，新增 `SERVER_URL` 常數（預設空字串＝同源；改 Tailscale 時填位址）。
  2. 取代 `submitEntry` 後半、`loadPendingQueue`／`savePendingQueue`／`syncPendingQueue`／`toggleNetworkDemo` 等模擬離線邏輯：改為寫入 IndexedDB 佇列→`POST /api/v1/submit`，HTTP 200 才清除；狀態標籤依伺服器回應變為「已記錄」或「待審核」。
  3. 網路狀態列不再是「點一下示範離線」的假開關，改反映真實狀態（`navigator.onLine`＋同步是否因網路/5xx/逾時卡住），點一下＝立即同步。
  4. `renderCategoryChips` 多記一個 `autoCategory`，送出時以 `suggested_category` 回報，後端據此記錄使用者的分類修正。
  5. `renderHome` 新增「送出失敗：訊息」標籤（4xx）；新增 `escapeHtml` 輔助函式。
  PIN、簽名、案號、分類確認、憑證合成、門檻橫幅的邏輯與樣式沒有改動。
- **後端配合調整（需你確認）：**
  - 你的「案號」是專案案號（UY2301），而我重建的後端把 `case_no` 當作每筆申報的唯一冪等鍵，兩者衝突。已改為：新增 `client_id`（前端產生的 UUID，唯一、冪等），`case_no` 改存專案案號（可重複），另增 `owner_case_no`、`doc_type`、`voucher_path` 欄位。這屬於 schema 變動，但因為整個後端仍是我重建的版本（等你的 zip），風險僅限於重建版；若 zip 的 schema 不同，請以 zip 為準，我再對齊。
  - 後端加上「無單據且金額 > NT$1,000 → needs_review」，與前端橫幅規則一致（伺服器端強制）。
  - `/submit` 新增可選的 `voucher` 檔案（簽名＋資料合成的憑證圖）。
- 發現並修正的真 bug：同步進行中又有新申報入列時，新項目會卡在佇列（沒有人再觸發同步）；現在本輪結束後會自動再跑一輪。UI 的離線判斷也改用 `stalled`（真的遇到可重試錯誤），避免誤報離線。
- **已知限制（請知悉）：**
  - 原 HTML 的「拍照申報／相簿上傳」仍是模擬辨識（`DEMO_RECEIPTS`），沒有真的選取或上傳單據照片，所以目前這個介面**不會觸發後端的 QR／OCR 辨識**。要接上需要新增 `<input type=file capture>` 與預覽，這會改到你的拍照流程，等你決定。
  - 4xx 失敗的項目（連同圖片）留在本機 `failed` 清單並顯示於列表，目前沒有「修正後重送」或「刪除」的介面。
  - 頁面還沒有 service worker，離線時重新整理頁面本身會載入失敗（資料不會丟）。

---
# 視窗 1：基線稽核與規格草案（白天 20 分鐘規則，台北 16:27–16:30）

## [視窗1] 基線稽核＋M1–M4 規格草案
- 狀態：完成（未寫任何程式）
- 測試結果：無新增程式；既有 79/79 未動。
- 產出：`docs/baseline-audit.md`（17 項缺口，其中 A1/A2/A4/A5/A6 已實測）、`docs/specs/M1-auth.md`、`M2-intake.md`、`M3-review.md`、`M4-deploy.md`（各含可測試驗收條件、規格挑戰、待決定問題）。
- 結論：**目前版本不可直接放上公網**（匿名即可匯出全部資料、匿名可呼叫規則確認端點、員工姓名可冒名）。
- 實測修正：我原先判斷「超大像素圖可拖垮記憶體」，實測被 Pillow 內建上限擋下，風險已下修；但「非圖片內容被當圖片存檔」成立。
- 阻塞／待使用者決定（寫在各規格末尾，共 Q1–Q12）：
  Q1 IP 封鎖誤傷同事是否接受；Q2 401 提示重新綁定、429 視為可重試（與「4xx 不重試」規則的例外）；Q3 金鑰到期天數；
  Q4 每日上限；Q5 是否移除照片 EXIF；Q6 收據／簽名保存年限（**需會計確認，我未查證**）；
  Q7 誰是 reviewer、主管自己的申報由誰審；Q8 除無單據>1,000 外還有哪些必審情況；
  Q9 雲端供應商與區域；Q10 通知負責人與管道；Q11 網域；Q12 備份保留與還原演練頻率。
- 下一步：使用者審閱規格 → 在 `docs/APPROVALS.md` 勾選已核准的模組 → 夜間排程才會開始實作該模組。
- 仍阻塞：`expense-backend.zip` 尚未提供（A17）。

---
# 規格修訂 1：使用者回覆 Q6／Q2／Q7／Q9（台北 16:38）
- Q6：保存 5 年 → M2 修訂（`retention_days=1825`、到期自動刪除、備份同步到期）。起算點仍請會計確認。
- Q2：使用者不同意 429 自動重試 → M1 採方案 B：4xx 一律不自動重試，改為使用者手動「重送／刪除」，401 重新綁定後手動重送；伺服器端讓 429 少發生。待使用者確認方案 B。
- Q7：改自動審核 → M3 新增自動審核引擎（自動核准／自動退回／轉人工），門檻 ⚠ 待確認（Q7a–c）；轉人工案件仍需一位 admin。
- Q9：AWS、台灣 → M4 採台北區域 `ap-east-2`；Lightsail 在台北是否提供**尚未確認**，上線前須核對 AWS 官方區域服務清單。
- 查證來源：AWS 台北區域開放（https://aws.amazon.com/de/blogs/aws/now-open-aws-asia-pacific-taipei-region/）；2025-07 免費方案改制（https://aws.amazon.com/about-aws/whats-new/2025/07/aws-free-tier-credits-month-free-plan/）。
- 尚未核准任何模組；`docs/APPROVALS.md` 全為 `[ ]`，夜間排程仍只會「無事可做」。
