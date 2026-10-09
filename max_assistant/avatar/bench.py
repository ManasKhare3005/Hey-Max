"""What does the avatar cost? Runs the same checks with the avatar off and on, and reports:

- which GPU the avatar renders on, and its memory / 3D load per GPU (Windows performance counters)
- total NVIDIA memory (nvidia-smi) and whether the language model is still 100% in VRAM (Ollama /api/ps)
- the avatar's CPU and RAM (whole WebView2 process tree)
- language model speed (tokens/s and reply time, same settings as Max) and Whisper speed (CPU)

Run: .venv\\Scripts\\python -m max_assistant.avatar.bench   (takes ~5 minutes; nothing is spoken)
"""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psutil
import requests

from ..config import ROOT, load_config
from . import STATS

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PROMPT = "In five short sentences, explain why the sky looks blue during the day and red at sunset."
SPEECH = ("Your next class is CSE 573 at two thirty in Brickyard. The lab report is due Friday at eleven fifty nine. "
          "Do you want me to set a reminder for Thursday evening?")


def counters(seconds: int = 6) -> list[dict]:
    """GPU memory and 3D engine load per process and adapter, averaged over a few seconds."""
    ps = ("$c = Get-Counter -Counter '\\GPU Process Memory(*)\\Dedicated Usage','\\GPU Process Memory(*)\\Shared Usage',"
          "'\\GPU Engine(*engtype_3D)\\Utilization Percentage','\\GPU Adapter Memory(*)\\Dedicated Usage' "
          f"-SampleInterval 1 -MaxSamples {seconds} -ErrorAction SilentlyContinue; "
          "$c | ForEach-Object { $_.CounterSamples } | Select-Object Path, InstanceName, CookedValue | ConvertTo-Json -Compress")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                         timeout=seconds + 60, creationflags=NO_WINDOW).stdout
    rows = json.loads(out or "[]")
    return rows if isinstance(rows, list) else [rows]


def summarize_counters(rows: list[dict], pids: set[int]) -> dict:
    """{luid: {...}} for the avatar's processes, plus which luid is the NVIDIA card."""
    acc: dict[tuple, list[float]] = {}
    for r in rows:
        inst, path, v = r["InstanceName"], r["Path"].lower(), float(r["CookedValue"] or 0)
        luid = "_".join(inst.split("luid_")[1].split("_")[:2]) if "luid_" in inst else "?"
        if "adapter memory" in path:
            acc.setdefault(("adapter", luid), []).append(v)
            continue
        pid = int(inst.split("_")[1]) if inst.startswith("pid_") else -1
        if pid not in pids:
            continue
        kind = "dedicated" if "dedicated" in path else "shared" if "shared" in path else "3d"
        acc.setdefault((kind, luid, pid), []).append(v)
    adapters = {k[1]: statistics.mean(v) for k, v in acc.items() if k[0] == "adapter"}
    nvidia = max(adapters, key=adapters.get) if adapters else "?"       # the one holding the language model
    per: dict[str, dict] = {}
    for key, values in acc.items():
        if key[0] == "adapter":
            continue
        kind, luid, _pid = key
        d = per.setdefault(luid, {"dedicated_mb": 0.0, "shared_mb": 0.0, "3d_pct": 0.0})
        if kind == "3d":
            d["3d_pct"] += statistics.mean(values)
        else:
            d[f"{kind}_mb"] += statistics.mean(values) / 2**20
    named = {("NVIDIA" if luid == nvidia else "iGPU") + f" ({luid})": {k: round(v, 1) for k, v in d.items()}
             for luid, d in per.items() if any(d.values())}
    return named


def nvidia_used_mb() -> float:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW).stdout
    return float(out.strip().splitlines()[0])


def ollama_fit(cfg) -> str:
    models = requests.get(cfg.llm.host.rstrip("/") + "/api/ps", timeout=5).json().get("models", [])
    m = next((m for m in models if m["name"].startswith(cfg.llm.fast_model.split(":")[0])), None)
    if not m:
        return "not loaded"
    return f"{100 * m.get('size_vram', 0) / max(1, m.get('size', 1)):.0f}% in VRAM"


def llm_runs(llm, cfg, n: int = 3) -> dict:
    tps, secs = [], []
    for _ in range(n):
        t = time.perf_counter()
        reply = llm.chat(cfg.llm.fast_model, [{"role": "user", "content": PROMPT}],
                         options={"num_predict": 160, "temperature": 0, "seed": 1})
        secs.append(time.perf_counter() - t)
        raw = reply.raw or {}
        if raw.get("eval_duration"):
            tps.append(raw["eval_count"] / (raw["eval_duration"] / 1e9))
    return {"tokens_per_s": round(statistics.mean(tps), 1) if tps else None, "reply_s": round(statistics.mean(secs), 2)}


def whisper_runs(model, audio: np.ndarray, n: int = 3) -> dict:
    secs = []
    for _ in range(n):
        t = time.perf_counter()
        segments, _ = model.transcribe(audio, language="en", beam_size=1)
        " ".join(s.text for s in segments)
        secs.append(time.perf_counter() - t)
    return {"transcribe_s": round(statistics.median(secs), 2)}


def tree(pid: int) -> list[psutil.Process]:
    try:
        p = psutil.Process(pid)
        return [p, *p.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []


def cpu_ram(procs: list[psutil.Process], seconds: float = 5) -> dict:
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(seconds)
    cpu = ram = 0.0
    for p in procs:
        try:
            cpu += p.cpu_percent(None)
            ram += p.memory_info().rss / 2**20
        except psutil.Error:
            pass
    return {"cpu_pct_of_one_core": round(cpu, 1), "cpu_pct_of_machine": round(cpu / psutil.cpu_count(), 1),
            "ram_mb": round(ram)}


def measure(label: str, llm, cfg, whisper, audio, avatar_pid: int | None) -> dict:
    print(f"\n== {label}", flush=True)
    pids = {p.pid for p in tree(avatar_pid)} if avatar_pid else set()
    r: dict = {"label": label}
    if avatar_pid:
        r["avatar_gpu"] = summarize_counters(counters(), pids)
        r["avatar_cpu_ram"] = cpu_ram(tree(avatar_pid))
        try:
            s = json.loads(STATS.read_text(encoding="utf-8"))
            r["avatar_page"] = {k: s.get(k) for k in ("fps", "cap", "cpu_ms_per_frame", "draw_calls", "triangles", "gpu", "tex_limit")}
        except Exception:
            r["avatar_page"] = "no stats from the page"
    r["nvidia_used_mb"] = nvidia_used_mb()
    r["llm"] = llm_runs(llm, cfg)
    r["llm_fit"] = ollama_fit(cfg)
    r["whisper"] = whisper_runs(whisper, audio)
    print(json.dumps(r, indent=1), flush=True)
    return r


def start_avatar(*args: str) -> subprocess.Popen:
    STATS.unlink(missing_ok=True)
    p = subprocess.Popen([sys.executable, "-m", "max_assistant.avatar", *args], cwd=str(ROOT), creationflags=NO_WINDOW)
    for _ in range(60):
        if STATS.exists():
            break
        time.sleep(0.5)
    time.sleep(8)                                    # past loading, into steady animation
    return p


def stop(p: subprocess.Popen):
    for c in tree(p.pid)[::-1]:
        try:
            c.kill()
        except psutil.Error:
            pass
    time.sleep(3)


def main():
    from faster_whisper import WhisperModel

    from ..llm import OllamaClient
    from ..tts import make_voice

    cfg = load_config()
    llm = OllamaClient(cfg.llm.host, cfg.llm.keep_alive, cfg.llm.temperature, cfg.llm.get("think", False),
                       cfg.llm.request_timeout_s, num_ctx=cfg.llm.get("num_ctx"), num_predict=cfg.llm.get("num_predict"),
                       gpu_layers={cfg.llm.fast_model: cfg.llm.get("fast_model_gpu_layers")}
                       if cfg.llm.get("fast_model_gpu_layers") else None)
    print("warming up the language model and Whisper…", flush=True)
    llm.chat(cfg.llm.fast_model, [{"role": "user", "content": "hi"}], options={"num_predict": 8})
    whisper = WhisperModel(cfg.stt.model, device=cfg.stt.device, compute_type=cfg.stt.compute_type)
    audio, sr = make_voice(cfg.tts).synthesize(SPEECH)
    audio = np.interp(np.arange(0, len(audio), sr / 16000), np.arange(len(audio)), audio).astype(np.float32)
    whisper_runs(whisper, audio, 1)

    results = [measure("avatar off", llm, cfg, whisper, audio, None)]
    for label, args in [("avatar on: 60 fps, full textures, iGPU", ["--fps", "60"]),
                        ("avatar on: 60 fps, textures <= 1024, iGPU", ["--fps", "60", "--tex", "1024"]),
                        ("avatar on: 30 fps (asleep), textures <= 1024, iGPU", ["--fps", "30", "--tex", "1024"]),
                        ("avatar on: 60 fps, full textures, default GPU choice", ["--fps", "60", "--gpu", "default"])]:
        p = start_avatar(*args)
        try:
            results.append(measure(label, llm, cfg, whisper, audio, p.pid))
        finally:
            stop(p)
    results.append(measure("avatar off again", llm, cfg, whisper, audio, None))
    out = ROOT / "data" / "avatar-bench.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
