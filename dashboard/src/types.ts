export type MaxEvent = {
  id: number;
  ts: number; // seconds since epoch
  kind: string;
  data: Record<string, any>;
};

export type ToolUse = { tool: string; args?: Record<string, unknown>; result?: string };

export type Message = {
  key: string;
  role: "you" | "max";
  text: string;
  ts: number;
  source?: string; // voice | dashboard | keyboard
  tools?: ToolUse[];
};

export type Approval = { id: number; prompt: string; tool: string };

export type Fact = { id: number; text: string; created: string; score?: number };

export type Reminder = { id: number; text: string; due: string };

export type Services = {
  models?: { name: string; vram_mb: number; size_mb: number }[] | { error: string };
  gpu?: { vram_used_mb: number; vram_total_mb: number; util: number } | { error: string };
  searxng?: boolean | { error: string };
  browser?: boolean;
  memory?: boolean;
};

export type MaxState = {
  name?: string;
  user?: string;
  wake_phrase?: string;
  mode?: string;
  fast_model?: string;
  planner_model?: string;
  stt?: string;
  started?: number;
  stage?: { stage?: string };
  heartbeat?: Record<string, any>;
  approvals?: Approval[];
  next_reminder?: Reminder | null;
  services?: Services;
};

export type OrbMode = "idle" | "listening" | "transcribing" | "thinking" | "speaking" | "alert" | "offline";

export type NoteItem = {
  id: number; title: string; kind: string; started: string; ended: string; folder: string; summary: string; words: number;
};
export type NoteDetail = NoteItem & { notes_md: string; transcript_md: string };
export type NotesStatus = {
  active: boolean; finishing: boolean; title?: string; kind?: string; elapsed_s?: number; words?: number; last?: string;
};
