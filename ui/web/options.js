// Conversion settings: form ↔ S.opts (saved to settings.json through /api/options), tabs.
import { $, S, api, t, toast } from './core.js';

export const getOpt = (path) => path.split('.').reduce((o, k) => (o ? o[k] : undefined), S.opts);
function setOpt(path, v) {
  const ks = path.split('.'); let o = S.opts;
  for (const k of ks.slice(0, -1)) o = o[k] = o[k] || {};
  o[ks[ks.length - 1]] = v;
}

export function applyOptsToForm() {
  document.querySelectorAll('[data-opt]').forEach((el) => {
    const v = getOpt(el.dataset.opt);
    if (el.classList.contains('seg')) {
      el.querySelectorAll('button').forEach((b) => {
        const on = b.dataset.v === String(v);
        b.classList.toggle('on', on); b.setAttribute('aria-pressed', on);
      });
    } else if (el.type === 'checkbox') el.checked = !!v;
    else if (el.type === 'text') { if (document.activeElement !== el) el.value = v ?? ''; }
    else if (el.type === 'range') {
      el.value = v;
      const o = document.querySelector(`output[data-for="${el.dataset.opt}"]`);
      if (o) o.textContent = (+v).toFixed(1);
    }
  });
  syncDependents();
}

// greys out (and disables) options that don't apply with the current choices
const dim = (el, off) => { el.disabled = off; el.closest('.switch, .field').classList.toggle('dim', off); };
export function syncDependents() {
  const is1 = getOpt('vrm.spec') !== '0.x', spring = getOpt('vrm.spring');
  document.querySelectorAll('[data-opt="vrm.spring"], [data-opt="vrm.spring_skirt"], [data-opt="vrm.auto_spring"], [data-opt="vrm.spring_stiffness"]')
    .forEach((el) => dim(el, !is1 || (el.dataset.opt !== 'vrm.spring' && !spring)));
  document.querySelectorAll('[data-opt="vrm.gaze"], [data-opt="vrm.combo_expr"]').forEach((el) => dim(el, !getOpt('vrm.morphs')));
  $('chooseOut').disabled = getOpt('output.mode') !== 'folder';
  // texture slimming needs Pillow on the Python side
  const pil = S.features.pillow !== false;
  document.querySelectorAll('[data-opt="vrm.tex_max"] button').forEach((el) => { el.disabled = !pil; });
  document.querySelector('[data-opt="vrm.tex_max"]').closest('.field').classList.toggle('dim', !pil);
  dim(document.querySelector('[data-opt="vrm.tex_jpeg"]'), !pil);
  $('texHint').textContent = t(pil ? 'texHint' : 'noPillow');
  $('texHint').classList.toggle('warn', !pil);
  renderUeRef();
  renderOutFolder();
}

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
function saveOpts() { clearTimeout(saveTimer); saveTimer = setTimeout(() => api('/api/options', { opts: S.opts }), 250); }

document.querySelectorAll('[data-opt]').forEach((el) => {
  if (el.classList.contains('seg')) {
    el.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      setOpt(el.dataset.opt, b.dataset.v); applyOptsToForm(); saveOpts();
    });
  } else {
    el.addEventListener('input', () => {
      setOpt(el.dataset.opt, el.type === 'checkbox' ? el.checked : el.type === 'text' ? el.value.trim() : +el.value);
      applyOptsToForm(); saveOpts();
    });
  }
});
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
