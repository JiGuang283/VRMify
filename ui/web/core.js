// Shared state and helpers for the UI modules (files / options / run / preview / tune).
import { STRINGS } from './i18n.js';

export const $ = (id) => document.getElementById(id);

// App state mirrored from ui/server.py (/api/state + events), plus UI-only bits.
export const S = {
  files: [], opts: {}, lang: 'zh_CN', busy: false, seq: 0, native: false, features: {}, appOut: '',
  current: null,        // file id shown in the preview
  logErrors: 0, progress: null, result: null,
  autoOpened: false,    // first file of a run already opened in the preview
  autoTune: false,      // open the tune panel by itself once the model on screen can be tuned
  ui: {},               // column widths from settings.json (layout.js)
};

export function t(key, args) {
  let s = (STRINGS[S.lang] || STRINGS.zh_CN)[key] ?? key;
  for (const [k, v] of Object.entries(args || {})) s = s.replaceAll('{' + k + '}', v);
  return s;
}
export const presetLabel = (p) => ((STRINGS[S.lang] || STRINGS.zh_CN).preset || {})[p] || p;

// Text from the server comes in Chinese, with an English version next to it when there is one
// (log lines, conversion errors: convert/logtext.py)
export const pick = (zh, en) => (S.lang === 'en' && en) || zh;
// an error from the server: a few fixed codes have their own strings, the rest come as zh + English version
const ERR = { 'no such file': 'errNoFile', 'not previewable': 'errNoPreview', 'file not found': 'errNotFound' };
export const errText = (e, en) => (ERR[e] ? t(ERR[e]) : pick(e, en));

export async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  return r.json();
}

export function toast(s) {
  const e = $('toast');
  e.textContent = s; e.classList.add('show');
  clearTimeout(toast._t); toast._t = setTimeout(() => e.classList.remove('show'), 3000);
}

export const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
export const clone = (o) => JSON.parse(JSON.stringify(o || {}));
export const num = (v, d) => (typeof v === 'number' && isFinite(v) ? v : d);
