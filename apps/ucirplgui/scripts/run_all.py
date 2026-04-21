from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def _bootstrap_path() -> None:
    app_root = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[3]
    for path in (repo_root, repo_root / "packages" / "viviian_core" / "src", app_root / "src"):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


def _launch(
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
    parser.add_argument(
        "--real-ecu",
        action="store_true",
        help=(
            "Point the ECU device interface at a real ECU over TCP using ECU_IP and ECU_PORT "
            "(same env vars as apps/rocket2_webservice_gui/webservice/server.py). "
            "Other boards still use the local simulator."
        ),
    )
    parser.add_argument(
        "--ecu-host",
        default=None,
        metavar="HOST",
        help="With --real-ecu, override ECU_IP (default: ECU_IP from the environment).",
    )
    parser.add_argument(
        "--ecu-port",
        type=int,
        default=None,
        metavar="PORT",
        help="With --real-ecu, override ECU_PORT (default: ECU_PORT from the environment).",
    )
    return parser.parse_args()


def main() -> int:
    _bootstrap_path()
    args = parse_args()

    ecu_real_env: dict[str, str] | None = None
    if args.real_ecu:
        host = (args.ecu_host or os.environ.get("ECU_IP", "")).strip()
        port_val = args.ecu_port if args.ecu_port is not None else os.environ.get("ECU_PORT")
        if not host or port_val is None or str(port_val).strip() == "":
            print(
                "error: --real-ecu requires ECU_IP and ECU_PORT (or --ecu-host / --ecu-port), "
                "matching apps/rocket2_webservice_gui.",
                file=sys.stderr,
            )
            return 2
        try:
            port_int = int(port_val)
        except (TypeError, ValueError):
            print("error: ECU_PORT / --ecu-port must be an integer.", file=sys.stderr)
            return 2
        ecu_real_env = {
            "UCIRPL_REAL_ECU": "1",
            "ECU_IP": host,
            "ECU_PORT": str(port_int),
        }

    app_root = Path(__file__).resolve().parents[1]
    repo_root = Path(__file__).resolve().parents[3]
    pythonpath = os.pathsep.join(
        (
            str(repo_root / "packages" / "viviian_core" / "src"),
            str(app_root / "src"),
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
                env_extra={"UCIRPL_SKIP_SIMULATOR_ECU": "1"} if ecu_real_env is not None else None,
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
                        "ucirplgui.device_interfaces.device_interfaces",
                        "--board",
                        board,
                    ],
                    pythonpath=pythonpath,
                    env_extra=ecu_real_env if board == "ecu" else None,
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
        if ecu_real_env is not None:
            print(
                f"  (ECU telemetry/commands: TCP {ecu_real_env['ECU_IP']}:{ecu_real_env['ECU_PORT']}, "
                "same wire format as apps/rocket2_webservice_gui)",
                flush=True,
            )
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
