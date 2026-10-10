/**
 * Gestures, drawn in code (no animation files): each is a pose as a function of time, which
 * behaviour.ts blends over his resting pose (easing in and out). Angles are radians on three-vrm's
 * normalised bones; for these VRoid (VRM 0.x) models +X is his right, -Z his front, +Y up, so
 *   right arm: z -1.2 = down by the side, 0 = straight out; y > 0 swings it forward
 *   left arm:  the mirror (z +1.2 down, y < 0 forward)
 *
 *   wave    right hand up by his face, waving (greetings)
 *   nod     two small nods (you said yes / approved)
 *   shake   head shake (you said no / declined)
 *   shrug   shoulders up, palms up in front (errors, "I'm not sure")
 *   chin    hand to chin, held while the big model thinks
 *   bounce  small happy hop (a task done)
 *   tilt    questioning head tilt, held while Max waits for a yes / no
 */

import type { Channel } from "./face";

export type GestureName = "wave" | "nod" | "shake" | "shrug" | "chin" | "bounce" | "tilt";
export type Vec3 = [number, number, number];
export type Bones = Partial<Record<ArmBone, Vec3>>;

export const ARM_BONES = [
  "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
  "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
] as const;
export type ArmBone = (typeof ARM_BONES)[number];

/** Arms down from the T-pose the model is exported in. */
export const REST: Bones = {
  rightUpperArm: [0, 0, -1.2],
  rightLowerArm: [0, 0.25, -0.1],
  leftUpperArm: [0, 0, 1.2],
  leftLowerArm: [0, -0.25, 0.1],
};

export type GesturePose = {
  arms?: Bones;
  head?: Vec3;                                   // added pitch (+ = down), yaw, roll
  lean?: number;                                 // spine forward
  lift?: number;                                 // whole body up (metres)
  face?: Partial<Record<Channel, number>>;
};

type Def = { dur: number; hold?: boolean; easeIn: number; easeOut: number; pose: (t: number) => GesturePose };

const TAU = Math.PI * 2;

export const GESTURES: Record<GestureName, Def> = {
  wave: {
    dur: 2.4, easeIn: 0.35, easeOut: 0.45,
    pose: (t) => {
      const w = Math.sin(t * TAU * 2.0) * 0.25;
      return {
        arms: {
          rightShoulder: [0, 0, 0.08],
          rightUpperArm: [0, 1.1, -0.9],
          rightLowerArm: [0, 0, 2.4 + w],
          rightHand: [0, 0, w * 0.5],
        },
        head: [0, 0.04, -0.06],
        face: { smile: 0.7, eyeSmile: 0.25, browSoft: 0.4 },
      };
    },
  },
  nod: {
    dur: 1.0, easeIn: 0.08, easeOut: 0.15,
    pose: (t) => ({ head: [0.13 * Math.max(0, Math.sin(t * TAU * 2.1)), 0, 0], face: { smile: 0.35, browSoft: 0.3 } }),
  },
  shake: {
    dur: 1.1, easeIn: 0.1, easeOut: 0.2,
    pose: (t) => ({ head: [0.02, 0.16 * Math.sin(t * TAU * 2.2) * (1 - t / 1.4), 0], face: { browDown: 0.25, mouthClose: 0.5 } }),
  },
  shrug: {
    dur: 1.7, easeIn: 0.3, easeOut: 0.45,
    pose: () => ({
      arms: {
        rightShoulder: [0, 0, 0.16],
        leftShoulder: [0, 0, -0.16],
        rightUpperArm: [0, 0.35, -1.2],              // elbows in, forearms forward, palms up
        leftUpperArm: [0, -0.35, 1.2],
        rightLowerArm: [0, 1.45, 0.15],
        leftLowerArm: [0, -1.45, -0.15],
        rightHand: [-1.0, 0, 0],
        leftHand: [-1.0, 0, 0],
      },
      head: [-0.02, 0, 0.12],
      face: { browSad: 0.55, browUp: 0.2, frown: 0.25, mouthClose: 0.5 },
    }),
  },
  chin: {
    dur: 0, hold: true, easeIn: 0.5, easeOut: 0.5,
    pose: () => ({
      arms: {
        rightUpperArm: [0, 1.0, -1.3],
        rightLowerArm: [0, 2.12, 0.3],
        rightHand: [0, 0.9, 0],
      },
      head: [0.04, 0.05, -0.08],
      face: { browDown: 0.35, eyeFocus: 0.2, frown: 0.2, mouthClose: 0.7 },
    }),
  },
  bounce: {
    dur: 0.9, easeIn: 0.05, easeOut: 0.2,
    pose: (t) => {
      const hop = Math.max(0, Math.sin(t * TAU * 1.6));
      return { lift: hop * 0.018, head: [-0.03 * hop, 0, 0], face: { smile: 0.9, eyeSmile: 0.35, browSoft: 0.5 } };
    },
  },
  tilt: {
    dur: 0, hold: true, easeIn: 0.35, easeOut: 0.35,
    pose: () => ({ head: [-0.02, 0.04, 0.14], lean: 0.03, face: { browUp: 0.45, eyeWide: 0.12, mouthClose: 0.5 } }),
  },
};

/**
 * Talking hands: while he speaks a longer sentence he may bring one or both forearms up in front
 * (hands at chest height, inside the picture) and move them in small beats on stressed words.
 * Blended by behaviour.ts under any gesture; BEAT is added (times the beat's strength) on top.
 */
export type TalkName = "right" | "both" | "left";

export const TALK: Record<TalkName, Bones> = {
  right: {                                       // one hand up, fingers up, palm in: making a point
    rightUpperArm: [0, 0.5, -1.2],
    rightLowerArm: [0, 1.7, 0.55],
    rightHand: [0, 0, -0.2],
  },
  left: {
    leftUpperArm: [0, -0.5, 1.2],
    leftLowerArm: [0, -1.7, -0.55],
    leftHand: [0, 0, 0.2],
  },
  both: {                                        // both hands in front of the chest, explaining
    rightUpperArm: [0, 0.7, -1.1],
    rightLowerArm: [0, 1.5, 0.45],
    rightHand: [0, 0, -0.2],
    leftUpperArm: [0, -0.7, 1.1],
    leftLowerArm: [0, -1.5, -0.45],
    leftHand: [0, 0, 0.2],
  },
};

export const BEAT: Bones = {
  rightLowerArm: [0, 0, -0.16],
  rightHand: [0, 0, -0.12],
  leftLowerArm: [0, 0, 0.16],
  leftHand: [0, 0, 0.12],
};
