import { useState } from "react";
import { api } from "../api";
import type { Approval, MaxEvent } from "../types";
import { clock, toolLabel } from "../format";

export function Approvals({ approvals }: { approvals: Approval[] }) {
  const [sending, setSending] = useState<number | null>(null);
  const answer = async (id: number, ok: boolean) => {
    setSending(id);
    try {
      await api.approve(id, ok);
    } finally {
      setSending(null);
    }
  };
  return (
    <section className={`approvals panel ${approvals.length ? "has" : ""}`}>
      <div className="panel-head">
        <span className="label">{approvals.length ? "⚠ approval needed" : "approvals"}</span>
        <span className="label dim">{approvals.length ? `${approvals.length} waiting` : "none waiting"}</span>
      </div>
      {approvals.length === 0 ? (
        <div className="approvals-empty">Risky actions (send, delete, buy, power) wait here for your OK, alongside the spoken yes/no.</div>
      ) : (
        approvals.map((a) => (
          <div key={a.id} className="approval">
            <div className="approval-prompt">{a.prompt}</div>
            {a.tool && <div className="label dim">tool · {a.tool}</div>}
            <div className="approval-actions">
              <button className="btn approve" disabled={sending === a.id} onClick={() => answer(a.id, true)}>Approve</button>
              <button className="btn deny" disabled={sending === a.id} onClick={() => answer(a.id, false)}>Deny</button>
            </div>
          </div>
        ))
      )}
    </section>
  );
}

const ICONS: Record<string, string> = {
  wake: "◉", heard: "›", tool_call: "⚙", tool_result: "✓", escalate: "⇪", answer: "◆",
  approval_request: "⚠", approval_result: "⚖", reminder: "⏰", memory: "✦", notes: "✍",
};

function describe(e: MaxEvent): { text: string; tone?: string } | null {
  const d = e.data || {};
  switch (e.kind) {
    case "wake": return { text: "wake word heard", tone: "cyan" };
    case "heard": return { text: `“${d.text}”${d.source && d.source !== "voice" ? ` (${d.source === "dashboard" ? "typed" : d.source})` : ""}` };
    case "tool_call": return { text: toolLabel({ tool: d.tool, args: d.args }), tone: "violet" };
    case "tool_result": return { text: String(d.result ?? "").slice(0, 120), tone: String(d.result ?? "").startsWith("Error") ? "red" : "green" };
    case "escalate": return { text: `handed to ${d.to}`, tone: "violet" };
    case "answer": return null; // shown in the conversation
    case "approval_request": return { text: d.prompt, tone: "amber" };
    case "approval_result": return { text: `${d.approved ? "approved" : "denied"} by ${d.by}`, tone: d.approved ? "green" : "red" };
    case "reminder": return { text: d.action === "fired" ? d.text : `reminder ${d.action}${d.text ? `: ${d.text}` : ""}`, tone: "amber" };
    case "memory": return { text: `memory ${d.action}${d.text ? `: ${d.text}` : ""}`, tone: "cyan" };
    case "notes": return { text: d.action === "saved" ? `notes saved: ${d.title}` : `notes ${d.action}${d.title ? `: ${d.title}` : ""}`,
                           tone: d.action === "failed" ? "red" : d.action === "started" ? "amber" : "cyan" };
  }
  return null;
}

export function Activity({ events }: { events: MaxEvent[] }) {
  const rows = events.map((e) => ({ e, d: describe(e) })).filter((r) => r.d) as { e: MaxEvent; d: { text: string; tone?: string } }[];
  return (
    <section className="activity panel">
      <div className="panel-head">
        <span className="label">live activity</span>
        <span className="label dim">{rows.length} events</span>
      </div>
      <div className="feed">
        {rows.length === 0 && <div className="approvals-empty">Tool calls, results, reminders and approvals stream here.</div>}
        {[...rows].reverse().map(({ e, d }) => (
          <div key={e.id} className={`feed-row tone-${d.tone || "plain"}`}>
            <span className="feed-icon">{ICONS[e.kind] || "·"}</span>
            <span className="feed-text">{d.text}</span>
            <span className="feed-time">{clock(e.ts)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
