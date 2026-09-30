# Max — a private, local voice assistant

Say **"Hey Max"** and it listens, thinks with a local LLM, runs tools on your laptop and answers out loud. No cloud: wake word, speech recognition, the LLM and the voice all run on your machine.

**Phases 1–2 of 6** — the voice loop, laptop control, web research and browser control. Coming next: memory + dashboard, Android app, Galaxy Watch app, Gmail/Calendar.

## How it works

```
 mic ──► sherpa-onnx keyword spotter ("Hey Max", CPU, always on)
            │ wake
            ▼
         record until you stop talking ──► faster-whisper (CPU) ──► text
                                                                  │
                                                                  ▼
                        ┌──────────── Agent (plan → tool → check) ────────────┐
                        │ fast model  qwen3:4b-instruct (100% GPU, ~50 tok/s) │
                        │   └─ think_harder ─► qwen3:8b (GPU+CPU, on demand)  │
                        │ safety gate: risky tools need your spoken "yes"     │
                        └──────────────┬──────────────────────────────────────┘
                                       ▼
      tools: apps · files · media · volume · notes · power
             web search (SearXNG, reads the top pages) · Max's own Chrome window
                                       │
                                       ▼
                               Piper voice (CPU) ──► speakers
```

## Setup (one time, ~15 min, mostly downloads)

1. Unzip this folder somewhere like `C:\Users\<you>\Projects\max`.
2. Open **PowerShell** in that folder and run:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup.ps1
   ```
   It installs Python 3.11 and Ollama if missing, creates a virtual environment, installs packages, downloads the voice, wake word, Whisper model and both LLMs (~8 GB total), and starts the private search engine if Docker Desktop is running.

## Run it

| Command | What it does |
|---|---|
| `.\run.bat` | Voice mode. Say "Hey Max", wait for the chime, speak. |
| `.\run.bat --text` | Type instead of talking. Great for testing without a mic. |
| `.\run.bat -v` | Voice mode with detailed logs in the console. |
| `.\run.bat --list-devices` | Show mic/speaker IDs to put in `config.yaml`. |
| `start_max.bat` (double-click) | Voice mode in its own minimized window. Best for everyday use. |

Make sure the **Ollama app is running** (it starts with Windows after install).

## Things to try

- "Hey Max, what time is it?"
- "Open Spotify" · "Pause the music" · "Next song"
- "Set the volume to 30"
- "Take a note: submit the CSE 572 assignment Friday" · "Read my notes"
- "Find my resume" → "Open number 1"
- "How's my battery?"
- "Take a screenshot" · "Lock my laptop"
- "Close Chrome" → asks for confirmation first
- "Go to sleep" → unloads the models to free your GPU for gaming
- "Who won the most recent Super Bowl?" · "What's the weather in Tempe?" → searches and reads the top pages
- "Search for cute dog pics" → "Show images instead" → "Open the first result" → "Go back"
- "Open YouTube" → "Search for lofi music" → "Play the second video" → "Pause it"
- "Open wikipedia.org/wiki/Golden_Retriever" → "How much do they weigh, according to this page?"

## Safety

Tools are marked safe or risky in code. Risky ones (`close_app`, `power`) always ask **"…Say yes or no."** Only a clear yes runs them; silence or anything ambiguous counts as no. Shutdown/restart also wait 10 seconds and can be cancelled ("cancel shutdown").

In the browser, clicks on buttons that buy, pay, send, post, subscribe, delete and similar ask first, and so does pressing Enter anywhere except a search box. Max never types into password fields.

## Tuning (`config.yaml`)

- **False triggers / misses:** `wake_word.sherpa.threshold` (raise to trigger less, lower if it misses you). Check with `--wake-test`.
- **Cuts you off mid-sentence:** raise `audio.silence_s` to 1.5.
- **Doesn't notice you talking:** lower `audio.vad_sensitivity` to 2.0.
- **Speech-to-text speed:** clear recordings go to Moonshine (~0.2 s), quiet or noisy ones to Whisper small.en (~1.8 s, much more accurate there); the split is `stt.fast_min_snr_db`. Transcription starts after 0.4 s of silence (`audio.early_s`) and Max answers right away if the sentence sounds finished; if you trail off ("search for…") it waits up to `audio.max_silence_s`. You can say "Hey Max, open YouTube" in one breath.
- **Quiet voice not waking it:** the wake word is tuned for quiet speech already (`wake_word.sherpa`). What limits it is your voice vs. room noise, which gain can't fix. Turn up the mic in Windows sound settings, get closer to the mic, or turn on your mic's noise suppression ("Voice Clarity" / audio enhancements) in Windows.
- **Different LLMs:** any Ollama model with tool support, e.g. `llama3.2:3b` or `qwen2.5:7b`. Pull it with `ollama pull <name>`.
- **GPU memory:** `llm.keep_alive` sets how long a model stays loaded after use.
- **Add app shortcuts:** add `spoken name: command` under `apps:`. Apps not listed are found automatically via the Start Menu.

## Web search and browsing

- **Search** runs on [SearXNG](https://github.com/searxng/searxng), a private meta-search engine, in Docker on `127.0.0.1:8888` (`docker compose up -d`). If Docker isn't running, Max falls back to DuckDuckGo. Each search also reads the top 4 pages and keeps the passages that match the question (with a preference for the newest year on "latest/most recent" questions), so answers come from the pages, not just snippets. Set `web.read_pages: 0` for snippet-only, faster searches.
- **Browser**: websites and Google searches open in **Max's own Chrome window** (your installed Chrome, separate profile in `data/browser-profile`). Chrome won't let automation attach to your everyday profile, and a separate one keeps Max's logins apart: sign in to sites once in Max's window and it remembers. Max can click things by name ("Images", "Sign in"), by position ("the second video"), type into fields, scroll, go back, read the page and manage tabs.

## Wake word

The wake phrase is plain text in `config.yaml`; no training needed:

```yaml
wake_word:
  engine: sherpa
  phrase: Hey Max      # any English words
```

It uses [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) open-vocabulary keyword spotting (a 3.3M-parameter model, ~5 MB int8, CPU). `setup.ps1` downloads the model to `models/kws/`.

- **Tune:** `.\.venv\Scripts\python -m max_assistant --wake-test` prints each detection and chimes. Say the phrase 10 times, then talk normally or play a video. Missed some → lower `sherpa.threshold` (or raise `boost`); false triggers → raise the threshold.
- **Robustness built in:** 4 staggered decoders (a single one misses ~1 in 5 calls depending on where the phrase falls in the model's 0.64 s audio chunks) and automatic gain for quiet speech (`streams`, `max_gain` in config). Measured on synthetic speech: 100% of normal-volume calls, repeated calls 1 s apart all caught, no false triggers.
- **Choose a good phrase:** two or more words, distinct sounds. "Hey Max" also fires on "Hey Mac" (they sound almost the same), and a single word like "Max" alone would fire constantly.
- **openWakeWord instead:** set `engine: openwakeword` to use its pre-trained models (`hey_jarvis`, `alexa`, `hey_mycroft`) or a custom-trained `.onnx`. If the sherpa model fails to load, Max falls back to this with "Hey Jarvis" and logs a warning.

## Troubleshooting

- **"Can't reach Ollama"** — open the Ollama app, or run `ollama serve`.
- **"Model isn't downloaded"** — run the `ollama pull` command it prints.
- **No wake detection** — run `--list-devices`, set `audio.input_device` to your mic's number; check Windows Settings → Privacy → Microphone allows desktop apps.
- **Stops responding when its window isn't in front / after clicking in its window** — fixed: a program printing to a console freezes when the console stops reading (clicking into a Command Prompt starts "Select" mode; editor terminals like VS Code's pause when inactive). Max now writes console output from a background thread and turns off click-to-select. Every 30 s it logs a heartbeat to `logs/max.log` (stage, mic frames, level, front window); if it's stuck, thread stacks go to `logs/stacks.log`.
- **Robotic voice** — Piper voice file is missing, so it fell back to the Windows voice; re-run `setup.ps1`.
- **Slow first answer** — the model is loading into VRAM; later answers are faster. Close GPU-heavy apps (games, NVIDIA overlay) to help.
- Logs are in `logs/max.log`.

## Project layout

```
max_assistant/
  main.py        voice loop + text mode
  agent.py       tool-calling loop, escalation, safety gate
  llm.py         Ollama client
  audio.py       mic stream, voice-activity recording, chimes
  wakeword.py    wake word engines (sherpa-onnx keyword spotting, openWakeWord)
  stt.py         faster-whisper
  tts.py         Piper / Windows voice
  web.py         SearXNG/DuckDuckGo search, page reading, passage ranking
  browser.py     Max's Chrome window (Playwright), element finding
  tools/
    registry.py  @tool decorator, schemas, risky flags
    system.py    laptop tools
    web.py       web_search
    browser.py   click / type / navigate / read / tabs
tests/           pytest suite (runs without a mic, GPU or Ollama)
```

### Adding a tool

```python
@reg.tool("Describe it for the LLM.", params={"city": {"type": "string"}}, risky=False)
def my_tool(city: str):
    return f"Result for {city}"
```

Run tests with `.\.venv\Scripts\python -m pip install pytest; .\.venv\Scripts\python -m pytest`.
