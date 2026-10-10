# Max — a private, local voice assistant

Say **"Hey Max"** and it listens, thinks with a local LLM, runs tools on your laptop and answers out loud. No cloud: wake word, speech recognition, the LLM and the voice all run on your machine.

**Phases 1–3 of 6** — the voice loop, laptop control, web research, browser control, memory, reminders and a live dashboard. Coming next: Android app, Galaxy Watch app, Gmail/Calendar.

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
- "Search for something" → Max asks "What should I search for?" → just answer, no "Hey Max" needed (it listens for 5 s after any question it asks; `audio.follow_up`)
- "Who won the most recent Super Bowl?" · "What's the weather in Tempe?" → searches and reads the top pages
- "Search for cute dog pics" → "Show images instead" → "Open the first result" → "Go back"
- "Open YouTube" → "Search for lofi music" → "Play the second video" → "Pause it"
- "Open wikipedia.org/wiki/Golden_Retriever" → "How much do they weigh, according to this page?"

## Safety

Tools are marked safe or risky in code. Risky ones (`close_app`, `power`) always ask **"…Say yes or no."** Only a clear yes runs them; silence or anything ambiguous counts as no. Shutdown/restart also wait 10 seconds and can be cancelled ("cancel shutdown").

**Voice ID:** run `run.bat --enroll-voice` once (six sentences; only a voiceprint is saved, never audio). After that, a risky request or a "yes" in a voice that isn't yours isn't accepted by voice: Max asks for an **Approve tap on your phone** instead (or refuses if the phone isn't connected). Tune `voice_id.threshold` in `config.yaml`.

In the browser, clicks on buttons that buy, pay, send, post, subscribe, delete and similar ask first, and so does pressing Enter anywhere except a search box. Max never types into password fields.

## Tuning (`config.yaml`)

- **False triggers / misses:** `wake_word.sherpa.threshold` (raise to trigger less, lower if it misses you). Check with `--wake-test`.
- **Cuts you off mid-sentence:** raise `audio.silence_s` to 1.5.
- **Doesn't notice you talking:** lower `audio.vad_sensitivity` to 2.0.
- **Speech-to-text:** Whisper small.en, with a vocabulary hint (`stt.prompt`). Transcription starts after 0.4 s of silence (`audio.early_s`) and Max answers right away if the sentence sounds finished; if you trail off ("search for…") it waits up to `audio.max_silence_s`. You can say "Hey Max, open YouTube" in one breath. An optional fast engine (Moonshine, ~0.2 s instead of ~1.8 s) can be enabled with `stt.fast_model_dir`; it's fine for some voices but misheard others a lot, so it's off by default.
- **Quiet voice not waking it:** the wake word is tuned for quiet speech already (`wake_word.sherpa`). What limits it is your voice vs. room noise, which gain can't fix. Turn up the mic in Windows sound settings, get closer to the mic, or turn on your mic's noise suppression ("Voice Clarity" / audio enhancements) in Windows.
- **Different LLMs:** any Ollama model with tool support, e.g. `llama3.2:3b` or `qwen2.5:7b`. Pull it with `ollama pull <name>`.
- **GPU memory:** `llm.keep_alive` sets how long a model stays loaded after use.
- **Add app shortcuts:** add `spoken name: command` under `apps:`. Apps not listed are found automatically via the Start Menu.

## Memory and reminders

- **Remember:** "Hey Max, remember that my exam is on Friday." Facts live in `data/max.db` (SQLite) with local embeddings (bge-small, CPU) so Max finds them by meaning. Relevant facts are attached to your requests automatically ("When is my exam?"). "Forget ..." always asks you to confirm first.
- **Recall past conversations:** "What did I ask you yesterday?" Every turn is logged and searchable.
- **Reminders:** "Remind me about the exam the day before at 7 pm", "in 2 hours", "tomorrow morning". When due, Max says it out loud (once it's idle) and shows a Windows notification. Reminders that came due while Max was off are announced at the next start. "What reminders do I have?" / "Cancel the exam reminder".

## Canvas and the daily digest

- **Canvas (ASU):** put your Canvas calendar feed link (Canvas → Calendar → Calendar Feed) in `secrets.yaml` (never committed). Then ask "What's due today?", "What do I have this week?", "When's my next class?". Read-only; assignments, quizzes and class sessions (with Zoom links). Cross-listed duplicate sessions are filtered out.
- **Day summary:** "What does my day look like?"
- **Daily digest email** every morning (`digest` in `config.yaml`, default 7:00): what's due today and in the next 3 days, today's classes and reminders, with a short focus note from the local model. Sent through Gmail with an [app password](https://myaccount.google.com/apppasswords) in `secrets.yaml`. If the laptop was off, it's sent when Max starts (until 6 PM); never twice a day. "Email me my summary" sends it on demand.

- **Deadline countdown:** phone notifications 24 hours and 3 hours before anything is due, and 10 minutes before each class (tap to join the Zoom link). Each alert is sent once; ones missed while the phone was offline arrive when it reconnects (`alerts` in `config.yaml`).

## Ask your course material

Put slides (`.pptx`), PDFs, Word and text files in `Documents\Max Course Material` (one subfolder per course works best). Max indexes them on this laptop, page by page and slide by slide, and keeps the index up to date every 10 minutes; your saved lecture notes are included. Ask "what did the professor say about RDF schema?", "explain SPARQL OPTIONAL from the lab handout" or "what's on the midterm review sheet?" and Max answers from the matching passages and says which file and page it came from. The files are never changed or moved.

## Read my screen

"What does this error mean?", "summarise what's on my screen": Max captures the window in front (into memory only, never saved), reads its text with Windows' built-in OCR and answers with the local model. Only when you ask.

## Always on

`powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1` makes Max (and the desktop overlay) start at login in the background (no console window) with a tray icon: open dashboard, pause listening, sleep models (free the GPU), quit. It restarts itself if it crashes; `-Uninstall` removes it. Starting Max a second time just opens the dashboard.

## Meeting & lecture notes

"Hey Max, **take notes**" (lecture: listens through the mic) or "take notes on my Zoom call" (meeting: records the laptop's audio plus your mic), or press ⏺ on the overlay / use the dashboard's Notes tab. "**Stop taking notes**" when it's over.

- **Live captions**: words appear ~0.5 s after they're said (a small streaming model, sherpa-onnx "Kroko", ~3% of a CPU core) in the dashboard, the overlay pill and the phone app.
- Whisper transcribes in the background (chunks cut at pauses, with the topic and recent sentences as a vocabulary hint) and its more accurate text **replaces the live captions** every ~30 s. Only Whisper's text is saved, so stopping doesn't mean waiting an hour.
- Notes are written in sections (the local model reads ~4k tokens at a time) and combined into **Summary, Key points, Details, Action items & deadlines, Open questions**.
- A **"Deadlines & dates (exact quotes)"** section is added straight from the transcript, since the model can garble a time.
- **Summary** button (next to Notes / Transcript): a short study summary (TL;DR, key takeaways, to-dos) written on first click and saved as `summary.md`; date quotes are copied verbatim.
- Saved to `Documents\Max Notes\<date> <title>\` as `notes.md` + `transcript.md`; no audio is kept. "What were the key points of today's lecture?" answers from them.
- Recording other people can require their consent (ASU generally requires the instructor's permission to record lectures).

## Desktop overlay

A small always-on-top pill you can drag anywhere on screen (`overlay.bat`, or installed at login by `install_autostart.ps1`):

- **status orb**: standing by, listening, thinking, speaking, needs approval, paused or off
- **🎤 listen now**: push-to-talk, same as saying the wake word
- **⏺ record**: take notes on a lecture or meeting (the pill shows ● REC and the time)
- **💬 chats / ✦ memories / ✓ due today / 📄 notes**: open a panel with recent conversations, saved facts, or today's Canvas items, classes (with Zoom links) and reminders
- **⏻ start / stop**: starts Max when it's off; stopping needs a second click so a stray click can't quit it
- panel footer: pause listening, open the full dashboard, hide the overlay

It's a separate lightweight process (pywebview on Windows' built-in Edge WebView) talking to Max's local API, so it keeps working while Max is stopped.

## Talking with Max

- **Faster answers:** Max starts speaking the first sentence while the rest is still being written (about 0.5-1 s sooner). Turn off with `audio.stream_replies`.
- **Interrupt it:** while Max is talking, say "stop" or "Max, stop" to cut it off, or "Hey Max…" to cut it off and ask something new. Works on laptop speakers: a keyword in what Max itself is saying is ignored (`audio.barge_in`).
- **Only the tools a request needs:** with ~50 tools the definitions alone outgrew the model's 4,096-token window, so each request now gets a small core plus the tool families that match it (by meaning and keywords) and any used in the last two turns (`toolselect.py`).

## Dashboard

While Max runs, open **http://127.0.0.1:8765** (this laptop only). A mission-control view of the assistant:

- a live **orb** that breathes, listens, thinks and speaks with Max, plus status (LLM on GPU, VRAM, search, browser, memory)
- the **conversation**, with the tools used for each answer; you can **type commands** too (replies stay silent)
- **approvals**: risky actions show Approve / Deny, racing the spoken yes/no
- **live activity**: tool calls, results, reminders, approvals as they happen
- **memory & reminders** drawer: search facts by meaning, add or forget them, set and cancel reminders
- **accuracy**: results of the command accuracy test (below), per category and over time
- **privacy**: everything Max keeps on this laptop, where it lives and how much; export it all as one zip or delete any category (laptop only, two clicks)

The same REST + WebSocket API (`/api/docs`) is what the phone app uses (and the watch will). Built with React + Vite + TypeScript (`dashboard/`); `setup.ps1` builds it if Node.js is installed. For UI work: `cd dashboard && npm run dev` (proxies to a running Max).

## Phone app (Android)

A native Kotlin + Jetpack Compose app (`android/`) that talks to Max on the laptop from anywhere:

- **push-to-talk**: tap the mic, speak, and the reply plays in Max's own Piper voice (transcription and the LLM still run on the laptop)
- **chat, today, memory, notes**: the conversation, what's due on Canvas, reminders (add/cancel), facts (add/forget), and meeting/lecture notes (start/stop recording on the laptop, read the notes)
- **always connected**: a foreground service keeps one WebSocket open, so reminders, "notes ready" and **approvals** arrive as notifications. Risky actions started from the phone wait for your **Approve / Deny** tap (90 s, silence = no) and never use the laptop mic
- **laptop controls**: listen now, pause / resume the mic
- **Max acts on the phone** (from the phone or by voice on the laptop): open any app, call a contact (asks first), text or WhatsApp a contact (opens ready to send; you tap Send), alarms and timers, Google Maps directions, Spotify / YouTube Music, emails (open filled in), "what did I miss?" from recent notifications, battery status. Each permission is off until you grant it in Settings → Phone powers; notifications are kept in memory only. With "Display over other apps" on, requests from the laptop happen right away even with the phone in your pocket; otherwise they arrive as a tap-to-open notification
- **quick access without opening the app**: a listening sheet slides up over whatever you're doing and starts listening at once. Open it from the side key (set Max as the phone's digital assistant), a Quick Settings tile, a home-screen widget, or long-press the app icon → "Talk to Max". It listens again if Max asks a question and closes itself after the reply; Settings → Quick access sets each one up in a tap

- **works with the laptop off** (see below)

### Without the laptop

The phone keeps its own copy of reminders and Max's calendar, and can record lectures on its own. Everything syncs with the laptop as soon as it can be reached (link comes up, app opens, something changes on either side).

- **Reminders** fire on the phone itself (exact alarms, re-armed after a reboot), so they come on time with the laptop off. Add them on the Today page: "tomorrow 5pm", "fri 9am", "in 2 hours", "oct 12" (a day alone = 9 AM) or the 📅 picker. Reminders set by voice on the laptop are copied to the phone too, and you get each one once: whichever of the phone's alarm and the laptop's event comes first. If the laptop was off when one came due, it doesn't announce it later ("While you were away") because the phone already did.
- **Calendar**: "add a study group to my calendar tomorrow at 3pm" (voice) or the Today page (phone) adds to **Max's calendar**. The phone shows it in its calendar app as a local calendar called "Max" (Settings → Calendar), offline too, with an alert 15 minutes before; editing or deleting an event there counts as well. "What's on tomorrow?", "remove the dentist from my calendar". No time of day = all day.
- **Lectures and meetings**: Notes → **Record on this phone** records in the background (screen off is fine; AAC, ~14 MB an hour; the phone's own mic only, since Android won't let apps record other apps' calls). When the laptop is back, the recording uploads itself and the laptop writes the notes with the usual pipeline (Whisper, section notes, write-up, exact deadline quotes), dated when you recorded it. "Notes ready" arrives on the phone; then both copies of the audio are deleted. A failed one keeps its audio (Retry).
- **Today page** shows the Canvas due dates and classes from the last sync while the laptop is away ("laptop offline · synced 2 h ago").
- How the two sides agree (`sync.py`, `OfflineLogic.kt`): every reminder/event has an id; a finished one (done / cancelled) beats an open one, otherwise the later edit wins.

How it connects: the API stays on `127.0.0.1`. [Tailscale](https://tailscale.com) gives the phone a private, encrypted route to the laptop, and `tailscale serve --bg 8765` publishes the API at `https://<laptop>.<tailnet>.ts.net` inside your tailnet only. Requests from the laptop itself are trusted; anything else needs the phone token (created in `data/phone_token.txt`, never committed).

Pairing: install Tailscale on both devices (same account) → run `tailscale serve --bg 8765` once → dashboard **Phone** tab shows a QR code → app **Settings → Pair (scan QR)**. On Samsung, allow the app to run unrestricted (Settings has a button) so notifications aren't delayed.

Building: `android\build.ps1` builds `app-debug.apk` with the JDK/SDK in `C:\max-android` (no Android Studio needed) and installs it over USB if the phone is connected with USB debugging on.

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

## Command accuracy test

`.\.venv\Scripts\python -m max_assistant.evals` sends about 90 realistic commands (`evals/commands.yaml`) to the real model with every tool stubbed, so nothing happens on the laptop or phone, and scores whether Max picked the right action and the right details, plus speed. Results go to `data/evals/` and the dashboard's Accuracy tab. Latest: **99% right action, 99% with the right details, median 1.4 s**, up from 90% / 84% on the first run (the misses led to fixes in `agent.py`).

## Troubleshooting

- **"Can't reach Ollama"** — open the Ollama app, or run `ollama serve`.
- **"Model isn't downloaded"** — run the `ollama pull` command it prints.
- **No wake detection** — run `--list-devices`, set `audio.input_device` to your mic's number; check Windows Settings → Privacy → Microphone allows desktop apps.
- **Stops responding when its window isn't in front / after clicking in its window** — fixed: a program printing to a console freezes when the console stops reading (clicking into a Command Prompt starts "Select" mode; editor terminals like VS Code's pause when inactive). Max now writes console output from a background thread and turns off click-to-select. Every 30 s it logs a heartbeat to `logs/max.log` (stage, mic frames, level, front window); if it's stuck, thread stacks go to `logs/stacks.log`.
- **Robotic voice** — Piper voice file is missing, so it fell back to the Windows voice; re-run `setup.ps1`.
- **Slow first answer** — the model is loading into VRAM; later answers are faster. Close GPU-heavy apps (games, NVIDIA overlay) to help.
- Logs are in `logs/max.log`.

## Avatar

Max as a 3D character in the bottom-right corner, above the clock, always on top. It starts with Max (`avatar.enabled` in `config.yaml`); the tray has **Show / hide avatar**.

- **Follows Max:** idle (breathing, weight shifts, eyes that make small quick jumps like real ones, glances, the odd sigh, look around or passing smile), looks at you with brows up when you say "Hey Max", nods in your pauses while you talk, eyes moving about with brows drawn and lips pressed while transcribing and thinking, eyes half-closed when paused, dozes off when the models sleep ("go to sleep") or Max is offline.
- **Talking like a person:** on stressed words (sharp rises in the voice's loudness) a small nod and brow raise; for longer sentences one or both hands come up in front and beat on those words; sometimes he looks away as he starts a sentence and back to you to finish it; blinks in pauses. The sentence text comes with the speech event: a question raises his brows at the end, "sorry" / "couldn't" softens them, good news smiles.
- **Face from parts** (`face.ts`): VRoid's preset expressions (Joy, Fun, Surprised) open the mouth into a grin at any strength, so the face is mixed from the model's separate brow, eye and mouth-corner morphs instead (closed-lip smile, raised brows without a gaping mouth). VRoid stores morph names where three.js doesn't look; they're read from the file.
- **Lip sync:** just before each sentence plays, Max sends its loudness curve and mouth shapes (aa / ih / ou / ee / oh, 60 a second) over the dashboard WebSocket. The shapes come from two band-energy ratios of the voice (how open, how front the vowel sounds) measured against the sentence's own average, so no phoneme model is needed (~3 ms a sentence). `avatar.lip_sync_delay_ms` if the mouth is early or late.
- **Gestures** (drawn in code, `gesture.ts`): waves at a greeting or the first "Hey Max" in 20 minutes, tilts his head while Max waits for a yes / no then nods or shakes, hand to chin while the big model thinks, shrugs when an answer admits a failure or a tool failed, a small happy hop when a task is done.
- **Mouse:** his eyes (and a little of his head) follow the pointer when it's near. Click him = talk to Max, double click = open the Max app, drag = move him; the spot is remembered (tray: **Reset avatar position**). The window is transparent and clipped to his outline, so clicks around him reach whatever is behind. The outline is drawn 10 times a second into a tiny off-screen picture and read back without waiting for the GPU (reading the real canvas stalled a frame each time: ~2.5 dropped frames a second); while his arms move fast the whole window is kept so nothing is clipped.
- **Hides for fullscreen games** (exclusive fullscreen, or borderless from Steam / Riot / Epic... folders, or `avatar.games`), not for maximised Chrome or videos.
- **Character:** the "Max edition" of VRoid's CC0 HairSample_Male (navy hoodie, cyan trim, glowing orb, cyan eyes), rebuilt with `python -m max_assistant.avatar.edition`. Any VRM works (`avatar.model`).
- **Cost:** renders on the Intel iGPU (0 MB of the 3050's VRAM) at every screen refresh (180 fps here, ~2 cores' worth of CPU across its processes; `avatar.fps: 90` halves that), 30 fps while asleep. While Whisper transcribes he drops to 60 fps and is pinned to the CPU's efficiency cores (Whisper uses 6 threads on the others); the rest of the time he may use any core, because on the E-cores alone he dropped a frame or two a second. Measured: Whisper 2.09 s with the avatar vs 2.19 s without.
- Code: `dashboard/avatar.html` + `src/avatar/` (three.js + @pixiv/three-vrm; `behaviour.ts` = what it does per state, `face.ts` = expressions from face parts, `gesture.ts` = gestures and talking hands, `link.ts` = connection and event -> gesture rules), `max_assistant/avatar/` (window, pinning, fullscreen check, `bench.py`).

**Phase A results** (VRoid AvatarSample_C, ~27k triangles, 300×460 window, `python -m max_assistant.avatar [--fps 60] [--tex 1024] [--debug]`, measured with `python -m max_assistant.avatar.bench`)

| | avatar off | avatar on, 60 fps, iGPU |
|---|---|---|
| Renders on | – | Intel UHD (forced via `--force_low_power_gpu`) |
| NVIDIA memory used by the avatar | – | 0 MB (left to choose itself, it takes 123 MB of the 3050) |
| iGPU load / shared memory | – | 13% / ~220 MB |
| Avatar CPU / RAM | – | ~7% of the machine / ~900 MB |
| `qwen3:4b` speed (still 100% in VRAM) | 49 tok/s | 47 tok/s |
| Whisper, 9 s clip (default threads) | 2.07 s | 2.74 s (+32%) |
| Whisper with the fix below | 2.07 s | 2.14 s (+3%, noise level) |

How much the avatar draws barely matters (even at 1 fps Whisper was 25% slower); what matters is CPU cores. The fix: the avatar process is pinned to the i5-12450H's efficiency cores and Whisper uses 6 threads. Texture shrinking made no measurable difference.

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
  memory.py      SQLite + embeddings: facts, conversation log, action log
  reminders.py   natural-language times, scheduler, Windows notifications
  events.py      event bus + approval broker (voice or dashboard)
  server.py      FastAPI: REST + WebSocket, serves the dashboard
  remote.py      phone access: token, pairing QR, audio in/out
  phone.py       Max acting on the phone (request/answer link)
  sync.py        phone <-> laptop sync of reminders and events (agenda.py = Max's calendar)
  phone_notes.py lectures recorded on the phone: upload queue -> notes
  course.py      course material index and search
  speech.py      speaking while thinking; interrupting
  toolselect.py  only the tools a request needs
  voiceid.py     voice ID (speaker embeddings)
  alerts.py      deadline countdown
  privacy.py     privacy page: list, export, delete
  evals.py       command accuracy test (evals/commands.yaml)
  live.py        live captions while taking notes (streaming model)
  browser.py     Max's Chrome window (Playwright), element finding
  tools/
    registry.py  @tool decorator, schemas, risky flags
    system.py    laptop tools
    web.py       web_search
    browser.py   click / type / navigate / read / tabs
    memory.py    remember / recall / forget, reminders
dashboard/      React + Vite + TypeScript mission-control UI
android/        Kotlin + Jetpack Compose phone app
tests/           pytest suite (runs without a mic, GPU or Ollama)
```

### Adding a tool

```python
@reg.tool("Describe it for the LLM.", params={"city": {"type": "string"}}, risky=False)
def my_tool(city: str):
    return f"Result for {city}"
```

Run tests with `.\.venv\Scripts\python -m pip install pytest; .\.venv\Scripts\python -m pytest`.
