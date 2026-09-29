# Max (formerly Jarvis) — project context for Claude

Handoff from a planning/build chat in the Claude app. Read this before making changes.

## Goal
A Jarvis-style personal voice assistant named **Max**: listens for a wake word, does tasks, browses the web, and works across Manas's Windows laptop, Android phone and Galaxy Watch. Two goals equally: a daily-driver tool AND a portfolio showpiece (clean architecture, dashboard, demo video, strong agentic design).

## Hard decisions (already agreed; don't change without asking)
- **Fully local LLM** via Ollama. No cloud LLM APIs.
- **Laptop is the only host** for now (considered a Mac mini/desktop; decided against for now). Keep the brain a separable service so it can move to another machine later with only a config change.
- **Wake word** "Hey Max" via **sherpa-onnx open-vocabulary keyword spotting** (CPU, always on; phrase is plain text in config, no training). Switched from openWakeWord on 2026-09-28 with Manas's OK because the openWakeWord training notebook is broken (upstream issue #296). openWakeWord `hey_jarvis` kept as `engine: openwakeword` and as automatic fallback. Known: "Hey Mac" also triggers. Single sherpa stream misses ~20% of calls (chunk-alignment sensitivity) -> detector runs 4 streams staggered by 0.16 s + AutoGain (max 8x) for quiet speech; cooldown 1 s. Don't "simplify" back to one stream. Assistant renamed Jarvis → Max on 2026-09-27.
- **Safety**: auto-run reads/safe actions; risky actions (send, delete, buy, close apps, power) require an explicit spoken/typed "yes". Silence or ambiguity = no.
- **Remote access**: phone/watch reach the laptop from anywhere via Tailscale (laptop must stay on).
- **Email/calendar**: Google (Gmail, Google Calendar, Google Tasks).
- **Galaxy Watch**: Wear OS app, tap-to-talk (no always-on wake word on the watch; battery).

## Hardware (Manas's laptop)
- Windows, NVIDIA RTX 3050 Laptop GPU with **4 GB VRAM** (~1.1 GB already used by Windows/apps, so ~3 GB usable), **16 GB RAM**.
- Model plan: `qwen3:4b-instruct` (fast; Instruct-2507, **not** plain `qwen3:4b`, which is now the always-reasoning Thinking-2507 build: 300-2000 hidden tokens/reply) forced 100% onto GPU via `fast_model_gpu_layers: 99` (auto-fallback if load fails) + `qwen3:8b` (planner, hybrid, `think:false` works; GPU+CPU split, only via `think_harder`). `num_ctx 4096`, `num_predict 256`, `keep_alive 15m`. Ollama env: `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0` (setup.ps1 sets them).
- Whisper runs on **CPU** (small.en, int8) so the LLM gets all the VRAM. Piper TTS on CPU.
- Manas games (Riot Vanguard installed) — the `free_gpu` tool ("go to sleep") unloads models.

## Build phases
1. ✅ **Voice loop + laptop control** (built, 27 tests passing, not yet run on real hardware)
2. Agent upgrade + web research/browsing (SearXNG self-hosted search + Playwright)
3. Memory (SQLite + local vector store) + React dashboard (chat, action log, approvals, device status)
4. Android app (Kotlin) + Tailscale + WebSocket link
5. Galaxy Watch (Wear OS) companion
6. Gmail/Calendar/Tasks via Google APIs, polish, demo video, README

## Current status
- Phase 1 runs on real hardware (text + voice). Wake word "Hey Max" (sherpa) confirmed working 2026-09-28.
- Speed fixes done 2026-09-29: 5-turn benchmark 346 s -> 4.4 s (model switch to 4b-instruct, 100% GPU at ~50 tok/s, direct tool replies skip the 2nd LLM call, static system prompt + timestamp in user message so Ollama reuses ~1.6k cached prompt tokens).
- Agent speed design (keep it): tools with `direct=True` return a finished spoken sentence and end the turn; not for compound requests ("and/then") or errors. System prompt must stay free of per-request data.
- Next: Manas to try it in voice mode; then Phase 2 (confirm plan first).

## Code map
- `max_assistant/main.py` — entry point; voice loop and `--text` mode; `Context` object shared with tools
- `max_assistant/agent.py` — tool-calling loop, `think_harder` escalation, safety gate, `parse_yes_no`, short-term history
- `max_assistant/llm.py` — Ollama HTTP client (`/api/chat` with tools, preload/unload)
- `max_assistant/audio.py` — mic stream (80 ms frames), energy VAD recorder with adaptive noise floor, chimes
- `max_assistant/wakeword.py` — `make_detector(cfg.wake_word)`: `SherpaKeywordDetector` / `WakeWordDetector` (openWakeWord), shared cooldown; `--wake-test` to tune
- `max_assistant/stt.py`, `max_assistant/tts.py` (Piper with Windows SAPI fallback)
- `max_assistant/tools/registry.py` — `@reg.tool(description, params, risky=, confirm=)`; `ctx` param is injected
- `max_assistant/tools/system.py` — apps (config aliases + Start Menu fuzzy match), media, volume (media keys), notes, files, screenshot, lock, power, free_gpu
- `config.yaml` — all settings; URI values like `"spotify:"` must be quoted in YAML
- Python package is `max_assistant` (not `max`, which would shadow the builtin); `python -m max_assistant`
- `setup.ps1` / `run.bat` — Windows setup and launcher; `.venv` in project root

## Conventions
- Python 3.11 target. Keep tools small; mark anything destructive or outward-facing `risky=True` with a `confirm` message.
- Spoken replies: 1–2 sentences, no markdown (the TTS strips it anyway).
- Tests must run without a mic, GPU, Ollama or Windows (fake LLM / fake mic). Run: `.\.venv\Scripts\python -m pytest`.
- Manas prefers iterative, hands-on work and complete implementations. Confirm the plan before starting each new phase.
