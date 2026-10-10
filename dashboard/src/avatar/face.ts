/**
 * The face in parts. VRoid's preset expressions (Joy, Fun, Surprised...) each move the whole face
 * at once: brows, eyes AND an open-mouthed grin, which looks like a cartoon at any strength. The
 * face mesh also carries the parts on their own (brows, eyes, mouth corners), so expressions here
 * are mixed from those: a raised brow without a gaping mouth, a smile with the lips closed.
 *
 *   browUp     brows raised (interest, emphasis)      browDown  brows lowered (concentrating)
 *   browSad    inner brows up (sympathy, unsure)      browSoft  relaxed, friendly brows
 *   eyeSmile   lower lids up (a real smile reaches the eyes; 1 = closed arcs, so keep it low)
 *   eyeWide    eyes a little wider (surprise, attention)
 *   eyeFocus   lids narrowed (concentrating)
 *   smile      closed-lip smile                        frown     corners down (pressed, "hmm")
 *   mouthClose lips together (the model's resting mouth is slightly parted)
 *
 * Morph targets are found by name suffix (Fcl_BRW_Surprised...), so other VRoid models work too;
 * channels a model lacks are ignored. Applied after vrm.update(), which leaves these morphs alone
 * (no preset expression binds them).
 */
import type * as THREE from "three";
import type { VRM } from "@pixiv/three-vrm";

export const CHANNELS = {
  browUp: "Fcl_BRW_Surprised",
  browDown: "Fcl_BRW_Angry",
  browSad: "Fcl_BRW_Sorrow",
  browSoft: "Fcl_BRW_Fun",
  eyeSmile: "Fcl_EYE_Joy",
  eyeWide: "Fcl_EYE_Surprised",
  eyeFocus: "Fcl_EYE_Angry",
  smile: "Fcl_MTH_Fun",
  frown: "Fcl_MTH_Down",
  mouthClose: "Fcl_MTH_Close",
} as const;
export type Channel = keyof typeof CHANNELS;

type Target = { infl: number[]; index: number };
type GltfJson = { meshes?: { primitives: { extras?: { targetNames?: string[] } }[] }[] };
type Parser = { json: GltfJson; associations: Map<object, { meshes?: number }> };

/**
 * Morph target names by mesh. VRoid keeps them on each primitive (extras.targetNames), where
 * three.js doesn't look, so its meshes only know them as "0", "1"... Read them from the file.
 */
export function nameMorphs(scene: THREE.Object3D, parser: Parser) {
  scene.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.morphTargetInfluences) return;
    const idx = parser.associations.get(m)?.meshes;
    const names = idx === undefined ? undefined : parser.json.meshes?.[idx]?.primitives?.[0]?.extras?.targetNames;
    if (!names || names.length !== m.morphTargetInfluences.length) return;
    m.morphTargetDictionary = Object.fromEntries(names.map((n, i) => [n, i]));
  });
}

export class Face {
  private targets: Partial<Record<Channel, Target[]>> = {};
  private raw: { infl: number[]; index: number; value: number }[] = [];
  readonly values: Record<Channel, number>;

  constructor(vrm: VRM) {
    this.values = Object.fromEntries(Object.keys(CHANNELS).map((k) => [k, 0])) as Record<Channel, number>;
    vrm.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.morphTargetDictionary || !m.morphTargetInfluences) return;
      for (const [name, index] of Object.entries(m.morphTargetDictionary)) {
        for (const [ch, suffix] of Object.entries(CHANNELS) as [Channel, string][]) {
          if (name.endsWith(suffix)) (this.targets[ch] ??= []).push({ infl: m.morphTargetInfluences, index });
        }
      }
    });
  }

  has(ch: Channel): boolean {
    return !!this.targets[ch]?.length;
  }

  /** For checks: hold any morph (by name suffix) at a weight; null clears them all. */
  hold(vrm: VRM, suffix: string | null, value = 1): number {
    if (suffix === null) {
      for (const r of this.raw) r.infl[r.index] = 0;
      this.raw = [];
      return 0;
    }
    vrm.scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.morphTargetDictionary || !m.morphTargetInfluences) return;
      for (const [name, index] of Object.entries(m.morphTargetDictionary))
        if (name.endsWith(suffix)) this.raw.push({ infl: m.morphTargetInfluences, index, value });
    });
    return this.raw.length;
  }

  apply() {
    for (const [ch, list] of Object.entries(this.targets) as [Channel, Target[]][]) {
      const v = Math.min(1, Math.max(0, this.values[ch]));
      for (const t of list) t.infl[t.index] = v;
    }
    for (const r of this.raw) r.infl[r.index] = r.value;
  }
}
