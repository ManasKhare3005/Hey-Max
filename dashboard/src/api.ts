import type { Fact, MaxState, NoteDetail, NoteItem, NotesStatus, Reminder } from "./types";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  state: () => call<MaxState>("/api/state"),
  turns: (limit = 40) =>
    call<{ id: number; ts: string; user: string; reply: string; tools: string[] }[]>(`/api/turns?limit=${limit}`),
  command: (text: string) => call<{ reply: string }>("/api/command", { method: "POST", body: JSON.stringify({ text }) }),
  approve: (id: number, approved: boolean) =>
    call<{ ok: boolean }>(`/api/approvals/${id}`, { method: "POST", body: JSON.stringify({ approved }) }),
  facts: (q = "") => call<Fact[]>(`/api/facts${q ? `?q=${encodeURIComponent(q)}` : ""}`),
  addFact: (text: string) => call<{ id: number; updated: boolean }>("/api/facts", { method: "POST", body: JSON.stringify({ text }) }),
  deleteFact: (id: number) => call<{ ok: boolean }>(`/api/facts/${id}`, { method: "DELETE" }),
  reminders: () => call<Reminder[]>("/api/reminders"),
  addReminder: (text: string, when: string) =>
    call<{ id: number; due: string; spoken: string }>("/api/reminders", {
      method: "POST",
      body: JSON.stringify({ text, when }),
    }),
  cancelReminder: (id: number) => call<{ ok: boolean }>(`/api/reminders/${id}`, { method: "DELETE" }),
  notesStatus: () => call<NotesStatus>("/api/notes/status"),
  notesStart: (kind: string) => call<{ ok: boolean; title: string }>("/api/notes/start", { method: "POST", body: JSON.stringify({ kind }) }),
  notesStop: () => call<{ ok: boolean }>("/api/notes/stop", { method: "POST" }),
  notes: () => call<NoteItem[]>("/api/notes"),
  note: (id: number) => call<NoteDetail>(`/api/notes/${id}`),
  openNote: (id: number) => call<{ ok: boolean }>(`/api/notes/${id}/open`, { method: "POST" }),
};
