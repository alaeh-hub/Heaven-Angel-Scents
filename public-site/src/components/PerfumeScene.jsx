import { useEffect, useRef } from 'react';
import * as THREE from 'three';

// Uriel H1 & Raphael A3: two perfume droplets hang glowing in the dark,
// a thin stream pours into each, they fall and land on the floor, and
// each rises into its bottle (tinted liquid-glass first, then it clears
// and fills, and the collar, cap and label arrive). Two display
// platforms, each with its own LED ring, lift the finished bottles over
// a glossy black floor that reflects them. A bloom + ACES pass with a
// little film grain finishes each frame.
//
// Ported from prototype/perfume-scene/index.html (which looped it every 10.8 s on three
// r149, fading to black between loops); here the timeline is driven
// from outside and there is no fade, since the hero fades the canvas in.
// The two labels that file embedded as base64 live in
// static/img/scene-label-*.jpg.
//
// variant="turntable" is the finished pair from
// prototype/turntable/*.html (one bottle each there, both here): each
// bottle turns on its lit platform, one full turn every 10 s after a
// 1.8 s ease-in, while the studio lights come up, caustics drift through
// the liquid and a shallow depth of field softens the floor.
//
// r149 used "legacy" colour handling (hex colours as-is); turning colour
// management off keeps every material looking as authored.
THREE.ColorManagement.enabled = false;

/** Scene time (seconds) of the final frame: both bottles on their platforms. */
export const SCENE_END = 10;
/** Scene time when the droplets have glowed in, just before the pour. */
export const SCENE_INTRO = 1.2;

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const lin = (a, b, t) => clamp((t - a) / (b - a));
const sstep = (x) => x * x * (3 - 2 * x);
const ss = (a, b, t) => sstep(lin(a, b, t));
const eIO = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const eIOS = (x) => -(Math.cos(Math.PI * x) - 1) / 2;
const eIn = (x) => x * x * x;
const lerp = (a, b, x) => a + (b - a) * x;

/** Cubic Hermite through [t, v] keys (Catmull-Rom tangents, clamped ends). */
function curve(keys, t) {
  if (t <= keys[0][0]) return keys[0][1];
  const n = keys.length - 1;
  if (t >= keys[n][0]) return keys[n][1];
  let i = 0;
  while (t > keys[i + 1][0]) i += 1;
  const [t0, v0] = keys[i];
  const [t1, v1] = keys[i + 1];
  const dt = t1 - t0;
  const u = (t - t0) / dt;
  const m = (j) => (j <= 0 || j >= n ? 0 : (keys[j + 1][1] - keys[j - 1][1]) / (keys[j + 1][0] - keys[j - 1][0]));
  const m0 = m(i) * dt;
  const m1 = m(i + 1) * dt;
  const u2 = u * u;
  const u3 = u2 * u;
  return (2 * u3 - 3 * u2 + 1) * v0 + (u3 - 2 * u2 + u) * m0 + (-2 * u3 + 3 * u2) * v1 + (u3 - u2) * m1;
}

const CAM = { // [time, value]
  az: [[0, 0.14], [1.5, 0.07], [3, -0.2], [5, -0.3], [7, 0.06], [8, 0], [10, 0]],
  el: [[0, 0.06], [1.5, 0.05], [3, 0.06], [5, 0.07], [7, 0.09], [8, 0.11], [10, 0.095]],
  dist: [[0, 5.6], [1.5, 4.7], [3, 6.3], [5, 7.4], [7, 8.3], [8, 8.7], [10, 7.8]],
  ty: [[0, 1.0], [1.5, 0.99], [3, 0.98], [5, 0.92], [7, 0.95], [8, 0.98], [10, 1.0]],
};

/** Time constant (ms) of the ease toward the scrolled-to moment (≈ 0.12 per frame at 60 Hz). */
const EASE_MS = 130;
/** Average ms per frame above which the scene steps down a quality tier (≈ 40 fps). */
const SLOW_FRAME_MS = 25;
/** Pixel-ratio cap and glass-refraction resolution, best first. */
const QUALITY = [
  { dpr: 1.75, transmission: 1, dof: true },
  { dpr: 1.5, transmission: 0.75, dof: true },
  { dpr: 1.25, transmission: 0.5, dof: false },
  { dpr: 1, transmission: 0.5, dof: false },
];

/* Turntable timing, exactly as in prototype/turntable/*.html. */
const SPIN = (Math.PI * 2) / 10; // one full turn every 10 s
const RAMP = 1.8; // the spin eases in over the first 1.8 s

/** Turntable angle at time t: eases in over RAMP, then a steady SPIN (continuous in speed). */
function spinAngle(t) {
  const x = Math.min(t / RAMP, 1);
  return t < RAMP ? SPIN * RAMP * (x * x * x - (x * x * x * x) / 2) : SPIN * (RAMP / 2 + (t - RAMP));
}

// Vertex shader shared by the full-screen post-processing passes.
const QUAD_VS = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }';

/**
 * Builds the scene and its post-processing on `renderer`. `invalidate`
 * is called when something arrives late (the label images) and the
 * current frame needs drawing again. `turntable` builds the turntable
 * variant instead of the story; `tones` (CSS colours) tint its backdrop
 * glow along render()'s `tone` argument.
 */
function buildScene(renderer, invalidate, { turntable = false, tones = [] } = {}) {
  const disposables = [];
  const keep = (x) => { disposables.push(x); return x; };

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x000000);
  const camera = new THREE.PerspectiveCamera(22, 1, 1.2, 120);

  /* ---------------- studio environments ---------------- */
  function buildEnv(strips) {
    const env = new THREE.Scene();
    env.background = new THREE.Color(0);
    for (const [w, h, pos, rgb] of strips) {
      const m = new THREE.Mesh(
        new THREE.PlaneGeometry(w, h),
        new THREE.MeshBasicMaterial({ color: new THREE.Color(...rgb), side: THREE.DoubleSide }),
      );
      m.position.set(...pos);
      m.lookAt(0, 0.8, 0);
      env.add(m);
    }
    const pm = new THREE.PMREMGenerator(renderer);
    const tex = pm.fromScene(env, 0.02).texture;
    pm.dispose();
    env.traverse((o) => {
      if (o.isMesh) {
        o.geometry.dispose();
        o.material.dispose();
      }
    });
    return keep(tex);
  }
  const TOP = [5, 1.6, [0, 6.5, 1.2], [2.0, 1.9, 1.8]];
  const STREAK = [0.16, 7, [1.3, 0.8, 6.5], [2.2, 2.1, 2.0]];
  const GOLD_R = [0.8, 9, [5.2, 0.6, -1.0], [3.6, 2.1, 0.5]];
  const GOLD_F = [0.22, 8, [4.2, 0.4, 3.8], [3.0, 1.7, 0.45]];
  const envPink = buildEnv([[0.8, 9, [-5.5, 0.6, -1.5], [4.2, 0.35, 1.7]], [0.4, 9, [-4, 0.2, 3.5], [2.8, 0.2, 0.9]], [0.5, 9, [0.5, 0.5, -6], [2.5, 0.2, 0.8]], GOLD_R, GOLD_F, TOP, STREAK]);
  const envCyan = buildEnv([[0.8, 9, [-5.5, 0.6, -1.5], [0.25, 2.0, 4.2]], [0.4, 9, [-4, 0.2, 3.5], [0.1, 0.9, 2.8]], [0.5, 9, [0.5, 0.5, -6], [0.1, 1.0, 2.6]], GOLD_R, GOLD_F, TOP, STREAK]);
  const envStage = buildEnv([[0.7, 9, [-5.5, 0.5, -1.5], [2.4, 0.3, 1.2]], [0.7, 9, [5.5, 0.5, -1.5], [0.2, 1.4, 2.8]], TOP, STREAK]);
  scene.environment = envStage;

  /* ---------------- product specs (measured from reference photos) ---------------- */
  const loader = new THREE.TextureLoader();
  const labelTex = (src) => {
    const t = loader.load(src, invalidate);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    return keep(t);
  };
  const S_RED = 0.25 / 74.5;
  // Raphael's bottle scaled uniformly so its overall height matches Uriel's original (proportions unchanged)
  const S_BLUE = (S_RED * (374 + 76 + 92)) / (317 + 67 + 82);
  // Both scents use this one bottle (glass, collar and cap dimensions).
  const BOTTLE = { R: 71.5 * S_BLUE, H: 317 * S_BLUE, collarR: 41 * S_BLUE, collarH: 67 * S_BLUE, capR: 29.5 * S_BLUE, capH: 82 * S_BLUE };
  // ...and one label size and placement: Uriel's label at the same relative
  // height it has on its original bottle, keeping that label's aspect ratio.
  const LABEL = {
    l0: (BOTTLE.H * 14) / 374,
    l1: (BOTTLE.H * 349) / 374,
    arc: (BOTTLE.H * 335) / 374 / (335 / (74.5 * 2.5133)) / BOTTLE.R,
  };
  const PINK = new THREE.Color(1.0, 0.1, 0.5);
  const CYAN = new THREE.Color(0.05, 0.8, 1.0);
  const SPEC = {
    red: {
      ...BOTTLE, ...LABEL, tex: labelTex('/static/img/scene-label-uriel.jpg'),
      liquid: 0xd8384f, liqEm: 0x5e0414, collar: 'clear', tint: new THREE.Color(1.0, 0.16, 0.3), rim: PINK, env: envPink,
    },
    blue: {
      ...BOTTLE, ...LABEL, tex: labelTex('/static/img/scene-label-raphael.jpg'),
      liquid: 0x1fb4c9, liqEm: 0x034a55, collar: 'gold', tint: new THREE.Color(0.1, 0.75, 1.0), rim: CYAN, env: envCyan,
    },
  };

  /* ---------------- glass ---------------- */
  // Two slow light sweeps (view-space directions) that travel across all the glass.
  // `i` is their strength (the turntable fades them in with its lights).
  const SWEEP = { a: { value: new THREE.Vector3(0, 0, 1) }, b: { value: new THREE.Vector3(0, 0, 1) }, i: { value: 1 } };
  // Turntable only: time driving the caustics in the liquid.
  const LIQT = { value: 0 };

  /** Rim glow + sweep highlights: the shared look of droplets, liquid forms and bottle glass. */
  function rimify(mat, col) {
    const u = { uRim: { value: 1 }, uRimCol: { value: col.clone() }, uCore: { value: 0.05 }, uSwA: SWEEP.a, uSwB: SWEEP.b, uSwI: SWEEP.i };
    mat.userData.u = u;
    mat.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, u);
      sh.fragmentShader = 'uniform float uRim; uniform float uCore; uniform vec3 uRimCol; uniform vec3 uSwA; uniform vec3 uSwB; uniform float uSwI;\n' + sh.fragmentShader.replace(
        '#include <emissivemap_fragment>',
        '#include <emissivemap_fragment>\n vec3 nR = normalize(normal); vec3 vR = normalize(vViewPosition);\n float fzR = abs(dot(nR, vR));\n totalEmissiveRadiance += uRimCol * uRim * (pow(1.0 - fzR, 2.6) * 1.6 + uCore * pow(fzR, 2.0));\n vec3 rfl = reflect(-vR, nR);\n totalEmissiveRadiance += uSwI * (vec3(1.0, 0.8, 0.55) * 2.6 * pow(max(dot(rfl, uSwA), 0.0), 700.0) + vec3(1.0) * 1.4 * pow(max(dot(rfl, uSwB), 0.0), 900.0));',
      );
    };
    return mat;
  }

  /** k = 1: coloured liquid-glass, k = 0: clear bottle glass. */
  function tintGlass(mat, sp, k) {
    mat.attenuationColor.setRGB(lerp(1, sp.tint.r, k), lerp(1, sp.tint.g, k), lerp(1, sp.tint.b, k));
    mat.attenuationDistance = lerp(4.0, 0.32, k);
    mat.userData.u.uRim.value = lerp(0.22, 1.0, k);
    mat.userData.u.uCore.value = lerp(0.0, 0.12, k);
  }

  function makeGlassMat(sp) {
    return rimify(new THREE.MeshPhysicalMaterial({
      color: 0xffffff, roughness: 0.015, transmission: 1, thickness: 0.4, ior: 1.47,
      clearcoat: 1, clearcoatRoughness: 0.02, specularIntensity: 1, envMap: sp.env, envMapIntensity: 1.7,
      attenuationColor: new THREE.Color(1, 1, 1), attenuationDistance: 4, emissive: 0x000000,
    }), sp.rim);
  }

  /** Lathe profile of the bottle glass: rounded base and shoulder, short neck. */
  function bottleProfile(sp) {
    const R = sp.R;
    const H = sp.H;
    const rb = 0.035;
    const rt = 0.055;
    const nr = sp.collarR * 0.92;
    const pts = [];
    for (let i = 0; i <= 6; i += 1) pts.push(new THREE.Vector2(0.001 + ((R - rb) * i) / 6, 0));
    for (let i = 1; i <= 10; i += 1) {
      const a = -Math.PI / 2 + ((Math.PI / 2) * i) / 10;
      pts.push(new THREE.Vector2(R - rb + rb * Math.cos(a), rb + rb * Math.sin(a)));
    }
    for (let i = 1; i <= 24; i += 1) pts.push(new THREE.Vector2(R, rb + ((H - rb - rt) * i) / 24));
    for (let i = 1; i <= 10; i += 1) {
      const a = ((Math.PI / 2) * i) / 10;
      pts.push(new THREE.Vector2(R - rt + rt * Math.cos(a), H - rt + rt * Math.sin(a)));
    }
    for (let i = 1; i <= 4; i += 1) pts.push(new THREE.Vector2(R - rt - ((R - rt - nr) * i) / 4, H));
    for (let i = 1; i <= 3; i += 1) pts.push(new THREE.Vector2(nr, H + (0.05 * i) / 3));
    for (let i = 1; i <= 3; i += 1) pts.push(new THREE.Vector2(nr * (1 - i / 3) + 0.001, H + 0.05));
    return pts;
  }

  /* ---------------- bottle ---------------- */
  function makeBottle(sp) {
    const root = new THREE.Group();
    const glassMat = makeGlassMat(sp);
    const glass = new THREE.Mesh(new THREE.LatheGeometry(bottleProfile(sp), 96), glassMat);
    root.add(glass);

    // perfume liquid (fills from the thick glass base)
    const R = sp.R;
    const H = sp.H;
    const liqH = H - 0.12;
    const lg = new THREE.CylinderGeometry(R - 0.02, R - 0.02, liqH, 96);
    lg.translate(0, liqH / 2, 0);
    const liqMat = new THREE.MeshPhysicalMaterial({
      color: sp.liquid, emissive: sp.liqEm, emissiveIntensity: 1.1, roughness: 0.06,
      clearcoat: 1, clearcoatRoughness: 0.04, envMap: sp.env, envMapIntensity: 1.1,
    });
    liqMat.onBeforeCompile = turntable
      ? (sh) => { // plus slow caustics drifting through the liquid
        sh.uniforms.uTime = LIQT;
        sh.vertexShader = 'varying vec3 vLoc;\n' + sh.vertexShader.replace('#include <begin_vertex>', '#include <begin_vertex>\n vLoc = position;');
        sh.fragmentShader = 'uniform float uTime; varying vec3 vLoc;\n' + sh.fragmentShader.replace(
          '#include <emissivemap_fragment>',
          '#include <emissivemap_fragment>\n float fz = abs(dot(normalize(normal), normalize(vViewPosition)));\n totalEmissiveRadiance *= 0.2 + 1.1*pow(fz, 1.4);\n diffuseColor.rgb *= 0.55 + 0.45*fz;\n float ca = sin(vLoc.y*15.0 + uTime*1.3 + sin(vLoc.x*22.0 + uTime*0.8)*1.6) * sin(vLoc.z*19.0 - uTime*1.1 + vLoc.y*6.0);\n totalEmissiveRadiance *= 0.8 + 0.5*pow(abs(ca), 2.4);',
        );
      }
      : (sh) => {
        sh.fragmentShader = sh.fragmentShader.replace(
          '#include <emissivemap_fragment>',
          '#include <emissivemap_fragment>\n float fz = abs(dot(normalize(normal), normalize(vViewPosition)));\n totalEmissiveRadiance *= 0.2 + 1.1*pow(fz, 1.4);\n diffuseColor.rgb *= 0.55 + 0.45*fz;',
        );
      };
    const liquid = new THREE.Mesh(lg, liqMat);
    liquid.position.y = 0.05;
    root.add(liquid);

    // parts that materialise after the glass forms
    const partMats = [];
    const reg = (m) => { partMats.push(m); return m; };
    const lh = sp.l1 - sp.l0;
    const labelMat = new THREE.MeshPhysicalMaterial({
      map: sp.tex, emissiveMap: sp.tex, emissive: 0xffffff, emissiveIntensity: 0.34,
      roughness: 0.38, clearcoat: 0.6, clearcoatRoughness: 0.12, envMap: sp.env, envMapIntensity: 0.45,
    });
    const label = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.004, R + 0.004, lh, 160, 1, true, -sp.arc / 2, sp.arc), labelMat);
    label.position.y = sp.l0 + lh / 2;
    root.add(label);

    const top = new THREE.Group();
    root.add(top);
    const collarMat = reg(sp.collar === 'gold'
      ? new THREE.MeshPhysicalMaterial({
        color: 0xd6a64f, metalness: 1, roughness: 0.2, clearcoat: 0.5, envMap: sp.env, envMapIntensity: 1.7,
      })
      : new THREE.MeshPhysicalMaterial({
        color: 0xf3a7b6, emissive: 0x3a0812, emissiveIntensity: 0.8, roughness: 0.12, clearcoat: 1, clearcoatRoughness: 0.03,
        sheen: 0.5, sheenColor: new THREE.Color(0xffd0da), envMap: envStage, envMapIntensity: 1.1,
      }));
    const collar = new THREE.Mesh(new THREE.CylinderGeometry(sp.collarR, sp.collarR, sp.collarH, 96), collarMat);
    collar.position.y = sp.collarH / 2;
    top.add(collar);
    for (const y of [0.012, sp.collarH - 0.012]) {
      const rim = new THREE.Mesh(new THREE.TorusGeometry(sp.collarR, 0.008, 12, 96), collarMat);
      rim.rotation.x = Math.PI / 2;
      rim.position.y = y;
      top.add(rim);
    }

    // clear overcap: back faces drawn first, then the front, so the pump shows through
    const capG = new THREE.Group();
    capG.position.y = sp.collarH;
    top.add(capG);
    const capMat = reg(new THREE.MeshPhysicalMaterial({
      color: 0xf4f7fa, roughness: 0.05, transparent: true, opacity: 0.3, clearcoat: 1, clearcoatRoughness: 0.02,
      envMap: sp.env, envMapIntensity: 2.2, side: THREE.FrontSide, depthWrite: false,
    }));
    capMat.userData.max = 0.3;
    const capBackMat = reg(capMat.clone());
    capBackMat.side = THREE.BackSide;
    capBackMat.userData.max = 0.18;
    capBackMat.envMapIntensity = 1.2;
    const capGeo = new THREE.CylinderGeometry(sp.capR, sp.capR, sp.capH, 96);
    const capBack = new THREE.Mesh(capGeo, capBackMat);
    capBack.position.y = sp.capH / 2;
    capBack.renderOrder = 4;
    capG.add(capBack);
    const cap = new THREE.Mesh(capGeo, capMat);
    cap.position.y = sp.capH / 2;
    cap.renderOrder = 6;
    capG.add(cap);
    const capRim = new THREE.Mesh(new THREE.TorusGeometry(sp.capR - 0.006, 0.006, 10, 96), capMat);
    capRim.rotation.x = Math.PI / 2;
    capRim.position.y = sp.capH;
    capRim.renderOrder = 6;
    capG.add(capRim);

    // pump: domed actuator with a nozzle, on a stem
    const pumpMat = reg(new THREE.MeshPhysicalMaterial({
      color: 0xe9ecef, roughness: 0.45, clearcoat: 0.3, emissive: 0x202226, envMap: envStage, envMapIntensity: 0.55,
    }));
    const ar = sp.capR * 0.6;
    const ah = sp.capH * 0.36;
    const ap = [];
    for (let i = 0; i <= 4; i += 1) ap.push(new THREE.Vector2(0.001 + ((ar - 0.001) * i) / 4, 0));
    for (let i = 1; i <= 6; i += 1) ap.push(new THREE.Vector2(ar, (ah * 0.75 * i) / 6));
    for (let i = 1; i <= 8; i += 1) {
      const a = ((Math.PI / 2) * i) / 8;
      ap.push(new THREE.Vector2(0.001 + (ar - 0.001) * Math.cos(a), ah * 0.75 + ah * 0.25 * Math.sin(a)));
    }
    const act = new THREE.Mesh(new THREE.LatheGeometry(ap, 64), pumpMat);
    act.position.y = sp.capH * 0.42;
    capG.add(act);
    const nozzle = new THREE.Mesh(new THREE.CircleGeometry(ar * 0.16, 24), reg(new THREE.MeshBasicMaterial({ color: 0x2a2c30 })));
    nozzle.position.set(0, sp.capH * 0.42 + ah * 0.55, ar + 0.0015);
    capG.add(nozzle);
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.018, sp.capH * 0.45, 24), pumpMat);
    stem.position.y = sp.capH * 0.22;
    capG.add(stem);

    function fade(m, a) {
      if (m.userData.max !== undefined) {
        m.opacity = a * m.userData.max;
        return;
      }
      const tr = a < 0.999;
      if (m.transparent !== tr) {
        m.transparent = tr;
        m.needsUpdate = true;
      }
      m.opacity = a;
      m.depthWrite = !tr || a > 0.5;
    }

    // Turntable lighting: every lit material dims with the studio lights,
    // from the strength it was authored with.
    let lightMats = null;
    function setLight(light, rim) {
      if (!lightMats) {
        lightMats = new Set();
        root.traverse((o) => { if (o.isMesh && o.material.envMapIntensity !== undefined) lightMats.add(o.material); });
        for (const m of lightMats) {
          m.userData.baseEnv = m.envMapIntensity;
          m.userData.baseEm = m.emissiveIntensity || 0;
        }
      }
      for (const m of lightMats) {
        m.envMapIntensity = m.userData.baseEnv * (0.06 + 0.94 * light);
        if (m.emissiveIntensity !== undefined) m.emissiveIntensity = m.userData.baseEm * (0.05 + 0.95 * light);
      }
      glassMat.envMapIntensity = glassMat.userData.baseEnv * (0.2 + 0.8 * light);
      glassMat.userData.u.uRim.value = rim;
      labelMat.emissiveIntensity = 0.34 * light;
      liqMat.emissiveIntensity = 1.1 * (0.2 + 0.8 * light);
    }

    return {
      root,
      /** Switch every fading material to its half-faded (transparent) variant, for warm(). */
      prime() {
        for (const m of partMats) fade(m, 0.5);
        fade(labelMat, 0.5);
      },
      update(s) { // s: show, tint, fill, parts, label, rotY (+ light, rim on the turntable)
        root.visible = s.show;
        tintGlass(glassMat, sp, s.tint);
        liquid.visible = s.fill > 0.002;
        liquid.scale.y = Math.max(s.fill, 0.001);
        liqMat.emissiveIntensity = 1.1 * Math.min(1, s.fill * 3);
        top.visible = s.parts > 0.002;
        for (const m of partMats) fade(m, s.parts);
        top.position.y = H + Math.pow(1 - s.parts, 3) * 0.22;
        capG.rotation.y = (1 - s.parts) * 1.8;
        label.visible = s.label > 0.002;
        fade(labelMat, s.label);
        if (s.light !== undefined) setLight(s.light, s.rim);
        root.rotation.y = s.rotY;
      },
    };
  }

  /* ---------------- liquid: floating droplet → fed by a poured stream → lands → rises into the bottle ---------------- */
  /** Deformable surface of revolution: N rings of M vertices, rewritten every frame. */
  function surf(N, M, mat) {
    const geo = new THREE.BufferGeometry();
    const pos = new Float32Array(N * M * 3);
    const nrm = new Float32Array(N * M * 3);
    const idx = [];
    for (let i = 0; i < N - 1; i += 1) {
      for (let j = 0; j < M; j += 1) {
        const a = i * M + j;
        const b = i * M + ((j + 1) % M);
        const c = (i + 1) * M + j;
        const d = (i + 1) * M + ((j + 1) % M);
        idx.push(a, b, c, b, d, c);
      }
    }
    const posAttr = new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage);
    const nrmAttr = new THREE.BufferAttribute(nrm, 3).setUsage(THREE.DynamicDrawUsage);
    geo.setAttribute('position', posAttr);
    geo.setAttribute('normal', nrmAttr);
    geo.setIndex(idx);
    const mesh = new THREE.Mesh(geo, mat);
    mesh.frustumCulled = false;
    const sn = new Float32Array(M);
    const cs = new Float32Array(M);
    for (let j = 0; j < M; j += 1) {
      sn[j] = Math.sin((j / M) * Math.PI * 2);
      cs[j] = Math.cos((j / M) * Math.PI * 2);
    }

    // Normals straight from the grid (central differences along the ring
    // and along the profile) instead of computeVertexNormals(), which walks
    // every triangle through Vector3s and was the bulk of the per-frame
    // cost. Same orientation as the triangle winding above.
    function normals() {
      for (let i = 0; i < N; i += 1) {
        const row = i * M;
        const below = (i > 0 ? i - 1 : i) * M;
        const above = (i < N - 1 ? i + 1 : i) * M;
        for (let j = 0; j < M; j += 1) {
          const a = (row + (j + 1 === M ? 0 : j + 1)) * 3;
          const b = (row + (j === 0 ? M - 1 : j - 1)) * 3;
          const c = (above + j) * 3;
          const d = (below + j) * 3;
          const ux = pos[a] - pos[b]; const uy = pos[a + 1] - pos[b + 1]; const uz = pos[a + 2] - pos[b + 2];
          const vx = pos[c] - pos[d]; const vy = pos[c + 1] - pos[d + 1]; const vz = pos[c + 2] - pos[d + 2];
          const nx = uy * vz - uz * vy;
          const ny = uz * vx - ux * vz;
          const nz = ux * vy - uy * vx;
          const l = Math.sqrt(nx * nx + ny * ny + nz * nz) || 1;
          const k = (row + j) * 3;
          nrm[k] = nx / l; nrm[k + 1] = ny / l; nrm[k + 2] = nz / l;
        }
      }
      // The end rings collapse to a point (so their ring tangent vanishes):
      // they take the normals of the ring next to them.
      nrm.copyWithin(0, M * 3, M * 6);
      nrm.copyWithin((N - 1) * M * 3, (N - 2) * M * 3, (N - 1) * M * 3);
    }

    return {
      mesh, geo, pos, N, M, sn, cs,
      done() {
        normals();
        posAttr.needsUpdate = true;
        nrmAttr.needsUpdate = true;
      },
    };
  }

  const PX = 0.56; // bottle / platform centres at x = ±PX
  const PPR = 0.46; // platform radius
  const PPH = 0.16; // platform height
  const mirror = new THREE.Group(); // everything reflected in the floor
  mirror.scale.y = -1;

  function makeDrop(sp, side, ph) {
    // the bottle profile resampled to one point per ring, so the blob can morph into it
    const pts = bottleProfile(sp);
    const L = [0];
    for (let j = 1; j < pts.length; j += 1) L.push(L[j - 1] + pts[j].distanceTo(pts[j - 1]));
    const N = 130;
    const M = 96;
    const Cr = new Float32Array(N);
    const Cy = new Float32Array(N);
    for (let i = 0; i < N; i += 1) {
      const d = (i / (N - 1)) * L[L.length - 1];
      let j = 0;
      while (j < L.length - 2 && L[j + 1] < d) j += 1;
      const f = (d - L[j]) / Math.max(1e-6, L[j + 1] - L[j]);
      Cr[i] = lerp(pts[j].x, pts[j + 1].x, f);
      Cy[i] = lerp(pts[j].y, pts[j + 1].y, f);
    }
    const mat = makeGlassMat(sp);
    mat.ior = 1.36;
    mat.thickness = 0.5;
    tintGlass(mat, sp, 1);
    const blob = surf(N, M, mat);
    const stream = surf(120, 28, mat);
    const sn2 = new Float32Array(M);
    const cs2 = new Float32Array(M);
    const sn3 = new Float32Array(M);
    const cs3 = new Float32Array(M);
    for (let j = 0; j < M; j += 1) {
      const phi = (j / M) * Math.PI * 2;
      sn2[j] = Math.sin(2 * phi); cs2[j] = Math.cos(2 * phi);
      sn3[j] = Math.sin(3 * phi); cs3[j] = Math.cos(3 * phi);
    }
    const group = new THREE.Group();
    group.add(blob.mesh, stream.mesh);
    scene.add(group);
    const mGroup = new THREE.Group();
    const bM = new THREE.Mesh(blob.geo, mat);
    const sM = new THREE.Mesh(stream.geo, mat);
    bM.frustumCulled = false;
    sM.frustumCulled = false;
    mGroup.add(bM, sM);
    mirror.add(mGroup);
    const YF = 1.02; // float height
    const T_HIT = 1.72; // the stream reaches the droplet
    const T_LAND = 2.9; // the droplet lands on the floor

    function update(t) {
      const vis = t < 3.97;
      group.visible = vis;
      mGroup.visible = vis;
      if (!vis) return;
      group.position.x = lerp(side * 0.62, side * PX, eIOS(lin(1.8, 2.95, t)));
      mGroup.position.copy(group.position);
      const glow = ss(0.05, 0.8, t);
      mat.userData.u.uRim.value = glow;
      mat.envMapIntensity = 1.7 * glow;

      // ----- blob -----
      const wAB = ss(T_HIT - 0.02, T_HIT + 0.22, t); // teardrop reshapes as the stream joins it
      const vol = ss(T_HIT, 2.75, t); // grows while being fed
      const h = lerp(0.44, 0.94, vol);
      const r0 = lerp(0.168, 0.24, vol);
      const yb = lerp(YF - 0.2, 0.0, eIn(lin(1.95, T_LAND, t))); // falls and lands on the floor
      const dH = t - T_HIT;
      const hit = dH > 0 ? Math.exp(-1.5 * dH) : 0;
      const dl = t - T_LAND;
      const sq = dl > 0 ? 0.16 * Math.exp(-4.2 * dl) * Math.cos(10 * dl) : 0;
      const feed = ss(T_HIT, T_HIT + 0.1, t) * (1 - ss(2.5, 2.85, t));
      const front = eIO(lin(2.95, 3.95, t)) * 1.35; // bottle forms from the base upward
      const idle = 1 - wAB;
      const P = blob.pos;
      // sin(2φ + 5t + ph) and sin(3φ − 4t) by angle addition, so the inner
      // loop needs no trig of its own
      const cA = Math.cos(5 * t + ph);
      const sA = Math.sin(5 * t + ph);
      const cB = Math.cos(4 * t);
      const sB = Math.sin(4 * t);
      for (let i = 0; i < N; i += 1) {
        const u = i / (N - 1);
        const th = Math.PI * (1 - u);
        const rA = 0.16 * Math.sin(th) * Math.pow(Math.sin(th / 2), 1.3) * (1 + 0.03 * Math.sin(t * 3.2 + ph) * Math.cos(2 * th));
        const yA = YF + 0.21 * Math.cos(th) * (1 + 0.025 * Math.sin(t * 3.2 + ph + 1.3));
        const s = (1 - Math.cos(Math.PI * u)) / 2;
        let rB = r0 * Math.sin(Math.PI * u) * (1 + 0.24 * (0.5 - s)) * (1 + sq * 0.7 * (1 - s));
        rB *= 1 + 0.03 * Math.sin(13 * (1 - s) - 11 * dH) * hit * (dH > 0 ? 1 : 0) + 0.012 * Math.sin(15 * (1 - s) - 14 * t + ph) * feed;
        rB = Math.max(rB, 0.03 * feed * ss(0.86, 1.0, u));
        const yB = yb + h * (1 - sq) * s + 0.04 * feed * ss(0.9, 1, u);
        const w = sstep(clamp((front - u) / 0.35));
        const rr0 = lerp(lerp(rA, rB, wAB), Cr[i], w);
        const y = lerp(lerp(yA, yB, wAB), Cy[i], w);
        const live = 1 - w;
        const sway = live * wAB * Math.sin(Math.PI * u) * (0.03 * Math.sin(t * 2.3 + ph) + 0.02 * Math.sin(t * 3.7 + ph * 2));
        const swz = live * wAB * Math.sin(Math.PI * u) * 0.02 * Math.cos(t * 2.9 + ph);
        const wob = live * (0.045 * hit * wAB + 0.012 * idle) * (1 - ss(0.8, 1, u));
        for (let j = 0; j < M; j += 1) {
          const r = rr0 * (1 + wob * (sn2[j] * cA + cs2[j] * sA) + 0.4 * wob * (sn3[j] * cB - cs3[j] * sB));
          const k = (i * M + j) * 3;
          P[k] = sway + r * blob.sn[j];
          P[k + 1] = y;
          P[k + 2] = swz + r * blob.cs[j];
        }
      }
      blob.done();
      const rot = idle * (t * 0.5 * side);
      blob.mesh.rotation.y = rot;
      bM.rotation.y = rot;
      blob.mesh.rotation.z = 0.1 * Math.sin(t * 0.8 + ph) * idle;
      bM.rotation.z = blob.mesh.rotation.z;

      // ----- poured stream -----
      const blobTop = lerp(YF + 0.21, yb + h * (1 - sq) + 0.04 * feed, wAB);
      const detach = lin(2.42, 2.86, t);
      const yBot = t < T_HIT ? lerp(4.0, YF + 0.19, eIn(lin(1.25, T_HIT, t))) : blobTop - 0.07;
      const yTop = t < 2.42 ? 4.0 : lerp(4.0, blobTop - 0.02, eIn(detach));
      const sOn = t > 1.25 && t < 2.86 && yTop - yBot > 0.015;
      stream.mesh.visible = sOn;
      sM.visible = sOn;
      if (sOn) {
        const S = stream.pos;
        const SN = stream.N;
        const SM = stream.M;
        const len = yTop - yBot;
        const thin = 1 - 0.45 * ss(0, 1, detach);
        const amp = 0.1 + 0.55 * detach;
        for (let i = 0; i < SN; i += 1) {
          const u = i / (SN - 1);
          const y = yBot + len * u;
          let r = 0.028 * thin * (1 + amp * Math.sin(y * 44 + t * 18 + ph));
          if (t < T_HIT) r += 0.02 * Math.exp(-(y - yBot) / 0.035); // falling tip bead
          r *= Math.sqrt(clamp((y - yBot) / 0.014)) * (t >= 2.42 ? Math.sqrt(clamp((yTop - y) / 0.014)) : 1);
          const cx = 0.03 * Math.sin(y * 1.9 + t * 1.4 + ph) * clamp((y - yBot) / 1.5);
          const cz = 0.02 * Math.cos(y * 1.6 + t * 1.1 + ph) * clamp((y - yBot) / 1.5);
          for (let j = 0; j < SM; j += 1) {
            const k = (i * SM + j) * 3;
            S[k] = cx + r * stream.sn[j];
            S[k + 1] = y;
            S[k + 2] = cz + r * stream.cs[j];
          }
        }
        stream.done();
      }
    }
    return { group, update };
  }

  /* ---------------- scene assembly ---------------- */
  const red = makeBottle(SPEC.red);
  const blue = makeBottle(SPEC.blue);
  const redM = makeBottle(SPEC.red);
  const blueM = makeBottle(SPEC.blue);
  scene.add(red.root, blue.root);
  scene.add(mirror);
  mirror.add(redM.root, blueM.root);
  const dropR = turntable ? null : makeDrop(SPEC.red, -1, 0.0);
  const dropB = turntable ? null : makeDrop(SPEC.blue, 1, 1.9);
  // Flat overlays left out of the turntable's depth-of-field depth pass.
  const noDepth = [];

  // reflections fade with depth below the glossy floor (stacked darkening layers)
  const floorGeo = new THREE.PlaneGeometry(90, 90);
  for (const [y, o] of [[-0.06, 0.22], [-0.16, 0.26], [-0.32, 0.3], [-0.55, 0.35], [-0.9, 0.45], [-1.4, 0.6]]) {
    const p = new THREE.Mesh(floorGeo, new THREE.MeshBasicMaterial({ color: 0, transparent: true, opacity: o, depthWrite: false }));
    p.rotation.x = -Math.PI / 2;
    p.position.y = y;
    p.renderOrder = 1;
    scene.add(p);
    noDepth.push(p);
  }

  // glossy black studio floor (reflections come from the mirrored scene beneath it)
  function radialTex(stops) {
    const c = document.createElement('canvas');
    c.width = 256;
    c.height = 256;
    const g = c.getContext('2d');
    const gr = g.createRadialGradient(128, 128, 0, 128, 128, 128);
    for (const [o, col] of stops) gr.addColorStop(o, col);
    g.fillStyle = gr;
    g.fillRect(0, 0, 256, 256);
    return keep(new THREE.CanvasTexture(c));
  }
  const floor = new THREE.Mesh(floorGeo, new THREE.MeshBasicMaterial({
    color: 0x000000, transparent: true, depthWrite: false,
    alphaMap: radialTex([[0, 'rgb(170,170,170)'], [0.03, 'rgb(190,190,190)'], [0.08, 'rgb(235,235,235)'], [0.2, '#fff'], [1, '#fff']]),
  }));
  floor.rotation.x = -Math.PI / 2;
  floor.renderOrder = 2;
  scene.add(floor);

  // coloured light pools on the floor, and haze in the dark behind
  const softTex = radialTex([[0, 'rgba(255,255,255,1)'], [0.35, 'rgba(255,255,255,0.35)'], [1, 'rgba(255,255,255,0)']]);
  function glowPlane(col, size, flat) {
    const opts = { map: softTex, color: col.clone(), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false };
    let p;
    if (flat) {
      p = new THREE.Mesh(new THREE.PlaneGeometry(size, size), new THREE.MeshBasicMaterial(opts));
      p.rotation.x = -Math.PI / 2;
    } else {
      p = new THREE.Sprite(new THREE.SpriteMaterial(opts));
      p.scale.set(size, size, 1);
    }
    p.renderOrder = 3;
    scene.add(p);
    return p;
  }
  const floorGlowR = glowPlane(PINK, 1.5, true);
  const floorGlowB = glowPlane(CYAN, 1.5, true);
  floorGlowR.position.y = 0.002;
  floorGlowB.position.y = 0.002;
  const hazeR = glowPlane(PINK.clone().multiplyScalar(0.5), 3.2, false);
  const hazeB = glowPlane(CYAN.clone().multiplyScalar(0.5), 3.2, false);
  hazeR.position.set(-PX - 0.3, 1.2, -2.2);
  hazeB.position.set(PX + 0.3, 1.2, -2.2);
  noDepth.push(floorGlowR, floorGlowB, hazeR, hazeB);
  // Turntable only: a wide glow behind the pair, tinted along `tones`.
  const toneCols = tones.map((c) => new THREE.Color(c).multiplyScalar(0.5));
  const hazeTone = turntable && toneCols.length ? glowPlane(toneCols[0], 4.2, false) : null;
  if (hazeTone) {
    hazeTone.position.set(0, 1.15, -2.8);
    noDepth.push(hazeTone);
  }

  // two display platforms, one LED ring each
  function makePlatform(col, parent) {
    const g = new THREE.Group();
    parent.add(g);
    // envMap set explicitly: newer three ignores envMapIntensity on the scene.environment fallback
    const mat = new THREE.MeshPhysicalMaterial({
      color: 0x020203, roughness: 0.07, clearcoat: 1, clearcoatRoughness: 0.02, envMap: envStage, envMapIntensity: 0.8,
    });
    const body = new THREE.Mesh(new THREE.CylinderGeometry(PPR, PPR, PPH, 128), mat);
    body.position.y = PPH / 2;
    g.add(body);
    const bevel = new THREE.Mesh(new THREE.TorusGeometry(PPR - 0.008, 0.008, 12, 128), mat);
    bevel.rotation.x = Math.PI / 2;
    bevel.position.y = PPH - 0.008;
    g.add(bevel);
    const leds = [];
    for (const [tube, k, op, add] of [[0.009, 3.0, 1, false], [0.03, 1.4, 0.35, true], [0.08, 1.0, 0.12, true]]) {
      const m = new THREE.MeshBasicMaterial({
        color: col.clone().multiplyScalar(k), transparent: true, opacity: 0,
        blending: add ? THREE.AdditiveBlending : THREE.NormalBlending, depthWrite: false,
      });
      m.userData.base = op;
      leds.push(m);
      const ring = new THREE.Mesh(new THREE.TorusGeometry(PPR + 0.004, tube, 12, 160), m);
      ring.rotation.x = Math.PI / 2;
      ring.position.y = PPH * 0.42;
      g.add(ring);
    }
    return { g, mat, leds };
  }
  const platR = makePlatform(PINK, scene);
  const platB = makePlatform(CYAN, scene);
  const platRM = makePlatform(PINK, mirror);
  const platBM = makePlatform(CYAN, mirror);
  platR.g.position.x = -PX;
  platRM.g.position.x = -PX;
  platB.g.position.x = PX;
  platBM.g.position.x = PX;

  /* ---------------- bloom + tone-mapping pipeline ---------------- */
  // Multisampled HDR scene target (this is where the antialiasing happens).
  const sceneRT = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4, depthBuffer: true, stencilBuffer: false });
  const rtOpts = { type: THREE.HalfFloatType, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false };
  const LEVELS = 5;
  const rtA = [];
  const rtB = [];
  for (let i = 0; i < LEVELS; i += 1) {
    rtA.push(new THREE.WebGLRenderTarget(1, 1, rtOpts));
    rtB.push(new THREE.WebGLRenderTarget(1, 1, rtOpts));
  }
  const quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  const quadScene = new THREE.Scene();
  const quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2));
  quadScene.add(quad);
  const brightMat = new THREE.ShaderMaterial({
    uniforms: { tex: { value: null }, thr: { value: 0.85 } }, vertexShader: QUAD_VS, depthTest: false, depthWrite: false,
    fragmentShader: 'uniform sampler2D tex; uniform float thr; varying vec2 vUv; void main(){ vec3 c = texture2D(tex, vUv).rgb; float l = max(c.r, max(c.g, c.b)); gl_FragColor = vec4(c * smoothstep(thr, thr + 0.9, l), 1.0); }',
  });
  const blurMat = new THREE.ShaderMaterial({
    uniforms: { tex: { value: null }, dir: { value: new THREE.Vector2() } }, vertexShader: QUAD_VS, depthTest: false, depthWrite: false,
    fragmentShader: 'uniform sampler2D tex; uniform vec2 dir; varying vec2 vUv; void main(){ vec3 s = texture2D(tex, vUv).rgb * 0.227; s += (texture2D(tex, vUv + dir*1.385).rgb + texture2D(tex, vUv - dir*1.385).rgb) * 0.316; s += (texture2D(tex, vUv + dir*3.231).rgb + texture2D(tex, vUv - dir*3.231).rgb) * 0.070; gl_FragColor = vec4(s, 1.0); }',
  });
  let compFS = `
      uniform sampler2D tScene, b0, b1, b2, b3, b4; uniform float strength, exposure, time; varying vec2 vUv;
      vec3 RRTAndODTFit(vec3 v){ vec3 a = v*(v+0.0245786)-0.000090537; vec3 b = v*(0.983729*v+0.4329510)+0.238081; return a/b; }
      vec3 aces(vec3 c){ const mat3 I = mat3(vec3(0.59719,0.07600,0.02840), vec3(0.35458,0.90834,0.13383), vec3(0.04823,0.01566,0.83777));
        const mat3 O = mat3(vec3(1.60475,-0.10208,-0.00327), vec3(-0.53108,1.10813,-0.07276), vec3(-0.07367,-0.00605,1.07602));
        c *= exposure/0.6; c = I*c; c = RRTAndODTFit(c); c = O*c; return clamp(c, 0.0, 1.0); }
      vec3 toSRGB(vec3 c){ return mix(pow(c, vec3(0.41666))*1.055 - 0.055, c*12.92, vec3(lessThanEqual(c, vec3(0.0031308)))); }
      void main(){
        vec3 c = texture2D(tScene, vUv).rgb;
        vec3 bl = texture2D(b0,vUv).rgb*0.9 + texture2D(b1,vUv).rgb*0.8 + texture2D(b2,vUv).rgb*0.7 + texture2D(b3,vUv).rgb*0.6 + texture2D(b4,vUv).rgb*0.5;
        c += bl * strength * 0.35;
        vec2 d = vUv - 0.5; c *= 1.0 - 0.55*smoothstep(0.35, 0.85, length(d));
        vec3 o = toSRGB(aces(c));
        float n = fract(sin(dot(gl_FragCoord.xy + time*61.7, vec2(12.9898, 78.233))) * 43758.5453) - 0.5;
        gl_FragColor = vec4(o + n*0.022, 1.0);
      }`;

  // Turntable depth of field: a depth pass (overlays hidden) gives each
  // pixel's distance, and the scene is mixed toward two blurred copies of
  // itself the further it sits from the focus distance.
  const dof = turntable ? {
    depthRT: new THREE.WebGLRenderTarget(1, 1, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter }),
    a: [new THREE.WebGLRenderTarget(1, 1, rtOpts), new THREE.WebGLRenderTarget(1, 1, rtOpts)],
    b: [new THREE.WebGLRenderTarget(1, 1, rtOpts), new THREE.WebGLRenderTarget(1, 1, rtOpts)],
    depthMat: new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking }),
    on: true,
  } : null;
  if (dof) {
    compFS = compFS
      .replace('uniform sampler2D tScene, b0, b1, b2, b3, b4;', `#include <packing>
      uniform sampler2D tScene, b0, b1, b2, b3, b4, tDepth, d1, d2; uniform float focus, aperture, cNear, cFar;`)
      .replace('vec3 c = texture2D(tScene, vUv).rgb;', `float dz = -perspectiveDepthToViewZ(unpackRGBAToDepth(texture2D(tDepth, vUv)), cNear, cFar);
        float coc = clamp(abs(dz - focus)/max(focus*0.32, 0.01), 0.0, 1.0) * aperture;
        vec3 c = texture2D(tScene, vUv).rgb;
        c = mix(c, texture2D(d1, vUv).rgb, smoothstep(0.0, 0.5, coc));
        c = mix(c, texture2D(d2, vUv).rgb, smoothstep(0.5, 1.0, coc));`);
  }
  const compMat = new THREE.ShaderMaterial({
    uniforms: {
      tScene: { value: sceneRT.texture }, b0: { value: rtA[0].texture }, b1: { value: rtA[1].texture }, b2: { value: rtA[2].texture },
      b3: { value: rtA[3].texture }, b4: { value: rtA[4].texture }, strength: { value: 0.85 }, exposure: { value: 1.0 }, time: { value: 0 },
      ...(dof && {
        tDepth: { value: dof.depthRT.texture }, d1: { value: dof.a[0].texture }, d2: { value: dof.a[1].texture },
        focus: { value: 8 }, aperture: { value: 0.18 }, cNear: { value: camera.near }, cFar: { value: camera.far },
      }),
    },
    vertexShader: QUAD_VS, depthTest: false, depthWrite: false,
    fragmentShader: compFS,
  });
  const BLACK = new THREE.Color(0);
  const WHITE = new THREE.Color(1, 1, 1);
  function pass(mat, target) {
    quad.material = mat;
    renderer.setRenderTarget(target);
    renderer.render(quadScene, quadCam);
  }
  function renderFrame() {
    compMat.uniforms.time.value = (compMat.uniforms.time.value + 1) % 1000;
    renderer.setRenderTarget(sceneRT);
    renderer.render(scene, camera);
    if (dof) {
      // Low quality tiers skip the depth pass; aperture 0 means nothing blurs.
      compMat.uniforms.aperture.value = dof.on ? 0.18 : 0;
      if (dof.on) {
        for (const o of noDepth) { o.userData.v = o.visible; o.visible = false; }
        scene.overrideMaterial = dof.depthMat;
        scene.background = WHITE;
        renderer.setRenderTarget(dof.depthRT);
        renderer.render(scene, camera);
        scene.overrideMaterial = null;
        scene.background = BLACK;
        for (const o of noDepth) o.visible = o.userData.v;
        let src = sceneRT.texture;
        for (let i = 0; i < 2; i += 1) {
          const k = 1.4 + i;
          blurMat.uniforms.tex.value = src;
          blurMat.uniforms.dir.value.set(k / dof.a[i].width, 0);
          pass(blurMat, dof.b[i]);
          blurMat.uniforms.tex.value = dof.b[i].texture;
          blurMat.uniforms.dir.value.set(0, k / dof.a[i].height);
          pass(blurMat, dof.a[i]);
          src = dof.a[i].texture;
        }
      }
    }
    brightMat.uniforms.tex.value = sceneRT.texture;
    pass(brightMat, rtA[0]);
    for (let i = 0; i < LEVELS; i += 1) {
      blurMat.uniforms.tex.value = i === 0 ? rtA[0].texture : rtA[i - 1].texture;
      blurMat.uniforms.dir.value.set(1 / rtA[i].width, 0);
      pass(blurMat, rtB[i]);
      blurMat.uniforms.tex.value = rtB[i].texture;
      blurMat.uniforms.dir.value.set(0, 1 / rtA[i].height);
      pass(blurMat, rtA[i]);
    }
    pass(compMat, null);
  }

  /* ---------------- timeline ---------------- */
  function bottleState(t, r0, r1, r2) {
    return {
      show: t >= 3.95, tint: 1 - ss(4.2, 5.3, t), fill: ss(3.97, 5.0, t), parts: ss(4.6, 5.4, t), label: ss(5.0, 5.8, t),
      rotY: t < 8 ? lerp(r0, r1, eIOS(lin(3.95, 7.6, t))) : lerp(r1, r2, eIOS(lin(8, 10, t))),
    };
  }

  /** Story: pose everything for scene time `t` (0 to SCENE_END). */
  function poseStory(t) {
    dropR.update(t);
    dropB.update(t);
    const rs = bottleState(t, -1.25, 0.14, -0.1);
    const bs = bottleState(t, 1.25, -0.14, 0.1);
    const rise = eIO(lin(7.0, 8.0, t));
    const py = PPH * rise;
    red.root.position.set(-PX, py, 0);
    blue.root.position.set(PX, py, 0);
    red.update(rs);
    blue.update(bs);
    redM.root.position.copy(red.root.position);
    blueM.root.position.copy(blue.root.position);
    redM.update(rs);
    blueM.update(bs);
    for (const p of [platR, platB, platRM, platBM]) {
      p.g.visible = rise > 0.001;
      p.g.scale.y = Math.max(rise, 0.001);
      p.mat.envMapIntensity = 0.8 * rise;
      for (const m of p.leds) m.opacity = m.userData.base * ss(7.2, 8.1, t);
    }
    const g = ss(0.1, 1.2, t);
    floorGlowR.position.x = dropR.group.visible ? dropR.group.position.x : -PX;
    floorGlowB.position.x = dropB.group.visible ? dropB.group.position.x : PX;
    floorGlowR.material.opacity = 0.22 * g + 0.22 * ss(7.2, 8.2, t);
    floorGlowB.material.opacity = floorGlowR.material.opacity;
    hazeR.material.opacity = 0.17 * g;
    hazeB.material.opacity = hazeR.material.opacity;

    const az = curve(CAM.az, t);
    const el = curve(CAM.el, t);
    const d = curve(CAM.dist, t);
    const ty = curve(CAM.ty, t);
    camera.position.set(d * Math.sin(az) * Math.cos(el), ty + d * Math.sin(el), d * Math.cos(az) * Math.cos(el));
    camera.lookAt(0, ty, 0);
    camera.updateMatrixWorld();
    const sa = -1.3 + t * 0.26;
    const sb = 1.5 - t * 0.22;
    SWEEP.a.value.set(Math.sin(sa), 0.35, Math.cos(sa)).normalize().transformDirection(camera.matrixWorldInverse);
    SWEEP.b.value.set(Math.sin(sb), 0.55, Math.cos(sb)).normalize().transformDirection(camera.matrixWorldInverse);
  }

  /**
   * Turntable: pose the pair `t` seconds after it first came into view,
   * with the backdrop glow at `tone` (0..1) along `tones`. Each bottle
   * turns exactly as in its prototype (same start angle, ease-in and
   * speed), and the two turn together.
   */
  const toneTmp = new THREE.Color();
  function poseTurntable(t, tone) {
    const light = ss(0.5, 2.2, t); // studio lights come up
    const rim = 0.95 * ss(0.2, 1.0, t) * (1 - 0.72 * ss(1.2, 2.4, t)); // rim glow flares, then settles
    const led = ss(0.0, 0.9, t);
    SWEEP.i.value = ss(0.4, 1.4, t);
    LIQT.value = t;
    const s = { show: true, tint: 0, fill: 1, parts: 1, label: 1, light, rim, rotY: -0.9 + spinAngle(t) };
    red.root.position.set(-PX, PPH, 0);
    blue.root.position.set(PX, PPH, 0);
    redM.root.position.copy(red.root.position);
    blueM.root.position.copy(blue.root.position);
    for (const b of [red, blue, redM, blueM]) b.update(s);
    for (const p of [platR, platB, platRM, platBM]) {
      p.g.visible = true;
      p.g.scale.y = 1;
      p.mat.envMapIntensity = 0.8 * (0.15 + 0.85 * light);
      for (const m of p.leds) m.opacity = m.userData.base * led;
    }
    floorGlowR.position.x = -PX;
    floorGlowB.position.x = PX;
    floorGlowR.material.opacity = 0.45 * led;
    floorGlowB.material.opacity = floorGlowR.material.opacity;
    hazeR.material.opacity = 0.2 * light;
    hazeB.material.opacity = hazeR.material.opacity;
    if (hazeTone) {
      const x = clamp(tone) * (toneCols.length - 1);
      const i = Math.min(Math.floor(x), toneCols.length - 2);
      if (i < 0) toneTmp.copy(toneCols[0]);
      else toneTmp.copy(toneCols[i]).lerp(toneCols[i + 1], sstep(x - i));
      hazeTone.material.color.copy(toneTmp);
      hazeTone.material.opacity = 0.16 * light;
    }

    // the camera eases back as the lights come up, then drifts gently;
    // the studio lights stay put, so highlights glide across the turning glass
    const d = lerp(6.5, 7.8, eIOS(lin(0, 2.4, t)));
    const az = 0.07 * Math.sin(t * 0.23);
    const el = 0.075 + 0.015 * Math.sin(t * 0.19);
    const ty = 0.98;
    camera.position.set(d * Math.sin(az) * Math.cos(el), ty + d * Math.sin(el), d * Math.cos(az) * Math.cos(el));
    camera.lookAt(0, ty, 0);
    camera.updateMatrixWorld();
    compMat.uniforms.focus.value = d - 0.22;
    const sa = -0.75 + 0.12 * Math.sin(t * 0.21);
    const sb = 0.95 + 0.1 * Math.sin(t * 0.17 + 1.0);
    SWEEP.a.value.set(Math.sin(sa), 0.35, Math.cos(sa)).normalize().transformDirection(camera.matrixWorldInverse);
    SWEEP.b.value.set(Math.sin(sb), 0.55, Math.cos(sb)).normalize().transformDirection(camera.matrixWorldInverse);
  }

  /** Pose for time `t` (story: scene time; turntable: seconds in view, with `tone`) and draw. */
  function render(t, tone = 0) {
    if (turntable) poseTurntable(t, tone);
    else poseStory(t);
    renderFrame();
  }

  /** Apply a QUALITY tier's scene-side settings (the renderer's are set by the caller). */
  function setQuality(q) {
    if (dof) dof.on = q.dof;
  }

  /**
   * Compile every shader the timeline will need, in the background
   * (KHR_parallel_shader_compile where available). Without this the cap,
   * collar, label and platforms each compiled the first time they came on
   * screen, and again when a fading material turned opaque: a visible
   * hitch partway through the scroll. The next render() restores the
   * proper material states.
   */
  function warm() {
    const opaque = renderer.compileAsync(scene, camera);
    for (const b of [red, blue, redM, blueM]) b.prime();
    const faded = renderer.compileAsync(scene, camera);
    return Promise.all([opaque, faded]);
  }

  /** Square canvas `size` CSS pixels across. */
  function setSize(size) {
    renderer.setSize(size, size, false);
    camera.aspect = 1;
    camera.updateProjectionMatrix();
    const px = Math.floor(size * renderer.getPixelRatio());
    sceneRT.setSize(px, px);
    for (let i = 0; i < LEVELS; i += 1) {
      const w = Math.max(1, px >> (i + 1));
      rtA[i].setSize(w, w);
      rtB[i].setSize(w, w);
    }
    if (dof) {
      dof.depthRT.setSize(Math.max(1, px >> 1), Math.max(1, px >> 1));
      for (let i = 0; i < 2; i += 1) {
        const w = Math.max(1, px >> (i + 1));
        dof.a[i].setSize(w, w);
        dof.b[i].setSize(w, w);
      }
    }
  }

  function dispose() {
    scene.traverse((o) => {
      if (o.isMesh) o.geometry.dispose();
      if (o.isMesh || o.isSprite) o.material.dispose();
    });
    quad.geometry.dispose();
    [brightMat, blurMat, compMat, sceneRT, ...rtA, ...rtB, ...disposables].forEach((x) => x.dispose());
    if (dof) [dof.depthRT, ...dof.a, ...dof.b, dof.depthMat].forEach((x) => x.dispose());
  }

  return { render, warm, setQuality, setSize, dispose };
}

/**
 * The Uriel & Raphael scene on a square canvas.
 *
 * variant="story" (the default): `progress` (anything with .get()
 * returning 0..1, e.g. a Motion scroll value) scrubs the scene from the
 * glowing droplets (0) to both bottles on their platforms (1); on load
 * the droplets first glow in by themselves. Without `progress`, `time`
 * holds one fixed moment (reduced motion uses the final frame). Renders
 * only while on screen and while something is changing.
 *
 * variant="turntable": the pair turns on their platforms continuously
 * while on screen (the clock pauses off screen, so the turn picks up
 * where it left off). `tone` (a 0..1 value with .get()) moves the
 * backdrop glow through `tones` (hex colours).
 *
 * `onReady` fires after the first frame.
 */
export default function PerfumeScene({
  variant = 'story', progress, time = SCENE_END, tone, tones, onReady, className = '',
}) {
  const mountRef = useRef(null);
  const progressRef = useRef(progress);
  progressRef.current = progress;
  const toneRef = useRef(tone);
  toneRef.current = tone;
  const tonesRef = useRef(tones);
  tonesRef.current = tones;
  const readyRef = useRef(onReady);
  readyRef.current = onReady;
  const turntable = variant === 'turntable';

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return undefined;

    let renderer;
    try {
      // No canvas antialiasing: the scene renders into a multisampled target.
      renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' });
    } catch {
      return undefined;
    }
    // Quality steps, dropped one at a time (never raised again, so it
    // can't oscillate) when the frame rate sags while the scene is moving.
    let tier = 0;
    let dirty = true;
    const view = buildScene(renderer, () => { dirty = true; }, { turntable, tones: tonesRef.current ?? [] });
    const applyTier = () => {
      const q = QUALITY[tier];
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, q.dpr));
      renderer.transmissionResolutionScale = q.transmission;
      view.setQuality(q);
    };
    applyTier();
    // The final pass tone-maps and converts to sRGB itself.
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
    mount.appendChild(renderer.domElement);

    view.warm().then(() => { dirty = true; }, () => {});

    const resize = () => {
      view.setSize(Math.max(1, Math.floor(mount.clientWidth)));
      dirty = true;
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(mount);

    const t0 = performance.now();
    const target = () => {
      const p = progressRef.current;
      if (!p) return time;
      // The droplets glow in on their own when the page opens; after that
      // the scroll carries the story on to the platforms.
      const intro = Math.min(SCENE_INTRO, (performance.now() - t0) / 1000);
      return Math.max(intro, lerp(SCENE_INTRO, SCENE_END, clamp(p.get())));
    };
    let current = progressRef.current ? 0 : time;
    let clock = 0; // turntable: seconds rendered on screen
    let frame = 0;
    let first = true;
    let last = 0; // timestamp of the previous rendered frame (0: none in a row)
    let avg = 16.7; // smoothed ms between consecutive rendered frames
    let streak = 0; // consecutive rendered frames behind `avg`

    const tick = (now) => {
      frame = requestAnimationFrame(tick);
      let dt;
      if (turntable) {
        // Always moving: advance the clock by the real frame time, so the
        // turn keeps its exact speed at any refresh rate.
        dt = last ? Math.min(now - last, 100) : 16.7;
        clock += dt / 1000;
        current = clock;
      } else {
        // Ease toward the target (like a scrubbed video with a little lag)
        // rather than jumping frame to frame; once it has caught up and
        // nothing else changed, the last frame simply stays on the canvas.
        // The ease is time-based, so it feels the same at 60 Hz, 120 Hz or
        // when a frame is dropped.
        const goal = target();
        const settled = Math.abs(goal - current) < 1e-4;
        if (settled && !dirty) {
          last = 0;
          return;
        }
        dt = last ? Math.min(now - last, 100) : 16.7;
        current = settled ? goal : current + (goal - current) * (1 - Math.exp(-dt / EASE_MS));
      }
      dirty = false;

      if (last && tier < QUALITY.length - 1) {
        avg += (dt - avg) * 0.1;
        streak += 1;
        if (streak > 45 && avg > SLOW_FRAME_MS) {
          tier += 1;
          applyTier();
          resize();
          streak = 0;
          avg = 16.7;
        }
      }
      last = now;

      view.render(current, toneRef.current?.get() ?? 0);
      if (first) {
        first = false;
        readyRef.current?.();
      }
    };

    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting && !frame) {
        last = 0;
        tick(performance.now());
      } else if (!entry.isIntersecting && frame) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    });
    io.observe(mount);

    return () => {
      io.disconnect();
      ro.disconnect();
      cancelAnimationFrame(frame);
      view.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [time, turntable]);

  return <div ref={mountRef} className={`perfume-scene ${className}`} aria-hidden="true" />;
}
