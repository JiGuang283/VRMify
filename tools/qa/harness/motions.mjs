// Test motions for measure.mjs. Each returns per-frame pose params (radians), 60 fps,
// applied to the VRM's normalized humanoid bones.
//   yaw: hips rotation about +Y
//   lLeg/rLeg: thigh pitch about +X (negative = leg forward), lKnee/rKnee: knee bend (+X)
const smooth = (t) => t * t * (3 - 2 * t);

export const MOTIONS = {
  static: (f) => ({}),
  yaw_gentle: (f) => ({ yaw: f < 150 ? Math.sin(f / 60 * Math.PI * 2 * 0.8) * 0.35 : 0 }),
  yaw_strong: (f) => ({ yaw: f < 150 ? Math.sin(f / 60 * Math.PI * 2 * 1.2) * 0.8 : 0 }),
  leg_raise: (f) => {
    const k = f < 40 ? smooth(f / 40) : f < 100 ? 1 : f < 140 ? smooth(1 - (f - 100) / 40) : 0;
    return { lLeg: -1.2 * k, lKnee: 1.0 * k };
  },
  walk: (f) => {
    const s = Math.sin(f / 60 * Math.PI * 2 * 0.9);
    const a = f < 180 ? 1 : 0;
    return {
      lLeg: -0.5 * s * a, rLeg: 0.5 * s * a,
      lKnee: Math.max(0, s) * 0.6 * a, rKnee: Math.max(0, -s) * 0.6 * a,
      yaw: Math.sin(f / 60 * Math.PI * 2 * 0.9) * 0.08 * a,
    };
  },
};
// idle: arms lowered from the T-pose rest over 1 s (armDown 0.7 ≈ 70°), slight sway.
MOTIONS.idle = (f) => ({ armDown: 0.7 * smooth(Math.min(1, f / 60)), yaw: Math.sin(f / 60 * Math.PI * 2 * 0.25) * 0.05 });
// Realistic gait with root motion (forward along +Z), pelvis bob/twist, arm swing.
//   rootZ: metres travelled; bob: pelvis vertical offset; roll: pelvis roll; arm: forward swing
function gait(f, speed, freq, thigh, knee, bob, armSwing) {
  const t = f / 60, ph = t * Math.PI * 2 * freq;
  const s = Math.sin(ph);
  const kneeL = knee * Math.max(0, Math.sin(ph + 0.9)), kneeR = knee * Math.max(0, Math.sin(ph + 0.9 + Math.PI));
  return {
    rootZ: speed * t, bob: -bob * Math.cos(2 * ph),
    yaw: 0.08 * s, roll: 0.04 * Math.sin(2 * ph), spineYaw: -0.1 * s,
    lLeg: -thigh * s, rLeg: thigh * s, lKnee: 0.1 + kneeL, rKnee: 0.1 + kneeR,
    armDown: 0.7 * smooth(Math.min(1, f / 40)), armSwingL: armSwing * s, armSwingR: -armSwing * s,
  };
}
MOTIONS.walk_real = (f) => (f < 300 ? gait(f, 1.2, 0.9, 0.45, 0.9, 0.025, 0.35) : { ...gait(299, 1.2, 0.9, 0, 0, 0, 0), rootZ: 1.2 * 5, lLeg: 0, rLeg: 0, lKnee: 0, rKnee: 0, yaw: 0, roll: 0, spineYaw: 0, bob: 0, armSwingL: 0, armSwingR: 0 });
MOTIONS.run = (f) => (f < 240 ? gait(f, 3.0, 1.4, 0.8, 1.6, 0.05, 0.7) : { rootZ: 3.0 * 4, armDown: 0.7 });
export const FRAMES = { walk_real: 300, run: 240, idle: 240, static: 120, yaw_gentle: 240, yaw_strong: 240, leg_raise: 200, walk: 270 };
