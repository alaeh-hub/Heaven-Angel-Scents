import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { SCENE_LABEL } from '../content/scents.js';

// Uriel H1 & Raphael A3. variant="story" (the hero) is
// prototype/wings/index.html: a gate of heaven opens in the dark: an oculus ringed in gold, thin beams of light, stars, under
// a halo. The halo splits into two rings that fly down to
// become the platforms' LED rings, each landing with a flash and a
// shockwave across the floor, while the beams and stars dissolve, outermost
// first, into streams of light that build each bottle from its base
// upward behind a glowing gold edge. A glossy black floor reflects it
// all; bloom, a horizontal anamorphic streak, a shallow depth of field
// and ACES with a little film grain finish each frame.
//
// The prototypes looped on three r149 and faded to black between loops;
// here the timeline is driven from outside (the hero scrubs it with the
// scroll) and there is no fade, since the hero fades the canvas in. The
// bottles are the 85 ml white-bottle product: their labels are cut from the
// product renders into static/img/scene/ by scripts/build_scent_images.py.
//
// variant="turntable" is the finished pair from
// prototype/turntable/*.html (one bottle each there, both here): each
// bottle turns on its lit platform, one full turn every 10 s after a
// 1.8 s ease-in, while the studio lights come up, caustics drift through
// the bottles and a shallow depth of field softens the floor.
//
// variant="spray" is prototype/spray/index.html: the same studio, but only
// Raphael A3, overcap off, held at a three-quarter angle on its platform.
// Every 5 s (first at 1.9 s) the actuator presses and a fine mist bursts
// from the nozzle across the frame, slows, widens and fades.
//
// r149 used "legacy" colour handling (hex colours as-is); turning colour
// management off keeps every material looking as authored.
THREE.ColorManagement.enabled = false;

/** Scene time (seconds) of the final frame: both bottles on their platforms. */
export const SCENE_END = 10;
/** Scene time when the clouds and light shafts have opened under the halo. */
export const SCENE_INTRO = 2.2;

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

/* Story camera, [time, value] keys as in prototype/wings/index.html. */
const CAM_KEYS = [0, 3.4, 6.2, 10];
const camKeys = (v) => CAM_KEYS.map((k, i) => [k, v[i]]);
const CAM = {
  ty: camKeys([1.2, 1.12, 1.02, 1.02]),
  dist: camKeys([9.4, 8.6, 8.2, 7.5]),
  az: camKeys([0.14, -0.05, 0.06, 0.0]),
  el: camKeys([0.05, 0.06, 0.09, 0.095]),
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
/** Rendered frames the average needs before it may step down (≈ 1/3 s at 60 Hz). */
const SLOW_STREAK = 20;
/** Averages this slow (≈ 20 fps) skip a tier: one step wouldn't be enough. */
const VERY_SLOW_FRAME_MS = 50;

/**
 * The tier the device has settled on so far, shared by every scene on
 * the page (the hero's and the notes section's), so the second scene
 * starts where the first one landed instead of re-learning it, with the
 * stutter and the render-target rebuilds that come with each step.
 * Touch devices start two steps down: a phone's GPU at 1.75x with depth
 * of field is almost always too slow, and every step down taken while
 * the visitor scrolls is a visible hitch.
 */
let sharedTier = typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches ? 2 : 0;

/* Turntable timing, exactly as in prototype/turntable/*.html. */
const SPIN = (Math.PI * 2) / 10; // one full turn every 10 s
const RAMP = 1.8; // the spin eases in over the first 1.8 s

/** Turntable angle at time t: eases in over RAMP, then a steady SPIN (continuous in speed). */
function spinAngle(t) {
  const x = Math.min(t / RAMP, 1);
  return t < RAMP ? SPIN * RAMP * (x * x * x - (x * x * x * x) / 2) : SPIN * (RAMP / 2 + (t - RAMP));
}

/* Spray timing, exactly as in prototype/spray/index.html. */
const SPRAY_CYCLE = 5; // one spray every 5 s...
const SPRAY_FIRST = 1.9; // ...the first once the lights are up
/** Spray scene time of a still frame mid-burst (its default `time`). */
const SPRAY_STILL = SPRAY_FIRST + 0.45;

// Vertex shader shared by the full-screen post-processing passes.
const QUAD_VS = 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }';

/**
 * Builds the scene and its post-processing on `renderer`. `invalidate`
 * is called when something arrives late (the label images) and the
 * current frame needs drawing again. `variant` is 'story', 'turntable'
 * or 'spray' (all share the studio: caustics, depth of field; the story
 * adds the heaven backdrop and its lens effects); `tones` (CSS colours) tint the turntable's backdrop glow along
 * render()'s `tone` argument.
 */
function buildScene(renderer, invalidate, { variant = 'story', tones = [] } = {}) {
  const turntable = variant === 'turntable';
  const spray = variant === 'spray';
  const story = !turntable && !spray;
  const disposables = [];
  const keep = (x) => { disposables.push(x); return x; };

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x000000);
  const camera = new THREE.PerspectiveCamera(22, 1, 1.2, 120);

  /* ---------------- studio environments ---------------- */
  // `size` is the PMREM cube face size: the scent/stage/gold envs only tint soft
  // reflections, so they are built small (each full-size build stalls the first frame).
  function buildEnv(strips, size = 128) {
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
    const tex = pm.fromScene(env, 0.02, 0.1, 100, { size }).texture;
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
  // The white plastic reflects neutral softboxes (the coloured studio
  // reflections would tint it); the scent's colour comes in as a rim light.
  const envWhite = buildEnv([
    [4, 3, [-3.5, 2.2, 4.5], [1.7, 1.7, 1.7]],
    [0.5, 8, [3.6, 0.8, 3.6], [2.0, 2.0, 2.0]],
    [0.3, 9, [-5, 0.6, -2.2], [1.2, 1.2, 1.2]],
    [0.4, 9, [5.2, 0.6, -1.0], [1.6, 1.0, 0.3]],
    TOP, STREAK,
  ], 256);

  /* ---------------- product specs (measured from reference photos) ---------------- */
  const loader = new THREE.TextureLoader();
  const labelTex = (src) => {
    const t = loader.load(src, invalidate);
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = renderer.capabilities.getMaxAnisotropy();
    return keep(t);
  };
  const S_RED = 0.25 / 74.5;
  // Overall height kept from the original two-bottle layout, so the camera
  // and the platforms need no retuning.
  const S_BLUE = (S_RED * (374 + 76 + 92)) / (317 + 67 + 82);
  // The 85 ml bottle (measured from the product render): a glossy white
  // body, a wide collar and a taller, narrower cap. Both scents share it.
  const TALL = 1.12; // body stretched up from the render's proportions
  const BOTTLE = { R: 71.5 * S_BLUE, H: 317 * S_BLUE * TALL, collarR: 47 * S_BLUE, collarH: 70 * S_BLUE, capR: 35 * S_BLUE, capH: 88 * S_BLUE };
  // ...and one label placement, cut from the product render by
  // scripts/build_scent_images.py (which writes SCENE_LABEL too).
  const LABEL = {
    l0: BOTTLE.H * SCENE_LABEL.l0,
    l1: BOTTLE.H * SCENE_LABEL.l1,
    // wraps further round as the bottle grows, so the print isn't stretched
    arc: SCENE_LABEL.arc * TALL,
  };
  const PINK = new THREE.Color(1.0, 0.1, 0.5);
  const CYAN = new THREE.Color(0.05, 0.8, 1.0);
  const SPEC = {
    red: { ...BOTTLE, ...LABEL, tex: labelTex('/static/img/scene/label-h1-uriel.webp'), rim: PINK, env: envPink },
    blue: { ...BOTTLE, ...LABEL, tex: labelTex('/static/img/scene/label-a3-raphael.webp'), rim: CYAN, env: envCyan },
  };

  /* ---------------- bottle surfaces ---------------- */
  // Two slow light sweeps (view-space directions) that travel across all the glossy surfaces.
  // `i` is their strength (the turntable fades them in with its lights).
  const SWEEP = { a: { value: new THREE.Vector3(0, 0, 1) }, b: { value: new THREE.Vector3(0, 0, 1) }, i: { value: 1 } };

  /** Rim glow + sweep highlights: the shared look of droplets, light forms and the bottle. */
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

  /** Glossy white bottle plastic, with a rim light in the scent's colour. */
  function makeBodyMat(sp) {
    const mat = rimify(new THREE.MeshPhysicalMaterial({
      color: 0xf4f4f6, roughness: 0.2, clearcoat: 1, clearcoatRoughness: 0.05,
      emissive: 0x3c3c42, envMap: envWhite, envMapIntensity: 1.75,
    }), sp.rim);
    mat.userData.u.uCore.value = 0;
    return mat;
  }

  /** Lathe profile of a short cylinder with softly rounded top and bottom edges. */
  function roundedCylinder(r, h, rr) {
    const pts = [new THREE.Vector2(0.001, 0), new THREE.Vector2(r - rr, 0)];
    for (let i = 1; i <= 8; i += 1) {
      const a = -Math.PI / 2 + ((Math.PI / 2) * i) / 8;
      pts.push(new THREE.Vector2(r - rr + rr * Math.cos(a), rr + rr * Math.sin(a)));
    }
    pts.push(new THREE.Vector2(r, h - rr));
    for (let i = 1; i <= 8; i += 1) {
      const a = ((Math.PI / 2) * i) / 8;
      pts.push(new THREE.Vector2(r - rr + rr * Math.cos(a), h - rr + rr * Math.sin(a)));
    }
    pts.push(new THREE.Vector2(0.001, h));
    return new THREE.LatheGeometry(pts, 96);
  }

  /** Lathe profile of the bottle body: rounded base and shoulder, short neck. */
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
    const bodyMat = makeBodyMat(sp);
    const body = new THREE.Mesh(new THREE.LatheGeometry(bottleProfile(sp), 96), bodyMat);
    root.add(body);

    const R = sp.R;
    const H = sp.H;

    // parts that materialise after the body forms
    const partMats = [];
    const reg = (m) => { partMats.push(m); return m; };
    const lh = sp.l1 - sp.l0;
    const labelMat = new THREE.MeshPhysicalMaterial({
      map: sp.tex, emissiveMap: sp.tex, emissive: 0xffffff, emissiveIntensity: 0.62,
      roughness: 0.3, clearcoat: 0.8, clearcoatRoughness: 0.08, envMap: sp.env, envMapIntensity: 0.3,
    });
    const label = new THREE.Mesh(new THREE.CylinderGeometry(R + 0.004, R + 0.004, lh, 160, 1, true, -sp.arc / 2, sp.arc), labelMat);
    label.position.y = sp.l0 + lh / 2;
    root.add(label);

    const top = new THREE.Group();
    root.add(top);
    const whiteMat = () => new THREE.MeshPhysicalMaterial({
      color: 0xf4f4f6, roughness: 0.18, clearcoat: 1, clearcoatRoughness: 0.04,
      emissive: 0x3c3c42, envMap: envWhite, envMapIntensity: 1.75,
    });
    const collar = new THREE.Mesh(roundedCylinder(sp.collarR, sp.collarH, 0.02), reg(whiteMat()));
    top.add(collar);

    // solid overcap, over the pump
    const capG = new THREE.Group();
    capG.position.y = sp.collarH;
    top.add(capG);
    const cap = new THREE.Mesh(roundedCylinder(sp.capR, sp.capH, 0.03), reg(whiteMat()));
    capG.add(cap);

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
    // what the spray variant moves: the actuator and nozzle (pressed down),
    // the overcap (taken off), and where the nozzle sits in bottle space
    const pump = {
      act, nozzle, actY: act.position.y, nozY: nozzle.position.y, cap: [cap],
      nozzleLocal: new THREE.Vector3(0, H + sp.collarH + sp.capH * 0.42 + ah * 0.55, ar + 0.004),
    };

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

    // Studio lighting: every lit material dims with the studio lights,
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
      bodyMat.envMapIntensity = bodyMat.userData.baseEnv * (0.2 + 0.8 * light);
      bodyMat.userData.u.uRim.value = rim;
      labelMat.emissiveIntensity = 0.62 * light;
    }

    return {
      root,
      pump,
      /** Switch every fading material to its half-faded (transparent) variant, for warm(). */
      prime() {
        for (const m of partMats) fade(m, 0.5);
        fade(labelMat, 0.5);
      },
      update(s) { // s: show, parts, label, rotY (+ light, rim)
        root.visible = s.show;
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

  const PX = 0.56; // bottle / platform centres at x = ±PX
  const PPR = 0.46; // platform radius
  const PPH = 0.16; // platform height
  const mirror = new THREE.Group(); // everything reflected in the floor
  mirror.scale.y = -1;

  /* ---------------- scene assembly ---------------- */
  const red = makeBottle(SPEC.red);
  const blue = makeBottle(SPEC.blue);
  const redM = makeBottle(SPEC.red);
  const blueM = makeBottle(SPEC.blue);
  scene.add(red.root, blue.root);
  scene.add(mirror);
  mirror.add(redM.root, blueM.root);
  // Flat overlays left out of the depth-of-field depth pass.
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

  /* ---------------- spray: Raphael alone, and its mist ---------------- */
  const SPRAY_X = 0.42; // bottle and platform centre
  const ROT = -0.72; // three-quarter view: label visible, nozzle sprays across the frame to the left
  const mists = [];
  if (spray) {
    for (const o of [red.root, redM.root, platR.g, platRM.g, floorGlowR, hazeR]) o.visible = false;
    hazeB.position.set(-0.6, 1.5, -2.2);
    hazeB.scale.set(4.2, 4.2, 1);
    for (const b of [blue, blueM]) { // overcap off to spray; the actuator sits down on the collar
      b.pump.cap.forEach((m) => { m.visible = false; });
      const drop = SPEC.blue.capH * 0.3;
      b.pump.actY -= drop;
      b.pump.nozY -= drop;
      b.pump.nozzleLocal.y -= drop;
    }
  }

  /**
   * Fine perfume mist: an analytic particle system (drag, turbulence,
   * spreading, fading) evaluated in the vertex shader from the burst's age.
   */
  function makeMist(count, sizeMin, sizeMax, alpha, spread, speed, life) {
    const g = new THREE.BufferGeometry();
    const P = new Float32Array(count * 3);
    const D = new Float32Array(count * 3);
    const A = new Float32Array(count * 4);
    for (let i = 0; i < count; i += 1) {
      // direction in a narrow cone around +x of the spray frame, biased to the centre
      const r = Math.pow(Math.random(), 1.6) * spread;
      const th = Math.random() * Math.PI * 2;
      D[i * 3] = 1; D[i * 3 + 1] = r * Math.sin(th); D[i * 3 + 2] = r * Math.cos(th);
      A[i * 4] = Math.pow(Math.random(), 1.4) * 0.34; // birth offset in the burst
      A[i * 4 + 1] = speed * (0.55 + 0.9 * Math.random()); // initial speed
      A[i * 4 + 2] = lerp(sizeMin, sizeMax, Math.pow(Math.random(), 2)); // size
      A[i * 4 + 3] = Math.random(); // seed
    }
    g.setAttribute('position', new THREE.BufferAttribute(P, 3));
    g.setAttribute('aDir', new THREE.BufferAttribute(D, 3));
    g.setAttribute('aP', new THREE.BufferAttribute(A, 4));
    const m = new THREE.ShaderMaterial({
      uniforms: {
        uAge: { value: -1 }, uOrigin: { value: new THREE.Vector3() },
        uX: { value: new THREE.Vector3(1, 0, 0) }, uY: { value: new THREE.Vector3(0, 1, 0) }, uZ: { value: new THREE.Vector3(0, 0, 1) },
        uScale: { value: 600 }, uAlpha: { value: alpha }, uLife: { value: life },
        uColA: { value: new THREE.Color(0.55, 0.95, 1.25) }, uColB: { value: new THREE.Color(1.35, 1.05, 0.6) },
      },
      vertexShader: `
        attribute vec3 aDir; attribute vec4 aP;
        uniform float uAge, uScale, uLife; uniform vec3 uOrigin, uX, uY, uZ;
        varying float vA; varying float vS;
        void main(){
          float age = uAge - aP.x;
          float k = 2.6;                                         // air drag
          float travel = aP.y * (1.0 - exp(-k*max(age,0.0))) / k;
          vec3 d = normalize(aDir);
          vec3 p = d * travel;
          float wob = max(age,0.0);
          p.y += -0.035*wob*wob + 0.05*wob*sin(aP.w*40.0);       // settle + drift
          p.y += 0.06*sin(aP.w*23.0 + wob*2.3)*wob; p.z += 0.07*cos(aP.w*31.0 + wob*1.9)*wob; // turbulence
          p.yz *= 1.0 + wob*0.9;                                 // cloud widens as it slows
          vec3 w = uOrigin + uX*p.x + uY*p.y + uZ*p.z;
          vec4 mv = modelViewMatrix * vec4(w, 1.0);
          gl_Position = projectionMatrix * mv;
          float fadeIn = smoothstep(0.0, 0.05, age), fadeOut = 1.0 - smoothstep(uLife*0.35, uLife, age);
          vA = (age > 0.0 ? fadeIn*fadeOut : 0.0) * (0.6 + 0.4*fract(aP.w*7.3));
          vS = aP.w;
          gl_PointSize = aP.z * (1.0 + 2.2*wob) * uScale / -mv.z;
        }`,
      fragmentShader: `
        uniform float uAlpha; uniform vec3 uColA, uColB; varying float vA; varying float vS;
        void main(){
          vec2 c = gl_PointCoord - 0.5; float r = dot(c,c)*4.0;
          float a = exp(-r*2.8) * vA * uAlpha; if (a < 0.002) discard;
          vec3 col = mix(uColA, uColB, step(0.82, vS));
          gl_FragColor = vec4(col*a, a);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    const pts = new THREE.Points(g, m);
    pts.frustumCulled = false;
    pts.renderOrder = 7;
    scene.add(pts);
    const reflected = new THREE.Points(g, m);
    reflected.frustumCulled = false;
    mirror.add(reflected);
    // Not in noDepth: hiding these for the depth pass left the label and
    // liquid black in the next frame (three r186). They add nothing to it
    // anyway: under the depth override they draw at their stored positions,
    // which are all zero (the motion lives in this shader).
    return pts;
  }
  if (spray) {
    mists.push(
      makeMist(2600, 0.010, 0.028, 0.55, 0.20, 2.3, 2.6), // fine droplets
      makeMist(260, 0.07, 0.16, 0.07, 0.26, 1.6, 3.2), // soft vapour
    );
  }

  /* ---------------- story: a gate of heaven → streams of light → perfumes ---------------- */
  /**
   * Builds the heaven backdrop (oculus, light beams, halo), light streams and bottle reveal, and returns
   * `update(t)` to pose them (bottles, platforms and camera are posed in
   * poseStory). Everything is a pure function of `t`, so the scroll can
   * scrub it either way.
   */
  function makeWings() {
    const GOLD_ENV = buildEnv([[0.8, 9, [-5.5, 0.6, -1.5], [3.6, 1.2, 1.4]], [0.8, 9, [5.5, 0.6, -1.5], [0.6, 1.8, 3.4]], [0.5, 8, [0, 0.8, 6], [3.2, 2.5, 1.6]], TOP, STREAK, GOLD_R, GOLD_F]);

    // the halo, and the two rings it splits into (they become the platform LED rings)
    const haloMat = new THREE.MeshPhysicalMaterial({
      color: 0xf0c46a, metalness: 1, roughness: 0.2, emissive: 0xffc766, emissiveIntensity: 1.6, envMap: GOLD_ENV, envMapIntensity: 1.5,
    });
    const HALO_Y = 2.0;
    const halo = new THREE.Mesh(new THREE.TorusGeometry(0.22, 0.014, 16, 128), haloMat);
    halo.rotation.x = 1.25;
    scene.add(halo);
    const LAND = [5.1, 5.3]; // when each ring lands on its platform
    const haloRings = [[-PX, PINK, LAND[0]], [PX, CYAN, LAND[1]]].map(([x, col, tl]) => {
      const m = haloMat.clone();
      const r = new THREE.Mesh(halo.geometry, m);
      r.visible = false;
      scene.add(r);
      const rm = new THREE.Mesh(halo.geometry, m);
      rm.visible = false;
      mirror.add(rm);
      return { r, rm, m, x, col, tl };
    });
    // golden motes rising through the light
    const MOTES = 220;
    const mp = new Float32Array(MOTES * 3);
    const ms = new Float32Array(MOTES);
    for (let i = 0; i < MOTES; i += 1) {
      mp.set([(Math.random() - 0.5) * 3.2, Math.random() * 2.6, (Math.random() - 0.5) * 1.6], i * 3);
      ms[i] = Math.random();
    }
    const mg = new THREE.BufferGeometry();
    mg.setAttribute('position', new THREE.BufferAttribute(mp, 3));
    mg.setAttribute('aS', new THREE.BufferAttribute(ms, 1));
    const motes = new THREE.Points(mg, new THREE.ShaderMaterial({
      uniforms: { uT: { value: 0 }, uA: { value: 0 }, uScale: { value: 600 } },
      vertexShader: `
        attribute float aS; uniform float uT, uA, uScale; varying float vA;
        void main(){
          vec3 p = position;
          p.y = mod(p.y + uT*(0.08 + 0.14*aS), 2.8);
          p.x += 0.08*sin(uT*0.9 + aS*40.0); p.z += 0.08*cos(uT*0.7 + aS*25.0);
          vec4 mv = modelViewMatrix * vec4(p, 1.0); gl_Position = projectionMatrix * mv;
          vA = uA * (0.4 + 0.6*sin(uT*3.0 + aS*50.0)*sin(uT*3.0 + aS*50.0)) * smoothstep(0.0, 0.35, p.y) * (1.0 - smoothstep(2.2, 2.8, p.y));
          gl_PointSize = (0.014 + 0.02*aS) * uScale / -mv.z;
        }`,
      fragmentShader: `
        varying float vA;
        void main(){ vec2 c = gl_PointCoord - 0.5; float a = exp(-dot(c,c)*16.0) * vA; if (a < 0.003) discard; gl_FragColor = vec4(vec3(1.7, 1.3, 0.7)*a, a); }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    }));
    motes.frustumCulled = false;
    motes.renderOrder = 8;
    scene.add(motes);
    noDepth.push(motes);

    // tall shafts of light descending from the halo
    const shaftCanvas = document.createElement('canvas');
    shaftCanvas.width = 64;
    shaftCanvas.height = 256;
    {
      const g = shaftCanvas.getContext('2d');
      const gx = g.createLinearGradient(0, 0, 64, 0);
      gx.addColorStop(0, 'rgba(255,255,255,0)');
      gx.addColorStop(0.5, 'rgba(255,255,255,1)');
      gx.addColorStop(1, 'rgba(255,255,255,0)');
      g.fillStyle = gx;
      g.fillRect(0, 0, 64, 256);
      g.globalCompositeOperation = 'destination-in';
      const gy = g.createLinearGradient(0, 0, 0, 256);
      gy.addColorStop(0, 'rgba(0,0,0,1)');
      gy.addColorStop(0.7, 'rgba(0,0,0,0.5)');
      gy.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = gy;
      g.fillRect(0, 0, 64, 256);
    }
    const shaftTex = keep(new THREE.CanvasTexture(shaftCanvas));
    // they pour from the oculus down onto the floor, fanning out as they fall
    const PORTAL = { y: 1.6, z: -2.6, r: 1.4 };
    const SHAFTS = [-1.8, -1.2, -0.7, -0.25, 0.25, 0.7, 1.2, 1.8].map((x1, i) => {
      const m = new THREE.MeshBasicMaterial({
        map: shaftTex, color: new THREE.Color(1.0, 0.84, 0.55), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide,
      });
      const x0 = x1 * 0.28;
      const y0 = PORTAL.y - 0.2;
      const len = Math.hypot(x1 - x0, y0);
      const w = 0.05 + 0.07 * ((i * 5) % 3) / 2;
      const p = new THREE.Mesh(new THREE.PlaneGeometry(w, len), m);
      p.position.set((x0 + x1) / 2, y0 / 2, -1.4 - 0.15 * (i % 3));
      p.rotation.z = Math.atan2(x1 - x0, y0);
      p.renderOrder = 3;
      scene.add(p);
      noDepth.push(p);
      return { p, m, ph: i * 1.3 };
    });

    // the oculus: a round opening in the dark, blazing with light
    const portalMat = new THREE.ShaderMaterial({
      uniforms: { uT: { value: 0 }, uA: { value: 0 } },
      vertexShader: 'varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: `
        uniform float uT, uA; varying vec2 vUv;
        void main(){
          vec2 p = (vUv - 0.5) * 2.0; float r = length(p); float a = atan(p.y, p.x);
          // crisp concentric gold rings and fine radial ticks around a clear, bright centre
          float ring = max(smoothstep(0.014, 0.0, abs(r - 0.97)), smoothstep(0.008, 0.0, abs(r - 0.78)) * 0.7 + smoothstep(0.008, 0.0, abs(r - 0.56)) * 0.5);
          float ticks = smoothstep(0.35, 0.5, abs(sin(a*36.0 + uT*0.25))) * step(0.84, r) * step(r, 0.93) * 0.8;
          float disc = smoothstep(0.35, 0.0, r) * 1.3 + 0.12;
          float I = (disc + ring + ticks) * step(r, 1.0) * uA;
          vec3 col = mix(vec3(1.5, 1.05, 0.5), vec3(2.0, 1.85, 1.6), smoothstep(0.5, 0.0, r));
          gl_FragColor = vec4(col*I, I);
        }`,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
    const portal = new THREE.Mesh(new THREE.CircleGeometry(1, 96), portalMat);
    portal.position.set(0, PORTAL.y, PORTAL.z);
    portal.renderOrder = 3;
    scene.add(portal);
    noDepth.push(portal);
    const rimMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(2.4, 1.7, 0.8), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false });
    const rim = new THREE.Mesh(new THREE.TorusGeometry(1, 0.012, 12, 160), rimMat);
    rim.position.copy(portal.position);
    rim.renderOrder = 3;
    scene.add(rim);
    noDepth.push(rim);

    // four-point stars that twinkle around the halo
    const starCanvas = document.createElement('canvas');
    starCanvas.width = 64;
    starCanvas.height = 64;
    {
      const g = starCanvas.getContext('2d');
      g.translate(32, 32);
      for (const [rx, ry] of [[30, 3], [3, 30]]) {
        const gr = g.createRadialGradient(0, 0, 0, 0, 0, 30);
        gr.addColorStop(0, 'rgba(255,255,255,1)');
        gr.addColorStop(1, 'rgba(255,255,255,0)');
        g.fillStyle = gr;
        g.beginPath();
        g.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
        g.fill();
      }
    }
    const starTex = keep(new THREE.CanvasTexture(starCanvas));
    const STARS = Array.from({ length: 16 }, () => {
      const a = Math.random() * Math.PI * 2;
      const r = 0.5 + Math.random() * 1.7;
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({
        map: starTex, color: new THREE.Color(1.6, 1.3, 0.8), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      sp.position.set(Math.cos(a) * r * 1.2, HALO_Y - 0.4 + Math.sin(a) * r * 0.7, -0.5 + Math.random() * 0.8);
      sp.renderOrder = 8;
      scene.add(sp);
      noDepth.push(sp);
      return { sp, ph: Math.random() * 6.28, sz: 0.1 + 0.18 * Math.random(), f: 1.5 + 2 * Math.random() };
    });

    // LED shockwave rings that ripple across the floor as each halo ring lands
    const ringTex = radialTex([[0, 'rgba(255,255,255,0)'], [0.78, 'rgba(255,255,255,0)'], [0.9, 'rgba(255,255,255,1)'], [0.95, 'rgba(255,255,255,0.35)'], [1, 'rgba(255,255,255,0)']]);
    const waves = [];
    for (const [x, col, t0] of [[-PX, PINK, LAND[0]], [PX, CYAN, LAND[1]]]) {
      for (let k = 0; k < 2; k += 1) {
        const m = new THREE.MeshBasicMaterial({
          map: ringTex, color: col.clone().multiplyScalar(2.2), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false,
        });
        const p = new THREE.Mesh(new THREE.PlaneGeometry(1, 1), m);
        p.rotation.x = -Math.PI / 2;
        p.position.set(x, 0.004, 0);
        p.renderOrder = 3;
        scene.add(p);
        noDepth.push(p);
        waves.push({ p, m, t0: t0 + k * 0.22 });
      }
    }

    // light from the heavens → streams of light → bottles. Each bottle is revealed from
    // its base upward over `dur` seconds from `t0`, and each particle
    // arrives at the height being revealed as it lands.
    const REVEAL = [
      { t0: 4.45, dur: 1.5, x: -PX, u: { value: -1 } },
      { t0: 4.65, dur: 1.5, x: PX, u: { value: -1 } },
    ];
    const HTOT = BOTTLE.H + BOTTLE.collarH + BOTTLE.capH;
    function makeStream(side, rv, tint) {
      const N = 5200;
      const S = new Float32Array(N * 3); // start: in the clouds and shafts of light
      const E = new Float32Array(N * 3); // end: on the bottle's surface
      const C = new Float32Array(N * 3); // control point of the arc between
      const T = new Float32Array(N * 3); // birth, arrival, seed
      for (let n = 0; n < N; n += 1) {
        const inCloud = Math.random() < 0.45;
        const sx = side * Math.pow(Math.random(), 0.8) * 1.9;
        const sy = inCloud ? 0.05 + 0.6 * Math.random() : 0.4 + 2.0 * Math.random();
        const sz = (Math.random() - 0.6) * (inCloud ? 1.8 : 0.7);
        S.set([sx, sy, sz], n * 3);
        const h = Math.pow(Math.random(), 0.9) * HTOT;
        const rr = h < BOTTLE.H ? BOTTLE.R : h < BOTTLE.H + BOTTLE.collarH ? BOTTLE.collarR : BOTTLE.capR;
        const ph = (Math.random() - 0.5) * Math.PI * 1.6;
        E.set([rv.x + rr * Math.sin(ph), PPH + h, rr * Math.cos(ph)], n * 3);
        C.set([
          (sx + rv.x) / 2 + side * (0.2 + 0.5 * Math.random()),
          Math.max(sy, PPH + h) + 0.25 + 0.6 * Math.random(),
          0.5 + 0.9 * (Math.random() - 0.3),
        ], n * 3);
        // the outermost light leaves first, then it gathers inward
        const birth = 3.45 + 0.95 * (Math.abs(sx) / 1.9) + 0.28 * Math.random();
        const arrive = Math.max(rv.t0 + rv.dur * (h / HTOT) + 0.05, birth + 1.15);
        T.set([birth, arrive, Math.random()], n * 3);
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(S, 3));
      g.setAttribute('aEnd', new THREE.BufferAttribute(E, 3));
      g.setAttribute('aCtl', new THREE.BufferAttribute(C, 3));
      g.setAttribute('aT', new THREE.BufferAttribute(T, 3));
      const m = new THREE.ShaderMaterial({
        uniforms: { uT: { value: 0 }, uScale: { value: 600 }, uTint: { value: tint.clone() } },
        vertexShader: `
          attribute vec3 aEnd, aCtl, aT; uniform float uT, uScale; varying float vA; varying float vK; varying float vR;
          void main(){
            float p = clamp((uT - aT.x)/(aT.y - aT.x), 0.0, 1.0);
            float e = p*p*p*(p*(p*6.0 - 15.0) + 10.0); // smootherstep: no kick at either end
            vec3 q = mix(mix(position, aCtl, e), mix(aCtl, aEnd, e), e);
            float sw = sin(3.14159*e)*(1.0 - e)*0.42; float ang = e*9.0 + aT.z*6.2832; q.x += sw*sin(ang); q.z += sw*cos(ang); // wide, slow turns that wind down as the light lands
            vec4 mv = modelViewMatrix * vec4(q, 1.0); gl_Position = projectionMatrix * mv;
            float on = step(aT.x, uT) * (1.0 - step(aT.y, uT));
            vA = on * smoothstep(0.0, 0.18, p) * (1.0 - smoothstep(0.82, 1.0, p)) * (0.82 + 0.18*sin(uT*5.0 + aT.z*60.0));
            vK = e; vR = aT.z;
            gl_PointSize = (0.026 + 0.024*aT.z) * (1.3 - 0.6*e) * uScale / -mv.z;
          }`,
        fragmentShader: `
          uniform vec3 uTint; varying float vA; varying float vK; varying float vR;
          void main(){
            vec2 c = gl_PointCoord - 0.5; float a = exp(-dot(c,c)*14.0) * vA; if (a < 0.003) discard;
            vec3 gold = vec3(1.6, 1.1, 0.45);
            vec3 col = mix(gold, uTint*1.6, smoothstep(0.35, 0.95, vK)*0.7);
            gl_FragColor = vec4(col*a*1.4, a);
          }`,
        transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
      });
      const pts = new THREE.Points(g, m);
      pts.frustumCulled = false;
      pts.renderOrder = 8;
      scene.add(pts);
      const reflected = new THREE.Points(g, m);
      reflected.frustumCulled = false;
      mirror.add(reflected);
      noDepth.push(pts, reflected);
      return m;
    }
    const streams = [makeStream(-1, REVEAL[0], PINK), makeStream(1, REVEAL[1], CYAN)];

    // Bottles build upward behind a glowing gold edge: every fragment above
    // the reveal height (|y|, so the reflection builds too) is discarded.
    function addReveal(root, u) {
      root.traverse((o) => {
        if (!o.isMesh || o.material.userData.revealed) return;
        const m = o.material;
        m.userData.revealed = true;
        const prev = m.onBeforeCompile;
        const prevKey = m.customProgramCacheKey();
        m.onBeforeCompile = (sh, r) => {
          prev.call(m, sh, r);
          sh.uniforms.uReveal = u;
          sh.vertexShader = 'varying vec3 vRevW;\n' + sh.vertexShader.replace('#include <project_vertex>', '#include <project_vertex>\n vRevW = (modelMatrix * vec4(transformed, 1.0)).xyz;');
          sh.fragmentShader = 'uniform float uReveal; varying vec3 vRevW;\n' + sh.fragmentShader
            .replace('void main() {', 'void main() {\n if (abs(vRevW.y) > uReveal) discard;')
            .replace('#include <emissivemap_fragment>', '#include <emissivemap_fragment>\n totalEmissiveRadiance += vec3(3.2, 2.1, 0.8) * (1.0 - smoothstep(0.0, 0.045, uReveal - abs(vRevW.y))) * step(uReveal, 2.4);');
        };
        // three caches programs by onBeforeCompile's source, which this
        // wrapper makes identical for every material: key by the original.
        m.customProgramCacheKey = () => `reveal|${prevKey}`;
        m.needsUpdate = true;
      });
    }
    addReveal(red.root, REVEAL[0].u);
    addReveal(redM.root, REVEAL[0].u);
    addReveal(blue.root, REVEAL[1].u);
    addReveal(blueM.root, REVEAL[1].u);

    const fmTmp = new THREE.Matrix4();
    const bufTmp = new THREE.Vector2();
    return {
      update(t) {
        const glow = ss(0.1, 1.2, t);
        const mote = ss(0.6, 1.6, t) * (1 - ss(4.6, 5.6, t));
        motes.visible = mote > 0.002;
        motes.material.uniforms.uT.value = t;
        motes.material.uniforms.uA.value = mote;
        motes.material.uniforms.uScale.value = renderer.getDrawingBufferSize(bufTmp).y * 0.62;
        // the shafts shimmer and the stars twinkle; all of it gives way to the streams
        const fade = ss(0.2, 1.4, t) * (1 - ss(3.5, 4.7, t));
        // the oculus irises open with a little overshoot, then eases down to a backlight behind the bottles
        const open = ss(0.2, 1.9, t);
        const iris = open * (1 + 0.05 * Math.sin(Math.PI * lin(0.2, 1.9, t))) * (1 + 0.015 * Math.sin(t * 1.8));
        const keepGlow = 0.16 + 0.84 * (1 - ss(3.6, 5.0, t));
        portal.scale.setScalar(Math.max(iris * PORTAL.r, 0.001));
        portal.visible = open > 0.002;
        portalMat.uniforms.uT.value = t;
        portalMat.uniforms.uA.value = keepGlow * open;
        rim.scale.copy(portal.scale);
        rim.visible = portal.visible;
        rimMat.opacity = 0.9 * keepGlow * open;
        for (const sh of SHAFTS) {
          sh.p.visible = fade > 0.002;
          sh.m.opacity = (0.55 + 0.25 * Math.sin(t * 1.6 + sh.ph)) * ss(0.4, 2.0, t) * (1 - ss(3.5, 4.7, t));
        }
        for (const st of STARS) {
          const tw = Math.max(0, Math.sin(t * st.f + st.ph));
          st.sp.visible = fade > 0.002;
          st.sp.material.opacity = tw * tw * fade;
          st.sp.scale.setScalar(st.sz * (0.6 + 0.6 * tw));
          st.sp.material.rotation = t * 0.3;
        }

        halo.visible = t < 3.62;
        haloMat.emissiveIntensity = 1.8 * ss(0.9, 1.7, t);
        halo.scale.setScalar(lerp(0.6, 1, ss(0.9, 1.7, t)));
        halo.position.set(0, HALO_Y + 0.03 * Math.sin(t * 1.6), 0);
        halo.rotation.z = t * 0.3;
        for (const h of haloRings) {
          const on = t >= 3.62 && t < h.tl;
          h.r.visible = on;
          h.rm.visible = on;
          if (!on) continue;
          const u = eIO(lin(3.62, h.tl, t));
          h.r.position.set(lerp(0, h.x, u), lerp(HALO_Y, PPH * 0.42, u) + 0.5 * Math.sin(Math.PI * u), 0.25 * Math.sin(Math.PI * u));
          h.r.rotation.set(lerp(1.25, Math.PI / 2, u), 0, t * 2.0 * (1 - u));
          h.r.scale.setScalar(lerp(1, (PPR + 0.004) / 0.22, u));
          h.m.emissive.setRGB(lerp(1, h.col.r, u), lerp(0.78, h.col.g, u), lerp(0.4, h.col.b, u));
          h.m.emissiveIntensity = 1.8 + 1.2 * u;
          h.rm.position.copy(h.r.position);
          h.rm.rotation.copy(h.r.rotation);
          h.rm.scale.copy(h.r.scale);
        }

        const scale = renderer.getDrawingBufferSize(bufTmp).y * 0.62;
        for (const m of streams) {
          m.uniforms.uT.value = t;
          m.uniforms.uScale.value = scale;
        }
        for (const rv of REVEAL) {
          rv.u.value = t < rv.t0 ? -1
            : t > rv.t0 + rv.dur + 0.4 ? 9
              : PPH - 0.02 + (HTOT + 0.06) * eIO(lin(rv.t0, rv.t0 + rv.dur, t));
        }
        for (const w of waves) {
          const a = t - w.t0;
          const on = a > 0 && a < 1.6;
          w.p.visible = on;
          if (!on) continue;
          const sc = 0.9 + a * 4.2;
          w.p.scale.set(sc, sc, 1);
          w.m.opacity = 0.85 * Math.exp(-a * 2.2) * ss(0, 0.05, a);
        }
      },
      land: LAND,
    };
  }
  const wings = story ? makeWings() : null;

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

  // Depth of field: a depth pass (overlays hidden) gives each pixel's
  // distance, and the scene is mixed toward two blurred copies of itself
  // the further it sits from the focus distance.
  const APERTURE = story ? 0.2 : 0.18;
  const dof = {
    depthRT: new THREE.WebGLRenderTarget(1, 1, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter }),
    a: [new THREE.WebGLRenderTarget(1, 1, rtOpts), new THREE.WebGLRenderTarget(1, 1, rtOpts)],
    b: [new THREE.WebGLRenderTarget(1, 1, rtOpts), new THREE.WebGLRenderTarget(1, 1, rtOpts)],
    depthMat: new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking }),
    on: true,
  };
  {
    compFS = compFS
      .replace('uniform sampler2D tScene, b0, b1, b2, b3, b4;', `#include <packing>
      uniform sampler2D tScene, b0, b1, b2, b3, b4, tDepth, d1, d2; uniform float focus, aperture, cNear, cFar;`)
      .replace('vec3 c = texture2D(tScene, vUv).rgb;', `float dz = -perspectiveDepthToViewZ(unpackRGBAToDepth(texture2D(tDepth, vUv)), cNear, cFar);
        float coc = clamp(abs(dz - focus)/max(focus*0.32, 0.01), 0.0, 1.0) * aperture;
        vec3 c = texture2D(tScene, vUv).rgb;
        c = mix(c, texture2D(d1, vUv).rgb, smoothstep(0.0, 0.5, coc));
        c = mix(c, texture2D(d2, vUv).rgb, smoothstep(0.5, 1.0, coc));`);
  }
  // Story only: an anamorphic streak (a long, horizontal-only blur of the
  // bright pass, added back in cool blue) and a brief flash as each halo
  // ring lands.
  const streakRT = story ? [new THREE.WebGLRenderTarget(1, 1, rtOpts), new THREE.WebGLRenderTarget(1, 1, rtOpts)] : null;
  if (streakRT) {
    compFS = compFS
      .replace('uniform float strength, exposure, time;', 'uniform float strength, exposure, time; uniform sampler2D tS; uniform float flash, streak;')
      .replace('vec3 bl = ', `c += texture2D(tS, vUv).rgb * streak * vec3(0.55, 0.8, 1.25);
        vec3 bl = `)
      .replace('vec3 o = toSRGB(aces(c));', `c = c*(1.0 + flash*3.0) + flash*vec3(0.5, 0.55, 0.6);
        vec3 o = toSRGB(aces(c));`);
  }
  const compMat = new THREE.ShaderMaterial({
    uniforms: {
      tScene: { value: sceneRT.texture }, b0: { value: rtA[0].texture }, b1: { value: rtA[1].texture }, b2: { value: rtA[2].texture },
      b3: { value: rtA[3].texture }, b4: { value: rtA[4].texture }, strength: { value: 0.85 }, exposure: { value: 1.0 }, time: { value: 0 },
      tDepth: { value: dof.depthRT.texture }, d1: { value: dof.a[0].texture }, d2: { value: dof.a[1].texture },
      focus: { value: 8 }, aperture: { value: APERTURE }, cNear: { value: camera.near }, cFar: { value: camera.far },
      ...(streakRT && { tS: { value: streakRT[0].texture }, flash: { value: 0 }, streak: { value: 0.25 } }),
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
    {
      // Low quality tiers skip the depth pass; aperture 0 means nothing blurs.
      compMat.uniforms.aperture.value = dof.on ? APERTURE : 0;
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
    if (streakRT) {
      let src = rtA[1].texture;
      for (const k of [1.5, 4, 10, 24]) {
        blurMat.uniforms.tex.value = src;
        blurMat.uniforms.dir.value.set(k / streakRT[0].width, 0);
        pass(blurMat, streakRT[1]);
        blurMat.uniforms.tex.value = streakRT[1].texture;
        blurMat.uniforms.dir.value.set((k * 1.7) / streakRT[0].width, 0);
        pass(blurMat, streakRT[0]);
        src = streakRT[0].texture;
      }
    }
    pass(compMat, null);
  }

  /* ---------------- timeline ---------------- */
  /** Story: pose everything for scene time `t` (0 to SCENE_END). */
  function poseStory(t) {
    SWEEP.i.value = 1;
    wings.update(t);

    // the bottles, revealed standing on their platforms as the lights come up
    const light = ss(4.4, 6.4, t);
    const base = { show: t > 4.4, parts: 1, label: 1, light: 0.35 + 0.65 * light, rim: 0.45 - 0.2 * light };
    const turn = eIOS(lin(4.4, 10, t));
    const rs = { ...base, rotY: lerp(-0.35, 0.08, turn) };
    const bs = { ...base, rotY: lerp(0.35, -0.08, turn) };
    red.root.position.set(-PX, PPH, 0);
    blue.root.position.set(PX, PPH, 0);
    redM.root.position.copy(red.root.position);
    blueM.root.position.copy(blue.root.position);
    red.update(rs);
    blue.update(bs);
    redM.update(rs);
    blueM.update(bs);

    // the platforms rise; each LED ring switches on (with a pulse) as its halo ring lands
    const rise = eIO(lin(4.15, 4.9, t));
    const [landR, landB] = wings.land;
    for (const [p, tl] of [[platR, landR], [platRM, landR], [platB, landB], [platBM, landB]]) {
      p.g.visible = rise > 0.001;
      p.g.scale.y = Math.max(rise, 0.001);
      p.mat.envMapIntensity = 0.8 * (0.2 + 0.8 * light);
      const on = ss(tl - 0.02, tl + 0.05, t);
      const pulse = t > tl ? Math.exp(-(t - tl) * 3.5) : 0;
      for (const m of p.leds) m.opacity = m.userData.base * on * (1 + 0.8 * pulse);
    }
    floorGlowR.position.x = -PX;
    floorGlowB.position.x = PX;
    const wingGlow = 0.15 * ss(0.3, 1.5, t) * (1 - ss(3.5, 4.5, t));
    floorGlowR.material.opacity = 0.4 * ss(landR, landR + 0.2, t) + wingGlow;
    floorGlowB.material.opacity = 0.4 * ss(landB, landB + 0.2, t) + wingGlow;
    hazeR.material.opacity = 0.17 * ss(0.2, 1.5, t);
    hazeB.material.opacity = hazeR.material.opacity;

    // camera and lens
    const d = curve(CAM.dist, t);
    const az = curve(CAM.az, t);
    const el = curve(CAM.el, t);
    const ty = curve(CAM.ty, t);
    camera.position.set(d * Math.sin(az) * Math.cos(el), ty + d * Math.sin(el), d * Math.cos(az) * Math.cos(el));
    camera.lookAt(0, ty, 0);
    camera.updateMatrixWorld();
    compMat.uniforms.focus.value = d - 0.1;
    const flashAt = (tl) => 0.12 * Math.exp(-Math.pow((t - (tl + 0.02)) / 0.05, 2));
    compMat.uniforms.flash.value = flashAt(landR) + flashAt(landB);
    compMat.uniforms.streak.value = 0.3 + 0.35 * ss(3.5, 5.0, t) * (1 - ss(5.6, 6.6, t)) + 0.3 * ss(7.5, 9.0, t);
    const sa = -1.4 + t * 0.3;
    const sb = 1.6 - t * 0.24;
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
    const s = { show: true, parts: 1, label: 1, light, rim, rotY: -0.9 + spinAngle(t) };
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

  /**
   * Spray: pose Raphael `t` seconds after it first came into view. The
   * lights come up as in the turntable, then the pump presses every
   * SPRAY_CYCLE seconds and the mist bursts from the nozzle.
   */
  const nozW = new THREE.Vector3();
  const q = new THREE.Quaternion();
  const ex = new THREE.Vector3();
  const ey = new THREE.Vector3(0, 1, 0);
  const ez = new THREE.Vector3();
  const buf = new THREE.Vector2();
  function poseSpray(t) {
    const light = ss(0.4, 1.8, t);
    const rim = 0.95 * ss(0.1, 0.8, t) * (1 - 0.72 * ss(1.0, 2.0, t));
    const led = ss(0.0, 0.8, t);
    SWEEP.i.value = ss(0.3, 1.2, t);

    // the spray cycle: a quick press, a held beat, then the actuator springs back
    const c = t < SPRAY_FIRST ? -1 : (t - SPRAY_FIRST) % SPRAY_CYCLE;
    const press = c < 0 ? 0 : (c < 0.1 ? sstep(c / 0.1) : 1 - ss(0.32, 0.62, c));
    const s = { show: true, parts: 1, label: 1, light, rim, rotY: ROT + 0.03 * Math.sin(t * 0.4) };
    blue.root.position.set(SPRAY_X, PPH, 0);
    blueM.root.position.copy(blue.root.position);
    for (const b of [blue, blueM]) {
      b.update(s);
      b.pump.act.position.y = b.pump.actY - 0.022 * press;
      b.pump.nozzle.position.y = b.pump.nozY - 0.022 * press;
    }

    // the mist's frame: out of the nozzle (x), world up (y), across (z)
    blue.root.updateMatrixWorld(true);
    nozW.copy(blue.pump.nozzleLocal).applyMatrix4(blue.root.matrixWorld);
    blue.root.getWorldQuaternion(q);
    ex.set(0, 0, 1).applyQuaternion(q);
    ez.crossVectors(ex, ey).normalize();
    const scale = renderer.getDrawingBufferSize(buf).y * 0.62;
    for (const m of mists) {
      const u = m.material.uniforms;
      u.uAge.value = c < 0 ? -1 : c;
      u.uOrigin.value.copy(nozW);
      u.uX.value.copy(ex);
      u.uY.value.copy(ey);
      u.uZ.value.copy(ez);
      u.uScale.value = scale;
    }

    for (const p of [platB, platBM]) {
      p.g.visible = true;
      p.g.scale.y = 1;
      p.g.position.x = SPRAY_X;
      p.mat.envMapIntensity = 0.8 * (0.15 + 0.85 * light);
      for (const m of p.leds) m.opacity = m.userData.base * led;
    }
    floorGlowB.position.x = SPRAY_X;
    floorGlowB.material.opacity = 0.45 * led;
    hazeB.material.opacity = 0.2 * light;

    // framed wider than the turntable, so the mist has room to drift left
    const d = lerp(7.0, 7.7, eIOS(lin(0, 2.2, t)));
    const az = 0.05 * Math.sin(t * 0.23);
    const el = 0.07 + 0.012 * Math.sin(t * 0.19);
    const ty = 1.12;
    camera.position.set(d * Math.sin(az) * Math.cos(el), ty + d * Math.sin(el), d * Math.cos(az) * Math.cos(el));
    camera.lookAt(0, ty, 0);
    camera.updateMatrixWorld();
    compMat.uniforms.focus.value = d - 0.1;
    const sa = -0.75 + 0.12 * Math.sin(t * 0.21);
    const sb = 0.95 + 0.1 * Math.sin(t * 0.17 + 1.0);
    SWEEP.a.value.set(Math.sin(sa), 0.35, Math.cos(sa)).normalize().transformDirection(camera.matrixWorldInverse);
    SWEEP.b.value.set(Math.sin(sb), 0.55, Math.cos(sb)).normalize().transformDirection(camera.matrixWorldInverse);
  }

  /** Pose for time `t` (story: scene time; studio: seconds in view, turntable with `tone`) and draw. */
  function render(t, tone = 0) {
    if (turntable) poseTurntable(t, tone);
    else if (spray) poseSpray(t);
    else poseStory(t);
    renderFrame();
  }

  /** Apply a QUALITY tier's scene-side settings (the renderer's are set by the caller). */
  function setQuality(q) {
    dof.on = q.dof;
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
    if (streakRT) for (const rt of streakRT) rt.setSize(Math.max(1, px >> 2), Math.max(1, px >> 3));
    {
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
      if (o.isMesh || o.isPoints) o.geometry.dispose();
      if (o.isMesh || o.isSprite || o.isPoints) o.material.dispose();
    });
    quad.geometry.dispose();
    [brightMat, blurMat, compMat, sceneRT, ...rtA, ...rtB, ...disposables].forEach((x) => x.dispose());
    [dof.depthRT, ...dof.a, ...dof.b, dof.depthMat, ...(streakRT || [])].forEach((x) => x.dispose());
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
 * variant="spray": Raphael A3 alone, spraying every 5 s while on screen
 * (same pausing clock as the turntable). With `paused`, it holds the
 * still frame at `time` instead (by default, mid-burst).
 *
 * `onReady` fires after the first frame.
 */
export default function PerfumeScene({
  variant = 'story', progress, time: timeProp, paused = false, tone, tones, onReady, className = '',
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
  const time = timeProp ?? (variant === 'spray' ? SPRAY_STILL : SCENE_END);
  // The studio variants run on their own clock unless held still.
  const running = variant !== 'story' && !paused;

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
    // Quality steps, dropped (never raised again, so it can't oscillate)
    // when the frame rate sags while the scene is moving. Starts from the
    // tier the page has already settled on (see sharedTier).
    let tier = sharedTier;
    let dirty = true;
    const view = buildScene(renderer, () => { dirty = true; }, { variant, tones: tonesRef.current ?? [] });
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

    view.warm().then(() => { dirty = true; }, () => { });

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
    let clock = 0; // studio: seconds rendered on screen
    let frame = 0;
    let first = true;
    let last = 0; // timestamp of the previous rendered frame (0: none in a row)
    let avg = 16.7; // smoothed ms between consecutive rendered frames
    let streak = 0; // consecutive rendered frames behind `avg`

    const tick = (now) => {
      frame = requestAnimationFrame(tick);
      let dt;
      if (running) {
        // Always moving: advance the clock by the real frame time, so the
        // turn (or spray) keeps its exact speed at any refresh rate.
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
        if (streak > SLOW_STREAK && avg > SLOW_FRAME_MS) {
          tier = Math.min(QUALITY.length - 1, tier + (avg > VERY_SLOW_FRAME_MS ? 2 : 1));
          sharedTier = Math.max(sharedTier, tier);
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
  }, [time, variant, running]);

  return <div ref={mountRef} className={`perfume-scene ${className}`} aria-hidden="true" />;
}
