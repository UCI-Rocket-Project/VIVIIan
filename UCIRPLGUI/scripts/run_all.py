from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _bootstrap_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    for path in (repo_root, repo_root / "src", repo_root / "UCIRPLGUI" / "src"):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def _launch(
    *,
    name: str,
    command: list[str],
    pythonpath: str,
) -> tuple[str, subprocess.Popen[bytes]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = pythonpath
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(command, env=env)
    return name, process


def _terminate_process_tree(process: subprocess.Popen[bytes], timeout_s: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    deadline = time.time() + timeout_s
    while process.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        process.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start all UCIRPLGUI processes with one command.")
    parser.add_argument(
        "--no-frontend",
        action="store_true",
        help="Start simulator/device/backend only (skip frontend window).",
    )
    return parser.parse_args()


def main() -> int:
    _bootstrap_path()
    args = parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    pythonpath = ";".join(
        (
            str(repo_root / "src"),
            str(repo_root / "UCIRPLGUI" / "src"),
            str(repo_root),
        )
    )

    python = sys.executable
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []

    try:
        processes.append(
            _launch(
                name="simulator",
                command=[python, "-u", "-m", "device_simulations.device_simulator"],
                pythonpath=pythonpath,
            )
        )
        time.sleep(0.3)

        for board in ("gse", "ecu", "extr_ecu", "loadcell"):
            processes.append(
                _launch(
                    name=f"device_{board}",
                    command=[
                        python,
                        "-u",
                        "-m",
                        "ucirplgui.device_interfaces.device_interfacees",
                        "--board",
                        board,
                    ],
                    pythonpath=pythonpath,
                )
            )

        processes.append(
            _launch(
                name="backend",
                command=[python, "-u", "-m", "ucirplgui.backend.pipeline"],
                pythonpath=pythonpath,
            )
        )

        if not args.no_frontend:
            processes.append(
                _launch(
                    name="frontend",
                    command=[python, "-u", "-m", "ucirplgui.frontend.frontend"],
                    pythonpath=pythonpath,
                )
            )

        print("Started UCIRPLGUI processes:", flush=True)
        for name, process in processes:
            print(f"  - {name}: pid={process.pid}", flush=True)
        print("Press Ctrl+C to stop all.", flush=True)

        stop = False

        def _handle_signal(_signum: int, _frame: object) -> None:
            nonlocal stop
            stop = True

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        while not stop:
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"{name} exited with code {code}. Stopping all processes.", flush=True)
                    stop = True
                    break
            time.sleep(0.1)
    finally:
        for _, process in reversed(processes):
            _terminate_process_tree(process)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
