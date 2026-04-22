# UCIRPLGUI

UCIRPLGUI is the operator-facing app in this monorepo.

## Location

- App root: `apps/GUI2.0`
- Runtime package: `apps/GUI2.0/src/ucirplgui`

## Run

From repository root:

```bash
python apps/GUI2.0/scripts/run_all.py
```

## Optional Real ECU Mode

```bash
python apps/GUI2.0/scripts/run_all.py --real-ecu --ecu-host <HOST> --ecu-port <PORT>
```

## Dev: auto-reload frontend

From the **repository root**, one command starts the simulator, device interfaces, backend, and the ImGui window, then **restarts only the frontend** when you save Python under `apps/GUI2.0/src` (requires [watchdog](https://github.com/gorakhargosh/watchdog): `pip install watchdog`, or `pip install -e ".[dev]"` from repo root):

```bash
python apps/GUI2.0/scripts/dev_frontend_reload.py
```

Real ECU (same rules as `run_all.py`):

```bash
python apps/GUI2.0/scripts/dev_frontend_reload.py --real-ecu --ecu-host <HOST> --ecu-port <PORT>
```

Also watch `viviian_core` for shared widget/theme edits:

```bash
python apps/GUI2.0/scripts/dev_frontend_reload.py --watch-viviian
```

`--debounce SEC` (default `0.35`) coalesces rapid saves.

**Two-terminal variant:** run `python apps/GUI2.0/scripts/run_all.py --no-frontend` in one shell, then only the watched frontend in another:

```bash
python apps/GUI2.0/scripts/dev_frontend_reload.py --frontend-only
```

**Shell alternative (frontend only, no Python watcher):** with [watchexec](https://github.com/watchexec/watchexec), from repo root:

```bash
export PYTHONPATH="packages/viviian_core/src:apps/GUI2.0/src:."
watchexec -r -e py -w apps/GUI2.0/src -- python -u -m ucirplgui.frontend.frontend
```

## Test

```bash
PYTHONPATH="packages/viviian_core/src:apps/GUI2.0/src" python -m unittest discover -s apps/GUI2.0/tests -p "test_*.py"
```

## Notes

- Device link status is published by device-interface processes directly to frontend-consumed JSON snapshots.
- Dashboard uses Tau-Ceti visual language by default.
