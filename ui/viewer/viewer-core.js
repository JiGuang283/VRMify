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
  constructor(container, { background = '#8a8f98' } = {}) {
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
    r.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
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
    tick();
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
    };
  }

  // ------------------------------------------------------------- camera --
  frame(kind) {
    const H = this.height, c = this.chestY, h = this.headY, E = this.extent || H, cy = this.centerY ?? H * 0.52;
    const presets = {
      body: [[0, cy + E * 0.03, E * 2.4], [0, cy, 0]],
      upper: [[0, c + 0.05, H * 1.1], [0, c + 0.03, 0]],
      face: [[0, h, H * 0.42], [0, h - 0.01, 0]],
      back: [[0, cy + E * 0.08, -E * 2.2], [0, cy + E * 0.03, 0]],
    };
    const [pos, tgt] = presets[kind] || presets.body;
    this.camera.position.set(...pos); this.controls.target.set(...tgt); this.controls.update();
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

  snapshot() { this.renderer.render(this.scene, this.camera); return this.renderer.domElement.toDataURL('image/png'); }

  // centre square of the current view, scaled to size×size (VRM thumbnails are square)
  snapshotSquare(size = 1024) {
    this.renderer.render(this.scene, this.camera);
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
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
