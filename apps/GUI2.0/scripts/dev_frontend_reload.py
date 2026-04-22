"""Run UCIRPL stack + ImGui frontend; restart the frontend when watched ``*.py`` files change.

Default: one process — simulator, device interfaces, backend, and a file-watched frontend
(same child layout as ``run_all.py --no-frontend`` plus a parent-managed frontend).

Use ``--frontend-only`` if you already run ``run_all.py --no-frontend`` elsewhere.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path


def _paths() -> tuple[Path, Path, str]:
    script = Path(__file__).resolve()
    app_root = script.parents[1]
    repo_root = script.parents[3]
    pythonpath = os.pathsep.join(
        (
            str(repo_root / "packages" / "viviian_core" / "src"),
            str(app_root / "src"),
            str(repo_root),
        )
    )
    return app_root, repo_root, pythonpath


def _terminate_process(process: subprocess.Popen[bytes] | None, timeout_s: float = 5.0) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    deadline = time.time() + timeout_s
    while process.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        pass


def _terminate_process_tree(process: subprocess.Popen[bytes], timeout_s: float = 5.0) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    deadline = time.time() + timeout_s
    while process.poll() is None and time.time() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        process.kill()


def _launch_one(
    *,
    name: str,
    command: list[str],
    pythonpath: str,
    env_extra: dict[str, str] | None = None,
) -> tuple[str, subprocess.Popen[bytes]]:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    env["PYTHONPATH"] = pythonpath
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(command, env=env)
    return name, process


def _resolve_ecu_real_env(args: argparse.Namespace) -> dict[str, str] | None:
    if not args.real_ecu:
        return None
    host = (args.ecu_host or os.environ.get("ECU_IP", "")).strip()
    port_val = args.ecu_port if args.ecu_port is not None else os.environ.get("ECU_PORT")
    if not host or port_val is None or str(port_val).strip() == "":
        print(
            "error: --real-ecu requires ECU_IP and ECU_PORT (or --ecu-host / --ecu-port).",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        port_int = int(port_val)
    except (TypeError, ValueError):
        print("error: ECU_PORT / --ecu-port must be an integer.", file=sys.stderr)
        raise SystemExit(2) from None
    return {
        "UCIRPL_REAL_ECU": "1",
        "ECU_IP": host,
        "ECU_PORT": str(port_int),
    }


def _start_stack(
    *,
    pythonpath: str,
    ecu_real_env: dict[str, str] | None,
) -> list[tuple[str, subprocess.Popen[bytes]]]:
    python = sys.executable
    processes: list[tuple[str, subprocess.Popen[bytes]]] = []
    processes.append(
        _launch_one(
            name="simulator",
            command=[python, "-u", "-m", "device_simulations.device_simulator"],
            pythonpath=pythonpath,
            env_extra={"UCIRPL_SKIP_SIMULATOR_ECU": "1"} if ecu_real_env is not None else None,
        )
    )
    time.sleep(0.3)
    for board in ("gse", "ecu", "extr_ecu", "loadcell"):
        processes.append(
            _launch_one(
                name=f"device_{board}",
                command=[
                    python,
                    "-u",
                    "-m",
                    "ucirplgui.device_interfaces.device_interfaces",
                    "--board",
                    board,
                ],
                pythonpath=pythonpath,
                env_extra=ecu_real_env if board == "ecu" else None,
            )
        )
    processes.append(
        _launch_one(
            name="backend",
            command=[python, "-u", "-m", "ucirplgui.backend.pipeline"],
            pythonpath=pythonpath,
        )
    )
    return processes


class _Debouncer:
    def __init__(self, delay_s: float, callback: Callable[[], None]) -> None:
        self._delay_s = delay_s
        self._callback = callback
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def touch(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._delay_s, self._fire)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
        self._callback()

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


def main() -> int:
    try:
        from watchdog.events import PatternMatchingEventHandler
        from watchdog.observers import Observer
    except ModuleNotFoundError:
        print(
            "error: watchdog is required. Install with:  pip install watchdog\n"
            "       or from repo root:  pip install -e \".[dev]\"",
            file=sys.stderr,
        )
        return 1

    parser = argparse.ArgumentParser(
        description="Run UCIRPL stack and ImGui frontend; restart frontend when Python sources change."
    )
    parser.add_argument(
        "--frontend-only",
        action="store_true",
        help="Do not start simulator/device/backend (use with run_all.py --no-frontend in another shell).",
    )
    parser.add_argument(
        "--real-ecu",
        action="store_true",
        help="Point ECU device interface at a real ECU (requires host/port; ignored with --frontend-only).",
    )
    parser.add_argument(
        "--ecu-host",
        default=None,
        metavar="HOST",
        help="With --real-ecu, override ECU_IP.",
    )
    parser.add_argument(
        "--ecu-port",
        type=int,
        default=None,
        metavar="PORT",
        help="With --real-ecu, override ECU_PORT.",
    )
    parser.add_argument(
        "--watch-viviian",
        action="store_true",
        help="Also watch packages/viviian_core/src (shared gui_utils, themes).",
    )
    parser.add_argument(
        "--debounce",
        type=float,
        default=0.35,
        metavar="SEC",
        help="Delay after last file change before restart (default: 0.35).",
    )
    args = parser.parse_args()

    app_root, repo_root, pythonpath = _paths()

    ecu_real_env: dict[str, str] | None = None
    if not args.frontend_only and args.real_ecu:
        ecu_real_env = _resolve_ecu_real_env(args)

    stack: list[tuple[str, subprocess.Popen[bytes]]] = []
    if not args.frontend_only:
        stack = _start_stack(pythonpath=pythonpath, ecu_real_env=ecu_real_env)
        print("Started UCIRPL stack (simulator, device interfaces, backend):", flush=True)
        for name, process in stack:
            print(f"  - {name}: pid={process.pid}", flush=True)
        if ecu_real_env is not None:
            print(
                f"  (ECU: TCP {ecu_real_env['ECU_IP']}:{ecu_real_env['ECU_PORT']})",
                flush=True,
            )

    child: subprocess.Popen[bytes] | None = None
    restart_lock = threading.Lock()
    stop = False

    def start_child() -> None:
        nonlocal child
        env = dict(os.environ)
        env["PYTHONPATH"] = pythonpath
        env["PYTHONUNBUFFERED"] = "1"
        child = subprocess.Popen(
            [sys.executable, "-u", "-m", "ucirplgui.frontend.frontend"],
            env=env,
        )
        print(f"frontend pid={child.pid}  (auto-reload on save)", flush=True)

    def restart() -> None:
        nonlocal child
        with restart_lock:
            print("file change: restarting frontend...", flush=True)
            _terminate_process(child)
            child = None
            start_child()

    class _Handler(PatternMatchingEventHandler):
        def __init__(self, debouncer: _Debouncer) -> None:
            super().__init__(patterns=["*.py"], ignore_directories=True, case_sensitive=False)
            self._debouncer = debouncer

        def on_modified(self, event: object) -> None:
            self._debouncer.touch()

        def on_created(self, event: object) -> None:
            self._debouncer.touch()

        def on_moved(self, event: object) -> None:
            self._debouncer.touch()

    debouncer = _Debouncer(args.debounce, restart)
    start_child()

    observer = Observer()
    handler = _Handler(debouncer)
    watch_paths: list[Path] = [app_root / "src"]
    if args.watch_viviian:
        watch_paths.append(repo_root / "packages" / "viviian_core" / "src")

    for path in watch_paths:
        if not path.is_dir():
            print(f"warning: watch path not found: {path}", file=sys.stderr)
            continue
        observer.schedule(handler, str(path), recursive=True)

    observer.start()
    print("Watching *.py for frontend reload. Ctrl+C stops all.", flush=True)

    def handle_signal(_signum: int, _frame: object | None) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        while not stop:
            time.sleep(0.1)
            for name, process in stack:
                code = process.poll()
                if code is not None:
                    print(f"{name} exited with code {code}. Stopping.", flush=True)
                    stop = True
                    break
            if stop:
                break
            with restart_lock:
                proc = child
                if proc is not None:
                    code = proc.poll()
                    if code is not None:
                        print(f"frontend exited with code {code}. Stopping.", flush=True)
                        stop = True
    finally:
        debouncer.cancel()
        observer.stop()
        observer.join(timeout=3.0)
        with restart_lock:
            _terminate_process(child)
            child = None
        for _, process in reversed(stack):
            _terminate_process_tree(process)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
