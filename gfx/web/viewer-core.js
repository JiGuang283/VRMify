// Reusable VRM viewer: three.js + three-vrm (the same SpringBone / MToon implementation as UniVRM
// and the QA harness). No UI of its own — pages wire their controls to the Viewer API.
//
//   const v = new Viewer(containerElement);
//   await v.loadUrl(url, name)  |  await v.loadBuffer(arrayBuffer, name)
//   v.setMotion('walk'); v.setLight({ az: 35, el: 40, intensity: 1.4, ambient: 0.6, shadow: true });
//   v.frame('face'); v.setExpression('happy', 1); v.snapshot() → PNG data URL
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

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

  // ------------------------------------------------------------ loading --
  async loadUrl(url, name) {
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
    this.vrm = null; this.info = null; this.springManager = null; this.name = '';
  }

  _setModel(gltf, v, name) {
    this.clear();
    VRMUtils.removeUnnecessaryVertices(gltf.scene);
    VRMUtils.combineSkeletons(gltf.scene);
    if (v.meta && v.meta.metaVersion === '0') VRMUtils.rotateVRM0(v);
    v.scene.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; o.frustumCulled = false; } });
    this.scene.add(v.scene);
    this.vrm = v; this.name = name;
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
    this.setLight({});
  }

  setBackground(color) { this.scene.background = new THREE.Color(color); }

  // ------------------------------------------------------------ motion --
  setMotion(m) { if (MOTIONS[m]) { this.motion = m; this.t = 0; } }
  setSpeed(s) { this.speed = s; }
  setPaused(p) { this.paused = p; }
  setSpring(on) {
    this.springOn = on;
    if (!this.vrm) return;
    this.vrm.springBoneManager = on ? this.springManager : undefined;
    if (on && this.springManager) this.springManager.reset();
  }
  resetPhysics() { if (this.springManager) this.springManager.reset(); }

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
  setExpression(name, value) { if (this.vrm && this.vrm.expressionManager) this.vrm.expressionManager.setValue(name, value); }
  resetExpressions() { if (this.vrm && this.vrm.expressionManager) this.vrm.expressionManager.resetValues(); }

  snapshot() { this.renderer.render(this.scene, this.camera); return this.renderer.domElement.toDataURL('image/png'); }

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
    }
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}
