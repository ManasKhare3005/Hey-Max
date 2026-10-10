/**
 * Max's avatar: a VRM character that follows Max over the dashboard WebSocket (see behaviour.ts
 * for what it does in each state and link.ts for the connection). URL options (the desktop window,
 * max_assistant/avatar, fills them in from config.yaml):
 *   ?fps=90      frame-rate cap; 0 = every screen refresh (a cap that doesn't divide the refresh
 *                rate, e.g. 60 on a 144 Hz screen, makes motion judder)
 *   ?asleep_fps=30 / ?busy_fps=30   while the models sleep / while Whisper transcribes
 *   ?ws=...      Max's event WebSocket;  ?lipsync_ms=80  mouth delay vs the event
 *   ?standalone=1  don't close when Max is gone
 *   ?frame=upper upper body (default) or full
 *   ?tex=1024    shrink textures larger than this (0 = keep the originals)
 *   ?scale=2     render at this many pixels per screen pixel (supersampling: cleaner hair and edges)
 *   ?snapshot=1  save one rendered frame (just this canvas, not the screen) via the window, for checks
 *   ?debug=1     on-screen stats (fps, draw calls, triangles, GPU name)
 *   ?model=...   VRM to load (default avatar/model.vrm)
 * Every 2 s the stats are also handed to the desktop window (window.pywebview.api.report) so the
 * benchmark can read them.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { VRM, VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";
import { Behaviour, modeOf } from "./behaviour";
import { connect } from "./link";

const params = new URLSearchParams(location.search);
const FPS = Number(params.get("fps") ?? 90);
const ASLEEP_FPS = Number(params.get("asleep_fps") ?? 30);
const BUSY_FPS = Number(params.get("busy_fps") ?? 30);
const WS = params.get("ws") || "ws://127.0.0.1:8765/api/ws";
const LIPSYNC_MS = Number(params.get("lipsync_ms") ?? 80);
const STANDALONE = params.get("standalone") === "1";
const FRAME = params.get("frame") || "upper";
const SNAPSHOT = params.get("snapshot") === "1";
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
const camera = new THREE.PerspectiveCamera(22, window.innerWidth / window.innerHeight, 0.05, 20);
camera.position.set(0, 1.28, 2.6);
camera.lookAt(0, 1.18, 0);

/** Point the camera at the model: from the top of the head down to the waist ("upper") or most of it. */
function frameModel(v: VRM) {
  v.scene.updateMatrixWorld(true);
  const head = v.humanoid.getNormalizedBoneNode("head")?.getWorldPosition(new THREE.Vector3());
  const top = (head?.y ?? 1.45) + 0.28;                           // head bone sits at the base of the skull
  const span = FRAME === "full" ? top - 0.35 : 0.72;              // metres of the body in view
  const centre = top - span / 2;
  const dist = span / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)));
  camera.position.set(0, centre + 0.05, dist);
  camera.lookAt(0, centre, 0);
}
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
let behaviour: Behaviour | null = null;

type Api = { report?: (x: unknown) => void; shape?: (cols: number, rows: number, runs: number[][]) => void; snapshot?: (x: string, mode?: string) => void; toggle?: () => void; close?: () => void };
const api = (): Api | undefined => (window as unknown as { pywebview?: { api?: Api } }).pywebview?.api;

const link = connect(WS, LIPSYNC_MS, {
  onWake: () => behaviour?.wake(),
  onToggle: () => api()?.toggle?.(),
  onOfflineLong: () => { if (!STANDALONE) api()?.close?.(); },
});

// Hidden (tray toggle, or a fullscreen game in front): stop drawing entirely
let hidden = false;
(window as unknown as { maxAvatar: unknown }).maxAvatar = { setHidden: (h: boolean) => { hidden = h; } };
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
  frameModel(v);
  scene.add(v.scene);
  vrm = v;
  behaviour = new Behaviour(v, camera, scene);
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

// ----- loop with a frame cap -----
const clock = new THREE.Clock();
let last = 0;
let cap = FPS;

/** The frame cap right now: lower while the models sleep or Whisper needs the CPU. */
function currentCap(): number {
  if (modeOf(link) === "asleep") return ASLEEP_FPS;
  if (link.sttBusy) return BUSY_FPS;
  return FPS;
}
let frames = 0;
let fps = 0;
let fpsAt = performance.now();
let frameMsSum = 0;
let statFrames = 0;

function frame(now: number) {
  requestAnimationFrame(frame);
  cap = currentCap();
  const minGap = cap > 0 ? 1000 / cap : 0;
  if (hidden || document.hidden || (minGap && now - last < minGap - 1)) return;   // over the cap / hidden
  last = now;
  const t0 = performance.now();
  const dt = Math.min(clock.getDelta(), 0.1);
  if (vrm && behaviour) {
    behaviour.update(modeOf(link), link, dt);
    vrm.update(dt);                                              // expressions, look-at, spring bones (hair)
  }
  renderer.render(scene, camera);
  if (vrm && now - shapeAt > 200) sendShape(now);
  if (SNAPSHOT && vrm && clock.elapsedTime > 3) snapOnModeChange();
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

// ----- outline: the window only takes clicks where Max is drawn -----
// A few times a second the frame is shrunk to a coarse grid (one cell per SHAPE_CELL px); cells he
// covers, grown by SHAPE_GROW cells so his edges and small movements stay inside, go to the window
// as runs per row. The window clips itself to them: clicks anywhere else reach what's behind.
const SHAPE_CELL = 4;
const SHAPE_GROW = 2;
const shapeCanvas = document.createElement("canvas");
const shapeCtx = shapeCanvas.getContext("2d", { willReadFrequently: true })!;
let shapeAt = 0;
let lastShape = "-";

function sendShape(now: number) {
  shapeAt = now;
  const a = api();
  if (!a?.shape) return;
  const cols = Math.ceil(window.innerWidth / SHAPE_CELL);
  const rows = Math.ceil(window.innerHeight / SHAPE_CELL);
  if (shapeCanvas.width !== cols || shapeCanvas.height !== rows) {
    shapeCanvas.width = cols;
    shapeCanvas.height = rows;
  }
  shapeCtx.clearRect(0, 0, cols, rows);
  shapeCtx.drawImage(renderer.domElement, 0, 0, cols, rows);   // right after render: the buffer is still there
  const px = shapeCtx.getImageData(0, 0, cols, rows).data;
  const solid = new Uint8Array(cols * rows);
  for (let y = 0; y < rows; y++)
    for (let x = 0; x < cols; x++)
      if (px[(y * cols + x) * 4 + 3] > 10) {
        for (let dy = -SHAPE_GROW; dy <= SHAPE_GROW; dy++)
          for (let dx = -SHAPE_GROW; dx <= SHAPE_GROW; dx++) {
            const yy = y + dy, xx = x + dx;
            if (yy >= 0 && yy < rows && xx >= 0 && xx < cols) solid[yy * cols + xx] = 1;
          }
      }
  const runs: number[][] = [];
  for (let y = 0; y < rows; y++) {
    let start = -1;
    for (let x = 0; x <= cols; x++) {
      const on = x < cols && solid[y * cols + x] === 1;
      if (on && start < 0) start = x;
      if (!on && start >= 0) { runs.push([y, start, x]); start = -1; }
    }
  }
  const key = runs.join(";");
  if (key === lastShape) return;
  lastShape = key;
  a.shape(cols, rows, runs);
}

// ?snapshot=1: one picture of the canvas per mode, once the pose has settled (for checks)
const snapped = new Set<string>();
let modeSince = { mode: "", at: 0 };
function snapOnModeChange() {
  const mode = modeOf(link);
  if (mode !== modeSince.mode) modeSince = { mode, at: performance.now() };
  const settle = mode === "speaking" ? 400 : mode === "asleep" ? 2500 : 1200;
  const a = api();
  if (!a?.snapshot || snapped.has(mode) || performance.now() - modeSince.at < settle) return;
  snapped.add(mode);
  a.snapshot(renderer.domElement.toDataURL("image/png"), mode);  // right after render: the buffer is still there
}

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
    fps: hidden ? 0 : Math.round(fps * 10) / 10,
    hidden,
    cap,
    mode: modeOf(link),
    online: link.online,
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
  stats.textContent = `fps ${s.fps}/${s.cap || "refresh"}  ${s.cpu_ms_per_frame} ms cpu  ${s.mode}${s.online ? "" : " (offline)"}\ncalls ${s.draw_calls}  tris ${s.triangles}\ntex ${s.textures} (limit ${TEX || "none"})\n${gpu}`;
  const a = api();
  if (a?.report && performance.now() - reported > 1900) {
    reported = performance.now();
    a.report(s);
  }
}, 500);
