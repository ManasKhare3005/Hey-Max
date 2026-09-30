import { useEffect, useState } from "react";
import { api } from "../api";
import type { Fact, Reminder } from "../types";
import { relative, whenLabel } from "../format";

type Tab = "memory" | "reminders";
type Props = { open: Tab | null; onClose: () => void; onTab: (t: Tab) => void; revision: number };

export default function Drawer({ open, onClose, onTab, revision }: Props) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  return (
    <>
      <div className={`scrim ${open ? "show" : ""}`} onClick={onClose} />
      <aside className={`drawer panel ${open ? "open" : ""}`} aria-hidden={!open}>
        <div className="drawer-head">
          <div className="tabs">
            <button className={open === "memory" ? "active" : ""} onClick={() => onTab("memory")}>✦ Memory</button>
            <button className={open === "reminders" ? "active" : ""} onClick={() => onTab("reminders")}>⏰ Reminders</button>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        {open === "memory" && <MemoryTab revision={revision} />}
        {open === "reminders" && <RemindersTab revision={revision} />}
      </aside>
    </>
  );
}

function MemoryTab({ revision }: { revision: number }) {
  const [facts, setFacts] = useState<Fact[]>([]);
  const [q, setQ] = useState("");
  const [draft, setDraft] = useState("");
  const load = () => api.facts(q).then(setFacts).catch(() => {});
  useEffect(() => {
    const t = window.setTimeout(load, q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [q, revision]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async () => {
    if (!draft.trim()) return;
    await api.addFact(draft.trim());
    setDraft("");
    load();
  };

  return (
    <div className="drawer-body">
      <p className="drawer-note">What Max remembers about you. Relevant facts are attached to your requests automatically. Say “remember …” or add one here.</p>
      <input className="field" placeholder="Search by meaning… (e.g. “exam”)" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="list">
        {facts.length === 0 && <div className="approvals-empty">{q ? "Nothing matches." : "Nothing saved yet."}</div>}
        {facts.map((f) => (
          <div key={f.id} className="list-row">
            <div>
              <div className="list-text">{f.text}</div>
              <div className="label dim">{q && f.score !== undefined ? `match ${(f.score * 100).toFixed(0)}% · ` : ""}{new Date(f.created).toLocaleDateString()}</div>
            </div>
            <button className="icon-btn danger" title="Forget" onClick={() => api.deleteFact(f.id).then(load)}>✕</button>
          </div>
        ))}
      </div>
      <form className="add-row" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input className="field" placeholder="New fact, e.g. My exam is on Friday" value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button className="btn primary" disabled={!draft.trim()}>Remember</button>
      </form>
    </div>
  );
}

function RemindersTab({ revision }: { revision: number }) {
  const [items, setItems] = useState<Reminder[]>([]);
  const [text, setText] = useState("");
  const [when, setWhen] = useState("");
  const [msg, setMsg] = useState("");
  const load = () => api.reminders().then(setItems).catch(() => {});
  useEffect(() => {
    load();
    const t = window.setInterval(load, 30000);
    return () => window.clearInterval(t);
  }, [revision]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = async () => {
    try {
      const r = await api.addReminder(text.trim(), when.trim());
      setMsg(`Set for ${r.spoken}.`);
      setText("");
      setWhen("");
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Couldn't set it");
    }
  };

  return (
    <div className="drawer-body">
      <p className="drawer-note">Max says these out loud and shows a Windows notification when they're due. Missed ones are announced when Max next starts.</p>
      <div className="list">
        {items.length === 0 && <div className="approvals-empty">No reminders. Try “remind me about my exam Friday at 9”.</div>}
        {items.map((r) => (
          <div key={r.id} className="list-row">
            <div>
              <div className="list-text">{r.text}</div>
              <div className="label dim">{whenLabel(r.due)} · {relative(r.due)}</div>
            </div>
            <button className="icon-btn danger" title="Cancel" onClick={() => api.cancelReminder(r.id).then(load)}>✕</button>
          </div>
        ))}
      </div>
      <form className="add-grid" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input className="field" placeholder="What (e.g. CSE 572 exam)" value={text} onChange={(e) => setText(e.target.value)} />
        <input className="field" placeholder="When (e.g. Thursday 7pm, in 2 hours)" value={when} onChange={(e) => setWhen(e.target.value)} />
        <button className="btn primary" disabled={!text.trim() || !when.trim()}>Set reminder</button>
      </form>
      {msg && <div className="drawer-note">{msg}</div>}
    </div>
  );
}
