import { useEffect, useState } from "react";
import { api } from "../api";
import type { NoteDetail, NoteItem, NotesStatus } from "../types";
import Markdown from "./Markdown";

const mmss = (s = 0) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

/** Meeting & lecture notes: record, list past sessions, read notes or transcripts. */
export default function NotesTab({ revision }: { revision: number }) {
  const [status, setStatus] = useState<NotesStatus>({ active: false, finishing: false });
  const [items, setItems] = useState<NoteItem[]>([]);
  const [open, setOpen] = useState<NoteDetail | null>(null);
  const [showTranscript, setShowTranscript] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => {
    api.notesStatus().then(setStatus).catch(() => {});
    api.notes().then(setItems).catch(() => {});
  };
  useEffect(() => {
    refresh();
    const t = window.setInterval(() => api.notesStatus().then(setStatus).catch(() => {}), 1500);
    return () => window.clearInterval(t);
  }, [revision]); // eslint-disable-line react-hooks/exhaustive-deps

  const start = async (kind: string) => {
    setError("");
    try {
      await api.notesStart(kind);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start");
    }
  };

  if (open) {
    return (
      <div className="drawer-body">
        <div className="note-head">
          <button className="icon-btn" onClick={() => setOpen(null)} aria-label="Back">‹</button>
          <div>
            <div className="list-text">{open.title}</div>
            <div className="label dim">{new Date(open.started).toLocaleString()} · {open.kind} · {open.words} words</div>
          </div>
        </div>
        <div className="tabs small">
          <button className={!showTranscript ? "active" : ""} onClick={() => setShowTranscript(false)}>Notes</button>
          <button className={showTranscript ? "active" : ""} onClick={() => setShowTranscript(true)}>Transcript</button>
          <button onClick={() => api.openNote(open.id)}>Open folder</button>
        </div>
        {showTranscript ? <pre className="note-body">{open.transcript_md}</pre>
          : <div className="note-body"><Markdown text={open.notes_md} /></div>}
      </div>
    );
  }

  return (
    <div className="drawer-body">
      <p className="drawer-note">
        Max listens to a lecture (mic) or a meeting on this laptop (laptop audio + mic), transcribes as it goes and writes
        notes when you stop. No audio is saved. Say “take notes” / “stop taking notes”, or use these buttons.
      </p>
      {status.active ? (
        <div className="rec-card">
          <div className="rec-top">
            <span className="rec-dot" /> <b>Recording</b> · {status.kind} · {mmss(status.elapsed_s)} · {status.words} words
          </div>
          {status.last && <div className="rec-last">“…{status.last}”</div>}
          <button className="btn deny" onClick={() => api.notesStop().then(refresh)}>Stop & write notes</button>
        </div>
      ) : status.finishing ? (
        <div className="rec-card"><div className="rec-top">✍ Writing up the notes…</div></div>
      ) : (
        <div className="add-grid two">
          <button className="btn primary" onClick={() => start("lecture")}>● Lecture (mic)</button>
          <button className="btn" onClick={() => start("meeting")}>● Meeting (laptop audio)</button>
        </div>
      )}
      {error && <div className="error-line">{error}</div>}
      <div className="list">
        {items.length === 0 && <div className="approvals-empty">No notes yet.</div>}
        {items.map((n) => (
          <button key={n.id} className="list-row note-row" onClick={() => api.note(n.id).then((d) => { setShowTranscript(false); setOpen(d); })}>
            <div>
              <div className="list-text">{n.title}</div>
              <div className="note-summary">{n.summary}</div>
              <div className="label dim">{new Date(n.started).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} · {n.kind} · {n.words} words</div>
            </div>
            <span className="chev">›</span>
          </button>
        ))}
      </div>
    </div>
  );
}
