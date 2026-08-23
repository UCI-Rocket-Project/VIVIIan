"""Run the whole GUI2.1 stack against the simulator.

Starts, in order:

    gse21_fake_server.py   fake GSE2V1 board on TCP 10001 (valves, echo, currents)
    gse21connector.py      TCP -> Flight telemetry, Flight -> TCP commands
    backend.py             Flight servers, decimation, parquet storage
    nidaq_playback.py      scripted pressure profile into the NIDAQ Flight path
    frontendv2.py          the ImGui window

Ctrl-C, or closing the GUI window, shuts everything down.

Before starting it clears processes left over from a previous run. backend.py
uses pythusa, which spawns multiprocessing workers that outlive their parent;
those keep holding the Flight ports (8815/8825) and the next backend then dies
on startup. Two copies of this stack cannot coexist anyway, so anything found
is a leftover. --keep-stale opts out.

    python apps/GUI2.1/run_sim.py                 # decay passes
    python apps/GUI2.1/run_sim.py --profile fail  # decay exceeds 3 psi/min
    python apps/GUI2.1/run_sim.py --real-window   # 180s decay window, not 20s
    python apps/GUI2.1/run_sim.py --keep-stale    # leave leftovers alone
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The GUI hosts the frontend Flight servers, so it comes up first and the
# connector/backend/playback attach to it. Each entry is (label, argv, delay
# before starting the next one).
STARTUP_DELAY_SECONDS = 1.5


# Scripts belonging to this stack. A process whose command line names one of
# these is ours, and so is anything it spawned.
STACK_SCRIPTS = (
    "gse21_fake_server.py",
    "frontendv2.py",
    "gse21connector.py",
    "backend.py",
    "nidaq_playback.py",
)


def _find_leftovers() -> list[tuple[int, str, str]]:
    """Processes from an earlier run of this stack, workers included."""
    try:
        import psutil
    except ImportError:
        print("run_sim: psutil not installed, skipping the leftover sweep", flush=True)
        return []

    mine = {os.getpid()}
    parent = psutil.Process()
    while parent is not None:
        mine.add(parent.pid)
        try:
            parent = parent.parent()
        except psutil.Error:
            break

    alive = set(psutil.pids())
    found: dict[int, tuple[int, str, str]] = {}

    def add(pid: int, kind: str, command: str) -> None:
        if pid not in mine:
            found.setdefault(pid, (pid, kind, command[:90]))

    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            pid = process.info["pid"]
            if pid in mine:
                continue
            command = " ".join(process.info["cmdline"] or [])
            if not command:
                continue

            if any(script in command for script in STACK_SCRIPTS):
                add(pid, "stack process", command)
                # pythusa's workers carry no marker of their own and their
                # parent is still alive, so they are only reachable from here.
                for child in process.children(recursive=True):
                    try:
                        add(child.pid, "worker", " ".join(child.cmdline() or []))
                    except psutil.Error:
                        add(child.pid, "worker", "")
                continue

            # A worker already orphaned by an earlier run records its parent's
            # pid on its command line, and that parent is gone.
            if "spawn_main" in command:
                match = re.search(r"parent_pid=(\d+)", command)
                if match and int(match.group(1)) not in alive:
                    add(pid, "orphaned worker", command)
        except psutil.Error:
            continue

    return list(found.values())


def _clear_leftovers(*, kill: bool) -> None:
    leftovers = _find_leftovers()
    if not leftovers:
        return

    print(f"run_sim: {len(leftovers)} process(es) left over from a previous run:", flush=True)
    for pid, kind, command in leftovers:
        print(f"run_sim:   pid {pid} ({kind}): {command}", flush=True)

    if not kill:
        print("run_sim: leaving them (--keep-stale); the backend will likely fail to start", flush=True)
        return

    import psutil

    # Workers first: killing a parent first orphans them, and an orphaned
    # worker still holds its port.
    ordered = [entry for entry in leftovers if entry[1] != "stack process"]
    ordered += [entry for entry in leftovers if entry[1] == "stack process"]
    killed = []
    for pid, _kind, _command in ordered:
        try:
            process = psutil.Process(pid)
            process.kill()
            killed.append(process)
        except psutil.Error:
            continue
    psutil.wait_procs(killed, timeout=5)
    print(f"run_sim: cleared {len(killed)} leftover process(es)", flush=True)


def _spawn(label: str, argv: list[str], env: dict[str, str]) -> subprocess.Popen:
    print(f"run_sim: starting {label}", flush=True)
    creationflags = 0
    if os.name == "nt":
        # So Ctrl-C in this console doesn't kill children before we can tidy up.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        argv,
        cwd=str(HERE),
        env=env,
        creationflags=creationflags,
    )


def _terminate(label: str, process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    print(f"run_sim: stopping {label}", flush=True)
    try:
        process.terminate()
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GUI2.1 against the simulator")
    parser.add_argument("--profile", choices=("pass", "fail"), default="pass",
                        help="whether the scripted decay stays under 3 psi/min")
    parser.add_argument("--real-window", action="store_true",
                        help="use the real 180s decay window instead of the 20s sim window")
    parser.add_argument("--no-playback", action="store_true",
                        help="skip the pressure playback (valve control only)")
    parser.add_argument("--keep-stale", action="store_true",
                        help="do not clear processes left over from a previous run")
    args = parser.parse_args()

    _clear_leftovers(kill=not args.keep_stale)

    env = dict(os.environ)
    env["GSE2V1_IP"] = "127.0.0.1"
    env["FAKE_GSE21_HOST"] = "127.0.0.1"
    env["GSE_SIM_PROFILE"] = args.profile
    env.setdefault("PYTHONUNBUFFERED", "1")
    if not args.real_window:
        # Short enough that a full procedure walkthrough is watchable.
        env["GSE_DECAY_WINDOW"] = "20"

    python = sys.executable
    stack: list[tuple[str, list[str]]] = [
        ("fake GSE2V1 board", [python, "-u", "gse21_fake_server.py"]),
        ("frontend", [python, "-u", "frontendv2.py"]),
        ("GSE2V1 connector", [python, "-u", "gse21connector.py"]),
        ("backend", [python, "-u", "backend.py"]),
    ]
    if not args.no_playback:
        stack.append(("nidaq playback", [python, "-u", "nidaq_playback.py", "--no-stdin"]))

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        for label, argv in stack:
            processes.append((label, _spawn(label, argv, env)))
            time.sleep(STARTUP_DELAY_SECONDS)

        print(
            "run_sim: all processes up. Arm automation in the GUI panel, then walk the "
            "Pressure Decay procedure. Close the GUI window to shut everything down.",
            flush=True,
        )

        frontend = next(process for label, process in processes if label == "frontend")
        while True:
            if frontend.poll() is not None:
                print("run_sim: frontend exited", flush=True)
                break
            dead = [label for label, process in processes if process.poll() is not None]
            if dead:
                print(f"run_sim: {', '.join(dead)} exited unexpectedly", flush=True)
                break
            time.sleep(0.4)

    except KeyboardInterrupt:
        print("\nrun_sim: interrupted", flush=True)
    finally:
        for label, process in reversed(processes):
            _terminate(label, process)
        print("run_sim: done", flush=True)


if __name__ == "__main__":
    main()
