// Drag handles to resize the left column and the side column (panels). Widths are saved in settings.json
// (the pywebview window doesn't keep browser storage between launches); double-click a handle to reset.
import { $, S, api } from './core.js';

const root = document.documentElement.style;
const VARS = { left: '--left-w', side: '--side-w', sideWide: '--side-wide-w' };
const MIN_PREVIEW = 240;                      // the preview never gets narrower than this while dragging

export function applyLayout() {
  for (const [k, v] of Object.entries(VARS)) {
    if (S.ui[k]) root.setProperty(v, S.ui[k] + 'px'); else root.removeProperty(v);
  }
}

const sideKey = () => ($('side').classList.contains('wide') ? 'sideWide' : 'side');
const width = (el) => el.getBoundingClientRect().width;

function drag(handle, key, measure) {
  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    handle.classList.add('dragging'); document.body.classList.add('resizing');
    const k = key(), x0 = e.clientX, w0 = measure.width(), room = measure.room();
    const move = (ev) => {
      const w = Math.round(Math.max(measure.min, Math.min(w0 + (ev.clientX - x0) * measure.dir, w0 + room - MIN_PREVIEW)));
      S.ui[k] = w; root.setProperty(VARS[k], w + 'px');
    };
    const up = () => {
      handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', up);
      handle.classList.remove('dragging'); document.body.classList.remove('resizing');
      api('/api/options', { ui: { [k]: S.ui[k] } });
    };
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', up);
  });
  handle.addEventListener('dblclick', () => {
    const k = key();
    delete S.ui[k]; root.removeProperty(VARS[k]);
    api('/api/options', { ui: { [k]: null } });
  });
}

// left column: dragging right widens it; the side column: dragging left widens it
drag($('splitLeft'), () => 'left', { dir: 1, min: 300, width: () => width(document.querySelector('.left')), room: () => width($('pv')) });
drag($('splitSide'), sideKey, { dir: -1, min: 220, width: () => width($('side')), room: () => width($('pv')) });
