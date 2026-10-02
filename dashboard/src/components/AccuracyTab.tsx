import { useEffect, useState } from "react";
import { api } from "../api";
import type { EvalData } from "../types";

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** Command accuracy test: how often Max picks the right action, and how fast (evals/commands.yaml). */
export default function AccuracyTab() {
  const [data, setData] = useState<EvalData | null>(null);
  useEffect(() => { api.evals().then(setData).catch(() => setData({ runs: [], latest: null })); }, []);
  if (!data) return <div className="drawer-body"><p className="drawer-note">Loading…</p></div>;
  const latest = data.latest;
  if (!latest) {
    return (
      <div className="drawer-body">
        <p className="drawer-note">No test runs yet. In the project folder run <code>.\.venv\Scripts\python -m max_assistant.evals</code>:
          about 90 realistic commands go to the real model with every tool stubbed (nothing happens on the laptop or phone).</p>
      </div>
    );
  }
  const s = latest.summary;
  const runs = data.runs.slice(-12);
  const misses = latest.results.filter((r) => !r.args_ok);
  const w = 360, h = 70;
  const pts = runs.map((r, i) => `${runs.length < 2 ? w / 2 : (i / (runs.length - 1)) * w},${h - r.full_accuracy * h}`).join(" ");
  return (
    <div className="drawer-body">
      <div className="acc-head">
        <div><div className="acc-big">{pct(s.tool_accuracy)}</div><div className="label dim">right action</div></div>
        <div><div className="acc-big">{pct(s.full_accuracy)}</div><div className="label dim">right details too</div></div>
        <div><div className="acc-big">{s.p50_s}s</div><div className="label dim">median · p95 {s.p95_s}s</div></div>
      </div>
      <p className="drawer-note">{s.n} commands · {s.model} · ~{s.avg_prompt_tokens} prompt tokens · {new Date(s.at).toLocaleString()}</p>
      {runs.length > 1 && (
        <svg className="acc-chart" viewBox={`-6 -6 ${w + 12} ${h + 12}`} role="img" aria-label="Accuracy over the last runs">
          <polyline points={pts} fill="none" stroke="currentColor" strokeWidth="2" />
          {runs.map((r, i) => <circle key={r.file} cx={runs.length < 2 ? w / 2 : (i / (runs.length - 1)) * w} cy={h - r.full_accuracy * h} r="3" fill="currentColor"><title>{`${r.at}: ${pct(r.full_accuracy)}`}</title></circle>)}
        </svg>
      )}
      <div className="acc-list">
        {Object.entries(s.categories).map(([cat, c]) => (
          <div key={cat} className="acc-row">
            <span className="acc-cat">{cat.replace(/_/g, " ")}</span>
            <span className="acc-bar"><span style={{ width: `${(c.full / c.n) * 100}%` }} /></span>
            <span className="label dim">{c.full}/{c.n}</span>
          </div>
        ))}
      </div>
      {misses.length > 0 && <>
        <div className="label dim acc-misses">Misses</div>
        {misses.map((m) => <div key={m.say} className="acc-miss"><b>“{m.say}”</b> <span className="note-summary">{m.why}</span></div>)}
      </>}
    </div>
  );
}
