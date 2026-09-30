import type { ToolUse } from "./types";

export const clock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

const NAMES: Record<string, string> = {
  open_website: "web", web_search: "search", browser_click: "click", browser_type: "type",
  browser_navigate: "navigate", browser_media: "video", browser_read: "read page", browser_tabs: "tabs",
  open_app: "open app", close_app: "close app", media_control: "media", volume: "volume",
  take_note: "note", read_notes: "notes", find_files: "find files", open_file: "open file",
  system_status: "status", screenshot: "screenshot", lock_screen: "lock", power: "power",
  cancel_shutdown: "cancel shutdown", free_gpu: "free gpu", get_datetime: "time",
  remember: "remember", recall: "recall", forget: "forget", set_reminder: "reminder",
  list_reminders: "reminders", cancel_reminder: "cancel reminder", think_harder: "think harder",
};

export function toolLabel(t: ToolUse): string {
  const name = NAMES[t.tool] || t.tool.replace(/_/g, " ");
  const args = t.args || {};
  const first = Object.values(args).find((v) => typeof v === "string" || typeof v === "number");
  return first !== undefined ? `${name} · ${String(first).slice(0, 38)}` : name;
}

export function relative(iso: string): string {
  const diff = new Date(iso).getTime() - Date.now();
  const mins = Math.round(diff / 60000);
  if (Math.abs(mins) < 1) return "now";
  if (Math.abs(mins) < 60) return mins > 0 ? `in ${mins} min` : `${-mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (Math.abs(hrs) < 24) return hrs > 0 ? `in ${hrs} h` : `${-hrs} h ago`;
  const days = Math.round(hrs / 24);
  return days > 0 ? `in ${days} d` : `${-days} d ago`;
}

export function whenLabel(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
