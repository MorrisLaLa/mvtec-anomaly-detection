# 不需要網域的 HTTPS：Tailscale Funnel

Funnel 會在你的機器上終結 TLS，並給一個固定的 `https://<機器>.<tailnet>.ts.net` 網址，**公開可連、現場人員的手機不用安裝 Tailscale**。我們的 Caddy 只在本機 `127.0.0.1:8080` 提供 HTTP，繼續負責安全標頭與請求本體上限（413）。

## 前置條件（來自 Tailscale 官方文件的整理，上線前請**核對**官方頁面 https://tailscale.com/kb/1223/funnel ）
1. 機器安裝 Tailscale（版本 ≥ 1.38.3）並登入你的 tailnet。
2. 在 tailnet 開啟 **MagicDNS** 與 **HTTPS** 憑證功能。
3. 在 tailnet 的存取政策（ACL）為該機器加上 **funnel** 節點屬性。
4. Funnel 只能使用 `ts.net` 網域名稱，只能監聽 443、8443、10000 埠。
5. Funnel 有不可設定的頻寬上限（官方未公布數字）。

## 使用
先在專案根目錄的 `.env` 設定 `COMPOSE_PROFILES=funnel`（有網域則是 `domain`，並設定 `PUBLIC_HOSTNAME`、`BACKUP_BUCKET`）。這樣單純的 `docker compose up -d`（包括 `deploy/rollback.sh`）都會一併啟動入口代理；沒設定的話只會啟動 app 與 ops，**外面連不進來**。
```
deploy/funnel/enable.sh --dry-run   # 先看會執行什麼
deploy/funnel/enable.sh
```
取得網址後，用它產生配對連結：`python -m app.admin create-device ... --base-url https://<機器>.<tailnet>.ts.net`。

## 尚未驗證／要注意（誠實說明）
- **全部從未在真實機器上跑過**；`tailscale funnel --bg 8080` 的語法與行為依我對文件的理解，未實測。
- **免費方案是否可用 Funnel**：只有第三方文章說個人免費方案可用，**官方頁面我沒能確認**，請在 Tailscale 價格頁核對。
- **用戶端 IP 與限流**：應用程式用 `X-Forwarded-For` 判斷來源 IP 做失敗封鎖（M1）。經過 Funnel 再經過 Caddy 後，應用程式看到的 IP 可能是代理而不是真正的手機 IP——若是這樣，所有人會共用同一個失敗計數。上線前必須用 `X-Forwarded-For` 實際檢查（發一個錯誤金鑰請求，看存取記錄裡的 IP），有問題要先修再開放。
- Funnel 的流量與可用性依賴 Tailscale 服務。
