// File list: add / remove, per-file status and outputs, drag & drop.
import { $, S, api, esc, pick, t, toast } from './core.js';
import { loadModel, previewFile } from './preview.js';
import { T, leaveTune, loadTune } from './tune.js';

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
        if (o.url) { S.current = f.id; renderFiles(); loadModel(o.url, o.name); if (!T.dirty) loadTune(); }
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

// In the pywebview window files come with their paths: the native dialog (/api/pick) and the backend's own drop
// handler (ui/app.py). A plain browser never shows the page a path, so there the files are uploaded to the local
// program instead (into a temp folder, folders keep their structure so a PMX still finds its textures) and added
// from there (/api/upload, /api/upload/done).
const pickFiles = () => (S.native ? api('/api/pick', { folder: false }) : $('upFiles').click());
$('addFiles').onclick = pickFiles;
$('drop').onclick = pickFiles;
$('addFolder').onclick = () => (S.native ? api('/api/pick', { folder: true }) : $('upFolder').click());
$('clearFiles').onclick = () => api('/api/clear', {});

// a group = what ends up in one upload folder: one dropped / picked folder, or all loose files of one drop / pick
// (together, so a PMX dropped with its textures finds them). key: same content → same folder on every upload.
async function groupKey(files) {
  const s = files.map((x) => `${x.rel}|${x.file.size}|${x.file.lastModified}`).sort().join('\n');
  const h = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(s));
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function upload(groups) {
  groups = groups.filter((g) => g.files.length);
  const total = groups.reduce((n, g) => n + g.files.length, 0);
  if (!total) return;
  let done = 0, failed = 0;
  const items = [];
  for (const g of groups) {
    const key = await groupKey(g.files);
    for (const x of g.files) {
      toast(t('uploading', { i: ++done, n: total, name: x.file.name }));
      try {
        const r = await (await fetch(`/api/upload?key=${encodeURIComponent(key)}&path=${encodeURIComponent(x.rel)}`,
          { method: 'POST', body: x.file })).json();
        if (r.error) failed++;
      } catch (e) { failed++; }
    }
    items.push({ key, roots: g.roots });
  }
  const r = await api('/api/upload/done', { items });
  toast(failed ? t('uploadFailed', { n: failed }) : t('uploaded', { n: (r.added || []).length }));
}

// files from <input type=file>: loose files are one group; with webkitdirectory each file carries its folder path
function fromInput(list) {
  const loose = { roots: [], files: [] }, dirs = {};
  for (const file of list) {
    const rel = file.webkitRelativePath || file.name, top = rel.split('/')[0];
    if (rel === file.name) { loose.files.push({ file, rel }); loose.roots.push(file.name); continue; }
    (dirs[top] ||= { roots: [top], files: [] }).files.push({ file, rel });
  }
  return [loose, ...Object.values(dirs)];
}
for (const id of ['upFiles', 'upFolder']) {
  $(id).addEventListener('change', (e) => { const g = fromInput(e.target.files); e.target.value = ''; upload(g); });
}

// dropped items: folders are walked with the entries API (readEntries returns at most 100 at a time)
const entryFile = (en) => new Promise((res, rej) => en.file(res, rej));
async function walk(entry, prefix, out) {
  if (entry.isFile) { out.push({ file: await entryFile(entry), rel: prefix + entry.name }); return; }
  const reader = entry.createReader();
  for (;;) {
    const batch = await new Promise((res, rej) => reader.readEntries(res, rej));
    if (!batch.length) break;
    for (const en of batch) await walk(en, prefix + entry.name + '/', out);
  }
}
async function fromDrop(dt) {
  // entries have to be taken while the drop event is running; reading them can wait
  const got = [...(dt.items || [])].filter((it) => it.kind === 'file')
    .map((it) => ({ entry: it.webkitGetAsEntry ? it.webkitGetAsEntry() : null, file: it.getAsFile() }));
  const loose = { roots: [], files: [] }, groups = [loose];
  for (const { entry, file } of got) {
    if (entry && entry.isDirectory) {
      const g = { roots: [entry.name], files: [] };
      await walk(entry, '', g.files);
      groups.push(g);
    } else if (file) { loose.files.push({ file, rel: file.name }); loose.roots.push(file.name); }
  }
  return groups;
}

let dragDepth = 0;
window.addEventListener('dragenter', (e) => { e.preventDefault(); dragDepth++; document.body.classList.add('dragging'); });
window.addEventListener('dragleave', () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove('dragging'); } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => {
  e.preventDefault(); dragDepth = 0; document.body.classList.remove('dragging');
  if (!S.native && e.dataTransfer) fromDrop(e.dataTransfer).then(upload);
});
