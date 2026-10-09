/**
 * Max's avatar, phase A (spike): load a VRM, give it a little idle life and measure what it costs.
 * No connection to Max yet. URL options:
 *   ?fps=60      frame-rate cap (frames are skipped, not just throttled by vsync)
 *   ?tex=1024    shrink textures larger than this (0 = keep the originals)
 *   ?scale=2     render at this many pixels per screen pixel (supersampling: cleaner hair and edges)
 *   ?debug=1     on-screen stats (fps, draw calls, triangles, GPU name)
 *   ?model=...   VRM to load (default avatar/model.vrm)
 * Every 2 s the stats are also handed to the desktop window (window.pywebview.api.report) so the
 * benchmark can read them.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRM, VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

const params = new URLSearchParams(location.search);
const FPS = Number(params.get("fps") || 60);
const TEX = Number(params.get("tex") || 0);
const DEBUG = params.get("debug") === "1";
const MODEL = params.get("model") || "avatar/model.vrm";
const SCALE = Number(params.get("scale") || 2);
if (DEBUG) document.body.classList.add("debug");

// ----- renderer: transparent, low-power GPU preferred, supersampled (the window is small) -----
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "low-power" });
renderer.setClearColor(0x000000, 0);
renderer.setPixelRatio(window.devicePixelRatio * SCALE);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(24, window.innerWidth / window.innerHeight, 0.1, 20);
camera.position.set(0, 1.28, 2.6);
camera.lookAt(0, 1.18, 0);
scene.add(new THREE.AmbientLight(0xffffff, 0.9));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(0.6, 1.8, 2.2);
scene.add(sun);

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// ----- the model -----
let vrm: VRM | null = null;
const loadStart = performance.now();
let loadMs = 0;

const loader = new GLTFLoader();
loader.register((parser) => new VRMLoaderPlugin(parser));
loader.load(MODEL, (gltf) => {
  const v = gltf.userData.vrm as VRM;
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.combineSkeletons(gltf.scene);
  VRMUtils.rotateVRM0(v);                       // VRoid 0.x models face -Z
  v.scene.traverse((o) => { o.frustumCulled = false; });
  if (TEX > 0) shrinkTextures(v.scene, TEX);
  sharpenTextures(v.scene);
  restPose(v);
  scene.add(v.scene);
  vrm = v;
  loadMs = performance.now() - loadStart;
}, undefined, (err) => { stats.textContent = `couldn't load ${MODEL}: ${err}`; document.body.classList.add("debug"); });

/** Textures above `max` px are redrawn smaller: VRoid exports carry several 2048² maps (~16 MB each on the GPU). */
function shrinkTextures(root: THREE.Object3D, max: number) {
  const done = new Set<THREE.Texture>();
  root.traverse((o) => {
    const mats = (o as THREE.Mesh).material;
    if (!mats) return;
    for (const m of Array.isArray(mats) ? mats : [mats]) {
      for (const value of Object.values(m)) {
        if (!(value instanceof THREE.Texture) || done.has(value)) continue;
        done.add(value);
        const img = value.image as { width: number; height: number } | undefined;
        if (!img || Math.max(img.width, img.height) <= max) continue;
        const k = max / Math.max(img.width, img.height);
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * k);
        c.height = Math.round(img.height * k);
        c.getContext("2d")!.drawImage(value.image as CanvasImageSource, 0, 0, c.width, c.height);
        value.image = c;
        value.needsUpdate = true;
      }
    }
  });
}

/** Anisotropic filtering: textures stay sharp on surfaces seen at an angle (hair, neck, clothes). */
function sharpenTextures(root: THREE.Object3D) {
  const max = renderer.capabilities.getMaxAnisotropy();
  root.traverse((o) => {
    const mats = (o as THREE.Mesh).material;
    if (!mats) return;
    for (const m of Array.isArray(mats) ? mats : [mats]) {
      for (const value of Object.values(m)) {
        if (value instanceof THREE.Texture && value.anisotropy !== max) {
          value.anisotropy = max;
          value.needsUpdate = true;
        }
      }
    }
  });
}

/** Arms down from the T-pose the model is exported in. */
function restPose(v: VRM) {
  const h = v.humanoid;
  h.getNormalizedBoneNode("leftUpperArm")?.rotation.set(0, 0, 1.2);
  h.getNormalizedBoneNode("rightUpperArm")?.rotation.set(0, 0, -1.2);
  h.getNormalizedBoneNode("leftLowerArm")?.rotation.set(0, -0.25, 0.1);
  h.getNormalizedBoneNode("rightLowerArm")?.rotation.set(0, 0.25, -0.1);
}

// ----- idle life: blink, breathe, a slow sway of the head -----
let nextBlink = 1.5;
let blinkT = -1;
function idle(v: VRM, t: number, dt: number) {
  const h = v.humanoid;
  const breath = Math.sin(t * 1.6);
  h.getNormalizedBoneNode("chest")?.rotation.set(breath * 0.012, 0, 0);
  h.getNormalizedBoneNode("spine")?.rotation.set(breath * 0.008, Math.sin(t * 0.31) * 0.02, 0);
  h.getNormalizedBoneNode("neck")?.rotation.set(Math.sin(t * 0.43) * 0.03, Math.sin(t * 0.27) * 0.06, Math.sin(t * 0.37) * 0.02);
  nextBlink -= dt;
  if (nextBlink <= 0 && blinkT < 0) { blinkT = 0; nextBlink = 2.5 + Math.random() * 4; }
  let blink = 0;
  if (blinkT >= 0) {
    blinkT += dt;
    blink = blinkT < 0.07 ? blinkT / 0.07 : Math.max(0, 1 - (blinkT - 0.07) / 0.1);
    if (blinkT > 0.17) blinkT = -1;
  }
  v.expressionManager?.setValue("blink", blink);
}

// ----- loop with a frame cap -----
const clock = new THREE.Clock();
const minGap = 1000 / FPS;
let last = 0;
let frames = 0;
let fps = 0;
let fpsAt = performance.now();
let frameMsSum = 0;
let statFrames = 0;

function frame(now: number) {
  requestAnimationFrame(frame);
  if (document.hidden || now - last < minGap - 1) return;       // skip: over the cap or not visible
  last = now;
  const t0 = performance.now();
  const dt = Math.min(clock.getDelta(), 0.1);
  if (vrm) {
    idle(vrm, clock.elapsedTime, dt);
    vrm.update(dt);                                              // expressions, look-at, spring bones (hair)
  }
  renderer.render(scene, camera);
  frameMsSum += performance.now() - t0;
  statFrames++;
  frames++;
  if (now - fpsAt >= 1000) {
    fps = (frames * 1000) / (now - fpsAt);
    frames = 0;
    fpsAt = now;
  }
}
requestAnimationFrame(frame);

// ----- stats -----
const stats = document.getElementById("stats")!;
function gpuName(): string {
  const gl = renderer.getContext();
  const ext = gl.getExtension("WEBGL_debug_renderer_info");
  return ext ? String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : "unknown";
}
const gpu = gpuName();
let reported = 0;

setInterval(() => {
  const info = renderer.info;
  const s = {
    fps: Math.round(fps * 10) / 10,
    cap: FPS,
    cpu_ms_per_frame: Math.round((frameMsSum / Math.max(1, statFrames)) * 100) / 100,
    draw_calls: info.render.calls,
    triangles: info.render.triangles,
    textures: info.memory.textures,
    geometries: info.memory.geometries,
    gpu,
    tex_limit: TEX,
    load_ms: Math.round(loadMs),
    loaded: !!vrm,
    size: [window.innerWidth, window.innerHeight, renderer.getPixelRatio()],
  };
  frameMsSum = 0;
  statFrames = 0;
  stats.textContent = `fps ${s.fps}/${s.cap}  ${s.cpu_ms_per_frame} ms cpu\ncalls ${s.draw_calls}  tris ${s.triangles}\ntex ${s.textures} (limit ${TEX || "none"})\n${gpu}`;
  const api = (window as unknown as { pywebview?: { api?: { report?: (x: unknown) => void } } }).pywebview?.api;
  if (api?.report && performance.now() - reported > 1900) {
    reported = performance.now();
    api.report(s);
  }
}, 500);
