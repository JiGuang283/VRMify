// Run bar (start / stop / progress) and the log.
import { $, S, api, pick, t, toast } from './core.js';
import { renderFiles } from './files.js';
import { flushOpts } from './options.js';
import { T, saveTune } from './tune.js';

$('start').onclick = async () => {
  await flushOpts();
  if (T.dirty && T.fid) await saveTune(T.edits);     // unsaved tune edits go into this conversion
  const r = await api('/api/convert', {});
  if (!r.started) toast(t('nothing'));
  S.result = null; S.logErrors = 0; renderBadge();
};
$('stop').onclick = () => api('/api/stop', {});
// after a run: show where the results went (the newest output, selected in the file manager)
const lastOutput = () => [...S.files].reverse().flatMap((f) => f.outputs.slice(-1))[0];
$('openOut').onclick = () => { const o = lastOutput(); if (o) api('/api/open', { path: o.path }); };

export function renderStatus() {
  $('start').hidden = S.busy; $('stop').hidden = !S.busy;
  let s = t('idle'), pct = 0;
  if (S.busy && S.progress) {
    s = t('converting', { i: S.progress.index + 1, n: S.progress.total, name: S.progress.name });
    pct = S.progress.index / S.progress.total * 100;
  } else if (S.result) { s = t(S.result.skipped ? 'finishedSkip' : 'finished', S.result); pct = 100; }
  $('status').textContent = s;
  $('openOut').hidden = S.busy || !lastOutput();
  $('progressFill').style.width = pct + '%';
  renderFiles();
}

let lastFile = null;
// fid: the file this line belongs to. When several files convert at once their lines interleave, so a short
// "· name" line marks each switch (a file's own first line "→ name" already says it)
// en: the English version of the line (null when there is none); both are kept so a language switch can redo the log
export function appendLog(msg, tag, fid, en) {
  const log = $('log');
  if (fid && fid !== lastFile && !msg.startsWith('→')) {
    const f = S.files.find((x) => x.id === fid);
    if (f) { const h = document.createElement('div'); h.className = 'file-mark'; h.textContent = '· ' + f.name; log.append(h); }
  }
  lastFile = fid || lastFile;
  const line = document.createElement('div');
  if (tag) line.className = tag;
  line.dataset.zh = msg;
  if (en) line.dataset.en = en;
  line.textContent = pick(msg, en);
  log.append(line);
  while (log.childElementCount > 3000) log.firstElementChild.remove();
  log.scrollTop = log.scrollHeight;
  if (tag === 'err') { S.logErrors++; renderBadge(); }
}
// after a language switch
export function relocalizeLog() {
  for (const line of $('log').children) if (line.dataset.zh !== undefined) line.textContent = pick(line.dataset.zh, line.dataset.en);
}
function renderBadge() { $('logBadge').hidden = !S.logErrors; $('logBadge').textContent = S.logErrors; }
$('logToggle').onclick = () => {
  const open = $('log').hidden;
  $('log').hidden = !open; $('logCard').classList.toggle('open', open);
  $('logToggle').setAttribute('aria-expanded', open);
};
$('clearLog').onclick = () => { $('log').innerHTML = ''; S.logErrors = 0; renderBadge(); };
