/**
 * What Max's body and face do, blended smoothly. Every frame `update()` works out targets for the
 * current mode and eases the current values towards them, so switching modes never snaps.
 *
 *   idle       breathing, small weight shifts, eyes that never sit perfectly still (saccades), the
 *              odd glance away, and now and then a fidget (a sigh, a look around, a passing smile)
 *   listening  (after the wake word) turns to you, brows up, attentive; nods in your pauses
 *   thinking   (transcribing, then the model) eyes move about up and to the side, brows drawn,
 *              lips pressed: never a hanging-open mouth
 *   speaking   mouth shapes from the voice; on stressed words a small nod, a brow raise and, for
 *              longer sentences, a beat of the hands he has brought up to explain; may look away
 *              as he starts a sentence and back to you to finish it; face follows the words
 *              (a question raises the brows at the end, "sorry" softens them, good news smiles)
 *   paused     relaxed, eyes half closed
 *   asleep     eyes closed, head lowered, slow breathing (models unloaded, or Max offline)
 *
 * The face is mixed from parts (face.ts): VRoid's preset expressions all open the mouth into a
 * grin. On top of the mode: gestures (gesture.ts) and the mouse: when the pointer is near, his
 * eyes and head follow it.
 */
import * as THREE from "three";
import type { VRM } from "@pixiv/three-vrm";
import type { LinkState, Speech } from "./link";
import { ARM_BONES, BEAT, GESTURES, REST, TALK, type ArmBone, type GestureName, type TalkName, type Vec3 } from "./gesture";
import { CHANNELS, Face, type Channel } from "./face";

export type Mode = "idle" | "listening" | "thinking" | "speaking" | "paused" | "asleep";

export function modeOf(s: LinkState): Mode {
  if (!s.online || s.asleep) return "asleep";
  if (s.speech) return "speaking";
  const st = s.stage;
  if (st === "paused") return "paused";
  if (st === "speaking") return "speaking";
  if (st === "thinking") return "thinking";
  if (st === "listening to command") return s.sttBusy ? "thinking" : "listening";   // transcribing = thinking
  return "idle";
}

type Pose = {
  yaw: number; pitch: number; roll: number;          // head
  lean: number;                                       // spine forward
  eyesClosed: number;                                 // 0 open .. 1 closed
  gazeX: number; gazeY: number;                       // eye target offset from the camera (metres)
};
type FaceValues = Record<Channel, number>;

const TARGETS: Record<Mode, Pose> = {
  idle:      { yaw: 0, pitch: 0, roll: 0, lean: 0, eyesClosed: 0, gazeX: 0, gazeY: 0 },
  listening: { yaw: 0, pitch: 0.03, roll: 0.05, lean: 0.05, eyesClosed: 0, gazeX: 0, gazeY: 0 },
  thinking:  { yaw: 0.06, pitch: -0.05, roll: -0.05, lean: 0, eyesClosed: 0, gazeX: -0.45, gazeY: 0.35 },
  speaking:  { yaw: 0, pitch: 0, roll: 0, lean: 0.02, eyesClosed: 0, gazeX: 0, gazeY: 0 },
  paused:    { yaw: 0, pitch: 0.05, roll: 0.03, lean: 0, eyesClosed: 0.35, gazeX: 0, gazeY: -0.2 },
  asleep:    { yaw: 0.05, pitch: 0.2, roll: 0.06, lean: -0.02, eyesClosed: 1, gazeX: 0, gazeY: -0.4 },
};

const zeroFace = (): FaceValues => Object.fromEntries(Object.keys(CHANNELS).map((k) => [k, 0])) as FaceValues;
const FACES: Record<Mode, Partial<FaceValues>> = {
  idle:      { mouthClose: 0.45, smile: 0.05 },
  listening: { mouthClose: 0.5, browUp: 0.3, eyeWide: 0.1 },
  thinking:  { mouthClose: 0.7, frown: 0.2, browDown: 0.3, eyeFocus: 0.15 },
  speaking:  { smile: 0.1, browSoft: 0.15 },
  paused:    { mouthClose: 0.5, browSoft: 0.3 },
  asleep:    { mouthClose: 0.6, browSoft: 0.3 },
};

// Spots his eyes go to while he thinks (offsets from your face, metres): up and to the sides
const THINK_SPOTS: [number, number][] = [[-0.45, 0.35], [0.4, 0.3], [-0.3, 0.05], [0.15, 0.42], [-0.55, 0.2]];

const POSITIVE = /\b(great|done|sure|of course|glad|happy|nice|good|perfect|awesome|hello|hi|hey|welcome|thanks|thank you|absolutely|ready)\b/i;
const SORRY = /\b(sorry|unfortunately|can'?t|couldn'?t|cannot|unable|not sure|don'?t know|failed)\b/i;

const damp = (cur: number, target: number, rate: number, dt: number) => cur + (target - cur) * (1 - Math.exp(-rate * dt));
const rand = (a: number, b: number) => a + Math.random() * (b - a);
/** Smooth wandering in -1..1 (incommensurate sines), different for each seed. */
const wander = (t: number, seed: number) =>
  (Math.sin(t * 0.31 + seed * 1.7) * 0.5 + Math.sin(t * 0.73 + seed * 4.1) * 0.3 + Math.sin(t * 1.37 + seed * 2.3) * 0.2);

/** Stressed syllables in a loudness envelope: sharp rises to a peak (seconds, strength 0..1). */
export function accents(env: number[], rate: number): { at: number; s: number }[] {
  const n = env.length;
  const sm = env.map((_, i) => (env[Math.max(0, i - 1)] + env[i] + env[Math.min(n - 1, i + 1)]) / 3);
  const out: { at: number; s: number }[] = [];
  let last = -1;
  for (let i = 1; i < n - 1; i++) {
    if (!(sm[i] >= sm[i - 1] && sm[i] > sm[i + 1] && sm[i] > 0.45)) continue;
    let low = sm[i];
    for (let k = Math.max(0, i - Math.round(rate * 0.2)); k < i; k++) low = Math.min(low, sm[k]);
    const rise = sm[i] - low;
    if (rise < 0.22 || (last >= 0 && (i - last) / rate < 0.22)) continue;
    out.push({ at: i / rate, s: Math.min(1, rise / 0.6) });
    last = i;
  }
  return out;
}

/** Pauses inside a sentence (quiet for 150 ms or more): start times in seconds. */
function pauses(env: number[], rate: number): number[] {
  const out: number[] = [];
  let quiet = 0;
  for (let i = 0; i < env.length; i++) {
    quiet = env[i] < 0.1 ? quiet + 1 : 0;
    if (quiet === Math.round(rate * 0.15) && i > rate * 0.3) out.push((i - quiet) / rate);
  }
  return out;
}

export class Behaviour {
  readonly face: Face;
  private pose: Pose = { ...TARGETS.idle };
  private faceNow: FaceValues = zeroFace();
  private gaze = new THREE.Object3D();
  private t = 0;
  private blinkT = -1;
  private nextBlink = 1.5;
  private doubleBlink = false;
  private glance = { until: 0, next: 4, x: 0, y: 0 };
  private saccade = { next: 0, x: 0, y: 0, cx: 0, cy: 0 };
  private think = { next: 0, spot: 0 };
  private fidget = { kind: "", at: -99, dur: 0, next: 12, sign: 1 };
  private idleRoll = 0;
  private mood = { kind: "", at: -99 };
  private wakeAt = -10;
  private mouth = 0;
  private names: Record<string, string> = {};
  private visemes = [0, 0, 0, 0, 0];                   // aa ih ou ee oh, smoothed
  private active: { name: GestureName; at: number; releasedAt: number } | null = null;
  private cursor: { x: number; y: number; at: number } | null = null;   // offset from his head, in window heights
  private frozenAt: number | null = null;
  // speech
  private sp = { id: -1, accents: [] as { at: number; s: number }[], pauses: [] as number[], tau: 0,
                 glanceUntil: -1, glanceX: 0, glanceY: 0, question: false, exclaim: false, sorry: false, good: false,
                 live: false, endedAt: -99 };
  private pulse = { nod: 0, brow: 0, beat: 0 };
  private talk: { name: TalkName | null; target: number; w: number; forced: TalkName | null; forcedW: number } =
    { name: null, target: 0, w: 0, forced: null, forcedW: 0 };
  // listening: your voice from the mic level
  private ear = { floor: 0, since: -1, lastLoud: -99, lastNod: -99, at: 0 };
  // how fast the arms move (rad/s, the fastest bone), for the window outline
  private prevArms: Partial<Record<ArmBone, Vec3>> = {};
  armSpeed = 0;

  constructor(private vrm: VRM, private camera: THREE.Camera, scene: THREE.Scene) {
    scene.add(this.gaze);
    if (vrm.lookAt) {
      vrm.lookAt.target = this.gaze;
      vrm.lookAt.autoUpdate = true;
    }
    this.face = new Face(vrm);
    const have = new Set((vrm.expressionManager?.expressions ?? []).map((e) => e.expressionName));
    for (const want of ["blink", "aa", "ih", "ou", "ee", "oh"]) {
      const found = [want, want[0].toUpperCase() + want.slice(1), want.toUpperCase()].find((n) => have.has(n));
      if (found) this.names[want] = found;
    }
  }

  /** The wake word: turn to the user right away, brows up, then blink. */
  wake() {
    this.wakeAt = this.t;
    this.blinkT = -1;
    this.nextBlink = 0.45;
  }

  /** A passing mood from what happened: "pleased" (a greeting, a task done) or "sorry" (a failure). */
  setMood(kind: "pleased" | "sorry") {
    this.mood = { kind, at: this.t };
  }

  /** Start a gesture. Held ones (chin, tilt) stay until release(); a new one replaces the current. */
  gesture(name: GestureName) {
    if (this.active?.name === name && GESTURES[name].hold && this.active.releasedAt < 0) return;
    this.active = { name, at: this.t, releasedAt: -1 };
  }

  /** For checks (?snapshot=1 pictures): hold a gesture fully applied at `age` seconds; null = normal. */
  freeze(name: GestureName | null, age = 0) {
    this.frozenAt = name ? age : null;
    this.active = name ? { name, at: this.t, releasedAt: -1 } : null;
  }

  /** For checks: hold the talking hands in a pose (beat 0..1 added); null = normal. */
  holdTalk(name: TalkName | null, beat = 0) {
    this.talk.forced = name;
    this.talk.forcedW = beat;
  }

  /** The talking hands up now, if any (for checks). */
  get talking(): TalkName | null {
    return this.talk.w > 0.5 ? this.talk.name : null;
  }

  /** The gesture playing now, if any (for checks). */
  get playing(): GestureName | null {
    return this.active?.name ?? null;
  }

  release(name: GestureName) {
    if (this.active?.name === name && this.active.releasedAt < 0) this.active.releasedAt = this.t;
  }

  /** Where the mouse is, relative to his head (window heights; +x right, +y down), or null when far away. */
  setCursor(x: number | null, y = 0) {
    this.cursor = x === null ? null : { x, y, at: this.t };
  }

  /** How strongly the current gesture applies (eases in and out), and its age in seconds. */
  private gestureWeight(): { w: number; age: number } {
    const g = this.active;
    if (!g) return { w: 0, age: 0 };
    if (this.frozenAt !== null) return { w: 1, age: this.frozenAt };
    const def = GESTURES[g.name];
    const age = this.t - g.at;
    if ((g.releasedAt >= 0 && this.t - g.releasedAt > def.easeOut) || (!def.hold && age > def.dur)) {
      this.active = null;
      return { w: 0, age: 0 };
    }
    const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
    let w = smooth(age / def.easeIn);
    if (g.releasedAt >= 0) w *= 1 - smooth((this.t - g.releasedAt) / def.easeOut);
    else if (!def.hold) w *= smooth((def.dur - age) / def.easeOut);
    return { w, age };
  }

  /** A new sentence: find its stressed words and pauses, read its tone, decide on hands and gaze. */
  private startSentence(s: Speech) {
    const gap = this.t - this.sp.endedAt;
    this.sp = {
      ...this.sp, id: s.id, accents: accents(s.env, s.rate), pauses: pauses(s.env, s.rate), tau: -1,
      question: /\?\s*$/.test(s.text), exclaim: /!\s*$/.test(s.text),
      sorry: SORRY.test(s.text), good: POSITIVE.test(s.text) && !SORRY.test(s.text),
      glanceUntil: -1,
    };
    // People often look away to find the words, then back to you
    if (Math.random() < 0.35) {
      const side = Math.random() < 0.5 ? -1 : 1;
      this.sp.glanceUntil = this.t + rand(0.5, 1.1);
      this.sp.glanceX = side * rand(0.25, 0.45);
      this.sp.glanceY = rand(0.05, 0.25);
    }
    // Hands: only for sentences long enough to explain something; often kept between sentences
    const long = (s.duration || s.env.length / s.rate) > 1.6;
    const keep = this.talk.name && gap < 0.8 && Math.random() < 0.6;
    if (!long) this.talk.target = 0;
    else if (!keep) {
      const r = Math.random();
      const pick: TalkName | null = r < 0.35 ? null : r < 0.6 ? "right" : r < 0.85 ? "both" : "left";
      if (pick && (this.talk.w < 0.05 || this.talk.name === pick)) {
        this.talk.name = pick;
        this.talk.target = 1;
      } else this.talk.target = 0;
    } else this.talk.target = 1;
  }

  update(mode: Mode, link: LinkState, dt: number) {
    this.t += dt;
    const t = this.t;
    const awake = mode !== "asleep" && mode !== "paused";
    const target = { ...TARGETS[mode] };
    const face: FaceValues = { ...zeroFace(), ...FACES[mode] };
    const sinceWake = t - this.wakeAt;
    const quick = sinceWake < 0.6;                     // snap towards the user after the wake word
    if (sinceWake < 1.2) {
      const k = 1 - sinceWake / 1.2;
      face.browUp = Math.max(face.browUp, 0.55 * k);
      face.eyeWide = Math.max(face.eyeWide, 0.25 * k);
    }

    // Idle: glances, fidgets, small weight shifts
    if (mode === "idle") {
      if (t > this.glance.next) {
        this.glance = { until: t + rand(0.7, 1.6), next: t + rand(4, 10), x: (Math.random() - 0.5) * 0.9, y: (Math.random() - 0.3) * 0.4 };
        if (Math.random() < 0.5) this.blinkSoon();                  // a blink often comes with a look away
      }
      if (t < this.glance.until) {
        target.gazeX = this.glance.x;
        target.gazeY = this.glance.y;
        target.yaw += this.glance.x * 0.08;
      }
      if (t > this.fidget.next) {
        const kinds = ["sigh", "look", "settle", "smile"];
        this.fidget = { kind: kinds[Math.floor(Math.random() * kinds.length)], at: t, dur: rand(1.6, 2.4), next: t + rand(9, 22), sign: Math.random() < 0.5 ? -1 : 1 };
        if (this.fidget.kind === "settle") this.idleRoll = rand(-0.05, 0.05);
      }
      const f = this.fidget, fa = t - f.at;
      if (fa < f.dur) {
        const k = Math.sin((fa / f.dur) * Math.PI);                  // in and out
        if (f.kind === "look") { target.yaw += f.sign * 0.22 * k; target.gazeX = f.sign * 0.6 * k; target.pitch -= 0.03 * k; }
        if (f.kind === "smile") { face.smile += 0.35 * k; face.eyeSmile += 0.12 * k; face.browSoft += 0.25 * k; face.mouthClose = 0; }
      }
      target.roll += this.idleRoll;
    }

    // Listening: watch the mic; a small nod when you pause after saying something
    if (mode === "listening") {
      const m = link.mic;
      if (m.at !== this.ear.at) {
        this.ear.at = m.at;
        const f = this.ear.floor;
        this.ear.floor = f === 0 || m.rms < f ? m.rms : f + (m.rms - f) * 0.03;
        if (m.rms > Math.max(this.ear.floor * 2.5, this.ear.floor + 80)) {
          this.ear.lastLoud = t;
          if (this.ear.since < 0) this.ear.since = t;
        }
      }
      if (this.ear.since >= 0 && t - this.ear.lastLoud > 0.45 && this.ear.lastLoud - this.ear.since > 0.8) {
        if (t - this.ear.lastNod > 1.6) {
          this.pulse.nod = Math.max(this.pulse.nod, 0.7);
          this.pulse.brow = Math.max(this.pulse.brow, 0.4);
          this.ear.lastNod = t;
        }
        this.ear.since = -1;
      }
    } else this.ear.since = -1;

    // Thinking: the eyes move from spot to spot, the head follows a little
    if (mode === "thinking") {
      if (t > this.think.next) {
        this.think.spot = (this.think.spot + 1 + Math.floor(Math.random() * (THINK_SPOTS.length - 1))) % THINK_SPOTS.length;
        this.think.next = t + rand(1.0, 2.2);
        if (Math.random() < 0.4) this.blinkSoon();
      }
      const [x, y] = THINK_SPOTS[this.think.spot];
      target.gazeX = x;
      target.gazeY = y;
      target.yaw += x * 0.12;
      target.pitch -= y * 0.06;
    }

    // Speaking: mouth shapes, beats on stressed words, tone from the words
    let open = 0;
    const vis = [0, 0, 0, 0, 0];
    let haveVis = false;
    const sp = link.speech;
    if (mode === "speaking" && sp) {
      if (sp.id !== this.sp.id) this.startSentence(sp);
      this.sp.live = true;
      const now = performance.now();
      const tau = (now - sp.startAt) / 1000;
      for (const a of this.sp.accents)
        if (a.at > this.sp.tau && a.at <= tau) {
          this.pulse.nod = Math.max(this.pulse.nod, a.s);
          if (a.s > 0.5) this.pulse.brow = Math.max(this.pulse.brow, a.s);
          this.pulse.beat = Math.max(this.pulse.beat, a.s);
        }
      for (const p of this.sp.pauses) if (p > this.sp.tau && p <= tau && Math.random() < 0.5) this.blinkSoon();
      this.sp.tau = tau;
      const dur = sp.duration || sp.env.length / sp.rate;
      if (dur && tau > dur - 0.35) this.talk.target = 0;

      const vi = Math.floor(tau * sp.rate);
      if (sp.vis.length && vi >= 0 && vi < sp.vis.length) {
        sp.vis[vi].forEach((v, k) => (vis[k] = v));
        haveVis = true;
      }
      if (sp.env.length) {
        open = vi >= 0 && vi < sp.env.length ? sp.env[vi] : 0;
      } else if (now >= sp.startAt) {                 // Windows voice: no audio to measure, a generic rhythm
        open = 0.5 + 0.5 * Math.sin(now / 70) * Math.sin(now / 173);
      }
      open = Math.pow(Math.max(0, open), 0.8);
      target.pitch += open * 0.02;
      // Head drifts more while talking; a glance away at the start of some sentences
      target.yaw += wander(t, 1) * 0.05;
      target.roll += wander(t, 2) * 0.035;
      target.pitch += wander(t, 3) * 0.02;
      if (t < this.sp.glanceUntil) {
        target.gazeX = this.sp.glanceX;
        target.gazeY = this.sp.glanceY;
        target.yaw += this.sp.glanceX * 0.1;
      }
      if (this.sp.sorry) { face.browSad += 0.4; face.smile = 0; face.browSoft = 0; }
      if (this.sp.good) { face.smile += 0.2; face.eyeSmile += 0.08; }
      if (this.sp.exclaim) face.browUp += 0.2;
      if (this.sp.question && dur && tau > dur - 0.7) { face.browUp += 0.4; target.roll += 0.06; target.pitch -= 0.02; }
    } else if (this.sp.live) {
      this.sp.live = false;                            // between sentences / done talking
      this.sp.endedAt = t;
    }
    if (mode !== "speaking" && t - this.sp.endedAt > 0.8) this.talk.target = 0;

    // Mood (a greeting, a task done, a failure), fading over a few seconds
    const ma = t - this.mood.at;
    if (ma < 3) {
      const k = ma < 0.3 ? ma / 0.3 : 1 - (ma - 0.3) / 2.7;
      if (this.mood.kind === "pleased") { face.smile += 0.5 * k; face.eyeSmile += 0.18 * k; face.browSoft += 0.35 * k; }
      if (this.mood.kind === "sorry") { face.browSad += 0.45 * k; face.frown += 0.15 * k; face.smile *= 1 - k; }
    }

    // The mouse nearby: eyes follow it, the head a little (not while thinking, asleep or paused)
    const c = this.cursor;
    if (c && t - c.at < 1 && (mode === "idle" || mode === "listening" || mode === "speaking")) {
      const cl = (v: number, m: number) => Math.max(-m, Math.min(m, v));
      target.gazeX = cl(c.x * 0.55, 0.9);
      target.gazeY = cl(-c.y * 0.55, 0.6);
      target.yaw = cl(c.x * 0.22, 0.3);
      target.pitch += cl(c.y * 0.12, 0.15);
    }

    this.mouth = open > this.mouth ? damp(this.mouth, open, 30, dt) : damp(this.mouth, open, 14, dt);
    for (let k = 0; k < 5; k++) {
      const v = this.visemes[k];
      this.visemes[k] = vis[k] > v ? damp(v, vis[k], 28, dt) : damp(v, vis[k], 16, dt);
    }

    // Ease everything towards the targets
    const rate = quick ? 14 : mode === "asleep" ? 1.5 : 5;
    const p = this.pose;
    for (const k of Object.keys(target) as (keyof Pose)[]) {
      p[k] = damp(p[k], target[k], k === "eyesClosed" && mode !== "asleep" ? 12 : rate, dt);
    }

    // Pulses (nods, brow raises, hand beats): jump up, die away in a fifth of a second
    const nod = this.pulse.nod, brow = this.pulse.brow, beat = this.pulse.beat;
    const decay = Math.exp(-dt / 0.16);
    this.pulse.nod *= decay;
    this.pulse.brow *= Math.exp(-dt / 0.3);
    this.pulse.beat *= decay;

    // Gesture on top
    const { w: gw, age } = this.gestureWeight();
    const gx = this.active && gw > 0 ? GESTURES[this.active.name].pose(age) : null;
    const add = (v: number | undefined) => (v ?? 0) * gw;
    const hp = p.pitch + add(gx?.head?.[0]) + nod * 0.05, hy = p.yaw + add(gx?.head?.[1]), hr = p.roll + add(gx?.head?.[2]);
    if (gx?.face) for (const [k, v] of Object.entries(gx.face) as [Channel, number][]) face[k] = Math.max(face[k], v * gw);
    face.browUp += brow * 0.35;
    if (mode === "speaking") { face.mouthClose = 0; face.smile = Math.min(face.smile, 0.35); }

    // Arms: rest, blended to the talking hands (with beats), then to the gesture's pose
    this.talk.w = damp(this.talk.w, this.talk.target, 4, dt);
    if (this.talk.w < 0.02 && this.talk.target === 0) this.talk.name = null;
    const tName = this.talk.forced ?? this.talk.name;
    const tw = this.talk.forced ? 1 : this.talk.w;
    const tb = this.talk.forced ? this.talk.forcedW : beat;
    const breath = Math.sin(t * 1.6 * (mode === "asleep" ? 0.55 : 1));
    const h = this.vrm.humanoid;
    let speed = 0;
    for (const b of ARM_BONES) {
      const r = REST[b] ?? [0, 0, 0];
      const tp = tName ? TALK[tName][b] : undefined;
      const bt = tp ? BEAT[b] : undefined;
      const base: Vec3 = [0, 1, 2].map((i) => {
        let v = r[i] + ((tp ?? r)[i] - r[i]) * tw + (bt ? bt[i] * tb * tw : 0);
        if (b === "rightShoulder" && i === 2) v += breath * 0.012;   // shoulders rise with the breath
        if (b === "leftShoulder" && i === 2) v -= breath * 0.012;
        return v;
      }) as Vec3;
      const g = gx?.arms?.[b] ?? base;
      const out: Vec3 = [0, 1, 2].map((i) => base[i] + (g[i] - base[i]) * gw) as Vec3;
      h.getNormalizedBoneNode(b)?.rotation.set(out[0], out[1], out[2]);
      const prev = this.prevArms[b];
      if (prev && dt > 0) speed = Math.max(speed, Math.abs(out[0] - prev[0]) / dt, Math.abs(out[1] - prev[1]) / dt, Math.abs(out[2] - prev[2]) / dt);
      this.prevArms[b] = out;
    }
    this.armSpeed = speed;
    this.vrm.scene.position.y = add(gx?.lift);

    // Body: breathing, slow weight shifts
    const sway = mode === "asleep" ? 0.3 : 1;
    const sigh = mode === "idle" && this.fidget.kind === "sigh" && t - this.fidget.at < this.fidget.dur
      ? Math.sin(((t - this.fidget.at) / this.fidget.dur) * Math.PI) : 0;
    h.getNormalizedBoneNode("spine")?.rotation.set(
      breath * 0.008 + p.lean + add(gx?.lean), wander(t, 5) * 0.025 * sway, wander(t, 6) * 0.012 * sway);
    h.getNormalizedBoneNode("chest")?.rotation.set(breath * 0.012 * (mode === "asleep" ? 1.6 : 1) - sigh * 0.04, 0, 0);
    h.getNormalizedBoneNode("neck")?.rotation.set(
      hp * 0.4 + wander(t, 7) * 0.02 * sway, hy * 0.4 + wander(t, 8) * 0.035 * sway, hr * 0.4);
    h.getNormalizedBoneNode("head")?.rotation.set(
      hp * 0.6 + wander(t, 9) * 0.012 * sway + sigh * 0.03, hy * 0.6, hr * 0.6 + wander(t, 10) * 0.015 * sway);

    // Eyes: look at the camera (the user), offset by the mode's gaze, plus small quick jumps
    if (awake && t > this.saccade.next) {
      const busy = mode === "thinking" || mode === "speaking";
      this.saccade = { ...this.saccade, next: t + rand(busy ? 0.3 : 0.5, busy ? 1.2 : 2.4), x: rand(-0.05, 0.05), y: rand(-0.03, 0.03) };
    }
    if (!awake) { this.saccade.x = 0; this.saccade.y = 0; }
    this.saccade.cx = damp(this.saccade.cx, this.saccade.x, 40, dt);
    this.saccade.cy = damp(this.saccade.cy, this.saccade.y, 40, dt);
    const cam = this.camera.position;
    this.gaze.position.set(cam.x + p.gazeX + this.saccade.cx, cam.y + p.gazeY + this.saccade.cy, cam.z);

    // Blinks on their own (sometimes two); eyesClosed covers sleeping / drowsy
    this.nextBlink -= dt;
    if (this.nextBlink <= 0 && this.blinkT < 0 && mode !== "asleep") {
      this.blinkT = 0;
      this.nextBlink = this.doubleBlink ? 0.12 : rand(2, 6);
      this.doubleBlink = !this.doubleBlink && Math.random() < 0.18;
      if (this.doubleBlink) this.nextBlink = 0.25;
    }
    let blink = 0;
    if (this.blinkT >= 0) {
      this.blinkT += dt;
      blink = this.blinkT < 0.06 ? this.blinkT / 0.06 : Math.max(0, 1 - (this.blinkT - 0.06) / 0.12);
      if (this.blinkT > 0.18) this.blinkT = -1;
    }
    const closed = Math.max(blink, p.eyesClosed);
    this.set("blink", closed);

    // Face: ease the parts (pulses were added on top already, so they stay quick)
    for (const k of Object.keys(face) as Channel[]) {
      this.faceNow[k] = damp(this.faceNow[k], face[k], 8, dt);
      this.face.values[k] = this.faceNow[k];
    }
    this.face.values.eyeSmile *= 1 - closed;           // lids that are already closing mustn't close twice
    this.face.values.eyeWide *= 1 - closed;

    // VRoid's mouth shapes at full strength open very wide (tongue showing): talking needs about half
    const [aa, ih, ou, ee, oh] = this.visemes;
    if (haveVis || aa + ih + ou + ee + oh > 0.02) {
      this.set("aa", aa * 0.6);
      this.set("ih", ih * 0.55);
      this.set("ou", ou * 0.6);
      this.set("ee", ee * 0.5);
      this.set("oh", oh * 0.6);
    } else {                                             // no mouth shapes sent (older Max): loudness only
      const m = this.mouth;
      this.set("aa", m * 0.55 * (0.8 + 0.2 * Math.sin(t * 9)));
      this.set("oh", m * 0.25 * (0.5 + 0.5 * Math.sin(t * 5.3)));
      this.set("ih", m * 0.12 * (0.5 + 0.5 * Math.sin(t * 7.1 + 1)));
      this.set("ou", 0);
      this.set("ee", 0);
    }
  }

  private blinkSoon() {
    if (this.blinkT < 0) this.nextBlink = Math.min(this.nextBlink, 0.05);
  }

  private set(name: string, value: number) {
    const n = this.names[name];
    if (n) this.vrm.expressionManager?.setValue(n, Math.min(1, Math.max(0, value)));
  }
}
