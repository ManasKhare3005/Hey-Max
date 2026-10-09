/**
 * What Max's body does in each state, blended smoothly. Every frame `update()` works out targets
 * for the current mode (head and spine angles, where the eyes look, expressions) and eases the
 * current values towards them, so switching modes never snaps.
 *
 *   idle       breathing, slow sway, looks at you with the odd glance away
 *   listening  (after the wake word) turns to you, leans in a little, attentive
 *   thinking   eyes up and to the side, head tilted
 *   speaking   mouth follows the loudness of Max's voice, small nods on stressed syllables
 *   paused     relaxed, eyes half closed
 *   asleep     eyes closed, head lowered, slow breathing (models unloaded, or Max offline)
 */
import * as THREE from "three";
import type { VRM } from "@pixiv/three-vrm";
import type { LinkState } from "./link";

export type Mode = "idle" | "listening" | "thinking" | "speaking" | "paused" | "asleep";

export function modeOf(s: LinkState): Mode {
  if (!s.online || s.asleep) return "asleep";
  if (s.speech) return "speaking";
  const st = s.stage;
  if (st === "paused") return "paused";
  if (st === "speaking") return "speaking";
  if (st === "thinking") return "thinking";
  if (st === "listening to command") return "listening";
  return "idle";
}

type Pose = {
  yaw: number; pitch: number; roll: number;          // head
  lean: number;                                       // spine forward
  eyesClosed: number;                                 // 0 open .. 1 closed
  happy: number; relaxed: number; surprised: number;
  gazeX: number; gazeY: number;                       // eye target offset from the camera (metres)
};

const TARGETS: Record<Mode, Pose> = {
  idle:      { yaw: 0, pitch: 0, roll: 0, lean: 0, eyesClosed: 0, happy: 0.08, relaxed: 0, surprised: 0, gazeX: 0, gazeY: 0 },
  listening: { yaw: 0, pitch: 0.03, roll: 0.05, lean: 0.05, eyesClosed: 0, happy: 0.12, relaxed: 0, surprised: 0.12, gazeX: 0, gazeY: 0 },
  thinking:  { yaw: 0.1, pitch: -0.07, roll: -0.06, lean: 0, eyesClosed: 0, happy: 0, relaxed: 0.1, surprised: 0, gazeX: -0.45, gazeY: 0.35 },
  speaking:  { yaw: 0, pitch: 0, roll: 0, lean: 0.02, eyesClosed: 0, happy: 0.18, relaxed: 0, surprised: 0, gazeX: 0, gazeY: 0 },
  paused:    { yaw: 0, pitch: 0.05, roll: 0.03, lean: 0, eyesClosed: 0.35, happy: 0, relaxed: 0.25, surprised: 0, gazeX: 0, gazeY: -0.2 },
  asleep:    { yaw: 0.05, pitch: 0.2, roll: 0.06, lean: -0.02, eyesClosed: 1, happy: 0, relaxed: 0, surprised: 0, gazeX: 0, gazeY: -0.4 },
};

const damp = (cur: number, target: number, rate: number, dt: number) => cur + (target - cur) * (1 - Math.exp(-rate * dt));

export class Behaviour {
  private pose: Pose = { ...TARGETS.idle };
  private gaze = new THREE.Object3D();
  private t = 0;
  private blinkT = -1;
  private nextBlink = 1.5;
  private glance = { until: 0, next: 4, x: 0, y: 0 };
  private wakeAt = -10;
  private mouth = 0;
  private names: Record<string, string> = {};

  constructor(private vrm: VRM, private camera: THREE.Camera, scene: THREE.Scene) {
    scene.add(this.gaze);
    if (vrm.lookAt) {
      vrm.lookAt.target = this.gaze;
      vrm.lookAt.autoUpdate = true;
    }
    // Expression names differ between models (VRM 0.x VRoid: "Surprised" is a custom one)
    const have = new Set((vrm.expressionManager?.expressions ?? []).map((e) => e.expressionName));
    for (const want of ["blink", "happy", "relaxed", "surprised", "aa", "oh", "ih"]) {
      const found = [want, want[0].toUpperCase() + want.slice(1), want.toUpperCase()].find((n) => have.has(n));
      if (found) this.names[want] = found;
    }
  }

  /** The wake word: turn to the user right away, a little surprised, then blink. */
  wake() {
    this.wakeAt = this.t;
    this.blinkT = -1;
    this.nextBlink = 0.45;
  }

  update(mode: Mode, link: LinkState, dt: number) {
    this.t += dt;
    const t = this.t;
    const target = { ...TARGETS[mode] };
    const sinceWake = t - this.wakeAt;
    const quick = sinceWake < 0.6;                     // snap towards the user after the wake word
    if (sinceWake < 1.2) target.surprised = Math.max(target.surprised, 0.45 * (1 - sinceWake / 1.2));

    // Idle: now and then a short glance somewhere else
    if (mode === "idle") {
      if (t > this.glance.next) {
        this.glance = { until: t + 0.7 + Math.random() * 0.9, next: t + 4 + Math.random() * 6,
                        x: (Math.random() - 0.5) * 0.9, y: (Math.random() - 0.3) * 0.4 };
      }
      if (t < this.glance.until) {
        target.gazeX = this.glance.x;
        target.gazeY = this.glance.y;
        target.yaw += this.glance.x * 0.08;
      }
    }

    // Speaking: mouth from the voice's loudness envelope, a small nod on loud syllables
    let open = 0;
    const sp = link.speech;
    if (mode === "speaking" && sp) {
      const now = performance.now();
      if (sp.env.length) {
        const i = Math.floor(((now - sp.startAt) / 1000) * sp.rate);
        open = i >= 0 && i < sp.env.length ? sp.env[i] : 0;
      } else if (now >= sp.startAt) {                 // Windows voice: no audio to measure, a generic rhythm
        open = 0.5 + 0.5 * Math.sin(now / 70) * Math.sin(now / 173);
      }
      open = Math.pow(Math.max(0, open), 0.8);
      target.pitch += open * 0.035;
    }
    this.mouth = open > this.mouth ? damp(this.mouth, open, 30, dt) : damp(this.mouth, open, 14, dt);

    // Ease everything towards the targets
    const rate = quick ? 14 : mode === "asleep" ? 1.5 : 5;
    const p = this.pose;
    for (const k of Object.keys(target) as (keyof Pose)[]) {
      p[k] = damp(p[k], target[k], k === "eyesClosed" && mode !== "asleep" ? 12 : rate, dt);
    }

    // Body
    const h = this.vrm.humanoid;
    const slow = mode === "asleep" ? 0.55 : 1;
    const breath = Math.sin(t * 1.6 * slow);
    const sway = mode === "asleep" ? 0.3 : 1;
    h.getNormalizedBoneNode("spine")?.rotation.set(breath * 0.008 + p.lean, Math.sin(t * 0.31) * 0.02 * sway, 0);
    h.getNormalizedBoneNode("chest")?.rotation.set(breath * 0.012 * (mode === "asleep" ? 1.6 : 1), 0, 0);
    h.getNormalizedBoneNode("neck")?.rotation.set(
      p.pitch * 0.4 + Math.sin(t * 0.43) * 0.02 * sway, p.yaw * 0.4 + Math.sin(t * 0.27) * 0.04 * sway, p.roll * 0.4);
    h.getNormalizedBoneNode("head")?.rotation.set(
      p.pitch * 0.6 + Math.sin(t * 0.37) * 0.012 * sway, p.yaw * 0.6, p.roll * 0.6 + Math.sin(t * 0.21) * 0.015 * sway);

    // Eyes: look at the camera (the user), offset by the mode's gaze
    const cam = this.camera.position;
    this.gaze.position.set(cam.x + p.gazeX, cam.y + p.gazeY, cam.z);

    // Blinks on their own; eyesClosed covers sleeping / drowsy
    this.nextBlink -= dt;
    if (this.nextBlink <= 0 && this.blinkT < 0 && mode !== "asleep") {
      this.blinkT = 0;
      this.nextBlink = 2.5 + Math.random() * 4;
    }
    let blink = 0;
    if (this.blinkT >= 0) {
      this.blinkT += dt;
      blink = this.blinkT < 0.07 ? this.blinkT / 0.07 : Math.max(0, 1 - (this.blinkT - 0.07) / 0.1);
      if (this.blinkT > 0.17) this.blinkT = -1;
    }
    this.set("blink", Math.max(blink, p.eyesClosed));
    // "happy" narrows the eyes on VRoid models: keep it off while blinking so they don't over-close
    this.set("happy", p.happy * (1 - Math.max(blink, p.eyesClosed)));
    this.set("relaxed", p.relaxed);
    this.set("surprised", p.surprised);
    const m = this.mouth;
    // VRoid's "aa" at full strength opens very wide (tongue showing): talking needs about half
    this.set("aa", m * 0.55 * (0.8 + 0.2 * Math.sin(t * 9)));
    this.set("oh", m * 0.25 * (0.5 + 0.5 * Math.sin(t * 5.3)));
    this.set("ih", m * 0.12 * (0.5 + 0.5 * Math.sin(t * 7.1 + 1)));
  }

  private set(name: string, value: number) {
    const n = this.names[name];
    if (n) this.vrm.expressionManager?.setValue(n, Math.min(1, Math.max(0, value)));
  }
}
