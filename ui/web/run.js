// Run bar (start / stop / progress) and the log.
import { $, S, api, t, toast } from './core.js';
import { renderFiles } from './files.js';

$('start').onclick = async () => {
  const r = await api('/api/convert', {});
  if (!r.started) toast(t('nothing'));
  S.result = null; S.logErrors = 0; renderBadge();
};
$('stop').onclick = () => api('/api/stop', {});

export function renderStatus() {
  $('start').hidden = S.busy; $('stop').hidden = !S.busy;
  let s = t('idle'), pct = 0;
  if (S.busy && S.progress) {
    s = t('converting', { i: S.progress.index + 1, n: S.progress.total, name: S.progress.name });
    pct = S.progress.index / S.progress.total * 100;
  } else if (S.result) { s = t('finished', S.result); pct = 100; }
  $('status').textContent = s;
  $('progressFill').style.width = pct + '%';
  renderFiles();
}

export function appendLog(msg, tag) {
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
$('logToggle').onclick = () => {
  const open = $('log').hidden;
  $('log').hidden = !open; $('logCard').classList.toggle('open', open);
  $('logToggle').setAttribute('aria-expanded', open);
};
$('clearLog').onclick = () => { $('log').innerHTML = ''; S.logErrors = 0; renderBadge(); };
