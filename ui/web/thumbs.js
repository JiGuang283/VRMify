// Automatic VRM thumbnails: when a file finishes converting, its VRM is loaded into a hidden viewer, framed on the
// face and saved as the thumbnail (the same as the Tune panel's "use current view"). Option vrm.auto_thumb;
// the server keeps a thumbnail the user set by hand (see /api/tune/thumb auto).
import { Viewer } from '/viewer/viewer-core.js';
import { api } from './core.js';
import { getOpt } from './options.js';
import { viewer } from './preview.js';

let tv = null, running = false;
const queue = [];
export let onThumb = null;                      // (file id) → called after a thumbnail was written
export function setOnThumb(fn) { onThumb = fn; }

function thumbViewer() {
  if (!tv) {
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;left:-20000px;top:0;width:1024px;height:1024px;pointer-events:none';
    box.setAttribute('aria-hidden', 'true');
    document.body.append(box);
    tv = new Viewer(box, { loop: false, pixelRatio: 1 });
  }
  return tv;
}

// a file entry that just went from running to done. Files with several VRMs (an archive with more than one
// model) are skipped: the thumbnail is per source file and would end up on every model
export function queueThumb(entry) {
  if (!getOpt('vrm.auto_thumb')) return;
  const vrms = (entry.outputs || []).filter((o) => o.url);
  if (vrms.length !== 1 || queue.some((q) => q.id === entry.id)) return;
  queue.push({ id: entry.id, url: vrms[0].url, name: vrms[0].name });
  if (!running) run();
}

async function run() {
  running = true;
  while (queue.length) {
    const job = queue.shift();
    try {
      const v = thumbViewer();
      v.setBackground(viewer.scene.background);
      v.setLight({ ...viewer.light });
      await v.loadUrl(job.url, job.name);
      if (v.vrm) v.vrm.update(0);
      v.frame('face');
      const data = v.snapshotSquare(1024);
      v.clear();
      const r = await api('/api/tune/thumb', { id: job.id, data, auto: true });
      if (r.ok && onThumb) onThumb(job.id);
    } catch (e) { console.warn('auto thumbnail failed', job.name, e); }
  }
  running = false;
}
