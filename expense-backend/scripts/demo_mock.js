/* 示範用模擬伺服器：攔截 /api/v1/* 的 fetch，在瀏覽器內模擬後端（驗證、角色、自動審核、審核、圖片）。
 * 只用於 demo 頁；正式版 expense-demo.html 不含此檔。資料存在 localStorage，不會送到任何地方。 */
(function () {
  const realFetch = window.fetch.bind(window);
  const KEYS = { ek_demo_submitter: '王志明', ek_demo_reviewer: '林會計', ek_demo_admin: '管理員' };  // 金鑰 → 裝置名稱；角色看 state.devices
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
  if (!state.devices) state.devices = [
    { id: 1, name: '王志明', role: 'submitter' }, { id: 2, name: '林會計', role: 'reviewer' }, { id: 3, name: '管理員', role: 'admin' }];
  const admins = () => state.devices.filter((d) => d.role === 'admin').length;
  const blobs = {};  // id -> {signature, voucher}（不持久化）
  const save = () => { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* 略過 */ } };
  const json = (status, body, headers = {}) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
  const pub = (it) => { const { device, ...rest } = it; return rest; };
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const hasReceipt = (x) => !!x.doc_type && x.doc_type !== '無單據';
  // 處理紀錄（與正式版相同的中文動作與狀態）
  const ST = { approved: '已核准', held: '待審核', rejected: '已退回' };
  const nowTaipei = () => new Date(Date.now() + 8 * 3600e3).toISOString().replace('T', ' ').slice(0, 19);
  const events = (x) => {  // 舊的示範資料沒有紀錄：依目前狀態補一筆自動判斷
    if (!x.events) x.events = [{ time: (x.created_day || '') + ' 09:00:00', action: x.review_status === 'held' ? '轉人工審核' : '自動審核',
      actor: '自動（v1）', from: '', to: x.review_status === 'held' ? 'held' : x.reviewed_by === 'auto:v1' ? 'approved' : 'held', comment: '' }];
    return x.events;
  };
  const addEvent = (x, action, actor, from, to, comment) => events(x).push({ time: nowTaipei(), action, actor, from, to, comment: comment || '' });
  // 示範沒有真的照片：依申報內容畫一張標明「示範」的發票圖，讓主管能試「看收據」
  function demoReceipt(it) {
    const cv = document.createElement('canvas'); cv.width = 480; cv.height = 640;
    const g = cv.getContext('2d');
    g.fillStyle = '#fff'; g.fillRect(0, 0, 480, 640);
    g.strokeStyle = '#999'; g.lineWidth = 2; g.strokeRect(10, 10, 460, 620);
    g.fillStyle = '#111'; g.textAlign = 'center';
    g.font = 'bold 26px sans-serif'; g.fillText(it.doc_type === '統一發票' ? '電子發票證明聯' : '收據', 240, 70);
    g.font = '18px sans-serif'; g.fillText(it.store || '（未填店家）', 240, 120);
    g.font = 'bold 40px sans-serif'; g.fillText('NT$ ' + Number(it.amount || 0).toLocaleString('en-US'), 240, 220);
    g.font = '18px sans-serif'; g.fillText(it.created_day || '', 240, 270);
    g.fillStyle = '#C1440E'; g.font = 'bold 30px sans-serif'; g.fillText('示範圖片・非真實單據', 240, 420);
    return new Promise((res) => cv.toBlob(res, 'image/png'));
  }

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
    const dev = state.devices.find((d) => d.name === KEYS[auth]);
    if (!dev) return json(401, { detail: 'unauthorized' });
    const user = { name: dev.name, role: dev.role };
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
      it.events = [{ time: nowTaipei(), action: d.status === 'held' ? '轉人工審核' : '自動審核', actor: '自動（v1）', from: '', to: d.status, comment: d.reasons.join('；') }];
      state.items.push(it); save();
      return json(200, { ok: true, duplicate: false, ...pub(it), needs_review: d.status === 'held' });
    }
    if (user.role === 'submitter') return json(403, { detail: 'forbidden' });
    if (path.startsWith('/admin/')) {  // 與正式版相同的規則：管理員 1–2 位、手機上不能拿掉另一位管理員
      if (user.role !== 'admin') return json(403, { detail: 'forbidden' });
      const body = opts.body ? JSON.parse(opts.body) : {};
      const target = state.devices.find((d) => d.id === body.device_id);
      if (path === '/admin/unblock') return json(200, { ok: true, cleared: 0 });
      if (path === '/admin/devices') return json(200, state.devices.map((d) => ({ ...d, status: '有效', is_me: d.id === dev.id })));
      if (!target) return json(404, { detail: '找不到這台裝置' });
      if (path === '/admin/role') {
        if (target.role === 'admin' && target.id !== dev.id && body.role !== 'admin')
          return json(403, { detail: '手機上不能拿掉另一位管理員，請在主機上執行 python -m app.admin set-role' });
        if (body.role === 'admin' && target.role !== 'admin' && admins() >= 2) return json(409, { detail: '管理員最多 2 位，請先把其中一位降級或改用轉移' });
        if (target.role === 'admin' && body.role !== 'admin' && admins() <= 1) return json(409, { detail: '管理員至少要保留 1 位，請先增加另一位管理員或改用轉移' });
        target.role = body.role; save();
        return json(200, { ok: true });
      }
      if (path === '/admin/transfer') {
        if (target.id === dev.id || target.role === 'admin') return json(409, { detail: '對方已經是管理員' });
        target.role = 'admin'; dev.role = 'reviewer'; save();
        return json(200, { ok: true });
      }
    }
    if (path === '/export') {  // 與正式版相同的欄位與公式防護；依申報日期篩選
      const cols = ['id', 'client_id', 'case_no', 'owner_case_no', 'doc_type', 'employee', 'amount', 'expense_date', 'store',
        'seller_tax_id', 'buyer_tax_id', 'invoice_no', 'invoice_date', 'category', 'category_source', 'recognition_source',
        'recognition_confidence', 'needs_review', 'review_status', 'reviewed_by', 'reviewed_at', 'review_reasons',
        'review_comment', 'sampled', 'created_day'];
      const head = ['編號', '申報識別碼', '案號', '業主案號', '單據類型', '申報人', '金額', '支出日期', '店家', '賣方統編',
        '買方統編', '發票號碼', '發票日期', '用途', '用途來源', '辨識方式', '辨識信心', '需人工審核', '審核狀態',
        '審核者', '審核時間', '審核理由', '審核意見', '抽查', '申報日期'];
      const VALS = { review_status: { approved: '已核准', held: '待審核', rejected: '已退回' } };
      const val = (c, v) => {
        if (c === 'needs_review' || c === 'sampled') return v ? '是' : '否';
        if (c === 'reviewed_by' && typeof v === 'string' && v.startsWith('auto:')) return '自動（' + v.slice(5) + '）';
        if (c === 'review_reasons') return (v || []).join('；');
        return (VALS[c] || {})[v] ?? v;
      };
      const f = query.get('from') || '', t = query.get('to') || '';
      const cell = (v) => {
        if (v === undefined || v === null) return '';
        let s = String(v);
        if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
        return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      };
      const rows = state.items.filter((x) => (!f || x.created_day >= f) && (!t || x.created_day <= t))
        .map((x) => cols.map((c) => cell(val(c, c === 'needs_review' ? x.review_status === 'held' : x[c]))).join(','));
      return new Response([head.join(','), ...rows].join('\r\n') + '\r\n', { status: 200, headers: { 'content-type': 'text/csv; charset=utf-8' } });
    }
    if (path === '/review/bulk-approve' && method === 'POST') {
      const approved = [], skipped = [];
      for (const id of JSON.parse(opts.body).ids || []) {
        const it = state.items.find((x) => x.id === id);
        const reason = !it ? '找不到' : it.employee === user.name ? '自己的申報' : it.review_status !== 'held' ? '已決定' : null;
        if (reason) { skipped.push({ id, reason }); continue; }
        addEvent(it, '人工審核', user.name, 'held', 'approved', '批次核准');
        Object.assign(it, { review_status: 'approved', reviewed_by: user.name, review_comment: '批次核准' });
        approved.push(id);
      }
      save();
      return json(200, { approved, skipped });
    }
    let hm = path.match(/^\/review\/(\d+)\/history$/);
    if (hm) {
      const it = state.items.find((x) => x.id === +hm[1]);
      if (!it) return json(404, { detail: '找不到這筆申報' });
      const steps = [{ time: (events(it)[0] || {}).time || '', action: '送出申報', actor: it.employee, from_status: null, to_status: null, comment: null },
        ...events(it).map((e) => ({ time: e.time, action: e.action, actor: e.actor, from_status: ST[e.from] || null, to_status: ST[e.to] || null,
          comment: e.comment || null, self_review: false }))];
      return json(200, { id: it.id, store: it.store, amount: it.amount, employee: it.employee, steps });
    }
    if (path === '/export/events') {
      const head = ['時間', '申報編號', '申報識別碼', '申報人', '店家', '金額', '動作', '執行者', '原狀態', '新狀態', '意見', '規則版本', '自審'];
      const f = query.get('from') || '', t = query.get('to') || '';
      const cell = (v) => { let s = v === undefined || v === null ? '' : String(v); if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
        return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
      const rows = [];
      state.items.forEach((x) => events(x).forEach((e) => {
        if ((f && e.time.slice(0, 10) < f) || (t && e.time.slice(0, 10) > t)) return;
        rows.push([e.time, x.id, x.client_id, x.employee, x.store, x.amount, e.action, e.actor, ST[e.from] || '', ST[e.to] || '', e.comment,
          e.actor.startsWith('自動') ? 'v1' : '', '否'].map(cell).join(','));
      }));
      rows.sort();
      return new Response([head.join(','), ...rows].join('\r\n') + '\r\n', { status: 200, headers: { 'content-type': 'text/csv; charset=utf-8' } });
    }
    if (path === '/review/items') {
      const st = query.get('status') || 'held', sp = query.get('sampled');
      const match = (x) => st === 'done' ? x.review_status !== 'held' : x.review_status === st;
      return json(200, state.items.filter((x) => match(x) && (sp === null || !!x.sampled === (sp === '1'))).slice().sort((a, b) => st === 'done' ? b.id - a.id : a.id - b.id)
        .map((x) => ({ ...pub(x), files: { ...(x.files || {}), receipt: hasReceipt(x) } })));
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
      addEvent(it, '人工審核', user.name, it.review_status, target, body.comment);
      Object.assign(it, { review_status: target, reviewed_by: user.name, review_comment: body.comment || null,
        review_reasons: target === 'rejected' ? [body.comment] : it.review_reasons });
      save();
      return json(200, { ok: true, review_status: target });
    }
    m = path.match(/^\/files\/(\d+)\/(\w+)$/);
    if (m) {
      const it = state.items.find((x) => x.id === +m[1]);
      const b = m[2] === 'receipt' ? (it && hasReceipt(it) ? await demoReceipt(it) : null) : (blobs[+m[1]] || {})[m[2]];
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
