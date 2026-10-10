/**
 * The avatar's line to Max: the dashboard WebSocket (/api/ws). Keeps the few things the avatar
 * reacts to (stage, models asleep, Whisper busy) and hands over moments (wake word, speech).
 * Reconnects on its own; reports "offline" while Max isn't there.
 *
 * Also turns what Max does into gestures (see gesture.ts):
 *   greeting heard, or the first wake word in a while -> wave
 *   Max asks for a yes / no -> questioning tilt (held), then nod (yes) or shake (no)
 *   the big model is called in -> hand to chin (held until the answer)
 *   the answer admits a failure ("I'm not sure", "I couldn't"...) or a tool failed -> shrug
 *   otherwise, if a tool did something this turn -> small happy bounce
 *   a "gesture" event {name} plays one directly
 */
import type { GestureName } from "./gesture";

export type Speech = { env: number[]; vis: number[][]; rate: number; duration: number; startAt: number; text: string; id: number };

export type LinkState = {
  online: boolean;
  stage: string;          // "waiting for wake word", "listening to command", "thinking", "speaking", "paused"...
  asleep: boolean;        // models unloaded ("go to sleep")
  sttBusy: boolean;       // Whisper is transcribing right now
  speech: Speech | null;  // the sentence being spoken, if any
  mic: { rms: number; at: number };   // the microphone's level (~3 a second), for listening nods
};

type Handlers = {
  onWake?: () => void;
  onToggle?: () => void;
  onOfflineLong?: () => void;        // Max has been gone for `offlineCloseMs`
  onGesture?: (name: GestureName) => void;
  onRelease?: (name: GestureName) => void;
  onReset?: () => void;              // tray "Reset avatar position"
  onBusy?: (busy: boolean) => void;  // Whisper started / finished
  onMood?: (mood: "pleased" | "sorry") => void;
};

const GREETING = /^\W*(hi|hello|hey|hiya|yo|howdy|good (morning|afternoon|evening)|what'?s up|sup)\b/i;
const FAILED = /\b(i'?m not sure|i am not sure|i don'?t know|i couldn'?t|i could not|i can'?t|i cannot|i was unable|i'?m unable|sorry)\b/i;
const TOOL_FAILED = /^\s*(error|couldn'?t|could not|failed|sorry|unable|no )/i;
const WAVE_AFTER_MS = 20 * 60 * 1000;   // the wake word gets a wave when Max hasn't been used for this long
const GESTURES = new Set(["wave", "nod", "shake", "shrug", "chin", "bounce", "tilt"]);

type Event = { kind: string; data: Record<string, unknown> };

export function connect(url: string, lipSyncDelayMs: number, handlers: Handlers, offlineCloseMs = 30000): LinkState {
  const state: LinkState = { online: false, stage: "", asleep: false, sttBusy: false, speech: null, mic: { rms: 0, at: 0 } };
  let speechId = 0;
  let retry = 500;
  let offlineSince = performance.now();
  let closedFired = false;
  let lastUsed = -Infinity;
  let toolsOk = 0;
  let toolsFailed = 0;
  const g = (name: GestureName) => handlers.onGesture?.(name);

  setInterval(() => {
    if (!state.online && !closedFired && performance.now() - offlineSince > offlineCloseMs) {
      closedFired = true;
      handlers.onOfflineLong?.();
    }
  }, 1000);

  const handle = (e: Event) => {
    const d = e.data || {};
    switch (e.kind) {
      case "hello": {
        const s = (d.state || {}) as Record<string, Record<string, unknown>>;
        state.stage = String(s.stage?.stage ?? "");
        state.asleep = Boolean(s.gpu?.asleep);
        break;
      }
      case "stage":
        state.stage = String(d.stage ?? "");
        if (state.stage !== "thinking") handlers.onRelease?.("chin");
        break;
      case "gpu":
        state.asleep = Boolean(d.asleep);
        break;
      case "stt":
        state.sttBusy = Boolean(d.busy);
        handlers.onBusy?.(state.sttBusy);
        break;
      case "level":
        state.mic = { rms: Number(d.rms) || 0, at: performance.now() };
        break;
      case "wake":
        handlers.onWake?.();
        if (performance.now() - lastUsed > WAVE_AFTER_MS) g("wave");
        lastUsed = performance.now();
        break;
      case "heard":
        lastUsed = performance.now();
        toolsOk = toolsFailed = 0;
        if (GREETING.test(String(d.text ?? ""))) { g("wave"); handlers.onMood?.("pleased"); }
        break;
      case "confirm":
        g("tilt");
        break;
      case "confirm_result":
        handlers.onRelease?.("tilt");
        g(d.approved ? "nod" : "shake");
        break;
      case "escalate":
        g("chin");
        break;
      case "tool_result":
        if (TOOL_FAILED.test(String(d.result ?? ""))) toolsFailed++;
        else toolsOk++;
        break;
      case "answer":
      case "direct_reply":
        handlers.onRelease?.("chin");
        if (FAILED.test(String(d.text ?? "")) || toolsFailed) { g("shrug"); handlers.onMood?.("sorry"); }
        else if (toolsOk) { g("bounce"); handlers.onMood?.("pleased"); }
        toolsOk = toolsFailed = 0;
        break;
      case "gesture":
        if (GESTURES.has(String(d.name))) g(String(d.name) as GestureName);
        break;
      case "speech":
        if (d.action === "start") {
          // The event leaves Python just before the audio starts; the sound card adds a little latency
          state.speech = {
            env: (d.env as number[]) || [], vis: (d.vis as number[][]) || [],
            rate: Number(d.rate) || 60, duration: Number(d.duration) || 0,
            startAt: performance.now() + lipSyncDelayMs, text: String(d.text ?? ""), id: ++speechId,
          };
        } else {
          state.speech = null;
        }
        break;
      case "avatar":
        if (d.action === "toggle") handlers.onToggle?.();
        if (d.action === "reset") handlers.onReset?.();
        break;
    }
  };

  const open = () => {
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      setTimeout(open, retry);
      return;
    }
    ws.onopen = () => {
      state.online = true;
      closedFired = false;
      retry = 500;
    };
    ws.onmessage = (m) => {
      try {
        handle(JSON.parse(m.data as string) as Event);
      } catch {
        /* not ours */
      }
    };
    ws.onclose = () => {
      if (state.online) offlineSince = performance.now();
      state.online = false;
      state.speech = null;
      if (state.sttBusy) handlers.onBusy?.(false);
      state.sttBusy = false;
      retry = Math.min(retry * 2, 5000);
      setTimeout(open, retry);
    };
  };
  open();
  return state;
}
