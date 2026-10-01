/* 離線佇列 + 真實 API 同步。可直接 <script src="sync.js"> 嵌入既有 PWA。
 * 用法：ExpenseSync.configure({SERVER_URL: 'http://100.x.y.z:8000'});
 *       await ExpenseSync.submit({fields: {case_no, amount, ...}, receipt: Blob, signature: Blob});
 * 規則：HTTP 200 → 移出佇列；5xx／網路錯誤／逾時 → 保留並以遞增間隔重試（2s,4s,8s…上限60s）；
 *       4xx → 判定最終失敗：不重試、移入 failed 清單並回報錯誤訊息（資料保留供使用者修正後重送）。 */
(function (global) {
  const cfg = { SERVER_URL: '', TIMEOUT_MS: 10000, RETRY_BASE_MS: 2000, RETRY_MAX_MS: 60000 };
  const DB = 'expense-sync', STORE = 'queue', FAILED = 'failed';
  let attempt = 0, timer = null, flushing = null;

  function idb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(DB, 2);
      r.onupgradeneeded = () => {
        const d = r.result;
        if (!d.objectStoreNames.contains(STORE)) d.createObjectStore(STORE, { keyPath: 'fields.case_no' });
        if (!d.objectStoreNames.contains(FAILED)) d.createObjectStore(FAILED, { keyPath: 'fields.case_no' });
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

  async function send(item) {
    const fd = new FormData();
    Object.entries(item.fields).forEach(([k, v]) => v != null && fd.append(k, v));
    if (item.receipt) fd.append('receipt', item.receipt, 'receipt.png');
    if (item.signature) fd.append('signature', item.signature, 'signature.png');
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), cfg.TIMEOUT_MS);
    try {
      const resp = await fetch(cfg.SERVER_URL + '/api/v1/submit', { method: 'POST', body: fd, signal: ctl.signal });
      if (resp.status === 200) return { ok: true, body: await resp.json().catch(() => ({})) };
      if (resp.status >= 400 && resp.status < 500) {
        return { ok: false, final: true, status: resp.status, message: await errorMessage(resp) };
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
  function flush() {
    if (flushing) return flushing;
    flushing = (async () => {
      const results = {}, failedNow = [];
      let retry = false;
      for (const item of (await all()).sort((a, b) => a.queued_at - b.queued_at)) {
        const r = await send(item);
        const k = item.fields.case_no;
        if (r.ok) { await del(k); results[k] = r.body; }
        else if (r.final) { // 4xx：重試結果不會變，移到 failed 並回報
          await putFailed({ ...item, status: r.status, message: r.message, failed_at: Date.now() });
          await del(k);
          failedNow.push({ case_no: k, status: r.status, message: r.message });
        } else { retry = true; break; } // 可重試錯誤：保持順序，停在這筆
      }
      const remaining = (await all()).length;
      if (retry) scheduleRetry(); else { attempt = 0; clearTimeout(timer); }
      return { sent: Object.keys(results).length, remaining, results, failed: failedNow };
    })().finally(() => { flushing = null; });
    return flushing;
  }

  async function enqueueOnly(item) {
    if (!item.fields || !item.fields.case_no) throw new Error('case_no required');
    await put({ ...item, queued_at: Date.now() });
  }
  async function submit(item) {
    await enqueueOnly(item);
    return flush();
  }

  global.addEventListener && global.addEventListener('online', () => { attempt = 0; flush(); });
  global.ExpenseSync = {
    configure: (o) => Object.assign(cfg, o),
    submit, enqueueOnly, flush,
    pending: async () => (await all()).length,
    failed: async () => (await allFailed()).map((i) => ({ case_no: i.fields.case_no, status: i.status, message: i.message })),
    dismissFailed: (case_no) => delFailed(case_no),
  };
})(typeof window !== 'undefined' ? window : globalThis);
