// Model-To-VRM UI. Talks to ui/server.py over HTTP (same API in the pywebview window and a browser).
import { STRINGS } from './i18n.js';
import { Viewer } from '/viewer/viewer-core.js';

const $ = (id) => document.getElementById(id);
const S = { files: [], opts: {}, lang: 'zh_CN', busy: false, seq: 0, native: false, current: null, logErrors: 0, pending: null, features: {} };

// ---------------------------------------------------------------- i18n --
function t(key, args) {
  let s = (STRINGS[S.lang] || STRINGS.zh_CN)[key] ?? key;
  for (const [k, v] of Object.entries(args || {})) s = s.replaceAll('{' + k + '}', v);
  return s;
}
function applyLang() {
  document.documentElement.lang = S.lang === 'en' ? 'en' : 'zh-CN';
  document.querySelectorAll('[data-i18n]').forEach((e) => { e.textContent = t(e.dataset.i18n); });
  document.querySelectorAll('[data-i18n-ph]').forEach((e) => { e.placeholder = t(e.dataset.i18nPh); });
  document.querySelectorAll('[data-lang]').forEach((b) => b.classList.toggle('on', b.dataset.lang === S.lang));
  $('fileList').dataset.empty = t('empty');
  renderFiles(); renderStatus(); renderOutFolder(); syncDependents();
  if (!$('tunePanel').hidden) renderTune();
}

// ----------------------------------------------------------------- api --
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return r.json();
}
function toast(s) { const e = $('toast'); e.textContent = s; e.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => e.classList.remove('show'), 3000); }

async function loadState() {
  const st = await api('/api/state');
  Object.assign(S, { files: st.files, opts: st.opts, lang: st.lang, busy: st.busy, seq: st.seq, native: st.native, features: st.features || {} });
  $('ver').textContent = st.version ? 'v' + st.version : '';
  applyOptsToForm();
  applyLang();
}

async function pollEvents() {
  for (;;) {
    try {
      const r = await api('/api/events?after=' + S.seq);
      for (const ev of r.events) { S.seq = Math.max(S.seq, ev.seq); handleEvent(ev); }
    } catch (e) { await new Promise((res) => setTimeout(res, 1500)); }
  }
}

function handleEvent(ev) {
  if (ev.type === 'log') appendLog(ev.msg, ev.tag);
  else if (ev.type === 'file') {
    const i = S.files.findIndex((f) => f.id === ev.file.id);
    const was = i >= 0 ? S.files[i].status : null;
    if (i >= 0) S.files[i] = ev.file; else S.files.push(ev.file);
    renderFiles();
    // a file just finished converting → open it in the preview: the first one of each run (unless the Tune
    // panel has unsaved changes for the model on screen), and the one already on screen (show the real output)
    if (ev.file.status === 'done' && was !== 'done' && ev.file.outputs.some((o) => o.url)) {
      const first = !S.autoOpened && !(T.dirty && T.fid === S.current && ev.file.id !== S.current);
      if (first) S.autoOpened = true;
      if (first || ev.file.id === S.current) previewFile(ev.file.id);
    }
  } else if (ev.type === 'files') loadState();
  else if (ev.type === 'busy') {
    S.busy = ev.busy;
    if (ev.busy) S.autoOpened = false;
    if (!ev.busy) { S.progress = null; S.result = { done: ev.done, failed: ev.failed }; }
    renderStatus(); renderFiles();
  } else if (ev.type === 'progress') { S.progress = ev; renderStatus(); }
  else if (ev.type === 'preview_busy') { if (ev.file === S.current) showBusy(t('pvMaking')); }
  else if (ev.type === 'preview') {
    if (ev.file !== S.current) return;
    if (ev.error) { showBusy(null); toast(t('pvFailed', { err: ev.error })); } else { loadModel(ev.url, ev.name); loadTune(); }
  }
}

// --------------------------------------------------------------- files --
const fmtSize = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
function renderFiles() {
  const ul = $('fileList');
  ul.innerHTML = '';
  for (const f of S.files) {
    const li = document.createElement('li');
    li.className = 'file' + (f.id === S.current ? ' current' : '');
    li.innerHTML = `<div class="row1"><span class="kind">${f.kind}</span><span class="name" title="${esc(f.path)}">${esc(f.name)}</span>
      ${f.tuned ? `<span class="chip tuned" title="${t('tuned')}">✎</span>` : ''}<span class="chip ${f.status}">${t('st_' + f.status)}</span>
      <button class="icon" data-act="preview" title="${t('preview')}">👁</button>
      <button class="icon" data-act="reveal" title="${t('reveal')}">📂</button>
      <button class="icon" data-act="remove" title="${t('remove')}">✕</button></div>`;
    if (f.outputs.length) {
      const d = document.createElement('div'); d.className = 'outs';
      d.innerHTML = t('outputs') + '：' + f.outputs.map((o, k) => `<a data-out="${k}" title="${esc(o.path)}">${esc(o.name)}</a>`).join('');
      li.append(d);
    }
    if (f.error) { const d = document.createElement('div'); d.className = 'err'; d.textContent = f.error; li.append(d); }
    li.addEventListener('click', (e) => {
      const act = e.target.dataset.act, out = e.target.dataset.out;
      if (act === 'remove') api('/api/remove', { ids: [f.id] });
      else if (act === 'reveal') api('/api/open', { path: f.outputs.length ? f.outputs[f.outputs.length - 1].path : f.path });
      else if (out !== undefined) {
        const o = f.outputs[+out];
        if (o.url) { S.current = f.id; renderFiles(); loadModel(o.url, o.name); loadTune(); } else api('/api/open', { path: o.path });
      } else previewFile(f.id);
    });
    ul.append(li);
  }
  $('drop').classList.toggle('compact', S.files.length > 0);
  $('drop').firstElementChild.textContent = t(S.files.length ? 'dropHintShort' : 'dropHint');
  const conv = S.files.filter((f) => f.kind !== 'vrm' && f.status !== 'running');
  const n = conv.filter((f) => f.status === 'pending' || f.status === 'error').length || conv.length;
  $('startCount').textContent = n ? `(${n})` : '';
  $('start').disabled = S.busy || !n;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

$('addFiles').onclick = () => api('/api/pick', { folder: false });
$('addFolder').onclick = () => api('/api/pick', { folder: true });
$('clearFiles').onclick = () => api('/api/clear', {});
$('drop').onclick = () => api('/api/pick', { folder: false });

// Drag & drop: in the pywebview window the backend receives full paths through its DOM handler;
// a plain browser only exposes file names, so tell the user to use the buttons.
let dragDepth = 0;
window.addEventListener('dragenter', (e) => { e.preventDefault(); dragDepth++; document.body.classList.add('dragging'); });
window.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove('dragging'); } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault(); dragDepth = 0; document.body.classList.remove('dragging');
  if (!S.native) toast(t('dropBrowser'));
});

// ------------------------------------------------------------- options --
const getOpt = (path) => path.split('.').reduce((o, k) => (o ? o[k] : undefined), S.opts);
function setOpt(path, v) {
  const ks = path.split('.'); let o = S.opts;
  for (const k of ks.slice(0, -1)) o = o[k] = o[k] || {};
  o[ks[ks.length - 1]] = v;
}
function applyOptsToForm() {
  document.querySelectorAll('[data-opt]').forEach((el) => {
    const v = getOpt(el.dataset.opt);
    if (el.classList.contains('seg')) el.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b.dataset.v === String(v)));
    else if (el.type === 'checkbox') el.checked = !!v;
    else if (el.type === 'text') { if (document.activeElement !== el) el.value = v ?? ''; }
    else if (el.type === 'range') { el.value = v; const o = document.querySelector(`output[data-for="${el.dataset.opt}"]`); if (o) o.textContent = (+v).toFixed(1); }
  });
  syncDependents();
}
function syncDependents() {
  const is1 = getOpt('vrm.spec') !== '0.x', spring = getOpt('vrm.spring');
  document.querySelectorAll('[data-opt="vrm.spring"], [data-opt="vrm.spring_skirt"], [data-opt="vrm.spring_stiffness"]').forEach((el) => {
    el.disabled = !is1 || (el.dataset.opt !== 'vrm.spring' && !spring);
    el.closest('.switch, .field').style.opacity = el.disabled ? 0.45 : 1;
  });
  $('chooseOut').disabled = getOpt('output.mode') !== 'folder';
  // texture slimming needs Pillow on the Python side
  const pil = S.features.pillow !== false;
  document.querySelectorAll('[data-opt="vrm.tex_max"] button, [data-opt="vrm.tex_jpeg"]').forEach((el) => { el.disabled = !pil; });
  document.querySelector('[data-opt="vrm.tex_max"]').closest('.field').style.opacity = pil ? 1 : 0.45;
  document.querySelector('[data-opt="vrm.tex_jpeg"]').closest('.switch').style.opacity = pil ? 1 : 0.45;
  $('texHint').textContent = t(pil ? 'texHint' : 'noPillow');
  $('texHint').classList.toggle('warn', !pil);
  renderUeRef();
  renderOutFolder();
}
function renderUeRef() {
  const f = getOpt('uemodel.align_ref');
  $('ueRef').textContent = f ? f.split(/[\\/]/).pop() : t('noRef');
  $('ueRef').title = f || '';
  $('ueRefClear').disabled = !f;
}
function renderOutFolder() {
  const f = getOpt('output.folder');
  $('outFolder').textContent = f || t('noFolder');
  $('outFolder').title = f || '';
  $('outFolder').style.opacity = getOpt('output.mode') === 'folder' ? 1 : 0.5;
}
let saveTimer = null;
function saveOpts() { clearTimeout(saveTimer); saveTimer = setTimeout(() => api('/api/options', { opts: S.opts }), 250); }
document.querySelectorAll('[data-opt]').forEach((el) => {
  if (el.classList.contains('seg')) {
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      setOpt(el.dataset.opt, b.dataset.v); applyOptsToForm(); saveOpts();
    });
  } else el.addEventListener('input', () => {
    setOpt(el.dataset.opt, el.type === 'checkbox' ? el.checked : el.type === 'text' ? el.value.trim() : +el.value);
    applyOptsToForm(); saveOpts();
  });
});
$('ueRefPick').onclick = async () => {
  const r = await api('/api/pick_ue_ref', {});
  if (!r.path) return;
  setOpt('uemodel.align_ref', r.path); renderUeRef(); saveOpts();
  if (r.warn) toast(t('ueWeakRef', { cm: r.warn }));
};
$('ueRefClear').onclick = () => { setOpt('uemodel.align_ref', ''); renderUeRef(); saveOpts(); };
$('chooseOut').onclick = async () => {
  const r = await api('/api/pick_output', {});
  if (r.folder) { setOpt('output.folder', r.folder); renderOutFolder(); saveOpts(); }
};
document.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('[data-tab]').forEach((x) => x.classList.toggle('on', x === b));
  document.querySelectorAll('[data-panel]').forEach((p) => { p.hidden = p.dataset.panel !== b.dataset.tab; });
}));
document.querySelectorAll('[data-lang]').forEach((b) => b.addEventListener('click', () => {
  S.lang = b.dataset.lang; applyLang(); api('/api/options', { lang: S.lang });
  if (viewer.info) fillPreviewPanels(viewer.info);
}));

// ----------------------------------------------------------- run / log --
$('start').onclick = async () => {
  const r = await api('/api/convert', {});
  if (!r.started) toast(t('nothing'));
  S.result = null; S.logErrors = 0; renderBadge();
};
$('stop').onclick = () => api('/api/stop', {});
function renderStatus() {
  $('start').hidden = S.busy; $('stop').hidden = !S.busy;
  let s = t('idle'), pct = 0;
  if (S.busy && S.progress) { s = t('converting', { i: S.progress.index + 1, n: S.progress.total, name: S.progress.name }); pct = S.progress.index / S.progress.total * 100; }
  else if (S.result) { s = t('finished', S.result); pct = 100; }
  $('status').textContent = s;
  $('progressFill').style.width = pct + '%';
  renderFiles();
}
function appendLog(msg, tag) {
  const log = $('log');
  const line = document.createElement('div');
  if (tag) line.className = tag;
  line.textContent = msg;
  log.append(line);
  while (log.childElementCount > 3000) log.firstElementChild.remove();
  log.scrollTop = log.scrollHeight;
  if (tag === 'err') { S.logErrors++; renderBadge(); }
}
function renderBadge() { $('logBadge').hidden = !S.logErrors; $('logBadge').textContent = S.logErrors; }
$('logToggle').onclick = () => { const open = $('log').hidden; $('log').hidden = !open; $('logCard').classList.toggle('open', open); };
$('clearLog').onclick = () => { $('log').innerHTML = ''; S.logErrors = 0; renderBadge(); };

// ------------------------------------------------------------- preview --
const viewer = new Viewer($('pv'));
viewer.onLoad = (info) => {
  fillPreviewPanels(info);
  // reload after saving tune edits keeps the camera
  if (S.keepView) { viewer.camera.position.copy(S.keepView.pos); viewer.controls.target.copy(S.keepView.tgt); viewer.controls.update(); S.keepView = null; }
  else viewer.frame(document.querySelector('[data-frame].on').dataset.frame);
  if (T.fid === S.current && T.info && T.info.ready && T.dirty) viewer.setSpringParams(springParams());
};
let modelUrl = null;

function showBusy(text) { $('pvBusy').hidden = !text; $('pvBusyText').textContent = text || ''; }
async function previewFile(id) {
  S.current = id; renderFiles(); loadTune();
  const r = await api('/api/preview', { id });
  if (r.url) loadModel(r.url, r.name);
  else if (r.pending) showBusy(t('pvMaking'));
  else if (r.error) toast(r.error);
}
async function loadModel(url, name, keepView) {
  showBusy(t('pvLoading'));
  S.keepView = keepView && viewer.vrm ? { pos: viewer.camera.position.clone(), tgt: viewer.controls.target.clone() } : null;
  try {
    await viewer.loadUrl(url, name);
    modelUrl = url;
    $('pvEmpty').hidden = true;
    $('pvTitle').textContent = name;
  } catch (e) { toast(t('pvFailed', { err: e.message })); }
  showBusy(null);
}
function fillPreviewPanels(info) {
  const rows = S.lang === 'en'
    ? [['VRM', info.spec], ['Height', info.height.toFixed(2) + ' m'], ['Humanoid bones', info.humanBones], ['Triangles', info.triangles.toLocaleString()],
      ['Materials', info.materials], ['Textures', info.textures], ['Expressions', info.expressions.length],
      ['SpringBone', info.springs ? `${info.springs} chains / ${info.joints} joints` : '—'], ['Colliders', info.colliders]]
    : [['VRM', info.spec], ['身高', info.height.toFixed(2) + ' m'], ['humanoid 骨', info.humanBones], ['三角面', info.triangles.toLocaleString()],
      ['材质', info.materials], ['贴图', info.textures], ['表情', info.expressions.length],
      ['SpringBone', info.springs ? `${info.springs} 条 / ${info.joints} 关节` : '—'], ['碰撞体', info.colliders]];
  $('infoList').innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  const box = $('exprList'); box.innerHTML = '';
  for (const e of info.expressions) {
    const row = document.createElement('div'); row.className = 'field'; row.dataset.name = e.name.toLowerCase();
    row.innerHTML = `<label title="${esc(e.name)}">${esc(e.name)}</label><input type="range" min="0" max="1" step="0.01" value="0">`;
    row.querySelector('input').addEventListener('input', (ev) => viewer.setExpression(e.name, +ev.target.value));
    box.append(row);
  }
  filterExpr();
}
function filterExpr() {
  const q = $('exprFilter').value.trim().toLowerCase();
  for (const r of $('exprList').children) r.style.display = !q || r.dataset.name.includes(q) ? '' : 'none';
}
$('exprFilter').addEventListener('input', filterExpr);
$('exprReset').onclick = () => { viewer.resetExpressions(); $('exprList').querySelectorAll('input').forEach((i) => { i.value = 0; }); };

const segClick = (id, attr, fn) => $(id).addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  $(id).querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
  fn(b.getAttribute(attr));
});
segClick('frameSeg', 'data-frame', (k) => viewer.frame(k));
segClick('motionSeg', 'data-motion', (m) => viewer.setMotion(m));
segClick('bgSeg', 'data-bg', (c) => viewer.setBackground(c));
$('pvSpring').addEventListener('change', () => viewer.setSpring($('pvSpring').checked));
function panelToggle(btn, panel) {
  $(btn).onclick = () => {
    const open = $(panel).hidden;
    ['lightPanel', 'exprPanel', 'infoPanel', 'tunePanel'].forEach((p) => { $(p).hidden = true; });
    $(panel).hidden = !open;
    if (open && panel === 'tunePanel') loadTune();
  };
}
panelToggle('lightBtn', 'lightPanel'); panelToggle('exprBtn', 'exprPanel'); panelToggle('infoBtn', 'infoPanel'); panelToggle('tuneBtn', 'tunePanel');
function updateLight() {
  viewer.setLight({ az: +$('lAz').value, el: +$('lEl').value, intensity: +$('lI').value, ambient: +$('lAmb').value, shadow: $('lShadow').checked });
  $('lAzV').textContent = $('lAz').value + '°'; $('lElV').textContent = $('lEl').value + '°';
  $('lIV').textContent = (+$('lI').value).toFixed(1); $('lAmbV').textContent = (+$('lAmb').value).toFixed(2);
}
['lAz', 'lEl', 'lI', 'lAmb', 'lShadow'].forEach((id) => $(id).addEventListener('input', updateLight));
updateLight();
$('shotBtn').onclick = async () => {
  if (!viewer.vrm) return;
  const r = await api('/api/snapshot', { name: (viewer.name || 'vrm').replace(/\.[^.]+$/, ''), data: viewer.snapshot(), model: modelUrl });
  if (r.path) toast(t('saved', { path: r.path }));
};

// ---------------------------------------------------------------- tune --
// Per-model adjustments written into the converted VRM (convert/vrm_edit.py): name, thumbnail,
// which expression drives each VRM preset, SpringBone multipliers. Physics changes preview live.
const T = { fid: null, info: null, edits: {}, dirty: false };
const MAIN_PRESETS = {
  '1.0': ['happy', 'angry', 'sad', 'relaxed', 'surprised', 'neutral', 'aa', 'ih', 'ou', 'ee', 'oh', 'blink', 'blinkLeft', 'blinkRight'],
  '0.x': ['joy', 'angry', 'sorrow', 'fun', 'neutral', 'a', 'i', 'u', 'e', 'o', 'blink', 'blink_l', 'blink_r'],
};
// three-vrm loads VRM 0.x presets under their 1.0 names
const V0_TO_V1 = { a: 'aa', i: 'ih', u: 'ou', e: 'ee', o: 'oh', joy: 'happy', sorrow: 'sad', fun: 'relaxed', blink_l: 'blinkLeft', blink_r: 'blinkRight' };
const presetLabel = (p) => ((STRINGS[S.lang] || STRINGS.zh_CN).preset || {})[p] || p;
const clone = (o) => JSON.parse(JSON.stringify(o || {}));
const num = (v, d) => (typeof v === 'number' && isFinite(v) ? v : d);

async function loadTune() {
  if ($('tunePanel').hidden) return;
  const fid = S.current;
  if (!fid) { T.fid = null; T.info = null; renderTune(); return; }
  const r = await api('/api/tune', { id: fid });
  if (fid !== S.current) return;
  T.fid = fid; T.info = r; T.edits = clone(r.edits); delete T.edits.thumbnail; T.dirty = false;
  renderTune();
}

function assigned(p, edits) {
  const e = (edits || T.edits).expressions || {};
  return p in e ? e[p] : (T.info.assign[p] || '');
}

// name of a source expression in the currently loaded (saved) model, for the "Try" button
function exprNameInModel(sid) {
  const saved = clone(T.info.edits);
  const is1 = T.info.spec === '1.0';
  for (const p of T.info.presets) if (assigned(p, saved) === sid) return is1 ? p : (V0_TO_V1[p] || p);
  return sid.startsWith('@') ? 'orig_' + sid.slice(1) : sid;
}

function springParams() {
  const sp = T.edits.spring || {}, gs = num(sp.stiffness, 1), gd = num(sp.drag, 1), gg = num(sp.gravity, 0), ch = sp.chains || {};
  const out = [];
  for (const c of T.info.springs || []) {
    const e = ch[c.key] || {}, cs = num(e.stiffness, 1), cg = num(e.gravity, 0);
    for (const j of c.joints) out.push({ node: j.node, enabled: !e.off,
      stiffness: Math.max(0, j.stiffness * gs * cs), gravityPower: Math.max(0, j.gravityPower + gg + cg),
      dragForce: Math.min(1, Math.max(0, j.dragForce * gd)) });
  }
  return out;
}

function markDirty() {
  T.dirty = true;
  $('tuneState').textContent = t('tuneDirty');
  $('tuneSave').disabled = false;
}

function slider(label, val, min, max, step, onInput, fmt) {
  const d = document.createElement('div'); d.className = 'field';
  d.innerHTML = `<label>${esc(label)}</label><input type="range" min="${min}" max="${max}" step="${step}" value="${val}"><output>${fmt(val)}</output>`;
  const inp = d.querySelector('input'), o = d.querySelector('output');
  inp.addEventListener('input', () => { o.textContent = fmt(inp.value); onInput(+inp.value); });
  return d;
}
const fmtMul = (v) => '×' + (+v).toFixed(2), fmtAdd = (v) => '+' + (+v).toFixed(2);

function renderTune() {
  const body = $('tuneBody'); body.innerHTML = '';
  const msg = (text) => { body.innerHTML = `<div class="tune-msg">${esc(text)}</div>`; $('tuneFoot').hidden = true; };
  if (!T.info || T.fid !== S.current) return msg(t('tuneNoFile'));
  if (T.info.error) return msg(T.info.error);
  if (T.info.editable === false) return msg(t('tuneNotEditable'));
  if (!T.info.ready) return msg(t('tuneNotReady'));
  $('tuneFoot').hidden = false;
  $('tuneState').textContent = T.dirty ? t('tuneDirty') : t('tuneClean');
  $('tuneSave').disabled = !T.dirty;
  const h3 = (k) => { const h = document.createElement('h3'); h.textContent = t(k); body.append(h); return h; };

  // name
  h3('tuneTitle');
  const nf = document.createElement('div'); nf.className = 'field';
  nf.innerHTML = `<input type="text" class="input" placeholder="${esc(T.info.title || '')}" value="${esc(T.edits.title || '')}">`;
  nf.querySelector('input').addEventListener('input', (e) => { T.edits.title = e.target.value.trim(); markDirty(); });
  body.append(nf);

  // thumbnail (saved immediately: it is a picture of the current view)
  h3('tuneThumb');
  const tr = document.createElement('div'); tr.className = 'thumb-row';
  tr.innerHTML = (T.info.thumbnail ? `<img src="${T.info.thumbnail}" alt="">` : `<div class="noimg">${t('thumbNone')}</div>`) +
    `<div class="col"><div><button class="btn small" data-a="shot">${t('thumbFromView')}</button> ` +
    (T.info.thumbnail ? `<button class="btn small ghost" data-a="rm">${t('thumbRemove')}</button>` : '') +
    `</div><div class="hint flush">${t('thumbHint')}</div></div>`;
  tr.addEventListener('click', async (e) => {
    const a = e.target.dataset.a;
    if (!a || !viewer.vrm) return;
    const r = await api('/api/tune/thumb', { id: T.fid, data: a === 'shot' ? viewer.snapshotSquare(1024) : '' });
    if (r.error) return toast(r.error);
    T.info.thumbnail = r.thumbnail || null;
    toast(t('thumbSaved'));
    renderTune();
  });
  body.append(tr);

  // preset expressions
  h3('tuneExpr');
  const opts = `<option value="">${esc(t('exprNone'))}</option>` +
    T.info.expressions.map((x) => `<option value="${esc(x.id)}">${esc(x.label)}</option>`).join('');
  for (const p of MAIN_PRESETS[T.info.spec] || []) {
    const d = document.createElement('div'); d.className = 'field';
    d.innerHTML = `<label title="${esc(p)}">${esc(presetLabel(p))}</label><select>${opts}</select><button class="mini" title="${t('tryExpr')}">▶</button>`;
    const sel = d.querySelector('select'), base = T.info.assign[p] || '';
    sel.value = assigned(p);
    sel.classList.toggle('changed', sel.value !== base);
    sel.addEventListener('change', () => {
      T.edits.expressions = T.edits.expressions || {};
      if (sel.value === base) delete T.edits.expressions[p]; else T.edits.expressions[p] = sel.value;
      sel.classList.toggle('changed', sel.value !== base);
      markDirty();
    });
    d.querySelector('button').addEventListener('click', () => {
      if (!sel.value) return;
      viewer.resetExpressions(); viewer.setExpression(exprNameInModel(sel.value), 1);
      clearTimeout(renderTune.tryTimer); renderTune.tryTimer = setTimeout(() => viewer.resetExpressions(), 1500);
    });
    body.append(d);
  }

  // physics (VRM 1.0 SpringBone)
  if ((T.info.springs || []).length) {
    h3('tunePhysics');
    const sp = T.edits.spring = T.edits.spring || {};
    sp.chains = sp.chains || {};
    const live = () => { viewer.setSpringParams(springParams()); markDirty(); };
    const sub = document.createElement('div'); sub.className = 'hint flush'; sub.textContent = t('allChains'); body.append(sub);
    body.append(slider(t('stiffMul'), num(sp.stiffness, 1), 0.2, 3, 0.05, (v) => { sp.stiffness = v; live(); }, fmtMul));
    body.append(slider(t('dragMul'), num(sp.drag, 1), 0.2, 2, 0.05, (v) => { sp.drag = v; live(); }, fmtMul));
    body.append(slider(t('gravAdd'), num(sp.gravity, 0), 0, 1, 0.02, (v) => { sp.gravity = v; live(); }, fmtAdd));
    const flt = document.createElement('div'); flt.className = 'field';
    flt.innerHTML = `<label>${esc(t('chains', { n: T.info.springs.length }))}</label><input class="input" placeholder="${esc(t('filter'))}">`;
    body.append(flt);
    const list = document.createElement('div'); list.className = 'chain-list'; body.append(list);
    for (const c of T.info.springs) {
      const e = sp.chains[c.key] || {};
      const get = () => (sp.chains[c.key] = sp.chains[c.key] || {});
      const row = document.createElement('div'); row.className = 'chain' + (e.off ? ' off' : ''); row.dataset.name = c.name.toLowerCase();
      const sw = document.createElement('label'); sw.className = 'switch';
      sw.innerHTML = `<input type="checkbox" ${e.off ? '' : 'checked'}><span title="${esc(c.name)}">${esc(c.name)} · ${c.joints.length}</span>`;
      sw.querySelector('input').addEventListener('change', (ev) => { get().off = !ev.target.checked; row.classList.toggle('off', !ev.target.checked); live(); });
      row.append(sw,
        slider(t('stiffMul'), num(e.stiffness, 1), 0.2, 3, 0.05, (v) => { get().stiffness = v; live(); }, fmtMul),
        slider(t('gravAdd'), num(e.gravity, 0), 0, 1, 0.02, (v) => { get().gravity = v; live(); }, fmtAdd));
      list.append(row);
    }
    flt.querySelector('input').addEventListener('input', (ev) => {
      const q = ev.target.value.trim().toLowerCase();
      for (const r of list.children) r.style.display = !q || r.dataset.name.includes(q) ? '' : 'none';
    });
  }
}

async function saveTune(edits) {
  const r = await api('/api/tune/save', { id: T.fid, edits });
  if (r.error) return toast(r.error);
  toast(r.applied ? t('tuneSaved', { n: r.applied }) : t('tuneSavedNone'));
  T.dirty = false;
  if (modelUrl && r.applied) await loadModel(modelUrl, viewer.name, true);
  await loadTune();
}
$('tuneSave').onclick = () => saveTune(T.edits);
$('tuneReset').onclick = () => { T.edits = {}; saveTune({}); };

// ------------------------------------------------------------- startup --
await loadState();
pollEvents();
window.__app = { S, T, viewer, previewFile };
