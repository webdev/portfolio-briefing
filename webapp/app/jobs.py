"""Single-flight background-job manager for the refresh endpoint.

The only state-mutating endpoint in the web app — POST /refresh — shells
out to the pipeline (``run_briefing.py``). This module enforces:

  - **Single-flight** — only one refresh can be in flight at a time.
    Concurrent calls return 409 with the existing ``job_id``.
  - **Thread-backed subprocess** — uses ``subprocess.Popen`` and a
    daemon thread that drains stdout into a queue. We deliberately do
    NOT use ``asyncio.create_task`` here because TestClient closes the
    request's event loop as soon as the handler returns, which would
    cancel any task running on it. Thread-based work survives that.
  - **Per-job log queue** — every stdout/stderr line is pushed onto a
    ``queue.Queue`` that the SSE handler drains via a small async wrapper.
  - **ingest_complete event** — the SSE stream emits a final event so the
    dashboard's JS can auto-reload.

Test seam: the subprocess command + cwd are configured via
``set_command_builder()`` so pytest can swap in a fake command
(``printf``, ``false``) without actually running the briefing.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import queue
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional


_SENTINEL = "__END_OF_STREAM__"


@dataclass
class JobState:
    """One refresh job's lifecycle."""

    id: str
    started_at: datetime
    finished_at: datetime | None = None
    return_code: int | None = None
    status: str = "running"   # 'running' | 'success' | 'failed'
    lines: list[str] = field(default_factory=list)  # buffered log lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "return_code": self.return_code,
            "status": self.status,
            "line_count": len(self.lines),
        }


# Module-level singleton — one running job at a time.
_JOBS: dict[str, JobState] = {}
_QUEUES: dict[str, queue.Queue[str]] = {}
_THREADS: dict[str, threading.Thread] = {}
_RUNNING_JOB_ID: Optional[str] = None
_JOB_LOCK = threading.Lock()


def _default_command_builder() -> tuple[list[str], Path | None]:
    """Production briefing command + cwd.

    The pipeline reads `config/briefing.yaml` relative to its own working
    directory, so cwd MUST be `skills/daily-portfolio-briefing/` — not
    the repo root (2026-06-30 user report: refresh failed with
    "Config file not found: config/briefing.yaml" because cwd was the
    repo root).
    """
    repo_root = Path(__file__).resolve().parents[2]
    skill_dir = repo_root / "skills" / "daily-portfolio-briefing"
    script = skill_dir / "scripts" / "run_briefing.py"
    cmd = [
        "uv", "run", "python",
        str(script),
        "--etrade-live",
        "--force",
        "--refresh-scout",
    ]
    return cmd, skill_dir


_COMMAND_BUILDER: Callable[[], tuple[list[str], Path | None]] = _default_command_builder


def set_command_builder(builder: Callable[[], tuple[list[str], Path | None]]) -> None:
    """Override the subprocess command — test seam."""
    global _COMMAND_BUILDER
    _COMMAND_BUILDER = builder


def reset_command_builder() -> None:
    """Restore the production command builder."""
    global _COMMAND_BUILDER
    _COMMAND_BUILDER = _default_command_builder


def get_running_job() -> JobState | None:
    if _RUNNING_JOB_ID is None:
        return None
    return _JOBS.get(_RUNNING_JOB_ID)


def get_job(job_id: str) -> JobState | None:
    return _JOBS.get(job_id)


def start_refresh_job(
    on_complete: Optional[Callable[[JobState], None]] = None,
) -> tuple[JobState, bool]:
    """Start a refresh job. Returns ``(state, started_new)``.

    If a job is already running, returns ``(running_job, False)``.
    """
    global _RUNNING_JOB_ID

    with _JOB_LOCK:
        if _RUNNING_JOB_ID is not None and _RUNNING_JOB_ID in _JOBS:
            existing = _JOBS[_RUNNING_JOB_ID]
            if existing.status == "running":
                return existing, False

        job_id = f"job_{uuid.uuid4().hex[:12]}"
        state = JobState(id=job_id, started_at=datetime.now())
        _JOBS[job_id] = state
        _QUEUES[job_id] = queue.Queue(maxsize=10_000)
        _RUNNING_JOB_ID = job_id

    t = threading.Thread(
        target=_run_job_thread,
        args=(state, on_complete),
        name=f"refresh-{job_id}",
        daemon=True,
    )
    _THREADS[job_id] = t
    t.start()
    return state, True


def _run_job_thread(
    state: JobState,
    on_complete: Optional[Callable[[JobState], None]],
) -> None:
    """Run the subprocess synchronously in this thread.

    Drains stdout (stderr merged) line-by-line into the per-job queue
    so SSE clients can drain in real time. Updates state.status / .return_code
    / .finished_at and invokes ``on_complete`` after the process exits.
    """
    cmd, cwd = _COMMAND_BUILDER()
    q = _QUEUES[state.id]

    _push_line(q, state, f"$ {shlex.join(cmd)}")
    _push_line(q, state, f"# cwd: {cwd or os.getcwd()}")
    _push_line(q, state, f"# started at {state.started_at.isoformat()}")
    _push_line(q, state, "")

    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(cwd) if cwd else None,
            env=os.environ.copy(),
            bufsize=1,
            text=True,
            universal_newlines=True,
        )
    except FileNotFoundError as e:
        _push_line(q, state, f"!! Failed to launch subprocess: {e}")
        _finish(state, q, return_code=-1, status="failed", on_complete=on_complete)
        return
    except Exception as e:  # pragma: no cover  # noqa: BLE001
        _push_line(q, state, f"!! Unexpected subprocess error: {e!r}")
        _finish(state, q, return_code=-1, status="failed", on_complete=on_complete)
        return

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _push_line(q, state, line.rstrip("\n"))
    except Exception as e:  # pragma: no cover  # noqa: BLE001
        _push_line(q, state, f"!! stdout read error: {e!r}")

    rc = proc.wait()
    status = "success" if rc == 0 else "failed"
    _push_line(q, state, "")
    _push_line(q, state, f"# finished at {datetime.now().isoformat()} rc={rc}")
    _finish(state, q, return_code=rc, status=status, on_complete=on_complete)


def _finish(
    state: JobState,
    q: queue.Queue,
    *,
    return_code: int,
    status: str,
    on_complete: Optional[Callable[[JobState], None]],
) -> None:
    """Mark a job finished + emit the SSE-stream sentinel."""
    global _RUNNING_JOB_ID

    state.return_code = return_code
    state.status = status
    state.finished_at = datetime.now()

    if on_complete is not None:
        try:
            on_complete(state)
        except Exception as e:  # pragma: no cover  # noqa: BLE001
            _push_line(q, state, f"!! post-completion hook failed: {e!r}")

    try:
        q.put(_SENTINEL, block=False)
    except queue.Full:
        pass

    with _JOB_LOCK:
        if _RUNNING_JOB_ID == state.id:
            _RUNNING_JOB_ID = None


def _push_line(q: queue.Queue, state: JobState, line: str) -> None:
    """Buffer line + push onto queue. Caps buffer at 5_000 lines."""
    state.lines.append(line)
    if len(state.lines) > 5_000:
        del state.lines[: len(state.lines) - 5_000]
    try:
        q.put(line, block=False)
    except queue.Full:
        pass


# ─── SSE stream ─────────────────────────────────────────────────────


async def sse_stream(job_id: str) -> AsyncIterator[bytes]:
    """Yield SSE frames for one job's logs.

    Replays buffered lines first (so late-joining clients see the full
    history), then drains the queue until the END_OF_STREAM sentinel.
    Emits an ``ingest_complete`` event with the job's final status.

    Reads from ``queue.Queue`` (thread-safe) inside ``asyncio.to_thread()``
    so the asyncio event loop stays responsive.
    """
    state = _JOBS.get(job_id)
    if state is None:
        yield _frame("error", _json.dumps({"error": "job not found"}))
        return

    # ── Case 1: job already finished — replay the buffer, emit
    # ingest_complete, close. No queue draining needed.
    if state.status != "running":
        for line in list(state.lines):
            yield _frame("log", line)
        yield _frame("caught_up", str(len(state.lines)))
        yield _frame("ingest_complete", _json.dumps({
            "status": state.status,
            "return_code": state.return_code,
            "job_id": job_id,
        }))
        return

    # ── Case 2: job is running. Replay the buffer to bring the SSE
    # client up to date, then drain the queue. Track buffered count
    # so we know how many queue items to skip (they're dupes of the
    # buffered lines we just sent — _push_line puts to both).
    buffered = list(state.lines)
    for line in buffered:
        yield _frame("log", line)
    yield _frame("caught_up", str(len(buffered)))
    skip_n = len(buffered)

    q = _QUEUES.get(job_id)
    if q is None:
        # Queue gone — job must have finished between checks
        yield _frame("ingest_complete", _json.dumps({
            "status": state.status,
            "return_code": state.return_code,
            "job_id": job_id,
        }))
        return

    while True:
        try:
            line = await asyncio.to_thread(_blocking_get, q, 30.0)
        except _QueueTimeout:
            yield b": keepalive\n\n"
            continue
        if line is _SENTINEL:
            yield _frame("ingest_complete", _json.dumps({
                "status": state.status,
                "return_code": state.return_code,
                "job_id": job_id,
            }))
            break
        if skip_n > 0:
            skip_n -= 1
            continue
        yield _frame("log", line)


class _QueueTimeout(Exception):
    pass


def _blocking_get(q: queue.Queue, timeout: float):
    try:
        return q.get(timeout=timeout)
    except queue.Empty as e:
        raise _QueueTimeout from e


def _frame(event: str, data: str) -> bytes:
    """Format one SSE event. ``data`` may be multiline."""
    data_lines = data.split("\n")
    body = "\n".join(f"data: {line}" for line in data_lines)
    return f"event: {event}\n{body}\n\n".encode("utf-8")


# ─── Test seam ─────────────────────────────────────────────────────


def reset_all() -> None:
    """Test-only: drop every job's state + in-flight queues. Joins running
    threads so the next test starts clean."""
    global _RUNNING_JOB_ID

    # Snapshot threads under lock, join outside
    with _JOB_LOCK:
        threads = list(_THREADS.values())
    for t in threads:
        if t.is_alive():
            t.join(timeout=2.0)

    with _JOB_LOCK:
        _JOBS.clear()
        _QUEUES.clear()
        _THREADS.clear()
        _RUNNING_JOB_ID = None


def wait_for_job(job_id: str, timeout: float = 5.0) -> JobState | None:
    """Test helper: block until the job finishes (or timeout)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = _JOBS.get(job_id)
        if s and s.status != "running":
            return s
        time.sleep(0.02)
    return _JOBS.get(job_id)
