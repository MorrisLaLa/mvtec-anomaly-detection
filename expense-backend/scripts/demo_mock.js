/* 示範用模擬伺服器：攔截 /api/v1/* 的 fetch，在瀏覽器內模擬後端（驗證、角色、自動審核、審核、圖片）。
 * 只用於 demo 頁；正式版 expense-demo.html 不含此檔。資料存在 localStorage，不會送到任何地方。 */
(function () {
  const realFetch = window.fetch.bind(window);
  const KEYS = { ek_demo_submitter: { name: '王志明', role: 'submitter' }, ek_demo_reviewer: { name: '陳主管', role: 'reviewer' } };
  const STORE = 'demo_server_v1';
  const sim = { offline: false, force: 'ok', latency: 350 };   // force: ok | 503 | 429
  window.__demoSim = sim;
  const taipeiDay = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);

  let state = null;
  try { state = JSON.parse(localStorage.getItem(STORE) || 'null'); } catch (e) { /* 無儲存 */ }
  if (!state) {
    const d = taipeiDay();
    const seed = (id, employee, store, amount, doc_type, status, reasons, sampled) => ({
      id, client_id: 'seed-' + id, employee, store, amount, category: doc_type === '無單據' ? '餐飲雜支' : '交通油料',
      doc_type, case_no: 'UY2301', owner_case_no: 'CPC-2026-042', created_day: d, review_status: status,
      review_reasons: reasons, sampled, reviewed_by: status === 'approved' ? 'auto:v1' : null, files: {}, device: 'seed' });
    state = { seq: 4, items: [
      seed(1, '李俊賢', '現場零星採買', 1500, '無單據', 'held', ['無單據金額超過 NT$1,000，需人工審核'], false),
      seed(2, '張家豪', '阿明小吃部', 180, '收據', 'held', ['沒有可自動驗證的電子發票 QR，需人工審核'], false),
      seed(3, '陳建宏', '中油 二仁溪站', 980, '統一發票', 'approved', [], true)] };
  }
  const blobs = {};  // id -> {signature, voucher}（不持久化）
  const save = () => { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* 略過 */ } };
  const json = (status, body, headers = {}) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
  const pub = (it) => { const { device, ...rest } = it; return rest; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function decide(it) {
    const r = [];
    if (it.doc_type === '統一發票') {   // 示範：拍照辨識為模擬，視為 QR 驗證成功
      if (state.items.some((x) => x.store === it.store && x.amount === it.amount && x.doc_type === '統一發票' && x.review_status !== 'rejected'))
        return { status: 'rejected', reasons: ['重複報銷：這張發票已經有申報'] };
      if (it.amount > 5000) r.push('金額超過單筆自動核准上限 NT$5,000');
    } else if (it.doc_type === '無單據') {
      if (it.amount > 1000) r.push('無單據金額超過 NT$1,000，需人工審核');
    } else r.push('沒有可自動驗證的電子發票 QR，需人工審核');
    return r.length ? { status: 'held', reasons: r } : { status: 'approved', reasons: [] };
  }

  window.fetch = async function (url, opts = {}) {
    const u = String(url);
    const i = u.indexOf('/api/v1/');
    if (i < 0) return realFetch(url, opts);
    const path = u.slice(i + 7).split('?')[0], query = new URLSearchParams(u.split('?')[1] || '');
    const method = (opts.method || 'GET').toUpperCase();
    await sleep(sim.latency);
    if (sim.offline) throw new TypeError('Failed to fetch');
    const auth = ((opts.headers || {}).Authorization || '').replace(/^Bearer /, '');
    const user = KEYS[auth];
    if (!user) return json(401, { detail: 'unauthorized' });
    if (path === '/rules') return json(200, []);
    if (path === '/me') return json(200, { ...user, expires_in_days: 340 });
    if (path === '/submit' && method === 'POST') {
      if (sim.force === '503') return new Response('unavailable', { status: 503 });
      if (sim.force === '429') return json(429, { detail: 'too many requests' }, { 'Retry-After': '120' });
      const fd = opts.body, g = (k) => (fd.get(k) || '').toString().trim();
      const cid = g('client_id');
      const dup = state.items.find((x) => x.device === user.name && x.client_id === cid);
      if (dup) return json(200, { ok: true, duplicate: true, ...pub(dup), needs_review: dup.review_status === 'held' });
      if (!/^[0-9]{1,9}$/.test(g('amount')) || parseInt(g('amount'), 10) <= 0) return json(422, { detail: 'amount 必須為正整數' });
      const it = { id: state.seq++, client_id: cid, employee: user.name, store: g('store').slice(0, 100), amount: parseInt(g('amount'), 10),
        category: g('category'), doc_type: g('doc_type'), case_no: g('case_no'), owner_case_no: g('owner_case_no'),
        created_day: taipeiDay(), device: user.name, files: { signature: !!fd.get('signature'), voucher: !!fd.get('voucher') } };
      const d = decide(it);
      Object.assign(it, { review_status: d.status, review_reasons: d.reasons, reviewed_by: d.status === 'held' ? null : 'auto:v1',
        sampled: d.status === 'approved' && it.id % 5 === 0 });
      blobs[it.id] = { signature: fd.get('signature'), voucher: fd.get('voucher') };
      state.items.push(it); save();
      return json(200, { ok: true, duplicate: false, ...pub(it), needs_review: d.status === 'held' });
    }
    if (user.role === 'submitter') return json(403, { detail: 'forbidden' });
    if (path === '/review/items') {
      const st = query.get('status') || 'held', sp = query.get('sampled');
      return json(200, state.items.filter((x) => x.review_status === st && (sp === null || !!x.sampled === (sp === '1'))).map(pub));
    }
    let m = path.match(/^\/review\/(\d+)\/decision$/);
    if (m && method === 'POST') {
      const it = state.items.find((x) => x.id === +m[1]);
      if (!it) return json(404, { detail: '找不到這筆申報' });
      const body = JSON.parse(opts.body), target = body.decision === 'approve' ? 'approved' : 'rejected';
      if (target === 'rejected' && !(body.comment || '').trim()) return json(422, { detail: '退回必須填寫理由' });
      if (it.review_status === target) return json(200, { ok: true, unchanged: true });
      if (!(it.review_status === 'held' || (it.review_status === 'approved' && it.sampled && target === 'rejected'))) return json(409, { detail: '這筆已決定，無法再變更' });
      if (it.employee === user.name) return json(403, { detail: '不可審核自己的申報' });
      Object.assign(it, { review_status: target, reviewed_by: user.name, review_comment: body.comment || null,
        review_reasons: target === 'rejected' ? [body.comment] : it.review_reasons });
      save();
      return json(200, { ok: true, review_status: target });
    }
    m = path.match(/^\/files\/(\d+)\/(\w+)$/);
    if (m) {
      const b = (blobs[+m[1]] || {})[m[2]];
      return b ? new Response(b, { status: 200, headers: { 'content-type': 'image/png', 'cache-control': 'no-store' } }) : json(404, { detail: 'not found' });
    }
    return json(404, { detail: 'not found' });
  };

  // 檢視器會略過 confirm()／prompt()：示範時自動確認，並用提示告訴使用者
  const note = (t) => { try { showToast('示範模式：' + t); } catch (e) { /* 頁面尚未載入 */ } };
  window.confirm = (m) => { note('自動選「確定」（' + m.replace(/。.*/, '') + '）'); return true; };
  window.prompt = (m) => { note('已自動填入退回理由'); return '缺少單據，請補件'; };

  // ---- 手機網路模擬（取代 navigator.connection）----
  const conn = new EventTarget(); conn.type = 'wifi';
  const setConn = (t) => {
    if (t === 'none') { Object.defineProperty(navigator, 'connection', { value: undefined, configurable: true }); return; }
    conn.type = t;
    Object.defineProperty(navigator, 'connection', { value: conn, configurable: true });
    conn.dispatchEvent(new Event('change'));
  };
  window.__demoNet = setConn;
  setConn('wifi');
  window.__demoReset = () => {
    try { localStorage.clear(); sessionStorage.clear(); } catch (e) { /* 略過 */ }
    try { indexedDB.deleteDatabase('expense-sync'); } catch (e) { /* 略過 */ }
    setTimeout(() => location.reload(), 200);
  };
})();
