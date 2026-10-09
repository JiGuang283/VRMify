// VRMify UI entry. Talks to ui/server.py over HTTP (same API in the pywebview window and a browser).
//   core.js     shared state, i18n, api
//   files.js    file list          options.js  conversion settings
//   run.js      run bar + log      preview.js  3D preview, toolbar, side column
//   tune.js     tune panel (per-model adjustments written into the VRM)
//   layout.js   drag handles for the column widths
import { $, S, api, t, toast } from './core.js';
import { renderFiles } from './files.js';
import { applyOptsToForm, renderOutFolder, syncDependents } from './options.js';
import { appendLog, renderStatus } from './run.js';
import { applyLayout } from './layout.js';
import { fillPreviewPanels, loadModel, previewFile, showBusy, viewer } from './preview.js';
import { T, loadTune, renderTune } from './tune.js';

function applyLang() {
  document.documentElement.lang = S.lang === 'en' ? 'en' : 'zh-CN';
  document.querySelectorAll('[data-i18n]').forEach((e) => { e.textContent = t(e.dataset.i18n); });
  document.querySelectorAll('[data-i18n-ph]').forEach((e) => { e.placeholder = t(e.dataset.i18nPh); });
  document.querySelectorAll('[data-i18n-title]').forEach((e) => { e.title = t(e.dataset.i18nTitle); });
  document.querySelectorAll('[data-lang]').forEach((b) => b.classList.toggle('on', b.dataset.lang === S.lang));
  $('fileList').dataset.empty = t('empty');
  renderStatus(); renderOutFolder(); syncDependents();
  fillPreviewPanels(viewer.info);
  if (!$('tunePanel').hidden) renderTune();
}
document.querySelectorAll('[data-lang]').forEach((b) => b.addEventListener('click', () => {
  S.lang = b.dataset.lang; applyLang(); api('/api/options', { lang: S.lang });
}));

async function loadState() {
  const st = await api('/api/state');
  Object.assign(S, { files: st.files, opts: st.opts, lang: st.lang, busy: st.busy, seq: st.seq, native: st.native,
    features: st.features || {}, appOut: st.app_out || '', ui: st.ui || {} });
  applyLayout();
  $('ver').textContent = st.version ? 'v' + st.version : '';
  applyOptsToForm();
  applyLang();
}

function handleEvent(ev) {
  if (ev.type === 'log') appendLog(ev.msg, ev.tag, ev.file);
  else if (ev.type === 'file') {
    const i = S.files.findIndex((f) => f.id === ev.file.id);
    const was = i >= 0 ? S.files[i].status : null;
    if (i >= 0) S.files[i] = ev.file; else S.files.push(ev.file);
    renderFiles();
    // a file just finished converting → open it in the preview: the first one of each run (unless the Tune
    // panel has unsaved changes for the model on screen), and the one already on screen (show the real output)
    if (ev.file.status === 'done' && was !== 'done' && ev.file.outputs.some((o) => o.url || o.previewable)) {
      const first = !S.autoOpened && !(T.dirty && T.fid === S.current && ev.file.id !== S.current);
      if (first) S.autoOpened = true;
      if (first || ev.file.id === S.current) previewFile(ev.file.id);
    }
  } else if (ev.type === 'files') loadState();
  else if (ev.type === 'busy') {
    S.busy = ev.busy;
    if (ev.busy) S.autoOpened = false;
    else { S.progress = null; S.result = { done: ev.done, failed: ev.failed, skipped: ev.skipped || 0 }; }
    renderStatus();
  } else if (ev.type === 'progress') { S.progress = ev; renderStatus(); }
  else if (ev.type === 'preview_busy') { if (ev.file === S.current) showBusy(t('pvMaking')); }
  else if (ev.type === 'preview') {
    if (ev.file !== S.current) return;
    if (ev.error) { showBusy(null); toast(t('pvFailed', { err: ev.error })); } else { loadModel(ev.url, ev.name); loadTune(); }
  }
}

async function pollEvents() {
  for (;;) {
    try {
      const r = await api('/api/events?after=' + S.seq);
      for (const ev of r.events) { S.seq = Math.max(S.seq, ev.seq); handleEvent(ev); }
    } catch (e) { await new Promise((res) => setTimeout(res, 1500)); }
  }
}

await loadState();
pollEvents();
window.__app = { S, T, viewer, previewFile };
