// Reusable VRM / PMX viewer: three.js + three-vrm (the same SpringBone / MToon implementation as UniVRM
// and the QA harness); PMX natively through three's MMDLoader (MMD toon shading, append bones, MMD
// physics via ammo.js). No UI of its own — pages wire their controls to the Viewer API.
//
//   const v = new Viewer(containerElement);
//   await v.loadUrl(url, name)  |  await v.loadBuffer(arrayBuffer, name)   (*.pmx URLs load as PMX)
//   v.setMotion('walk'); v.setLight({ az: 35, el: 40, intensity: 1.4, ambient: 0.6, shadow: true });
//   v.frame('face'); v.setExpression('happy', 1); v.snapshot() → PNG data URL
//   v.setSpringParams([{ node, stiffness, gravityPower, dragForce, enabled }])  live SpringBone tuning
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { MMDLoader } from 'three/addons/loaders/MMDLoader.js';
import { MMDAnimationHelper } from 'three/addons/animation/MMDAnimationHelper.js';

// ammo.js (Bullet physics, WebAssembly) for MMD physics: a classic script that defines a global factory,
// loaded on the first PMX. MMDPhysics reads the global `Ammo`.
let ammoReady = null;
function loadAmmo() {
  if (!ammoReady) {
    ammoReady = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = new URL('./vendor/three/addons/libs/ammo.wasm.js', import.meta.url).href;
      s.onload = () => window.Ammo().then((lib) => { window.Ammo = lib; resolve(); }, reject);
      s.onerror = () => reject(new Error('ammo.js failed to load'));
      document.head.append(s);
    });
  }
  return ammoReady;
}

// VRM humanoid motion targets → standard MMD bone names (the motions are written for normalized VRM bones,
// which like MMD bones have no rest rotation, face +Z and have the character's left at +X)
const MMD_BONES = {
  hips: ['センター', '下半身'], spine: ['上半身'], leftUpperLeg: ['左足'], rightUpperLeg: ['右足'],
  leftLowerLeg: ['左ひざ'], rightLowerLeg: ['右ひざ'], leftUpperArm: ['左腕'], rightUpperArm: ['右腕'],
  leftLowerArm: ['左ひじ'], rightLowerArm: ['右ひじ'], head: ['頭'], chest: ['上半身2', '上半身'],
};

// standard MMD bones: the PMX counterpart of humanoid bones in the skeleton overlay
const MMD_STANDARD = new Set(['センター', '下半身', '上半身', '上半身2', '上半身3', '首', '頭', '両目', '左目', '右目',
  ...['肩', '腕', 'ひじ', '手首', '足', 'ひざ', '足首', 'つま先', '足先EX',
    '親指０', '親指１', '親指２', '人指１', '人指２', '人指３', '中指１', '中指２', '中指３',
    '薬指１', '薬指２', '薬指３', '小指１', '小指２', '小指３'].flatMap((n) => ['左' + n, '右' + n])]);

// skeleton overlay colours by bone role (see Viewer.bones)
export const BONE_COLORS = { humanoid: '#ffb020', weighted: '#4fc3f7', spring: '#ff6fb5', control: '#b0b8c4', unused: '#ff4040' };

// English bone names (PMX converted from FBX / psk / xps keeps them) — the same rules as
// convert/vrmconv/humanoid.py _en_bone, reduced to the bones the preview motions drive.
const EN_LIMB = { arm: 'UpperArm', upperarm: 'UpperArm', shoulder: 'UpperArm', forearm: 'LowerArm', lowerarm: 'LowerArm',
  elbow: 'LowerArm', upleg: 'UpperLeg', upperleg: 'UpperLeg', thigh: 'UpperLeg', hip: 'UpperLeg',
  leg: 'LowerLeg', lowerleg: 'LowerLeg', calf: 'LowerLeg', shin: 'LowerLeg', knee: 'LowerLeg' };
const EN_PREFIX = /^(?:character\d*|chr|bip0*1|jbipc|jbip|j|def|org|mch|armature|skeleton|rig|cc_base|valvebiped_bip01)(?=[a-z])/;
function enBone(name) {
  const s = (name || '').split(':').pop().toLowerCase();
  const m = s.replace(/[. ]/g, '_').match(/(?:^|[^a-z])(l|r)(?:[^a-z]|$)|left|right/);
  const side = m ? ((m[1] || m[0]).replace(/[_. ]/g, '').startsWith('l') ? 'left' : 'right') : null;
  let base = s.replace(/left|right/g, '').replace(/(?:^|[_. ])[lr](?=[_. ]|$)/g, '_').replace(/[^a-z0-9]/g, '');
  base = base.replace(EN_PREFIX, '');
  if (!side) return { hips: 'hips', hip: 'hips', pelvis: 'hips', spine: 'spine', spine0: 'spine', spine1: 'chest',
    chest: 'chest', head: 'head' }[base] || null;
  return EN_LIMB[base] ? side + EN_LIMB[base] : null;
}

// Same pose model as tools/qa/harness/motions.mjs (normalized humanoid bones, radians, 60 fps).
const smooth = (x) => x * x * (3 - 2 * x);
function gait(f, freq, thigh, knee, bob, armSwing) {
  const ph = f / 60 * Math.PI * 2 * freq, s = Math.sin(ph);
  return { bob: -bob * Math.cos(2 * ph), yaw: 0.08 * s, roll: 0.04 * Math.sin(2 * ph), spineYaw: -0.1 * s,
    lLeg: -thigh * s, rLeg: thigh * s,
    lKnee: 0.1 + knee * Math.max(0, Math.sin(ph + 0.9)), rKnee: 0.1 + knee * Math.max(0, Math.sin(ph + 0.9 + Math.PI)),
    armDown: 0.7, armSwingL: armSwing * s, armSwingR: -armSwing * s };
}
export const MOTIONS = {
  tpose: () => ({}),
  idle: (f) => ({ armDown: 0.7, yaw: Math.sin(f / 60 * Math.PI * 2 * 0.25) * 0.05, bob: Math.sin(f / 60 * Math.PI * 2 * 0.3) * 0.004 }),
  walk: (f) => gait(f, 0.9, 0.45, 0.9, 0.025, 0.35),
  run: (f) => gait(f, 1.4, 0.8, 1.6, 0.05, 0.7),
  legRaise: (f) => { const c = f % 240, k = c < 40 ? smooth(c / 40) : c < 100 ? 1 : c < 140 ? smooth(1 - (c - 100) / 40) : 0;
    return { armDown: 0.7, lLeg: -1.2 * k, lKnee: 1.0 * k }; },
  turn: (f) => ({ armDown: 0.7, yaw: Math.sin(f / 60 * Math.PI * 2 * 0.6) * 0.8 }),
};

export class Viewer {
  // loop: false → no render loop of its own (offscreen use: load, frame, snapshot); pixelRatio overrides the screen's
  constructor(container, { background = '#8a8f98', loop = true, pixelRatio = null } = {}) {
    this.container = container;
    this.vrm = null;
    this.mmd = null;                 // { mesh, helper, grant, bones, rest, armRest }
    this.name = '';
    this.info = null;
    this.height = 1.6; this.headY = 1.45; this.chestY = 1.2;
    this.motion = 'idle'; this.t = 0; this.acc = 0; this.speed = 1; this.paused = false;
    this.springOn = true; this.springManager = null;
    this.light = { az: 35, el: 40, intensity: 1.4, ambient: 0.6, shadow: true };
    this.onLoad = null;

    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    r.setPixelRatio(pixelRatio || Math.min(2, window.devicePixelRatio || 1));
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.domElement.style.display = 'block';
    r.domElement.style.width = '100%';
    r.domElement.style.height = '100%';
    container.prepend(r.domElement);

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(background);
    this.camera = new THREE.PerspectiveCamera(30, 1, 0.02, 100);
    this.camera.position.set(0, 1.2, 4);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.target.set(0, 0.9, 0);
    this.controls.enableDamping = true;
    this.controls.zoomToCursor = true;            // the wheel zooms toward the point under the pointer
    this.controls.minDistance = 0.03;
    // double-click: that point of the model (or the bone under the pointer) becomes the orbit centre;
    // on the empty background it goes back to the last framing (resetView)
    r.domElement.addEventListener('dblclick', (e) => {
      const p = this.pointAt(e.clientX, e.clientY);
      if (p) this.focusOn(p);
      else this.resetView();
    });

    this.ambient = new THREE.AmbientLight(0xffffff, 0.6);
    this.sun = new THREE.DirectionalLight(0xffffff, 1.4);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0003;
    this.sun.shadow.normalBias = 0.003;
    this.scene.add(this.ambient, this.sun, this.sun.target);
    this.floor = new THREE.Mesh(new THREE.CircleGeometry(1.2, 48), new THREE.ShadowMaterial({ opacity: 0.25 }));
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.receiveShadow = true;
    this.scene.add(this.floor);

    this.loader = new GLTFLoader();
    this.loader.register((p) => new VRMLoaderPlugin(p));

    new ResizeObserver(() => this.resize()).observe(container);
    this.resize();
    this._fitShadow();
    this.clock = new THREE.Clock();
    const tick = () => { requestAnimationFrame(tick); this._tick(); };
    if (loop) tick();
  }

  resize() {
    const w = Math.max(1, this.container.clientWidth), h = Math.max(1, this.container.clientHeight);
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  get loaded() { return !!(this.vrm || this.mmd); }
  get kind() { return this.vrm ? 'vrm' : this.mmd ? 'pmx' : null; }

  // ------------------------------------------------------------ loading --
  async loadUrl(url, name) {
    if (/\.pmx$/i.test(name || '') || /\.pmx(\?|$)/i.test(url)) return this.loadPmx(url, name);
    const r = await fetch(url);
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return this.loadBuffer(await r.arrayBuffer(), name || url.split('/').pop());
  }

  async loadBuffer(buf, name) {
    const gltf = await this.loader.parseAsync(buf, '');
    const v = gltf.userData.vrm;
    if (!v) throw new Error('not a VRM file');
    this._setModel(gltf, v, name);
    return this.info;
  }

  clear() {
    if (this.vrm) { this.scene.remove(this.vrm.scene); VRMUtils.deepDispose(this.vrm.scene); }
    if (this.mmd) {
      const { mesh, helper } = this.mmd;
      this.scene.remove(mesh);
      try { helper.remove(mesh); } catch (e) { /* physics already gone */ }
      mesh.geometry.dispose();
      for (const m of [].concat(mesh.material)) {
        for (const k of ['map', 'matcap', 'gradientMap']) if (m[k]) m[k].dispose();
        m.dispose();
      }
    }
    this.vrm = null; this.mmd = null; this.info = null; this.springManager = null; this.name = ''; this.gltf = null;
    this._jointsByNode = null; this._removedJoints = new Set();
    if (this._boneGfx) this.setBonesVisible(false, true);
    this.highlightChain(null);
    this._byNode = null;
    this._boneList = null; this._bonePos = null; this.boneHover = -1;
  }

  _setModel(gltf, v, name) {
    this.clear();
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    if (v.meta && v.meta.metaVersion === '0') VRMUtils.rotateVRM0(v);
    v.scene.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; o.frustumCulled = false; } });
    this.scene.add(v.scene);
    this.vrm = v; this.name = name; this.gltf = gltf;
    this.springManager = v.springBoneManager || null;
    if (!this.springOn) v.springBoneManager = undefined;
    v.humanoid.resetNormalizedPose();
    v.update(0);
    const box = new THREE.Box3().setFromObject(v.scene);
    this.height = Math.max(0.3, box.max.y - box.min.y);
    // 道具之类又宽又扁的模型：取景距离按最大边长算，不然镜头会怼到模型里
    const sz = box.getSize(new THREE.Vector3());
    this.extent = Math.max(this.height, sz.x, sz.z);
    this.centerY = (box.min.y + box.max.y) / 2;
    const head = v.humanoid.getNormalizedBoneNode('head');
    const chest = v.humanoid.getNormalizedBoneNode('chest') || v.humanoid.getNormalizedBoneNode('spine');
    const p = new THREE.Vector3();
    this.headY = head ? head.getWorldPosition(p).y + 0.06 * this.height / 1.6 : this.height * 0.9;
    this.chestY = chest ? chest.getWorldPosition(p).y : this.height * 0.7;
    this._fitShadow();
    this.t = 0;
    this._applyMotion(0);
    if (this.springManager) this.springManager.reset();
    this.info = this._info(gltf, v);
    if (this.showBones) this.setBonesVisible(true);
    if (this.onLoad) this.onLoad(this.info);
  }

  // ---------------------------------------------------------------- PMX --
  async loadPmx(url, name) {
    let physicsOk = true;
    try { await loadAmmo(); } catch (e) { physicsOk = false; console.warn(e); }
    const mesh = await new Promise((res, rej) => new MMDLoader().load(url, res, undefined, rej));
    this.clear();
    mesh.castShadow = true; mesh.receiveShadow = true; mesh.frustumCulled = false;
    this.scene.add(mesh);
    const mmd = mesh.geometry.userData.MMD || {};
    const helper = new MMDAnimationHelper();
    let physics = physicsOk && (mmd.rigidBodies || []).length > 0;
    try { helper.add(mesh, { physics }); } catch (e) { console.warn(e); physics = false; helper.add(mesh, { physics: false }); }
    if (!this.springOn) helper.enable('physics', false);
    const bones = {};
    for (const b of mesh.skeleton.bones) if (!(b.name in bones)) bones[b.name] = b;
    const en = {};                    // English fallback: slot → bone (first match wins; clavicle+shoulder → shoulder is the arm)
    for (const b of mesh.skeleton.bones) {
      const k = enBone(b.name);
      if (!k) continue;
      if (/UpperArm$/.test(k) && /clavicle/i.test(b.name)) continue;
      if (/UpperArm$/.test(k) && /shoulder/i.test(b.name) && mesh.skeleton.bones.some((o) => enBone(o.name) === k && !/shoulder|clavicle/i.test(o.name))) continue;
      if (!(k in en)) en[k] = b;
    }
    const pick = (k) => (MMD_BONES[k] || []).map((n) => bones[n]).find(Boolean) || en[k] || null;
    const rest = mesh.skeleton.bones.map((b) => [b.position.clone(), b.quaternion.clone()]);
    // how far the arms already hang down in the model's rest pose (MMD models are usually in A-pose)
    const armRest = {};
    for (const [arm, elbow, side] of [['leftUpperArm', 'leftLowerArm', 1], ['rightUpperArm', 'rightLowerArm', -1]]) {
      const a = pick(arm), e = pick(elbow);
      if (a && e) {
        const pa = a.getWorldPosition(new THREE.Vector3()), pe = e.getWorldPosition(new THREE.Vector3());
        armRest[arm] = Math.atan2(pa.y - pe.y, Math.abs(pe.x - pa.x) || 1e-6);
      }
    }
    this.mmd = { mesh, helper, grant: helper.createGrantSolver(mesh), bones, pick, rest, armRest, physics };
    this.name = name || url.split('/').pop();
    const box = new THREE.Box3().setFromObject(mesh);
    this.height = Math.max(0.3, box.max.y - box.min.y);
    const sz = box.getSize(new THREE.Vector3());
    this.extent = Math.max(this.height, sz.x, sz.z);
    this.centerY = (box.min.y + box.max.y) / 2;
    const p = new THREE.Vector3(), head = pick('head'), chest = pick('chest');
    this.headY = head ? head.getWorldPosition(p).y + 0.06 * this.height / 1.6 : this.height * 0.9;
    this.chestY = chest ? chest.getWorldPosition(p).y : this.height * 0.7;
    this._fitShadow();
    this.t = 0;
    this._applyMotionPmx(0);
    this.info = {
      name: this.name, spec: 'PMX', height: this.height, humanBones: Object.keys(MMD_BONES).filter((k) => pick(k)).length,
      triangles: Math.round((mesh.geometry.index ? mesh.geometry.index.count : mesh.geometry.attributes.position.count) / 3),
      materials: [].concat(mesh.material).length,
      textures: new Set([].concat(mesh.material).map((m) => m.map && m.map.uuid).filter(Boolean)).size,
      springs: 0, joints: 0, colliders: 0,
      rigidBodies: (mmd.rigidBodies || []).length, constraints: (mmd.constraints || []).length, physics,
      expressions: Object.keys(mesh.morphTargetDictionary || {}).map((n) => ({ name: n, preset: false })),
    };
    this.info.bones = this.boneStats();
    if (this.showBones) this.setBonesVisible(true);
    if (this.onLoad) this.onLoad(this.info);
    return this.info;
  }

  _applyMotionPmx(f) {
    const { mesh, pick, rest, armRest } = this.mmd;
    mesh.skeleton.bones.forEach((b, i) => { b.position.copy(rest[i][0]); b.quaternion.copy(rest[i][1]); });
    const p = MOTIONS[this.motion](f);
    const set = (k, x, y, z) => { const b = pick(k); if (b) b.quaternion.setFromEuler(new THREE.Euler(x, y, z)); };
    set('hips', 0, p.yaw || 0, p.roll || 0);
    set('spine', 0, p.spineYaw || 0, 0);
    mesh.position.y = (p.bob || 0) * this.height / 1.6;
    set('leftUpperLeg', p.lLeg || 0, 0, 0); set('rightUpperLeg', p.rLeg || 0, 0, 0);
    set('leftLowerLeg', p.lKnee || 0, 0, 0); set('rightLowerLeg', p.rKnee || 0, 0, 0);
    // VRM motions lower the arm from T-pose by `a`; the PMX arm already hangs down by armRest
    const a = 1.22 * Math.min(1, (p.armDown || 0) / 0.7);
    set('leftUpperArm', p.armSwingL || 0, 0, -(a - (armRest.leftUpperArm || 0)));
    set('rightUpperArm', p.armSwingR || 0, 0, a - (armRest.rightUpperArm || 0));
    set('leftLowerArm', 0, p.armSwingL ? -0.3 : 0, 0); set('rightLowerArm', 0, p.armSwingR ? 0.3 : 0, 0);
    mesh.updateMatrixWorld(true);
    this.mmd.grant.update();          // 付与 bones (足D etc. carry the leg skinning in most MMD models)
    // again after the grant: MMD physics reads bone.matrixWorld, and the leg rigid bodies usually hang
    // on 足D / ひざD — without this they stay in the rest pose and skirts never meet the legs
    mesh.updateMatrixWorld(true);
  }

  _info(gltf, v) {
    const json = gltf.parser.json;
    let tris = 0;
    for (const m of json.meshes || []) for (const pr of m.primitives) {
      const a = json.accessors[pr.indices != null ? pr.indices : pr.attributes.POSITION];
      tris += a.count / 3;
    }
    const sb = json.extensions && json.extensions.VRMC_springBone;
    const sa = json.extensions && json.extensions.VRM && json.extensions.VRM.secondaryAnimation;
    const em = v.expressionManager;
    const presets = new Set(em ? Object.values(em.presetExpressionMap || {}).map((e) => e.expressionName) : []);
    return {
      name: (v.meta && (v.meta.name || v.meta.title)) || this.name,
      spec: v.meta && v.meta.metaVersion === '0' ? '0.x' : '1.0',
      height: this.height,
      humanBones: Object.keys(v.humanoid.humanBones).length,
      triangles: Math.round(tris),
      materials: (json.materials || []).length,
      textures: (json.images || []).length,
      springs: sb ? sb.springs.length : (sa ? sa.boneGroups.length : 0),
      joints: sb ? sb.springs.reduce((a, s) => a + s.joints.length, 0) : (sa ? sa.boneGroups.reduce((a, g) => a + g.bones.length, 0) : 0),
      colliders: sb ? (sb.colliders || []).length : 0,
      expressions: em ? em.expressions.map((e) => ({ name: e.expressionName, preset: presets.has(e.expressionName) }))
        .sort((a, b) => (b.preset - a.preset)) : [],
      bones: this.boneStats(),
    };
  }

  // ------------------------------------------------------------- camera --
  frame(kind) {
    this.frameKind = kind;
    const H = this.height, c = this.chestY, h = this.headY, E = this.extent || H, cy = this.centerY ?? H * 0.52;
    const presets = {
      body: [[0, cy + E * 0.03, E * 2.4], [0, cy, 0]],
      upper: [[0, c + 0.05, H * 1.1], [0, c + 0.03, 0]],
      face: [[0, h, H * 0.42], [0, h - 0.01, 0]],
      back: [[0, cy + E * 0.08, -E * 2.2], [0, cy + E * 0.03, 0]],
    };
    const [pos, tgt] = presets[kind] || presets.body;
    this._focus = null;
    this.camera.position.set(...pos); this.controls.target.set(...tgt); this.controls.update();
  }

  // the point of the model under the pointer (client coordinates): a bone of the skeleton overlay when one is
  // within a few pixels, else the first surface hit (skinned and morphed as shown). null when nothing is there
  pointAt(x, y) {
    if (!this.loaded) return null;
    const b = this._boneGfx ? this.boneAt(x, y, 8) : null;
    if (b) return this._bonePos[b.index].clone();
    const r = this.renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((x - r.left) / r.width * 2 - 1, -(y - r.top) / r.height * 2 + 1), this.camera);
    const hit = ray.intersectObject(this.vrm ? this.vrm.scene : this.mmd.mesh, true).find((h) => h.object.isMesh);
    return hit ? hit.point.clone() : null;
  }

  // back to the last framing preset (frame), undoing orbit / pan / zoom / double-click focus
  resetView() { if (this.loaded) this.frame(this.frameKind || 'body'); }

  // move the orbit centre to p (world), keeping the viewing direction and distance; eased over ~0.3 s
  focusOn(p) {
    this._focus = { from: this.controls.target.clone(), to: p.clone(), t: 0 };
  }

  // the orbit centre onto a SpringBone chain (its joint nodes), a little closer when far away
  focusChain(nodes) {
    const byNode = this.vrm ? this._nodes() : null;
    if (!byNode || !nodes || !nodes.length) return;
    this.scene.updateMatrixWorld();
    const c = new THREE.Vector3(), p = new THREE.Vector3(), box = new THREE.Box3();
    let n = 0;
    for (const i of nodes) {
      const o = byNode.get(i);
      if (!o) continue;
      p.setFromMatrixPosition(o.matrixWorld); c.add(p); box.expandByPoint(p); n++;
    }
    if (!n) return;
    c.divideScalar(n);
    const size = Math.max(0.15, box.getSize(p).length());
    const off = this.camera.position.clone().sub(this.controls.target);
    if (off.length() > size * 3) this.camera.position.copy(this.controls.target).add(off.setLength(size * 3));
    this.focusOn(c);
  }

  _stepFocus(dt) {
    const f = this._focus;
    f.t = Math.min(1, f.t + dt / 0.3);
    const k = 1 - Math.pow(1 - f.t, 3);
    const tgt = f.from.clone().lerp(f.to, k), d = tgt.clone().sub(this.controls.target);
    this.controls.target.add(d);
    this.camera.position.add(d);
    if (f.t >= 1) this._focus = null;
  }

  // ------------------------------------------------------------- lights --
  setLight(opts) {
    Object.assign(this.light, opts);
    const L = this.light, H = this.height;
    const az = THREE.MathUtils.degToRad(L.az), el = THREE.MathUtils.degToRad(L.el);
    const d = H * 2.5, cy = H * 0.55;
    this.sun.position.set(Math.sin(az) * Math.cos(el) * d, cy + Math.sin(el) * d, Math.cos(az) * Math.cos(el) * d);
    this.sun.target.position.set(0, cy, 0);
    this.sun.intensity = L.intensity;
    this.ambient.intensity = L.ambient;
    this.sun.castShadow = !!L.shadow;
  }

  _fitShadow() {
    const c = this.sun.shadow.camera, r = this.height * 0.75;
    c.left = -r; c.right = r; c.top = r; c.bottom = -r; c.near = 0.1; c.far = this.height * 6;
    c.updateProjectionMatrix();
    this.floor.scale.setScalar(this.height / 1.6);
    this.sun.shadow.bias = -0.0003;
    this.sun.shadow.normalBias = 0.003 * this.height / 1.6;
    this.setLight({});
  }

  setBackground(color) { this.scene.background = new THREE.Color(color); }

  // ------------------------------------------------------------ motion --
  setMotion(m) { if (MOTIONS[m]) { this.motion = m; this.t = 0; } }
  setSpeed(s) { this.speed = s; }
  setPaused(p) { this.paused = p; }
  setSpring(on) {
    this.springOn = on;
    if (this.mmd) {
      this.mmd.helper.enable('physics', on);
      if (on && this.mmd.physics) this.mmd.helper.objects.get(this.mmd.mesh).physics.reset();
      return;
    }
    if (!this.vrm) return;
    this.vrm.springBoneManager = on ? this.springManager : undefined;
    if (on && this.springManager) this.springManager.reset();
  }
  resetPhysics() {
    if (this.springManager) this.springManager.reset();
    if (this.mmd && this.mmd.physics) this.mmd.helper.objects.get(this.mmd.mesh).physics.reset();
  }

  // glTF node index → SpringBone joint (three-vrm keeps no spring/chain grouping, the file does)
  _joints() {
    if (this._jointsByNode || !this.springManager || !this.gltf) return this._jointsByNode || new Map();
    const m = new Map(), assoc = this.gltf.parser.associations;
    for (const j of this.springManager.joints) {
      const a = assoc.get(j.bone);
      if (a && a.nodes != null) m.set(a.nodes, j);
    }
    return (this._jointsByNode = m);
  }

  setSpringParams(list) {
    const m = this._joints(), mgr = this.springManager;
    if (!mgr) return;
    let readded = false;
    for (const p of list) {
      const j = m.get(p.node);
      if (!j) continue;
      Object.assign(j.settings, { stiffness: p.stiffness, gravityPower: p.gravityPower, dragForce: p.dragForce });
      if (p.enabled === false && !this._removedJoints.has(j)) { mgr.deleteJoint(j); this._removedJoints.add(j); }
      else if (p.enabled !== false && this._removedJoints.has(j)) { mgr.addJoint(j); this._removedJoints.delete(j); readded = true; }
    }
    if (readded) mgr.reset();
  }

  _applyMotion(f) {
    const v = this.vrm;
    if (!v) return;
    const p = MOTIONS[this.motion](f), n = (b) => v.humanoid.getNormalizedBoneNode(b);
    const set = (b, x, y, z) => { const o = n(b); if (o) o.rotation.set(x, y, z); };
    set('hips', 0, p.yaw || 0, p.roll || 0);
    set('spine', 0, p.spineYaw || 0, 0);
    v.scene.position.y = (p.bob || 0) * this.height / 1.6;
    set('leftUpperLeg', p.lLeg || 0, 0, 0); set('rightUpperLeg', p.rLeg || 0, 0, 0);
    set('leftLowerLeg', p.lKnee || 0, 0, 0); set('rightLowerLeg', p.rKnee || 0, 0, 0);
    const a = 1.22 * Math.min(1, (p.armDown || 0) / 0.7);
    set('leftUpperArm', p.armSwingL || 0, 0, -a); set('rightUpperArm', p.armSwingR || 0, 0, a);
    set('leftLowerArm', 0, p.armSwingL ? -0.3 : 0, 0); set('rightLowerArm', 0, p.armSwingR ? 0.3 : 0, 0);
  }

  // -------------------------------------------------------- expressions --
  setExpression(name, value) {
    if (this.vrm && this.vrm.expressionManager) this.vrm.expressionManager.setValue(name, value);
    if (this.mmd) {
      const m = this.mmd.mesh, i = (m.morphTargetDictionary || {})[name];
      if (i !== undefined) m.morphTargetInfluences[i] = value;
    }
  }
  resetExpressions() {
    if (this.vrm && this.vrm.expressionManager) this.vrm.expressionManager.resetValues();
    if (this.mmd && this.mmd.mesh.morphTargetInfluences) this.mmd.mesh.morphTargetInfluences.fill(0);
  }

  // eye gaze preview: yaw / pitch in degrees as VRM lookAt takes them (±90 = the far end of the range).
  // ranges = { h, down, up } overrides the max eye angles (the tune panel previews manual values);
  // null restores the model's own. setLook(null) hands the eyes back to the normal update.
  setLook(yaw, pitch, ranges) {
    const la = this.vrm && this.vrm.lookAt, ap = la && la.applier;
    if (!ap || !ap.applyYawPitch || !ap.rangeMapHorizontalInner) return false;
    const maps = { h: ['rangeMapHorizontalInner', 'rangeMapHorizontalOuter'], down: ['rangeMapVerticalDown'], up: ['rangeMapVerticalUp'] };
    if (!ap._origScale) ap._origScale = Object.fromEntries(Object.values(maps).flat().map((k) => [k, ap[k].outputScale]));
    for (const [k, names] of Object.entries(maps)) {
      for (const n of names) ap[n].outputScale = ranges && ranges[k] != null ? ranges[k] : ap._origScale[n];
    }
    if (yaw === null) { la.autoUpdate = true; ap.applyYawPitch(0, 0); return true; }
    la.autoUpdate = false;
    ap.applyYawPitch(yaw, pitch);
    return true;
  }

  // ------------------------------------------------------------- bones --
  // Every bone of the loaded model and its role:
  //   humanoid  humanoid bone (PMX: a standard MMD bone)
  //   weighted  moves vertices
  //   spring    SpringBone joint / collider (PMX: a bone driven by a physics rigid body)
  //   control   moves nothing itself, but bones below it do (PMX: IK bones too)
  //   unused    none of the above: an empty node
  // → [{ obj, name, parent (index or -1), kind }], cached per model
  bones() {
    if (this._boneList) return this._boneList;
    let objs = [], kind = new Map(), mark = (o, k) => { if (o && !kind.has(o)) kind.set(o, k); };
    const skinned = [];
    if (this.vrm) {
      const json = this.gltf.parser.json, byNode = this._nodes(), ext = json.extensions || {};
      const roots = new Set((json.scenes || [])[json.scene || 0]?.nodes || []);
      (json.nodes || []).forEach((n, i) => { if (n.mesh == null && !roots.has(i) && byNode.get(i)) objs.push(byNode.get(i)); });
      const node = (i) => byNode.get(i);
      const hb = ext.VRMC_vrm ? Object.values(ext.VRMC_vrm.humanoid.humanBones).map((b) => b.node)
        : ext.VRM ? ext.VRM.humanoid.humanBones.map((b) => b.node) : [];
      hb.forEach((i) => mark(node(i), 'humanoid'));
      const sb = ext.VRMC_springBone, sa = ext.VRM && ext.VRM.secondaryAnimation;
      if (sb) {
        for (const s of sb.springs || []) for (const j of s.joints) mark(node(j.node), 'spring');
        for (const c of sb.colliders || []) mark(node(c.node), 'spring');
      }
      if (sa) {
        for (const g of sa.boneGroups || []) for (const r of g.bones || []) node(r)?.traverse((o) => mark(o, 'spring'));
        for (const g of sa.colliderGroups || []) mark(node(g.node), 'spring');
      }
      this.vrm.scene.traverse((o) => { if (o.isSkinnedMesh) skinned.push(o); });
    } else if (this.mmd) {
      const { mesh } = this.mmd, mmd = mesh.geometry.userData.MMD || {}, B = mesh.skeleton.bones;
      objs = B.slice();
      B.forEach((b) => { if (MMD_STANDARD.has(b.name)) mark(b, 'humanoid'); });
      for (const r of mmd.rigidBodies || []) if (r.type !== 0 && r.boneIndex >= 0) mark(B[r.boneIndex], 'spring');
      for (const ik of mmd.iks || []) mark(B[ik.target], 'ik');
      skinned.push(mesh);
    }
    for (const m of skinned) {
      const si = m.geometry.attributes.skinIndex, sw = m.geometry.attributes.skinWeight, bs = m.skeleton.bones;
      if (!si || !sw) continue;
      const hit = new Uint8Array(bs.length);
      for (let k = 0; k < sw.array.length; k++) if (sw.array[k] > 0) hit[si.array[k]] = 1;
      hit.forEach((h, i) => { if (h) mark(bs[i], 'weighted'); });
    }
    const index = new Map(objs.map((o, i) => [o, i]));
    const list = objs.map((o) => {
      let p = o.parent;
      while (p && !index.has(p)) p = p.parent;
      return { obj: o, name: o.name, parent: p ? index.get(p) : -1, kind: kind.get(o) === 'ik' ? 'control' : kind.get(o) || null };
    });
    // control: no role of its own, but something below has one
    for (const b of list) {
      if (!b.kind || b.kind === 'control') continue;
      for (let p = b.parent; p >= 0 && !list[p].kind; p = list[p].parent) list[p].kind = 'control';
    }
    for (const b of list) b.kind = b.kind || 'unused';
    return (this._boneList = list);
  }

  // glTF node index → Object3D
  _nodes() {
    if (this._byNode) return this._byNode;
    const m = new Map();
    for (const [o, a] of this.gltf.parser.associations) if (o.isObject3D && a && a.nodes != null && !m.has(a.nodes)) m.set(a.nodes, o);
    return (this._byNode = m);
  }

  // one SpringBone chain (VRM 1.0), picked by its joint nodes: the chain, each joint's hit radius and the
  // colliders it collides with, drawn over the model and following it. null clears it.
  highlightChain(nodes) {
    if (this._chainGfx) {
      this.scene.remove(this._chainGfx);
      this._chainGfx.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
      this._chainGfx = null;
    }
    const sb = this.vrm && this.gltf.parser.json.extensions?.VRMC_springBone;
    if (!nodes || !nodes.length || !sb) return;
    const spring = (sb.springs || []).find((sp) => sp.joints[0] && sp.joints[0].node === nodes[0]);
    if (!spring) return;
    const byNode = this._nodes(), over = { depthTest: false, depthWrite: false, transparent: true };
    const g = new THREE.Group(), items = [];
    // a ball = three great circles (a full wireframe sphere hides the model under dozens of colliders)
    const ring = [];
    for (let k = 0; k < 3; k++) {
      for (let i = 0; i < 32; i++) {
        for (const a of [i / 32 * Math.PI * 2, (i + 1) / 32 * Math.PI * 2]) {
          const c = Math.cos(a), s = Math.sin(a);
          ring.push(k === 0 ? [c, s, 0] : k === 1 ? [c, 0, s] : [0, c, s]);
        }
      }
    }
    const ringGeo = new THREE.BufferGeometry().setFromPoints(ring.map((q) => new THREE.Vector3(...q)));
    const sphere = (color, opacity) => new THREE.LineSegments(ringGeo, new THREE.LineBasicMaterial({ ...over, color, opacity }));
    // the chain: a line through the joints and a sphere of hitRadius on each
    const js = spring.joints.map((j) => ({ obj: byNode.get(j.node), r: j.hitRadius || 0 })).filter((j) => j.obj);
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(js.map(() => new THREE.Vector3())),
      new THREE.LineBasicMaterial({ ...over, color: 0xffe14d }));
    g.add(line);
    for (const j of js) {
      const m = sphere(0xffe14d, 0.9);
      g.add(m);
      items.push({ mesh: m, obj: j.obj, offset: new THREE.Vector3(), r: Math.max(j.r, 0.004) });
    }
    // its colliders: sphere = one ball; capsule = balls at both ends joined by lines
    const ids = new Set((spring.colliderGroups || []).flatMap((gi) => (sb.colliderGroups || [])[gi]?.colliders || []));
    const caps = [];
    for (const ci of ids) {
      const c = sb.colliders[ci], obj = byNode.get(c.node);
      if (!obj) continue;
      const sh = c.shape.sphere || c.shape.capsule;
      const off = new THREE.Vector3(...(sh.offset || [0, 0, 0]));
      const a = sphere(0x40e0ff, 0.5); g.add(a);
      items.push({ mesh: a, obj, offset: off, r: sh.radius });
      if (c.shape.capsule) {
        const tail = new THREE.Vector3(...(sh.tail || [0, 0, 0]));
        const b = sphere(0x40e0ff, 0.5); g.add(b);
        items.push({ mesh: b, obj, offset: tail, r: sh.radius });
        const l = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(Array.from({ length: 8 }, () => new THREE.Vector3())),
          new THREE.LineBasicMaterial({ ...over, color: 0x40e0ff, opacity: 0.6 }));
        g.add(l);
        caps.push({ line: l, a: items[items.length - 2], b: items[items.length - 1] });
      }
    }
    g.children.forEach((o) => { o.renderOrder = 1000; o.frustumCulled = false; });
    this._chainGfx = g;
    this._chain = { line, joints: js, items, caps };
    this.scene.add(g);
    this._updateChain();
  }

  _updateChain() {
    const { line, joints, items, caps } = this._chain, p = new THREE.Vector3();
    const lp = line.geometry.attributes.position;
    joints.forEach((j, i) => { p.setFromMatrixPosition(j.obj.matrixWorld); lp.setXYZ(i, p.x, p.y, p.z); });
    lp.needsUpdate = true;
    for (const it of items) {
      it.mesh.position.copy(it.offset).applyMatrix4(it.obj.matrixWorld);
      it.mesh.scale.setScalar(it.r);
    }
    // capsule sides: four lines between the end balls, offset by the radius across the axis
    const d = new THREE.Vector3(), u = new THREE.Vector3(), v = new THREE.Vector3();
    for (const c of caps) {
      const A = c.a.mesh.position, B = c.b.mesh.position, r = c.a.r, cp = c.line.geometry.attributes.position;
      d.subVectors(B, A).normalize();
      u.set(Math.abs(d.y) < 0.9 ? 0 : 1, Math.abs(d.y) < 0.9 ? 1 : 0, 0).cross(d).normalize();
      v.crossVectors(d, u);
      [u, v, u.clone().negate(), v.clone().negate()].forEach((w, k) => {
        cp.setXYZ(k * 2, A.x + w.x * r, A.y + w.y * r, A.z + w.z * r);
        cp.setXYZ(k * 2 + 1, B.x + w.x * r, B.y + w.y * r, B.z + w.z * r);
      });
      cp.needsUpdate = true;
    }
  }

  // bone counts by role (the info panel)
  boneStats() {
    const s = { total: 0, humanoid: 0, weighted: 0, spring: 0, control: 0, unused: 0 };
    for (const b of this.bones()) { s.total++; s[b.kind]++; }
    return s;
  }

  // skeleton overlay: a line to the parent and a dot per bone, coloured by role, drawn over the model
  setBonesVisible(on, keep = false) {
    if (!keep) this.showBones = on;
    if (this._boneGfx) { this.scene.remove(this._boneGfx); this._boneGfx.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); }); this._boneGfx = null; }
    if (!on || !this.loaded) return;
    const list = this.bones(), n = list.length;
    const col = (k) => new THREE.Color(BONE_COLORS[k]);
    const lines = new THREE.BufferGeometry(), dots = new THREE.BufferGeometry();
    const lc = new Float32Array(n * 6), dc = new Float32Array(n * 3);
    list.forEach((b, i) => {
      const c = col(b.kind);
      c.toArray(dc, i * 3); c.toArray(lc, i * 6); c.toArray(lc, i * 6 + 3);
    });
    lines.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 6), 3));
    lines.setAttribute('color', new THREE.BufferAttribute(lc, 3));
    dots.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    dots.setAttribute('color', new THREE.BufferAttribute(dc, 3));
    const over = { depthTest: false, depthWrite: false, transparent: true, vertexColors: true };
    const g = new THREE.Group();
    g.add(new THREE.LineSegments(lines, new THREE.LineBasicMaterial({ ...over, opacity: 0.85 })));
    g.add(new THREE.Points(dots, new THREE.PointsMaterial({ ...over, size: 5, sizeAttenuation: false })));
    const hl = new THREE.BufferGeometry();
    hl.setAttribute('position', new THREE.BufferAttribute(new Float32Array(3), 3));
    const hp = new THREE.Points(hl, new THREE.PointsMaterial({ depthTest: false, transparent: true, size: 11, sizeAttenuation: false, color: 0xffffff }));
    hp.visible = false;
    g.add(hp);
    g.children.forEach((o) => { o.renderOrder = 999; o.frustumCulled = false; });
    this._boneGfx = g;
    this.scene.add(g);
    this._updateBones();
  }

  _updateBones() {
    const g = this._boneGfx;
    if (!g) return;
    const list = this.bones(), [ls, ds, hp] = g.children;
    const lp = ls.geometry.attributes.position, dp = ds.geometry.attributes.position, v = new THREE.Vector3();
    this._bonePos = this._bonePos || list.map(() => new THREE.Vector3());
    list.forEach((b, i) => this._bonePos[i].setFromMatrixPosition(b.obj.matrixWorld));
    list.forEach((b, i) => {
      const p = this._bonePos[i], q = b.parent >= 0 ? this._bonePos[b.parent] : p;
      dp.setXYZ(i, p.x, p.y, p.z);
      lp.setXYZ(i * 2, p.x, p.y, p.z); lp.setXYZ(i * 2 + 1, q.x, q.y, q.z);
    });
    lp.needsUpdate = dp.needsUpdate = true;
    hp.visible = this.boneHover != null && this.boneHover >= 0;
    if (hp.visible) { v.copy(this._bonePos[this.boneHover]); hp.geometry.attributes.position.setXYZ(0, v.x, v.y, v.z); hp.geometry.attributes.position.needsUpdate = true; }
  }

  // bone under the pointer (client coordinates), within `px` pixels → { index, name, kind, parent } or null;
  // highlights it in the overlay
  boneAt(x, y, px = 10) {
    if (!this._boneGfx || !this._bonePos) return null;
    const r = this.renderer.domElement.getBoundingClientRect(), v = new THREE.Vector3();
    let best = -1, bd = px * px, bz = Infinity;
    this._bonePos.forEach((p, i) => {
      v.copy(p).project(this.camera);
      if (v.z > 1) return;
      const dx = (v.x + 1) / 2 * r.width + r.left - x, dy = (1 - v.y) / 2 * r.height + r.top - y, d = dx * dx + dy * dy;
      if (d < bd - 1e-6 || (Math.abs(d - bd) < 1e-6 && v.z < bz)) { best = i; bd = d; bz = v.z; }
    });
    this.boneHover = best;
    if (best < 0) return null;
    const list = this.bones(), b = list[best];
    return { index: best, name: b.name, kind: b.kind, parent: b.parent >= 0 ? list[b.parent].name : null };
  }

  snapshot() { this.renderer.render(this.scene, this.camera); return this.renderer.domElement.toDataURL('image/png'); }

  // centre square of the current view, scaled to size×size (VRM thumbnails are square)
  snapshotSquare(size = 1024) {
    const gfx = [this._boneGfx, this._chainGfx].filter(Boolean);   // a thumbnail never shows the overlays
    gfx.forEach((o) => { o.visible = false; });
    this.renderer.render(this.scene, this.camera);
    gfx.forEach((o) => { o.visible = true; });
    const src = this.renderer.domElement, s = Math.min(src.width, src.height);
    const c = document.createElement('canvas'); c.width = c.height = size;
    c.getContext('2d').drawImage(src, (src.width - s) / 2, (src.height - s) / 2, s, s, 0, 0, size, size);
    return c.toDataURL('image/png');
  }

  // --------------------------------------------------------------- loop --
  _tick() {
    const dt = Math.min(0.1, this.clock.getDelta());
    if (this.vrm && !this.paused) {
      // motion advances on a 60 Hz grid (like the QA harness); physics steps with real time
      this.acc += dt * this.speed * 60;
      const steps = Math.floor(this.acc);
      this.acc -= steps;
      this.t += steps;
      this._applyMotion(this.t);
      this.vrm.update(dt * this.speed);
    } else if (this.mmd && !this.paused) {
      this.acc += dt * this.speed * 60;
      const steps = Math.floor(this.acc);
      this.acc -= steps;
      this.t += steps;
      this._applyMotionPmx(this.t);
      this.mmd.helper.update(dt * this.speed);      // MMD physics only (no animation clip → no IK)
    }
    if (this._focus) this._stepFocus(dt);
    this.controls.update();
    if (this._boneGfx || this._chainGfx) this.scene.updateMatrixWorld();
    if (this._boneGfx) this._updateBones();
    if (this._chainGfx) this._updateChain();
    this.renderer.render(this.scene, this.camera);
  }
}
