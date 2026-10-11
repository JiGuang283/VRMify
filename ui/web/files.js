// File list: add / remove, per-file status and outputs, drag & drop.
import { $, S, api, esc, pick, t, toast } from './core.js';
import { loadModel, previewFile } from './preview.js';
import { leaveTune, loadTune } from './tune.js';

const fmtSize = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';

export function renderFiles() {
  const ul = $('fileList');
  ul.innerHTML = '';
  for (const f of S.files) {
    const li = document.createElement('li');
    li.className = 'file' + (f.id === S.current ? ' current' : '');
    const btn = (act, icon, key) => `<button class="icon" data-act="${act}" title="${esc(t(key))}" aria-label="${esc(t(key))}">${icon}</button>`;
    li.innerHTML = `<div class="row1"><span class="kind">${f.kind}</span>
      <span class="name" title="${esc(f.path)} · ${fmtSize(f.size)}">${esc(f.name)}</span>
      ${f.tuned ? `<span class="chip tuned" title="${esc(t('tuned'))}">✎</span>` : ''}<span class="chip ${f.status}">${t('st_' + f.status)}</span>
      ${btn('preview', '👁', 'preview')}${btn('reveal', '📂', 'reveal')}${btn('remove', '✕', 'remove')}</div>`;
    if (f.outputs.length) {
      const d = document.createElement('div'); d.className = 'outs';
      d.innerHTML = t('outputs') + '：' + f.outputs.map((o, k) => `<a data-out="${k}" title="${esc(o.path)}">${esc(o.name)}</a>`).join('');
      li.append(d);
    }
    if (f.error) { const d = document.createElement('div'); d.className = 'err'; d.textContent = pick(f.error, f.error_en); li.append(d); }
    if (f.status === 'skipped') { const d = document.createElement('div'); d.className = 'note'; d.textContent = t('skippedNote'); li.append(d); }
    li.addEventListener('click', (e) => {
      const act = e.target.dataset.act, out = e.target.dataset.out;
      if (act === 'remove') api('/api/remove', { ids: [f.id] });
      else if (act === 'reveal') api('/api/open', { path: f.outputs.length ? f.outputs[f.outputs.length - 1].path : f.path });
      else if (out !== undefined) {
        const o = f.outputs[+out];
        if (!leaveTune(f.id)) return;
        if (o.url) { S.current = f.id; renderFiles(); loadModel(o.url, o.name); loadTune(); }
        else if (o.previewable) previewFile(f.id, +out);
        else api('/api/open', { path: o.path });
      } else previewFile(f.id);
    });
    ul.append(li);
  }
  $('drop').classList.toggle('compact', S.files.length > 0);
  $('drop').firstElementChild.textContent = t(S.files.length ? 'dropHintShort' : 'dropHint');
  const conv = S.files.filter((f) => f.kind !== 'vrm' && f.status !== 'running');
  const todo = conv.filter((f) => f.status === 'pending' || f.status === 'error').length;
  const n = todo || conv.length;                     // nothing left to convert → the button converts everything again
  $('startLabel').textContent = t(todo || !n ? 'start' : 'restart');
  $('startCount').textContent = n ? `(${n})` : '';
  $('start').disabled = S.busy || !n;
}

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
