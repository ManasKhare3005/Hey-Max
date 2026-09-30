import type { MutableRefObject } from "react";
import type { MaxState, Message, OrbMode } from "../types";
import Orb from "./Orb";

const LABELS: Record<OrbMode, [string, string]> = {
  idle: ["Standing by", "Say the wake word or type below"],
  listening: ["Listening", "Go ahead, I'm recording"],
  transcribing: ["Transcribing", "Turning your words into text"],
  thinking: ["Thinking", "Working out what to do"],
  speaking: ["Speaking", "Answering out loud"],
  alert: ["Needs approval", "Say yes or no, or use the buttons"],
  offline: ["Offline", "Start Max to connect"],
};

type Props = {
  mode: OrbMode;
  level: MutableRefObject<number>;
  lastWake: number;
  state: MaxState;
  messages: Message[];
};

export default function Hero({ mode, level, lastWake, state, messages }: Props) {
  const [title, sub] = LABELS[mode];
  const lastYou = [...messages].reverse().find((m) => m.role === "you");
  const hb = state.heartbeat || {};
  const phrase = state.wake_phrase && !state.wake_phrase.startsWith("(") ? state.wake_phrase : null;

  return (
    <section className={`hero panel mode-${mode}`}>
      <div className="orb-wrap">
        <Orb mode={mode} level={level} wakeAt={lastWake} />
      </div>
      <div className="hero-info">
        <div className="label">status</div>
        <h1 className="hero-title">{title}</h1>
        <p className="hero-sub">{sub}</p>

        <div className="hero-heard">
          <div className="label">last heard</div>
          <div className="heard-text">{lastYou ? `“${lastYou.text}”` : "—"}</div>
        </div>

        <div className="hero-stats">
          <Stat k="wake word" v={phrase ? `“${phrase}”` : state.mode === "text" ? "text mode" : "—"} />
          <Stat k="speech" v={state.stt || "—"} />
          <Stat k="mic" v={hb.expected ? `${Math.round((100 * hb.frames) / hb.expected)}% · ${hb.level_dbfs} dB` : "—"} />
          <Stat k="planner" v={state.planner_model || "—"} />
        </div>
      </div>
    </section>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="stat">
      <div className="label">{k}</div>
      <div className="stat-v">{v}</div>
    </div>
  );
}
