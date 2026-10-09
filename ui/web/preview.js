// 3D preview (viewer-core.js), its toolbar and the side column (light / expressions / tune / info).
import { BONE_COLORS, Viewer } from '/viewer/viewer-core.js';
import { $, S, api, esc, t, toast } from './core.js';
import { renderFiles } from './files.js';
import { flushOpts } from './options.js';
import { T, leaveTune, loadTune, springParams } from './tune.js';

export const viewer = new Viewer($('pv'));
export let modelUrl = null;

viewer.onLoad = (info) => {
  fillPreviewPanels(info);
  fillLegend();
  // reload after saving tune edits keeps the camera
  if (S.keepView) { viewer.camera.position.copy(S.keepView.pos); viewer.controls.target.copy(S.keepView.tgt); viewer.controls.update(); S.keepView = null; }
  else viewer.frame(document.querySelector('[data-frame].on').dataset.frame);
  if (T.fid === S.current && T.info && T.info.ready && T.dirty) viewer.setSpringParams(springParams());
};

export function showBusy(text) { $('pvBusy').hidden = !text; $('pvBusyText').textContent = text || ''; }

export async function previewFile(id, output) {
  if (!leaveTune(id)) return;
  S.current = id; renderFiles();
  // the tune panel opens with the preview once there is something to tune (a converted / preview VRM),
  // unless another panel (light / expressions / info) is open
  S.autoTune = !openPanel();
  loadTune();
  await flushOpts();
  const r = await api('/api/preview', output === undefined ? { id } : { id, output });
  if (r.url) loadModel(r.url, r.name);
  else if (r.pending) showBusy(t('pvMaking'));
  else if (r.error) toast(r.error);
}

export async function loadModel(url, name, keepView) {
  showBusy(t('pvLoading'));
  S.keepView = keepView && viewer.loaded ? { pos: viewer.camera.position.clone(), tgt: viewer.controls.target.clone() } : null;
  try {
    await viewer.loadUrl(url, name);
    modelUrl = url;
    $('pvEmpty').hidden = true;
    $('pvHint').hidden = false;
    $('pvTitle').textContent = name;
  } catch (e) { toast(t('pvFailed', { err: e.message })); }
  showBusy(null);
}

// ---- side column: one panel at a time; the preview narrows instead of being covered
const PANELS = { lightPanel: 'lightBtn', exprPanel: 'exprBtn', tunePanel: 'tuneBtn', infoPanel: 'infoBtn' };
export const openPanel = () => Object.keys(PANELS).find((p) => !$(p).hidden) || null;
export function showPanel(id) {
  for (const [p, btn] of Object.entries(PANELS)) {
    $(p).hidden = p !== id;
    $(btn).classList.toggle('on', p === id);
    $(btn).setAttribute('aria-pressed', p === id);
  }
  $('side').hidden = !id;
  if (id !== 'tunePanel') viewer.highlightChain(null);
  $('splitSide').hidden = !id;
  $('side').classList.toggle('wide', id === 'tunePanel');
}
for (const [p, btn] of Object.entries(PANELS)) {
  $(btn).onclick = () => {
    S.autoTune = false;
    const open = $(p).hidden;
    showPanel(open ? p : null);
    if (open && p === 'tunePanel') loadTune();
  };
}

// ---- info + expression panels
export function fillPreviewPanels(info) {
  if (!info) return;
  const rows = info.spec === 'PMX'
    ? [[t('iFormat'), 'PMX'], [t('iHeight'), info.height.toFixed(1) + t('iMmdUnit')], [t('iTris'), info.triangles.toLocaleString()],
      [t('iMats'), info.materials], [t('iTex'), info.textures], [t('iMorphs'), info.expressions.length],
      [t('iPhysics'), info.rigidBodies ? t('iRigid', { r: info.rigidBodies, j: info.constraints }) + (info.physics ? '' : t('iOff')) : '—']]
    : [['VRM', info.spec], [t('iHeight'), info.height.toFixed(2) + ' m'], [t('iHumanoid'), info.humanBones],
      [t('iTris'), info.triangles.toLocaleString()], [t('iMats'), info.materials], [t('iTex'), info.textures],
      [t('iExpr'), info.expressions.length], ['SpringBone', info.springs ? t('iChains', { n: info.springs, j: info.joints }) : '—'],
      [t('iColliders'), info.colliders]];
  if (info.bones) rows.splice(info.spec === 'PMX' ? 1 : 2, 0, [t('iBones'), t('iBonesV', { n: info.bones.total, u: info.bones.unused })]);
  $('infoList').innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  const box = $('exprList'); box.innerHTML = '';
  for (const e of info.expressions) {
    const row = document.createElement('div'); row.className = 'field'; row.dataset.name = e.name.toLowerCase();
    row.innerHTML = `<label title="${esc(e.name)}">${esc(e.name)}</label><input type="range" min="0" max="1" step="0.01" value="0" aria-label="${esc(e.name)}">`;
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

// ---- toolbar
const segClick = (id, attr, fn) => $(id).addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  $(id).querySelectorAll('button').forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); });
  fn(b.getAttribute(attr));
});
segClick('frameSeg', 'data-frame', (k) => viewer.frame(k));
segClick('motionSeg', 'data-motion', (m) => viewer.setMotion(m));
segClick('bgSeg', 'data-bg', (c) => viewer.setBackground(c));
$('pvSpring').addEventListener('change', () => viewer.setSpring($('pvSpring').checked));

// ---- skeleton overlay: bones coloured by role, legend with counts, name + parent of the bone under the pointer
const BONE_KINDS = ['humanoid', 'weighted', 'spring', 'control', 'unused'];
const kindLabel = (k) => t({ humanoid: viewer.kind === 'pmx' ? 'bHumanoidPmx' : 'bHumanoid', weighted: 'bWeighted',
  spring: 'bSpring', control: 'bControl', unused: 'bUnused' }[k]);
function fillLegend() {
  const on = $('pvBones').checked && viewer.loaded;
  $('pvLegend').hidden = !on;
  $('pvTip').hidden = true;
  if (!on) return;
  const s = viewer.boneStats();
  $('pvLegend').innerHTML = BONE_KINDS.filter((k) => s[k]).map((k) =>
    `<div><i style="background:${BONE_COLORS[k]}"></i>${esc(kindLabel(k))}<b>${s[k]}</b></div>`).join('');
}
$('pvBones').addEventListener('change', () => { viewer.setBonesVisible($('pvBones').checked); fillLegend(); });
$('pv').addEventListener('pointermove', (e) => {
  if (!$('pvBones').checked || e.buttons) { $('pvTip').hidden = true; viewer.boneHover = -1; return; }
  const b = viewer.boneAt(e.clientX, e.clientY), tip = $('pvTip');
  tip.hidden = !b;
  if (!b) return;
  const r = $('pv').getBoundingClientRect();
  tip.innerHTML = `<div>${esc(b.name)}</div><div class="k">${esc(kindLabel(b.kind))} · ${esc(t('bParent', { p: b.parent || t('bRoot') }))}</div>`;
  tip.style.left = (e.clientX - r.left) + 'px'; tip.style.top = (e.clientY - r.top) + 'px';
});
$('pv').addEventListener('pointerleave', () => { $('pvTip').hidden = true; viewer.boneHover = -1; });

function updateLight() {
  viewer.setLight({ az: +$('lAz').value, el: +$('lEl').value, intensity: +$('lI').value, ambient: +$('lAmb').value, shadow: $('lShadow').checked });
  $('lAzV').textContent = $('lAz').value + '°'; $('lElV').textContent = $('lEl').value + '°';
  $('lIV').textContent = (+$('lI').value).toFixed(1); $('lAmbV').textContent = (+$('lAmb').value).toFixed(2);
}
['lAz', 'lEl', 'lI', 'lAmb', 'lShadow'].forEach((id) => $(id).addEventListener('input', updateLight));
updateLight();

$('shotBtn').onclick = async () => {
  if (!viewer.loaded) return;
  const r = await api('/api/snapshot', { name: (viewer.name || 'vrm').replace(/\.[^.]+$/, ''), data: viewer.snapshot(), model: modelUrl });
  if (r.path) toast(t('saved', { path: r.path }));
};
