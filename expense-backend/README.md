# 支出申報後端

```
pip install -r requirements.txt   # 另需系統套件 libzbar0（apt-get install libzbar0）
python -m pytest                  # 62 個測試（含 Playwright 端到端）
uvicorn app.main:app --port 8000  # PWA 示範頁：http://localhost:8000/pwa/
```
設定：`config/settings.yaml`（記得改 `my_company_tax_id`），環境變數 `EXPENSE_DB_PATH`、`EXPENSE_STORAGE_DIR`、`EXPENSE_CONFIG`。
