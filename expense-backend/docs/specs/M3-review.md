# M3 主管審核與匯出 — 規格草案（高風險，須使用者核准）

狀態：草案　風險：高　依賴：M1、M2　會改動 schema（`expenses` 新增 review 欄位、新增 `review_events` 表）→ **核准即同意**

## 目的
讓主管處理 `needs_review` 的申報、安全地取得憑證圖片、匯出資料，並留下不可竄改的審核軌跡。

## 設計
- **狀態：** `expenses.review_status` ∈ {not_required, pending, approved, rejected}。`needs_review=1` 者初始為 `pending`。
- **端點（皆需 reviewer／admin）：**
  - `GET /api/v1/review/pending` 列出待審（分頁，僅必要欄位）。
  - `POST /api/v1/review/{id}/decision` body `{decision: approve|reject, comment}`；rejected 必須有 comment。
  - `GET /api/v1/files/{id}/{kind}` kind ∈ receipt|signature|voucher，經角色檢查才回傳圖片，`Cache-Control: no-store`。
- **職責分離：** 審核人不得審核自己名下（同一裝置／同一員工名稱）的申報。
- **稽核軌跡：** `review_events`（只新增、不更新不刪除）：事件、審核人裝置、時間、舊新狀態、comment。
- **狀態轉換：** pending → approved／rejected；已決定者不可再改（需 admin 以「重新開啟」事件，且留下記錄）。重複送出相同決定 → 冪等。
- **匯出：** reviewer／admin；欄位白名單（不含檔案路徑、不含簽名內容）；可依日期、案號、狀態篩選；每次匯出寫入稽核記錄；沿用公式注入防護。
- **分類規則確認：** 沿用 M1 的 reviewer 限制，並記入 `review_events`。
- **前台：** 審核畫面（清單、檢視憑證圖、核准／退回），所有顯示轉義（見 M2）。

## 驗收條件
1. submitter 呼叫任何 review／files／export 端點 → 403。
2. 無單據 1,001 元申報出現在 `pending`；1,000 元不出現。
3. approve／reject 後狀態正確；reject 不帶 comment → 422；再次決定 → 409（相同決定重送為冪等 200）。
4. 審核人審自己的申報 → 403。
5. `review_events` 無法被應用程式碼更新或刪除（以資料庫觸發器或測試驗證）。
6. `files` 端點：未授權 403；不存在 404；回應含 `no-store`；無法以 `../` 取得其他檔案。
7. 匯出 CSV 不含路徑欄位；以 `=cmd|...` 開頭的店名在 CSV 中被中和；匯出動作有稽核記錄。
8. Playwright：以 reviewer 身分在審核畫面完成一次核准與一次退回，狀態與圖片顯示正確；注入 XSS 店名不執行。
9. 既有測試仍全綠。

## 規格挑戰
- 金額為何不採「主管金額門檻」以外的規則？（例如特定分類必審）→ 待使用者決定是否需要。
- 審核人與申報人是同一個人（6 人公司可能只有一位主管）：職責分離會造成主管自己的申報無人可審 → **待決定**：由 admin 代審並加註？
- 憑證圖片是個資，`files` 端點每次存取都應記錄。

## 待使用者決定
- Q7：誰是 reviewer？主管本人的申報由誰審？
- Q8：除了「無單據 >1,000」，還有哪些情況必須主管審？
