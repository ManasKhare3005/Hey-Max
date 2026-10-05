import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Approval, MaxEvent, MaxState, Message, OrbMode, ToolUse } from "./types";

const ACTIVITY_KINDS = new Set([
  "wake", "heard", "tool_call", "tool_result", "escalate", "approval_request", "approval_result",
  "reminder", "memory", "answer", "notes", "docnotes",
]);

/** Live connection to the running Max process: WebSocket events + periodic /api/state. */
export function useMax() {
  const [connected, setConnected] = useState(false);
  const [state, setState] = useState<MaxState>({});
  const [stage, setStage] = useState("starting");
  const [messages, setMessages] = useState<Message[]>([]);
  const [activity, setActivity] = useState<MaxEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [lastWake, setLastWake] = useState(0);
  const [revision, setRevision] = useState(0); // bumps when memory/reminders/notes change
  const [recording, setRecording] = useState(""); // kind of the notes recording in progress, or ""
  const level = useRef(0); // 0..1 mic level for the orb (no re-render per frame)
  const pendingTools = useRef<ToolUse[]>([]);

  const handle = useCallback((e: MaxEvent) => {
    const d = e.data || {};
    switch (e.kind) {
      case "stage":
        setStage(d.stage ?? "");
        break;
      case "level": {
        const rms = Number(d.rms) || 0;
        const db = rms > 0 ? 20 * Math.log10(rms / 32768) : -100;
        level.current = Math.max(0, Math.min(1, (db + 70) / 45)); // -70 dB -> 0, -25 dB -> 1
        return;
      }
      case "wake":
        setLastWake(Date.now());
        break;
      case "heard":
        pendingTools.current = [];
        setMessages((m) => [...m, { key: `h${e.id}`, role: "you", text: d.text, ts: e.ts, source: d.source }]);
        break;
      case "tool_call":
        pendingTools.current = [...pendingTools.current, { tool: d.tool, args: d.args }];
        break;
      case "tool_result": {
        const t = [...pendingTools.current].reverse().find((x) => x.tool === d.tool && x.result === undefined);
        if (t) t.result = String(d.result ?? "");
        break;
      }
      case "answer":
        setMessages((m) => [...m, { key: `a${e.id}`, role: "max", text: d.text, ts: e.ts, tools: pendingTools.current }]);
        pendingTools.current = [];
        break;
      case "approval_request":
        setApprovals((a) => [...a.filter((x) => x.id !== d.id), { id: d.id, prompt: d.prompt, tool: d.tool }]);
        break;
      case "approval_result":
        setApprovals((a) => a.filter((x) => x.id !== d.id));
        break;
      case "reminder":
      case "memory":
      case "docnotes":
        setRevision((r) => r + 1);
        break;
      case "notes":
        setRecording(d.action === "started" ? d.kind || "notes" : "");
        setRevision((r) => r + 1);
        break;
    }
    if (ACTIVITY_KINDS.has(e.kind)) setActivity((a) => [...a.slice(-199), e]);
  }, []);

  // Conversation history from the database, so a refresh doesn't start empty
  useEffect(() => {
    api
      .turns(30)
      .then((turns) => {
        const hist: Message[] = [];
        for (const t of turns) {
          const ts = new Date(t.ts).getTime() / 1000;
          hist.push({ key: `t${t.id}u`, role: "you", text: t.user, ts });
          hist.push({ key: `t${t.id}m`, role: "max", text: t.reply, ts, tools: t.tools.map((tool) => ({ tool })) });
        }
        setMessages((m) => (m.length ? m : hist));
      })
      .catch(() => {});
  }, []);

  // WebSocket with reconnect
  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let delay = 1000;
    let closed = false;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${proto}://${location.host}/api/ws`);
      socket.onopen = () => {
        setConnected(true);
        delay = 1000;
      };
      socket.onmessage = (msg) => {
        const e = JSON.parse(msg.data);
        if (e.kind === "hello") {
          const st = e.data?.state || {};
          if (st.stage?.stage) setStage(st.stage.stage);
          const recent: MaxEvent[] = e.data?.recent || [];
          setActivity(recent.filter((x) => ACTIVITY_KINDS.has(x.kind)).slice(-120));
          return;
        }
        handle(e as MaxEvent);
      };
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, (delay = Math.min(delay * 1.6, 8000)));
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      socket?.close();
    };
  }, [handle]);

  // Status (models, GPU, services) every few seconds
  useEffect(() => {
    let alive = true;
    const poll = () =>
      api
        .state()
        .then((s) => {
          if (!alive) return;
          setState(s);
          if (s.approvals) setApprovals(s.approvals);
        })
        .catch(() => {});
    poll();
    const t = window.setInterval(poll, 4000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [revision]);

  const orbMode: OrbMode = !connected
    ? "offline"
    : approvals.length
      ? "alert"
      : /listening/.test(stage)
        ? "listening"
        : /transcrib/.test(stage)
          ? "transcribing"
          : /thinking/.test(stage)
            ? "thinking"
            : /speaking/.test(stage)
              ? "speaking"
              : "idle";

  return { connected, state, stage, messages, activity, approvals, level, lastWake, revision, orbMode, setMessages, recording };
}
