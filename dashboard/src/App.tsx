import { useCallback, useState } from "react";
import Conversation from "./components/Conversation";
import Drawer, { type Tab } from "./components/Drawer";
import Hero from "./components/Hero";
import { Activity, Approvals } from "./components/SidePanels";
import TopBar from "./components/TopBar";
import { relative } from "./format";
import { useMax } from "./useMax";

export default function App() {
  const max = useMax();
  const [drawer, setDrawer] = useState<Tab | null>(null);
  const close = useCallback(() => setDrawer(null), []);
  const next = max.state.next_reminder;
  const hb = max.state.heartbeat || {};

  return (
    <div className={`app mode-${max.orbMode}`}>
      <div className="backdrop" aria-hidden />
      <TopBar state={max.state} connected={max.connected} stage={max.stage} />

      <main className="main">
        <div className="center">
          <Hero mode={max.orbMode} level={max.level} lastWake={max.lastWake} state={max.state} messages={max.messages} />
          <Conversation messages={max.messages} connected={max.connected} userName={max.state.user} />
        </div>
        <div className="right">
          <Approvals approvals={max.approvals} />
          <Activity events={max.activity} />
        </div>
      </main>

      <footer className="bottombar panel">
        <button className="bar-btn" onClick={() => setDrawer("reminders")}>
          <span className="label">next reminder</span>
          <span className="bar-value">{next ? `${next.text} · ${relative(next.due)}` : "none"}</span>
        </button>
        <button className="bar-btn" onClick={() => setDrawer("memory")}>
          <span className="label">memory</span>
          <span className="bar-value">browse & edit ›</span>
        </button>
        <button className={`bar-btn ${max.recording ? "rec" : ""}`} onClick={() => setDrawer("notes")}>
          <span className="label">notes</span>
          <span className="bar-value">{max.recording ? `● recording ${max.recording}` : "meetings & lectures ›"}</span>
        </button>
        <button className="bar-btn" onClick={() => setDrawer("phone")}>
          <span className="label">phone</span>
          <span className="bar-value">pair ›</span>
        </button>
        <div className="bar-spacer" />
        <div className="bar-btn static">
          <span className="label">listener</span>
          <span className={`bar-value ${hb.ok === false ? "warn" : ""}`}>
            {hb.expected ? (hb.ok === false ? "falling behind" : "healthy") : max.state.mode === "text" ? "text mode" : "—"}
          </span>
        </div>
        <div className="bar-btn static">
          <span className="label">api</span>
          <span className="bar-value"><a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a></span>
        </div>
      </footer>

      {!max.connected && (
        <div className="offline-banner">Can't reach Max. Start it with <code>start_max.bat</code> (reconnecting automatically)…</div>
      )}
      <Drawer open={drawer} onClose={close} onTab={setDrawer} revision={max.revision} />
    </div>
  );
}
