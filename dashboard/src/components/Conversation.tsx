import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Message } from "../types";
import { clock, toolLabel } from "../format";

type Props = { messages: Message[]; connected: boolean; userName?: string };

const SUGGESTIONS = ["what's the weather in Tempe?", "what reminders do I have?", "open youtube", "what time is it?"];

export default function Conversation({ messages, connected, userName }: Props) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, busy]);

  const send = async (value = text) => {
    const cmd = value.trim();
    if (!cmd || busy) return;
    setBusy(true);
    setError("");
    setText("");
    try {
      await api.command(cmd); // the exchange itself arrives over the live event stream
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't reach Max");
      setText(cmd);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="conversation panel">
      <div className="panel-head">
        <span className="label">conversation</span>
        <span className="label dim">{messages.length ? `${messages.length} messages` : ""}</span>
      </div>

      <div className="messages" ref={list}>
        {messages.length === 0 && (
          <div className="empty">
            <div className="empty-title">No conversation yet</div>
            <div className="empty-sub">Say the wake word, or try one of these:</div>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)} disabled={!connected}>{s}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) => (
          <div key={m.key} className={`msg ${m.role}`}>
            <div className="msg-meta">
              <span>{m.role === "you" ? userName || "you" : "max"}</span>
              {m.source && m.source !== "voice" && <span className="src">{m.source === "dashboard" ? "typed" : m.source}</span>}
              <span className="dim">{clock(m.ts)}</span>
            </div>
            {m.tools && m.tools.length > 0 && (
              <div className="tool-chips">
                {m.tools.map((t, i) => (
                  <span key={i} className="tool-chip" title={t.result || ""}>⚙ {toolLabel(t)}</span>
                ))}
              </div>
            )}
            <div className="bubble">{m.text}</div>
          </div>
        ))}
        {busy && (
          <div className="msg max">
            <div className="bubble typing"><span /><span /><span /></div>
          </div>
        )}
      </div>

      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <span className="prompt">›</span>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={connected ? "Type a command… (replies stay silent)" : "Max is offline"}
          disabled={!connected}
          aria-label="Command"
        />
        <button className="btn primary" disabled={!connected || busy || !text.trim()}>Send</button>
      </form>
      {error && <div className="error-line">{error}</div>}
    </section>
  );
}
