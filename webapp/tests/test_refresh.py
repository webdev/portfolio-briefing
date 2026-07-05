"""POST /refresh + SSE /events contract.

The subprocess command is swapped to a no-op (`true` on POSIX) so we
exercise the full lifecycle — Popen → readline loop → return code →
ingest_complete event — without actually running the briefing pipeline.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from app import jobs


@pytest.fixture(autouse=True)
def _reset_jobs_state():
    """Each test gets a clean job registry."""
    jobs.reset_all()
    jobs.reset_command_builder()
    yield
    jobs.reset_all()
    jobs.reset_command_builder()


def _use_echo_command(extra_lines: list[str] | None = None) -> None:
    """Swap in `printf <lines>` so the subprocess produces deterministic
    output and exits 0 immediately."""
    lines = extra_lines or ["pipeline: hello", "pipeline: done"]
    payload = "\n".join(lines) + "\n"
    def _builder():
        # `printf` is POSIX-portable; subprocess args go after.
        return (["printf", "%s", payload], Path("/tmp"))
    jobs.set_command_builder(_builder)


def _use_failing_command() -> None:
    """Swap in a command that exits non-zero (`false`)."""
    def _builder():
        return (["sh", "-c", "echo problem; exit 7"], Path("/tmp"))
    jobs.set_command_builder(_builder)


def test_post_refresh_returns_job_id_202(client):
    _use_echo_command()
    r = client.post("/refresh")
    assert r.status_code == 202
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "started"
    # Wait for the subprocess to finish so the next test starts fresh
    _wait_for_completion(body["job_id"], client)


def test_concurrent_refresh_returns_409(client):
    """Use a slow command to keep the first job running while we attempt a second."""
    def _slow_builder():
        return (["sh", "-c", "echo running; sleep 0.3"], Path("/tmp"))
    jobs.set_command_builder(_slow_builder)

    r1 = client.post("/refresh")
    assert r1.status_code == 202
    job_id_1 = r1.json()["job_id"]

    # Immediately retry — should 409
    r2 = client.post("/refresh")
    assert r2.status_code == 409
    body2 = r2.json()
    assert body2["job_id"] == job_id_1
    assert body2["status"] == "already_running"

    _wait_for_completion(job_id_1, client, timeout=3)


def test_job_status_404_for_unknown(client):
    r = client.get("/jobs/job_doesnotexist")
    assert r.status_code == 404


def test_job_status_returns_state(client):
    _use_echo_command()
    r = client.post("/refresh")
    job_id = r.json()["job_id"]
    _wait_for_completion(job_id, client)
    r2 = client.get(f"/jobs/{job_id}")
    assert r2.status_code == 200
    body = r2.json()
    assert body["id"] == job_id
    assert body["status"] in ("success", "running", "failed")


def test_events_sse_contract(client):
    """The SSE stream should:
       - replay buffered lines
       - emit a `caught_up` event
       - emit `ingest_complete` at the end with status JSON
    """
    _use_echo_command(["line A", "line B", "line C"])
    r = client.post("/refresh")
    job_id = r.json()["job_id"]
    _wait_for_completion(job_id, client)

    # Now drain the SSE stream — since the job is already complete, the
    # stream should immediately replay + emit ingest_complete + close.
    with client.stream("GET", f"/events?job_id={job_id}") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = b"".join(resp.iter_bytes())
    text = body.decode()
    # Should see the echoed payload
    assert "line A" in text
    assert "line C" in text
    # The completion event with a JSON body
    assert "event: ingest_complete" in text
    assert '"status":' in text or '"status"' in text


def test_events_sse_404_for_unknown_job(client):
    r = client.get("/events?job_id=does_not_exist")
    assert r.status_code == 404


def test_events_emits_log_event_format(client):
    _use_echo_command(["one shot"])
    r = client.post("/refresh")
    job_id = r.json()["job_id"]
    _wait_for_completion(job_id, client)
    with client.stream("GET", f"/events?job_id={job_id}") as resp:
        body = b"".join(resp.iter_bytes())
    text = body.decode()
    # SSE frame format: "event: log\ndata: <line>\n\n"
    assert "event: log" in text
    assert "data: one shot" in text


def test_failed_command_marks_job_failed(client):
    _use_failing_command()
    r = client.post("/refresh")
    job_id = r.json()["job_id"]
    _wait_for_completion(job_id, client, timeout=3)
    r2 = client.get(f"/jobs/{job_id}")
    body = r2.json()
    assert body["status"] == "failed"
    assert body["return_code"] == 7


def _wait_for_completion(job_id: str, client, timeout: float = 3.0) -> None:
    """Poll the job status until it's no longer 'running' or timeout hits."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}")
        if r.status_code == 200 and r.json().get("status") != "running":
            return
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")
