# PROGRESS

> 前置說明：repo 內原本沒有 expense-backend 骨架，依任務文件描述於 `expense-backend/` 重建
> （欄位與 settings.yaml 為推測，可能與原骨架不同）。

## [任務1] 統一編號檢查碼驗證
- 狀態：完成
- 測試結果：見任務3合併記錄
- 自我修正迴圈次數：0
- 備註：真實統編（台積電、鴻海、統一超商、中華電信）通過檢查碼。依指示實作傳統版（mod 10）。
  我記得財政部自 2023 年起改為 mod 5 的新制（此點未查證），新核發的統編若驗證失敗請告知。

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
- 測試結果：Playwright 3/3 通過（連線成功、伺服器離線佇列保留且重整後仍在、伺服器恢復後自動遞增重試）；全部 62/62
- 自我修正迴圈次數：3
- 備註：
  - **你的 PWA HTML 沒有提供，PIN／簽名／案號／分類確認畫面都沒動到。** 我做了可直接嵌入的 `frontend/sync.js`
    （IndexedDB 佇列、10 秒逾時、2s→4s→…→60s 重試、只有 HTTP 200 才清佇列）與最小示範頁 `frontend/index.html`。
    收到你的 HTML 後只需引入 sync.js 並把送出動作改成 `ExpenseSync.submit({fields, receipt, signature})`。
  - 修正1：sync.js 的 IndexedDB keyPath 寫錯（程式 bug，已修）。修正2：離線時 reload 測試設計不合理（無 service worker），改為先恢復伺服器再 reload。修正3：自動重試測試誤用 wait_for_function（不 await Promise），改為 Python 端輪詢。
  - 伺服器位址：`ExpenseSync.configure({SERVER_URL})`（預設同源，後端於 `/pwa` 提供靜態檔）。跨來源（如 Tailscale）需要後端加 CORS，目前未加。
  - 依規格「失敗就保留」，4xx（例如資料格式錯誤）也會被無限重試；建議之後對 422 另外處理。
