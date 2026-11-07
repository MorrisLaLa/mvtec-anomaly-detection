# 基線稽核：現有程式對「公網版」的缺口

稽核對象：分支 `claude/expense-reporting-system-a4vamf`（79 個測試全綠）。
方法：讀程式與 grep 驗證，A1、A2、A4、A5、A6 另以小型探測腳本實測（結果見下表「實測」欄）；完整的對抗測試在視窗 8。證據行號為稽核當下。
嚴重度：高＝公網上線前必須修；中＝應修；低＝可接受或後續。

| # | 嚴重度 | 缺口 | 證據 | 實測 | 對應規格 |
|---|---|---|---|---|---|
| A1 | 高 | **所有端點都沒有身分驗證**（只有 `/health` 該公開）。任何人可送申報、匯出全部申報資料 | `app/routers/expenses.py` 60–226 行的端點無任何 Depends 驗證；`grep` 找不到 Authorization／token | 匿名 `GET /export` → 200（**確認**） | M1 |
| A2 | 高 | **任何人都能改分類規則**：`POST /pending-rule-suggestions/confirm` 與 `POST /corrections` 無授權，可被灌假修正後自行確認，污染自動分類 | 同上 209–224 行 | 匿名 `POST …/confirm` 未被拒絕授權，只因無待確認建議回 409（**確認端點可匿名呼叫**） | M1、M3 |
| A3 | 高 | **申報人可被冒名**：`employee` 是表單欄位，由用戶端自填 | `submit()` 的 `employee: str = Form("")` | 未實測（由程式碼可直接確認） | M1 |
| A4 | 高 | **匯出無授權且含全部欄位**（含簽名／收據檔案路徑、員工姓名） | `export()` 226 行起 | 同 A1（**確認**） | M1、M3 |
| A5 | 中 | **上傳檔案未驗證內容**：不看魔術位元組；一律存成 `.png`；`parse_invoice_qr` 直接 `Image.open`，未自訂像素上限 | `recognition.py:60`；`expenses.py:14, 106–109` 只限位元組數 | 900MP PNG（壓縮後 0.9MB）→ `parse_invoice_qr` 回 None、0.0 秒：Pillow 內建的像素上限已擋下（**炸彈風險比原先判斷低**，約 89–179MP 之間只警告仍會解碼，未測）。但**非圖片內容（HTML）以 `image/png` 上傳 → 200 且被存成檔案（確認）** | M2 |
| A6 | 中 | **無請求本體大小上限**：`await file.read(MAX+1)` 發生在框架已解析完整個 multipart 之後；未見反向代理／應用層的總大小限制 | 同上 | 60MB 本體 → 413（**限制有效，但是在框架解析完整個本體之後才拒絕**；記憶體峰值未量測） | M2、M4 |
| A7 | 中 | **無限流、無防暴力破解、無每日上限** | grep 無 limiter／rate | — | M1、M2 |
| A8 | 中 | **欄位長度無上限**（store、note、category 等）；可塞入超長字串進資料庫與 CSV | `submit()` 參數 | — | M2 |
| A9 | 中 | **前台儲存型 XSS 風險**：`innerHTML` 直接嵌入 `e.store`、`e.category`、`e.reporter`。目前只顯示自己的資料；一旦 M3 審核畫面顯示他人資料就會變成實際漏洞 | `frontend/expense-demo.html` 約 1270、1275、1278 行 | — | M2（修現有）、M3 |
| A10 | 中 | **照片可能含 EXIF（含 GPS）**，原樣存檔，屬不必要的個資蒐集 | 上傳後直接 `storage.save` 原始位元組 | — | M2 |
| A11 | 中 | **重送冪等未綁定裝置／內容**：同一 `client_id` 不論內容是否相同都回傳既有結果；公網上 `client_id` 可被猜測的前提雖低（UUID），仍應綁定裝置並偵測內容不一致 | `submit()` 的 existing 分支 | — | M1、M2 |
| A12 | 中 | **無存取記錄／稽核軌跡**，出事無法追查；無審核事件記錄 | grep 無 logging | — | M2、M3 |
| A13 | 中 | **無備份、無還原演練、`Dockerfile` 為空、無 HTTPS／部署設定** | repo | — | M4 |
| A14 | 低 | PIN 為 4 位數、明文存在 `localStorage`，僅作手機本機解鎖，不是後端授權。公網版需明確定位為「便利功能」，不可當安全邊界 | `expense-demo.html` PIN 流程 | — | M1（文件） |
| A15 | 低 | 字型來自 Google Fonts（第三方請求，會洩漏使用者 IP 給 Google）；CSP 需允許或改自架 | `<link href=fonts.googleapis.com>` | — | M4 |
| A16 | 低 | `sync.js` 目前把所有 4xx 視為最終失敗；公網版會出現 401（金鑰失效，應提示重新綁定）與 429（限流，應稍後重試），與使用者原規則衝突，**需使用者決定** | `frontend/sync.js` | — | M1、前台視窗 |
| A17 | 低 | 資料庫 schema 為重建版，與使用者原始 `expense-backend.zip` 的差異尚未比對（zip 仍未提供） | — | — | 阻塞項 |

## 已有且可保留的防護
`case_no`／`client_id` 路徑穿越防護（`is_safe_name`）、CSV 公式注入防護、`*` CORS 拒絕啟動、重送冪等、低信心辨識不寫入確定值、無單據 >1,000 元伺服器端強制待審核。

## 稽核結論
**目前版本不可直接放上公網。** A1–A4 任何一項單獨成立就足以讓外部人員讀取或竄改全部申報資料。
修復順序建議：M1（A1–A4、A7、A11）→ M2（A5、A6、A8–A10、A12）→ M3 → M4。
