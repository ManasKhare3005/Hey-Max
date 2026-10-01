import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { NoteDetail, NoteItem, NotesLive, NotesStatus } from "../types";
import Markdown from "./Markdown";

const mmss = (s = 0) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

/** Meeting & lecture notes: record, list past sessions, read notes or transcripts. */
export default function NotesTab({ revision }: { revision: number }) {
  const [status, setStatus] = useState<NotesStatus>({ active: false, finishing: false });
  const [items, setItems] = useState<NoteItem[]>([]);
  const [open, setOpen] = useState<NoteDetail | null>(null);
  const [view, setView] = useState<"notes" | "summary" | "transcript">("notes");
  const [summarizing, setSummarizing] = useState(false);
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

  const summarize = async (refresh = false) => {
    if (!open || summarizing) return;
    setView("summary");
    if (open.summary_md && !refresh) return;
    setSummarizing(true);
    setError("");
    try {
      const { summary_md } = await api.noteSummary(open.id, refresh);
      setOpen((o) => (o && o.id === open.id ? { ...o, summary_md } : o));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't write the summary");
    } finally {
      setSummarizing(false);
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
          <button className={view === "notes" ? "active" : ""} onClick={() => setView("notes")}>Notes</button>
          <button className={view === "summary" ? "active" : ""} onClick={() => summarize()}>Summary</button>
          <button className={view === "transcript" ? "active" : ""} onClick={() => setView("transcript")}>Transcript</button>
          <button onClick={() => api.openNote(open.id)}>Open folder</button>
        </div>
        {error && <div className="error-line">{error}</div>}
        {view === "transcript" ? <pre className="note-body">{open.transcript_md}</pre>
          : view === "summary" ? (
            <div className="note-body">
              {summarizing ? <p className="drawer-note">✍ Max is writing a short summary (takes ~10–20 s)…</p>
                : open.summary_md ? <>
                    <Markdown text={open.summary_md} />
                    <button className="btn summary-redo" onClick={() => summarize(true)}>↻ Regenerate</button>
                  </>
                : <p className="drawer-note">No summary yet.</p>}
            </div>
          ) : <div className="note-body"><Markdown text={open.notes_md} /></div>}
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
          <LiveTranscript />
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
          <button key={n.id} className="list-row note-row" onClick={() => api.note(n.id).then((d) => { setView("notes"); setError(""); setOpen(d); })}>
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

/** Words as they're said: live captions (dim) that Whisper's accurate text replaces (solid). */
function LiveTranscript() {
  const [live, setLive] = useState<NotesLive>({ active: true });
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);                  // keep scrolled to the newest words unless the user scrolls up

  useEffect(() => {
    let alive = true;
    const tick = () => api.notesLive().then((v) => alive && setLive(v)).catch(() => {});
    tick();
    const t = window.setInterval(tick, 400);
    return () => { alive = false; window.clearInterval(t); };
  }, []);
  useEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [live]);

  const empty = !live.final?.length && !live.live?.length && !live.partial;
  return (
    <div className="live-box" ref={box}
         onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}>
      {empty && <span className="live-wait">{live.captions === false ? "Transcribing every ~30 s…" : "Listening… words appear as they're said."}</span>}
      {live.final?.map((l, i) => <p key={`f${i}`} className="live-final"><span className="live-t">{l.t}</span>{l.text}</p>)}
      {(live.live?.length || live.partial) ? (
        <p className="live-rough">
          {live.live?.map((l) => l.text).join(" ")}{live.live?.length && live.partial ? " " : ""}
          <span className="live-partial">{live.partial}</span><span className="live-caret" />
        </p>
      ) : null}
    </div>
  );
}
