// Standalone VRM preview page (gfx/web_preview.py): wires the sidebar controls to the shared Viewer.
// Loads ?model=<url> or a dropped / picked file.
import { Viewer } from './viewer-core.js';

const $ = (id) => document.getElementById(id);
const view = $('view');
const viewer = new Viewer(view);
let modelName = '';

function status(s) { $('status').textContent = s || ''; }
function toast(s) { const e = $('toast'); e.textContent = s; e.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => e.classList.remove('show'), 2500); }

viewer.onLoad = (info) => {
  const rows = [
    ['规范', 'VRM ' + info.spec], ['身高', info.height.toFixed(2) + ' m'], ['humanoid 骨', info.humanBones],
    ['三角面', info.triangles.toLocaleString()], ['材质', info.materials], ['贴图', info.textures],
    ['表情', info.expressions.length],
    ['SpringBone', info.springs ? info.springs + ' 条 / ' + info.joints + ' 关节' : '无'], ['碰撞体', info.colliders],
  ];
  $('info').innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  fillExpressions(info.expressions);
  $('title').textContent = info.name;
  $('file').textContent = modelName;
  $('drop').textContent = '';
  document.title = 'VRM 预览 - ' + modelName;
  viewer.frame(document.querySelector('[data-frame].on')?.dataset.frame || 'body');
  status('');
};

async function load(fn, name) {
  modelName = name;
  status('读取 ' + name + ' …');
  try { await fn(); } catch (e) { status(''); toast('打开失败：' + e.message); console.error(e); }
}

// ---------------------------------------------------------- expressions --
function fillExpressions(list) {
  const box = $('expr');
  box.innerHTML = '';
  if (!list.length) { box.innerHTML = '<div class="sub">没有表情</div>'; return; }
  for (const e of list) {
    const row = document.createElement('div');
    row.className = 'row'; row.dataset.name = e.name.toLowerCase();
    const lab = document.createElement('label'); lab.textContent = e.name; lab.title = e.name;
    const r = document.createElement('input'); r.type = 'range'; r.min = 0; r.max = 1; r.step = 0.01; r.value = 0;
    r.addEventListener('input', () => viewer.setExpression(e.name, +r.value));
    row.append(lab, r); box.append(row);
  }
  filterExpressions();
}
function filterExpressions() {
  const q = $('exprFilter').value.trim().toLowerCase();
  for (const row of $('expr').children) if (row.dataset.name !== undefined) row.style.display = !q || row.dataset.name.includes(q) ? '' : 'none';
}
$('exprFilter').addEventListener('input', filterExpressions);
$('exprReset').addEventListener('click', () => {
  viewer.resetExpressions();
  for (const r of $('expr').querySelectorAll('input')) r.value = 0;
});

// ------------------------------------------------------- camera / motion --
const seg = (attr, fn) => document.querySelectorAll(`[${attr}]`).forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll(`[${attr}]`).forEach((x) => x.classList.toggle('on', x === b));
  fn(b.getAttribute(attr));
}));
seg('data-frame', (k) => viewer.frame(k));
seg('data-motion', (m) => viewer.setMotion(m));
seg('data-bg', (c) => viewer.setBackground(c));
$('speed').addEventListener('input', () => { viewer.setSpeed(+$('speed').value); $('speedV').textContent = $('speed').value + '×'; });
$('pause').addEventListener('change', () => viewer.setPaused($('pause').checked));
$('spring').addEventListener('change', () => viewer.setSpring($('spring').checked));
$('resetPhys').addEventListener('click', () => viewer.resetPhysics());

// -------------------------------------------------------------- lights --
function updateLight() {
  viewer.setLight({ az: +$('lightAz').value, el: +$('lightEl').value, intensity: +$('lightI').value,
    ambient: +$('ambI').value, shadow: $('shadow').checked });
  $('lightAzV').textContent = $('lightAz').value + '°';
  $('lightElV').textContent = $('lightEl').value + '°';
  $('lightIV').textContent = (+$('lightI').value).toFixed(1);
  $('ambIV').textContent = (+$('ambI').value).toFixed(2).replace(/0$/, '');
}
['lightAz', 'lightEl', 'lightI', 'ambI', 'shadow'].forEach((id) => $(id).addEventListener('input', updateLight));
updateLight();

// ----------------------------------------------------------- file input --
const openFile = (f) => load(async () => viewer.loadBuffer(await f.arrayBuffer(), f.name), f.name);
$('open').addEventListener('click', () => $('picker').click());
$('picker').addEventListener('change', () => { const f = $('picker').files[0]; if (f) openFile(f); });
view.addEventListener('dragover', (e) => { e.preventDefault(); $('drop').classList.add('over'); });
view.addEventListener('dragleave', () => $('drop').classList.remove('over'));
view.addEventListener('drop', (e) => {
  e.preventDefault(); $('drop').classList.remove('over');
  const f = [...e.dataTransfer.files].find((x) => /\.(vrm|glb)$/i.test(x.name));
  if (f) openFile(f); else toast('只能打开 .vrm 文件');
});

// ------------------------------------------------------------ snapshot --
$('shot').addEventListener('click', async () => {
  const url = viewer.snapshot();
  const base = (modelName || 'vrm').replace(/\.[^.]+$/, '');
  if (location.protocol.startsWith('http')) {
    try {
      const r = await fetch('api/snapshot?name=' + encodeURIComponent(base), { method: 'POST', body: url });
      if (r.ok) { toast('已保存：' + (await r.text())); return; }
    } catch (e) { /* fall through to download */ }
  }
  const a = document.createElement('a'); a.href = url; a.download = base + '_preview.png'; a.click();
});

// ------------------------------------------------------------- startup --
const q = new URLSearchParams(location.search);
if (q.get('model')) {
  const name = q.get('name') || q.get('model').split('/').pop();
  load(() => viewer.loadUrl(q.get('model'), name), name);
}
// keep-alive for gfx/web_preview.py in browser mode (it exits a minute after the tab is closed)
if (location.protocol.startsWith('http')) setInterval(() => fetch('api/ping').catch(() => {}), 10000);
window.__preview = { viewer, get vrm() { return viewer.vrm; }, frame: (k) => viewer.frame(k),
  setMotion: (m) => document.querySelector(`[data-motion="${m}"]`)?.click() };
