// Tune panel: per-model adjustments written into the converted VRM (convert/vrm_edit.py) — name, thumbnail,
// which expression drives each VRM preset and how strong, gaze range, SpringBone multipliers.
// Physics changes preview live; everything else is written on "Save".
import { $, S, api, clone, esc, num, presetLabel, t, toast } from './core.js';
import { loadModel, modelUrl, showPanel, viewer } from './preview.js';

export const T = {
  fid: null, info: null, edits: {}, dirty: false,
  open: { meta: true, expr: true, look: true, physics: true, chains: false },   // collapsible sections (kept across models)
};

// three-vrm loads VRM 0.x presets under their 1.0 names
const V0_TO_V1 = { a: 'aa', i: 'ih', u: 'ou', e: 'ee', o: 'oh', joy: 'happy', sorrow: 'sad', fun: 'relaxed', blink_l: 'blinkLeft', blink_r: 'blinkRight' };

// switching to another model drops unsaved tune edits: ask first
export function leaveTune(id) {
  if (!T.dirty || !T.fid || T.fid === id) return true;
  if (!confirm(t('tuneDiscard'))) return false;
  T.dirty = false;
  return true;
}

export async function loadTune() {
  if ($('tunePanel').hidden && !S.autoTune) return;
  const fid = S.current;
  if (!fid) { T.fid = null; T.info = null; renderTune(); return; }
  const r = await api('/api/tune', { id: fid });
  if (fid !== S.current) return;
  T.fid = fid; T.info = r; T.edits = clone(r.edits); delete T.edits.thumbnail; T.dirty = false;
  if (S.autoTune && r.ready && $('tunePanel').hidden) { showPanel('tunePanel'); S.autoTune = false; }
  if (!$('tunePanel').hidden) renderTune();
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

export function springParams() {
  const sp = T.edits.spring || {}, gs = num(sp.stiffness, 1), gd = num(sp.drag, 1), gg = num(sp.gravity, 0), ch = sp.chains || {};
  const out = [];
  for (const c of T.info.springs || []) {
    const e = ch[c.key] || {}, cs = num(e.stiffness, 1), cg = num(e.gravity, 0);
    for (const j of c.joints) {
      out.push({ node: j.node, enabled: !e.off, stiffness: Math.max(0, j.stiffness * gs * cs),
        gravityPower: Math.max(0, j.gravityPower + gg + cg), dragForce: Math.min(1, Math.max(0, j.dragForce * gd)) });
    }
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
  d.innerHTML = `<label>${esc(label)}</label><input type="range" min="${min}" max="${max}" step="${step}" value="${val}" aria-label="${esc(label)}"><output>${fmt(val)}</output>`;
  const inp = d.querySelector('input'), o = d.querySelector('output');
  inp.addEventListener('input', () => { o.textContent = fmt(inp.value); onInput(+inp.value); });
  return d;
}
const fmtMul = (v) => '×' + (+v).toFixed(2), fmtAdd = (v) => '+' + (+v).toFixed(2), fmtDeg = (v) => (+v).toFixed(1) + '°';
const hint = (text) => { const d = document.createElement('div'); d.className = 'hint flush'; d.textContent = text; return d; };

// a collapsible section; its open / closed state is remembered in T.open
function section(parent, key, title) {
  const d = document.createElement('details'); d.className = 'sec'; d.open = T.open[key];
  d.addEventListener('toggle', () => { T.open[key] = d.open; });
  const s = document.createElement('summary'); s.textContent = title;
  d.append(s); parent.append(d);
  return d;
}

export function renderTune() {
  const body = $('tuneBody');
  const keep = T.renderedFid === T.fid ? body.scrollTop : 0;   // re-render (reset / thumbnail …) stays in place
  T.renderedFid = T.fid;
  requestAnimationFrame(() => { body.scrollTop = keep; });
  body.innerHTML = '';
  const msg = (text) => { body.innerHTML = `<div class="tune-msg">${esc(text)}</div>`; $('tuneFoot').hidden = true; };
  if (!T.info || T.fid !== S.current) return msg(t('tuneNoFile'));
  if (T.info.error) return msg(T.info.error);
  if (T.info.editable === false) return msg(t('tuneNotEditable'));
  if (!T.info.ready) {
    const f = S.files.find((x) => x.id === T.fid);
    return msg(t(f && f.kind === 'pmx' ? 'tuneNotReadyPmx' : 'tuneNotReady'));
  }
  $('tuneFoot').hidden = false;
  $('tuneState').textContent = T.dirty ? t('tuneDirty') : t('tuneClean');
  $('tuneSave').disabled = !T.dirty;
  renderMeta(section(body, 'meta', t('tuneMeta')));
  renderExpr(section(body, 'expr', t('tuneExpr')));
  if (T.info.look) renderLook(section(body, 'look', t('tuneLook')));
  if ((T.info.springs || []).length) renderPhysics(section(body, 'physics', t('tunePhysics')));
}

// ---- name + thumbnail (the thumbnail is saved right away: it is a picture of the current view)
function renderMeta(sec) {
  const nf = document.createElement('div'); nf.className = 'field';
  nf.innerHTML = `<label>${esc(t('tuneTitle'))}</label><input type="text" class="input" placeholder="${esc(T.info.title || '')}" value="${esc(T.edits.title || '')}">`;
  nf.querySelector('input').addEventListener('input', (e) => { T.edits.title = e.target.value.trim(); markDirty(); });
  const tr = document.createElement('div'); tr.className = 'thumb-row';
  tr.innerHTML = (T.info.thumbnail ? `<img src="${T.info.thumbnail}" alt="">` : `<div class="noimg">${t('thumbNone')}</div>`) +
    `<div class="col"><div><button class="btn small" data-a="shot">${t('thumbFromView')}</button> ` +
    (T.info.thumbnail ? `<button class="btn small ghost" data-a="rm">${t('thumbRemove')}</button>` : '') +
    `</div><div class="hint flush">${t('thumbHint')}</div></div>`;
  tr.addEventListener('click', async (e) => {
    const a = e.target.dataset.a;
    if (!a || !viewer.loaded) return;
    const r = await api('/api/tune/thumb', { id: T.fid, data: a === 'shot' ? viewer.snapshotSquare(1024) : '' });
    if (r.error) return toast(r.error);
    T.info.thumbnail = r.thumbnail || null;
    toast(t('thumbSaved'));
    renderTune();
  });
  sec.append(nf, tr);
}

// ---- which expression drives each preset, and how strong
function renderExpr(sec) {
  sec.append(hint(t('exprHint')));
  const tryExpr = (sid, w) => {
    if (!sid) return;
    viewer.resetExpressions(); viewer.setExpression(exprNameInModel(sid), w);
    clearTimeout(renderTune.tryTimer); renderTune.tryTimer = setTimeout(() => viewer.resetExpressions(), 1500);
  };
  const choices = T.info.expressions.map((x) => `<option value="${esc(x.id)}">${esc(x.label)}</option>`).join('');
  // gaze presets are driven by the gaze range below, not picked here
  for (const p of (T.info.presets || []).filter((x) => !/^look/i.test(x))) {
    const base = T.info.assign[p] || '';
    const none = `<option value="">${esc(t(base ? 'exprNone' : 'exprMissing'))}</option>`;
    const d = document.createElement('div'); d.className = 'field preset';
    const k = num((T.edits.intensity || {})[p], 1);
    d.innerHTML = `<label title="${esc(p)}">${esc(presetLabel(p))}</label><select aria-label="${esc(presetLabel(p))}">${none}${choices}</select>` +
      `<input type="range" class="strength" min="0" max="1.5" step="0.05" value="${k}" title="${esc(t('exprStrength'))}" aria-label="${esc(t('exprStrength'))}">` +
      `<output class="strength">${Math.round(k * 100)}%</output><button class="mini" title="${esc(t('tryExpr'))}" aria-label="${esc(t('tryExpr'))}">▶</button>`;
    const sel = d.querySelector('select'), rng = d.querySelector('input'), out = d.querySelector('output');
    const sync = () => {
      sel.classList.toggle('changed', sel.value !== base);
      sel.title = sel.selectedOptions[0] ? sel.selectedOptions[0].textContent : '';   // combos are long: full text on hover
      d.classList.toggle('missing', !sel.value);
      rng.classList.toggle('changed', +rng.value !== 1);
    };
    sel.value = assigned(p);
    sync();
    rng.addEventListener('input', () => {
      const v = +rng.value;
      out.textContent = Math.round(v * 100) + '%';
      T.edits.intensity = T.edits.intensity || {};
      if (v === 1) delete T.edits.intensity[p]; else T.edits.intensity[p] = v;
      sync(); tryExpr(sel.value, v); markDirty();
    });
    sel.addEventListener('change', () => {
      T.edits.expressions = T.edits.expressions || {};
      if (sel.value === base) delete T.edits.expressions[p]; else T.edits.expressions[p] = sel.value;
      sync(); markDirty();
    });
    d.querySelector('button').addEventListener('click', () => tryExpr(sel.value, +rng.value));
    sec.append(d);
  }
}

// ---- gaze range (VRM look-at max angles; also re-weights the added gaze expressions)
function renderLook(sec) {
  sec.append(hint(t('lookHint')));
  const cur = () => Object.assign({}, T.info.look, T.edits.lookAt || {});
  // yaw / pitch for each preview arrow, as seen on screen from the front (VRM look-at input:
  // +yaw = the model's own left = screen right; ±90 = full range)
  const peek = (dir) => {
    const [yaw, pitch] = { left: [-90, 0], right: [90, 0], up: [0, 90], down: [0, -90] }[dir];
    viewer.setLook(yaw, pitch, cur());
    clearTimeout(renderTune.lookTimer); renderTune.lookTimer = setTimeout(() => viewer.setLook(null), 1500);
  };
  const set = (key, v) => { T.edits.lookAt = Object.assign(cur(), { [key]: v }); markDirty(); };
  sec.append(slider(t('lookH'), cur().h, 0, 40, 0.5, (v) => { set('h', v); peek('left'); }, fmtDeg),
    slider(t('lookDown'), cur().down, 0, 30, 0.5, (v) => { set('down', v); peek('down'); }, fmtDeg),
    slider(t('lookUp'), cur().up, 0, 30, 0.5, (v) => { set('up', v); peek('up'); }, fmtDeg));
  const row = document.createElement('div'); row.className = 'field';
  row.innerHTML = `<label></label>` + ['left', 'right', 'up', 'down'].map((d) =>
    `<button class="mini" data-d="${d}" title="${esc(t('lookTry'))}" aria-label="${esc(t('lookTry'))}">${{ left: '◀', right: '▶', up: '▲', down: '▼' }[d]}</button>`).join(' ') +
    (T.edits.lookAt ? ` <button class="btn small ghost" data-d="reset">${esc(t('lookReset'))}</button>` : '');
  row.addEventListener('click', (e) => {
    const d = e.target.dataset.d;
    if (!d) return;
    if (d === 'reset') { delete T.edits.lookAt; markDirty(); renderTune(); return; }
    peek(d);
  });
  sec.append(row);
}

// ---- physics (VRM 1.0 SpringBone): overall multipliers, then each chain (collapsed by default)
// the chain kept shown in the preview (pin button), per file; hovering another chain shows that one meanwhile
let pinned = null;                 // { fid, key, nodes }
const pinnedNodes = () => (pinned && pinned.fid === T.fid ? pinned.nodes : null);
const ICON_EYE = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2"/></svg>';
const ICON_AIM = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="5"/><path d="M8 1v3M8 12v3M1 8h3M12 8h3"/></svg>';

function renderPhysics(sec) {
  if (pinned && (pinned.fid !== T.fid || !T.info.springs.some((c) => c.key === pinned.key))) pinned = null;
  viewer.highlightChain(pinnedNodes());
  const sp = T.edits.spring = T.edits.spring || {};
  sp.chains = sp.chains || {};
  const live = () => { viewer.setSpringParams(springParams()); markDirty(); };
  sec.append(hint(t('allChains')),
    slider(t('stiffMul'), num(sp.stiffness, 1), 0.2, 3, 0.05, (v) => { sp.stiffness = v; live(); }, fmtMul),
    slider(t('dragMul'), num(sp.drag, 1), 0.2, 2, 0.05, (v) => { sp.drag = v; live(); }, fmtMul),
    slider(t('gravAdd'), num(sp.gravity, 0), 0, 1, 0.02, (v) => { sp.gravity = v; live(); }, fmtAdd));
  const chains = section(sec, 'chains', t('chains', { n: T.info.springs.length }));
  const flt = document.createElement('div'); flt.className = 'field';
  flt.innerHTML = `<input class="input" placeholder="${esc(t('filter'))}" aria-label="${esc(t('filter'))}">`;
  const list = document.createElement('div'); list.className = 'chain-list';
  chains.append(flt, list);
  for (const c of T.info.springs) {
    const e = sp.chains[c.key] || {};
    const get = () => (sp.chains[c.key] = sp.chains[c.key] || {});
    const row = document.createElement('div'); row.className = 'chain' + (e.off ? ' off' : ''); row.dataset.name = c.name.toLowerCase();
    const nodes = c.joints.map((j) => j.node);
    row.classList.toggle('pinned', !!pinned && pinned.fid === T.fid && pinned.key === c.key);
    const head = document.createElement('div'); head.className = 'chain-head';
    const pin = document.createElement('button'); pin.className = 'ibtn' + (row.classList.contains('pinned') ? ' on' : '');
    pin.innerHTML = ICON_EYE; pin.title = t('pinChain'); pin.setAttribute('aria-label', t('pinChain'));
    pin.onclick = () => {
      const on = !row.classList.contains('pinned');
      list.querySelectorAll('.chain.pinned').forEach((r) => { r.classList.remove('pinned'); r.querySelector('.ibtn').classList.remove('on'); });
      pinned = on ? { fid: T.fid, key: c.key, nodes } : null;
      row.classList.toggle('pinned', on); pin.classList.toggle('on', on);
      viewer.highlightChain(on ? nodes : null);
    };
    const aim = document.createElement('button'); aim.className = 'ibtn';
    aim.innerHTML = ICON_AIM; aim.title = t('focusChain'); aim.setAttribute('aria-label', t('focusChain'));
    aim.onclick = () => viewer.focusChain(nodes);
    const sw = document.createElement('label'); sw.className = 'switch';
    sw.innerHTML = `<input type="checkbox" ${e.off ? '' : 'checked'}><span title="${esc(c.name)}">${esc(c.name)} · ${c.joints.length}</span>`;
    sw.querySelector('input').addEventListener('change', (ev) => { get().off = !ev.target.checked; row.classList.toggle('off', !ev.target.checked); live(); });
    head.append(sw, pin, aim);
    row.append(head,
      slider(t('stiffMul'), num(e.stiffness, 1), 0.2, 3, 0.05, (v) => { get().stiffness = v; live(); }, fmtMul),
      slider(t('gravAdd'), num(e.gravity, 0), 0, 1, 0.02, (v) => { get().gravity = v; live(); }, fmtAdd));
    // hovering a chain shows it in the preview (joints, hit radius, its colliders)
    row.addEventListener('pointerenter', () => viewer.highlightChain(nodes));
    row.addEventListener('pointerleave', () => viewer.highlightChain(pinnedNodes()));
    list.append(row);
  }
  flt.querySelector('input').addEventListener('input', (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    for (const r of list.children) r.style.display = !q || r.dataset.name.includes(q) ? '' : 'none';
  });
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
