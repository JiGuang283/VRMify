// VRM preview: three.js + three-vrm (the same SpringBone / MToon implementation the QA harness uses).
// Loads ?model=<url> (served by gfx/web_preview.py) or a dropped / picked file.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const $ = (id) => document.getElementById(id);
const view = $('view');

// ---------------------------------------------------------------- scene --
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
view.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#8a8f98');
const camera = new THREE.PerspectiveCamera(30, 1, 0.02, 100);
camera.position.set(0, 1.2, 4);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 0.9, 0);
controls.enableDamping = true;

const ambient = new THREE.AmbientLight(0xffffff, 0.6);
const sun = new THREE.DirectionalLight(0xffffff, 1.4);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.bias = -0.0003;
sun.shadow.normalBias = 0.003;
scene.add(ambient, sun, sun.target);

const floor = new THREE.Mesh(new THREE.CircleGeometry(1.2, 48), new THREE.ShadowMaterial({ opacity: 0.25 }));
floor.rotation.x = -Math.PI / 2;
floor.receiveShadow = true;
scene.add(floor);

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / Math.max(1, h);
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize);
resize();

// --------------------------------------------------------------- state --
let vrm = null, gltfScene = null, modelName = '', modelUrl = '';
let motion = 'idle', t = 0, speed = 1, paused = false, springOn = true;
let springManager = null;
let height = 1.6, headY = 1.45, chestY = 1.2;

// ------------------------------------------------------------- loading --
const loader = new GLTFLoader();
loader.register((p) => new VRMLoaderPlugin(p));

function status(s) { $('status').textContent = s || ''; }
function toast(s) { const e = $('toast'); e.textContent = s; e.classList.add('show'); clearTimeout(toast._t); toast._t = setTimeout(() => e.classList.remove('show'), 2500); }

async function loadArrayBuffer(buf, name) {
  status('解析 ' + name + ' …');
  const gltf = await loader.parseAsync(buf, '');
  const v = gltf.userData.vrm;
  if (!v) throw new Error('不是 VRM 文件（没有 VRM / VRMC_vrm 扩展）');
  setModel(gltf, v, name);
}

async function loadUrl(url, name) {
  status('读取 ' + name + ' …');
  const r = await fetch(url);
  if (!r.ok) throw new Error('读取失败：HTTP ' + r.status);
  modelUrl = url;
  await loadArrayBuffer(await r.arrayBuffer(), name);
}

function setModel(gltf, v, name) {
  if (vrm) { scene.remove(vrm.scene); VRMUtils.deepDispose(vrm.scene); }
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  if (v.meta && v.meta.metaVersion === '0') VRMUtils.rotateVRM0(v);
  v.scene.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; o.frustumCulled = false; } });
  scene.add(v.scene);
  vrm = v; gltfScene = gltf.scene; modelName = name;
  springManager = vrm.springBoneManager || null;
  vrm.humanoid.resetNormalizedPose();
  vrm.update(0);
  const box = new THREE.Box3().setFromObject(vrm.scene);
  height = Math.max(0.3, box.max.y - box.min.y);
  const hp = new THREE.Vector3(), cp = new THREE.Vector3();
  const head = vrm.humanoid.getNormalizedBoneNode('head'), chest = vrm.humanoid.getNormalizedBoneNode('chest') || vrm.humanoid.getNormalizedBoneNode('spine');
  headY = head ? head.getWorldPosition(hp).y + 0.06 * height / 1.6 : height * 0.9;
  chestY = chest ? chest.getWorldPosition(cp).y : height * 0.7;
  fitShadow();
  t = 0;
  applyMotion(0);
  if (springManager) springManager.reset();
  frame(document.querySelector('[data-frame].on')?.dataset.frame || 'body');
  fillInfo(gltf, v);
  fillExpressions();
  $('title').textContent = (v.meta && (v.meta.name || v.meta.title)) || name;
  $('file').textContent = name;
  $('drop').textContent = '';
  document.title = 'VRM 预览 - ' + name;
  status('');
}

// ---------------------------------------------------------------- info --
function fillInfo(gltf, v) {
  const json = gltf.parser.json;
  let tris = 0;
  for (const m of json.meshes || []) for (const p of m.primitives) {
    const a = json.accessors[p.indices ?? p.attributes.POSITION];
    tris += (p.indices != null ? a.count : json.accessors[p.attributes.POSITION].count) / 3;
  }
  const sb = json.extensions?.VRMC_springBone;
  const sa = json.extensions?.VRM?.secondaryAnimation;
  const springs = sb ? sb.springs.length : (sa ? sa.boneGroups.length : 0);
  const joints = sb ? sb.springs.reduce((a, s) => a + s.joints.length, 0) : (sa ? sa.boneGroups.reduce((a, g) => a + g.bones.length, 0) : 0);
  const rows = [
    ['规范', v.meta?.metaVersion === '0' ? 'VRM 0.x' : 'VRM 1.0'],
    ['身高', height.toFixed(2) + ' m'],
    ['humanoid 骨', Object.keys(v.humanoid.humanBones).length],
    ['三角面', Math.round(tris).toLocaleString()],
    ['材质', (json.materials || []).length],
    ['贴图', (json.images || []).length],
    ['表情', v.expressionManager ? v.expressionManager.expressions.length : 0],
    ['SpringBone', springs ? springs + ' 条 / ' + joints + ' 关节' : '无'],
    ['碰撞体', sb ? (sb.colliders || []).length : (sa ? sa.colliderGroups.length + ' 组' : 0)],
  ];
  $('info').innerHTML = rows.map(([k, val]) => `<dt>${k}</dt><dd>${val}</dd>`).join('');
}

function fillExpressions() {
  const box = $('expr');
  box.innerHTML = '';
  const em = vrm.expressionManager;
  if (!em || !em.expressions.length) { box.innerHTML = '<div class="sub">没有表情</div>'; return; }
  const presets = new Set(Object.values(em.presetExpressionMap || {}).map((e) => e.expressionName));
  const list = [...em.expressions].sort((a, b) => (presets.has(b.expressionName) - presets.has(a.expressionName)));
  for (const e of list) {
    const row = document.createElement('div');
    row.className = 'row'; row.dataset.name = e.expressionName.toLowerCase();
    const lab = document.createElement('label'); lab.textContent = e.expressionName; lab.title = e.expressionName;
    const r = document.createElement('input'); r.type = 'range'; r.min = 0; r.max = 1; r.step = 0.01; r.value = 0;
    r.addEventListener('input', () => em.setValue(e.expressionName, +r.value));
    row.append(lab, r); box.append(row);
  }
  filterExpressions();
}
function filterExpressions() {
  const q = $('exprFilter').value.trim().toLowerCase();
  for (const row of $('expr').children) if (row.dataset.name !== undefined) row.style.display = !q || row.dataset.name.includes(q) ? '' : 'none';
}
$('exprFilter').addEventListener('input', filterExpressions);
$('exprReset').addEventListener('click', () => {
  if (!vrm?.expressionManager) return;
  vrm.expressionManager.resetValues();
  for (const r of $('expr').querySelectorAll('input')) r.value = 0;
});

// -------------------------------------------------------------- camera --
function frame(kind) {
  const H = height;
  const presets = {
    body: [new THREE.Vector3(0, H * 0.55, H * 2.4), new THREE.Vector3(0, H * 0.52, 0)],
    upper: [new THREE.Vector3(0, chestY + 0.05, H * 1.1), new THREE.Vector3(0, chestY + 0.03, 0)],
    face: [new THREE.Vector3(0, headY, H * 0.42), new THREE.Vector3(0, headY - 0.01, 0)],
    back: [new THREE.Vector3(0, H * 0.6, -H * 2.2), new THREE.Vector3(0, H * 0.55, 0)],
  };
  const [pos, tgt] = presets[kind];
  camera.position.copy(pos); controls.target.copy(tgt); controls.update();
  document.querySelectorAll('[data-frame]').forEach((b) => b.classList.toggle('on', b.dataset.frame === kind));
}
document.querySelectorAll('[data-frame]').forEach((b) => b.addEventListener('click', () => frame(b.dataset.frame)));

// -------------------------------------------------------------- lights --
function fitShadow() {
  const c = sun.shadow.camera, r = height * 0.75;
  c.left = -r; c.right = r; c.top = r; c.bottom = -r; c.near = 0.1; c.far = height * 6;
  c.updateProjectionMatrix();
  floor.scale.setScalar(height / 1.6);
  updateLight();
}
function updateLight() {
  const az = THREE.MathUtils.degToRad(+$('lightAz').value), el = THREE.MathUtils.degToRad(+$('lightEl').value);
  const d = height * 2.5, cy = height * 0.55;
  sun.position.set(Math.sin(az) * Math.cos(el) * d, cy + Math.sin(el) * d, Math.cos(az) * Math.cos(el) * d);
  sun.target.position.set(0, cy, 0);
  sun.intensity = +$('lightI').value;
  ambient.intensity = +$('ambI').value;
  sun.castShadow = $('shadow').checked;
  $('lightAzV').textContent = $('lightAz').value + '°';
  $('lightElV').textContent = $('lightEl').value + '°';
  $('lightIV').textContent = (+$('lightI').value).toFixed(1);
  $('ambIV').textContent = (+$('ambI').value).toFixed(2).replace(/0$/, '');
}
['lightAz', 'lightEl', 'lightI', 'ambI', 'shadow'].forEach((id) => $(id).addEventListener('input', updateLight));
document.querySelectorAll('[data-bg]').forEach((b) => b.addEventListener('click', () => {
  scene.background = new THREE.Color(b.dataset.bg);
  document.querySelectorAll('[data-bg]').forEach((x) => x.classList.toggle('on', x === b));
}));
updateLight();

// ------------------------------------------------------------- motions --
// Same pose model as tools/qa/harness/motions.mjs (normalized humanoid bones, radians, 60 fps).
const smooth = (x) => x * x * (3 - 2 * x);
function gait(f, freq, thigh, knee, bob, armSwing) {
  const ph = f / 60 * Math.PI * 2 * freq, s = Math.sin(ph);
  return { bob: -bob * Math.cos(2 * ph), yaw: 0.08 * s, roll: 0.04 * Math.sin(2 * ph), spineYaw: -0.1 * s,
    lLeg: -thigh * s, rLeg: thigh * s,
    lKnee: 0.1 + knee * Math.max(0, Math.sin(ph + 0.9)), rKnee: 0.1 + knee * Math.max(0, Math.sin(ph + 0.9 + Math.PI)),
    armDown: 0.7, armSwingL: armSwing * s, armSwingR: -armSwing * s };
}
const MOTIONS = {
  tpose: () => ({}),
  idle: (f) => ({ armDown: 0.7, yaw: Math.sin(f / 60 * Math.PI * 2 * 0.25) * 0.05, bob: Math.sin(f / 60 * Math.PI * 2 * 0.3) * 0.004 }),
  walk: (f) => gait(f, 0.9, 0.45, 0.9, 0.025, 0.35),
  run: (f) => gait(f, 1.4, 0.8, 1.6, 0.05, 0.7),
  legRaise: (f) => { const c = f % 240, k = c < 40 ? smooth(c / 40) : c < 100 ? 1 : c < 140 ? smooth(1 - (c - 100) / 40) : 0;
    return { armDown: 0.7, lLeg: -1.2 * k, lKnee: 1.0 * k }; },
  turn: (f) => ({ armDown: 0.7, yaw: Math.sin(f / 60 * Math.PI * 2 * 0.6) * 0.8 }),
};
function applyMotion(f) {
  if (!vrm) return;
  const p = MOTIONS[motion](f), h = vrm.humanoid, n = (b) => h.getNormalizedBoneNode(b);
  const set = (b, x, y, z, order) => { const o = n(b); if (o) o.rotation.set(x, y, z, order || 'XYZ'); };
  const k = height / 1.6;
  set('hips', 0, p.yaw || 0, p.roll || 0);
  set('spine', 0, p.spineYaw || 0, 0);
  vrm.scene.position.y = (p.bob || 0) * k;
  set('leftUpperLeg', p.lLeg || 0, 0, 0); set('rightUpperLeg', p.rLeg || 0, 0, 0);
  set('leftLowerLeg', p.lKnee || 0, 0, 0); set('rightLowerLeg', p.rKnee || 0, 0, 0);
  const a = 1.22 * Math.min(1, (p.armDown || 0) / 0.7);
  set('leftUpperArm', p.armSwingL || 0, 0, -a); set('rightUpperArm', p.armSwingR || 0, 0, a);
  set('leftLowerArm', 0, p.armSwingL ? -0.3 : 0, 0); set('rightLowerArm', 0, p.armSwingR ? 0.3 : 0, 0);
}
document.querySelectorAll('[data-motion]').forEach((b) => b.addEventListener('click', () => {
  motion = b.dataset.motion; t = 0;
  document.querySelectorAll('[data-motion]').forEach((x) => x.classList.toggle('on', x === b));
}));
$('speed').addEventListener('input', () => { speed = +$('speed').value; $('speedV').textContent = speed + '×'; });
$('pause').addEventListener('change', () => { paused = $('pause').checked; });
$('spring').addEventListener('change', () => {
  springOn = $('spring').checked;
  if (!vrm) return;
  vrm.springBoneManager = springOn ? springManager : undefined;
  if (springOn && springManager) springManager.reset();
});
$('resetPhys').addEventListener('click', () => { if (springManager) springManager.reset(); });

// ----------------------------------------------------------- file input --
async function openFile(file) {
  try { await loadArrayBuffer(await file.arrayBuffer(), file.name); modelUrl = ''; }
  catch (e) { status(''); toast('打开失败：' + e.message); console.error(e); }
}
$('open').addEventListener('click', () => $('picker').click());
$('picker').addEventListener('change', () => { const f = $('picker').files[0]; if (f) openFile(f); });
view.addEventListener('dragover', (e) => { e.preventDefault(); $('drop').classList.add('over'); });
view.addEventListener('dragleave', () => $('drop').classList.remove('over'));
view.addEventListener('drop', (e) => {
  e.preventDefault(); $('drop').classList.remove('over');
  const f = [...e.dataTransfer.files].find((x) => /\.(vrm|glb)$/i.test(x.name));
  if (f) openFile(f); else toast('只能打开 .vrm 文件');
});

// ------------------------------------------------------------ snapshot --
$('shot').addEventListener('click', async () => {
  const url = renderer.domElement.toDataURL('image/png');
  const base = (modelName || 'vrm').replace(/\.[^.]+$/, '');
  if (location.protocol.startsWith('http')) {
    try {
      const r = await fetch('api/snapshot?name=' + encodeURIComponent(base), { method: 'POST', body: url });
      if (r.ok) { toast('已保存：' + (await r.text())); return; }
    } catch (e) { /* fall through to download */ }
  }
  const a = document.createElement('a'); a.href = url; a.download = base + '_preview.png'; a.click();
});

// --------------------------------------------------------------- loop --
const clock = new THREE.Clock();
let acc = 0;
function tick() {
  requestAnimationFrame(tick);
  const dt = Math.min(0.1, clock.getDelta());
  if (vrm && !paused) {
    // motion advances on a 60 Hz grid (like the QA harness); physics steps with real time
    acc += dt * speed * 60;
    const steps = Math.floor(acc); acc -= steps;
    t += steps;
    applyMotion(t);
    vrm.update(dt * speed);
  }
  controls.update();
  renderer.render(scene, camera);
}
tick();

// ------------------------------------------------------------- startup --
const q = new URLSearchParams(location.search);
if (q.get('model')) {
  loadUrl(q.get('model'), q.get('name') || q.get('model').split('/').pop())
    .catch((e) => { status(''); toast('打开失败：' + e.message); console.error(e); });
}
// keep-alive for gfx/web_preview.py in browser mode (it exits a minute after the tab is closed)
if (location.protocol.startsWith('http')) setInterval(() => fetch('api/ping').catch(() => {}), 10000);
window.__preview = { get vrm() { return vrm; }, frame, setMotion: (m) => document.querySelector(`[data-motion="${m}"]`)?.click() };
