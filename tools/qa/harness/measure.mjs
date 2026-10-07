// SpringBone behaviour of one VRM, headless (three.js + three-vrm, no rendering).
//   node measure.mjs model.vrm            → one JSON object on stdout
// env: MOTIONS=idle,walk_real,run   FPS=60   JITTER=0 (frame-time jitter ratio)   DETAIL=3
//
// rest:     after 2 s standing still, how far each spring tip has moved from the bind pose
//           (a correct setup stays put; large values = joints fight colliders / wrong rest dir)
// motions:  per chain category (hair = under head/neck, skirt = under hips/legs,
//           torso = under spine/chest/shoulders, other = arms etc.)
//   hf      tip energy above 8 Hz / chain length ×1000 — genuine shake (gait content < 7 Hz);
//           mean and max over chains. Native VRoid/sample models: hair ≈ 2–16, skirt ≈ 3–58.
//   swing   peak tip deformation / chain length (anchor-local)
//   skirtInside  share of skirt joint samples inside the model's own leg colliders
//           (counts rest overlap too — compare against idle)
import * as THREE from 'three';
import fs from 'fs';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { MOTIONS, FRAMES } from './motions.mjs';

// Textures and MToon are irrelevant here and slow to decode in Node: strip them.
function stripGlb(buf) {
  const jl = buf.readUInt32LE(12);
  const json = JSON.parse(buf.slice(20, 20 + jl).toString('utf8'));
  delete json.images; delete json.textures; delete json.samplers;
  for (const m of json.materials || []) {
    if (m.pbrMetallicRoughness) delete m.pbrMetallicRoughness.baseColorTexture;
    delete m.extensions; delete m.normalTexture; delete m.emissiveTexture; delete m.occlusionTexture;
  }
  json.extensionsUsed = (json.extensionsUsed || []).filter((e) => e !== 'VRMC_materials_mtoon' && e !== 'KHR_texture_transform');
  if (json.extensions && json.extensions.VRM) json.extensions.VRM.materialProperties = [];
  let js = Buffer.from(JSON.stringify(json));
  js = Buffer.concat([js, Buffer.alloc((4 - js.length % 4) % 4, 0x20)]);
  const bin = buf.slice(20 + jl);
  const h = Buffer.alloc(20);
  h.writeUInt32LE(0x46546c67, 0); h.writeUInt32LE(2, 4); h.writeUInt32LE(20 + js.length + bin.length, 8);
  h.writeUInt32LE(js.length, 12); h.writeUInt32LE(0x4e4f534a, 16);
  const o = Buffer.concat([h, js, bin]);
  return o.buffer.slice(o.byteOffset, o.byteOffset + o.byteLength);
}

async function load(path) {
  const loader = new GLTFLoader();
  loader.register((p) => new VRMLoaderPlugin(p));
  const warn = console.warn; console.warn = () => {};   // material-bind warnings: materials were stripped
  try {
    const vrm = await new Promise((r, j) => loader.parse(stripGlb(fs.readFileSync(path)), '', (g) => r(g.userData.vrm), j));
    if (vrm.meta && vrm.meta.metaVersion === '0') VRMUtils.rotateVRM0(vrm);
    new THREE.Scene().add(vrm.scene);
    vrm.update(0); vrm.scene.updateMatrixWorld(true);
    return vrm;
  } finally { console.warn = warn; }
}

const wp = (o) => new THREE.Vector3().setFromMatrixPosition(o.matrixWorld);
const LEG = ['leftUpperLeg', 'rightUpperLeg', 'leftLowerLeg', 'rightLowerLeg'];

async function restDrift(path) {
  const vrm = await load(path);
  const joints = vrm.springBoneManager ? [...vrm.springBoneManager.joints] : [];
  if (!joints.length) return null;
  const pos = () => joints.map((j) => wp(j.child || j.bone));
  const p0 = pos();
  for (let i = 0; i < 120; i++) vrm.update(1 / 60);
  vrm.scene.updateMatrixWorld(true);
  const p1 = pos();
  const d = joints.map((j, i) => [p1[i].distanceTo(p0[i]), j.bone.name]).sort((a, b) => b[0] - a[0]);
  return { max: d[0][0], maxName: d[0][1], over1cm: d.filter((x) => x[0] > 0.01).length, joints: d.length,
    top: d.slice(0, 5).map(([v, n]) => [n, +v.toFixed(4)]) };
}

function chainsOf(vrm) {
  const h = vrm.humanoid, sbm = vrm.springBoneManager;
  const roleOf = new Map(Object.keys(h.humanBones).map((r) => [h.getRawBoneNode(r), r]));
  const joints = [...sbm.joints];
  const jbones = new Set(joints.map((j) => j.bone));
  const chains = [];
  for (const j of joints) {
    let p = j.bone.parent;
    while (p && !jbones.has(p) && !roleOf.has(p)) p = p.parent;
    if (p && jbones.has(p)) continue;                    // not a chain root
    const path = [j];
    for (let cur = j; ;) {
      const nx = joints.find((k) => { let q = k.bone.parent; while (q && q !== cur.bone && !jbones.has(q)) q = q.parent; return q === cur.bone; });
      if (!nx) break;
      path.push(nx); cur = nx;
    }
    const tip = path[path.length - 1].child || path[path.length - 1].bone;
    let anc = j.bone.parent;
    while (anc && !roleOf.has(anc)) anc = anc.parent;
    const role = anc ? roleOf.get(anc) : '?';
    const cat = ['head', 'neck', 'jaw'].includes(role) ? 'hair'
      : (role === 'hips' || /Leg|Foot|Toes/.test(role)) ? 'skirt'
        : ['spine', 'chest', 'upperChest', 'leftShoulder', 'rightShoulder'].includes(role) ? 'torso' : 'other';
    let L = 0, prev = wp(j.bone);
    for (const k of path.slice(1)) { const q = wp(k.bone); L += q.distanceTo(prev); prev = q; }
    L += wp(tip).distanceTo(prev);
    chains.push({ root: j, tip, anchor: anc, cat, joints: path, L: Math.max(L, 0.02) });
  }
  return { chains, roleOf };
}

async function measure(path, mname) {
  const vrm = await load(path);
  const h = vrm.humanoid, sbm = vrm.springBoneManager;
  const { chains, roleOf } = chainsOf(vrm);
  const loc = (c) => (c.anchor ? c.anchor.worldToLocal(wp(c.tip)) : wp(c.tip));
  const legCols = [];
  for (const g of sbm.colliderGroups) for (const col of g.colliders) {
    let p = col.parent; while (p && !roleOf.has(p)) p = p.parent;
    if (p && LEG.includes(roleOf.get(p)) && !legCols.includes(col)) legCols.push(col);
  }
  const node = (n) => h.getNormalizedBoneNode(n);
  for (let i = 0; i < 60; i++) vrm.update(1 / 60);       // settle before measuring
  vrm.scene.updateMatrixWorld(true);
  const rest = chains.map(loc);
  const traj = chains.map(() => []);
  let inside = 0, skirtTips = 0;
  const N = FRAMES[mname];
  const fps = +(process.env.FPS || 60), jit = +(process.env.JITTER || 0);
  let acc60 = 0, seed = 12345, nextDt = 1 / fps;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  for (let f = 0; f < N + 120; f++) {
    const p = f < N ? MOTIONS[mname](f) : (mname === 'idle' ? MOTIONS[mname](N - 1) : {});
    node('hips').rotation.set(0, p.yaw || 0, p.roll || 0);
    if (p.spineYaw != null) node('spine').rotation.set(0, p.spineYaw, 0);
    vrm.scene.position.set(0, p.bob || 0, p.rootZ || 0);
    node('leftUpperLeg').rotation.set(p.lLeg || 0, 0, 0); node('rightUpperLeg').rotation.set(p.rLeg || 0, 0, 0);
    node('leftLowerLeg').rotation.set(p.lKnee || 0, 0, 0); node('rightLowerLeg').rotation.set(p.rKnee || 0, 0, 0);
    if (p.armDown != null) {
      const a = 1.22 * Math.min(1, p.armDown / 0.7);
      node('leftUpperArm').rotation.set(p.armSwingL || 0, 0, -a, 'XYZ'); node('rightUpperArm').rotation.set(p.armSwingR || 0, 0, a, 'XYZ');
      node('leftLowerArm').rotation.set(0, p.armSwingL ? -0.3 : 0, 0); node('rightLowerArm').rotation.set(0, p.armSwingR ? 0.3 : 0, 0);
    }
    // physics at FPS (optionally jittered), motion sampled at 60 Hz
    acc60 += 1 / 60;
    while (acc60 >= nextDt - 1e-9) { vrm.update(nextDt); acc60 -= nextDt; nextDt = (1 / fps) * (1 + jit * (rnd() * 2 - 1)); }
    vrm.scene.updateMatrixWorld(true);
    chains.forEach((c, i) => traj[i].push(loc(c).sub(rest[i])));
    if (legCols.length) for (const c of chains) if (c.cat === 'skirt') {
      for (const j of c.joints) {
        const t = wp(j.child || j.bone); skirtTips++;
        for (const col of legCols) { if (col.shape.calculateCollision(col.colliderMatrix, t, 0, new THREE.Vector3()) < -0.005) { inside++; break; } }
      }
    }
  }
  const W0 = (mname === 'walk_real' || mname === 'run') ? 60 : 0;   // skip the start-up transient
  const hfOf = (i) => {                                             // spectral energy above 8 Hz
    const T = traj[i], hi = Math.min(N, T.length), n = hi - W0;
    if (n < 32) return 0;
    let e = 0;
    for (const ax of ['x', 'y', 'z']) {
      const xs = []; for (let t = W0; t < hi; t++) xs.push(T[t][ax]);
      const mean = xs.reduce((a, b) => a + b, 0) / n;
      for (let k = Math.ceil(8 * n / 60); k <= n / 2; k++) {
        let re = 0, im = 0;
        for (let t = 0; t < n; t++) { const a = 2 * Math.PI * k * t / n; re += (xs[t] - mean) * Math.cos(a); im -= (xs[t] - mean) * Math.sin(a); }
        e += (re * re + im * im) * 2 / (n * n);
      }
    }
    return Math.sqrt(e) / chains[i].L * 1000;
  };
  const hf = chains.map((c, i) => hfOf(i));
  const swingOf = (i) => Math.max(0, ...traj[i].slice(W0, N).map((v) => v.length())) / chains[i].L;
  const out = { cats: {} };
  for (const cat of ['hair', 'skirt', 'torso', 'other']) {
    const ids = chains.map((c, i) => (c.cat === cat ? i : -1)).filter((i) => i >= 0);
    if (!ids.length) continue;
    out.cats[cat] = { n: ids.length, hf: ids.reduce((a, i) => a + hf[i], 0) / ids.length,
      hfMax: Math.max(...ids.map((i) => hf[i])), swing: Math.max(...ids.map(swingOf)) };
  }
  out.skirtInside = skirtTips ? inside / skirtTips : null;
  const nd = +(process.env.DETAIL || 3);
  out.worst = chains.map((c, i) => [hf[i], i]).sort((a, b) => b[0] - a[0]).slice(0, nd)
    .map(([v, i]) => ({ cat: chains[i].cat, chain: chains[i].root.bone.name, hf: +v.toFixed(2), swing: +swingOf(i).toFixed(3),
      joints: chains[i].joints.length, length: +chains[i].L.toFixed(3) }));
  return out;
}

const path = process.argv[2];
if (!path) { console.error('usage: node measure.mjs model.vrm'); process.exit(2); }
const result = { file: path, rest: await restDrift(path), motions: {} };
if (result.rest) {
  for (const m of (process.env.MOTIONS || 'idle,walk_real,run').split(',')) result.motions[m] = await measure(path, m);
}
process.stdout.write(JSON.stringify(result) + '\n');
