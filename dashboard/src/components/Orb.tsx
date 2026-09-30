import { useEffect, useRef } from "react";
import type { MutableRefObject } from "react";
import type { OrbMode } from "../types";

type Palette = { core: [number, number, number]; glow: [number, number, number]; ring: [number, number, number] };

const PALETTES: Record<OrbMode, Palette> = {
  idle: { core: [34, 211, 238], glow: [14, 116, 144], ring: [34, 211, 238] },
  listening: { core: [94, 234, 212], glow: [20, 184, 166], ring: [94, 234, 212] },
  transcribing: { core: [125, 211, 252], glow: [56, 128, 196], ring: [125, 211, 252] },
  thinking: { core: [165, 180, 252], glow: [99, 102, 241], ring: [129, 140, 248] },
  speaking: { core: [103, 232, 249], glow: [6, 182, 212], ring: [165, 243, 252] },
  alert: { core: [252, 211, 77], glow: [217, 119, 6], ring: [251, 191, 36] },
  offline: { core: [100, 116, 139], glow: [51, 65, 85], ring: [71, 85, 105] },
};

// How lively each state is: [wobble amount, wobble speed, breathing speed]
const MOTION: Record<OrbMode, [number, number, number]> = {
  idle: [0.035, 0.6, 0.55],
  listening: [0.05, 1.6, 1.2],
  transcribing: [0.06, 2.2, 1.6],
  thinking: [0.08, 2.8, 1.0],
  speaking: [0.07, 2.0, 2.6],
  alert: [0.06, 1.4, 2.0],
  offline: [0.02, 0.3, 0.3],
};

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const rgba = (c: number[], a: number) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;

type Props = { mode: OrbMode; level: MutableRefObject<number>; wakeAt: number };

/** Canvas orb driven by Max's state and the live mic level (read from a ref, no re-renders). */
export default function Orb({ mode, level, wakeAt }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const modeRef = useRef(mode);
  const wakeRef = useRef(wakeAt);
  modeRef.current = mode;
  wakeRef.current = wakeAt;

  useEffect(() => {
    const el = canvas.current!;
    const ctx = el.getContext("2d")!;
    let raf = 0;
    let w = 0, h = 0, dpr = 1;
    const cur = { core: [...PALETTES.idle.core], glow: [...PALETTES.idle.glow], ring: [...PALETTES.idle.ring], amp: 0.035, spd: 0.6, breathe: 0.55, lvl: 0 };
    const pulses: { born: number }[] = [];
    let lastPulse = 0;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      const r = el.getBoundingClientRect();
      w = r.width; h = r.height;
      el.width = Math.round(w * dpr); el.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    const frame = (now: number) => {
      const t = now / 1000;
      const m = modeRef.current;
      const p = PALETTES[m];
      const [amp, spd, br] = MOTION[m];
      const k = 0.06; // smoothing toward the target state
      for (const key of ["core", "glow", "ring"] as const)
        for (let i = 0; i < 3; i++) cur[key][i] = lerp(cur[key][i], p[key][i], k);
      cur.amp = lerp(cur.amp, amp, k); cur.spd = lerp(cur.spd, spd, k); cur.breathe = lerp(cur.breathe, br, k);
      const target = m === "listening" ? level.current : m === "speaking" ? 0.35 + 0.25 * Math.sin(t * 9) ** 2 : 0;
      cur.lvl = lerp(cur.lvl, target, 0.25);

      const cx = w / 2, cy = h / 2;
      const base = Math.min(w, h) * 0.215; // keeps the outer tick ring inside the panel
      // Flash after "Hey Max". wakeAt is wall-clock (Date.now()); `now` is the animation clock
      const sinceWake = Date.now() - wakeRef.current;
      const wakeBoost = sinceWake >= 0 && sinceWake < 900 ? 1 - sinceWake / 900 : 0;
      const breathe = 1 + 0.025 * Math.sin(t * cur.breathe * 2) + cur.lvl * 0.16 + wakeBoost * 0.12;
      const R = base * breathe;
      ctx.clearRect(0, 0, w, h);

      // outer glow
      const g = ctx.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 2.6);
      g.addColorStop(0, rgba(cur.glow, 0.34 + wakeBoost * 0.3));
      g.addColorStop(0.45, rgba(cur.glow, 0.10));
      g.addColorStop(1, rgba(cur.glow, 0));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);

      // speaking / wake pulses
      if ((m === "speaking" && now - lastPulse > 520) || (wakeBoost > 0.97 && now - lastPulse > 300)) {
        pulses.push({ born: now }); lastPulse = now;
      }
      for (let i = pulses.length - 1; i >= 0; i--) {
        const age = (now - pulses[i].born) / 1600;
        if (age > 1) { pulses.splice(i, 1); continue; }
        ctx.beginPath();
        ctx.arc(cx, cy, R * (1.05 + age * 1.1), 0, Math.PI * 2);
        ctx.strokeStyle = rgba(cur.ring, 0.45 * (1 - age));
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }

      // HUD rings
      ctx.save();
      ctx.translate(cx, cy);
      const rings = [
        { r: 1.42, dash: [2, 10], rot: t * 0.12, a: 0.35, lw: 1 },
        { r: 1.62, dash: [40, 14, 6, 14], rot: -t * (m === "thinking" ? 0.9 : 0.18), a: 0.5, lw: 1.4 },
        { r: 1.86, dash: [1, 5], rot: t * 0.05, a: 0.18, lw: 1 },
      ];
      for (const ring of rings) {
        ctx.save();
        ctx.rotate(ring.rot);
        ctx.setLineDash(ring.dash);
        ctx.beginPath();
        ctx.arc(0, 0, base * ring.r, 0, Math.PI * 2);
        ctx.strokeStyle = rgba(cur.ring, ring.a);
        ctx.lineWidth = ring.lw;
        ctx.stroke();
        ctx.restore();
      }
      ctx.setLineDash([]);
      // tick marks
      for (let i = 0; i < 72; i++) {
        const a = (i / 72) * Math.PI * 2 + t * 0.03;
        const long = i % 6 === 0;
        const r1 = base * 2.02, r2 = base * (long ? 2.12 : 2.07);
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1);
        ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2);
        ctx.strokeStyle = rgba(cur.ring, long ? 0.35 : 0.14);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      // thinking: orbiting sparks
      if (m === "thinking" || m === "transcribing") {
        for (let i = 0; i < 3; i++) {
          const a = t * (1.6 + i * 0.45) + (i * Math.PI * 2) / 3;
          const rr = base * (1.25 + 0.12 * i);
          ctx.beginPath();
          ctx.arc(Math.cos(a) * rr, Math.sin(a) * rr, 2.4, 0, Math.PI * 2);
          ctx.fillStyle = rgba(cur.core, 0.9);
          ctx.shadowColor = rgba(cur.core, 1);
          ctx.shadowBlur = 12;
          ctx.fill();
          ctx.shadowBlur = 0;
        }
      }
      ctx.restore();

      // the blob
      const N = 140;
      ctx.beginPath();
      for (let i = 0; i <= N; i++) {
        const th = (i / N) * Math.PI * 2;
        const s = reduce ? 0 : t * cur.spd;
        const wob =
          Math.sin(th * 3 + s) * 0.5 +
          Math.sin(th * 5 - s * 1.3) * 0.3 +
          Math.sin(th * 7 + s * 0.7) * 0.2 +
          (m === "listening" ? Math.sin(th * 8 + s * 2.4) * cur.lvl * 0.35 : 0);
        const r = R * (1 + wob * (cur.amp + cur.lvl * 0.07));
        const x = cx + Math.cos(th) * r, y = cy + Math.sin(th) * r;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
      ctx.closePath();
      const core = ctx.createRadialGradient(cx - R * 0.35, cy - R * 0.4, R * 0.05, cx, cy, R * 1.15);
      core.addColorStop(0, "rgba(240,253,255,0.95)");
      core.addColorStop(0.28, rgba(cur.core, 0.95));
      core.addColorStop(0.75, rgba(cur.glow, 0.85));
      core.addColorStop(1, rgba(cur.glow, 0.35));
      ctx.fillStyle = core;
      ctx.shadowColor = rgba(cur.core, 0.8);
      ctx.shadowBlur = 40;
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.strokeStyle = rgba(cur.core, 0.6);
      ctx.lineWidth = 1.2;
      ctx.stroke();

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [level]);

  return <canvas ref={canvas} className="orb-canvas" aria-label={`Max is ${mode}`} role="img" />;
}
