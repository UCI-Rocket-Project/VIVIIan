"""Launch the GUI2.1 test stack in a configurable order.

Edit LAUNCH_ORDER to reorder, add, remove, or tune startup delays.
Each entry: (script_filename, seconds_to_wait_after_starting_it)
"""

from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _spawn_child(script: str) -> subprocess.Popen:
    kwargs: dict = {"cwd": str(HERE)}
    if sys.platform == "win32":
        # Children in their own console process group — Ctrl+C is delivered to THIS
        # launcher only (see handler below), then we reap them explicitly.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        [sys.executable, str(HERE / script)],
        **kwargs,
    )


def _kill_hard(procs: list[subprocess.Popen]) -> None:
    """Best-effort: terminate, then SIGKILL, then ``taskkill /T`` on survivors (Windows)."""
    for p in reversed(procs):
        if p.poll() is None:
            p.terminate()
    time.sleep(0.75)
    for p in reversed(procs):
        if p.poll() is None:
            p.kill()
    time.sleep(0.2)
    if sys.platform == "win32":
        for p in reversed(procs):
            if p.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)

# ---------------------------------------------------------------------------
# Configure here
# ---------------------------------------------------------------------------
LAUNCH_ORDER: list[tuple[str, float]] = [
    ("fake_gse_server.py", 0.5),   # TCP server must be bound first
    ("backend.py",         1.0),   # Flight endpoints must exist before data arrives
    ("gse_connector.py",   0.5),   # Bridges TCP → Flight
    ("frontend.py",        0.0),   # GUI
]
# ---------------------------------------------------------------------------


def main() -> None:
    procs: list[subprocess.Popen] = []
    stop = threading.Event()
    cleaned_up = False

    def cleanup() -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        print("\n[run_test_setup] stopping all child processes ...")
        _kill_hard(procs)

    def on_signal(_signum: int, _frame: object | None) -> None:
        print("\n[run_test_setup] interrupt received")
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)

    exit_code = 0
    try:
        for script, delay in LAUNCH_ORDER:
            print(f"[run_test_setup] starting {script} ...")
            procs.append(_spawn_child(script))
            if delay > 0:
                time.sleep(delay)

        print("[run_test_setup] all processes started — Ctrl+C stops children and exits")
        while not stop.wait(0.5):
            for p in procs:
                if p.poll() is not None:
                    print(f"[run_test_setup] PID {p.pid} exited with code {p.returncode}, shutting down")
                    exit_code = p.returncode or 1
                    stop.set()
                    break
            if stop.is_set():
                break

    except KeyboardInterrupt:
        stop.set()
    finally:
        cleanup()
        print("[run_test_setup] done")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
