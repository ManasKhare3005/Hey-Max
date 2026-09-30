import { useEffect, useState } from "react";
import type { MaxState } from "../types";

type Props = { state: MaxState; connected: boolean; stage: string };

function Pill({ label, value, ok, title }: { label: string; value: string; ok: boolean | null; title?: string }) {
  return (
    <div className={`pill ${ok === null ? "" : ok ? "ok" : "bad"}`} title={title}>
      <span className="dot" />
      <span className="pill-label">{label}</span>
      <span className="pill-value">{value}</span>
    </div>
  );
}

export default function TopBar({ state, connected, stage }: Props) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const s = state.services || {};
  const models = Array.isArray(s.models) ? s.models : [];
  const gpu = s.gpu && "vram_used_mb" in s.gpu ? s.gpu : null;
  const fast = models.find((m) => m.name.startsWith((state.fast_model || "").split(":")[0]));
  const onGpu = fast ? Math.round((fast.vram_mb / Math.max(1, fast.size_mb)) * 100) : 0;

  return (
    <header className="topbar panel">
      <div className="brand">
        <img src="/orb.svg" alt="" className="brand-orb" />
        <div>
          <div className="brand-name">{(state.name || "Max").toUpperCase()}</div>
          <div className="brand-sub">mission control</div>
        </div>
        <div className={`status-chip ${connected ? "on" : "off"}`}>
          <span className="dot" />
          {connected ? stage || "online" : "offline"}
        </div>
      </div>

      <div className="pills">
        <Pill label="LLM" value={fast ? `${fast.name} · ${onGpu}% gpu` : state.fast_model ? "idle" : "…"} ok={connected ? !!fast || null : false}
          title="Language model loaded in Ollama and how much of it is on the GPU" />
        <Pill label="VRAM" value={gpu ? `${(gpu.vram_used_mb / 1024).toFixed(1)} / ${(gpu.vram_total_mb / 1024).toFixed(0)} GB` : "–"} ok={gpu ? gpu.vram_used_mb / gpu.vram_total_mb < 0.95 : null} />
        <Pill label="SEARCH" value={s.searxng === true ? "searxng" : "duckduckgo"} ok={s.searxng === true ? true : null}
          title="Private SearXNG in Docker, or the DuckDuckGo fallback" />
        <Pill label="BROWSER" value={s.browser ? "open" : "closed"} ok={s.browser ? true : null} />
        <Pill label="MEMORY" value={s.memory ? "on" : "off"} ok={!!s.memory} />
      </div>

      <div className="clock">
        <div className="clock-time">{now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
        <div className="clock-date">{now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}</div>
      </div>
    </header>
  );
}
