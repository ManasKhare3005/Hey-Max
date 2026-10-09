/**
 * The avatar's line to Max: the dashboard WebSocket (/api/ws). Keeps the few things the avatar
 * reacts to (stage, models asleep, Whisper busy) and hands over moments (wake word, speech).
 * Reconnects on its own; reports "offline" while Max isn't there.
 */

export type Speech = { env: number[]; rate: number; duration: number; startAt: number };

export type LinkState = {
  online: boolean;
  stage: string;          // "waiting for wake word", "listening to command", "thinking", "speaking", "paused"...
  asleep: boolean;        // models unloaded ("go to sleep")
  sttBusy: boolean;       // Whisper is transcribing right now
  speech: Speech | null;  // the sentence being spoken, if any
};

type Handlers = {
  onWake?: () => void;
  onToggle?: () => void;
  onOfflineLong?: () => void;        // Max has been gone for `offlineCloseMs`
};

type Event = { kind: string; data: Record<string, unknown> };

export function connect(url: string, lipSyncDelayMs: number, handlers: Handlers, offlineCloseMs = 30000): LinkState {
  const state: LinkState = { online: false, stage: "", asleep: false, sttBusy: false, speech: null };
  let retry = 500;
  let offlineSince = performance.now();
  let closedFired = false;

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
        break;
      case "gpu":
        state.asleep = Boolean(d.asleep);
        break;
      case "stt":
        state.sttBusy = Boolean(d.busy);
        break;
      case "wake":
        handlers.onWake?.();
        break;
      case "speech":
        if (d.action === "start") {
          // The event leaves Python just before the audio starts; the sound card adds a little latency
          state.speech = {
            env: (d.env as number[]) || [], rate: Number(d.rate) || 60, duration: Number(d.duration) || 0,
            startAt: performance.now() + lipSyncDelayMs,
          };
        } else {
          state.speech = null;
        }
        break;
      case "avatar":
        if (d.action === "toggle") handlers.onToggle?.();
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
      state.sttBusy = false;
      retry = Math.min(retry * 2, 5000);
      setTimeout(open, retry);
    };
  };
  open();
  return state;
}
