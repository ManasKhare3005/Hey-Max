"""Change Max from the phone: a request typed in the phone app's Settings is handed to Claude
Code on this laptop, which implements it on its own git branch. Max then runs the tests, builds
the dashboard / phone app if they changed, and reports back. Nothing reaches main until the
change is approved on the phone; nothing is ever pushed to GitHub.

    request -> worktree on branch phone/<id> (data/dev-worktree, so the running Max and the
               main checkout are untouched) -> `claude -p` (edits allowed, shell limited to
               tests/builds/read-only git) -> commit -> pytest -> npm build / gradle build
            -> "ready" (phone shows summary, tests, Approve / Reject)
    approve -> merge into main -> rebuild the dashboard -> publish the APK for the phone's
               Update button -> restart Max in background mode
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .config import ROOT

log = logging.getLogger(__name__)
DATA = ROOT / "data"
WORKTREE = DATA / "dev-worktree"
JOBS_FILE = DATA / "dev-jobs.json"
APK_DIR = DATA / "app"
APK = APK_DIR / "max.apk"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PROMPT = """Manas sent this change request for Max from his phone:

\"\"\"{request}\"\"\"

Implement it completely in this repository. Read CLAUDE.md first and follow its conventions.
You are in a git worktree on the branch {branch}; work only here. The Python tests run with:
  .venv/Scripts/python -m pytest -q
Add or update tests for what you change and make sure they pass. If you change the dashboard,
check it builds with `npm run build` in dashboard/. Don't commit, push, touch secrets.yaml, or
edit files outside this folder. If the request is unclear or unsafe, change nothing and explain.

Finish with a short summary for a phone screen (2-5 plain sentences, no markdown): what you
changed and anything Manas must do (e.g. say a new phrase, install the app update)."""

ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep", "TodoWrite",
    "Bash(git status*)", "Bash(git diff*)", "Bash(git log*)", "Bash(git show*)",
    "Bash(ls*)", "Bash(cat*)", "Bash(head*)", "Bash(tail*)", "Bash(grep*)", "Bash(find*)", "Bash(wc*)",
    "Bash(npm run build*)", "Bash(cd dashboard && npm run build*)", "Bash(npx tsc*)",
    "Bash(.venv/Scripts/python -m pytest*)", "Bash(.venv/Scripts/python.exe -m pytest*)",
]
DENIED_TOOLS = ["Bash(git push*)", "Bash(git commit*)", "Bash(git reset*)", "Bash(git checkout*)", "Bash(rm *)",
                "Bash(curl*)", "Bash(powershell*)", "WebFetch", "WebSearch"]


@dataclass
class Job:
    id: int
    request: str
    status: str = "queued"     # queued, working, testing, building, ready, failed, merged, rejected
    created: float = field(default_factory=time.time)
    branch: str = ""
    progress: list[str] = field(default_factory=list)     # recent steps, e.g. "Editing agent.py"
    summary: str = ""
    files: list[str] = field(default_factory=list)
    tests: str = ""            # "129 passed" / failure tail
    tests_ok: bool = False
    apk: bool = False          # a new phone app was built for this change
    error: str = ""


SHARED = (".venv", "dashboard/node_modules", "models")      # junctions into the main checkout


def remove_worktree():
    """Delete the work copy. The shared folders are junctions: remove the links first (rmdir on a
    junction removes only the link), so nothing can ever recurse into the real .venv or models."""
    for rel in SHARED:
        link = WORKTREE / rel
        if link.exists() or os.path.lexists(link):
            if is_junction(link):
                os.rmdir(link)
            elif link.exists():
                raise RuntimeError(f"{link} isn't a link; not deleting the work copy automatically")
    if WORKTREE.exists():
        run(["git", "worktree", "remove", "--force", str(WORKTREE)], ROOT)
        shutil.rmtree(WORKTREE, ignore_errors=True)
    run(["git", "worktree", "prune"], ROOT)


def is_junction(path: Path) -> bool:
    if hasattr(os.path, "isjunction") and os.path.isjunction(path):
        return True
    return os.path.islink(path)


def find_claude() -> str:
    """The Claude Code executable. npm installs a .cmd shim; call the real claude.exe behind it,
    because cmd.exe would mangle arguments."""
    found = shutil.which("claude") or "claude"
    exe = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(exe) if found.lower().endswith((".cmd", ".ps1")) or not Path(found).suffix and exe.exists() else found


def run(cmd: list[str], cwd: Path, timeout: float = 900, env: dict | None = None) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, creationflags=NO_WINDOW, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def git(*args: str, cwd: Path = ROOT) -> str:
    code, out = run(["git", *args], cwd, timeout=120)
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.strip()[-300:]}")
    return out.strip()


def describe_tool(name: str, args: dict) -> str:
    """One line for the phone: what Claude is doing right now."""
    path = Path(str(args.get("file_path") or args.get("path") or "")).name
    if name in ("Edit", "Write", "MultiEdit"):
        return f"Editing {path}"
    if name == "Read":
        return f"Reading {path}"
    if name in ("Grep", "Glob"):
        return f"Searching for {str(args.get('pattern', ''))[:40]}"
    if name == "Bash":
        cmd = str(args.get("command", ""))
        return "Running the tests" if "pytest" in cmd else "Building the dashboard" if "npm" in cmd else f"Running {cmd[:50]}"
    return name


class DevLoop:
    def __init__(self, on_event: Callable[[str, dict], None] | None = None, claude: str | None = None,
                 model: str = "", timeout_min: float = 40, restart: Callable[[], None] | None = None):
        self.on_event = on_event or (lambda kind, data: None)
        self.claude = claude or find_claude()
        self.model = model
        self.timeout = timeout_min * 60
        self.restart = restart or (lambda: None)        # restart Max after a merge (set by main)
        self.jobs: list[Job] = self._load()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        for j in self.jobs:                              # Max restarted mid-job
            if j.status in ("queued", "working", "testing", "building"):
                j.status, j.error = "failed", "Max restarted while this was running."
        self._save()

    # ----- persistence -----
    def _load(self) -> list[Job]:
        try:
            return [Job(**j) for j in json.loads(JOBS_FILE.read_text(encoding="utf-8"))]
        except Exception:
            return []

    def _save(self):
        DATA.mkdir(exist_ok=True)
        JOBS_FILE.write_text(json.dumps([asdict(j) for j in self.jobs[-30:]], indent=1), encoding="utf-8")

    def _update(self, job: Job, **changes):
        with self._lock:
            for k, v in changes.items():
                setattr(job, k, v)
            self._save()
        self.on_event("devjob", {"id": job.id, "status": job.status, "step": (job.progress or [""])[-1],
                                 "request": job.request[:80]})

    def list(self) -> list[dict]:
        with self._lock:
            return [asdict(j) for j in reversed(self.jobs[-15:])]

    def get(self, job_id: int) -> Job:
        for j in self.jobs:
            if j.id == job_id:
                return j
        raise KeyError(job_id)

    @property
    def busy(self) -> Job | None:
        return next((j for j in self.jobs if j.status in ("queued", "working", "testing", "building")), None)

    # ----- a new request -----
    def submit(self, request: str) -> Job:
        request = request.strip()
        if len(request) < 5:
            raise ValueError("Describe the change in a few words.")
        with self._lock:
            if self.busy is not None:
                raise RuntimeError("Claude is still working on the last request.")
            if any(j.status == "ready" for j in self.jobs):
                raise RuntimeError("Approve or reject the finished change first.")
            job = Job(id=max((j.id for j in self.jobs), default=0) + 1, request=request)
            job.branch = f"phone/{job.id}-{time.strftime('%Y%m%d-%H%M')}"
            self.jobs.append(job)
            self._save()
        threading.Thread(target=self._work, args=(job,), name="dev-job", daemon=True).start()
        return job

    def _step(self, job: Job, text: str):
        self._update(job, progress=(job.progress + [text])[-8:])

    def _work(self, job: Job):
        try:
            self._update(job, status="working")
            self._prepare(job)
            self._run_claude(job)
            changed = self._commit(job)
            if not changed:
                self._update(job, status="failed", error="Claude didn't change anything. " + job.summary[:200])
                self._cleanup(job, delete_branch=True)
                return
            self._update(job, status="testing", files=changed)
            self._step(job, "Running all tests")
            code, out = run([str(VENV_PY), "-m", "pytest", "-q"], WORKTREE, timeout=900)
            tail = [l for l in out.strip().splitlines() if l.strip()]
            self._update(job, tests_ok=code == 0, tests=(tail[-1] if code == 0 else "\n".join(tail[-12:]))[:1500])
            self._update(job, status="building")
            if any(f.startswith("dashboard/") for f in changed):
                self._step(job, "Building the dashboard")
                code, out = run(["cmd", "/c", "npm", "run", "build"], WORKTREE / "dashboard", timeout=600)
                if code != 0:
                    raise RuntimeError("dashboard build failed: " + out.strip()[-400:])
            if any(f.startswith("android/") for f in changed):
                self._step(job, "Building the phone app (a few minutes)")
                self._build_apk(job)
            self._update(job, status="ready")
            self._step(job, "Ready for your OK")
        except Exception as exc:
            if job.status != "rejected":                 # rejected from the phone while running
                log.exception("dev job %s failed", job.id)
                self._update(job, status="failed", error=str(exc)[:600])
            self._cleanup(job, delete_branch=True)
        finally:
            self._proc = None

    def _prepare(self, job: Job):
        self._step(job, "Setting up a separate copy of the code")
        remove_worktree()
        git("worktree", "add", "-b", job.branch, str(WORKTREE), "main")
        # Big, gitignored folders are shared with the main checkout instead of copied
        for rel in SHARED:
            src, dst = ROOT / rel, WORKTREE / rel
            if src.exists() and not dst.exists():
                run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], ROOT)
        # Gitignored but needed: the project notes for Claude, settings to run tests and builds
        for rel in ("CLAUDE.md", "secrets.yaml", "android/local.properties"):
            if (ROOT / rel).exists():
                shutil.copy2(ROOT / rel, WORKTREE / rel)

    def _run_claude(self, job: Job):
        self._step(job, "Claude is reading the code")
        prompt = PROMPT.format(request=job.request, branch=job.branch)
        cmd = [self.claude, "-p", "--output-format", "stream-json", "--verbose",
               "--permission-mode", "acceptEdits", "--allowedTools", *ALLOWED_TOOLS,
               "--disallowedTools", *DENIED_TOOLS]
        if self.model:
            cmd += ["--model", self.model]
        env = {**os.environ, "MAX_DEV_JOB": str(job.id)}
        self._proc = subprocess.Popen(cmd, cwd=str(WORKTREE), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      stdin=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                      creationflags=NO_WINDOW, env=env)
        self._proc.stdin.write(prompt)                   # via stdin: no quoting trouble with the request
        self._proc.stdin.close()
        killer = threading.Timer(self.timeout, self._proc.kill)
        killer.start()
        result, other = "", []
        try:
            for line in self._proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    other.append(line.strip())
                    continue
                if msg.get("type") == "assistant":
                    for part in (msg.get("message") or {}).get("content") or []:
                        if part.get("type") == "tool_use":
                            self._step(job, describe_tool(part.get("name", ""), part.get("input") or {}))
                elif msg.get("type") == "result":
                    result = str(msg.get("result") or "")
                    if msg.get("is_error"):
                        raise RuntimeError("Claude stopped: " + result[:400])
            self._proc.wait()
        finally:
            killer.cancel()
        if self._proc.returncode not in (0, None) and not result:
            raise RuntimeError("Claude Code failed: " + " ".join(other)[-400:] if other else
                               f"Claude Code exited with code {self._proc.returncode} (timed out?)")
        self._update(job, summary=result.strip()[:2000])

    def _commit(self, job: Job) -> list[str]:
        status = git("status", "--porcelain", cwd=WORKTREE)      # links, CLAUDE.md, secrets: gitignored
        if not status:
            return []
        git("add", "-A", cwd=WORKTREE)
        files = git("diff", "--cached", "--name-only", cwd=WORKTREE).splitlines()
        git("-c", "user.name=Max (Claude, from phone)", "-c", "user.email=max@localhost", "commit", "-q",
            "-m", f"Phone request: {job.request[:60]}", "-m", f"{job.request}\n\n{job.summary}", cwd=WORKTREE)
        return files

    def _build_apk(self, job: Job):
        tools = Path("C:/max-android")
        env = {**os.environ, "JAVA_HOME": str(tools / "jdk17"), "ANDROID_HOME": str(tools / "sdk")}
        code, out = run(["cmd", "/c", "gradlew.bat", "assembleDebug", "--console=plain"], WORKTREE / "android",
                        timeout=1500, env=env)
        if code != 0:
            raise RuntimeError("phone app build failed: " + out.strip()[-500:])
        built = WORKTREE / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        APK_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, APK_DIR / f"pending-{job.id}.apk")
        self._update(job, apk=True)

    def _cleanup(self, job: Job, delete_branch: bool):
        remove_worktree()
        if delete_branch and job.branch:
            run(["git", "branch", "-D", job.branch], ROOT)

    # ----- the decision (from the phone) -----
    def approve(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job.status != "ready":
            raise RuntimeError(f"That change is {job.status}, not waiting for approval.")
        dirty = [l[3:] for l in git("status", "--porcelain").splitlines() if not l.startswith("??")]
        overlap = sorted(set(dirty) & set(job.files))
        if overlap:
            raise RuntimeError("These files have unsaved edits on the laptop: " + ", ".join(overlap[:5]))
        self._cleanup(job, delete_branch=False)
        try:
            git("merge", "--no-ff", "-q", "-m", f"Merge phone request {job.id}: {job.request[:50]}", job.branch)
        except RuntimeError:
            run(["git", "merge", "--abort"], ROOT)
            raise
        git("branch", "-d", job.branch)
        if any(f.startswith("dashboard/") for f in job.files):
            run(["cmd", "/c", "npm", "run", "build"], ROOT / "dashboard", timeout=600)
        pending = APK_DIR / f"pending-{job.id}.apk"
        if pending.exists():
            pending.replace(APK)
        self._update(job, status="merged")
        if any(f.startswith("max_assistant/") or f == "config.yaml" for f in job.files):
            threading.Timer(2.0, self.restart).start()     # let the API answer first
        return job

    def reject(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job.status in ("working", "testing", "building", "queued"):
            if self._proc is not None:
                self._proc.kill()                           # _work notices and cleans up
            self._update(job, status="rejected")
            return job
        if job.status != "ready":
            raise RuntimeError(f"That change is already {job.status}.")
        self._cleanup(job, delete_branch=True)
        (APK_DIR / f"pending-{job.id}.apk").unlink(missing_ok=True)
        self._update(job, status="rejected")
        return job


def apk_info() -> dict:
    """The phone app the Update button installs (built time in ms, like the app's BuildConfig)."""
    if not APK.exists():
        return {"available": False}
    st = APK.stat()
    return {"available": True, "built": int(st.st_mtime * 1000), "size": st.st_size}


def publish_apk(path: Path) -> dict:
    """Make a freshly built APK the one the phone's Update button offers."""
    APK_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, APK)
    return apk_info()


def restart_max_detached():
    """Start a new background Max once this one has exited (the single-instance lock frees then)."""
    exe = Path(sys.executable).with_name("pythonw.exe")
    exe = exe if exe.exists() else Path(sys.executable)
    code = (f"import time, subprocess, psutil\n"
            f"try:\n    psutil.Process({os.getpid()}).wait(120)\nexcept Exception:\n    pass\n"
            f"time.sleep(2)\nsubprocess.Popen([r'{exe}', '-m', 'max_assistant', '--tray'], cwd=r'{ROOT}')\n")
    subprocess.Popen([str(exe), "-c", code], cwd=str(ROOT), creationflags=0x00000008 | 0x00000200,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


