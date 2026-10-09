// Conversion settings: form ↔ S.opts (saved to settings.json through /api/options), tabs.
// The first tab shows what most conversions need; finer options sit in the "Advanced" fold.
import { $, S, api, t, toast } from './core.js';

export const getOpt = (path) => path.split('.').reduce((o, k) => (o ? o[k] : undefined), S.opts);
function setOpt(path, v) {
  const ks = path.split('.'); let o = S.opts;
  for (const k of ks.slice(0, -1)) o = o[k] = o[k] || {};
  o[ks[ks.length - 1]] = v;
}

// "Convert to" combines two stored options: target (vrm / pmx) and vrm.spec (1.0 / 0.x)
const targetValue = () => (getOpt('target') === 'pmx' ? 'pmx' : getOpt('vrm.spec') === '0.x' ? 'vrm0' : 'vrm1');
// "File size" presets over the two texture options; anything else shows as custom (set under Advanced)
const SIZES = { orig: [0, false], mid: [2048, true], small: [1024, true] };
const sizeValue = () => Object.keys(SIZES).find((k) => SIZES[k][0] === +getOpt('vrm.tex_max') && SIZES[k][1] === !!getOpt('vrm.tex_jpeg'));

const markSeg = (seg, v) => seg.querySelectorAll('button').forEach((b) => {
  const on = b.dataset.v === v;
  b.classList.toggle('on', on); b.setAttribute('aria-pressed', on);
});

export function applyOptsToForm() {
  document.querySelectorAll('[data-opt]').forEach((el) => {
    const v = getOpt(el.dataset.opt);
    if (el.classList.contains('seg')) markSeg(el, String(v));
    else if (el.type === 'checkbox') el.checked = !!v;
    else if (el.type === 'text') { if (document.activeElement !== el) el.value = v ?? ''; }
    else if (el.type === 'range') {
      el.value = v;
      const o = document.querySelector(`output[data-for="${el.dataset.opt}"]`);
      if (o) o.textContent = (+v).toFixed(1);
    }
  });
  markSeg($('targetSeg'), targetValue());
  markSeg($('sizeSeg'), sizeValue());
  syncDependents();
}

// greys out (and disables) options that don't apply with the current choices
const dim = (el, off) => { el.disabled = off; el.closest('.switch, .field').classList.toggle('dim', off); };
export function syncDependents() {
  const target = targetValue(), spring = getOpt('vrm.spring');
  document.querySelector('.settings-card').classList.toggle('pmx', target === 'pmx');
  $('targetHint').textContent = t('targetHint_' + target);
  document.querySelectorAll('[data-opt="vrm.spring"], [data-opt="vrm.spring_skirt"], [data-opt="vrm.auto_spring"], [data-opt="vrm.spring_stiffness"]')
    .forEach((el) => dim(el, target === 'vrm0' || (el.dataset.opt !== 'vrm.spring' && !spring)));
  document.querySelectorAll('[data-opt="vrm.gaze"], [data-opt="vrm.combo_expr"]').forEach((el) => dim(el, !getOpt('vrm.morphs')));
  $('chooseOut').disabled = getOpt('output.mode') !== 'folder';
  // texture slimming needs Pillow on the Python side
  const pil = S.features.pillow !== false;
  for (const seg of [$('sizeSeg'), document.querySelector('[data-opt="vrm.tex_max"]')]) {
    seg.querySelectorAll('button').forEach((b) => { b.disabled = !pil && b.dataset.v !== 'orig' && b.dataset.v !== '0'; });
  }
  dim(document.querySelector('[data-opt="vrm.tex_jpeg"]'), !pil);
  const size = sizeValue();
  $('texHint').textContent = !pil ? t('noPillow') : size ? t('sizeHint_' + size)
    : t('sizeCustom', { max: +getOpt('vrm.tex_max') || t('texOrig'), fmt: getOpt('vrm.tex_jpeg') ? 'JPEG' : 'PNG' });
  $('texHint').classList.toggle('warn', !pil);
  renderUeRef();
  renderOutFolder();
  syncFormats();
}

// "Other formats" tab: only the sections for kinds in the file list; archives may hold any of them
let showAllFormats = false;
export function syncFormats() {
  const kinds = new Set(S.files.map((f) => f.kind));
  const any = ['zip', '7z', 'rar', 'tar'].some((k) => kinds.has(k)) || showAllFormats;
  let shown = 0;
  document.querySelectorAll('.fmt').forEach((sec) => {
    const on = any || sec.dataset.kinds.split(' ').some((k) => kinds.has(k));
    sec.hidden = !on; shown += on;
  });
  $('inputNone').hidden = shown > 0;
}
$('inputAll').onclick = () => { showAllFormats = true; syncFormats(); };

function renderUeRef() {
  const f = getOpt('uemodel.align_ref');
  $('ueRef').textContent = f ? f.split(/[\\/]/).pop() : t('noRef');
  $('ueRef').title = f || '';
  $('ueRefClear').disabled = !f;
}
export function renderOutFolder() {
  const mode = getOpt('output.mode'), f = mode === 'app' ? S.appOut : getOpt('output.folder');
  $('outFolder').textContent = mode === 'beside' ? t('outBeside') : (f || t('noFolder'));
  $('outFolder').title = f || '';
  $('outFolder').classList.toggle('dim', mode === 'beside');
}

let saveTimer = null;
function saveOpts() { clearTimeout(saveTimer); saveTimer = setTimeout(flushOpts, 250); }
// sends a pending change now (starting a conversion right after changing a setting must use the new value)
export async function flushOpts() {
  if (saveTimer === null) return;
  clearTimeout(saveTimer); saveTimer = null;
  await api('/api/options', { opts: S.opts });
}
const changed = () => { applyOptsToForm(); saveOpts(); };

document.querySelectorAll('[data-opt]').forEach((el) => {
  if (el.classList.contains('seg')) {
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      setOpt(el.dataset.opt, b.dataset.v); changed();
    });
  } else {
    el.addEventListener('input', () => {
      setOpt(el.dataset.opt, el.type === 'checkbox' ? el.checked : el.type === 'text' ? el.value.trim() : +el.value);
      changed();
    });
  }
});
$('targetSeg').addEventListener('click', (e) => {
  const v = e.target.closest('button')?.dataset.v; if (!v) return;
  setOpt('target', v === 'pmx' ? 'pmx' : 'vrm');
  if (v !== 'pmx') setOpt('vrm.spec', v === 'vrm0' ? '0.x' : '1.0');
  changed();
});
$('sizeSeg').addEventListener('click', (e) => {
  const v = e.target.closest('button')?.dataset.v; if (!v || !SIZES[v]) return;
  setOpt('vrm.tex_max', String(SIZES[v][0])); setOpt('vrm.tex_jpeg', SIZES[v][1]);
  changed();
});
// license terms back to the conservative defaults (author / contact fields stay)
const META_DEFAULTS = { avatar_permission: 'everyone', commercial_usage: 'personalNonProfit', credit_notation: 'required',
  allow_redistribution: false, modification: 'prohibited' };
$('metaReset').onclick = () => { for (const [k, v] of Object.entries(META_DEFAULTS)) setOpt('meta.' + k, v); changed(); };

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
// the Advanced fold remembers whether it was open (per browser profile; harmless if storage is unavailable)
try { $('advanced').open = localStorage.getItem('advancedOpen') === '1'; } catch (e) { /* no storage */ }
$('advanced').addEventListener('toggle', () => {
  try { localStorage.setItem('advancedOpen', $('advanced').open ? '1' : '0'); } catch (e) { /* no storage */ }
});
