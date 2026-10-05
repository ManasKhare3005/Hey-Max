"""Command accuracy test: does Max pick the right action, with the right details, quickly?

    .\\.venv\\Scripts\\python -m max_assistant.evals            (all of evals/commands.yaml)
    .\\.venv\\Scripts\\python -m max_assistant.evals phone web   (only some categories)

Uses the real models (Ollama, embeddings) but stubs every tool, so nothing happens on the
laptop or the phone, and risky tools are auto-approved. Results go to data/evals/*.json and
the dashboard's Accuracy tab.
"""
from __future__ import annotations

import json
import logging
import statistics
import sys
import tempfile
import time
from pathlib import Path

import yaml

from .config import ROOT, load_config, resolve_path

log = logging.getLogger(__name__)
CASES = ROOT / "evals" / "commands.yaml"
OUT = resolve_path("data/evals")


# Realistic answers for tools whose output the next step depends on (a list to pick from, etc.)
CANNED = {
    "list_reminders": "Pending reminders: check the oven (today at 5:20 PM); call mom (tomorrow at 6 PM).",
    "find_files": "Found 2 files: 1. Documents/Resume 2026.pdf  2. Downloads/sparql-tutorial.pdf",
    "recall": "Manas's sister's name is Priya.",
    "web_search": "Web results: [1] Example result with the facts the user asked about.",
    "canvas_due": "Due today: Lab 4 for CSE 572, by end of day.",
    "mail_check": "2 unread: 1. From Prof. Jane Smith - Midterm moved (2 h ago) 2. From Canvas - New grade posted (5 h ago)",
    "mail_search": "1. From Prof. Jane Smith - Midterm moved (2 h ago): The midterm is moved to Monday at 10am.",
}


def load_cases(path: Path = CASES, only: list[str] | None = None) -> list[dict]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = []
    for category, items in raw.items():
        if only and category not in only:
            continue
        for item in items:
            turns = item.get("turns") or [item]
            cases.append({"category": category, "turns": [
                {"say": t["say"], "expect": [t["expect"]] if isinstance(t["expect"], str) else list(t["expect"]),
                 "args": t.get("args") or {}, "origin": t.get("origin", "local")} for t in turns]})
    return cases


def judge(turn: dict, calls: list[tuple[str, dict]]) -> tuple[bool, bool, str]:
    """(right tool, right details, why) for one turn."""
    names = [n for n, _ in calls]
    expect = turn["expect"]
    if not names:
        ok = "none" in expect
        return ok, ok, "" if ok else f"no tool called (wanted {'/'.join(expect)})"
    match = next(((n, a) for n, a in calls if n in expect), None)
    if match is None:
        return False, False, f"called {names} (wanted {'/'.join(expect)})"
    name, args = match
    for key, want in turn["args"].items():
        if str(want).lower() not in str(args.get(key, "")).lower():
            return True, False, f"{name}: {key}={args.get(key)!r} (wanted {want!r})"
    return True, True, ""


def build_for_eval(cfg):
    """A full Max (all tools registered) with temporary storage and every tool stubbed."""
    from .events import EventBus
    from .main import build

    tmp = Path(tempfile.mkdtemp(prefix="max-eval-"))
    cfg["memory"] = {"enabled": True, "db": str(tmp / "memory.db")}
    cfg["course"] = {**(cfg.get("course") or {}), "folders": [str(tmp / "course")], "include_lecture_notes": False,
                     "index": str(tmp / "course.db")}
    cfg["notes"] = {**(cfg.get("notes") or {}), "folder": str(tmp / "notes"), "file": str(tmp / "quick.md")}
    secrets = dict(cfg.get("secrets") or {})
    secrets["gmail"] = {"personal": {"address": "eval@example.com", "app_password": "test test test test"},   # stubbed: no login
                        "university": {"address": "eval@school.edu", "app_password": "test test test test"}}
    cfg["secrets"] = secrets
    bus = EventBus()
    confirms: list[str] = []
    # No approval broker: risky tools are approved straight away (from the phone they'd wait for a tap)
    ctx, llm, agent = build(cfg, lambda prompt: confirms.append(prompt) or True, lambda *a: None, bus=bus)
    calls: list[tuple[str, dict]] = []
    for name, tool in agent.registry.tools.items():
        def stub(_name=name, **kwargs):
            calls.append((_name, kwargs))
            return CANNED.get(_name, f"Done ({_name.replace('_', ' ')}).")
        tool.func = stub
    tokens: list[int] = []
    real_chat = llm.chat

    def chat(*a, **kw):
        reply = real_chat(*a, **kw)
        tokens.append(int((reply.raw or {}).get("prompt_eval_count") or 0))
        return reply
    llm.chat = chat
    return agent, calls, tokens


def run(only: list[str] | None = None, cfg=None, verbose: bool = True) -> dict:
    from .events import set_origin

    cfg = cfg or load_config()
    cases = load_cases(only=only)
    agent, calls, tokens = build_for_eval(cfg)
    agent.handle("hello")                                   # load the model before timing anything
    results = []
    for case in cases:
        agent.history.clear()
        for turn in case["turns"]:
            calls.clear()
            tokens.clear()
            set_origin(turn["origin"])
            t0 = time.perf_counter()
            try:
                answer = agent.handle(turn["say"])
            except Exception as exc:                        # a crash is a failure, not the end of the run
                answer = f"(crashed: {exc})"
            finally:
                set_origin("local")
            seconds = time.perf_counter() - t0
            tool_ok, args_ok, why = judge(turn, list(calls))
            r = {"category": case["category"], "say": turn["say"], "origin": turn["origin"], "expect": turn["expect"],
                 "called": [n for n, _ in calls], "tool_ok": tool_ok, "args_ok": args_ok, "why": why,
                 "seconds": round(seconds, 2), "prompt_tokens": max(tokens or [0]), "answer": answer[:200]}
            results.append(r)
            if verbose:
                mark = "OK " if args_ok else ("~  " if tool_ok else "BAD")
                print(f"{mark} {seconds:5.2f}s {r['prompt_tokens']:5d} tok  {case['category']:16s} {turn['say'][:52]:52s} "
                      f"{','.join(r['called']) or '-'}  {why}")
    summary = summarize(results, cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    path.write_text(json.dumps({"summary": summary, "results": results}, indent=2), encoding="utf-8")
    if verbose:
        print(f"\nRight action: {summary['tool_accuracy']:.0%}   right details too: {summary['full_accuracy']:.0%}   "
              f"median {summary['p50_s']}s, 95th percentile {summary['p95_s']}s   ({summary['n']} commands)")
        for cat, c in summary["categories"].items():
            print(f"  {cat:18s} {c['full']}/{c['n']}")
        print(f"Saved {path}")
    return {"summary": summary, "results": results, "path": str(path)}


def summarize(results: list[dict], cfg=None) -> dict:
    n = len(results) or 1
    secs = sorted(r["seconds"] for r in results) or [0]
    cats: dict[str, dict] = {}
    for r in results:
        c = cats.setdefault(r["category"], {"n": 0, "tool": 0, "full": 0})
        c["n"] += 1
        c["tool"] += r["tool_ok"]
        c["full"] += r["args_ok"]
    return {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "n": len(results),
        "tool_accuracy": round(sum(r["tool_ok"] for r in results) / n, 3),
        "full_accuracy": round(sum(r["args_ok"] for r in results) / n, 3),
        "p50_s": round(statistics.median(secs), 2), "p95_s": round(secs[min(len(secs) - 1, int(0.95 * len(secs)))], 2),
        "avg_prompt_tokens": round(sum(r["prompt_tokens"] for r in results) / n),
        "model": (cfg.llm.fast_model if cfg is not None else ""),
        "categories": cats,
    }


def runs() -> list[dict]:
    """Saved runs, oldest first (summaries only), plus the latest run's details."""
    if not OUT.is_dir():
        return []
    out = []
    for f in sorted(OUT.glob("*.json")):
        try:
            out.append({"file": f.name, **json.loads(f.read_text(encoding="utf-8"))["summary"]})
        except Exception:
            continue
    return out


def latest() -> dict | None:
    files = sorted(OUT.glob("*.json")) if OUT.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    run(sys.argv[1:] or None)
