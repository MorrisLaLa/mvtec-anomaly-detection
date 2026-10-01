/* 離線佇列 + 真實 API 同步。可直接 <script src="sync.js"> 嵌入既有 PWA。
 * 用法：ExpenseSync.configure({SERVER_URL: 'http://100.x.y.z:8000'});
 *       await ExpenseSync.submit({fields: {case_no, amount, ...}, receipt: Blob, signature: Blob});
 * 規則：只有 HTTP 200 才從佇列移除；失敗/逾時保留，並以遞增間隔重試（2s,4s,8s…上限60s）。 */
(function (global) {
  const cfg = { SERVER_URL: '', TIMEOUT_MS: 10000, RETRY_BASE_MS: 2000, RETRY_MAX_MS: 60000 };
  const DB = 'expense-sync', STORE = 'queue';
  let attempt = 0, timer = null, flushing = null;

  function idb() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => r.result.createObjectStore(STORE, { keyPath: 'fields.case_no' });
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
  }
  async function tx(mode, fn) {
    const db = await idb();
    return new Promise((res, rej) => {
      const t = db.transaction(STORE, mode), s = t.objectStore(STORE);
      const out = fn(s);
      t.oncomplete = () => { db.close(); res(out && out.result); };
      t.onerror = () => { db.close(); rej(t.error); };
    });
  }
  const all = () => tx('readonly', (s) => s.getAll());
  const put = (item) => tx('readwrite', (s) => s.put(item));
  const del = (k) => tx('readwrite', (s) => s.delete(k));

  async function send(item) {
    const fd = new FormData();
    Object.entries(item.fields).forEach(([k, v]) => v != null && fd.append(k, v));
    if (item.receipt) fd.append('receipt', item.receipt, 'receipt.png');
    if (item.signature) fd.append('signature', item.signature, 'signature.png');
    const ctl = new AbortController();
    const to = setTimeout(() => ctl.abort(), cfg.TIMEOUT_MS);
    try {
      const resp = await fetch(cfg.SERVER_URL + '/api/v1/submit', { method: 'POST', body: fd, signal: ctl.signal });
      if (resp.status !== 200) return { ok: false };
      return { ok: true, body: await resp.json().catch(() => ({})) };
    } catch (e) {
      return { ok: false };
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
      const results = {};
      let failed = false;
      for (const item of (await all()).sort((a, b) => a.queued_at - b.queued_at)) {
        const r = await send(item);
        if (r.ok) { await del(item.fields.case_no); results[item.fields.case_no] = r.body; }
        else { failed = true; break; } // 保持順序：前一筆失敗就停
      }
      const remaining = (await all()).length;
      if (failed) scheduleRetry(); else { attempt = 0; clearTimeout(timer); }
      return { sent: Object.keys(results).length, remaining, results };
    })().finally(() => { flushing = null; });
    return flushing;
  }

  async function submit(item) {
    if (!item.fields || !item.fields.case_no) throw new Error('case_no required');
    await put({ ...item, queued_at: Date.now() });
    return flush();
  }

  global.addEventListener && global.addEventListener('online', () => { attempt = 0; flush(); });
  global.ExpenseSync = {
    configure: (o) => Object.assign(cfg, o),
    submit, flush,
    pending: async () => (await all()).length,
  };
})(typeof window !== 'undefined' ? window : globalThis);
