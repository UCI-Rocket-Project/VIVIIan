"""Run the GUI3.0 ops view against the simulator.

Same stack as ``apps/GUI2.1/run_sim.py`` -- fake board, connector, backend,
pressure playback -- with this front end in place of ``frontendv2.py``. The
process-sweeping and spawn helpers are imported from GUI2.1's runner rather than
copied, so there is one implementation of "clear the leftovers from the last
run" and it cannot drift.

    python apps/GUI3.0/run_sim.py                 # decay passes
    python apps/GUI3.0/run_sim.py --profile fail  # decay exceeds 3 psi/min
    python apps/GUI3.0/run_sim.py --real-window   # 180s decay window, not 20s
    python apps/GUI3.0/run_sim.py --keep-stale    # leave leftovers alone
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUI21 = HERE.parent / "GUI2.1"
ROOT = HERE.parent.parent

STARTUP_DELAY_SECONDS = 1.5

# Every process in the stack needs these. psutil is included because the
# leftover sweep runs in *this* process: without it the sweep silently skips,
# the previous run keeps holding the Flight ports, and the new stack dies on
# bind with an error that says nothing about the real cause.
REQUIRED_MODULES = ("pyarrow", "imgui_bundle", "glfw", "psutil")

REEXEC_FLAG = "GUI3_RUNSIM_REEXEC"


def _venv_python() -> Path | None:
    for relative in ("Scripts/python.exe", "bin/python"):
        candidate = ROOT / ".venv" / relative
        if candidate.is_file():
            return candidate
    return None


def _can_import(python: Path | str, modules) -> bool:
    code = "import " + ", ".join(modules)
    try:
        return subprocess.run(
            [str(python), "-c", code],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _reexec_under_venv_if_needed() -> None:
    """Re-run this script under the repo venv when the current python is wrong.

    Everything has to happen under one interpreter: the leftover sweep needs
    psutil here, and the stack needs pyarrow in the children. Resolving only the
    children would leave the sweep skipped and the old run still holding the
    ports. Re-executing the whole script is the one move that fixes both.
    """
    if os.environ.get(REEXEC_FLAG) == "1":
        return  # already the re-executed copy; never recurse
    if _can_import(sys.executable, REQUIRED_MODULES):
        return

    venv = _venv_python()
    if venv is None or not _can_import(venv, REQUIRED_MODULES):
        raise SystemExit(
            "run_sim: no interpreter has what the stack needs (%s).\n"
            "         tried %s\n"
            "         and   %s\n"
            "         Install them, or run this script with the repo venv:\n"
            "             .venv\\Scripts\\python.exe apps/GUI3.0/run_sim.py"
            % (", ".join(REQUIRED_MODULES), sys.executable,
               venv if venv is not None else "(no .venv found under %s)" % ROOT)
        )

    print("run_sim: %s is missing some of %s; re-running under %s"
          % (sys.executable, ", ".join(REQUIRED_MODULES), venv), flush=True)
    env = dict(os.environ)
    env[REEXEC_FLAG] = "1"
    # Same console, so Ctrl-C still reaches the child and its cleanup runs.
    raise SystemExit(
        subprocess.run([str(venv), str(Path(__file__).resolve())] + sys.argv[1:],
                       env=env).returncode
    )


def _load_gui21_runner():
    """Import GUI2.1's run_sim by path.

    Both apps have a module called ``run_sim``, so a plain import would find
    this file again depending on path order.
    """
    if str(GUI21) not in sys.path:
        sys.path.insert(0, str(GUI21))
    spec = importlib.util.spec_from_file_location(
        "gui21_run_sim", str(GUI21 / "run_sim.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the GUI3.0 ops view against the simulator")
    parser.add_argument("--profile", choices=("pass", "fail"), default="pass",
                        help="whether the scripted decay stays under 3 psi/min")
    parser.add_argument("--real-window", action="store_true",
                        help="use the real 180s decay window instead of the 20s sim window")
    parser.add_argument("--no-playback", action="store_true",
                        help="skip the pressure playback (valve control only)")
    parser.add_argument("--keep-stale", action="store_true",
                        help="do not clear processes left over from a previous run")
    _reexec_under_venv_if_needed()
    args = parser.parse_args()

    python = sys.executable
    runner = _load_gui21_runner()

    # The sweep matches a process by testing whether any of these strings is in
    # its command line, so ours has to be this app's full path: a bare "main.py"
    # would match unrelated work of the user's and kill it. frontendv2 belongs
    # to GUI2.1's stack, not this one. Adjusted here, in this process only --
    # apps/GUI2.1 is not modified.
    runner.STACK_SCRIPTS = tuple(
        name for name in runner.STACK_SCRIPTS if name != "frontendv2.py"
    ) + (str(HERE / "main.py"),)

    runner._clear_leftovers(kill=not args.keep_stale)

    env = dict(os.environ)
    env["GSE2V1_IP"] = "127.0.0.1"
    env["FAKE_GSE21_HOST"] = "127.0.0.1"
    env["GSE_SIM_PROFILE"] = args.profile
    env.setdefault("PYTHONUNBUFFERED", "1")
    if not args.real_window:
        env["GSE_DECAY_WINDOW"] = "20"

    # The ops view hosts the Flight servers, so it comes up first and everything
    # else attaches to it.
    stack: list[tuple[str, list[str]]] = [
        ("fake GSE2V1 board", [python, "-u", str(GUI21 / "gse21_fake_server.py")]),
        ("ops view (GUI3.0)", [python, "-u", str(HERE / "main.py")]),
        ("GSE2V1 connector", [python, "-u", str(GUI21 / "gse21connector.py")]),
        ("backend", [python, "-u", str(GUI21 / "backend.py")]),
    ]
    if not args.no_playback:
        stack.append(
            ("nidaq playback",
             [python, "-u", str(GUI21 / "nidaq_playback.py"), "--no-stdin"])
        )

    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        for index, (label, argv) in enumerate(stack):
            processes.append((label, runner._spawn(label, argv, env)))
            if index < len(stack) - 1:
                time.sleep(STARTUP_DELAY_SECONDS)

        print("run_sim: stack up. Ctrl-C or close the window to stop.", flush=True)
        while True:
            for label, process in processes:
                if process.poll() is not None:
                    print("run_sim: %s exited (%s)" % (label, process.returncode), flush=True)
                    return
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("run_sim: interrupted", flush=True)
    finally:
        for label, process in reversed(processes):
            runner._terminate(label, process)


if __name__ == "__main__":
    main()
