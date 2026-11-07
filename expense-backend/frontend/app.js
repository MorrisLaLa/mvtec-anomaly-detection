// 前台主程式（原本內嵌在 expense-demo.html；資安審查 D3：CSP 不允許行內腳本與行內事件）
// 事件寫法：data-click／data-change／data-input="函式名"，data-args='[參數 JSON]'，data-pass="event"|"el" 附加事件或元素
function dispatchAction(kind) {
  return (e) => {
    const el = e.target.closest && e.target.closest(`[data-${kind}]`);
    if (!el) return;
    const fn = window[el.dataset[kind]];
    if (typeof fn !== 'function') return;
    const args = el.dataset.args ? JSON.parse(el.dataset.args) : [];
    if (el.dataset.pass === 'event') args.push(e); else if (el.dataset.pass === 'el') args.push(el);
    fn(...args);
  };
}
['click', 'change', 'input'].forEach(k => document.addEventListener(k, dispatchAction(k)));

// ---------------- Server sync config ----------------
// 伺服器位址：本頁由後端 /pwa 同源提供時留空；改用 Tailscale 時填入，例如 'http://100.x.y.z:8000'
// （跨來源時，後端 settings.yaml 的 cors_allowed_origins 必須包含本頁的來源）
const SERVER_URL = '';
ExpenseSync.configure({ SERVER_URL, onFlush: onSyncFlush });

// ---------------- Demo data ----------------
const CASES = [
  { company: 'UY2301', owner: 'CPC-2026-042', note: '42吋海管 PIT 計畫' },
  { company: 'UY2505', owner: 'CPC-2026-051', note: '陸管腐蝕評估' },
  { company: 'UY2510', owner: 'CPC-2025-118', note: '閥室維護工程' },
];
let boundCaseIndex = 0;
let reviewCaseIndex = 0;
let caseSheetContext = 'bind'; // 'bind' = home pill, 'review' = inside review screen

const CATEGORIES = ['五金材料', '交通油料', '工具租賃', '餐飲雜支', '待分類'];

// classifier: keyword -> {category, confidence}
function classify(storeName) {
  const s = storeName;
  if (/五金|鐵材|工具行|螺絲/.test(s)) return { category: '五金材料', confidence: 'high' };
  if (/加油|中油|台塑|全國加油/.test(s)) return { category: '交通油料', confidence: 'high' };
  if (/租賃|機具/.test(s)) return { category: '工具租賃', confidence: 'high' };
  if (/超商|檳榔|小吃|便當/.test(s)) return { category: '餐飲雜支', confidence: 'mid' };
  return { category: '待分類', confidence: 'low' };
}

let selectedDocType = '統一發票';   // 有照片時由使用者選擇；送出後由後端自動辨識 QR

let entries = [
  { store: '永昌五金行', amount: 620, category: '五金材料', case: CASES[0], date: daysAgo(1), type: '統一發票', status: 'ok' },
  { store: '中油 二仁溪站', amount: 980, category: '交通油料', case: CASES[0], date: daysAgo(2), type: '統一發票', status: 'ok' },
  { store: '現場零星採買（沒有收據）', amount: 150, category: '待分類', case: CASES[0], date: daysAgo(3), type: '無單據', status: 'ok' },
];

let currentReceipt = null; // receipt being reviewed
let correctionMade = false;
let autoCategory = '';     // 系統自動判斷的分類（送出時一併回報，供後端記錄使用者修正）
let signatureDataURL = null;
let sigCtx = null, sigDrawing = false, sigHasStroke = false;

// ---------------- Identity ----------------
const USER_KEY = 'expense_demo_user';
const PIN_KEY = 'expense_demo_pins';
let currentUser = null;

function loadUser() {
  try { return localStorage.getItem(USER_KEY); } catch (err) { return null; }
}
function saveUser(name) {
  try { localStorage.setItem(USER_KEY, name); } catch (err) { /* storage unavailable */ }
}
function loadPins() {
  try { return JSON.parse(localStorage.getItem(PIN_KEY) || '{}'); } catch (err) { return {}; }
}
function savePin(name, pin) {
  try {
    const pins = loadPins();
    pins[name] = pin;
    localStorage.setItem(PIN_KEY, JSON.stringify(pins));
  } catch (err) { /* storage unavailable — PIN only holds for this session */ }
}
function initials(name) { return name.slice(-2); }

// 姓名一律來自伺服器的裝置名稱（沒有選姓名畫面）
async function loginFromServer(skipPin) {
  let name = loadUser() || '現場人員';
  if (skipPin || ExpenseSync.autoAllowed()) { // 行動網路 + 僅 Wi‑Fi 策略時不連線，沿用已存的姓名
    try {
      const r = await api('/api/v1/me');
      if (r.status === 200) name = (await r.json()).name || name;
    } catch (e) { /* 離線：沿用已存的姓名 */ }
  }
  if (!skipPin && loadPins()[name]) { chooseIdentity(name); return; }
  // 回應較慢時使用者可能已經點進別的畫面：只記下姓名，不把人拉回首頁
  const away = ['screen-review', 'screen-processing', 'screen-result', 'screen-reviewq', 'screen-settings']
    .some(s => document.getElementById(s).style.display !== 'none');
  if (away) {
    currentUser = name;
    saveUser(name);
    document.getElementById('brandUserLabel').textContent = '一六國際 · ' + name;
  } else finishLogin(name);
}

// ---------------- PIN flow ----------------
let pinMode = 'setup';      // 'setup' | 'unlock'
let pinStage = 1;           // setup: 1 = first entry, 2 = confirm
let pinBuffer = '';
let pinFirstEntry = '';
let pendingIdentity = null;
let pinContext = 'unlock';  // 'unlock'（開啟 App 時）| 'settings'（從設定頁開啟 PIN）

function openSettings() {
  document.getElementById('pinEnabled').checked = !!(currentUser && loadPins()[currentUser]);
  document.getElementById('adminTools').style.display = myRole === 'admin' ? 'block' : 'none';  // 只是隱藏；伺服器端另有角色檢查
  if (myRole === 'admin') loadAdminDevices();
  showScreen('screen-settings');
}
const ROLE_LABEL = { submitter: '申報人', reviewer: '審核人員', admin: '管理員' };
async function loadAdminDevices() {
  const box = document.getElementById('adminDevices');
  let rows = [];
  try {
    const r = await api('/api/v1/admin/devices');
    if (!r.ok) return;
    rows = await r.json();
  } catch (e) { showToast('連不上伺服器'); return; }
  box.textContent = '';
  rows.forEach(d => {  // 名稱一律用 textContent，不經 innerHTML
    const div = document.createElement('div');
    div.className = 'admin-dev entry-actions';
    const label = document.createElement('span');
    label.textContent = `${d.name}　${ROLE_LABEL[d.role] || d.role}${d.is_me ? '（我）' : ''}`;
    div.appendChild(label);
    const btn = (text, fn) => { const b = document.createElement('button'); b.className = 'mini-btn'; b.textContent = text; b.onclick = fn; div.appendChild(b); };
    if (d.role !== 'admin') {
      btn('設為管理員', () => setDeviceRole(d.id, 'admin'));
      btn(d.role === 'reviewer' ? '改為申報人' : '改為審核人員', () => setDeviceRole(d.id, d.role === 'reviewer' ? 'submitter' : 'reviewer'));
      btn('轉移給他', () => transferAdmin(d.id, d.name));
    } else if (d.is_me) {
      btn('我不當管理員了', () => setDeviceRole(d.id, 'reviewer', true));
    } else {
      const note = document.createElement('span');
      note.className = 'onboard-sub';
      note.textContent = '（另一位管理員：要拿掉請在主機上操作）';
      div.appendChild(note);
    }
    box.appendChild(div);
  });
}
function leftAdmin() {
  myRole = 'reviewer';
  try { localStorage.setItem('expense_role', myRole); } catch (e) { /* 略過 */ }
  document.getElementById('adminTools').style.display = 'none';
}
async function adminPost(path, body) {
  const r = await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  let detail = '';
  try { detail = (await r.json()).detail || ''; } catch (e) { /* 略過 */ }
  if (!r.ok) showToast(typeof detail === 'string' && detail ? detail : '操作失敗（' + r.status + '）');
  return r.ok;
}
async function setDeviceRole(id, role, self = false) {
  try {
    if (!await adminPost('/api/v1/admin/role', { device_id: id, role })) return;
    showToast('已變更角色');
    if (self) leftAdmin(); else loadAdminDevices();
  } catch (e) { showToast('連不上伺服器'); }
}
async function transferAdmin(id, name) {
  if (!window.confirm(`確定把你的管理員權限轉給「${name}」？轉移後你會變成審核人員。`)) return;
  try {
    if (!await adminPost('/api/v1/admin/transfer', { device_id: id })) return;
    showToast('已轉移管理員權限');
    leftAdmin();
  } catch (e) { showToast('連不上伺服器'); }
}
async function unblockLogins() {
  try {
    const r = await api('/api/v1/admin/unblock', { method: 'POST' });
    if (!r.ok) { showToast(r.status === 403 ? '只有管理員可以解除' : '解除失敗，請稍後再試'); return; }
    const n = (await r.json()).cleared;
    showToast(n ? `已解除封鎖（${n} 個來源）` : '已解除（目前沒有被封鎖的來源）');
  } catch (e) { showToast('連不上伺服器'); }
}
function onPinToggle() {
  const box = document.getElementById('pinEnabled');
  if (!currentUser) { box.checked = false; return; }
  if (box.checked) {
    pinContext = 'settings';
    pendingIdentity = currentUser;
    pinMode = 'setup'; pinStage = 1; pinFirstEntry = ''; pinBuffer = '';
    document.getElementById('pinAvatar').textContent = initials(currentUser);
    document.getElementById('pinError').textContent = '';
    document.getElementById('pinTitle').textContent = '設定你的 PIN';
    document.getElementById('pinSub').textContent = '請設定 4 位數 PIN';
    document.getElementById('pinAltLink').textContent = '取消';
    renderPinDots();
    showScreen('screen-pin');
  } else {
    try {
      const pins = loadPins();
      delete pins[currentUser];
      localStorage.setItem(PIN_KEY, JSON.stringify(pins));
    } catch (err) { /* 略過 */ }
    showToast('已關閉 PIN');
  }
}
function pinAlt() {
  if (pinContext === 'settings') { pinContext = 'unlock'; openSettings(); }
  else showToast('忘記 PIN？請向管理員索取新的連結，重新設定這支手機。');
}

function chooseIdentity(name) {
  pendingIdentity = name;
  const pins = loadPins();
  document.getElementById('pinAvatar').textContent = initials(name);
  pinBuffer = '';
  document.getElementById('pinError').textContent = '';
  if (pins[name]) {
    pinMode = 'unlock';
    document.getElementById('pinTitle').textContent = '歡迎回來，' + name;
    document.getElementById('pinSub').textContent = '輸入你的 4 位數 PIN 繼續';
    document.getElementById('pinAltLink').textContent = '忘記 PIN？';
  } else {
    pinMode = 'setup';
    pinStage = 1;
    pinFirstEntry = '';
    document.getElementById('pinTitle').textContent = '設定你的 PIN';
    document.getElementById('pinSub').textContent = '第一次使用此身份，請設定 4 位數 PIN';
    document.getElementById('pinAltLink').textContent = '取消';
  }
  renderPinDots();
  showScreen('screen-pin');
}

function renderPinDots() {
  const dots = document.querySelectorAll('#pinDots .pin-dot');
  dots.forEach((d, i) => d.classList.toggle('filled', i < pinBuffer.length));
}

function pinDigit(d) {
  if (pinBuffer.length >= 4) return;
  pinBuffer += d;
  renderPinDots();
  if (pinBuffer.length === 4) setTimeout(processPinEntry, 160);
}
function pinBackspace() {
  pinBuffer = pinBuffer.slice(0, -1);
  renderPinDots();
}
function pinFail(message) {
  const wrap = document.getElementById('pinKeypad').closest('.onboard');
  document.getElementById('pinError').textContent = message;
  wrap.classList.remove('pin-shake'); void wrap.offsetWidth; wrap.classList.add('pin-shake');
  pinBuffer = '';
  renderPinDots();
}

function processPinEntry() {
  if (pinMode === 'setup') {
    if (pinStage === 1) {
      pinFirstEntry = pinBuffer;
      pinBuffer = '';
      pinStage = 2;
      document.getElementById('pinTitle').textContent = '再輸入一次確認';
      document.getElementById('pinSub').textContent = '請重新輸入剛才設定的 PIN';
      renderPinDots();
      return;
    }
    // stage 2 — confirm
    if (pinBuffer === pinFirstEntry) {
      savePin(pendingIdentity, pinBuffer);
      if (pinContext === 'settings') { pinContext = 'unlock'; showToast('已開啟 PIN'); openSettings(); }
      else finishLogin(pendingIdentity);
    } else {
      pinStage = 1;
      pinFirstEntry = '';
      document.getElementById('pinTitle').textContent = '設定你的 PIN';
      document.getElementById('pinSub').textContent = pinContext === 'settings' ? '請設定 4 位數 PIN' : '第一次使用此身份，請設定 4 位數 PIN';
      pinFail('兩次輸入不一致，請重新設定');
    }
  } else {
    // unlock
    const pins = loadPins();
    if (pinBuffer === pins[pendingIdentity]) {
      finishLogin(pendingIdentity);
    } else {
      pinFail('PIN 錯誤，請再試一次');
    }
  }
}

function finishLogin(name) {
  currentUser = name;
  saveUser(name);
  document.getElementById('brandUserLabel').textContent = '一六國際 · ' + name;
  goHome();
}

// ---------------- Helpers ----------------
function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0,10);
}
function todayStr() { return new Date().toISOString().slice(0,10); }
function fmtDate(s) {
  const [y,m,d] = s.split('-');
  return `${parseInt(m)}/${parseInt(d)}`;
}
function fmtMoney(n) { return 'NT$ ' + n.toLocaleString('en-US'); }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function iconForType(type) {
  if (type === '交通油料') return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V9l4-5h6l4 5v11"/><path d="M4 13h14"/><circle cx="8" cy="17" r="1.2"/><circle cx="15" cy="17" r="1.2"/></svg>`;
  if (type === '五金材料') return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 7l3 3-8 8H6v-3z"/><path d="M17.5 3.5l3 3-2 2-3-3z"/></svg>`;
  if (type === '工具租賃') return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="17" r="2.4"/><circle cx="17" cy="17" r="2.4"/><path d="M9.2 15.5 13 6h4"/><path d="M6 10h5"/></svg>`;
  if (type === '無單據') return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M9 9l6 6M15 9l-6 6"/></svg>`;
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v3h3"/></svg>`;
}

// ---------------- Render home ----------------
function renderHome() {
  const list = document.getElementById('entryList');
  list.innerHTML = '';
  let total = 0;
  entries.forEach((e, i) => {
    total += e.amount;
    const div = document.createElement('div');
    div.className = 'entry' + (e._new ? ' new' : '');
    if (e.voucherImage) { div.style.cursor = 'pointer'; div.onclick = (ev) => { if (!ev.target.closest('[data-click]')) openVoucherViewer(e.voucherImage); }; }
    const statusTag = e.status === 'failed'
      ? `<span class="tag tag-review">${escapeHtml(failText(e))}</span>`
      : e.status === 'waiting-wifi'
      ? `<span class="tag tag-pending">${ExpenseSync.networkKind() === 'unsupported' ? '等 Wi‑Fi（請在 Wi‑Fi 下按「立即上傳」）' : '等 Wi‑Fi（省流量）'}</span>`
      : e.status === 'rejected'
      ? `<span class="tag tag-review">這張被退回：${escapeHtml(friendlyReasons(e.reviewReasons))}</span>`
      : e.status === 'pending-sync'
      ? '<span class="tag tag-pending">等待送出</span>'
      : (e.status === 'review' ? '<span class="tag tag-review">審核中</span>' : '<span class="tag tag-ok">已送出</span>');
    const actions = e.status === 'failed'
      ? `<div class="entry-actions">${e.failStatus === 401 ? `<button class="mini-btn" data-click="rebindDevice" data-pass="event">重新綁定</button>` : ''}<button class="mini-btn" data-click="resendEntry" data-args='${escapeHtml(JSON.stringify([e.clientId]))}' data-pass="event">重送</button><button class="mini-btn danger" data-click="deleteFailedEntry" data-args='${escapeHtml(JSON.stringify([e.clientId]))}' data-pass="event">刪除</button></div>`
      : e.status === 'waiting-wifi'
      ? `<div class="entry-actions"><button class="mini-btn" data-click="forceUploadEntry" data-args='${escapeHtml(JSON.stringify([e.clientId]))}' data-pass="event">本筆改用行動網路上傳</button></div>`
      : '';
    div.innerHTML = `
      <div class="entry-icon">${iconForType(e.category === '待分類' && e.type === '無單據' ? '無單據' : e.category)}</div>
      <div class="entry-body">
        <div class="entry-top">
          <div class="entry-store">${escapeHtml(e.store)}</div>
          <div class="entry-amount">${fmtMoney(e.amount)}</div>
        </div>
        <div class="entry-meta">
          <span class="tag tag-case">${escapeHtml(e.case.company)}</span>
          <span class="tag tag-cat">${escapeHtml(e.category)}</span>
          ${statusTag}
          ${e.signed ? '<span class="tag tag-cat">已簽收</span>' : ''}
          ${e.reporter ? `<span class="tag tag-user">${escapeHtml(e.reporter)}</span>` : ''}
          <span class="entry-date">${fmtDate(e.date)}</span>
        </div>
        ${actions}
      </div>`;
    list.appendChild(div);
    e._new = false;
  });
  document.getElementById('monthTotal').textContent = fmtMoney(total);
  document.getElementById('boundCaseLabel').innerHTML = CASES[boundCaseIndex].company + '<small>目前案件</small>';
}

function openVoucherViewer(src) {
  document.getElementById('voucherImg').src = src;
  document.getElementById('voucherOverlay').classList.add('show');
}
function closeVoucherViewer(evt) {
  if (evt) evt.stopPropagation();
  document.getElementById('voucherOverlay').classList.remove('show');
}

// ---------------- Screen switching ----------------
function showScreen(id) {
  ['screen-bind','screen-pair','screen-result','screen-settings','screen-pin','screen-home','screen-reviewq','screen-processing','screen-review'].forEach(s => {
    document.getElementById(s).style.display = (s === id) ? 'flex' : 'none';
  });
  document.getElementById('homeActionBar').style.display = (id === 'screen-home') ? 'flex' : 'none';
}
function goHome() { renderHome(); showScreen('screen-home'); }

// ---------------- Capture flow ----------------
function startCapture(source) {
  // 開啟手機相機（photo）或相簿（album）；選好照片後由 onReceiptPicked 接手
  document.getElementById(source === 'photo' ? 'capCamera' : 'capAlbum').click();
}

function onReceiptPicked(input) {
  const file = input.files && input.files[0];
  input.value = ''; // 讓同一張照片可以再次選取
  if (!file) return;
  if (currentReceipt && currentReceipt.previewUrl) URL.revokeObjectURL(currentReceipt.previewUrl);
  currentReceipt = { type: 'photo', store: '', amount: 0, hasTax: true, isNoReceipt: false, file, previewUrl: URL.createObjectURL(file) };
  openReview();
}

function startNoReceipt() {
  currentReceipt = { type: 'none', store: '', amount: 0, hasTax: false, isNoReceipt: true };
  openReview();
}

function openReview() {
  toggleMore(false); // 每次進確認畫面，「更多選項」預設收起
  correctionMade = false;
  document.getElementById('correctionNote').classList.remove('show');
  reviewCaseIndex = boundCaseIndex;
  resetSignatureField();

  const r = currentReceipt;
  document.getElementById('fieldAmount').value = r.amount || '';
  document.getElementById('fieldStore').value = r.store || '';
  document.getElementById('fieldDate').value = todayStr();
  document.getElementById('fieldReportDate').value = todayStr();

  // receipt type display
  const typeLabel = document.getElementById('receiptTypeLabel');
  const typeSub = document.getElementById('receiptTypeSub');
  const thumb = document.getElementById('receiptThumb');
  if (r.isNoReceipt) {
    typeLabel.textContent = '沒有收據';
    typeSub.textContent = '請填寫店家、金額與用途';
    thumb.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M9 9l6 6M15 9l-6 6"/></svg>`;
  } else if (r.file) {
    typeLabel.textContent = '已附單據照片';
    typeSub.textContent = '送出後系統會自動辨識電子發票 QR；金額留空時以發票上的金額為準';
    thumb.innerHTML = `<img src="${r.previewUrl}" alt="單據照片" style="width:100%;height:100%;object-fit:cover;border-radius:7px;">`;
  } else if (r.hasTax) {
    typeLabel.textContent = '統一發票';
    typeSub.textContent = '已辨識統一編號，屬有效發票';
    thumb.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v3h3"/></svg>`;
  } else {
    typeLabel.textContent = '收據';
    typeSub.textContent = '未辨識統一編號，屬一般收據';
    thumb.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M6 3h9l3 3v15H6z"/><path d="M15 3v3h3"/><path d="M9 12h6M9 15h4" stroke-dasharray="1.5 2"/></svg>`;
  }

  // 單據類型（僅照片模式）
  selectedDocType = '統一發票';
  document.getElementById('docTypeGroup').style.display = r.file ? 'block' : 'none';
  if (r.file) renderDocTypeChips();

  // category chips
  let cls;
  if (r.isNoReceipt) {
    cls = { category: '待分類', confidence: 'low' };
  } else {
    cls = classify(r.store);
  }
  renderCategoryChips(cls.category, cls.confidence);

  document.getElementById('reviewCaseLabel').innerHTML = CASES[reviewCaseIndex].company + '<small>' + CASES[reviewCaseIndex].company + ' / ' + CASES[reviewCaseIndex].owner + '</small>';

  checkThreshold();
  showScreen('screen-review');
}

function renderDocTypeChips() {
  const row = document.getElementById('docTypeChips');
  row.innerHTML = '';
  ['統一發票', '收據'].forEach(t => {
    const chip = document.createElement('div');
    chip.className = 'chip' + (t === selectedDocType ? ' active' : '');
    chip.textContent = t;
    chip.onclick = () => { selectedDocType = t; renderDocTypeChips(); };
    row.appendChild(chip);
  });
  document.getElementById('docTypeNote').textContent = selectedDocType === '統一發票'
    ? '有電子發票 QR 時，系統會自動核對並可能自動核准。' : '收據沒有可驗證的 QR，會交由審核人員確認。';
}

let selectedCategory = '';
function renderCategoryChips(auto, confidence) {
  selectedCategory = auto;
  autoCategory = auto;
  const row = document.getElementById('categoryChips');
  row.innerHTML = '';
  CATEGORIES.forEach(cat => {
    const chip = document.createElement('div');
    chip.className = 'chip' + (cat === selectedCategory ? ' active' + (confidence === 'low' && cat === auto ? ' low-conf' : '') : '');
    chip.textContent = cat;
    chip.onclick = () => selectCategory(cat, auto);
    chip.dataset.cat = cat;
    row.appendChild(chip);
  });
  const note = document.getElementById('categoryAutoNote');
  if (confidence === 'high') {
    note.className = 'auto-note';
    note.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 6"/></svg> 系統依店家名稱自動判斷，如不正確請直接點選其他用途`;
  } else if (confidence === 'mid') {
    note.className = 'auto-note';
    note.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 6"/></svg> 系統初步判斷，信心度中等，請確認一下`;
  } else {
    note.className = 'auto-note warn';
    note.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8v5M12 16h.01"/><circle cx="12" cy="12" r="9"/></svg> 系統無法自動判斷，請手動選擇一個用途`;
  }
}
function selectCategory(cat, autoCat) {
  selectedCategory = cat;
  document.querySelectorAll('#categoryChips .chip').forEach(c => {
    c.classList.toggle('active', c.dataset.cat === cat);
    c.classList.remove('low-conf');
  });
  const cnote = document.getElementById('correctionNote');
  if (cat !== autoCat) {
    correctionMade = true;
    const storeName = document.getElementById('fieldStore').value || '此店家';
    cnote.innerHTML = `已記下你的修正：<strong>${escapeHtml(storeName)}</strong> 系統原判斷為「${escapeHtml(autoCat)}」，已改為「${escapeHtml(cat)}」。同店家累積 3 次相同修正後，系統會提示更新自動判斷規則。`;
    cnote.classList.add('show');
  } else {
    correctionMade = false;
    cnote.classList.remove('show');
  }
}

function onAmountChange() { checkThreshold(); }
function toggleMore(open) {
  const box = document.getElementById('moreOptions');
  const show = open === undefined ? box.style.display === 'none' : open;
  box.style.display = show ? 'block' : 'none';
  document.getElementById('moreToggle').textContent = show ? '收起更多選項' : '更多選項';
}
function checkThreshold() {
  const banner = document.getElementById('thresholdBanner');
  const amt = parseInt(document.getElementById('fieldAmount').value || '0', 10);
  if (currentReceipt && currentReceipt.isNoReceipt && amt > 1000) {
    banner.textContent = `沒有收據的金額超過 NT$1,000，此筆會由審核人員確認`;
    banner.classList.add('show');
  } else {
    banner.classList.remove('show');
  }
}

// ---------------- Case sheet ----------------
function openCaseSheet(fromReview) {
  caseSheetContext = fromReview ? 'review' : 'bind';
  const wrap = document.getElementById('caseOptions');
  wrap.innerHTML = '';
  CASES.forEach((c, i) => {
    const activeIdx = caseSheetContext === 'review' ? reviewCaseIndex : boundCaseIndex;
    const opt = document.createElement('div');
    opt.className = 'case-option' + (i === activeIdx ? ' selected' : '');
    opt.innerHTML = `
      <span class="case-option-text">${c.company} / ${c.owner}<small>${c.note}</small></span>
      <svg class="check-circle" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 6"/></svg>`;
    opt.onclick = () => selectCase(i);
    wrap.appendChild(opt);
  });
  document.getElementById('sheetBackdrop').classList.add('show');
  document.getElementById('caseSheet').classList.add('show');
}
function closeCaseSheet() {
  document.getElementById('sheetBackdrop').classList.remove('show');
  document.getElementById('caseSheet').classList.remove('show');
}
function selectCase(i) {
  if (caseSheetContext === 'review') {
    reviewCaseIndex = i;
    document.getElementById('reviewCaseLabel').innerHTML = CASES[i].company + '<small>' + CASES[i].company + ' / ' + CASES[i].owner + '</small>';
  } else {
    boundCaseIndex = i;
    document.getElementById('boundCaseLabel').innerHTML = CASES[i].company + '<small>目前案件</small>';
  }
  closeCaseSheet();
}

// ---------------- Submit ----------------
function newClientId() {
  // 每筆申報唯一的冪等鍵（與「案號」無關）：重送同一筆時後端不會重複入庫
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return 'c-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}
async function dataUrlToBlob(dataUrl) {
  return (await fetch(dataUrl)).blob();
}

async function submitEntry() {
  const amount = parseInt(document.getElementById('fieldAmount').value || '0', 10);
  const rawStore = document.getElementById('fieldStore').value.trim();
  const store = rawStore || '（未命名店家）';
  const date = document.getElementById('fieldDate').value;
  const reportDate = document.getElementById('fieldReportDate').value;
  const isReview = currentReceipt.isNoReceipt && amount > 1000;
  const caseObj = CASES[reviewCaseIndex];
  const typeLabel = currentReceipt.isNoReceipt ? '無單據' : (currentReceipt.file ? selectedDocType : (currentReceipt.hasTax ? '統一發票' : '收據'));
  const amountBlank = !document.getElementById('fieldAmount').value.trim();

  let voucherImage = null;
  if (signatureDataURL) {
    voucherImage = await buildVoucherImage({
      amount, store, category: selectedCategory, date, reportDate,
      caseCompany: caseObj.company, caseOwner: caseObj.owner,
      type: typeLabel, signatureDataURL,
    });
  }

  const clientId = newClientId();
  const entry = {
    store, amount, category: selectedCategory, case: caseObj, date,
    type: typeLabel,
    status: 'pending-sync', // 收到後端 HTTP 200 才會改成已記錄／待審核
    signed: !!signatureDataURL,
    voucherImage,
    reporter: currentUser,
    clientId,
    _new: true,
    _wasReview: isReview,
    _signed: !!signatureDataURL,
  };

  try {
    // 先寫進本機（IndexedDB）離線佇列，再嘗試送出；只有伺服器回 200 才會從佇列移除
    await ExpenseSync.enqueueOnly({
      fields: {
        client_id: clientId,
        case_no: caseObj.company,
        owner_case_no: caseObj.owner,
        doc_type: typeLabel,
        employee: currentUser || '',
        // 有照片且金額留空：交給伺服器以發票 QR 為準；其餘照舊
        amount: (currentReceipt.file && amountBlank) ? '' : String(amount),
        store: rawStore,
        expense_date: date,
        category: selectedCategory,
        suggested_category: autoCategory,
      },
      receipt: currentReceipt.file || null,
      signature: signatureDataURL ? await dataUrlToBlob(signatureDataURL) : null,
      voucher: voucherImage ? await dataUrlToBlob(voucherImage) : null,
      meta: {
        store, amount, category: selectedCategory, caseIndex: reviewCaseIndex, date,
        type: typeLabel, reporter: currentUser, signed: !!signatureDataURL, voucherImage, wasReview: isReview,
      },
    });
  } catch (err) {
    showToast('無法存入本機佇列，請重試');
    return;
  }

  entries.unshift(entry);
  pendingCount++;
  showToast(!ExpenseSync.autoAllowed()
    ? (ExpenseSync.networkKind() === 'unsupported' ? '已存檔，請在 Wi‑Fi 下按「立即上傳」' : '已存檔，連上 Wi‑Fi 後自動上傳')
    : (isOnline ? '申報送出中…' : '已離線存檔，恢復連線後自動上傳'));
  updateNetUI();
  showResult(entry.clientId);
  runSync();
}

// ---------------- Composite voucher image (data + signature, one file) ----------------
function buildVoucherImage(data) {
  return new Promise((resolve) => {
    const W = 640, H = 900;
    const canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, W, H);
    ctx.strokeStyle = '#D8D4C8';
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, W - 2, H - 2);

    ctx.fillStyle = '#1B1F1D';
    ctx.font = '600 26px "IBM Plex Sans TC", sans-serif';
    ctx.fillText('支出憑證', 36, 58);
    ctx.fillStyle = '#565F5A';
    ctx.font = '400 13px "IBM Plex Mono", monospace';
    ctx.fillText('一六國際 · 現場人員支出申報系統', 36, 80);

    ctx.strokeStyle = '#D8D4C8';
    ctx.beginPath(); ctx.moveTo(36, 98); ctx.lineTo(W - 36, 98); ctx.stroke();

    const rows = [
      ['金額', data.amount ? 'NT$ ' + Number(data.amount).toLocaleString('en-US') : '以發票上的金額為準'],
      ['店家', data.store],
      ['分類', data.category],
      ['支出日期', data.date],
      ['申報日期', data.reportDate],
      ['案號（公司 / 業主）', data.caseCompany + ' / ' + data.caseOwner],
      ['憑證類型', data.type],
    ];
    let y = 134;
    rows.forEach(([label, val]) => {
      ctx.fillStyle = '#565F5A';
      ctx.font = '400 12.5px "IBM Plex Sans TC", sans-serif';
      ctx.fillText(label, 36, y);
      ctx.fillStyle = '#1B1F1D';
      ctx.font = '600 17px "IBM Plex Mono", monospace';
      ctx.fillText(String(val), 36, y + 23);
      y += 56;
    });

    ctx.strokeStyle = '#D8D4C8';
    ctx.beginPath(); ctx.moveTo(36, y + 6); ctx.lineTo(W - 36, y + 6); ctx.stroke();
    y += 34;

    ctx.fillStyle = '#565F5A';
    ctx.font = '400 12.5px "IBM Plex Sans TC", sans-serif';
    ctx.fillText('廠商簽名', 36, y);
    y += 16;

    const finish = () => {
      ctx.fillStyle = '#B7B2A2';
      ctx.font = '400 10.5px "IBM Plex Mono", monospace';
      ctx.fillText('產生時間 ' + new Date().toLocaleString('zh-TW'), 36, H - 24);
      resolve(canvas.toDataURL('image/png'));
    };

    if (data.signatureDataURL) {
      const img = new Image();
      img.onload = () => {
        const sigW = W - 72, sigH = 200;
        ctx.strokeStyle = '#D8D4C8';
        ctx.strokeRect(36, y, sigW, sigH);
        ctx.drawImage(img, 36, y, sigW, sigH);
        finish();
      };
      img.onerror = finish;
      img.src = data.signatureDataURL;
    } else {
      finish();
    }
  });
}

// ---------------- Offline queue + real sync (sync.js → POST /api/v1/submit) ----------------
// 規則：HTTP 200 才清除本機佇列；5xx／網路錯誤／逾時（10 秒）保留並以遞增間隔重試；
//       4xx 直接判定失敗、顯示錯誤訊息，不進重試佇列。
let isOnline = navigator.onLine !== false;
let syncStalled = false;   // 上一輪同步有項目因網路/5xx 留在佇列
let pendingCount = 0;
let heldKind = null;       // 因上傳策略（非 Wi‑Fi）而等待時的網路類型

function entryFromMeta(clientId, meta, status, failMessage) {
  if (!meta) return null;
  return {
    store: meta.store, amount: meta.amount, category: meta.category,
    case: CASES[meta.caseIndex] || CASES[0], date: meta.date, type: meta.type,
    status, failMessage, signed: meta.signed, voucherImage: meta.voucherImage,
    reporter: meta.reporter, clientId, _new: false, _wasReview: meta.wasReview, _signed: meta.signed,
  };
}

async function loadPendingQueue() {
  try {
    const [pend, failed] = await Promise.all([ExpenseSync.pendingItems(), ExpenseSync.failed()]);
    const restored = [
      ...pend.map(i => entryFromMeta(i.client_id, i.meta, 'pending-sync')),
      ...failed.map(i => { const x = entryFromMeta(i.client_id, i.meta, 'failed', i.message); if (x) { x.failStatus = i.status; x.retryAfter = i.retryAfter; } return x; }),
    ].filter(Boolean);
    entries = [...restored, ...entries];
    pendingCount = pend.length;
    renderHome();
    updateNetUI();
    if (pend.length) runSync();
  } catch (err) {
    // IndexedDB unavailable — continue without persisted queue
  }
}

function runSync() {
  return ExpenseSync.flush().catch(() => {});
}
function syncNow() {
  if (!pendingCount) { showToast('沒有等待送出的資料'); return; }
  if (!ExpenseSync.autoAllowed()) { // 非 Wi‑Fi：使用者明確要求才上傳
    const msg = ExpenseSync.networkKind() === 'unsupported'
      ? '請確認目前已連上 Wi‑Fi，再上傳這些資料。要上傳嗎？'
      : '目前不是 Wi‑Fi，上傳會使用行動網路流量。要繼續嗎？';
    if (!confirm(msg)) return;
    ExpenseSync.flush({ force: true });
    return;
  }
  runSync();
}

function friendlyReasons(rs) {
  // 伺服器的退回原因轉成現場看得懂的話（不顯示系統代碼）
  const out = (rs || []).map(r => /^重複報銷/.test(r) ? '這張發票已經報過了' : r);
  return out.join('；') || '請洽管理員';
}
let resultClientId = null;
function showResult(id) { resultClientId = id; renderResult(); showScreen('screen-result'); }
function closeResult() { resultClientId = null; goHome(); }
function recaptureFromResult() { resultClientId = null; startCapture('photo'); }
function renderResult() {
  const e = entries.find(x => x.clientId === resultClientId);
  if (!e) return;
  const done = '<button class="btn btn-secondary" data-click="closeResult">完成</button>';
  let title, sub = '', actions = done;
  if (e.status === 'ok') { title = '✓ 已送出'; }
  else if (e.status === 'review') { title = '審核中'; sub = '審核人員確認中，通過後不用再做什麼'; }
  else if (e.status === 'rejected') {
    title = '這張被退回：' + friendlyReasons(e.reviewReasons);
    actions = '<button class="btn btn-primary" data-click="recaptureFromResult">重新拍一張</button>' + done;
  } else if (e.status === 'failed') {
    title = failText(e);
    actions = `<button class="btn btn-primary" data-click="resendFromResult" data-args='${escapeHtml(JSON.stringify([e.clientId]))}'>重送</button>` + done;
  } else if (e.status === 'waiting-wifi' || !isOnline) {
    title = '已存好';
    sub = '連上 Wi‑Fi 會自動送出';
    if (e.status === 'waiting-wifi') actions = `<button class="btn btn-primary" data-click="forceUploadEntry" data-args='${escapeHtml(JSON.stringify([e.clientId]))}'>現在用行動網路送出</button>` + done;
  } else { title = '傳送中…'; sub = '請稍等'; }
  document.getElementById('resultTitle').textContent = title;
  document.getElementById('resultSub').textContent = sub;
  document.getElementById('resultActions').innerHTML = actions;
}
function resendFromResult(id) { resendEntry(id).then(renderResult); renderResult(); }
function failText(e) {
  if (e.failStatus === 401) return '裝置未授權，請重新綁定';
  if (e.failStatus === 429) return `請求太頻繁，請約 ${Math.max(1, Math.ceil((e.retryAfter || 60) / 60))} 分鐘後再送`;
  return '送出失敗：' + (e.failMessage || '');
}
async function resendEntry(id, ev) {
  if (ev) ev.stopPropagation();
  const e = entries.find(x => x.clientId === id);
  if (e) { e.status = 'pending-sync'; e.failMessage = ''; e.failStatus = null; pendingCount++; renderHome(); updateNetUI(); }
  await ExpenseSync.retryFailed(id);
}
async function deleteFailedEntry(id, ev) {
  if (ev) ev.stopPropagation();
  if (!confirm('刪除這筆失敗的申報？刪除後無法復原。')) return;
  await ExpenseSync.dismissFailed(id);
  entries = entries.filter(x => x.clientId !== id);
  renderHome();
}
function forceUploadEntry(id, ev) {
  if (ev) ev.stopPropagation();
  if (!confirm('這筆會使用行動網路上傳，要繼續嗎？')) return;
  ExpenseSync.flush({ only: id });
}
function rebindDevice(ev) { if (ev) ev.stopPropagation(); showBind(); }
function onWifiToggle() {
  ExpenseSync.setPolicy(document.getElementById('wifiOnly').checked ? 'wifi_only' : 'any');
  updateNetUI();
  if (ExpenseSync.autoAllowed()) runSync();
}

// ---------------- 審核（reviewer／admin） ----------------
let myRole = null;
const isReviewer = () => myRole === 'reviewer' || myRole === 'admin';
function api(path, opts = {}) {
  return fetch(SERVER_URL + path, { ...opts, headers: { ...(opts.headers || {}), Authorization: 'Bearer ' + ExpenseSync.getKey() } });
}
function showReviewBar() { document.getElementById('reviewBar').style.display = isReviewer() ? 'flex' : 'none'; }
function loadCachedRole() {
  // 角色快取在手機上；非 Wi‑Fi 時不為了查角色而連線（遵守「行動網路下不傳輸」）
  try { myRole = localStorage.getItem('expense_role'); } catch (e) { myRole = null; }
  showReviewBar();
}
async function loadMe() {
  if (!ExpenseSync.autoAllowed()) { loadCachedRole(); return; }
  try {
    const r = await api('/api/v1/me');
    if (r.status !== 200) return;
    myRole = (await r.json()).role;
    try { localStorage.setItem('expense_role', myRole); } catch (e) { /* 略過 */ }
    showReviewBar();
    refreshReviewCount();
  } catch (e) { loadCachedRole(); }
}
async function refreshReviewCount() {
  if (!isReviewer() || !ExpenseSync.autoAllowed()) return;
  try {
    const r = await api('/api/v1/review/items?status=held');
    if (r.ok) document.getElementById('reviewBarText').textContent = `審核：${(await r.json()).length} 筆待處理`;
  } catch (e) { /* 離線 */ }
}
function openReviewQueue() {
  const today = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);  // 台北日期，與伺服器的申報日期一致
  if (!document.getElementById('exportFrom').value) document.getElementById('exportFrom').value = today.slice(0, 8) + '01';
  if (!document.getElementById('exportTo').value) document.getElementById('exportTo').value = today;
  showScreen('screen-reviewq'); loadReviewQueue('held');
}
async function saveFile(filename, blob) {  // 存檔；其他打包版本可改寫這個函式
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return true;
}
function selectAllPicks() {
  const boxes = [...document.querySelectorAll('.rq-pick')];
  const on = boxes.some(b => !b.checked);
  boxes.forEach(b => { b.checked = on; });
}
async function bulkApprove() {
  const picked = [...document.querySelectorAll('.rq-pick:checked')];
  if (!picked.length) { showToast('請先勾選要核准的申報'); return; }
  const total = picked.reduce((s, b) => s + Number(b.dataset.amount || 0), 0);
  if (!window.confirm(`確定核准勾選的 ${picked.length} 筆，共 ${fmtMoney(total)}？自己的申報會自動略過。`)) return;
  try {
    const r = await api('/api/v1/review/bulk-approve', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: picked.map(b => Number(b.dataset.id)) }) });
    if (!r.ok) { showToast('核准失敗（' + r.status + '）'); return; }
    const j = await r.json();
    showToast(`已核准 ${j.approved.length} 筆` + (j.skipped.length ? `，略過 ${j.skipped.length} 筆（${[...new Set(j.skipped.map(s => s.reason))].join('、')}）` : ''));
  } catch (e) { showToast('連不上伺服器'); }
  loadReviewQueue('held');
  refreshReviewCount();
}
async function showHistory(id) {
  const box = document.getElementById('historySteps');
  box.textContent = '讀取中…';
  document.getElementById('historyBackdrop').classList.add('show');
  document.getElementById('historySheet').classList.add('show');
  try {
    const r = await api(`/api/v1/review/${id}/history`);
    if (!r.ok) { box.textContent = '無法取得處理紀錄'; return; }
    const h = await r.json();
    document.getElementById('historyTitle').textContent = `處理紀錄：${h.store || '（未填店家）'} ${fmtMoney(Number(h.amount) || 0)}`;
    box.textContent = '';
    h.steps.forEach(s => {  // 一律用 textContent，不經 innerHTML
      const div = document.createElement('div');
      div.className = 'history-step';
      const what = document.createElement('div');
      what.textContent = `${s.action}・${s.actor || ''}` + (s.to_status ? `：${s.from_status ? s.from_status + ' → ' : ''}${s.to_status}` : '') + (s.self_review ? '（自審）' : '');
      const when = document.createElement('small');
      when.textContent = s.time + (s.comment ? `　意見：${s.comment}` : '');
      div.append(what, when);
      box.appendChild(div);
    });
  } catch (e) { box.textContent = '連不上伺服器'; }
}
function closeHistory() {
  document.getElementById('historyBackdrop').classList.remove('show');
  document.getElementById('historySheet').classList.remove('show');
}
async function exportCsv(kind) {
  const events = kind === 'events';
  const f = document.getElementById('exportFrom').value, t = document.getElementById('exportTo').value;
  if (f && t && f > t) { showToast('開始日期不能晚於結束日期'); return; }
  try {
    const q = new URLSearchParams();
    if (f) q.set('from', f);
    if (t) q.set('to', t);
    const r = await api((events ? '/api/v1/export/events?' : '/api/v1/export?') + q.toString());
    if (!r.ok) { showToast(r.status === 403 ? '只有審核人員或管理員可以匯出' : '匯出失敗（' + r.status + '）'); return; }
    const text = await r.text();
    // 加上 BOM，Excel 才會以 UTF-8 開啟，中文不會變亂碼
    const blob = new Blob(['\ufeff' + text], { type: 'text/csv;charset=utf-8' });
    if (!await saveFile(`${events ? '處理紀錄' : '支出申報'}_${f || '最早'}_至_${t || '最新'}.csv`, blob)) return;
    const n = Math.max(0, text.trim().split('\n').length - 1);
    showToast(n ? `已匯出 ${n} 筆` : '這段期間沒有申報（已匯出空白表）');
  } catch (e) { showToast('連不上伺服器'); }
}
async function loadReviewQueue(mode) {
  document.getElementById('rqSampled').dataset.on = mode === 'sampled' ? '1' : '0';
  document.getElementById('bulkRow').style.display = mode === 'held' ? 'flex' : 'none';
  const list = document.getElementById('reviewList');
  list.innerHTML = '';
  let items = [];
  try {
    const r = await api(mode === 'held' ? '/api/v1/review/items?status=held'
      : mode === 'done' ? '/api/v1/review/items?status=done' : '/api/v1/review/items?status=approved&sampled=1');
    if (r.ok) items = await r.json();
  } catch (e) { showToast('連不上伺服器'); }
  if (!items.length) {
    list.innerHTML = '<div class="empty-hint">' + (mode === 'held' ? '目前沒有待處理的申報。' : mode === 'done' ? '還沒有已處理的申報。' : '目前沒有需要抽查的自動核准案件。') + '</div>';
    return;
  }
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'entry';
    const files = ['receipt', 'signature', 'voucher'].filter(k => it.files && it.files[k])
      .map(k => `<button class="mini-btn" data-click="viewFile" data-args='[${Number(it.id)}, "${k}"]'>${{ receipt: '看收據', signature: '看簽名', voucher: '看憑證' }[k]}</button>`).join('');
    const reasons = (it.review_reasons || []).join('；');
    const doneTag = mode === 'done' ? `<span class="tag ${it.review_status === 'rejected' ? 'tag-review' : 'tag-ok'}">${it.review_status === 'rejected' ? '已退回' : '已核准'}・${escapeHtml(String(it.reviewed_by || '').startsWith('auto:') ? '自動' : (it.reviewed_by || ''))}</span>` : '';
    const pick = mode === 'held' ? `<input type="checkbox" class="rq-pick" aria-label="勾選這筆" data-id="${Number(it.id)}" data-amount="${Number(it.amount) || 0}">` : '';
    div.innerHTML = `${pick}<div class="entry-body">
      <div class="entry-top"><div class="entry-store">${escapeHtml(it.store || '（未填店家）')}</div><div class="entry-amount">${fmtMoney(Number(it.amount) || 0)}</div></div>
      <div class="entry-meta"><span class="tag tag-user">${escapeHtml(it.employee || '')}</span><span class="tag tag-cat">${escapeHtml(it.doc_type || '')}</span><span class="tag tag-case">${escapeHtml(it.case_no || '')}</span><span class="tag tag-cat">${escapeHtml(it.category || '')}</span><span class="entry-date">${escapeHtml(it.created_day || '')}</span>${doneTag}</div>
      ${reasons ? `<div class="auto-note warn">${escapeHtml(reasons)}</div>` : ''}
      <div class="entry-actions">${files}<button class="mini-btn" data-click="showHistory" data-args='[${Number(it.id)}]'>處理紀錄</button>${mode === 'held' ? `<button class="mini-btn" data-click="decideItem" data-args='[${Number(it.id)}, "approve"]'>核准</button>` : ''}${mode !== 'done' ? `<button class="mini-btn danger" data-click="decideItem" data-args='[${Number(it.id)}, "reject"]'>退回</button>` : ''}</div></div>`;
    list.appendChild(div);
  });
}
async function viewFile(id, kind) {
  try {
    const r = await api(`/api/v1/files/${id}/${kind}`);
    if (!r.ok) { showToast('無法取得圖片'); return; }
    openVoucherViewer(URL.createObjectURL(await r.blob()));
  } catch (e) { showToast('連不上伺服器'); }
}
async function decideItem(id, decision) {
  let comment = '';
  if (decision === 'reject') {
    comment = (window.prompt('退回理由（必填）') || '').trim();
    if (!comment) { showToast('退回必須填寫理由'); return; }
  }
  try {
    const r = await api(`/api/v1/review/${id}/decision`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision, comment }) });
    if (r.ok) showToast(decision === 'approve' ? '已核准' : '已退回');
    else if (r.status === 403) showToast('不可審核自己的申報');
    else if (r.status === 409) showToast('這筆已決定，無法再變更');
    else showToast('操作失敗（' + r.status + '）');
  } catch (e) { showToast('連不上伺服器'); }
  loadReviewQueue(document.getElementById('rqSampled').dataset.on === '1' ? 'sampled' : 'held');  // 已處理分頁沒有決定按鈕
  refreshReviewCount();
}

// ---------------- 裝置綁定 ----------------
function showBind(msg) {
  document.getElementById('bindError').textContent = msg || '';
  document.getElementById('bindKey').value = '';
  showScreen('screen-bind');
}
async function submitBind() {
  const key = document.getElementById('bindKey').value.trim();
  const err = (m) => { document.getElementById('bindError').textContent = m; };
  if (!key) { err('請輸入金鑰'); return; }
  try {
    const r = await fetch(SERVER_URL + '/api/v1/rules', { headers: { Authorization: 'Bearer ' + key } });
    if (r.status === 200) {
      ExpenseSync.setKey(key);
      showToast('已綁定這支手機');
      loadMe();
      loginFromServer(true); // 姓名來自伺服器；重新綁定不再重問 PIN
    } else if (r.status === 401) err('金鑰無效、已過期或已被撤銷');
    else if (r.status === 429) err('嘗試次數過多，請稍後再試');
    else err('伺服器回應異常（' + r.status + '）');
  } catch (e) { err('連不上伺服器，請確認網路'); }
}

// sync.js 每輪同步結束（含背景自動重試）都會呼叫
function onSyncFlush(r) {
  // 等待 Wi‑Fi 不是失敗：依上傳策略標示尚在佇列的項目
  const waiting = !ExpenseSync.autoAllowed();
  entries.forEach(e => { if (e.status === 'pending-sync' || e.status === 'waiting-wifi') e.status = waiting ? 'waiting-wifi' : 'pending-sync'; });
  heldKind = waiting && r.remaining > 0 ? ExpenseSync.networkKind() : null;
  Object.keys(r.results).forEach(id => {
    const e = entries.find(x => x.clientId === id);
    if (e) {
      const srv = r.results[id] || {};
      if (typeof srv.amount === 'number') e.amount = srv.amount;
      // 以伺服器的審核結果為準：approved 已記錄、held 待審核、rejected 已退回（附原因）
      e.status = srv.review_status === 'rejected' ? 'rejected' : (srv.review_status === 'held' || (!srv.review_status && srv.needs_review) ? 'review' : 'ok');
      e.reviewReasons = srv.review_status === 'rejected' ? (srv.review_reasons || []) : [];
    }
  });
  r.failed.forEach(f => {
    const e = entries.find(x => x.clientId === f.client_id);
    if (e) { e.status = 'failed'; e.failMessage = f.message; e.failStatus = f.status; e.retryAfter = f.retryAfter; }
  });
  pendingCount = r.remaining;
  syncStalled = r.stalled; // 只有網路錯誤／5xx／逾時才算離線；單純還有新項目排隊不算
  updateNetUI();
  if (document.getElementById('screen-home').style.display !== 'none') renderHome();
  if (document.getElementById('screen-result').style.display !== 'none') renderResult();

  if (r.failed.length) {
    showToast(failText({ failStatus: r.failed[0].status, retryAfter: r.failed[0].retryAfter, failMessage: r.failed[0].message }));
  } else if (r.sent === 1) {
    const e = entries.find(x => x.clientId === Object.keys(r.results)[0]);
    if (e && e.status === 'rejected') showToast('這張被退回：' + friendlyReasons(e.reviewReasons));
    else showToast(e && e._wasReview ? '已送出，審核中' : (e && e._signed ? '申報已送出（含簽收單）' : '申報已送出'));
  } else if (r.sent > 1) {
    showToast(`已同步 ${r.sent} 筆資料`);
  }
}

function updateNetUI() {
  const strip = document.getElementById('netStrip');
  const label = document.getElementById('netLabel');
  const offline = !isOnline || syncStalled || !!heldKind;
  if (heldKind && pendingCount) {
    strip.classList.add('offline');
    label.textContent = heldKind === 'unsupported'
      ? `等 Wi‑Fi · ${pendingCount} 筆 · 連上 Wi‑Fi 後按此處「立即上傳」`
      : `等 Wi‑Fi（省流量）· ${pendingCount} 筆等待送出`;
    return;
  }
  if (!offline) {
    strip.classList.remove('offline');
    label.textContent = pendingCount ? `線上 · 正在同步 ${pendingCount} 筆資料` : '線上 · 資料即時同步';
  } else {
    strip.classList.add('offline');
    label.textContent = pendingCount ? `沒有網路 · ${pendingCount} 筆等待送出` : '離線 · 拍照與簽名仍可正常記錄';
  }
}

window.addEventListener('online', () => { isOnline = true; updateNetUI(); runSync(); });
window.addEventListener('offline', () => { isOnline = false; updateNetUI(); });

// ---------------- Signature pad ----------------
function resetSignatureField() {
  signatureDataURL = null;
  const box = document.getElementById('signatureBox');
  box.classList.remove('signed');
  document.getElementById('signatureBoxIcon').innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17.5l1.6-4.6L14.9 2.6a1.8 1.8 0 0 1 2.5 0l1 1a1.8 1.8 0 0 1 0 2.5L8.1 16.4 3 18z"/><path d="M3 21h18"/></svg>`;
  document.getElementById('signatureBoxText').textContent = '點此請廠商簽名';
  document.getElementById('signatureBoxSub').textContent = '確認交付數量或到貨內容';
}

function setupCanvas() {
  const canvas = document.getElementById('sigCanvas');
  const wrap = document.getElementById('sigCanvasWrap');
  const rect = wrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = rect.width * ratio;
  canvas.height = rect.height * ratio;
  sigCtx = canvas.getContext('2d');
  sigCtx.scale(ratio, ratio);
  sigCtx.lineWidth = 2.4;
  sigCtx.lineCap = 'round';
  sigCtx.lineJoin = 'round';
  sigCtx.strokeStyle = '#1B1F1D';
  sigHasStroke = false;
  document.getElementById('sigPlaceholder').style.display = 'flex';

  function pos(evt) {
    const r = canvas.getBoundingClientRect();
    const p = evt.touches ? evt.touches[0] : evt;
    return { x: p.clientX - r.left, y: p.clientY - r.top };
  }
  function start(evt) {
    evt.preventDefault();
    sigDrawing = true;
    sigHasStroke = true;
    document.getElementById('sigPlaceholder').style.display = 'none';
    const p = pos(evt);
    sigCtx.beginPath();
    sigCtx.moveTo(p.x, p.y);
  }
  function move(evt) {
    if (!sigDrawing) return;
    evt.preventDefault();
    const p = pos(evt);
    sigCtx.lineTo(p.x, p.y);
    sigCtx.stroke();
  }
  function end() { sigDrawing = false; }

  canvas.onpointerdown = start;
  canvas.onpointermove = move;
  canvas.onpointerup = end;
  canvas.onpointerleave = end;
  canvas.ontouchstart = start;
  canvas.ontouchmove = move;
  canvas.ontouchend = end;
}

function openSignaturePad() {
  document.getElementById('sigOverlay').classList.add('show');
  requestAnimationFrame(setupCanvas);
}
function clearSignature() {
  setupCanvas();
}
function cancelSignature() {
  document.getElementById('sigOverlay').classList.remove('show');
}
function confirmSignature() {
  if (!sigHasStroke) {
    showToast('請先簽名再確認');
    return;
  }
  const canvas = document.getElementById('sigCanvas');
  signatureDataURL = canvas.toDataURL('image/png');
  const box = document.getElementById('signatureBox');
  box.classList.add('signed');
  document.getElementById('signatureBoxIcon').innerHTML = `<img src="${signatureDataURL}" alt="簽名">`;
  document.getElementById('signatureBoxText').textContent = '廠商已簽名';
  document.getElementById('signatureBoxSub').textContent = '點一下可重新簽名';
  document.getElementById('sigOverlay').classList.remove('show');
}

function showToast(text) {
  const t = document.getElementById('toast');
  document.getElementById('toastText').textContent = text;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

// ---------------- 配對連結（點連結或掃 QR） ----------------
function pairFail(title, msg) {
  document.getElementById('pairTitle').textContent = title;
  document.getElementById('pairMsg').textContent = msg;
  showScreen('screen-pair');
}
async function pairWithToken(token) {
  showScreen('screen-pair');
  try {
    const r = await fetch(SERVER_URL + '/api/v1/pair', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token }) });
    if (r.status === 200) {
      const j = await r.json();
      ExpenseSync.setKey(j.key);
      try { localStorage.removeItem('expense_role'); } catch (e) { /* 略過 */ }
      finishLogin(j.name); // 姓名由伺服器的裝置名稱決定；PIN 為選用，預設不設
      showToast('設定完成，' + j.name + '你好');
      loadMe();
    } else if (r.status === 429) {
      pairFail('請稍後再試', '嘗試次數太多了，請過幾分鐘再點一次連結。');
    } else {
      pairFail('這個連結已失效', '這個連結已失效，請向管理員索取新的。');
    }
  } catch (e) {
    pairFail('連不上伺服器', '請確認手機有網路，然後再點一次連結。');
  }
}

// ---------------- Init ----------------
renderHome();
updateNetUI();
loadPendingQueue();

document.getElementById('wifiOnly').checked = ExpenseSync.getPolicy() === 'wifi_only';
if (ExpenseSync.getKey()) loadMe();
const savedUser = loadUser();
const pairMatch = /^#pair=([A-Za-z0-9_-]{1,200})$/.exec(location.hash);
if (pairMatch) {
  history.replaceState(null, '', location.pathname + location.search); // 立刻清掉網址中的配對碼
  pairWithToken(pairMatch[1]);
} else if (!ExpenseSync.getKey()) {
  showBind(); // 尚未綁定裝置金鑰
} else if (savedUser && loadPins()[savedUser]) {
  chooseIdentity(savedUser); // 此身份已自行開啟 PIN → 顯示解鎖畫面
} else if (savedUser) {
  finishLogin(savedUser); // PIN 為選用、預設關閉：直接進首頁
} else {
  loginFromServer(); // 已有金鑰但沒存姓名：姓名來自伺服器
}
