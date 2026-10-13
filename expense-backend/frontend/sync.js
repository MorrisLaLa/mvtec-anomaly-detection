/* 離線佇列 + 真實 API 同步。可直接 <script src="sync.js"> 嵌入既有 PWA。
 * 用法：ExpenseSync.configure({SERVER_URL: 'http://100.x.y.z:8000'});
 *       await ExpenseSync.submit({fields: {client_id, case_no, amount, ...}, receipt: Blob, signature: Blob});
 * 規則：HTTP 200 → 移出佇列；5xx／網路錯誤／逾時 → 保留並以遞增間隔重試（2s,4s,8s…上限60s）；
 *       4xx → 判定最終失敗：不重試、移入 failed 清單並回報錯誤訊息（資料保留，由使用者按「重送」或「刪除」）。
 * 上傳策略（預設僅 Wi‑Fi）：Android Chrome 可由 navigator.connection.type 判斷；iPhone Safari 無此 API → 「手動上傳」模式。
 *       等待 Wi‑Fi 不是失敗，不計入重試。 */
(function (global) {
  const cfg = { SERVER_URL: '', onFlush: null, TIMEOUT_MS: 10000, RETRY_BASE_MS: 2000, RETRY_MAX_MS: 60000 };
  const DB = 'expense-sync', STORE = 'queue', FAILED = 'failed';
  let attempt = 0, timer = null, flushing = null, again = false;

  function idb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(DB, 2);
      r.onupgradeneeded = () => {
        const d = r.result;
        if (!d.objectStoreNames.contains(STORE)) d.createObjectStore(STORE, { keyPath: 'fields.client_id' });
        if (!d.objectStoreNames.contains(FAILED)) d.createObjectStore(FAILED, { keyPath: 'fields.client_id' });
      };
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  async function tx(mode, fn, store = STORE) {
    const db = await idb();
    return new Promise((res, rej) => {
      const t = db.transaction(store, mode), s = t.objectStore(store);
      const out = fn(s);
      t.oncomplete = () => { db.close(); res(out && out.result); };
      t.onerror = () => { db.close(); rej(t.error); };
    });
  }
  const all = () => tx('readonly', (s) => s.getAll());
  const put = (item) => tx('readwrite', (s) => s.put(item));
  const del = (k) => tx('readwrite', (s) => s.delete(k));
  const allFailed = () => tx('readonly', (s) => s.getAll(), FAILED);
  const putFailed = (item) => tx('readwrite', (s) => s.put(item), FAILED);
  const delFailed = (k) => tx('readwrite', (s) => s.delete(k), FAILED);

  // FastAPI 的 detail 可能是字串或 [{loc, msg}]，轉成給人看的訊息
  async function errorMessage(resp) {
    try {
      const j = await resp.json();
      if (typeof j.detail === 'string') return j.detail;
      if (Array.isArray(j.detail)) return j.detail.map((d) => `${(d.loc || []).slice(1).join('.')}: ${d.msg}`).join('; ');
    } catch (e) { /* 非 JSON */ }
    return `HTTP ${resp.status}`;
  }

  // ---- 裝置金鑰、上傳策略、網路判斷 ----
  const KEY_NAME = 'expense_device_key', POLICY_NAME = 'expense_upload_policy';
  const lsGet = (k) => { try { return global.localStorage.getItem(k); } catch (e) { return null; } };
  const lsSet = (k, v) => { try { global.localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } };
  const getKey = () => lsGet(KEY_NAME);
  const setKey = (k) => lsSet(KEY_NAME, k);
  const getPolicy = () => (lsGet(POLICY_NAME) === 'any' ? 'any' : 'wifi_only');
  const setPolicy = (p) => lsSet(POLICY_NAME, p === 'any' ? 'any' : 'wifi_only');

  /* 'wifi' | 'cellular' | 'other' | 'unsupported'（API 不存在，如 iPhone Safari，或桌機 Chrome 不提供 type） */
  function networkKind() {
    const c = global.navigator && global.navigator.connection;
    if (!c || typeof c.type !== 'string') return 'unsupported';
    if (c.type === 'wifi' || c.type === 'ethernet') return 'wifi';
    if (c.type === 'cellular') return 'cellular';
    return 'other'; // none／unknown／bluetooth…：保守視為非 Wi‑Fi
  }
  const autoAllowed = () => getPolicy() === 'any' || networkKind() === 'wifi';

  // ---- 圖片壓縮（長邊上限、JPEG 品質）；失敗時回傳原檔 ----
  async function compressImage(blob, opts = {}) {
    const maxEdge = opts.maxEdge || 1600, type = opts.type || 'image/jpeg', quality = opts.quality || 0.8;
    try {
      const bmp = await createImageBitmap(blob);
      const scale = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
      const w = Math.max(1, Math.round(bmp.width * scale)), h = Math.max(1, Math.round(bmp.height * scale));
      if (scale === 1 && type === blob.type) { bmp.close && bmp.close(); return blob; }
      const cv = global.document.createElement('canvas');
      cv.width = w; cv.height = h;
      const ctx = cv.getContext('2d');
      if (type === 'image/jpeg') { ctx.fillStyle = '#FFFFFF'; ctx.fillRect(0, 0, w, h); } // JPEG 無透明：先鋪白底
      ctx.drawImage(bmp, 0, 0, w, h);
      bmp.close && bmp.close();
      const out = await new Promise((res) => cv.toBlob(res, type, quality));
      return out && out.size < blob.size ? out : blob;
    } catch (e) { return blob; }
  }

  async function send(item) {
    const fd = new FormData();
    Object.entries(item.fields).forEach(([k, v]) => v != null && fd.append(k, v));
    // 上傳前壓縮以節省流量：收據照片轉 JPEG；簽名／憑證保持 PNG（有透明度），僅縮小尺寸
    if (item.receipt) fd.append('receipt', await compressImage(item.receipt), 'receipt.jpg');
    if (item.signature) fd.append('signature', await compressImage(item.signature, { type: 'image/png' }), 'signature.png');
    if (item.voucher) fd.append('voucher', await compressImage(item.voucher, { type: 'image/png' }), 'voucher.png');
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), cfg.TIMEOUT_MS);
    try {
      const key = getKey();
      const headers = key ? { Authorization: 'Bearer ' + key } : {};
      const resp = await fetch(cfg.SERVER_URL + '/api/v1/submit', { method: 'POST', body: fd, headers, signal: ctl.signal });
      if (resp.status === 200) return { ok: true, body: await resp.json().catch(() => ({})) };
      if (resp.status >= 400 && resp.status < 500) {
        const ra = parseInt(resp.headers.get('Retry-After') || '', 10);
        return { ok: false, final: true, status: resp.status, message: await errorMessage(resp), retryAfter: isNaN(ra) ? null : ra };
      }
      return { ok: false, final: false, status: resp.status }; // 5xx 與其他：可重試
    } catch (e) {
      return { ok: false, final: false }; // 網路錯誤／逾時：可重試
    } finally {
      clearTimeout(to);
    }
  }

  function scheduleRetry() {
    clearTimeout(timer);
    const delay = Math.min(cfg.RETRY_BASE_MS * 2 ** attempt, cfg.RETRY_MAX_MS);
    attempt++;
    timer = setTimeout(flush, delay);
  }

  /* 依序送出佇列；回傳 {sent, remaining, results}。有失敗就排程遞增重試。 */
  function flush(opts) {
    opts = opts && typeof opts === 'object' && !(opts instanceof Event) ? opts : {};
    if (flushing) { again = true; return flushing; } // 進行中又有新項目：本輪結束後再跑一輪，避免新項目卡在佇列
    flushing = (async () => {
      let summary;
      do { again = false; summary = await flushOnce(opts); } while (again && !summary.stalled);
      return summary;
    })().finally(() => { flushing = null; });
    return flushing;
  }

  async function flushOnce(opts = {}) {
    {
      const results = {}, failedNow = [];
      let retry = false;
      let items = (await all()).sort((a, b) => a.queued_at - b.queued_at);
      if (opts.only) items = items.filter((i) => i.fields.client_id === opts.only);
      // 上傳策略：非 Wi‑Fi 且使用者沒有明確要求（force／only）→ 全部保留等待；這不是失敗，不排程重試
      if (!opts.force && !opts.only && !autoAllowed()) {
        const held = (await all()).length;
        const summary = { sent: 0, remaining: held, results, failed: failedNow, stalled: false, held, networkKind: networkKind() };
        if (cfg.onFlush) { try { cfg.onFlush(summary); } catch (e) { /* UI 錯誤不影響同步 */ } }
        return summary;
      }
      for (const item of items) {
        const r = await send(item);
        const k = item.fields.client_id;
        if (r.ok) { await del(k); results[k] = r.body; }
        else if (r.final) { // 4xx：重試結果不會變，移到 failed 並回報
          await putFailed({ ...item, status: r.status, message: r.message, retryAfter: r.retryAfter, failed_at: Date.now() });
          await del(k);
          failedNow.push({ client_id: k, status: r.status, message: r.message, retryAfter: r.retryAfter });
        } else { retry = true; break; } // 可重試錯誤：保持順序，停在這筆
      }
      const remaining = (await all()).length;
      if (retry) scheduleRetry(); else { attempt = 0; clearTimeout(timer); }
      const summary = { sent: Object.keys(results).length, remaining, results, failed: failedNow, stalled: retry, held: 0, networkKind: networkKind() };
      if (cfg.onFlush) { try { cfg.onFlush(summary); } catch (e) { /* UI 錯誤不影響同步 */ } }
      return summary;
    }
  }

  /* 使用者按「重送」：把失敗項目放回佇列再送（仍遵守上傳策略）；不傳 id 則全部重送 */
  async function retryFailed(client_id) {
    const list = (await allFailed()).filter((i) => !client_id || i.fields.client_id === client_id);
    for (const it of list) {
      const { status, message, retryAfter, failed_at, ...rest } = it;
      await put({ ...rest, queued_at: Date.now() });
      await delFailed(it.fields.client_id);
    }
    return flush();
  }

  async function enqueueOnly(item) {
    if (!item.fields || !item.fields.client_id) throw new Error('client_id required');
    await put({ ...item, queued_at: Date.now() });
  }
  async function submit(item) {
    await enqueueOnly(item);
    return flush();
  }

  global.addEventListener && global.addEventListener('online', () => { attempt = 0; flush(); });
  // Android：連線類型改變（例如連上 Wi‑Fi）時自動重新嘗試
  try { global.navigator && global.navigator.connection && global.navigator.connection.addEventListener('change', () => flush()); } catch (e) { /* 不支援 */ }
  global.ExpenseSync = {
    configure: (o) => Object.assign(cfg, o),
    submit, enqueueOnly, flush, retryFailed, compressImage,
    getKey, setKey, getPolicy, setPolicy, networkKind, autoAllowed,
    pending: async () => (await all()).length,
    pendingItems: async () => (await all()).map((i) => ({ client_id: i.fields.client_id, meta: i.meta })),
    failed: async () => (await allFailed()).map((i) => ({ client_id: i.fields.client_id, status: i.status, message: i.message, retryAfter: i.retryAfter, meta: i.meta })),
    dismissFailed: (client_id) => delFailed(client_id),
  };
})(typeof window !== 'undefined' ? window : globalThis);
